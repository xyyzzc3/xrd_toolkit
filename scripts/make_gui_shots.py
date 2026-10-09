#!/usr/bin/env python3
"""重拍 GUI 展示图（showcase/gui/*.png）——README 图墙用的那 8 张。

什么时候跑：**界面外观变了就重拍**（改了工具栏/面板壳/配色/对话框
之后，README 里的图还是旧样子，评审一眼能看出来）。

2026-10-09 起两条重拍规矩（用户 2026-10-08："说明书的图抠得也太差
了"）：① 面板一律**铺满绘图区**再抓（`fit_panel`）——默认 505×420
摆在 1600×1000 里一半是灰底，缩到 README/说明书宽度后图小得没法看；
② 要认线的图（对比/热图/瀑布）勾上 [显示数据名]（默认关，展示图得
看得出哪条是哪条）。

场景与文档里的图注一一对应（改图注前先看这里；2026-09-25 起 README
只放三张 GUI 图，其余在 docs/NOTES.md）：
    gui_main        LaB₆ 积到 1D，右侧参数坞（1D 页）        → README
    gui_calib       校准页三列 + Δ + 结论（自动取点两轮）     → README
    gui_heatmap     三个数据集的热图 + 旁边一条 1D            → README
    gui_compare     同一条曲线的原始 vs 扣背景产物（短名图例）→ NOTES
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
产物缓存与配方写在系统临时目录，不碰 outputs/（2026-10-03 定）。
"""
import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")
sys.path.insert(0, str(ROOT / "src"))

# 重拍图不碰用户的文件区（用户 2026-10-03 定："不是用户自己操作的，
# 就不应该出现在文件区"）：产物缓存与配方写进系统临时目录，必须在
# import xrd_toolkit 之前设。图本身不受影响——每张都是当场算的
# （重拍前先看配色的确定性规则：脚本头部的 MPLBACKEND=Agg 那条）。
_SCRATCH = Path(tempfile.mkdtemp(prefix="xrd_shots_"))
os.environ.setdefault("XRD_STAGE_CACHE", str(_SCRATCH / "stage"))
os.environ.setdefault("XRD_RECIPES", str(_SCRATCH / "recipes.json"))

# 批处理那张图的"导出"也走临时目录（2026-10-09：此前 demo 导出落进
# 仓库 outputs/，每重拍一次留一个 导出_时间戳_原始/ 文件夹——翻了翻
# 有四个，全是这个脚本的）。用 /tmp 短前缀：日志会打印真实路径，截图
# 里短一点好看，收尾 rmtree 掉。
_EXPORT_DEMO = Path(tempfile.mkdtemp(prefix="xrd_export_demo_", dir="/tmp"))

from unittest import mock                                  # noqa: E402

from PySide6.QtCore import Qt                              # noqa: E402
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


def area_size(window):
    """绘图区（MDI 视口）的 (宽, 高)——先 window.resize 定窗口再量它。"""
    vp = window.mdi.viewport().size()
    return vp.width(), vp.height()


def force_layout(window) -> None:
    """offscreen 下 grab 一把把布局推出来（原因见 fit_dock）。

    凡是"先量视口、再按视口分格"的场景（views / batch / heatmap）都
    要先调它——不调的话量到的是没布局时的 640×480。
    """
    window.grab()
    settle(300)


def fit_dock(window, dock, rect=None) -> None:
    """把面板摆到 rect=(x, y, w, h)（默认铺满绘图区）并抬到最上层。

    重拍专用，见文件头部 2026-10-09 的第 ① 条规矩：默认 505×420 的
    面板摆在 1600×1000 的画布里一半是灰底，缩到 README/说明书宽度后
    图小得没法看。**先 window.resize 再调这里**；save() 里那次同尺寸
    resize 是空操作，不会把摆好的位置顶掉。

    两次 window.grab() 不是为了图：offscreen + 不 show 的窗口，
    processEvents 推不动布局（实测 resize(1600,1000) 之后 MDI 视口
    还停在 640×480；`layout().activate()`、`updateGeometry()` 都没用，
    只有 grab() 会真的渲染一遍、把布局带出来）。第一次 grab 让视口
    是真实尺寸，第二次让刚改过尺寸的子窗口把**内容部件也跟到位**
    （不 grab 的话 dock 1312×738 了、内容还是 500×326）。
    """
    window.grab()
    vw, vh = area_size(window)
    x, y, w, h = rect or (4, 4, vw - 12, vh - 12)
    dock.move(x, y)
    dock.resize(w, h)
    window.grab()
    window.mdi.setActiveSubWindow(dock)   # 抬到最上层（级联下免得被盖）
    settle(500)


def fit_panel(window, key: str, rect=None) -> None:
    """按面板键铺（plot_docks 里的常规图面板）。"""
    fit_dock(window, window.plot_docks[key], rect)


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


def check_only(window, paths) -> None:
    """只勾这组文件（其余全不勾）——点出图按钮之前要（重新）保证的事。

    文件栏一冒出新产物，"新产物 = 上一轮勾选清零"（2026-10-01 甲）就把
    对号清了。以前靠上一段留下的勾选、缓存又是热的（产物早就在、不算
    "新"）；2026-10-03 起产物缓存被隔离到临时目录（见文件头部），
    **每次都是冷缓存**——凡是"点一下 A、产物落下、再点 B"的段落都必须
    自己重勾，否则 B 只记一句"未勾选任何项"，截图静默少一块。
    """
    want = set(paths)
    for i in range(window.file_list.count()):
        item = window.file_list.item(i)
        item.setCheckState(Qt.Checked if item.data(Qt.UserRole) in want
                           else Qt.Unchecked)
    for g in window.file_list.groups():
        g.setCheckState(Qt.Unchecked)
    QApplication.processEvents()


# ══ 各场景 ══════════════════════════════════════════════════════
def _set_manual_anchors(window, key: str, n: int = 4) -> None:
    """在 1D 面板上沿曲线取 n 个纯背景锚点（背景/对比两张共用）。"""
    from xrd_toolkit.gui import plot_compare as gui_compare
    import numpy as np
    dock = window.plot_docks[key]
    ax = content_of(window, key).axes_1d
    # 切到手动锚点 + 打开拾取开关
    combo = window.params["背景扣除模式"]
    combo.setCurrentIndex(combo.findData("anchor"))
    window.bg_pick_btn.setChecked(True)
    # 在曲线上取四个纯背景位置（避开峰）：用曲线自身的分位挑点
    tth = np.asarray(dock.last_tth, dtype=float)
    inten = np.asarray(dock.last_intensity, dtype=float)
    xs = np.linspace(float(tth[0]) + 0.4, float(tth[-1]) - 0.4, n)
    for x in xs:
        y = float(np.interp(x, tth, inten))
        press = _press_at(ax, x, y)
        gui_compare._anchor_press(window, key, press)
        gui_compare._anchor_release(window, key, press)
        settle(120)
    settle(500)


def shot_main(window) -> None:
    """主窗口：LaB₆ 积到 1D，右侧参数坞停在 1D 页。"""
    add_all(window, [LAB6], entrance="1D")
    window.view_buttons["1D"].click()
    key = "1D|" + LAB6
    wait_for(lambda: key in window.plot_docks
             and len(content_of(window, key).axes_1d.lines) > 0)
    window.resize(1600, 1000)          # 先定窗口，再按绘图区铺满
    settle(400)
    fit_panel(window, key)
    save(window, "gui_main")


def shot_compare(window) -> None:
    """对比：同一条曲线的"没扣背景 vs 扣了背景"（短名图例）。

    旧场景是两张连续 LMFP 叠图——两帧几乎重合，画出来像一条线，
    "对比"看不出名堂（2026-10-09 重拍时改）。改成同一条曲线的两个
    阶段：**1D 产物**（按当前配置积出来的原样）对比**处理产物**（扣完
    背景），两条线一高一低，正是《使用说明》第 7 节的例子（"原始数据 /
    1D 产物 / 处理产物都行"）。

    为什么不拿**原始条目**去比：原始条目取数是"产物优先"（_curve_for，
    设计如此）——这个窗口里刚存过扣背景产物，勾原始条目会拿到**处理
    后**的那一份，两条又画成同一条线（2026-10-09 初版重拍实测：
    "复用处理产物 2 条"、图上一团）。要"原样的那条"，就点「1D 产物」
    那条目录里的条目——产物条目说的是那一份产物，直接读盘。
    """
    from xrd_toolkit.gui import sources as gui_sources
    path = LMFP[0]
    add_all(window, [path], entrance="处理")
    window.view_buttons["1D"].click()
    key = "1D|" + path
    wait_for(lambda: key in window.plot_docks
             and len(content_of(window, key).axes_1d.lines) > 0)
    _set_manual_anchors(window, key)
    window.bg_pick_btn.setChecked(False)      # 拍图状态收干净
    # [存成产物]：一个没勾 = 存编辑对象这一张（合并语义，见 app.py）
    window.proc_save_btn.click()
    wait_for(lambda: any(s.kind == gui_sources.BG
                         for s in gui_sources.all_sources(window)))
    # 只勾 1D 产物 + 处理产物。**按 kind 挑、不能按路径**：三样条目的
    # UserRole 都是源文件路径，check_only 那套会把同一条曲线勾两遍。
    # 也**不要**再去动组节点的对号——组上的 setCheckState 会级联到
    # 子项（file_dock.on_item_changed），后手会把刚勾好的产物又清掉；
    # 叶子改完，组态由 itemChanged 自动重算。
    for s in gui_sources.all_sources(window):
        s.item.setCheckState(Qt.Checked
                             if s.kind in (gui_sources.ONED, gui_sources.BG)
                             else Qt.Unchecked)
    QApplication.processEvents()
    window.compare_btn.click()
    wait_for(lambda: any(k.startswith("对比")
                         and len(content_of(window, k).axes_1d.lines) >= 2
                         for k in window.plot_docks), timeout_s=240)
    # [显示数据名] 要**面板出来之后**再勾：新面板的显示参数刻意从默认
    # 起步（panel_state._data_snapshot："开新图永远是默认长相"），先勾
    # 再开图会被默认值盖掉、图例根本不画。勾上走即改即画那条路
    # （_refresh_compare 把控件值写进本面板快照再重画）
    window.params["显示数据名"].setChecked(True)
    settle(500)
    window.resize(1600, 1000)
    settle(400)
    fit_panel(window, next(k for k in window.plot_docks
                           if k.startswith("对比")))
    save(window, "gui_compare")


def shot_customize(window) -> None:
    """Customize 对话框（只有对话框本体，460×542）。

    不走 _open_customize_dialog 那条路（它会 dlg.exec() 开模态事件
    循环，offscreen 下会一直等在那儿 ✗ 踩过）：自己建、show、抓、关。
    """
    from xrd_toolkit.gui.customize import _build_customize_dialog
    # 用**对比面板**：只有它多出"曲线颜色"一节（2026-10-08 对话框加了
    # 滚动区之后那一节落在打开时的视口以下，图拍的就是打开时的样子）
    add_all(window, LMFP[:2], entrance="对比")
    window.compare_btn.click()
    wait_for(lambda: any(k.startswith("对比")
                         and len(content_of(window, k).axes_1d.lines) >= 2
                         for k in window.plot_docks))
    key = next(k for k in window.plot_docks if k.startswith("对比"))
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
    # 瀑布 χ 刻度（默认关）：面板出来之后才勾——先勾会被新面板的默认
    # 盖掉（原因见 shot_compare 那处注释）；勾上由 _refresh_name_labels
    # 就地重画瀑布
    window.params["显示数据名"].setChecked(True)
    settle(500)
    # 布局：左上近方形的 2D，右上剖面，底下整行给瀑布（36 个扇区横着
    # 才看得清）。旧场景按"每类一行"平铺，三块挤在左上角、右边和
    # 底下全是灰（2026-10-09 重拍时改）。
    window.resize(2800, 1800)
    settle(800)
    force_layout(window)
    vw, vh = area_size(window)
    left = int(vw * 0.34)
    top_h = int((vh - 12) * 0.5)
    fit_panel(window, "2D|" + LAB6, (4, 4, left, top_h))
    fit_panel(window, "剖面|" + LAB6, (left + 8, 4, vw - left - 16, top_h))
    fit_panel(window, "瀑布|" + LAB6, (4, top_h + 12, vw - 12,
                                       vh - top_h - 20))
    settle(600)
    window.grab().save(str(OUT / "gui_views.png"))
    print("  ✓ showcase/gui/gui_views.png  (2800×1800)")


def shot_batch(window) -> None:
    """批量：导文件夹 → 积分（日志 k/n）→ 导出 txt + 全部数据总表。"""
    from xrd_toolkit.gui import plot_export as gui_export
    add_all(window, LMFP, entrance="1D")
    window.view_buttons["1D"].click()
    wait_for(lambda: sum(1 for p in LMFP
                         if "1D|" + p in window.plot_docks
                         and getattr(window.plot_docks["1D|" + p],
                                     "last_tth", None) is not None) == 3,
             timeout_s=240)
    # 上面这次积分落了新产物，勾选已被清（甲）；导出读的是勾选状态，
    # 不重勾就会拍到一段"未勾选任何项"的空日志
    check_only(window, LMFP)
    # 导出：弹窗换成脚本返回值（走真实的写盘链路）
    dialog_result = {"dir": _EXPORT_DEMO, "suffix": ".txt",
                     "csv": True, "bg": False}
    with mock.patch.object(gui_export, "_build_export_dialog",
                           return_value=dialog_result):
        gui_export._run_export(window)
    # 等导出真的写完（"已导出 …" ×3 + "导出完成：…" 都在日志尾巴上，
    # 图注要拍的就是它们；settle 不够，导出是后台任务）
    wait_for(lambda: "导出完成" in window.log_text.toPlainText(), timeout_s=120)
    settle(400)
    # 三块 1D 一字排开铺满（旧场景"每类一行"排出来右边一大片灰）
    window.resize(1600, 1000)
    settle(400)
    force_layout(window)
    vw, vh = area_size(window)
    col_w = (vw - 20) // 3
    for i, p in enumerate(LMFP):
        fit_panel(window, "1D|" + p, (4 + i * (col_w + 8), 4, col_w, vh - 12))
    # 日志滚到底：图注要的 k/n 与导出完成行都在末尾
    bar = window.log_text.verticalScrollBar()
    bar.setValue(bar.maximum())
    save(window, "gui_batch")


def shot_heatmap(window) -> None:
    """热图：三个数据集 + 旁边一条 1D（README 图注就是这么写的）。"""
    add_all(window, LMFP, entrance="对比")
    window.heat_btn.click()
    wait_for(lambda: any(k.startswith("热图") for k in window.plot_docks),
             timeout_s=240)
    hkey = next((k for k in window.plot_docks if k.startswith("热图")), None)
    if hkey is not None:
        ax = content_of(window, hkey).axes_heat
        wait_for(lambda: len(ax.images) > 0
                 and ax.images[0].get_array() is not None, timeout_s=240)
    # 行名（默认关）：面板出来之后才勾——先勾会被新面板的默认盖掉
    # （原因见 shot_compare 那处注释）
    window.params["显示数据名"].setChecked(True)
    settle(400)
    # 热图那一步按各文件自己的设置补算了 1D（新产物 → 勾选清零，甲）：
    # 不重勾这一步点 1D 只会静默少一块（图注要求的 parallel 1D）。
    # **只勾第一条**：图注是"旁边一条 1D"，全勾会开出三块互相挤
    check_only(window, [LMFP[0]])
    window.view_buttons["1D"].click()
    wait_for(lambda: "1D|" + LMFP[0] in window.plot_docks
             and len(content_of(window, "1D|" + LMFP[0]).axes_1d.lines) > 0,
             timeout_s=240)
    check_only(window, LMFP)   # 拍图时文件栏回到"三条都选中"的样子
    # 热图大块在左、那条 1D 在右，两块铺满
    window.resize(1600, 1000)
    settle(400)
    force_layout(window)
    vw, vh = area_size(window)
    heat_w = int(vw * 0.60)
    fit_panel(window, hkey, (4, 4, heat_w, vh - 12))
    fit_panel(window, "1D|" + LMFP[0],
              (heat_w + 8, 4, vw - heat_w - 16, vh - 12))
    # 编辑对象拨回热图：中间点过一次 1D，焦点跑到那条 1D 面板上，参数坞
    # 会回放成"显示数据名未勾选"，跟图上画着的行名打架。走"点面板"的
    # 同一条路（app 的 eventFilter 也是调它）拨回来，坞里就自洽了
    gui_state._set_focus(window, hkey, window.plot_docks[hkey].windowTitle())
    settle(300)
    save(window, "gui_heatmap")


def shot_background(window) -> None:
    """背景扣除：真 LMFP 上四个手动锚点（原始/基线/结果三线同在）。"""
    path = LMFP[0]
    add_all(window, [path], entrance="处理")
    window.view_buttons["1D"].click()
    key = "1D|" + path
    wait_for(lambda: key in window.plot_docks
             and len(content_of(window, key).axes_1d.lines) > 0)
    ax = content_of(window, key).axes_1d
    _set_manual_anchors(window, key)
    window.resize(1600, 1000)
    settle(400)
    fit_panel(window, key)
    save(window, "gui_background")
    # 内容自检（像素看不出线型）：三条线在不在、锚点几个
    styles = [(ln.get_linestyle(), str(ln.get_color()),
               gui_state._is_aux_line(ln) if hasattr(gui_state, "_is_aux_line")
               else None) for ln in ax.lines]
    from xrd_toolkit.gui import plot_panels as gui_plot_panels
    print(f"    轴上 {len(ax.lines)} 条线："
          + " | ".join(f"{ls}{'(辅助)' if gui_plot_panels._is_aux_line(ln) else ''}"
                       for ln, (ls, _, _) in zip(ax.lines, styles)))
    panel_file = window.plot_docks[key].panel_file
    print(f"    锚点 {len(window.bg_anchors.get(str(panel_file), []))} 个，"
          f"辅助线 {sum(1 for ln in ax.lines if gui_plot_panels._is_aux_line(ln))} 条")


def _press_at(ax, xdata: float, ydata: float):
    """造一个"在 (xdata, ydata) 处按下并松手"的事件（锚点两段式接口）。"""
    from types import SimpleNamespace
    px, py = ax.transData.transform((xdata, ydata))
    return SimpleNamespace(inaxes=ax, button=1, x=px, y=py,
                           xdata=xdata, ydata=ydata)


def _scroll_calib_to_table(window) -> None:
    """把校准页滚到「数据」组（三列 + Δ + 结论行）入镜的位置。

    默认停在页顶，只看得见操作/自动/手动区——2026-10-04 重拍时发现旧图
    一直没拍到图注承诺的结果区，一并修正。
    """
    scroll = getattr(window, "calib_scroll", None)
    combo = getattr(window, "calib_slot_combo", {}).get("current")
    if scroll is None or combo is None:
        return
    content = scroll.widget()
    bar = scroll.verticalScrollBar()
    y = combo.mapTo(content, combo.rect().topLeft()).y()
    bar.setValue(max(bar.minimum(), y - 10))
    settle(150)


def _has_result(window, name: str) -> bool:
    """校准累积列表里有没有这条结果（等任务真完成，别被起点骗了）。"""
    return any(r["name"] == name for r in
               (getattr(window, "calib_state", {}) or {}).get("results", []))


def shot_calib(window) -> None:
    """校准页：跑两次 自动取点（A/B 两槽都有结果：三列 + Δ + 结论）。"""
    add_all(window, [LAB6])
    window.calib_btn.click()          # 进校准工作台
    window.calib_pixel_chk.setChecked(True)
    settle(400)
    # 自动校准（真 pyFAI，慢；2026-10-08 起校准页只有这一个动作入口）
    auto_btn = window.findChild(QPushButton, "start_auto_calib")
    # 等**这一条具体结果**进列表——results 里本来就有借来的"原始1"，
    # 拿"非空"当完成信号会在任务刚起步时就截图（2026-10-08 实拍到了：
    # 表格 A/B 还空着、结论写"还没有可比的候选"）
    auto_btn.click()
    wait_for(lambda: _has_result(window, "自动取点1"), timeout_s=300)
    settle(400)
    auto_btn.click()                  # 再跑一次 → 填 B 槽（图注承诺 A/B）
    wait_for(lambda: _has_result(window, "自动取点2"), timeout_s=300)
    settle(600)
    # 校准面板铺满（面板越大标题行越不挤；旧图标题被两端裁掉）
    window.resize(1500, 1000)
    settle(600)
    if hasattr(window, "calib_dock"):
        fit_dock(window, window.calib_dock)
    # 图注承诺的是结果区（三列 + 指标 + 结论行）——滚下去让它真的入镜
    _scroll_calib_to_table(window)
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
        # 截图里不要那条只装「帮助」的系统菜单栏（2026-10-08 用户："窗口
        # 最上方还是有一行单独的帮助"）：它实际住在 macOS 的屏幕顶部系统
        # 菜单栏、不占窗口，Windows/Linux 上干脆是隐藏的——只有离屏渲染
        # 会把它画成窗口内一行。隐掉，截图才与用户看到的窗口一致。
        window.menuBar().setVisible(False)
        try:
            SHOTS[name](window)
        finally:
            window.hide()      # 显示过的窗口直接 close 会弹保存询问
            window.close()
        if window.parent() is not None:
            window.deleteLater()
        QApplication.processEvents()
    print("\n完成。看一眼再决定要不要提交。")
    shutil.rmtree(_EXPORT_DEMO, ignore_errors=True)   # 演示导出不留痕
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
