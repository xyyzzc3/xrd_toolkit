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
from unittest import mock
from pathlib import Path

# 项目根 = 本文件所在 scripts/ 的上一级（与 scripts/ 下其他脚本同一惯例：
# 不依赖当前工作目录，从 __file__ 推导）
ROOT = Path(__file__).resolve().parents[1]

# 界面探针不开真窗口（CI / 无显示环境也能跑）；要在自己屏幕上看着跑，
# 把下面这行去掉或改成 "cocoa"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(ROOT / "src"))

import numpy as np                                       # noqa: E402
from matplotlib.backend_bases import MouseEvent          # noqa: E402
from PySide6.QtCore import Qt                            # noqa: E402
from PySide6.QtWidgets import QApplication               # noqa: E402

from xrd_toolkit.gui import panel_state as gui_state     # noqa: E402
from xrd_toolkit.gui import plot_panels as gui_plot_panels   # noqa: E402
from xrd_toolkit.gui import plot_views as gui_views      # noqa: E402
from xrd_toolkit.gui import app as gui_app               # noqa: E402
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
    window.add_files([lab6], select=True)
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
    window.add_files([lmfp], select=True)   # 导入默认不勾选：探针要两个都选上
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


def check_batch_background(window, lab6: str, lmfp: str) -> None:
    """D. 批量处理产物（用户 2026-09-24 的流程）。

    1D 产物 → 在一张图上放锚点 → [批量处理] → 各存一份 → 对比直接读。
    锚点**只传 2θ**：这里用一个锚点强度明显不同的检查来印证（同一个 2θ
    在两个文件上的 y 必须不同，除非两条曲线恰好一样）。
    """
    from xrd_toolkit.services import stage_cache
    print("\nD. 批量处理（真数据）")
    key1d = "1D|" + lab6
    dock = window.plot_docks.get(key1d)
    if dock is None:
        report(False, "批量处理：没有 1D 面板（前面的检查没过）")
        return
    # 确保两个文件都勾着（对比/批量都按勾选走）
    for i in range(window.file_list.count()):
        window.file_list.item(i).setCheckState(Qt.Checked)
    # 编辑对象 = lab6 那张；手动锚点模式 + 拾取开关
    gui_state._set_focus(window, key1d, Path(lab6).name)
    combo = window.params["背景扣除模式"]
    combo.setCurrentIndex(combo.findData("anchor"))
    window.params["背景窗口 (°)"].setValue(1.0)
    dock.params_snapshot = dict(
        dock.params_snapshot or {},
        **{"背景扣除模式": "anchor", "背景窗口 (°)": 1.0})
    # 放两个锚点：取曲线 10%/60% 处的真实点（背景位置，非峰）
    tth = np.asarray(dock.last_tth, dtype=float)
    inten = np.asarray(dock.last_intensity, dtype=float)
    xs = [float(tth[int(len(tth) * f)]) for f in (0.1, 0.6)]
    key_a = str(gui_views._bg_path_of(dock))
    window.bg_anchors[key_a] = [(x, float(np.interp(x, tth, inten)))
                                for x in xs]
    window.proc_batch_btn.click()
    QApplication.processEvents()
    log = window.log_text.toPlainText()
    report("批量处理完成" in log, "批量处理：跑完",
           [ln for ln in log.splitlines() if ln.startswith("批量处理完成")][:1])
    # lmfp 未必开过 1D 面板（本探针前面只给它开了对比/热图）→ 从文件
    # 列表拿它的路径字符串（面板键与锚点键都用这一份）
    other = [k for k in window.bg_anchors if k != key_a]
    report(len(other) == 1, "另一个文件也拿到了锚点", other[:1])
    if not other:
        return
    path_b = other[0]
    got_a = window.bg_anchors.get(key_a) or []
    got_b = window.bg_anchors.get(path_b) or []
    report(len(got_a) == len(got_b) == 2, "两个文件各拿到一套锚点",
           f"A={len(got_a)} B={len(got_b)}")
    if got_a and got_b:
        report([x for x, _ in got_a] == [x for x, _ in got_b],
               "锚点 2θ 照搬（位置跨文件）")
        report(abs(got_a[0][1] - got_b[0][1]) > 1e-9,
               "强度各取各的（两个文件曲线不同 → y 不同）",
               f"A={got_a[0][1]:.1f} B={got_b[0][1]:.1f}")
    # 产物在不在 + 对比是不是读它
    geom = gui_state._collect_geometry(window)
    npt = int(window.params["输出点数"].value())
    n_bg = 0
    for path in (key_a, path_b):
        # 用同一个面板取"控件那部分"参数（模式/窗口/截断都在坞里，窗级），
        # 锚点按 path 取——这正是 _bg_settings 的口径
        st = gui_state._bg_settings(window, dock, path)
        if stage_cache.load_proc(path, config=window.config_name, npt=npt,
                               tth_min=geom.get("tth_min_deg"),
                               tth_max=geom.get("tth_max_deg"),
                               settings=st) is not None:
            n_bg += 1
    report(n_bg == 2, "两份扣背景产物都落盘了", f"{n_bg}/2")
    before = len(window.log_text.toPlainText())
    window.compare_btn.click()
    wait_until(lambda: "对比完成" in window.log_text.toPlainText())
    tail = window.log_text.toPlainText()[before:]
    report("处理产物" in tail, "对比直接读扣背景产物",
           [ln for ln in tail.splitlines() if "对比完成" in ln][:1])


def check_stage_folders(window, lab6: str) -> None:
    """E. 阶段文件夹（文件栏里的产物分组，用户 2026-09-25 的流程）。

    前面的 D 已经跑过 [批量处理] → 文件栏里该长出一个"扣背景 …"组；
    勾整组 → [对比] 直接读那批产物（不重算）；产物条目出 1D 图也是读盘
    （面板快照的背景扣除应为「不扣」，免得二次相减）。
    """
    print("\nE. 阶段文件夹（真数据）")
    groups = {g.text(0): g for g in window.file_list.groups()}
    bg_groups = [t for t in groups if t.startswith("处理")]
    report(bool(bg_groups), "文件栏里出现了扣背景分组", bg_groups[:1])
    if not bg_groups:
        return
    group = groups[bg_groups[0]]
    report(group.childCount() >= 2, "分组里有东西", group.childCount())
    # 整组勾上（勾组 = 全选组里子项），别的都取消
    for i in range(window.file_list.count()):
        window.file_list.item(i).setCheckState(Qt.Unchecked)
    group.setCheckState(Qt.Checked)
    QApplication.processEvents()
    kids = [group.child(i) for i in range(group.childCount())]
    report(all(k.checkState() == Qt.Checked for k in kids),
           "勾组 = 组里全勾")
    # 产物条目出 1D 图：读盘画线，日志里能看到"直接读盘不重算"
    before = len(window.log_text.toPlainText())
    window.view_buttons["1D"].click()
    ok = wait_until(lambda: "直接读盘不重算" in window.log_text.toPlainText())
    report(ok, "产物条目出 1D 图（读盘，不重算）")
    tail = window.log_text.toPlainText()[before:]
    report("背景扣除已置「不扣」" in tail, "产物面板不再二次扣背景")
    # 对比：整组勾着点 [对比] → 曲线数与组里条目数一致
    before = len(window.log_text.toPlainText())
    window.compare_btn.click()
    wait_until(lambda: "对比完成" in window.log_text.toPlainText()[before:])
    tail = window.log_text.toPlainText()[before:]
    report("处理产物" in tail, "对比读到的是扣背景产物",
           [ln for ln in tail.splitlines() if "对比完成" in ln][:1])
    ckeys = [k for k in window.plot_docks if k.startswith("对比|")]
    n_curves = max((len(content_of(window, k).axes_1d.lines) for k in ckeys),
                   default=0)
    report(n_curves >= group.childCount(), "整组都画进去了",
           f"{n_curves} 条 / 组里 {group.childCount()} 个")


def check_one_d_product_background(window) -> None:
    """F. 1D 产物条目也能扣背景（用户 2026-09-25 问起的那条）。

    "1D 产物"= 那条原始积分曲线（钉在某份缓存上），不是"已完成"的东西：
    勾它 → [批量处理] → 真扣一份，且**挂在勾的那份 1D 的键下面**；
    而"扣背景产物"条目仍然跳过（再扣就是二次相减）。
    """
    print("\nF. 1D 产物扣背景（真数据）")
    from xrd_toolkit.services import stage_cache
    group = next((g for g in window.file_list.groups()
                  if g.text(0).startswith("1D 产物")), None)
    report(group is not None, "文件栏里有「1D 产物」组",
           group.text(0) if group is not None else None)
    if group is None:
        return
    # 只勾 1D 产物条目，别的都取消
    for i in range(window.file_list.count()):
        window.file_list.item(i).setCheckState(Qt.Unchecked)
    for g in window.file_list.groups():
        for i in range(g.childCount()):
            g.child(i).setCheckState(Qt.Unchecked)
    group.setCheckState(Qt.Checked)
    QApplication.processEvents()
    window.view_buttons["1D"].click()             # 产物条目出图 = 读盘
    ok = wait_until(lambda: "直接读盘不重算" in window.log_text.toPlainText())
    report(ok, "1D 产物条目出图（读盘）")
    # 编辑对象 = 产物面板；切手动锚点 + 放两个锚点
    key = next((k for k in window.plot_docks if k.startswith("1D|1d#")), None)
    if key is None:
        report(False, "1D 产物面板出来了")
        return
    dock = window.plot_docks[key]
    report(dock.params_snapshot.get("背景扣除模式") == "off",
           "1D 产物面板不强制「不扣」（跟原始文件一个待遇）",
           dock.params_snapshot.get("背景扣除模式"))
    gui_state._set_focus(window, key, dock.panel_display)
    combo = window.params["背景扣除模式"]
    combo.setCurrentIndex(combo.findData("anchor"))
    window.params["背景窗口 (°)"].setValue(1.0)
    dock.params_snapshot = dict(dock.params_snapshot or {},
                                **{"背景扣除模式": "anchor",
                                   "背景窗口 (°)": 1.0})
    tth = np.asarray(dock.last_tth, dtype=float)
    inten = np.asarray(dock.last_intensity, dtype=float)
    xs = [float(tth[int(len(tth) * f)]) for f in (0.1, 0.6)]
    window.bg_anchors[str(dock.panel_file)] = [
        (x, float(np.interp(x, tth, inten))) for x in xs]
    before = len(stage_cache.list_batches("bg"))
    window.proc_batch_btn.click()
    QApplication.processEvents()
    log = window.log_text.toPlainText()
    report("条来自 1D 产物" in log, "[批量处理] 收下了 1D 产物条目",
           [ln for ln in log.splitlines()
            if ln.startswith("批量处理完成")][-1:])
    batches = stage_cache.list_batches("bg")
    report(len(batches) == before + 1, "新落了一个扣背景批次",
           f"{before} → {len(batches)}")
    if len(batches) > before:
        # 按**这个文件**取它那一条（批里可能有别的文件）
        item = batches[0]["items"].get(str(Path(dock.panel_file).resolve()))
        report(item is not None, "台账里有这个文件的一条")
        if item is not None:
            import json as _json
            with np.load(stage_cache.CACHE_ROOT / "bg"
                         / f"{item['key']}.npz") as data:
                stored = _json.loads(str(data["meta"]))
            base = key.split("#", 1)[1]
            report(stored.get("base_key") == base,
                   "产物挂在**勾的那份 1D 的键**下面（不按当前设置另算）",
                   f"base={str(stored.get('base_key'))[:8]} 勾的={base[:8]}")
    # 分组刷新出来了
    report(any(g.text(0).startswith("处理") for g in
               window.file_list.groups()), "文件栏里出现新的扣背景分组")


def check_product_delete(window) -> None:
    """G. 文件栏里删产物（用户 2026-09-25 定："所有产物都能在文件那边删除"）。

    单条（右键一条）与整组（右键组名）两条路都走一遍：盘上的 npz 真少、
    分组真跟着变、台账不留空壳。
    """
    print("\nG. 删除产物（真数据）")
    from xrd_toolkit.gui import file_dock as gui_file_dock
    from xrd_toolkit.gui import sources as gui_sources
    from xrd_toolkit.services import stage_cache
    group = next((g for g in window.file_list.groups()
                  if g.text(0).startswith("1D 产物")), None)
    report(group is not None, "有「1D 产物」组可删")
    if group is None or group.childCount() < 2:
        report(False, '组里至少两条（只有 1 条没法验"删一条留一条"）',
               group.childCount() if group is not None else 0)
        return
    # ① 删一条
    before = group.childCount()
    victim = gui_sources.source_of(group.child(0))
    npz = stage_cache.CACHE_ROOT / victim.kind / f"{victim.key}.npz"
    report(npz.exists(), "这一条的产物文件在盘上")
    n = gui_file_dock.drop_product_item(window, group.child(0))
    report(n == 1 and not npz.exists(), "删一条：只在盘上少了这一个文件", n)
    after = next((g for g in window.file_list.groups()
                  if g.text(0).startswith("1D 产物")), None)
    report(after is not None and after.childCount() == before - 1,
           "分组还在、少了一条", f"{before} → "
           f"{after.childCount() if after is not None else '无'}")
    # ② 删整组
    keys = [gui_sources.source_of(after.child(i)) for i in range(after.childCount())]
    n = gui_file_dock.drop_product_group(window, after)
    report(n == len(keys), "删整组：份数与组里条目数一致", f"{n}/{len(keys)}")
    report(all(not (stage_cache.CACHE_ROOT / k.kind / f"{k.key}.npz").exists()
               for k in keys), "整组的产物文件都没了")
    report(not any(g.text(0).startswith("1D 产物")
                   for g in window.file_list.groups()), "分组消失")
    # ③ 扣背景那边同理（台账不留空壳）
    bg = next((g for g in window.file_list.groups()
               if g.text(0).startswith("处理")), None)
    if bg is not None:
        n = gui_file_dock.drop_product_group(window, bg)
        report(n == 0 or n > 0, "扣背景分组整组删除跑通", f"{n} 份")
        report(not any(g.text(0) == bg.text(0)
                       for g in window.file_list.groups()), "那一组也没了")


def check_processing_chain(window, lab6: str) -> None:
    """H. 处理链（背景 / 平滑 / 裁剪，用户 2026-09-25 定稿）在真数据上跑一遍。

    守三件事：① 改参数即重画（平滑削峰、裁剪挖空 + 纵轴跟着放开）；
    ② [批量处理] 把三项一起写进产物（组名与元数据都写明链）；
    ③ 导出文件里裁剪点不写行、头里注明链。
    """
    print("\nH. 处理链（平滑 + 裁剪，真数据）")
    from xrd_toolkit.gui import plot_views as gui_views
    from xrd_toolkit.services import stage_cache
    key = "1D|" + lab6
    dock = window.plot_docks.get(key)
    if dock is None:
        report(False, "处理链：没有 1D 面板（前面的检查没过）")
        return
    gui_state._set_focus(window, key, dock.panel_display)
    ax = content_of(window, key).axes_1d
    raw = np.asarray(dock.last_intensity, dtype=float)
    tth = np.asarray(dock.last_tth, dtype=float)
    peak_x = float(tth[int(np.argmax(raw))])
    raw_peak = float(np.nanmax(raw))
    # ① 平滑：窗口取峰宽量级（0.3°），峰值该明显下降
    window.params["平滑曲线"].setChecked(True)
    window.params["平滑窗口 (°)"].setValue(0.30)
    gui_views._refresh_proc(window)
    QApplication.processEvents()
    shown = np.asarray(ax.lines[0].get_ydata(), dtype=float)
    report(np.nanmax(shown) < raw_peak * 0.95, "平滑削峰（真数据）",
           f"{raw_peak:.0f} → {np.nanmax(shown):.0f}")
    report(len(shown) == len(raw), "平滑不改变点数")
    # ①b 换 Savitzky–Golay：同一个窗口，峰该比滑动平均**高**（保峰）
    w_avg = 0.30
    cb = window.params["平滑方法"]
    cb.setCurrentIndex(cb.findData("savgol"))
    gui_views._refresh_proc(window)
    QApplication.processEvents()
    shown_sg = np.asarray(ax.lines[0].get_ydata(), dtype=float)
    report(np.nanmax(shown_sg) > np.nanmax(shown),
           "同样的窗口下 SG 比滑动平均保峰",
           f"滑动平均 {np.nanmax(shown):.0f} vs SG {np.nanmax(shown_sg):.0f}"
           f"（窗口 {w_avg:g}°，原始 {raw_peak:.0f}）")
    report(bool(window.params["平滑阶数"].isEnabled()), "SG 下阶数可编辑")
    cb.setCurrentIndex(cb.findData("boxcar"))     # 后面的检查回到默认方法
    gui_views._refresh_proc(window)
    QApplication.processEvents()
    shown = np.asarray(ax.lines[0].get_ydata(), dtype=float)

    # ② 裁剪：挖掉最强峰附近 ±0.5°，纵轴自动范围该放开
    lo, hi = peak_x - 0.5, peak_x + 0.5
    # 界面的自然手势：先填起止、再勾上开关（勾上 = 把这一段加进清单）
    window.params["裁剪起点 (°)"].setValue(lo)
    window.params["裁剪终点 (°)"].setValue(hi)
    window.params["裁剪区间"].setChecked(True)
    gui_views._refresh_proc(window)
    QApplication.processEvents()
    shown = np.asarray(ax.lines[0].get_ydata(), dtype=float)
    inside = (tth >= lo) & (tth <= hi)
    report(np.isnan(shown[inside]).all(), "裁剪区间内是空的（图上断开）",
           f"{int(inside.sum())} 点")
    report(np.isfinite(shown[~inside]).all(), "区间外不受影响")
    # ②b 多段：再加一段远处的窄区间，两段都该是空的
    window.params["裁剪起点 (°)"].setValue(float(tth[-1]) - 0.4)
    window.params["裁剪终点 (°)"].setValue(float(tth[-1]) - 0.1)
    gui_app._on_cut_add(window)
    QApplication.processEvents()
    shown2 = np.asarray(ax.lines[0].get_ydata(), dtype=float)
    band2 = (tth >= float(tth[-1]) - 0.4) & (tth <= float(tth[-1]) - 0.1)
    report(np.isnan(shown2[band2]).all() and np.isnan(shown2[inside]).all(),
           "多段裁剪：两段都是空的", window.cut_list_lbl.text())
    # 自动范围按**画出来的那条**算（裁剪掉的部分不参与）——这正是用户要的效果；
    # 注意别用 ax.get_ylim()：面板可能停在手动范围模式，那不代表自动范围
    hi_auto = gui_state._auto_y_range(shown, False)[1]
    hi_raw = gui_state._auto_y_range(raw, False)[1]
    report(hi_auto < hi_raw * 0.5, "裁剪后自动范围放开（大峰不再压扁）",
           f"{hi_raw:.0f} → {hi_auto:.0f}")
    # ③ [批量处理]：三项进产物，组名与元数据写明链
    # 勾上这个文件的**原始条目**（前面的段落删过产物、也清过勾选，这里
    # 不能指望还有谁被勾着——探针踩过：批处理静默地"没有选中"）
    for i in range(window.file_list.count()):
        item = window.file_list.item(i)
        item.setCheckState(Qt.Checked
                           if item.data(Qt.UserRole) == lab6 else Qt.Unchecked)
    for g in window.file_list.groups():
        g.setCheckState(Qt.Unchecked)
    QApplication.processEvents()
    window.proc_batch_btn.click()
    QApplication.processEvents()
    log = window.log_text.toPlainText()
    report("批量处理完成" in log, "批量处理跑完",
           [ln for ln in log.splitlines()
            if ln.startswith("批量处理完成")][-1:])
    group = next((g for g in window.file_list.groups()
                  if g.text(0).startswith("处理后")), None)
    report(group is not None, "文件栏里出现「处理后」分组",
           group.text(0) if group is not None else None)
    if group is not None:
        report("平滑 0.3°" in group.text(0) and "删 " in group.text(0),
               "组名写明这一组做过什么", group.text(0))
    mine = [b for b in stage_cache.list_batches("bg")
            if str(Path(lab6).resolve()) in b["items"]]
    if mine:
        item = mine[0]["items"][str(Path(lab6).resolve())]
        meta = stage_cache.meta_by_key("bg", item["key"])
        report("smooth=boxcar/0.3°" in str(meta.get("chain")),
               "产物元数据里记着链", str(meta.get("chain"))[:60])
    else:
        report(False, "产物元数据里记着链", "没有批次")
    # ④ 导出：裁剪点不写行、头里注明
    import tempfile
    from pathlib import Path as _P
    from xrd_toolkit.gui import plot_export as gui_export
    outdir = _P(tempfile.mkdtemp(prefix="xrd_probe_export_"))
    with mock.patch.object(gui_export, "_build_export_dialog",
                           return_value={"dir": outdir, "suffix": ".txt",
                                         "csv": False, "bg": True}):
        gui_export._run_export(window)
    target = outdir / (lab6 and _P(lab6).stem) / "integrated_2th.txt"
    ok = target.exists()
    report(ok, "导出落盘", str(target.name))
    if ok:
        text = target.read_text()
        report("processed:" in text and "cut:" in text,
               "文件头写明处理链与删除点数",
               [ln for ln in text.splitlines()[:3] if ln.startswith("#")][-1:])
        report("nan" not in text.lower(), "文件里没有 nan 行")


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
        check_batch_background(window, lab6_key, lmfp_key)
        check_stage_folders(window, lab6_key)
        check_one_d_product_background(window)
        check_product_delete(window)
        check_processing_chain(window, lab6_key)
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
