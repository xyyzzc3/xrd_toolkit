"""视图注册表与绘图：出图调度、1D/对比绘图、悬停取点、手势、面板工具栏。

模块图见 panel_state 模块 docstring（本模块在 app.py 之下、
panels.py 之上）。

视图注册表（本模块的扩展点）：_VIEW_BUILDERS / _VIEW_RUNNERS 两张
表，视图名 → 建面板内容 / 跑计算。四个视图（2D / 剖面 / 1D /
瀑布）全部注册；新视图接线 = 往表里加条目（builder / runner），
_build_view_widget / _run_view 的分发骨架不用再动。对比（[对比]
按钮）是 1D 的多文件叠图变体，流程独立（_plot_compare →
_run_compare → _finish_compare），不占注册表。

其余内容：
  - 绘图：_draw_1d / _redraw_compare（对比多曲线 + 图例）/
    _draw_2d（图像 + 对比度 + 束心十字）/ _draw_profile（过束心
    剖面）/ _draw_waterfall（36 扇区堆叠，对齐 CLI 画法）——都读
    该面板自己的参数快照；程序重画不覆盖 Customize 用户改动
    （_snapshot_canvas 先拍现状，_settle_scale/_apply_text_guards
    /_restore_line_styles 保护记账）；
  - 后台任务：各视图 worker（_compute_integration/_compute_image/
    _compute_profile/_compute_waterfall，纯计算，后台线程跑）/
    _spawn（1D 特化）/ _spawn_task（通用版）/ 各 _on_*_done（过期
    结果丢弃，面板关了静默）/ _on_integration_error /
    _on_view_error；_apply_params / _apply_image_params（两个
    [应用] 各管各的）；
  - 悬停取点（_hover_motion/_hover_leave）：白边圆点吸附最近真实
    数据点 + 状态栏坐标；
  - 手势：_pan_*（左键拖 = 平移）/ _wheel_zoom（放大镜点亮时滚轮
    以光标为中心缩放；Ctrl+滚轮 = 总缩放）/ _on_xlim_changed /
    _on_ylim_changed / _connect_axis_sync（范围改动实时写回参数
    快照）；
  - 每面板工具栏：_SlimToolbar（[Home][Zoom][Customize][Save]）/
    _save_panel（存 PNG 置 figure_saved）。
"""
from pathlib import Path

import numpy as np
from matplotlib import cm
from matplotlib.backends.backend_qtagg import (
    FigureCanvasQTAgg, NavigationToolbar2QT)
from matplotlib.colors import LogNorm
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QLabel, QMainWindow, QMdiSubWindow, QPushButton,
    QVBoxLayout, QWidget)

from xrd_toolkit.core.processor import line_profile
from xrd_toolkit.gui.customize import _default_texts, _open_customize_dialog
from xrd_toolkit.gui.panels import (
    _apply_area_zoom, _install_resize_grip, _PanelResizeFilter, _panel_extra,
    _PlotSubWindow, _settle, _toggle_pop_out)
from xrd_toolkit.gui.panel_state import (
    _auto_contrast_values, _auto_y_range, _collect_geometry,
    _compare_shown_curves, _content, _data_snapshot, _display_snapshot,
    _log, _panel_param, _set_focus)
from xrd_toolkit.gui.tasks import BackgroundTask
from xrd_toolkit.services.data_loader import load_diffraction_image
from xrd_toolkit.services.integrator import integrate_1d, integrate_sectors


PLOT_OPEN_W, PLOT_OPEN_H = 500, 300   # 新面板默认画布尺寸（画布真 5:3，
                                      # 面板总高 = 画布 + 工具栏 + 标题栏）


# ══ 文件 → 1D 闭环：选文件 → 后台积分 → 1D 面板出图 ══════════
def _compute_integration(path_str: str, geom: dict, npt: int) -> tuple:
    """后台线程里运行的纯计算：读图 → 全角度积分。

    不碰任何界面控件；异常由 tasks.BackgroundTask 转成 error 信号
    送回主线程。
    """
    image = load_diffraction_image(path_str)
    tth, intensity = integrate_1d(image, npt=npt, **geom)
    return tth, intensity


def _compute_image(path_str: str):
    """后台线程里运行的纯计算：读衍射图（2D 视图只要原图）。

    不碰任何界面控件；异常由 tasks.BackgroundTask 转成 error 信号
    送回主线程。
    """
    return load_diffraction_image(path_str)


def _compute_profile(path_str: str, center, angle_deg: float) -> tuple:
    """后台线程里运行的纯计算：读图 → 过束心的线剖面。

    center = (行, 列)（配置条目的 beam_center），angle_deg = 剖面
    线与水平方向的夹角；返回 (t, 强度)，t = 到束心的带符号距离。
    """
    image = load_diffraction_image(path_str)
    return line_profile(image, center, angle_deg)


def _compute_waterfall(path_str: str, geom: dict, npt: int) -> tuple:
    """后台线程里运行的纯计算：读图 → 36 扇区分区积分。

    与 CLI sector_waterfall 同引擎（默认 36 扇区）。geom 只取
    integrate_sectors 认识的几何键（2θ 范围参数是 integrate_1d
    的，扇区积分走全范围）。
    """
    image = load_diffraction_image(path_str)
    return integrate_sectors(
        image, n_sectors=36, npt=npt,
        pixel_size_m=geom["pixel_size_m"],
        wavelength_m=geom["wavelength_m"],
        dist_m=geom["dist_m"],
        poni1_m=geom["poni1_m"],
        poni2_m=geom["poni2_m"],
        rot1_deg=geom["rot1_deg"],
        rot2_deg=geom["rot2_deg"])


def _run_view(window: QMainWindow, name: str, path: Path, key: str) -> None:
    """对 (视图, 文件) 面板跑对应的计算（注册表分发器，后台线程）。

    通用准备（快照/几何/点数）在这里做一次，具体计算按视图名查
    _VIEW_RUNNERS 交给各 runner。新视图接线 = 往表里加条目，本
    函数不用动。
    """
    runner = _VIEW_RUNNERS.get(name)
    if runner is None:
        _log(window, f"{name} 视图尚未接线（面板占位）")
        return
    # 开工前先刷新参数快照：数据参数 = 控件当前值（计算就用它），
    # 显示参数沿用面板自己的旧快照（重算不改长相）；新面板旧快照
    # 里是默认值，正好从默认起步
    dock = window.plot_docks.get(key)
    if dock is not None:
        dock.params_snapshot = _data_snapshot(window, dock.params_snapshot)
    geom = _collect_geometry(window)
    npt = int(window.params["输出点数"].value())
    runner(window, path, key, geom, npt)


def _run_1d(window: QMainWindow, path: Path, key: str,
            geom: dict, npt: int) -> None:
    """1D = 全角度积分：状态行/日志提示后交给后台线程。"""
    window.status_text.setText(f"正在积分 {path.name}…")
    _log(window, f"开始积分 {path.name}（后台线程）")
    _spawn(window, path, geom, npt, key)


def _run_2d(window: QMainWindow, path: Path, key: str,
            geom: dict, npt: int) -> None:
    """2D = 衍射图原图：后台读图，画布 imshow（对数色标 + 对比度参数）。"""
    window.status_text.setText(f"正在读取 {path.name}…")
    _log(window, f"开始读取 {path.name}（后台线程）")
    _spawn_task(window, key, _compute_image, (str(path),),
                _on_image_done,
                lambda msg: _on_view_error(window, path, key, "读取", msg))


def _run_profile(window: QMainWindow, path: Path, key: str,
                 geom: dict, npt: int) -> None:
    """剖面 = 过束心直线采样：中心取配置、角度取该面板快照。

    剖面角度是显示参数（每张图各记各的）：改角度后点图像 [应用]
    按新角度重算（见 _apply_image_params 的剖面分支）；主 [应用]
    （数据参数）重算时沿用面板自己的旧角度。
    """
    dock = window.plot_docks.get(key)
    center = window.config["beam_center"]   # (行, 列)
    angle = _panel_param(window, dock, "剖面角度 (°)", 0.0)
    if dock is not None:
        dock.profile_angle = angle   # [应用] 比较用：角度没变只重画
    window.status_text.setText(f"正在计算剖面 {path.name}…")
    _log(window, f"开始计算剖面 {path.name}（后台线程，角度 {angle:g}°）")
    _spawn_task(window, key, _compute_profile, (str(path), center, angle),
                _on_profile_done,
                lambda msg: _on_view_error(window, path, key, "剖面计算", msg))


def _run_waterfall(window: QMainWindow, path: Path, key: str,
                   geom: dict, npt: int) -> None:
    """瀑布 = 36 扇区分区积分堆叠（对齐 CLI sector_waterfall）。"""
    window.status_text.setText(f"正在扇形积分 {path.name}…")
    _log(window, f"开始扇形积分 {path.name}（后台线程，36 扇区）")
    _spawn_task(window, key, _compute_waterfall, (str(path), geom, npt),
                _on_waterfall_done,
                lambda msg: _on_view_error(window, path, key, "瀑布积分", msg))


# 视图注册表：视图名 → 计算 runner（签名 window/path/key/geom/npt）。
# 新视图接线 = 加条目，_run_view 分发骨架不动
_VIEW_RUNNERS = {"2D": _run_2d, "剖面": _run_profile, "1D": _run_1d,
                 "瀑布": _run_waterfall}


def _apply_params(window: QMainWindow) -> None:
    """[应用] 按钮：用当前参数重算焦点面板。

    焦点面板 = 参数坞顶部"编辑对象"（点图面板或计算完成时设定）；
    没选焦点时提示用户先点图。
    """
    key = window.focus_panel
    if key is None:
        _log(window, "先点击要更新的图面板（如 1D），再点 [应用]")
        return
    dock = window.plot_docks.get(key)
    if dock is None:
        _log(window, "编辑对象的面板已不存在")
        window.focus_panel = None
        window.focus_label.setText("编辑对象：未选中图面板")
        return
    view = key.split("|", 1)[0]
    _log(window, f"[应用] 重算编辑对象：{dock.windowTitle()}")
    if view == "对比":
        _run_compare(window, key)   # 一组文件全部重算
        return
    _run_view(window, view, dock.panel_file, key)


def _apply_image_params(window: QMainWindow) -> None:
    """[应用] 按钮（图像参数组）：把当前显示参数应用到编辑对象（焦点面板）。

    两个 [应用] 各管各的：这个按钮只更新焦点面板快照里的显示参数
    （_display_snapshot），数据参数沿用旧快照——顺手改了数据控件也
    不会冒充成这张图的计算参数。显示参数（对比度/剖面角度/对数纵
    轴/纵轴范围/对比归一化）与数据参数同款：每张图各记各的（存在
    各自面板的 params_snapshot 里），点哪张图参数坞就显示哪张图的
    设置。改完点 [应用] 用已有数据重画焦点那张图（不重新积分），
    别的图保持自己的设置不动；只有剖面角度真的变过才重算剖面。
    没算完的焦点面板提示先完成计算。
    """
    key = window.focus_panel
    if key is None:
        _log(window, "先点击要更新的图面板（如 1D），再点 [应用]")
        return
    dock = window.plot_docks.get(key)
    if dock is None:
        _log(window, "编辑对象的面板已不存在")
        window.focus_panel = None
        window.focus_label.setText("编辑对象：未选中图面板")
        return
    dock.params_snapshot = _display_snapshot(window, dock.params_snapshot)
    view = key.split("|", 1)[0]
    if view == "1D":
        if getattr(dock, "last_tth", None) is None:
            _log(window, f"[应用] 图像参数：{dock.windowTitle()} 还没有"
                         f"计算结果（积分完成后再试）")
            return
        _draw_1d(window, dock, dock.last_tth, dock.last_intensity)
    elif view == "对比":
        if not getattr(dock, "compare_data", None):
            _log(window, f"[应用] 图像参数：{dock.windowTitle()} 还没有"
                         f"计算结果（积分完成后再试）")
            return
        _redraw_compare(window, key)
    elif view == "2D":
        if getattr(dock, "last_image", None) is None:
            _log(window, f"[应用] 图像参数：{dock.windowTitle()} 还没有"
                         f"计算结果（读取完成后再试）")
            return
        _draw_2d(window, dock, dock.last_image)
    elif view == "剖面":
        if getattr(dock, "last_profile_t", None) is None:
            _log(window, f"[应用] 图像参数：{dock.windowTitle()} 还没有"
                         f"计算结果（剖面算完后再试）")
            return
        angle = _panel_param(window, dock, "剖面角度 (°)", 0.0)
        if angle != getattr(dock, "profile_angle", None):
            # 角度变了：剖面要重算（读图 + 线剖面，后台线程）
            _log(window, f"[应用] 图像参数：{dock.windowTitle()} 剖面"
                         f"角度改为 {angle:g}°，重新计算")
            _run_profile(window, dock.panel_file, key,
                         _collect_geometry(window),
                         int(window.params["输出点数"].value()))
            return
        _draw_profile(window, dock, dock.last_profile_t,
                      dock.last_profile_intensity)
    elif view == "瀑布":
        if getattr(dock, "last_waterfall", None) is None:
            _log(window, f"[应用] 图像参数：{dock.windowTitle()} 还没有"
                         f"计算结果（扇形积分完成后再试）")
            return
        tth, i2d, chi = dock.last_waterfall
        _draw_waterfall(window, dock, tth, i2d, chi)
    else:
        _log(window, f"[应用] 图像参数已更新编辑对象：{dock.windowTitle()}"
                     f"（{view} 视图尚未接线）")
        return
    _log(window, f"[应用] 图像参数已重画：{dock.windowTitle()}")


def _spawn_task(window: QMainWindow, key: str, worker, args: tuple,
                on_done, on_error) -> None:
    """启动后台任务：worker(*args) 在线程里跑，done/error 回调回主线程。

    _spawn（1D 积分）的本体抽出来通用化：新视图（2D/剖面/瀑布）
    换 worker 与回调即可，三条规则同一份——任务引用挂在
    window._tasks 防垃圾回收、每面板只认最新任务（_latest_task）、
    收尾清登记（不残留已完成任务）。task 变量在闭包外定义、闭包
    内只引用：done/error 回调在任务结束时才被调用，那时 task 早
    已完成赋值。
    """
    task = None

    def done(result):
        window._tasks.remove(task)
        on_done(window, key, task, result)
        # 收尾后再清登记：过期检查（回调里对比 _latest_task）要先看得到自己
        if window._latest_task.get(key) is task:
            del window._latest_task[key]   # 不残留已完成任务（防涨爆）

    def error(msg):
        window._tasks.remove(task)
        on_error(msg)
        if window._latest_task.get(key) is task:
            del window._latest_task[key]

    task = BackgroundTask(worker, *args, on_done=done, on_error=error)
    window._latest_task[key] = task   # 每面板只认最新任务（防旧结果覆盖）
    window._tasks.append(task)
    task.start()


def _spawn(window: QMainWindow, path: Path, geom: dict, npt: int,
           key: str, on_done=None, on_error=None) -> None:
    """启动后台积分任务（1D）：_spawn_task 的 1D 特化，保持旧签名。

    单文件面板走默认回调（_on_integration_done 画一张图）；对比
    面板传入 on_done/on_error——一个面板有多个任务，各自把结果
    画到同一张图上、出错时各自计数。
    """
    def done(window_, key_, task, result):
        (on_done or _on_integration_done)(window_, key_, task, result)

    def error(msg):
        if on_error is not None:
            on_error(msg)
        else:
            _on_integration_error(window, path, key, msg)

    _spawn_task(window, key, _compute_integration, (str(path), geom, npt),
                done, error)


def _on_integration_done(window: QMainWindow, key: str, task, result) -> None:
    """面板的计算完成（主线程）：画进它自己的面板。

    每个面板绑定自己的文件，结果永远画回自己的面板；唯一的过期
    情况是同一面板连点两次开了两个任务——先开的晚到会被丢弃
    （每面板只认最新任务，旧结果不得覆盖新图）。
    """
    suffix = _batch_step(window, key)   # 批量进度：完成任务即计数
    if window._latest_task.get(key) is not task:
        dock = window.plot_docks.get(key)
        if dock is not None:   # 面板还开着才记日志；关了静默丢弃
            _log(window, f"已忽略 {dock.panel_display} 的过期结果"
                         f"（同一面板已有更新的计算）")
        return
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已被关闭（关闭即遗忘）：迟到结果静默丢弃
    tth, intensity = result
    # 结果留在面板上：图像参数 [应用] 只改显示时，用已有数据重画，
    # 不用重新积分
    dock.last_tth = tth
    dock.last_intensity = intensity
    _set_focus(window, key, dock.windowTitle())   # 最新出的图成为编辑对象
    _draw_1d(window, dock, tth, intensity)
    if len(tth):
        _log(window, f"积分完成：{dock.panel_display}（{len(tth)} 点，"
                     f"2θ {tth.min():.3f}~{tth.max():.3f}°）{suffix}")
    else:
        _log(window, f"积分完成：{dock.panel_display}（0 点，无有效数据）"
                     f"{suffix}")


def _batch_step(window: QMainWindow, key: str) -> str:
    """批量进度计数：key 属于当前批（视图一致）就 +1，返回 "（k/n）"
    后缀（贴到完成/失败日志末尾）；非批量或批已走完返回空串。

    [1D] 等按钮一次勾 N 个文件 = 一批（_plot_view 记账 total/视图）。
    每个任务结束时恰好回调一次（done 或 error），进度按"完成数/总
    数"计；批外零散的面板（[应用] 重算、单个开图）不计数。
    """
    batch = getattr(window, "_batch", None)
    if batch is None or key.split("|", 1)[0] != batch["view"]:
        return ""
    batch["done"] += 1
    suffix = f"（{batch['done']}/{batch['total']}）"
    if batch["done"] >= batch["total"]:
        del window._batch   # 批走完：清账，之后零散任务回到无计数
    return suffix


def _on_integration_error(window: QMainWindow, path: Path, key: str,
                          msg: str) -> None:
    """积分失败（主线程）：报错进日志区，不崩溃（批内带进度计数）。"""
    suffix = _batch_step(window, key)
    _log(window, f"积分失败：{path.name} — {msg}{suffix}")


def _on_view_error(window: QMainWindow, path: Path, key: str, what: str,
                   msg: str) -> None:
    """新视图计算失败（主线程）：报错进日志区，不崩溃。

    what = 计算名（读取/剖面计算/瀑布积分），日志统一 "{what}失败：
    文件名 — 原因"；批内带进度计数后缀。
    """
    suffix = _batch_step(window, key)
    _log(window, f"{what}失败：{path.name} — {msg}{suffix}")


def _cache_image(window: QMainWindow, path_str: str, image) -> None:
    """把读好的图存进路径键图像缓存（_apply_auto_contrast 共用）。

    上限 3 张、弹出最早的一张——同一文件反复点不再重复解码。
    """
    cache = window._image_cache
    cache[path_str] = image
    while len(cache) > 3:
        cache.pop(next(iter(cache)))


def _on_image_done(window: QMainWindow, key: str, task, result) -> None:
    """2D 读图完成（主线程）：缓存图像、画进面板。

    与 _on_integration_done 同款过期防护：每面板只认最新任务，
    面板关了静默丢弃。
    """
    suffix = _batch_step(window, key)   # 批量进度：完成任务即计数
    if window._latest_task.get(key) is not task:
        dock = window.plot_docks.get(key)
        if dock is not None:   # 面板还开着才记日志；关了静默丢弃
            _log(window, f"已忽略 {dock.panel_display} 的过期结果"
                         f"（同一面板已有更新的计算）")
        return
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已被关闭（关闭即遗忘）：迟到结果静默丢弃
    image = result
    dock.last_image = image   # 图像 [应用] 只改对比度时用已有图重画
    _cache_image(window, str(dock.panel_file), image)
    _set_focus(window, key, dock.windowTitle())   # 最新出的图成为编辑对象
    _draw_2d(window, dock, image)
    _log(window, f"读取完成：{dock.panel_display}"
                 f"（{image.shape[0]}×{image.shape[1]} 像素）{suffix}")


def _on_profile_done(window: QMainWindow, key: str, task, result) -> None:
    """剖面计算完成（主线程）：结果留面板、画曲线（同 1D 的过期防护）。"""
    suffix = _batch_step(window, key)   # 批量进度：完成任务即计数
    if window._latest_task.get(key) is not task:
        dock = window.plot_docks.get(key)
        if dock is not None:
            _log(window, f"已忽略 {dock.panel_display} 的过期结果"
                         f"（同一面板已有更新的计算）")
        return
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已被关闭：迟到结果静默丢弃
    t, intensity = result
    dock.last_profile_t = t
    dock.last_profile_intensity = intensity
    _set_focus(window, key, dock.windowTitle())
    _draw_profile(window, dock, t, intensity)
    if len(t):
        _log(window, f"剖面完成：{dock.panel_display}（{len(t)} 点，"
                     f"距离 {t.min():.0f}~{t.max():.0f} px）{suffix}")
    else:
        _log(window, f"剖面完成：{dock.panel_display}（0 点，无有效数据）"
                     f"{suffix}")


def _on_waterfall_done(window: QMainWindow, key: str, task, result) -> None:
    """扇形积分完成（主线程）：结果留面板、画堆叠瀑布（同 1D 的过期防护）。"""
    suffix = _batch_step(window, key)   # 批量进度：完成任务即计数
    if window._latest_task.get(key) is not task:
        dock = window.plot_docks.get(key)
        if dock is not None:
            _log(window, f"已忽略 {dock.panel_display} 的过期结果"
                         f"（同一面板已有更新的计算）")
        return
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已被关闭：迟到结果静默丢弃
    tth, i2d, chi = result
    dock.last_waterfall = (tth, i2d, chi)
    _set_focus(window, key, dock.windowTitle())
    _draw_waterfall(window, dock, tth, i2d, chi)
    if len(tth):
        _log(window, f"扇形积分完成：{dock.panel_display}"
                     f"（{i2d.shape[1]} 扇区 × {i2d.shape[0]} 点）{suffix}")
    else:
        _log(window, f"扇形积分完成：{dock.panel_display}"
                     f"（0 点，无有效数据）{suffix}")


def _snapshot_canvas(ax):
    """重画前把会被 ax.clear() 抹掉的状态拍下来（Customize 保护用）。

    返回 (标题, x 标签, y 标签, 纵轴刻度, [(图例名,颜色,线型,线宽,
    标记), ...])。曲线只收有数据的（悬停圆点是空数据假线，不算）。
    """
    lines = [(line.get_label(), line.get_color(), line.get_linestyle(),
              line.get_linewidth(), line.get_marker())
             for line in ax.lines if len(line.get_xdata()) > 0]
    return (ax.get_title(), ax.get_xlabel(), ax.get_ylabel(),
            ax.get_yscale(), lines)


def _settle_scale(dock, cur_scale, param_log):
    """纵轴刻度记账：返回本次应使用的刻度名（与用户讨论定稿）。

    cur_scale = 重画前画布上的刻度（ax.clear() 会重置成 linear，须
    提前拍下）。规则：用户在 Customize 对话框改过刻度 → 以用户为准；
    除非参数里的 [对数纵轴] 又改过了（上次生效时记下的 _yscale_param
    与现在不同）→ 参数为准。首画无条件用参数值。
    """
    want = "log" if param_log else "linear"
    ours = getattr(dock, "_yscale_ours", None)
    ours_param = getattr(dock, "_yscale_param", None)
    if ours is None or ours_param is None or ours_param != param_log:
        final = want          # 首画 / 参数又改过：参数为准
    elif cur_scale != ours:
        final = cur_scale     # 用户在 Customize 改过：用户为准
    else:
        final = ours          # 维持上次（参数值或用户值）
    dock._yscale_ours = final
    dock._yscale_param = param_log
    return final


def _apply_text_guards(dock, ax, keep_title, keep_xlabel, keep_ylabel,
                       texts=None):
    """标题/轴标签保护：用户在 Customize 里改过的保留，其余照默认。

    texts = (默认标题, 默认 x 标签, 默认 y 标签)；None = 按面板的
    视图名查 _default_texts（1D/对比 = 积分图默认，2D/剖面/瀑布 =
    各自默认）。标题的"参数源"= 显示名（panel_display）：显示名没
    变 → 用户手改的标题以用户为准；显示名变过 → 参数为准（默认
    标题跟新名字）。轴标签没有参数源 → 用户改过一次就永远以用户
    为准。
    """
    if texts is None:
        view = dock.panel_key.split("|", 1)[0]
        texts = _default_texts(view, dock.panel_display)
    default_title, default_xlabel, default_ylabel = texts
    ours = getattr(dock, "_title_ours", None)
    ours_display = getattr(dock, "_title_display", None)
    if (ours is not None and keep_title != ours
            and ours_display == dock.panel_display):
        ax.set_title(keep_title)   # 用户为准（不更新 ours：它仍是默认基准）
    else:
        ax.set_title(default_title)
        dock._title_ours = default_title
        dock._title_display = dock.panel_display
    ours = getattr(dock, "_xlabel_ours", None)
    if ours is not None and keep_xlabel != ours:
        ax.set_xlabel(keep_xlabel)
    else:
        ax.set_xlabel(default_xlabel)
        dock._xlabel_ours = default_xlabel
    ours = getattr(dock, "_ylabel_ours", None)
    if ours is not None and keep_ylabel != ours:
        ax.set_ylabel(keep_ylabel)
    else:
        ax.set_ylabel(default_ylabel)
        dock._ylabel_ours = default_ylabel


def _restore_line_styles(ax, old_lines):
    """把重画前拍下的曲线样式原样套回新画的曲线（Customize 保护：
    用户改过的颜色/线型/线宽/标记/图例名不被重画盖掉）。数量变了
    就按顺序对前面几条（新多的曲线用默认样式）。"""
    for i, line in enumerate(ax.lines):
        if i >= len(old_lines):
            break
        label, color, ls, lw, marker = old_lines[i]
        line.set_label(label)
        line.set_color(color)
        line.set_linestyle(ls)
        line.set_linewidth(lw)
        line.set_marker(marker)


def _refresh_home(dock):
    """程序自己重画后清空视图账本：新画好的视图 = 新的"家"（Home）。

    mpl 只在用户手势（框选/平移）里记账，程序重画不自动刷新——
    不刷的话 Home 会跳回重画前的老视图（与用户讨论定稿）。
    """
    canvas = getattr(_content(dock), "canvas", None)
    toolbar = getattr(canvas, "toolbar", None)
    if toolbar is not None:
        toolbar.update()


def _draw_1d(window: QMainWindow, dock, tth, intensity) -> None:
    """在指定的 1D 面板画出积分曲线（只允许主线程调用）。

    x 轴范围：优先该面板快照里的"视图 2θ 范围"（缩放/平移实时
    写回的显示窗口，只看图不参与计算）；没被用户动过（None）就
    跟随数据组的积分 2θ 上下限。显示样式跟随该面板自己的 1D 显示
    参数（对数纵轴 / 纵轴范围）——每张图各记各的，画哪张就用哪张
    的设置（_panel_param），不看参数坞控件当前值：控件此刻可能正
    显示别的面板的设置。积分完成与图像参数 [应用] 都会走这里——
    后者只改显示、不重新积分。

    全程举着 _setting_limits 旗标：画图里 set_xlim/set_ylim/clear
    引发的范围变化是程序自己设的，不算用户改动，不触发同步写回
    （否则 ax.clear() 会先把范围重置成 (0,1)，同步会写回错值）。
    """
    ax = _content(dock).axes_1d
    # Customize 保护：clear 会把标题/标签/刻度/曲线全抹掉，先拍下现状
    keep_title, keep_xlabel, keep_ylabel, keep_scale, old_lines = \
        _snapshot_canvas(ax)
    window._setting_limits = True
    try:
        ax.clear()
        ax.plot(tth, intensity, "b-", lw=0.8)
        lo = _panel_param(window, dock, "视图 2θ 下限 (°)", None)
        hi = _panel_param(window, dock, "视图 2θ 上限 (°)", None)
        if lo is None or hi is None or not lo < hi:
            lo = _panel_param(window, dock, "2θ 下限 (°)", 1.0)
            hi = _panel_param(window, dock, "2θ 上限 (°)", 8.0)
        if lo < hi:
            ax.set_xlim(lo, hi)
        # 视图范围框显示"正在看的窗口"——只有画的正是焦点面板才填
        if window.plot_docks.get(window.focus_panel) is dock:
            window.params["视图 2θ 下限 (°)"].setValue(lo)
            window.params["视图 2θ 上限 (°)"].setValue(hi)
        # 对数纵轴：弱峰"抬起来"（XRD 行规，主峰与弱峰强度差几个数量级）。
        # 刻度以 Customize 用户改动为准时（_settle_scale），纵轴范围也
        # 按生效的刻度算（eff_log），避免对数轴拿到线性分位画不出来
        log_y = _panel_param(window, dock, "对数纵轴", False)
        scale = _settle_scale(dock, keep_scale, log_y)
        eff_log = (scale == "log")
        if scale != "linear":
            ax.set_yscale(scale)
        # 纵轴范围：自动 = 按曲线 1%/99.9% 分位；手动 = 手填上下限。
        # 对数轴画不出 ≤0 的范围，手动值也兜底抬高。自动模式把算出的
        # 区间填进置灰输入框（只读展示"程序正在用的区间"）——只有画的
        # 正是焦点面板才填：否则会覆盖用户正在看的别面板参数
        if _panel_param(window, dock, "纵轴自动", True):
            ylo, yhi = _auto_y_range(intensity, eff_log)
            if window.plot_docks.get(window.focus_panel) is dock:
                window.params["纵轴下限"].setValue(ylo)
                window.params["纵轴上限"].setValue(yhi)
        else:
            ylo = _panel_param(window, dock, "纵轴下限", 1.0)
            yhi = _panel_param(window, dock, "纵轴上限", 100000.0)
            if eff_log:
                ylo = max(ylo, 1e-6)
        if ylo < yhi:
            ax.set_ylim(ylo, yhi)
        _apply_text_guards(dock, ax, keep_title, keep_xlabel, keep_ylabel)
        _restore_line_styles(ax, old_lines)
        ax.grid(alpha=0.3)
        _content(dock).draw()
    finally:
        window._setting_limits = False
    _connect_axis_sync(window, dock.panel_key)   # ax.clear() 清掉了回调（见 helper 注释）
    _refresh_home(dock)   # 程序重画 = 新"家"（见 helper 注释）
    dock.figure_saved = False   # 重画 = 新内容还没存盘


def _draw_2d(window: QMainWindow, dock, image) -> None:
    """在指定的 2D 面板画出衍射图：对数色标 + 对比度参数 + 束心十字。

    样式对齐 CLI view_diffraction / 校准图：magma + LogNorm、自动
    对比度 1%/99.9% 分位（下限兜底 1.0）、origin="lower"（数组行
    序与 beam_center 的行序一致）。束心 = 当前几何配置的
    beam_center（(行, 列)，画图取 (x=列, y=行)）。对比度参数读该
    面板自己的快照：自动模式把算出的区间填进置灰输入框（同 1D
    纵轴的只读展示语义，只填焦点面板）；手动模式手填上下限。
    """
    ax = _content(dock).axes_2d
    keep_title, keep_xlabel, keep_ylabel, keep_scale, old_lines = \
        _snapshot_canvas(ax)
    window._setting_limits = True
    try:
        ax.clear()
        auto = _panel_param(window, dock, "自动对比度", True)
        if auto:
            lo, hi = _auto_contrast_values(image)
            vmin = max(1.0, lo)
            vmax = max(hi, vmin * 10.0)
            if window.plot_docks.get(window.focus_panel) is dock:
                window.params["对比度下限"].setValue(vmin)
                window.params["对比度上限"].setValue(vmax)
        else:
            vmin = _panel_param(window, dock, "对比度下限", 1.0)
            vmax = _panel_param(window, dock, "对比度上限", 100000.0)
            vmin = max(vmin, 1e-12)
            if vmax <= vmin:
                vmax = vmin * 10.0   # 手填区间不合法时兜底（防 LogNorm 报错）
        ax.imshow(image, cmap="magma", norm=LogNorm(vmin=vmin, vmax=vmax),
                  origin="lower")
        ax.set_aspect("equal")
        cy, cx = window.config["beam_center"]
        ax.plot([cx], [cy], "+", color="white", ms=10, mew=1.2)
        _apply_text_guards(dock, ax, keep_title, keep_xlabel, keep_ylabel)
        _content(dock).draw()
    finally:
        window._setting_limits = False
    _refresh_home(dock)   # 程序重画 = 新"家"（见 helper 注释）
    dock.figure_saved = False   # 重画 = 新内容还没存盘


def _draw_profile(window: QMainWindow, dock, t, intensity) -> None:
    """在指定的剖面面板画出强度剖面（x = 到束心的带符号距离）。

    与 _draw_1d 同套路但更简：x 轴是像素距离不是 2θ，没有视图
    2θ 范围的概念（范围写回只连纵轴）；纵轴显示参数（对数纵轴 /
    纵轴自动 / 上下限）读该面板自己的快照，缩放/平移写回纵轴窗口。
    """
    ax = _content(dock).axes_profile
    keep_title, keep_xlabel, keep_ylabel, keep_scale, old_lines = \
        _snapshot_canvas(ax)
    window._setting_limits = True
    try:
        ax.clear()
        ax.plot(t, intensity, "b-", lw=0.8)
        log_y = _panel_param(window, dock, "对数纵轴", False)
        scale = _settle_scale(dock, keep_scale, log_y)
        eff_log = (scale == "log")
        if scale != "linear":
            ax.set_yscale(scale)
        if _panel_param(window, dock, "纵轴自动", True):
            ylo, yhi = _auto_y_range(intensity, eff_log)
            if window.plot_docks.get(window.focus_panel) is dock:
                window.params["纵轴下限"].setValue(ylo)
                window.params["纵轴上限"].setValue(yhi)
        else:
            ylo = _panel_param(window, dock, "纵轴下限", 1.0)
            yhi = _panel_param(window, dock, "纵轴上限", 100000.0)
            if eff_log:
                ylo = max(ylo, 1e-6)
        if ylo < yhi:
            ax.set_ylim(ylo, yhi)
        _apply_text_guards(dock, ax, keep_title, keep_xlabel, keep_ylabel)
        _restore_line_styles(ax, old_lines)
        ax.grid(alpha=0.3)
        _content(dock).draw()
    finally:
        window._setting_limits = False
    _connect_axis_sync(window, dock.panel_key, ax=ax,
                       sync_x=False)   # 只写回纵轴（x = 像素距离）
    _refresh_home(dock)
    dock.figure_saved = False


def _draw_waterfall(window: QMainWindow, dock, tth, i2d, chi) -> None:
    """在指定的瀑布面板画出 36 扇区堆叠瀑布（对齐 CLI 画法）。

    与 CLI sector_waterfall 同一画法：原强度（不取根号）沿 Y 轴
    错开堆叠，行间距自适应（行高 = 该行峰值 × 0.7），每条曲线画
    到自身第一个 0（被探测器切掉的位置）——右端阶梯即截断几何；
    y 刻度 = 各扇区 χ 基线，曲线名 = 扇区 χ（悬停读数用，无图例）。
    纵轴显示参数不适用（行偏移由数据决定），范围写回不连（同 2D）。
    """
    ax = _content(dock).axes_waterfall
    keep_title, keep_xlabel, keep_ylabel, keep_scale, old_lines = \
        _snapshot_canvas(ax)
    window._setting_limits = True
    try:
        ax.clear()
        n = i2d.shape[1]
        colors = cm.viridis(np.linspace(0, 1, n))
        # 每条曲线画到自身第一个 0（截断几何）；未截断的画到末尾
        curves = []
        for k in range(n):
            v = i2d[:, k]
            dead = ~np.isfinite(v) | (v == 0)
            end = int(np.argmax(dead)) if dead.any() else len(v)
            curves.append((tth[:end], v[:end], k))
        # 行间距自适应：行高 = 该行峰值 × 0.7，弱扇区行矮、强扇区
        # 行高；NaN 兜底成 0（坏扇区压成一条基线，不炸整张图）
        i_pos = np.clip(i2d, 0.0, None)
        peak = float(np.nanmax(i_pos)) if np.isfinite(i_pos).any() else 0.0
        heights = np.nan_to_num(np.maximum(i_pos.max(axis=0), 0.05 * peak))
        offsets = np.zeros(n)
        for k in range(1, n):
            offsets[k] = offsets[k - 1] + heights[k - 1] * 0.7
        for t_cut, v_cut, k in curves:
            ax.plot(t_cut, np.clip(v_cut, 0.0, None) + offsets[k],
                    color=colors[k], lw=0.5)
        ax.set_yticks(offsets)
        ax.set_yticklabels([f"{c:.0f}°" for c in chi], fontsize=6)
        _apply_text_guards(dock, ax, keep_title, keep_xlabel, keep_ylabel)
        _restore_line_styles(ax, old_lines)
        for line, c in zip(ax.lines, chi):
            line.set_label(f"{c:.0f}°")   # 重画后重贴扇区名（悬停读数）
        ax.grid(alpha=0.2)
        _content(dock).draw()
    finally:
        window._setting_limits = False
    _refresh_home(dock)
    dock.figure_saved = False


# ══ 悬停取点（鼠标放曲线上 = 出点 + 状态栏坐标）═════════════
def _hover_label(dock, name: str, x: float, y: float) -> str:
    """悬停读数文本：坐标名按视图走。

    剖面 x = 到束心的像素距离；瀑布 y = 堆叠后的强度（已加行偏移）。
    其余（1D/对比）维持 2θ/强度。
    """
    view = dock.panel_key.split("|", 1)[0]
    if view == "剖面":
        return f"{name}　距离 = {x:.4g} px, 强度 = {y:.4g}"
    if view == "瀑布":
        return f"{name}　2θ = {x:.4g}°, 堆叠强度 = {y:.4g}"
    return f"{name}　2θ = {x:.4g}°, 强度 = {y:.4g}"


def _hover_motion(window: QMainWindow, key: str, event) -> None:
    """鼠标在曲线上移动时：选最近的那条线、吸附最近的真实数据点，
    画一个白边圆点，状态栏右侧实时显示该点坐标。

    选线按像素距离：把每条线在鼠标 x 处的 y 换算成屏幕像素再比
    距离，纵轴对数、各线范围不同时也公平。圆点颜色 = 所选线的
    颜色（对比图里一眼对上图例），白边保证点在线上也看得清。
    点吸附最近真实数据点而不是鼠标原始位置——坐标报的是真的
    算出来的值。ax.clear()（重算/[应用]）会连标记一起删掉，懒
    重建：发现标记已不在当前坐标轴上就重画一个。
    """
    dock = window.plot_docks.get(key)
    ax = getattr(event, "inaxes", None)
    if dock is None or ax is None:
        _hover_leave(window, key)
        return
    if getattr(dock, "_pan_start", None) is not None:
        _hover_leave(window, key)   # 正在按住左键平移：悬停点退场，别乱跳
        return
    lines = [ln for ln in ax.lines if len(ln.get_xdata()) > 1]
    if not lines:
        _hover_leave(window, key)
        return
    # 选线：数据坐标换算成像素坐标后比距离（对数轴/范围差异下仍公平）
    line, best_d2 = None, None
    for ln in lines:
        xd = ln.get_xdata()
        if xd.size < 2 or not (xd[0] <= event.xdata <= xd[-1]):
            continue   # 鼠标不在该线的 x 范围里，直接跳过
        yline = float(np.interp(event.xdata, xd, ln.get_ydata()))
        px, py = ax.transData.transform((event.xdata, yline))
        d2 = (px - event.x) ** 2 + (py - event.y) ** 2
        if best_d2 is None or d2 < best_d2:
            line, best_d2 = ln, d2
    if line is None:
        _hover_leave(window, key)
        return
    # 吸附最近真实数据点
    xd = line.get_xdata()
    i = int(np.argmin(np.abs(xd - event.xdata)))
    x, y = float(xd[i]), float(line.get_ydata()[i])
    # 白边圆点（ax.clear() 会删掉它 → 不在当前轴上就重建）
    marker = getattr(dock, "hover_marker", None)
    if marker is None or marker.axes is not ax:
        marker = ax.plot([], [], "o", ms=7, mec="white", mew=1.0,
                         zorder=5)[0]
        dock.hover_marker = marker
    marker.set_color(line.get_color())
    marker.set_data([x], [y])
    marker.set_visible(True)
    ax.figure.canvas.draw_idle()
    # 坐标前缀 = 曲线名（对比图 = 文件名）；1D 没设图例名时
    # matplotlib 会默认给 _childN，不算数 → 回退面板标题
    name = line.get_label()
    if not name or name.startswith("_child"):
        name = dock.panel_display
    window.coord_label.setText(_hover_label(dock, name, x, y))


def _hover_leave(window: QMainWindow, key: str, event=None) -> None:
    """鼠标离开曲线/坐标轴：藏起圆点、清空坐标标签（常驻标签留空）。"""
    dock = window.plot_docks.get(key)
    if dock is not None:
        marker = getattr(dock, "hover_marker", None)
        if marker is not None:
            marker.set_data([], [])
            marker.set_visible(False)
            if marker.axes is not None:   # ax.clear() 后标记已与轴断开
                marker.axes.figure.canvas.draw_idle()
    window.coord_label.setText("")


# ══ 面板工具栏与手势（拖 = 平移 / 滚轮 = 缩放）═════════════
class _SlimToolbar(NavigationToolbar2QT):
    """只留 [Home][Zoom][Customize][Save] 的精简工具栏（过滤父类工具清单）。

    放大/平移改成鼠标手势（拖 = 平移，放大镜点不点亮都是），放大镜
    按钮当开关（与用户讨论定稿）：点亮 = 滚轮（触摸板两指滚动）以
    光标为中心缩放（每格 10%）；熄灭 = 滚轮还给绘图区滚动。框选
    放大已删除（画框后再缩小会出 bug，且与滚轮缩放重复）。抓手/
    前进/后退/子图按钮退休；双击不回全图——回首页只有 Home
    一个入口；Customize = 自绘轴属性对话框（标题/轴标签/纵轴刻度/
    图边距，见 _open_customize_dialog；mpl 自带子图配置器被替换
    ——那是英文技术术语，且 hspace/wspace/Export values 对单图
    无用、字段还挤）；Save = 本面板另存为图片。父类 __init__ 按
    toolitems 表逐个建按钮，覆盖成只含这四个的表即可；放大镜
    QAction mpl 自带 checkable，点击自动亮灭翻转——把 mpl 的
    triggered→zoom() 断开，按钮就只当纯开关（mode 永远停在
    NONE，不再进框选模式），toggled 信号接日志提示。

    Save 重写 save_figure 走 _save_panel：存完置 figure_saved，
    关窗询问"未保存"时不会再问已经存过盘的面板（旧版工具栏 Save
    绕过记账，存过还问）。
    """

    toolitems = [t for t in NavigationToolbar2QT.toolitems
                 if t[0] in ("Home", "Zoom", "Customize", "Save")]

    def __init__(self, canvas, parent=None, window=None, key=None):
        super().__init__(canvas, parent)
        self._window = window
        self._panel_key = key
        # 放大镜按钮只当纯开关：断开 mpl 的 zoom()（会切框选模式），
        # 点击只剩亮灭翻转；toggled 信号写日志
        self._actions["zoom"].triggered.disconnect()
        self._actions["zoom"].toggled.connect(self._log_zoom_toggle)
        # Customize 断开 mpl 自带的图选项编辑器（edit_parameters =
        # toolitem 的回调名，也是 _actions 的键），换成自绘轴属性对话框
        self._actions["edit_parameters"].triggered.disconnect()
        self._actions["edit_parameters"].triggered.connect(
            self._open_customize)

    def _open_customize(self):
        if self._window is not None and self._panel_key is not None:
            _open_customize_dialog(self._window, self._panel_key)

    def _log_zoom_toggle(self, on: bool) -> None:
        if self._window is not None:
            _log(self._window,
                 f"放大镜已{'开启' if on else '关闭'}："
                 f"{'滚轮以光标为中心缩放' if on else '滚轮滚动绘图区'}，"
                 f"左键拖 = 平移")

    def save_figure(self, *args):
        if self._window is not None and self._panel_key is not None:
            _save_panel(self._window, self._panel_key)
        else:
            super().save_figure(*args)


def _save_panel(window: QMainWindow, key: str) -> None:
    """单面板保存（工具栏 [Save] 走这里）：选文件名存 PNG。

    成功即置 figure_saved = True——这张面板在关窗询问里不再算
    "未保存"；用户取消（没选文件名）不动记账。
    """
    dock = window.plot_docks.get(key)
    if dock is None:
        return
    figure = getattr(_content(dock), "figure", None)
    if figure is None:
        _log(window, "该面板还没有可保存的图")
        return
    default = str(Path("outputs") / f"{dock.windowTitle()}.png")
    name, _ = QFileDialog.getSaveFileName(
        window, f"保存 {dock.windowTitle()}", default, "PNG 图片 (*.png)")
    if not name:
        return   # 用户取消：不动已保存记账
    if not name.lower().endswith(".png"):
        name += ".png"
    try:
        Path(name).parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(name)
    except OSError as err:
        _log(window, f"保存失败 {dock.windowTitle()} → {name}（{err}）")
        return
    dock.figure_saved = True
    _log(window, f"已保存 {dock.windowTitle()} → {name}")


def _magnifier_on(dock) -> bool:
    """该面板的放大镜开关是否点亮（只管滚轮：点亮 = 滚轮缩放，
    熄灭 = 滚轮滚动；左键拖在任何时候都是平移）。"""
    toolbar = getattr(_content(dock), "toolbar", None)
    if toolbar is None:
        return False
    action = toolbar._actions.get("zoom")
    return bool(action is not None and action.isChecked())


def _pan_press(window: QMainWindow, key: str, event) -> None:
    """按住左键在图上按下：记起点像素与当时的显示范围，准备平移。"""
    dock = window.plot_docks.get(key)
    if dock is None or event.inaxes is None or event.button != 1:
        return
    # 左键拖 = 平移（放大镜点不点亮都是——框选放大已删除，与用户
    # 讨论定稿：框选会带来画框后再缩小出 bug，且与滚轮缩放重复）
    dock._pan_start = (event.x, event.y)
    dock._pan_limits = (event.inaxes.get_xlim(), event.inaxes.get_ylim())


def _pan_motion(window: QMainWindow, key: str, event) -> None:
    """按住左键拖动 = 整图平移（图跟着鼠标走，像拖地图）。

    起点之后每次移动都从"按下时的显示范围"重算（绝对位移，不
    累计误差）。像素差换算：把起始范围的两个角换算成像素坐标，
    平移后再反算回数据坐标——对数轴也精确（数据坐标直接相减在
    对数轴上会变形）。范围变化走 xlim_changed/ylim_changed →
    自动同步写回参数快照。
    """
    dock = window.plot_docks.get(key)
    if dock is None or event.inaxes is None or event.button != 1:
        return
    start = getattr(dock, "_pan_start", None)
    limits = getattr(dock, "_pan_limits", None)
    if start is None or limits is None:
        return
    ax = event.inaxes
    (x0, x1), (y0, y1) = limits
    dx = event.x - start[0]
    dy = event.y - start[1]
    inv = ax.transData.inverted()
    p0 = ax.transData.transform((x0, y0))
    p1 = ax.transData.transform((x1, y1))
    nx0, ny0 = inv.transform((p0[0] - dx, p0[1] - dy))
    nx1, ny1 = inv.transform((p1[0] - dx, p1[1] - dy))
    ax.set_xlim(nx0, nx1)
    ax.set_ylim(ny0, ny1)
    ax.figure.canvas.draw_idle()


def _pan_release(window: QMainWindow, key: str, event) -> None:
    """松开鼠标：结束平移（清掉起点与起始范围）。"""
    dock = window.plot_docks.get(key)
    if dock is not None:
        dock._pan_start = None
        dock._pan_limits = None


def _wheel_zoom(window: QMainWindow, key: str, event) -> None:
    """滚轮 = 以光标为中心缩放——只在放大镜点亮时生效（与用户讨论定稿）。

    放大镜熄灭时滚轮事件穿透给 QMdiArea 兜底滚动（滚轮 = 滚动
    绘图区看别的图，再也不会误缩图）。点亮后：
    光标对着的那个数据点缩放前后钉在原地（像地图应用）：上下限
    按比例向光标收拢/张开。范围变化自动同步写回参数（缩放会动
    纵轴 → 纵轴自动随之关掉，纵轴窗口由用户接管）。
    Ctrl+滚轮 = 总缩放（Excel 习惯），优先级最高、放大镜点不点亮
    都生效：画布把 Qt 层滚轮事件吃进 mpl 事件（guiEvent），到不
    了视口上的总缩放过滤器——在图上方 Ctrl+滚轮在这里拦截，并把
    Qt 事件 accept 掉，否则未消费的事件传播到视口会再缩一次（双倍）。
    """
    dock = window.plot_docks.get(key)
    if dock is None:
        return
    gui = getattr(event, "guiEvent", None)
    if gui is not None and gui.modifiers() & Qt.ControlModifier:
        delta = gui.angleDelta().y()
        if delta != 0:
            _apply_area_zoom(window, window._area_zoom
                             * (1.1 if delta > 0 else 1.0 / 1.1))
        gui.accept()   # 防事件再传播到视口过滤器（会缩两次）
        return
    ax = getattr(event, "inaxes", None)
    if ax is None or event.xdata is None or event.ydata is None:
        return
    if not _magnifier_on(dock):
        return   # 放大镜熄灭：滚轮只滚动绘图区，不缩图
    # 懒记账（照抄 mpl 框选/平移手势的做法）：滚轮直接改坐标轴范围、
    # 绕过 mpl 的记账，账本一直空着 Home 就无事可做——首次滚轮缩放
    # 前把当前视图记成"家"（程序重画时账本会被 _refresh_home 清空，
    # 所以"家"= 最近一次画好的视图）
    toolbar = getattr(ax.figure.canvas, "toolbar", None)
    if toolbar is not None and toolbar._nav_stack() is None:
        toolbar.push_current()
    # 每格 10%（1.25 = 25% 太猛：触摸板两指一滑是连续好多小格事件，
    # 连乘几下图就飞了；与用户讨论定为 10%）
    factor = 1.0 / 1.1 if event.button == "up" else 1.1
    x, y = event.xdata, event.ydata
    xlo, xhi = ax.get_xlim()
    ylo, yhi = ax.get_ylim()
    ax.set_xlim(x - (x - xlo) * factor, x + (xhi - x) * factor)
    if ax.get_yscale() == "log" and ylo > 0 and y > 0:
        # 对数轴：加性缩放会把下限算到 0 以下 → matplotlib 整个忽略
        # 这次 set_ylim，上限还跟着涨、下限卡死（缩不动）。改乘性：
        # 光标两侧的比例各开 factor 次方，永远 > 0，光标点在
        # 对数空间里同样钉在原地
        ax.set_ylim(y * (ylo / y) ** factor, y * (yhi / y) ** factor)
    else:
        ax.set_ylim(y - (y - ylo) * factor, y + (yhi - y) * factor)
    ax.figure.canvas.draw_idle()


def _on_xlim_changed(window: QMainWindow, key: str, ax) -> None:
    """x 范围被改动（缩放/平移/Home/Customize 对话框）→ 写回视图
    2θ 范围（看图的窗口，与数据组的积分 2θ 范围互不干扰）。

    焦点面板同时刷新参数坞视图范围框，别面板只写快照——控件正
    显示焦点面板的值，不能串台。程序自己画图设的范围
    （_setting_limits）不算用户改动，跳过（否则 ax.clear() 把
    范围重置成 (0,1) 时会把错值写回快照）。
    """
    if getattr(window, "_setting_limits", False):
        return
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已关：静默跳过
    snap = getattr(dock, "params_snapshot", None)
    if not snap:
        return
    xlo, xhi = ax.get_xlim()
    snap["视图 2θ 下限 (°)"] = float(xlo)
    snap["视图 2θ 上限 (°)"] = float(xhi)
    if window.plot_docks.get(window.focus_panel) is dock:
        window.params["视图 2θ 下限 (°)"].setValue(xlo)
        window.params["视图 2θ 上限 (°)"].setValue(xhi)


def _on_ylim_changed(window: QMainWindow, key: str, ax) -> None:
    """y 范围被改动 → 写回纵轴窗口 + 关掉纵轴自动。

    用户手动定过的纵轴窗口由用户接管（自动不再覆盖）；纯 x 方向
    的缩放/平移不碰这里——只有真的动了纵轴才关自动。焦点面板
    同时刷新参数坞控件；程序自己画图（_setting_limits）跳过。
    """
    if getattr(window, "_setting_limits", False):
        return
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已关：静默跳过
    snap = getattr(dock, "params_snapshot", None)
    if not snap:
        return
    ylo, yhi = ax.get_ylim()
    snap["纵轴自动"] = False
    snap["纵轴下限"] = float(ylo)
    snap["纵轴上限"] = float(yhi)
    if window.plot_docks.get(window.focus_panel) is dock:
        # setChecked(False) 触发 sync_ylim → 上下限框解除置灰
        window.params["纵轴自动"].setChecked(False)
        window.params["纵轴下限"].setValue(ylo)
        window.params["纵轴上限"].setValue(yhi)


def _connect_axis_sync(window: QMainWindow, key: str, ax=None,
                       sync_x: bool = True) -> None:
    """把 x/y 范围同步写回回调连到面板的坐标轴。

    sync_x=False = 只连纵轴（剖面：x 轴是像素距离，没有视图 2θ
    范围的概念）。matplotlib 3.11 起 ax.clear()（cla）会把 ax 的
    回调注册表整个清空 → 每次重画完都必须重连（清空后重连只有
    一套，不会叠罗汉）。闭包只抓 key 不抓容器对象：面板弹出/收回
    会换容器（子窗口 ↔ 弹出窗口），抓 key 回调永远现查到当前
    容器；面板关了则 None 守卫静默跳过。构建面板时内容还没挂进
    容器 → 调用方直接把 ax 传进来。
    """
    if ax is None:
        dock = window.plot_docks.get(key)
        if dock is None:
            return
        ax = _content(dock).axes_1d
    if sync_x:
        ax.callbacks.connect("xlim_changed",
                             lambda a, k=key: _on_xlim_changed(window, k, a))
    ax.callbacks.connect("ylim_changed",
                         lambda a, k=key: _on_ylim_changed(window, k, a))


def _build_view_widget(window: QMainWindow, name: str, key: str,
                       title: str) -> QWidget:
    """按视图名创建面板内容（注册表分发器）。

    查 _VIEW_BUILDERS 找对应 builder；未注册视图 = 占位标签（面板
    照常开出，接线后自动变成真内容）。每张面板内容独立（自己的
    画布/坐标轴，挂在控件上供 _draw_1d 使用）；以后 2D/剖面/瀑布
    接线 = 往表里加条目，本函数不用动。每个面板都装 _FocusMarker：
    点它即成为参数面板的编辑对象。
    """
    builder = _VIEW_BUILDERS.get(name)
    if builder is None:
        placeholder = QLabel(f"{title} — 尚未接线")
        placeholder.setAlignment(Qt.AlignCenter)
        widget = placeholder
    else:
        widget = builder(window, key)
    # 焦点切换改挂容器（子窗口/弹出窗口）上：点标题栏/边框也选中
    # （见 _FocusMarker 与两个容器的 __init__），这里不再挂内容上
    _install_resize_grip(window, key, widget)
    return widget


def _build_canvas_panel(window: QMainWindow, key: str, ax_attr: str,
                        hover: bool, sync: str) -> QWidget:
    """面板内容骨架（1D/2D/剖面/瀑布共用）：画布 + 精简工具栏 +
    弹出按钮 + 手势。

    ax_attr = 坐标轴挂到容器上的属性名（axes_1d / axes_2d /
    axes_profile / axes_waterfall）——各 _draw_* 按名取轴。hover =
    是否接悬停取点（2D 是图没有曲线）；sync = 接哪些方向的范围
    写回："xy" = x/y 都写（1D），"y" = 只写纵轴（剖面：x 是像素
    距离），"" = 都不接（2D 像素轴无参数语义 / 瀑布行偏移由数据
    决定）。手势（拖 = 平移、滚轮 = 以光标为中心缩放）是通用的，
    全部视图都接。

    每张面板自己的精简工具栏 [Home][Customize][Save] + [弹出]，
    只作用于本面板的图。放大/平移改成鼠标手势（拖 = 平移、滚轮
    = 以光标为中心缩放），放大镜/抓手/前进后退/子图按钮全砍掉；
    回首页不绑双击——Home 按钮就是回首页。工具栏放画布上方，
    面板标题栏不动。
    """
    fig = Figure(figsize=(5, 3), tight_layout=True)
    canvas = FigureCanvasQTAgg(fig)
    ax = fig.add_subplot(111)
    setattr(canvas, ax_attr, ax)
    toolbar = _SlimToolbar(canvas, canvas, window, key)
    # 容器 = 工具栏 + 画布竖排。把画布原有属性挂到容器上（坐标轴
    # / figure / draw），其余代码仍按 _content(dock) 直取，不必改
    # 调用点
    widget = QWidget()
    box = QVBoxLayout(widget)
    box.setContentsMargins(0, 0, 0, 0)
    box.setSpacing(0)
    box.addWidget(toolbar)
    box.addWidget(canvas)
    setattr(widget, ax_attr, ax)
    widget.figure = fig
    widget.canvas = canvas
    widget.toolbar = toolbar
    widget.draw = canvas.draw   # _content(dock).draw() 仍直接落到画布
    widget.panel_key = key   # 弹出/收回按钮经它找面板
    # 弹出按钮：工具栏末尾（按钮跟着内容走，弹出后在新窗口里
    # 照样能点；addWidget 不动 toolitems 表，测试不受影响）
    popout = QPushButton("弹出")
    popout.setFocusPolicy(Qt.NoFocus)
    toolbar.addWidget(popout)
    widget.popout_btn = popout
    popout.clicked.connect(lambda: _toggle_pop_out(window, key))
    # 画布尺寸变化 = 用户拖了面板边框（或程序平铺/开局）→ 记
    # 比例记忆。过滤器装在画布上而不是容器上：弹出/收回换容器
    # 不用重挂（逻辑见 _on_canvas_resized）
    canvas.installEventFilter(_PanelResizeFilter(window, key, canvas))
    # 悬停取点：鼠标移动 → 曲线上出点 + 状态栏出坐标；
    # 移出坐标轴 → 清空（细节见 _hover_motion/_hover_leave）
    if hover:
        canvas.mpl_connect("motion_notify_event",
                           lambda ev, k=key: _hover_motion(window, k, ev))
        canvas.mpl_connect("axes_leave_event",
                           lambda ev, k=key: _hover_leave(window, k, ev))
    # 手势：按住左键拖 = 平移；滚轮（触摸板两指滚动）=
    # 以光标为中心缩放。拖动期间悬停点退场（别在拖图时乱跳）
    canvas.mpl_connect("button_press_event",
                       lambda ev, k=key: _pan_press(window, k, ev))
    canvas.mpl_connect("motion_notify_event",
                       lambda ev, k=key: _pan_motion(window, k, ev))
    canvas.mpl_connect("button_release_event",
                       lambda ev, k=key: _pan_release(window, k, ev))
    canvas.mpl_connect("scroll_event",
                       lambda ev, k=key: _wheel_zoom(window, k, ev))
    # 范围同步写回：缩放/平移/Home/Customize 对话框改动 x/y 范围
    # → 写回该面板快照 + 焦点时同步参数坞控件（x/y 分开处理：
    # 动 x 只写视图范围，动 y 才关纵轴自动，见两个处理函数）。
    # 每次重画 ax.clear() 都会清掉这些回调，画完由 _draw_* 重连
    # （见 _connect_axis_sync）
    if sync:
        _connect_axis_sync(window, key, ax, sync_x=(sync == "xy"))
    return widget


def _build_1d_widget(window: QMainWindow, key: str) -> QWidget:
    """1D 面板内容：画布 + 精简工具栏 + 弹出按钮 + 悬停取点 +
    手势 + 范围写回（x/y 都写）。"""
    return _build_canvas_panel(window, key, "axes_1d", hover=True, sync="xy")


def _build_2d_widget(window: QMainWindow, key: str) -> QWidget:
    """2D 面板内容：同 1D 骨架，无悬停取点、无范围写回（像素轴
    没有 2θ/纵轴参数语义）。"""
    return _build_canvas_panel(window, key, "axes_2d", hover=False, sync="")


def _build_profile_widget(window: QMainWindow, key: str) -> QWidget:
    """剖面面板内容：同 1D 骨架，只写回纵轴（x = 像素距离）。"""
    return _build_canvas_panel(window, key, "axes_profile",
                               hover=True, sync="y")


def _build_waterfall_widget(window: QMainWindow, key: str) -> QWidget:
    """瀑布面板内容：同 1D 骨架，无范围写回（行偏移由数据决定）。"""
    return _build_canvas_panel(window, key, "axes_waterfall",
                               hover=True, sync="")


# 视图注册表：视图名 → 内容 builder（签名 window/key → QWidget）。
# 新视图接线 = 加条目，分发骨架不动
_VIEW_BUILDERS = {"2D": _build_2d_widget, "剖面": _build_profile_widget,
                  "1D": _build_1d_widget, "瀑布": _build_waterfall_widget}


def _open_plot_panel(window: QMainWindow, name: str, key: str,
                     title: str) -> QMdiSubWindow:
    """新开一张图面板：QMdiSubWindow + 内容 + 几何状态 + 级联摆放。

    开局几何：画布默认 500×300（真 5:3，内容 sizeHint 自带），
    子窗口显式 resize(sizeHint())——QMdiSubWindow 不会自动适配内容
    （探针验证），不显式设会以极小尺寸裁剪内容。初始尺寸按当前
    总缩放比例开（和周围的图大小一致）。落点 = 左上角小错位级联
    （像扑克牌发牌：下面几张的标题栏露出来，一眼知道叠着几张）：
    24px 一档、6 档循环回起点，永远待在绘图区左上角区域——旧的
    一路向右下角排（10 档不循环）会让图堆越滚越远。级联只数子
    窗口（弹出的不算），开新图完全不动旧图——这正是"图不再连
    在一起"的核心。
    """
    sub = _PlotSubWindow(window, key)
    sub.setObjectName(f"plot_{name}")
    window.mdi.addSubWindow(sub)
    content = _build_view_widget(window, name, key, title)
    sub.setWidget(content)
    sub.setWindowTitle(title)
    # 比例记忆的初始状态：没拖过 = 默认画布 (500, 300)。_settling
    # 期间（开局/弹出/收回/平铺的程序性尺寸变化）画布 Resize
    # 事件不记成"用户拖过"
    sub._dragged = False
    sub._canvas_pref = (PLOT_OPEN_W, PLOT_OPEN_H)
    sub._last_canvas = (PLOT_OPEN_W, PLOT_OPEN_H)
    sub._settling = True
    # 面板代数 +1：后台任务回调核对代数，关过重开后旧代迟到结果
    # 不会串进新面板
    window._panel_epoch[key] = window._panel_epoch.get(key, 0) + 1
    window.plot_docks[key] = sub
    z = window._area_zoom
    hint = sub.sizeHint()
    sub.resize(max(60, round(hint.width() * z)),   # 必须显式设（见 docstring）
               max(40, round(hint.height() * z)))
    n = sum(1 for d in window.plot_docks.values()
            if isinstance(d, QMdiSubWindow))
    off = 16 + 24 * ((n - 1) % 6)   # 左上角小错位：6 档循环（见 docstring）
    sub.move(round(off * z), round(off * z))
    sub.show()
    _settle(window)
    # 开局引发的画布尺寸事件已全部消化：把最终实际画布尺寸记下，
    # 之后到达的迟到事件对不上预期值会被跳过（不误标"拖过"）
    canvas = getattr(content, "canvas", None)
    if canvas is not None:
        sub._last_canvas = (canvas.width(), canvas.height())
    else:
        # 占位面板没有画布尺寸约束：给个和 1D 面板相仿的开局大小
        ew, eh = _panel_extra(sub)
        sub.resize(PLOT_OPEN_W + ew, PLOT_OPEN_H + eh)
    sub._settling = False
    return sub


def _plot_view(window: QMainWindow, name: str) -> None:
    """工具栏作图按钮的动作：对每个对号文件开面板（或复用）并计算。

    支持批量：勾选几个文件（选入/拖入即全勾），点一下视图按钮 =
    一次开出多张图。面板按「视图 + 文件条目」成对创建：同一文件
    重复点 = 刷新那张图；面板标题 = 视图_显示名（如 1D_lab6-
    00024.tif），多张图一眼分清。重复文件改名加入的条目显示名不同
    （xxx (1).tif），面板键补显示名区分，各自成图、互不当过期。
    按钮是纯动作不是开关——点一下算一下，重复点击安全；面板的
    开/关只由 × 和拖动管理。
    """
    checked = [window.file_list.item(i)
               for i in range(window.file_list.count())
               if window.file_list.item(i).checkState() == Qt.Checked]
    if not checked:
        _log(window, "没有选中的文件")
        return
    if len(checked) > 1:
        # 批量进度记账：这一批的总数/视图名；每个任务结束回调计数
        # 一次（k/n 后缀贴在完成/失败日志末尾，批走完自动清账）
        window._batch = {"view": name, "total": len(checked), "done": 0}
    for item in checked:
        path = Path(item.data(Qt.UserRole))
        display = item.text()
        key = f"{name}|{path}"
        dock = window.plot_docks.get(key)
        if dock is not None and getattr(dock, "panel_item", None) is not item:
            if getattr(dock, "panel_display", None) == display:
                dock.panel_item = item   # 条目删后重加：面板归位到新条目
            else:
                # 同路径的另一条目（重复文件改名加入）→ 键补显示名区分
                key = f"{name}|{path}|{display}"
                dock = window.plot_docks.get(key)
        if dock is None:
            title = f"{name}_{display}"
            # 新面板级联摆放，现有面板原地不动（开新图不再重排旧图）
            dock = _open_plot_panel(window, name, key, title)
            dock.panel_file = path   # 面板绑定自己的文件（删文件不影响已开的面板）
            dock.panel_item = item   # 面板绑定自己的列表条目（重名条目各自成图）
            dock.panel_display = display   # 显示名（标题/日志/默认存盘名用）
            dock.figure_saved = False   # 有没有存过盘（关窗询问用）
            dock.params_snapshot = _data_snapshot(window)   # 开图快照：数据用当前值，显示从默认起步
            _log(window, f"打开{name}面板：{display}")
        dock.setVisible(True)
        _run_view(window, name, path, key)


# ══ 对比面板（1D 多文件叠图）════════════════════════════════
def _compare_title(displays) -> str:
    """对比面板标题：两个文件 = A vs B；更多 = A 等 N 个文件。"""
    if len(displays) == 2:
        return f"对比_{displays[0]}_vs_{displays[1]}"
    return f"对比_{displays[0]} 等 {len(displays)} 个文件"


def _redraw_compare(window: QMainWindow, key: str) -> None:
    """用面板已有的曲线数据按该面板自己的 1D 显示参数重画整张对比图。

    与 _draw_1d 同套路：坐标范围随参数（2θ 上下限 / 对数纵轴 /
    纵轴范围），只是画多条曲线 + 图例。显示参数同样只读本面板快照
    （_panel_param），不读参数坞控件——各算完到齐收尾时焦点可能还
    在别的面板上，读控件会把别的图的设置画到这张图上。对比归一化
    只动显示数据（原始结果原样保留在 compare_data，见
    _compare_shown_curves 的四模式）。
    """
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已关：静默丢弃
    ax = _content(dock).axes_1d
    # Customize 保护：同 _draw_1d，先拍下现状再 clear
    keep_title, keep_xlabel, keep_ylabel, keep_scale, old_lines = \
        _snapshot_canvas(ax)
    window._setting_limits = True   # 同 _draw_1d：程序设范围不算用户改动
    try:
        ax.clear()
        curves = _compare_shown_curves(window, dock)
        # 画图顺序 = 文件列表顺序（不随各文件算完的先后变）→ 图例顺序、
        # 颜色序号稳定（i 由 _compare_shown_curves 携带）
        for tth, shown, display, i in curves:
            ax.plot(tth, shown, f"C{i}", lw=0.8, label=display)
        _restore_line_styles(ax, old_lines)   # 图例在下面读标签，先套回样式
        xlo = _panel_param(window, dock, "视图 2θ 下限 (°)", None)
        xhi = _panel_param(window, dock, "视图 2θ 上限 (°)", None)
        if xlo is None or xhi is None or not xlo < xhi:
            xlo = _panel_param(window, dock, "2θ 下限 (°)", 1.0)
            xhi = _panel_param(window, dock, "2θ 上限 (°)", 8.0)
        if xlo < xhi:
            ax.set_xlim(xlo, xhi)
        if window.plot_docks.get(window.focus_panel) is dock:
            window.params["视图 2θ 下限 (°)"].setValue(xlo)
            window.params["视图 2θ 上限 (°)"].setValue(xhi)
        log_y = _panel_param(window, dock, "对数纵轴", False)
        scale = _settle_scale(dock, keep_scale, log_y)
        eff_log = (scale == "log")
        if scale != "linear":
            ax.set_yscale(scale)
        auto_y = _panel_param(window, dock, "纵轴自动", True)
        ylo = yhi = None
        if auto_y:
            if curves:
                ylo, yhi = _auto_y_range(
                    np.concatenate([s for _, s, _, _ in curves]), eff_log)
                if ylo < yhi:
                    ax.set_ylim(ylo, yhi)
            else:
                pass   # 一条曲线都没算成：空图，纵轴交给 matplotlib 默认
        else:
            ylo = _panel_param(window, dock, "纵轴下限", 1.0)
            yhi = _panel_param(window, dock, "纵轴上限", 100000.0)
            if eff_log:
                ylo = max(ylo, 1e-6)
            if ylo < yhi:
                ax.set_ylim(ylo, yhi)
        # 自动模式把实际用的区间填进置灰输入框（同 _draw_1d，只填焦点）
        if auto_y and ylo is not None and window.plot_docks.get(window.focus_panel) is dock:
            window.params["纵轴下限"].setValue(ylo)
            window.params["纵轴上限"].setValue(yhi)
        _apply_text_guards(dock, ax, keep_title, keep_xlabel, keep_ylabel)
        if dock.compare_data:
            ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
        _content(dock).draw()
    finally:
        window._setting_limits = False
    _connect_axis_sync(window, dock.panel_key)   # ax.clear() 清掉了回调（见 helper 注释）
    _refresh_home(dock)   # 程序重画 = 新"家"（见 helper 注释）
    dock.figure_saved = False   # 重画 = 新内容还没存盘


def _finish_compare(window: QMainWindow, key: str) -> None:
    """对比面板全部曲线到齐（含失败）：整图重画 → 成为编辑对象 → 记日志。"""
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已关：静默丢弃
    _redraw_compare(window, key)
    _set_focus(window, key, dock.panel_display)
    if dock.compare_data:
        _log(window, f"对比完成：{len(dock.compare_data)} 条曲线")
    else:
        _log(window, "对比失败：所有文件的积分都失败了，面板留空")


def _run_compare(window: QMainWindow, key: str) -> None:
    """对对比面板的每个文件各起一个后台积分，结果画到同一张图。

    代次（gen）防过期：重复点 [对比] 或数据 [应用] 时旧代任务
    全部作废。快照开工前拍下（与单文件面板一致）。
    """
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已关：迟到点击/重算不落地
    # 数据参数 = 控件当前值（计算就用它），显示参数沿用面板自己的
    # 旧快照（重按 [对比] 刷新不改这张图的长相）
    dock.params_snapshot = _data_snapshot(window, dock.params_snapshot)
    dock.compare_gen += 1
    gen = dock.compare_gen
    epoch = window._panel_epoch.get(key, 0)   # 面板代数：关过重开旧代全作废
    dock.compare_pending = len(dock.compare_files)
    dock.compare_data = {}
    geom = _collect_geometry(window)
    npt = int(window.params["输出点数"].value())
    window._setting_limits = True   # 程序自己清轴：不触发范围同步写回
    try:
        _content(dock).axes_1d.clear()
    finally:
        window._setting_limits = False

    def finish_one(path, display, result):
        """单文件结果到位：存原始数据、画一条曲线；全齐后收尾。"""
        # 现查面板（不捕获对象：面板可能已关、已换容器）：
        # 面板没了 / 代数变了（关过重开）/ 代次旧了 → 丢弃
        panel = window.plot_docks.get(key)
        if (panel is None
                or window._panel_epoch.get(key, 0) != epoch
                or panel.compare_gen != gen):
            return   # 旧结果静默丢弃（整图已由新代次重画）
        tth, intensity = result
        panel.compare_data[display] = (tth, intensity)
        panel.compare_pending -= 1
        if panel.compare_pending == 0:
            _finish_compare(window, key)

    def fail_one(path, display, msg):
        panel = window.plot_docks.get(key)
        if (panel is None
                or window._panel_epoch.get(key, 0) != epoch
                or panel.compare_gen != gen):
            return
        panel.compare_pending -= 1
        _log(window, f"对比：{display} 积分失败 — {msg}")
        if panel.compare_pending == 0:
            _finish_compare(window, key)

    for path, display in dock.compare_files:
        # 默认参数绑定防闭包晚绑定（循环变量到回调执行时已走到末尾）
        def spawn_one(path=path, display=display):
            def done(window_, key_, task, result):
                finish_one(path, display, result)

            def error(msg):
                fail_one(path, display, msg)

            window.status_text.setText(f"正在积分 {path.name}…")
            _spawn(window, path, geom, npt, key,
                   on_done=done, on_error=error)

        spawn_one()
    _log(window, f"开始对比 {len(dock.compare_files)} 个文件（后台线程）")


def _plot_compare(window: QMainWindow) -> None:
    """[对比] 按钮：把勾选文件的 1D 曲线叠到同一张面板上。

    只做 1D。勾选 ≥2 个文件，每个文件后台各算各的积分，结果陆续
    画进同一张图（一个文件一条曲线，自动分色 + 图例显示名），
    全部完成后该面板成为编辑对象。同一勾选集合重复点 = 复用同一
    张面板刷新；换集合 = 新开一张。面板键 = "对比|排序后的路径串"
    （与单文件面板 "1D|路径" 并存，互不干扰）。
    """
    checked = [window.file_list.item(i)
               for i in range(window.file_list.count())
               if window.file_list.item(i).checkState() == Qt.Checked]
    if len(checked) < 2:
        _log(window, "对比至少勾选两个文件（勾上的文件叠到一张图）")
        return
    files = [(Path(item.data(Qt.UserRole)), item.text()) for item in checked]
    key = "对比|" + ",".join(sorted(str(p) for p, _ in files))
    dock = window.plot_docks.get(key)
    if dock is None:
        title = _compare_title([d for _, d in files])
        # 新面板级联摆放，现有面板原地不动（同 _plot_view）
        dock = _open_plot_panel(window, "1D", key, title)
        dock.panel_display = title   # 标题/日志/默认存盘名用
        dock.figure_saved = False
        dock.compare_files = files   # 面板绑定这组文件（重算用）
        dock.compare_gen = 0
        dock.params_snapshot = _data_snapshot(window)   # 新面板：显示参数从默认起步
        _log(window, f"打开对比面板：{len(files)} 个文件叠一张图")
    else:
        # 复用面板：文件显示名可能变过（删除重加/改名）→ 绑定刷新，
        # 标题/图例跟着新名字走
        dock.compare_files = files
        title = _compare_title([d for _, d in files])
        if title != dock.panel_display:
            dock.setWindowTitle(title)
            dock.panel_display = title
    dock.setVisible(True)
    _run_compare(window, key)
