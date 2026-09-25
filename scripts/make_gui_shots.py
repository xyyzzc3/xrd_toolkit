#!/usr/bin/env python3
"""重拍 GUI 展示图（showcase/gui/*.png）——README 图墙用的那 8 张。

什么时候跑：**界面外观变了就重拍**（改了工具栏/面板壳/配色/对话框
之后，README 里的图还是旧样子，评审一眼能看出来）。

场景与文档里的图注一一对应（改图注前先看这里；2026-09-25 起 README
只放三张 GUI 图，其余四张在 docs/NOTES.md）：
    gui_main        LaB₆ 积到 1D，右侧参数坞（1D 页）        → README
    gui_calib       校准页三列 + Δ + 结论（自动 + 二次精修）  → README
    gui_heatmap     三个数据集的热图 + 旁边一条 1D            → README
    gui_compare     两张 LMFP 叠图 + 图例                     → NOTES
    gui_customize   Customize 对话框（单张，460×542）         → NOTES
    gui_views       2D / 剖面 / 瀑布 三块面板（2800×1800）    → NOTES
    gui_batch       导入文件夹 → 批量积分（日志带 k/n）→ 导出 → NOTES
    gui_background  真 LMFP 上的四个手动锚点（原始/基线/结果）→ NOTES

不开真窗口（QT_QPA_PLATFORM=offscreen + widget.grab），所以 CLI 里
也能跑；**真实数据**（data/ 下的 lab6 + 3 张 LMFP）与**真实计算**
（pyFAI 积分、校准），只是弹窗都被替换成脚本返回值。

用法示例：
    python scripts/make_gui_shots.py              # 全部重拍
    python scripts/make_gui_shots.py gui_main gui_views
"""
import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")
sys.path.insert(0, str(ROOT / "src"))

from unittest import mock                                  # noqa: E402

from PySide6.QtWidgets import QApplication, QPushButton    # noqa: E402

from xrd_toolkit.gui import panel_state as gui_state       # noqa: E402
from xrd_toolkit.gui.app import create_window              # noqa: E402

OUT = ROOT / "showcase" / "gui"

# 数据自动找（不写死路径）：先看项目 data/，再看用户那份原始数据目录
# （真数据不入仓库，换机器要自己拷——见 scripts/check_env.py）
DATA_DIRS = (ROOT / "data", Path.home() / "Desktop" / "lmfpdata")


def find_data(pattern: str, count: int) -> list:
    """按 DATA_DIRS 顺序找 count 个匹配 pattern 的文件（找到就停）。"""
    for d in DATA_DIRS:
        hits = sorted(d.glob(pattern)) if d.is_dir() else []
        if len(hits) >= count:
            return [str(p.relative_to(ROOT)) if p.is_relative_to(ROOT)
                    else str(p) for p in hits[:count]]
    return []


LAB6 = ""      # main() 里定（找不到就报错退出）
LMFP = []


def settle(ms: int = 400) -> None:
    """把排队的事件跑完（后台结果靠事件循环投递）。"""
    deadline = time.time() + ms / 1000
    while time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)


def wait_for(predicate, timeout_s: float = 180.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def content_of(window, key: str):
    return gui_state._content(window.plot_docks[key])


def save(window, name: str, size=(1600, 1000)) -> None:
    """摆好尺寸 → 跑几轮事件 → 抓图。"""
    window.resize(*size)
    settle(600)
    path = OUT / f"{name}.png"
    window.grab().save(str(path))
    print(f"  ✓ {path.relative_to(ROOT)}  ({size[0]}×{size[1]})")


def tile(window, orient: str = "横排") -> None:
    """把面板摆开再拍（否则级联叠着，后面开的会把前面的盖掉）。

    2026-09-24 实测：不摆的话热图/背景那两张的"有颜色像素"比旧图少
    好几倍——热图面板被 1D 面板盖掉大半。
    """
    from xrd_toolkit.gui.panels import _arrange
    _arrange(window, orient)
    settle(600)


def add_all(window, paths, entrance: str = None) -> None:
    """导入文件（全勾上）并可选地点一个入口。

    入口要**点**：参数坞开局是收起的（用户 2026-09-25 定"开界面时上面
    什么都不选、右边参数栏是隐藏的"），图注里写了"parameter dock on the
    right"的那些场景不点它就会拍到一张没有参数坞的图。
    """
    window.add_files(list(paths), select=True)
    if entrance:
        window.entrance_buttons[entrance].click()
    settle(200)


# ══ 各场景 ══════════════════════════════════════════════════════
def shot_main(window) -> None:
    """主窗口：LaB₆ 积到 1D，右侧参数坞停在 1D 页。"""
    add_all(window, [LAB6], entrance="1D")
    window.view_buttons["1D"].click()
    wait_for(lambda: "1D|" + LAB6 in window.plot_docks
             and len(content_of(window, "1D|" + LAB6).axes_1d.lines) > 0)
    save(window, "gui_main")


def shot_compare(window) -> None:
    """对比：两张 LMFP 叠图（带图例）。"""
    add_all(window, LMFP[:2], entrance="对比")
    window.compare_btn.click()
    wait_for(lambda: any(k.startswith("对比|")
                         and len(content_of(window, k).axes_1d.lines) >= 2
                         for k in window.plot_docks))
    save(window, "gui_compare")


def shot_customize(window) -> None:
    """Customize 对话框（只有对话框本体，460×542）。

    不走 _open_customize_dialog 那条路（它会 dlg.exec() 开模态事件
    循环，offscreen 下会一直等在那儿 ✗ 踩过）：自己建、show、抓、关。
    """
    from xrd_toolkit.gui.customize import _build_customize_dialog
    # 用**对比面板**：只有它多出"曲线颜色"一节（README 图注写了
    # "per-curve colors (Compare panels)"，旧图也是这么拍的）
    add_all(window, LMFP[:2], entrance="对比")
    window.compare_btn.click()
    wait_for(lambda: any(k.startswith("对比|")
                         and len(content_of(window, k).axes_1d.lines) >= 2
                         for k in window.plot_docks))
    key = next(k for k in window.plot_docks if k.startswith("对比|"))
    dock = window.plot_docks[key]
    content = content_of(window, key)
    dialog = _build_customize_dialog(window, dock, content.axes_1d,
                                     content.figure)
    dialog.show()
    settle(500)
    dialog.adjustSize()
    settle(300)
    dialog.grab().save(str(OUT / "gui_customize.png"))
    print(f"  ✓ showcase/gui/gui_customize.png "
          f"({dialog.width()}×{dialog.height()})")
    dialog.close()


def shot_views(window) -> None:
    """2D / 剖面 / 瀑布 三块面板（大窗口 2800×1800）。"""
    add_all(window, [LAB6])
    for name in ("2D", "剖面", "瀑布"):
        window.view_buttons[name].click()
    wait_for(lambda: all(any(k.startswith(v + "|") for k in window.plot_docks)
                         for v in ("2D", "剖面", "瀑布")), timeout_s=240)
    # 等三块都画出来（瀑布最慢）
    ax_w = content_of(window, "瀑布|" + LAB6).axes_waterfall
    wait_for(lambda: len(ax_w.lines) > 0, timeout_s=240)
    settle(600)
    from xrd_toolkit.gui.panels import _arrange
    _arrange(window, "横排")      # 三块并排（图注：2D / 剖面 / 瀑布）
    settle(600)
    window.resize(2800, 1800)
    settle(600)
    window.grab().save(str(OUT / "gui_views.png"))
    print("  ✓ showcase/gui/gui_views.png  (2800×1800)")


def shot_batch(window) -> None:
    """批量：导文件夹 → 积分（日志 k/n）→ 导出 txt + CSV 总表。"""
    from xrd_toolkit.gui import plot_export as gui_export
    add_all(window, LMFP, entrance="1D")
    window.view_buttons["1D"].click()
    wait_for(lambda: sum(1 for p in LMFP
                         if "1D|" + p in window.plot_docks
                         and getattr(window.plot_docks["1D|" + p],
                                     "last_tth", None) is not None) == 3,
             timeout_s=240)
    # 导出：弹窗换成脚本返回值（走真实的写盘链路）
    dialog_result = {"dir": Path(ROOT / "outputs"), "suffix": ".txt",
                     "csv": True, "bg": False}
    with mock.patch.object(gui_export, "_build_export_dialog",
                           return_value=dialog_result):
        gui_export._run_export(window)
    settle(800)
    tile(window)
    save(window, "gui_batch")


def shot_heatmap(window) -> None:
    """热图：三个数据集 + 旁边一条 1D（README 图注就是这么写的）。"""
    add_all(window, LMFP, entrance="对比")
    window.heat_btn.click()
    wait_for(lambda: any(k.startswith("热图|") for k in window.plot_docks),
             timeout_s=240)
    hkey = next((k for k in window.plot_docks if k.startswith("热图|")), None)
    if hkey is not None:
        ax = content_of(window, hkey).axes_heat
        wait_for(lambda: len(ax.images) > 0
                 and ax.images[0].get_array() is not None, timeout_s=240)
    window.view_buttons["1D"].click()
    wait_for(lambda: "1D|" + LMFP[0] in window.plot_docks
             and len(content_of(window, "1D|" + LMFP[0]).axes_1d.lines) > 0,
             timeout_s=240)
    tile(window)      # 热图与那条 1D 并排（图注：with a single 1D alongside）
    save(window, "gui_heatmap")


def shot_background(window) -> None:
    """背景扣除：真 LMFP 上四个手动锚点（原始/基线/结果三线同在）。"""
    from xrd_toolkit.gui import plot_compare as gui_compare
    path = LMFP[0]
    add_all(window, [path], entrance="扣背景")
    window.view_buttons["1D"].click()
    key = "1D|" + path
    wait_for(lambda: key in window.plot_docks
             and len(content_of(window, key).axes_1d.lines) > 0)
    dock = window.plot_docks[key]
    ax = content_of(window, key).axes_1d
    # 切到手动锚点 + 打开拾取开关
    combo = window.params["背景扣除模式"]
    combo.setCurrentIndex(combo.findData("anchor"))
    window.bg_pick_btn.setChecked(True)
    # 在曲线上取四个纯背景位置（避开峰）：用曲线自身的分位挑点
    import numpy as np
    tth = np.asarray(dock.last_tth, dtype=float)
    inten = np.asarray(dock.last_intensity, dtype=float)
    xs = np.linspace(float(tth[0]) + 0.4, float(tth[-1]) - 0.4, 4)
    for x in xs:
        y = float(np.interp(x, tth, inten))
        press = _press_at(ax, x, y)
        gui_compare._anchor_press(window, key, press)
        gui_compare._anchor_release(window, key, press)
        settle(120)
    settle(500)
    save(window, "gui_background")
    # 内容自检（像素看不出线型）：三条线在不在、锚点几个
    styles = [(ln.get_linestyle(), str(ln.get_color()),
               gui_state._is_aux_line(ln) if hasattr(gui_state, "_is_aux_line")
               else None) for ln in ax.lines]
    from xrd_toolkit.gui import plot_panels as gui_plot_panels
    print(f"    轴上 {len(ax.lines)} 条线："
          + " | ".join(f"{ls}{'(辅助)' if gui_plot_panels._is_aux_line(ln) else ''}"
                       for ln, (ls, _, _) in zip(ax.lines, styles)))
    print(f"    锚点 {len(window.bg_anchors.get(str(dock.panel_file), []))} 个，"
          f"辅助线 {sum(1 for ln in ax.lines if gui_plot_panels._is_aux_line(ln))} 条")


def _press_at(ax, xdata: float, ydata: float):
    """造一个"在 (xdata, ydata) 处按下并松手"的事件（锚点两段式接口）。"""
    from types import SimpleNamespace
    px, py = ax.transData.transform((xdata, ydata))
    return SimpleNamespace(inaxes=ax, button=1, x=px, y=py,
                           xdata=xdata, ydata=ydata)


def shot_calib(window) -> None:
    """校准页：跑过 ① 自动 + ③ 二次精修（三列 + Δ + 结论）。"""
    add_all(window, [LAB6])
    window.calib_btn.click()          # 进校准工作台
    window.calib_pixel_chk.setChecked(True)
    settle(400)
    # ① 自动校准（真 pyFAI，慢）
    auto_btn = window.findChild(QPushButton, "start_auto_calib")
    refine_btn = window.findChild(QPushButton, "start_refined_calib")
    auto_btn.click()
    wait_for(lambda: bool(getattr(window, "calib_state", {}).get("results")),
             timeout_s=300)
    settle(600)
    # ③ 二次精修（按钮要 current 存在才可用）
    if refine_btn is not None and refine_btn.isEnabled():
        refine_btn.click()
        wait_for(lambda: len(window.calib_state.get("results", [])) >= 2,
                 timeout_s=300)
        settle(600)
    save(window, "gui_calib", size=(1500, 1000))
    # 把日志里的指标行打出来：README 图注引用那些数字，重拍后要跟着更新
    # （指标在**完整**结果里；state["results"] 存的是裁剪过的表格副本）
    for line in window.log_text.toPlainText().splitlines():
        if "环位偏差" in line or "采纳" in line or "保持" in line:
            print("    " + line)


SHOTS = {"gui_main": shot_main, "gui_compare": shot_compare,
         "gui_customize": shot_customize, "gui_views": shot_views,
         "gui_batch": shot_batch, "gui_heatmap": shot_heatmap,
         "gui_background": shot_background, "gui_calib": shot_calib}


def main() -> int:
    ap = argparse.ArgumentParser(description="重拍 GUI 展示图")
    ap.add_argument("names", nargs="*", choices=[*SHOTS, []],
                    help="只拍这几张（默认全部）")
    args = ap.parse_args()
    names = args.names or list(SHOTS)
    global LAB6, LMFP
    lab6 = find_data("lab6*.tif", 1)
    lmfp = find_data("LMFP*.tif", 3)
    if not lab6 or len(lmfp) < 3:
        print("找不到足够的真数据：需要 1 张 lab6*.tif + 3 张 LMFP*.tif，"
              "找过这些目录：")
        for d in DATA_DIRS:
            print(f"  {d}")
        print("真数据不入仓库（隐私），换机器要自己拷（见 check_env.py）。")
        return 2
    LAB6, LMFP = lab6[0], lmfp
    print("用到的数据：", LAB6, "|", ", ".join(LMFP))
    OUT.mkdir(parents=True, exist_ok=True)
    QApplication.instance() or QApplication([])
    print(f"项目根：{ROOT}\n输出目录：{OUT.relative_to(ROOT)}\n")
    for name in names:
        print(f"[{name}]")
        window = create_window()
        try:
            SHOTS[name](window)
        finally:
            window.hide()      # 显示过的窗口直接 close 会弹保存询问
            window.close()
        if window.parent() is not None:
            window.deleteLater()
        QApplication.processEvents()
    print("\n完成。看一眼再决定要不要提交。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
