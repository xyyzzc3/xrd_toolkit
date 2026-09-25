#!/usr/bin/env python3
"""真窗口看一眼：用真数据走一遍关键交互、抓图，然后把窗口留在屏幕上。

与 scripts/check_gui.py 的分工（两个都值得跑，别互相替代）：
  * check_gui.py = **无头自动判据**（一条条 PASS/FAIL + 退出码），搬代码、
    改接线之后先跑它；
  * 本脚本 = **真窗口 + 抓图 + 留给你点**：它只把三件确定的事走一遍，
    其余交给你自己玩。加 --exit 就"跑完即退"，无头环境（--headless）
    也能用，图照样出。

三件事（每步存一张图，并给一条能自动判的结论）：
  ① 每个真数据文件开一张 1D（真实积分链路 + 产物缓存）
  ② 在最前面那张的曲线上发一次真鼠标移动 → 悬停取点 + 状态栏读数
  ③ 临时把批量上限调小、再开另一种视图 → 日志里的"先画前 N 张"提示
     （--cap-demo 0 关掉这一步）

用法：
    python scripts/show_gui.py                      # data/ 下所有真数据
    python scripts/show_gui.py --files data/a.tif data/b.tif
    python scripts/show_gui.py --cap-demo 0         # 不演示上限提示
    python scripts/show_gui.py --exit               # 跑完即退（不留窗口）
    python scripts/show_gui.py --headless --exit    # 无头（抓图仍在）

退出码：0 = 三件事都成立；1 = 有一步没成立（图仍会存下来供查看）。
图默认存 outputs/gui_shots/（outputs/ 不上传，隐私数据不进仓库）。
"""
import argparse
import os
import sys
import time
from pathlib import Path

# 项目根 = 本文件所在 scripts/ 的上一级（与 scripts/ 下其他脚本同一惯例）
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# 真窗口是默认（要给人看）；--headless 才切 offscreen。**必须在 import Qt
# 之前定**，所以这里先看一眼 argv。
if "--headless" in sys.argv:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
else:
    os.environ.pop("QT_QPA_PLATFORM", None)   # 别被外部继承的 offscreen 压住

import numpy as np                                       # noqa: E402
from matplotlib.backend_bases import MouseEvent          # noqa: E402
from PySide6.QtWidgets import (QApplication,             # noqa: E402
                               QToolButton)

from xrd_toolkit.gui import panel_state as gui_state     # noqa: E402
from xrd_toolkit.gui import plot_views as gui_views      # noqa: E402
from xrd_toolkit.gui.app import create_window            # noqa: E402

# data/ 下认这些后缀（与 CLI 的白名单同一批；真数据不入仓库）
SUFFIXES = (".tif", ".tiff", ".edf", ".cbf")

_failed = []


def report(ok: bool, name: str, detail: str = "") -> None:
    """打印一行结论；失败记下来（决定退出码）。"""
    if not ok:
        _failed.append(name)
    tail = f"（{detail}）" if detail else ""
    print(f"[{'OK ' if ok else 'FAIL'}] {name}{tail}", flush=True)


def pump(predicate, timeout_s: float = 180.0) -> bool:
    """轮询处理事件直到条件成立（后台积分结果靠事件循环投递）。"""
    end = time.time() + timeout_s
    while time.time() < end:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def content_of(window, key: str):
    """面板容器 → 面板内容（子窗口或弹出窗口都能取）。"""
    return gui_state._content(window.plot_docks[key])


def fire(canvas, name: str, x: float, y: float, **kw) -> None:
    """发一个真鼠标事件（走画布回调表 = 用户真实路径）。"""
    canvas.callbacks.process(name, MouseEvent(name, canvas, x, y, **kw))


def grab(window, outdir: Path, name: str) -> None:
    """把当前窗口渲染成 PNG（QWidget.grab = 应用自己画，不动你屏幕）。"""
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / name
    window.grab().save(str(path))
    print(f"      图：{path}（{path.stat().st_size // 1024} KB）", flush=True)


def default_files() -> list:
    """data/ 下的真数据（没有就报清楚：真数据不入仓库）。"""
    folder = ROOT / "data"
    return sorted(str(p) for p in folder.glob("*")
                  if p.is_file() and p.suffix.lower() in SUFFIXES)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="真窗口看一眼：真数据走关键交互 + 抓图 + 留在屏幕上")
    ap.add_argument("--files", nargs="*", default=None,
                    help="要开的真数据文件（默认：data/ 下所有支持的格式）")
    ap.add_argument("--cap-demo", type=int, default=2,
                    help="演示批量上限提示时临时用的上限（0 = 不演示）")
    ap.add_argument("--outdir", default="outputs/gui_shots",
                    help="抓图存哪（默认 outputs/gui_shots）")
    ap.add_argument("--exit", action="store_true",
                    help="跑完就退出（默认把窗口留在屏幕上给你点）")
    ap.add_argument("--headless", action="store_true",
                    help="不开真窗口（offscreen，抓图仍在）")
    ap.add_argument("--timeout", type=float, default=180.0,
                    help="每步最多等多少秒（默认 180）")
    args = ap.parse_args()

    files = [str((ROOT / f).resolve() if not Path(f).is_absolute() else Path(f))
             for f in (args.files or default_files())]
    if not files:
        print(f"没找到真数据：{ROOT / 'data'} 下没有 {'/'.join(SUFFIXES)} 文件。\n"
              f"真数据不进仓库（隐私），换机器要自己拷；或用 --files 指定。")
        return 2
    for f in files:
        if not Path(f).exists():
            print(f"找不到：{f}")
            return 2

    outdir = Path(args.outdir)
    if not outdir.is_absolute():
        outdir = ROOT / outdir

    print(f"项目根：{ROOT}")
    print(f"真数据：{len(files)} 个 —— {', '.join(Path(f).name for f in files)}")
    print(f"窗口模式：{'offscreen（无头）' if args.headless else '真窗口'}，"
          f"上限演示：{args.cap_demo or '关'}\n")

    app = QApplication.instance() or QApplication([])   # noqa: F841
    window = create_window()
    window.resize(1500, 950)
    window.show()

    # ① 每个文件一张 1D（真积分；已算过的会走产物缓存）
    print("① 开 1D 面板（真数据）")
    window.add_files(files, select=True)   # 导入默认不勾选（界面用 [全选]）
    window.view_buttons["1D"].click()
    keys = ["1D|" + f for f in files]
    drawn = pump(lambda: all(k in window.plot_docks
                             and len(content_of(window, k).axes_1d.lines) > 0
                             for k in keys), args.timeout)
    report(drawn, f"1D 出图 {len(keys)} 张（真积分/产物缓存）")
    if not drawn:
        grab(window, outdir, "0_failed.png")
        return 1
    grab(window, outdir, "1_1d_panels.png")

    # ② 悬停取点：在**最前面**那张（最后开的）的曲线上发一次真鼠标移动
    print("\n② 悬停取点（最前面那张）")
    front = keys[-1]
    content = content_of(window, front)
    ax = content.axes_1d
    xd = np.asarray(ax.lines[0].get_xdata(), dtype=float)
    yd = np.asarray(ax.lines[0].get_ydata(), dtype=float)
    i = len(xd) // 3                      # 随便挑靠前的一个真实数据点
    px, py = ax.transData.transform((float(xd[i]), float(yd[i])))
    fire(content.canvas, "motion_notify_event", float(px), float(py),
         button=1, buttons=frozenset({1}))
    pump(lambda: bool(window.coord_label.text()), 5.0)
    reading = window.coord_label.text()
    report(bool(reading), "状态栏出悬停读数", reading)
    grab(window, outdir, "2_hover_dot.png")

    # ③ 批量上限提示：上限调小，再用**另一种**视图开（同视图会复用面板，
    #    不产生"新面板"，也就碰不到上限）
    if args.cap_demo:
        print(f"\n③ 批量上限提示（临时把上限调成 {args.cap_demo}）")
        other = "2D"
        orig = gui_views.MAX_PANELS_PER_BATCH
        gui_views.MAX_PANELS_PER_BATCH = args.cap_demo
        try:
            window.view_buttons[other].click()
            pump(lambda: len([k for k in window.plot_docks
                              if k.startswith(other + "|")]) == args.cap_demo,
                 args.timeout)
            log = window.log_text.toPlainText()
            line = next((ln for ln in log.splitlines() if "先画前" in ln), "")
            report(bool(line), f"{other} 上限提示出现在日志", line[:60] + "…"
                   if len(line) > 60 else line)
            grab(window, outdir, "3_batch_cap.png")
        finally:
            gui_views.MAX_PANELS_PER_BATCH = orig   # 还原，别把上限留在小值

    # 面板标题栏那四个按钮（自绘工具栏）：读一眼 action 名单
    bar = content.slim_bar
    names = [b.defaultAction().text() for b in bar.findChildren(QToolButton)
             if b.defaultAction() is not None]
    print(f"\n面板标题栏按钮：{names}")
    print(f"打开的面板：{len(window.plot_docks)} 张")

    print(f"\n== {'全过' if not _failed else '有失败：' + '、'.join(_failed)}"
          f"；图在 {outdir} ==")
    if not args.exit:
        print("窗口留在屏幕上（关掉它即结束本脚本）")
        app.exec()
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
