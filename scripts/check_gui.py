#!/usr/bin/env python3
"""GUI 真数据探针：一条命令回答"界面这条链现在通不通"。

什么时候跑：
  * 拆模块 / 搬函数之后——尤其动过 plot_panels / plot_views / plot_compare
  * 改完画布交互（悬停取点 / 滚轮缩放 / 锚点点选）之后
  * 界面"点了没反应"时先跑它：分清是引擎算错还是界面接线断了

为什么要它（2026-09-24 真事故）：plot_views 拆成三个模块那次，全量测试
443 条全绿，但**用户在 1D 图上点选锚点毫无反应**——事件回调引用的名字
跟着代码搬去了别的模块，这一侧忘了导入（NameError 被 matplotlib 打印到
后台：不弹窗、不写日志、测试也照过）。根因是单元测试直接调处理函数，
绕过了"画布上真连的那根线"。

本脚本反过来走用户路径：真 pyFAI 积分 + 从 canvas.callbacks 发真鼠标
事件（锚点点选、悬停、滚轮缩放都要真的点在曲线上才算过）。

用法示例：
    python scripts/check_gui.py
    python scripts/check_gui.py --lab6 data/lab6-00024.tif --lmfp data/LMFP_1_atten0-00029.tif
退出码 0 = 全过，1 = 有失败。
"""
import argparse
import os
import sys
import time
from pathlib import Path

# 项目根 = 本文件所在 scripts/ 的上一级（与 scripts/ 下其他脚本同一惯例：
# 不依赖当前工作目录，从 __file__ 推导）
ROOT = Path(__file__).resolve().parents[1]

# 界面探针不开真窗口（CI / 无显示环境也能跑）；要在自己屏幕上看着跑，
# 把下面这行去掉或改成 "cocoa"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(ROOT / "src"))

from matplotlib.backend_bases import MouseEvent          # noqa: E402
from PySide6.QtWidgets import QApplication               # noqa: E402

from xrd_toolkit.gui import panel_state as gui_state     # noqa: E402
from xrd_toolkit.gui import plot_panels as gui_plot_panels   # noqa: E402
from xrd_toolkit.gui import plot_views as gui_views      # noqa: E402
from xrd_toolkit.gui.app import create_window            # noqa: E402

# 默认样例数据（不入仓库，换机器要自己拷；见 scripts/check_env.py）
SAMPLE_LAB6 = "data/lab6-00024.tif"
SAMPLE_LMFP = "data/LMFP_1_atten0-00029.tif"

_failed = []


def report(ok: bool, name: str, detail: str = "") -> None:
    """打印一行结论；失败进总结并让退出码非 0。"""
    if not ok:
        _failed.append(name)
    tail = f"（{detail}）" if detail else ""
    print(f"[{'OK ' if ok else 'FAIL'}] {name}{tail}")


def wait_until(predicate, timeout_s: float = 180.0) -> bool:
    """轮询处理事件直到条件成立（后台积分结果靠事件循环投递）。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
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


def check_single_views(window, lab6: str) -> None:
    """单文件视图：面板壳（plot_panels）+ 出图（plot_views）。"""
    print("\nA. 单文件视图（真积分）")
    window.add_files([lab6])
    window.view_buttons["1D"].click()
    key1d = "1D|" + lab6
    ok = wait_until(
        lambda: key1d in window.plot_docks
        and len(content_of(window, key1d).axes_1d.lines) > 0)
    report(ok, "1D 真积分出图（曲线非空）")
    if not ok:
        return
    dock = window.plot_docks["1D|" + lab6]
    ax = content_of(window, "1D|" + lab6).axes_1d
    xd = ax.lines[0].get_xdata()
    report(1.0 < float(xd[0]) < 3.0 and 6.0 < float(xd[-1]) < 9.0,
           "2θ 范围像真的（1–8° 内）",
           f"{float(xd[0]):.2f}–{float(xd[-1]):.2f}°")
    report("2θ" in ax.get_xlabel(), "x 轴标签就位", ax.get_xlabel()[:24])
    report(Path(lab6).stem[:8] in ax.get_title(), "标题带文件名",
           ax.get_title()[:34])

    # 悬停取点（面板壳接的 motion 回调）
    canvas = content_of(window, "1D|" + lab6).canvas
    xm = float(xd[len(xd) // 2])
    ym = float((ax.lines[0].get_ydata())[len(xd) // 2])
    px, py = ax.transData.transform((xm, ym))
    fire(canvas, "motion_notify_event", px, py, buttons=frozenset())
    # 悬停读数写进状态行右侧的 coord_label（不是 status_text——后者是
    # 消息行，之前拿它当证据是**假通过**：匹配到的是"积分完成…2θ …"
    # 那条消息，跟悬停无关。2026-09-24 换成缓存命中消息后暴露）
    report("2θ" in window.coord_label.text(),
           "悬停：状态行右侧出坐标", window.coord_label.text()[:40])
    marker = getattr(dock, "hover_marker", None)
    report(marker is not None and len(marker.get_xdata()) > 0,
           "悬停：白边圆点标记出现")
    fire(canvas, "motion_notify_event", 3, 3, buttons=frozenset())

    # 锚点点选（2026-09-24 事故的那条路）
    combo = window.params["背景扣除模式"]
    combo.setCurrentIndex(combo.findData("anchor"))
    window.bg_pick_btn.setChecked(True)
    canvas.callbacks.process(
        "button_press_event",
        MouseEvent("button_press_event", canvas, px, py, button=1))
    canvas.callbacks.process(
        "button_release_event",
        MouseEvent("button_release_event", canvas, px, py, button=1))
    anchors = window.bg_anchors.get(str(gui_views._bg_path_of(dock)), [])
    report(len(anchors) == 1, "锚点点选真的加上了一个", anchors)
    if anchors:
        report(abs(anchors[0][0] - xm) < 0.05,
               "锚点吸附到真实数据点（<0.05°）",
               f"点 {xm:.3f}° → 记 {anchors[0][0]:.3f}°")
    report(any(gui_plot_panels._is_aux_line(ln) for ln in ax.lines),
           "锚点触发重画（辅助线带 bg: 前缀）")
    window.bg_pick_btn.setChecked(False)

    # 滚轮缩放（放大镜点亮时才缩，熄灭时滚轮还给绘图区）
    xlim0 = ax.get_xlim()
    content_of(window, "1D|" + lab6).toolbar._actions["zoom"].trigger()
    fire(canvas, "scroll_event", px, py, step=1, button="up")
    report(tuple(ax.get_xlim()) != tuple(xlim0),
           "滚轮缩放改了 x 范围（放大镜点亮）",
           f"{xlim0[0]:.2f}–{xlim0[1]:.2f} → "
           f"{ax.get_xlim()[0]:.2f}–{ax.get_xlim()[1]:.2f}")
    content_of(window, "1D|" + lab6).toolbar._actions["zoom"].trigger()

    # 其余三个单文件视图
    print("\nB. 其余单文件视图（真数据）")
    for view in ("2D", "剖面", "瀑布"):
        window.view_buttons[view].click()
    ok = wait_until(lambda: all(any(k.startswith(v + "|") for k in window.plot_docks)
                                for v in ("2D", "剖面", "瀑布")))
    report(ok, "2D / 剖面 / 瀑布三个面板都开了")
    if not ok:
        return
    ax2d = content_of(window, "2D|" + lab6).axes_2d
    wait_until(lambda: len(ax2d.images) > 0
               and ax2d.images[0].get_array() is not None)
    report(len(ax2d.images) > 0 and ax2d.images[0].get_array() is not None,
           "2D 出图（有图像矩阵）")
    axp = content_of(window, "剖面|" + lab6).axes_profile
    wait_until(lambda: len(axp.lines) > 0)
    report(len(axp.lines) > 0, "剖面出图（曲线非空）")
    axw = content_of(window, "瀑布|" + lab6).axes_waterfall
    wait_until(lambda: len(axw.lines) > 0)
    report(len(axw.lines) == 36, "瀑布出图（36 扇区）", len(axw.lines))


def check_multi_views(window, lab6: str, lmfp: str) -> None:
    """多文件视图：整个在 plot_compare 里（对比 + 热图）。"""
    print("\nC. 多文件视图（plot_compare）")
    window.add_files([lmfp])          # 新加入的文件默认打勾 → 两个都选中
    window.compare_btn.click()
    ok = wait_until(lambda: any(
        k.startswith("对比|")
        and len(content_of(window, k).axes_1d.lines) >= 2
        for k in window.plot_docks))
    report(ok, "对比面板画出 2 条真曲线")
    ckey = next((k for k in window.plot_docks
                 if k.startswith("对比|")), None)
    if ckey:
        axc = content_of(window, ckey).axes_1d
        report("对比" in window.plot_docks[ckey].windowTitle(),
               "对比标题是 A vs B 形态",
               window.plot_docks[ckey].windowTitle()[:44])
        legend = axc.get_legend()
        report(legend is not None and len(legend.get_texts()) == 2,
               "图例两条", len(legend.get_texts()) if legend else 0)

    window.heat_btn.click()
    ok = wait_until(
        lambda: any(k.startswith("热图|") for k in window.plot_docks))
    hkey = next((k for k in window.plot_docks
                 if k.startswith("热图|")), None)
    report(ok and hkey is not None, "热图面板开出来了", hkey)
    if not hkey:
        return
    dock = window.plot_docks[hkey]
    axh = content_of(window, hkey).axes_heat
    wait_until(lambda: len(axh.images) > 0
               and axh.images[0].get_array() is not None)
    report(len(axh.images) > 0 and axh.images[0].get_array() is not None,
           "热图有图像矩阵")
    report(getattr(dock, "_heat_colorbar", None) is not None,
           "热图带颜色条")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="GUI 真数据探针（跑完给出退出码）")
    ap.add_argument("--lab6", default=SAMPLE_LAB6,
                    help=f"标样图像（默认 {SAMPLE_LAB6}）")
    ap.add_argument("--lmfp", default=SAMPLE_LMFP,
                    help="第二个样品图像，用于对比 / 热图"
                         f"（默认 {SAMPLE_LMFP}）")
    args = ap.parse_args()

    lab6 = (ROOT / args.lab6).resolve() if not Path(args.lab6).is_absolute() \
        else Path(args.lab6)
    lmfp = (ROOT / args.lmfp).resolve() if not Path(args.lmfp).is_absolute() \
        else Path(args.lmfp)
    for path in (lab6, lmfp):
        if not path.exists():
            print(f"找不到数据文件：{path}\n"
                  f"data/ 下的真数据不入仓库（隐私），换机器要自己拷；"
                  f"或用 --lab6/--lmfp 指定其它图像。")
            return 2

    print(f"项目根目录：{ROOT}")
    print(f"样例数据：{lab6.name} + {lmfp.name}\n")
    app = QApplication.instance() or QApplication([])   # noqa: F841
    window = create_window()
    try:
        # 传**绝对路径**：应用按进程工作目录解析相对路径，脚本要能在任何
        # 目录下跑（面板键 = 视图|路径串，用同一份字符串取面板）
        lab6_key, lmfp_key = str(lab6), str(lmfp)
        check_single_views(window, lab6_key)
        check_multi_views(window, lab6_key, lmfp_key)
        print("\n日志末行：" + window.log_text.toPlainText().strip()
              .splitlines()[-1])
    finally:
        window.hide()          # 没显示过的窗口这是空操作；显示过的必须先
        window.close()         # 藏起来——否则 close 会弹"保存询问"卡住套件

    print()
    if _failed:
        print(f"== {len(_failed)} 项失败，见上面的 FAIL ==")
        for name in _failed:
            print(f"   - {name}")
        return 1
    print("== 界面链路正常（真数据 + 真画布事件全过）==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
