#!/usr/bin/env python3
"""卡顿实测：把"哪儿卡、卡多久"量出来（2026-10-03）。

背景：用户要"优化流畅性"，但优化前必须先把数字拿到手（轻用不卡、
重活才卡——重活是哪些、各卡几秒，得实测）。做法：

  * **事件循环抖动计（Jank）**：挂一个 50 ms 的 QTimer 记 tick 间隔，
    最大间隔 = 主线程被独占的最长时间——这段时间窗口完全画不动、
    点什么都没反应，就是用户嘴里的"卡"。
  * 每个场景量两样：整段墙钟耗时 + 最大卡顿（> 1 秒的卡顿还会打印
    当时主线程在干什么——从最大间隔处抓的调用栈）。

场景（真数据；默认取 ~/Desktop/lmfpdata 的 LMFP 系列）：
  ① 启动：窗口建出来为止
  ② 开 1 张 1D：冷缓存（真积分）与热缓存各一次
  ③ 批量处理 N 个文件（后台算 + 落产物 + 文件栏重建）
  ④ 导出 N 个文件（同步写盘——预期是最大头）
  ⑤ 一次开 24 张 1D 面板（防爆图上限）
  ⑥ 空转 10 秒：有没有白烧 CPU

缓存写到系统临时目录（不碰你的 outputs/，与探针同一套规矩）。
用法：
    python scripts/measure_perf.py               # 默认 24 个文件
    python scripts/measure_perf.py --n 81        # 全部
    python scripts/measure_perf.py --data ~/Desktop/lmfpdata
"""
import argparse
import faulthandler
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(ROOT / "src"))

# 实测也不碰用户的文件区（与 check_gui / show_gui 同一套规矩，2026-10-03）
_SCRATCH = Path(tempfile.mkdtemp(prefix="xrd_perf_"))
os.environ.setdefault("XRD_STAGE_CACHE", str(_SCRATCH / "stage"))
os.environ.setdefault("XRD_RECIPES", str(_SCRATCH / "recipes.json"))

_T_IMPORT = time.perf_counter()

import numpy as np                                       # noqa: E402
from PySide6.QtCore import QTimer, Qt                    # noqa: E402
from PySide6.QtWidgets import QApplication               # noqa: E402
from unittest import mock                                # noqa: E402

from xrd_toolkit.gui import plot_export as gui_export    # noqa: E402
from xrd_toolkit.gui import plot_views as gui_views      # noqa: E402
from xrd_toolkit.gui import sources as gui_sources       # noqa: E402

_IMPORT_S = time.perf_counter() - _T_IMPORT

DEFAULT_DATA = Path.home() / "Desktop" / "lmfpdata"


class Jank:
    """事件循环抖动计：tick 间隔的最大值 = 主线程最长独占时间。"""

    def __init__(self, interval_ms: int = 50):
        self.timer = QTimer()
        self.timer.setInterval(interval_ms)
        self.timer.timeout.connect(self._tick)
        self._last = None
        self._gaps = []
        self._worst_stack = None
        self._last_stack = None

    def _tick(self):
        now = time.perf_counter()
        if self._last is not None:
            gap = now - self._last
            self._gaps.append(gap)
            if gap > 1.0:                     # 卡超 1 秒：抓一份调用栈
                self._last_stack = self._stack()
        self._last = now

    @staticmethod
    def _stack() -> str:
        import traceback
        frames = traceback.extract_stack()
        keep = [f"{Path(f.filename).name}:{f.lineno} {f.name}"
                for f in frames if "measure_perf" not in f.filename][-6:]
        return " ← ".join(keep)

    def start(self):
        self._gaps = []
        self._last = time.perf_counter()
        self._last_stack = None
        self.timer.start()

    def stop(self) -> float:
        self.timer.stop()
        # 把"最后一 tick 到现在"也计入：同步阻塞段（如导出循环）期间
        # 计时器根本发不出 tick，不补这一下就完全量不到那段冻结
        if self._last is not None:
            tail = time.perf_counter() - self._last
            if tail > 0:
                self._gaps.append(tail)
        gaps = self._gaps or [0.0]
        self.worst = max(gaps)
        self.worst_stack = self._last_stack
        # p95：偶发的大卡顿才是用户痛感，均值会把它稀释掉
        self.p95 = float(np.percentile(gaps, 95))
        return self.worst


jank = Jank()


def pump(predicate, timeout_s: float = 600.0) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


def report(name: str, wall_s: float, note: str = ""):
    print(f"{name:<34} 用时 {wall_s:6.1f} s | 最长卡顿 {jank.worst:5.2f} s"
          f" | p95 {jank.p95:4.2f} s {note}", flush=True)
    if jank.worst > 1.0 and jank.worst_stack:
        print(f"{'':<34}   卡最狠时主线程在：{jank.worst_stack}", flush=True)


def check_files(window, paths):
    """只勾这几个文件（复用探针的写法）。"""
    want = {str(p) for p in paths}
    for i in range(window.file_list.count()):
        it = window.file_list.item(i)
        it.setCheckState(Qt.Checked if it.data(Qt.UserRole) in want
                         else Qt.Unchecked)
    for g in window.file_list.groups():
        g.setCheckState(Qt.Unchecked)
    QApplication.processEvents()


def check_group(window, prefix: str) -> int:
    """勾上名字以 prefix 开头的产物组（导出 N 条产物的真实路径）。

    用户导出整批不是"勾 81 个原始条目"（那要 81 个面板全开着），而是
    勾产物组——导出走缓存读盘。返回勾上的条数。
    """
    n = 0
    for i in range(window.file_list.count()):
        it = window.file_list.item(i)
        it.setCheckState(Qt.Unchecked)
    for g in window.file_list.groups():
        hit = g.text(0).startswith(prefix)
        g.setCheckState(Qt.Checked if hit else Qt.Unchecked)
        if hit:
            n += g.childCount()
    QApplication.processEvents()
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="卡顿实测")
    ap.add_argument("--data", default=str(DEFAULT_DATA))
    ap.add_argument("--n", type=int, default=24, help="用几个文件")
    args = ap.parse_args()

    data_dir = Path(args.data).expanduser()
    files = sorted(p for p in data_dir.glob("LMFP*.tif"))[:args.n]
    if not files:
        print(f"没找到数据：{data_dir}（--data 指定其它目录）")
        return 2
    print(f"数据：{len(files)} 个文件（{data_dir}）")
    print(f"缓存：{_SCRATCH}（临时目录，不碰 outputs/）\n")

    # ① 启动（import 已经发生在上面的模块导入里；这里量建窗口）
    t0 = time.perf_counter()
    app = QApplication.instance() or QApplication([])
    from xrd_toolkit.gui.app import create_window
    window = create_window()
    window.show()
    QApplication.processEvents()
    t1 = time.perf_counter()
    print(f"{'① import + 建窗口':<34} 用时 {_IMPORT_S + (t1 - t0):6.1f} s"
          f"（import {_IMPORT_S:.1f} s + 建窗口 {t1 - t0:.1f} s）")

    window.add_files([str(p) for p in files], select=True)
    QApplication.processEvents()

    # ② 开 1 张 1D：冷缓存
    one = [str(files[0])]
    check_files(window, one)
    jank.start()
    t0 = time.perf_counter()
    window.view_buttons["1D"].click()
    pump(lambda: getattr(window.plot_docks.get("1D|" + one[0]),
                         "last_tth", None) is not None, 300)
    t1 = time.perf_counter()
    jank.stop()
    report("② 开 1 张 1D（冷缓存＝真积分）", t1 - t0)

    # ②b 热缓存（同一张再来一遍）
    jank.start()
    t0 = time.perf_counter()
    window.view_buttons["1D"].click()
    QApplication.processEvents()
    t1 = time.perf_counter()
    jank.stop()
    report("②b 再点一次（热缓存）", t1 - t0)

    # ③ 批量处理 N 个文件（勾选 → [批量处理]）
    check_files(window, files)
    window.params["背景扣除模式"].setCurrentIndex(
        window.params["背景扣除模式"].findData("auto"))
    QApplication.processEvents()
    jank.start()
    t0 = time.perf_counter()
    window.proc_batch_btn.click()
    pump(lambda: "批量处理完成" in window.log_text.toPlainText(), 1200)
    t1 = time.perf_counter()
    jank.stop()
    report(f"③ 批量处理 {len(files)} 个文件", t1 - t0)

    # ④ 导出整批（真实路径：勾**产物组** → 从缓存读盘写 txt；同步循环）
    out = _SCRATCH / "export"
    n_prod = check_group(window, "1D 产物")
    if not n_prod:
        n_prod = check_group(window, "处理产物")
    with mock.patch.object(
            gui_export, "_build_export_dialog",
            return_value={"dir": out, "suffix": ".txt", "csv": True,
                          "bg": False}):
        jank.start()
        t0 = time.perf_counter()
        gui_export._run_export(window)
        t1 = time.perf_counter()
        jank.stop()
    report(f"④ 导出 {n_prod} 条产物（同步写盘）", t1 - t0)

    # ⑤ 一次开 24 张面板（上限就是 24；拿前 24 个文件）
    many = [str(p) for p in files[:min(24, len(files))]]
    for dock in list(window.plot_docks.values()):
        dock.close()
    QApplication.processEvents()
    check_files(window, many)
    jank.start()
    t0 = time.perf_counter()
    window.view_buttons["剖面"].click()      # 换个没开过的视图，真新建面板
    pump(lambda: len([k for k in window.plot_docks
                      if k.startswith("剖面|")]) == len(many), 900)
    t1 = time.perf_counter()
    jank.stop()
    report(f"⑤ 一次开 {len(many)} 张剖面面板", t1 - t0)

    # ⑥ 空转 10 秒：有没有白烧 CPU
    cpu0 = time.process_time()
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 10:
        QApplication.processEvents()
        time.sleep(0.01)
    cpu = time.process_time() - cpu0
    print(f"{'⑥ 空转 10 秒':<34} 期间 CPU 时间 {cpu:5.2f} s"
          f"（{'白烧 CPU' if cpu > 1.0 else '基本空闲'}）")

    window.hide()
    window.close()
    print(f"\n缓存目录（可删）：{_SCRATCH}")
    return 0


if __name__ == "__main__":
    faulthandler.dump_traceback_later(1800, exit=True)   # 卡死兜底
    sys.exit(main())
