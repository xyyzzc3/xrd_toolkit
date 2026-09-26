"""单文件视图：runner 分发、四个视图的出图、后台任务回调。

模块图见 panel_state 模块 docstring（本模块在 app.py / plot_compare
之下、plot_panels 之上）。

视图注册表：runner 表 _VIEW_RUNNERS 在本模块（视图名 → 跑计算），
builder 表 _VIEW_BUILDERS 在 plot_panels（视图名 → 建面板内容）——
两边都往表里加条目就能加新视图，_run_view / _build_view_widget 的
分发骨架不用再动。四个单文件视图（2D / 剖面 / 1D / 瀑布）在这里跑
计算；对比（[对比] 按钮）与热图（[热图] 按钮）是多文件视图，整个
流程在 plot_compare。

其余内容：
  - 出图：_draw_1d（曲线 + 背景叠加）/ _draw_2d（图像 + 对比度 +
    束心十字）/ _draw_profile（过束心剖面）/ _draw_waterfall（36
    扇区堆叠，对齐 CLI 画法）——都读该面板自己的参数快照；程序重
    画不覆盖 Customize 用户改动（plot_panels 的 _snapshot_canvas
    先拍现状，_settle_scale / _apply_text_guards /
    _restore_line_styles 保护记账）。对比 / 热图的绘图入口在
    plot_compare（_redraw_compare / _draw_heatmap），按需延迟导入；
  - 后台任务：各视图 worker（_compute_integration/_compute_image/
    _compute_profile/_compute_waterfall，纯计算，后台线程跑）/
    _spawn（1D 特化）/ _spawn_task（通用版）/ 各 _on_*_done（过期
    结果丢弃，面板关了静默）/ _on_integration_error /
    _on_view_error；_apply_params / _apply_image_params（两个
    [应用] 各管各的）；
  - 背景扣除：_draw_bg_overlay（原始曲线 / 基线 / 锚点标记的辅助
    线，统一带 _AUX_GID_PREFIX）+ _bg_path_of + _refresh_proc（按各
    面板快照重画全部曲线面板）；
  - 文件 → 视图闭环：_plot_view（作图按钮的动作：对每个对号文件开
    面板并计算）+ _batch_step（批量进度记账：状态栏进度条 + 大批量
    合并日志）+ _pending_products / _spawn_headless（超出画面板上限
    的文件"只算不画"：照样算完入库，之后点开是复用缓存）。

面板壳（画布容器、手势、悬停取点、每面板工具栏）在 plot_panels；
多文件视图（对比 / 热图 / 锚点拾取）在 plot_compare。
"""
import time
from pathlib import Path

import numpy as np
from matplotlib import cm
from matplotlib.colors import LogNorm
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.core.processor import line_profile
from xrd_toolkit.gui.panel_state import (
    _auto_contrast_values, _auto_y_range, _AUX_GID_PREFIX, _proc_curve,
    _collect_geometry, _content, _curve_color, _proc_params, _proc_settings,
    _data_snapshot, _display_snapshot, _log, _panel_param, _set_focus)
from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui.panels import _settle
from xrd_toolkit.gui.plot_panels import (
    _apply_text_guards, _connect_axis_sync, _data_lines, _open_plot_panel,
    _refresh_home, _restore_line_styles, _settle_scale, _snapshot_canvas)
from xrd_toolkit.gui.tasks import BackgroundTask
from xrd_toolkit.services import process, stage_cache
from xrd_toolkit.services.background import (compute_baseline,
                                              subtract_background)
from xrd_toolkit.services.data_loader import load_diffraction_image
from xrd_toolkit.services.integrator import integrate_1d, integrate_sectors


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
    """1D = 全角度积分：**先看分阶段产物缓存**，命中直接画；否则后台算。

    跨会话的收益在这儿（见 services/stage_cache）：关掉程序第二天再开，
    81 张图不用全部重积分。命中时**必须记日志**（"复用缓存：X"）——
    静默复用会让人以为重算了、或怀疑数字不对。
    """
    dock = window.plot_docks.get(key)
    cached = None
    try:
        cached = stage_cache.load_1d(
            path, config=window.config_name, npt=npt,
            tth_min=geom.get("tth_min_deg"), tth_max=geom.get("tth_max_deg"))
    except Exception:                                    # noqa: BLE001
        cached = None    # 缓存读失败（文件被删/权限）：当作没缓存
    if cached is not None and dock is not None:
        tth, intensity = cached
        suffix, quiet = _batch_step(window, key, path.name)
        batch = getattr(window, "_batch", None)
        if batch is not None:
            batch["cached"] = batch.get("cached", 0) + 1   # 收尾汇总里报一句
        if not quiet:
            _log(window, f"复用缓存：{path.name}（几何 {window.config_name}，"
                         f"{len(tth)} 点，2θ {tth[0]:.3f}~{tth[-1]:.3f}°）"
                         f"{suffix}")
        window.status_text.setText(f"复用缓存 {path.name}"
                                   f"（{len(tth)} 点）")
        dock.last_tth, dock.last_intensity = tth, intensity
        dock._new_data = True        # 这份是新数据 → 面板 [Home] 的家跟着走
        _draw_1d(window, dock, tth, intensity)
        _set_focus(window, key, dock.windowTitle())
        return
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
    # 多文件视图（对比/热图）的计算在 plot_compare：模块级导入会成环
    # （见文件头"依赖方向"），只取本函数用得到的两个 runner
    from xrd_toolkit.gui.plot_compare import _run_compare, _run_heatmap
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
    if view == "热图":
        _run_heatmap(window, key, force=True)   # 数据参数变了：全部重积分
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
    # 具体重画在 _redraw_panel（它按视图类型再延迟导入 plot_compare）
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
    if view == "剖面":
        # 角度变了要先重算（读图 + 线剖面，后台线程），其余都是"用已有
        # 数据重画"——统一走 _redraw_panel（与面板 [Home] 共用）
        angle = _panel_param(window, dock, "剖面角度 (°)", 0.0)
        if angle != getattr(dock, "profile_angle", None):
            _log(window, f"[应用] 图像参数：{dock.windowTitle()} 剖面"
                         f"角度改为 {angle:g}°，重新计算")
            _run_profile(window, dock.panel_file, key,
                         _collect_geometry(window),
                         int(window.params["输出点数"].value()))
            return
    reason = _redraw_panel(window, key)
    if reason:
        _log(window, f"[应用] 图像参数：{dock.windowTitle()} 还没有"
                     f"计算结果（{reason}）")
        return
    _log(window, f"[应用] 图像参数已重画：{dock.windowTitle()}")


def _redraw_panel(window: QMainWindow, key: str) -> str:
    """按该面板自己的参数快照重画一张图（**用已有数据，不重算**）。

    两个调用方共用：图像参数的 [应用]（改显示参数后重画编辑对象）与
    面板的 [Home]（回到"参数定义的样子"）。Home 以前走 mpl 的历史栈，
    而程序重画会清空那个栈 → 按下去常常"没反应"或只回到"上次画的
    位置"（用户 2026-09-24 反馈）；现在 Home 与 [应用] 同源 = 确定性：
    参数是什么样，Home 就是什么样（缩放/平移不写进参数，所以它确实
    是"最初的样子"）。

    返回 "" = 重画了；否则返回"还没算完"的说明（调用方拼进日志）。
    视图类型没接线/面板已关也返回说明文本。
    """
    dock = window.plot_docks.get(key)
    if dock is None:
        return "面板已关闭"
    view = key.split("|", 1)[0]
    if view == "1D":
        if getattr(dock, "last_tth", None) is None:
            return "积分完成后再试"
        _draw_1d(window, dock, dock.last_tth, dock.last_intensity)
    elif view == "对比":
        if not getattr(dock, "compare_data", None):
            return "积分完成后再试"
        from xrd_toolkit.gui.plot_compare import _redraw_compare
        _redraw_compare(window, key)
    elif view == "2D":
        if getattr(dock, "last_image", None) is None:
            return "读取完成后再试"
        _draw_2d(window, dock, dock.last_image)
    elif view == "剖面":
        if getattr(dock, "last_profile_t", None) is None:
            return "剖面算完后再试"
        _draw_profile(window, dock, dock.last_profile_t,
                      dock.last_profile_intensity)
    elif view == "瀑布":
        if getattr(dock, "last_waterfall", None) is None:
            return "扇形积分完成后再试"
        tth, i2d, chi = dock.last_waterfall
        _draw_waterfall(window, dock, tth, i2d, chi)
    elif view == "热图":
        if getattr(dock, "heat_data", None) is None:
            return "热图完成后再试"
        from xrd_toolkit.gui.plot_compare import _draw_heatmap, _heat_data
        data = _heat_data(window, dock)
        if data is None:
            return "热图数据不完整"
        dock.heat_data = data   # 与画的保持同一份：_apply_auto_heatlim 读它
        _draw_heatmap(window, dock, data[0], data[1], data[2])
    else:
        return f"{view} 视图尚未接线"
    return ""


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

    1D 的产物顺手落进分阶段缓存（services/stage_cache）：写盘放在
    算完那一刻（后台线程里，不占界面），下次开会话直接命中。
    """
    # 派活这一刻抓住当前的 _compute_integration——**不是**任务跑起来后
    # 再查模块属性：测试 patch 的正是 gui_views._compute_integration，而
    # 它们的 mock 常常在点完按钮（with 块结束）就撤销了，现查会在那时
    # 变回真函数、拿假路径去积分（2026-09-24 踩过：9 条测试因此超时）。
    compute = _compute_integration

    def worker(path_str, geom_, npt_, config):
        """后台线程：算 1D，顺手把产物落盘。"""
        tth, intensity = compute(path_str, geom_, npt_)
        try:
            stage_cache.store_1d(
                path_str, tth, intensity, config=config, npt=npt_,
                tth_min=geom_.get("tth_min_deg"),
                tth_max=geom_.get("tth_max_deg"))
        except Exception:                                # noqa: BLE001
            pass      # 缓存写失败不影响这次计算（下次重算一遍而已）
        return tth, intensity

    def done(window_, key_, task, result):
        (on_done or _on_integration_done)(window_, key_, task, result)

    def error(msg):
        if on_error is not None:
            on_error(msg)
        else:
            _on_integration_error(window, path, key, msg)

    _spawn_task(window, key, worker,
                (str(path), geom, npt, window.config_name), done, error)


def _on_integration_done(window: QMainWindow, key: str, task, result) -> None:
    """面板的计算完成（主线程）：画进它自己的面板。

    每个面板绑定自己的文件，结果永远画回自己的面板；唯一的过期
    情况是同一面板连点两次开了两个任务——先开的晚到会被丢弃
    （每面板只认最新任务，旧结果不得覆盖新图）。
    """
    # 批量进度：完成任务即计数（大批量时 quiet=True → 不写张张一条）
    suffix, quiet = _batch_step(
        window, key, getattr(window.plot_docks.get(key), "panel_display", ""))
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
    if quiet:
        return          # 大批量：逐张那行不写（进度与汇总由 _batch_step 出）
    if len(tth):
        _log(window, f"积分完成：{dock.panel_display}（{len(tth)} 点，"
                     f"2θ {tth.min():.3f}~{tth.max():.3f}°）{suffix}")
    else:
        _log(window, f"积分完成：{dock.panel_display}（0 点，无有效数据）"
                     f"{suffix}")


def _progress_show(window: QMainWindow, total: int, value: int = 0) -> None:
    """批量进度条：显示出来并把范围设成 total（开面板阶段也用它）。"""
    bar = getattr(window, "batch_progress", None)
    if bar is None:
        return
    bar.setRange(0, max(1, int(total)))
    bar.setValue(int(value))
    bar.setVisible(True)


def _progress_hide(window: QMainWindow) -> None:
    """批收尾：进度条收起来（平时不占状态栏的地方）。"""
    bar = getattr(window, "batch_progress", None)
    if bar is not None:
        bar.setVisible(False)


def _batch_step(window: QMainWindow, key: str, name: str = ""):
    """批量进度计数：+1、推进度条，返回 (k/n 后缀, 是否让调用方别写日志)。

    [1D] 等按钮一次勾 N 个文件 = 一批（_plot_view 记账 total/视图）。
    每个任务结束时恰好回调一次（done 或 error），进度按"完成数/总
    数"计；批外零散的面板（[应用] 重算、单个开图）不计数。

    日志（2026-09-25 用户："81 张 = 81 行「打开面板」+ 81 行「积分完成」，
    把日志刷没了"）：大批量（total > BATCH_LOG_MERGE_AFTER）不逐张写，
    改成每 8 张一行进度 + 批收尾一行汇总（带用时与复用缓存张数），
    调用方拿到 quiet=True 就别写自己那行。**失败路径传 name=""**：它照常
    用返回的（k/n）后缀逐条写——失败是要看的，不合并。单张与小批照旧
    逐条写（一眼看清哪张好了）。
    """
    batch = getattr(window, "_batch", None)
    if batch is None or key.split("|", 1)[0] != batch["view"]:
        return "", False
    batch["done"] += 1
    total = batch["total"]
    quiet = total > BATCH_LOG_MERGE_AFTER
    bar = getattr(window, "batch_progress", None)
    if bar is not None:
        bar.setValue(batch["done"])
    suffix = f"（{batch['done']}/{total}）"
    if quiet and name and batch["done"] % 8 == 0 and batch["done"] < total:
        _log(window, f"{batch['view']} 进度：{batch['done']}/{total}"
                     f"（最近：{name}）")
    if batch["done"] >= total:
        _progress_hide(window)
        if quiet:
            dt = time.time() - batch.get("start", time.time())
            cached = batch.get("cached", 0)
            extra = f"；其中复用缓存 {cached} 张" if cached else ""
            _log(window, f"{batch['view']} 批完成：{total} 张"
                         f"（用时 {dt:.1f} s{extra}）")
        del window._batch   # 批走完：清账，之后零散任务回到无计数
        # 1D 批走完 → 文件栏的「1D 产物」分组该长出来了（这次算的这批
        # 现在有产物了）；别的视图没有产物，刷了也没变化
        if batch["view"] == "1D":
            window.refresh_groups()
    return suffix, quiet


def _on_integration_error(window: QMainWindow, path: Path, key: str,
                          msg: str) -> None:
    """积分失败（主线程）：报错进日志区，不崩溃（批内带进度计数）。"""
    suffix, _ = _batch_step(window, key)   # 失败永远逐条写（不合并）
    _log(window, f"积分失败：{path.name} — {msg}{suffix}")


def _on_view_error(window: QMainWindow, path: Path, key: str, what: str,
                   msg: str) -> None:
    """新视图计算失败（主线程）：报错进日志区，不崩溃。

    what = 计算名（读取/剖面计算/瀑布积分），日志统一 "{what}失败：
    文件名 — 原因"；批内带进度计数后缀。
    """
    suffix, _ = _batch_step(window, key)   # 失败永远逐条写（不合并）
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
    # 批量进度：完成任务即计数（大批量时 quiet=True → 不写张张一条）
    suffix, quiet = _batch_step(
        window, key, getattr(window.plot_docks.get(key), "panel_display", ""))
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
    if not quiet:
        _log(window, f"读取完成：{dock.panel_display}"
                     f"（{image.shape[0]}×{image.shape[1]} 像素）{suffix}")


def _on_profile_done(window: QMainWindow, key: str, task, result) -> None:
    """剖面计算完成（主线程）：结果留面板、画曲线（同 1D 的过期防护）。"""
    # 批量进度：完成任务即计数（大批量时 quiet=True → 不写张张一条）
    suffix, quiet = _batch_step(
        window, key, getattr(window.plot_docks.get(key), "panel_display", ""))
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
    if quiet:
        return          # 大批量：逐张那行不写（见 _batch_step 的说明）
    if len(t):
        _log(window, f"剖面完成：{dock.panel_display}（{len(t)} 点，"
                     f"距离 {t.min():.0f}~{t.max():.0f} px）{suffix}")
    else:
        _log(window, f"剖面完成：{dock.panel_display}（0 点，无有效数据）"
                     f"{suffix}")


def _on_waterfall_done(window: QMainWindow, key: str, task, result) -> None:
    """扇形积分完成（主线程）：结果留面板、画堆叠瀑布（同 1D 的过期防护）。"""
    # 批量进度：完成任务即计数（大批量时 quiet=True → 不写张张一条）
    suffix, quiet = _batch_step(
        window, key, getattr(window.plot_docks.get(key), "panel_display", ""))
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
    if quiet:
        return          # 大批量：逐张那行不写（见 _batch_step 的说明）
    if len(tth):
        _log(window, f"扇形积分完成：{dock.panel_display}"
                     f"（{i2d.shape[1]} 扇区 × {i2d.shape[0]} 点）{suffix}")
    else:
        _log(window, f"扇形积分完成：{dock.panel_display}"
                     f"（0 点，无有效数据）{suffix}")


def _draw_bg_overlay(window: QMainWindow, dock, ax, tth, intensity, base,
                     path) -> None:
    """画背景扣除的辅助线：基线（点线）+ 原始曲线（虚线灰）+ 锚点标记。

    全部带 bg: 前缀的 gid——它们不是"曲线"，_snapshot_canvas /
    _restore_line_styles / _hover_motion 都按线号或最近距离处理线条，
    辅助线混进去会让样式回填错位、悬停点乱跳。
    锚点直接从 window.bg_anchors 重建（不存 dock 属性），所以面板
    弹出/收回、参数重画都不会丢。
    """
    ax.plot(tth, base, linestyle=":", lw=1.0, color="#1baf7a",
            gid=_AUX_GID_PREFIX + "baseline", label="基线")
    if _panel_param(window, dock, "背景显示原始", True):
        ax.plot(tth, intensity, linestyle="--", lw=0.6, color="#999999",
                gid=_AUX_GID_PREFIX + "raw", label="原始")
    anchors = getattr(window, "bg_anchors", {}).get(str(path), [])
    if anchors and _panel_param(window, dock, "背景扣除模式", "off") == "anchor":
        xs = [p[0] for p in anchors]
        ys = [p[1] for p in anchors]
        ln = ax.plot(xs, ys, "o", ms=6, mfc="none", mec="#e34948", mew=1.4,
                     gid=_AUX_GID_PREFIX + "anchor", label="锚点")[0]
        ln.set_zorder(6)


def _bg_path_of(dock):
    """面板对应的文件路径（锚点按路径存；路径是唯一的，显示名可能重名）。"""
    return getattr(dock, "panel_file", None)


def _curve_source(window, path, kw: dict):
    """取某个文件**现有**的 1D 曲线：面板缓存优先，其次 1D 产物。

    没有则返回 (None, None)——批量扣背景要拿原始曲线去重取锚点强度，
    拿不到的文件会被跳过并记进摘要（不静默）。
    """
    panel = window.plot_docks.get("1D|" + str(path))
    if panel is not None and getattr(panel, "last_tth", None) is not None:
        return panel.last_tth, panel.last_intensity
    got = stage_cache.load_1d(path, **kw)
    return got if got is not None else (None, None)


def _curve_for(window, path):
    """对比 / 热图取曲线：**产物优先**（扣背景产物 → 1D 产物），否则 None。

    返回 (tth, intensity, 来源说明)。产物齐了就不用再积分——跨会话秒开
    （用户 2026-09-24 第 5 条：对比直接用上一步扣完背景的产物）。
    来源说明进日志：用哪一份**看得见**，不是悄悄发生的。
    """
    geom = _collect_geometry(window)
    npt = int(window.params["输出点数"].value())
    kw = dict(config=window.config_name, npt=npt,
              tth_min=geom.get("tth_min_deg"), tth_max=geom.get("tth_max_deg"))
    dock = window.plot_docks.get(window.focus_panel)
    if dock is not None:
        # 设置模板 = 当前编辑对象那份（背景三项 + 平滑 + 裁剪），锚点按**该
        # 文件自己的**取（_proc_settings 内部按 path 查 window.bg_anchors）
        settings = _proc_settings(window, dock, path)
        if settings["mode"] != "off" or process.chain_parts(settings):
            got = stage_cache.load_proc(path, **kw, settings=settings)
            if got is not None:
                return got[0], got[1], "处理产物"
    got = stage_cache.load_1d(path, **kw)
    if got is not None:
        return got[0], got[1], "1D 产物"
    return None


def _proc_batch_apply(window: QMainWindow) -> None:
    """[批量处理]：把「处理」页当前这套链用到勾选文件，各生成一份处理产物。

    链 = **背景扣除 → 平滑 → 裁剪**（顺序见 services/process），三项都可关：
    全关时这个按钮只记一条提示、不写产物。

    锚点**只传 2θ 位置**，强度到每个文件自己的曲线上重新取：一批数据的
    背景**形状**（空气散射 / 光路 / 探测器）是共同的，绝对强度不是——
    直接套 A 的强度会把 B 的基线抬错几倍（与用户 2026-09-24 讨论定稿）。
    平滑窗口与裁剪区间是整批共用的（它们是"要看什么"的选择，不是每张图
    各自的物理属性）。

    处理完存进分阶段产物：对比 / 热图 / 导出下次直接读它，跨会话秒开。
    每个目标面板的参数快照也写成同一套设置——这样"面板上看到的曲线"与
    "对比里用的曲线"是同一条（否则两处数字对不上，最容易让人怀疑自己）。

    可处理的条目 = **原始数据** + **1D 产物**（它就是那条原始积分曲线，
    只是钉在某一份缓存上，见 _open_product_panel 的说明）；**处理产物**
    跳过——它已经是处理完的结果，再处理一遍就是二次扣除/二次平滑。
    """
    dock = window.plot_docks.get(window.focus_panel)
    focus_path = _bg_path_of(dock) if dock is not None else None
    if dock is None or focus_path is None:
        _log(window, "先点一张 1D 图（编辑对象），再点 [批量处理]")
        return
    settings = _proc_settings(window, dock, focus_path)
    params0 = _proc_params(window, dock, focus_path)
    chain = process.chain_parts(settings)
    if settings["mode"] == "off" and not chain:
        _log(window, "「处理」页里三项都关着（背景扣除 / 平滑 / 裁剪）——"
                     "先开一项，再点 [批量处理]")
        return
    xs = [x for x, _ in settings["anchors"]]
    if settings["mode"] == "anchor" and not xs:
        _log(window, "先在图上点几个锚点（背景扣除模式 = 手动锚点），"
                     "再点 [批量处理]")
        return
    # 可处理的是原始数据 + 1D 产物；处理产物本身跳过（见 docstring）
    picked = gui_sources.checked_sources(window)
    targets = [s for s in picked
               if s.kind in (gui_sources.RAW, gui_sources.ONED)]
    already = [s for s in picked if s.kind == gui_sources.BG]
    if not targets:
        _log(window, "没有选中的文件"
                     "（处理产物已经是处理完的结果，不用再来一遍）"
                     if already else "没有选中的文件")
        return
    if already:
        _log(window, f"跳过 {len(already)} 个处理产物条目："
                     "它们已经是处理完的结果（幂等，不再来一遍）")
    geom = _collect_geometry(window)
    npt = int(window.params["输出点数"].value())
    kw = dict(config=window.config_name, npt=npt,
              tth_min=geom.get("tth_min_deg"), tth_max=geom.get("tth_max_deg"))
    blank = getattr(window, "bg_blank", None)
    blank_curve = ((blank["tth"], blank["intensity"])
                   if settings["mode"] == "blank" and blank is not None
                   else None)
    done = skipped = from_product = 0
    records = []    # [(源文件, 产物键)]：整批完了写一次台账（不是每张一次）
    seen = set()    # 同一文件只扣一份（勾了它的原始条目又勾了它的 1D 产物）
    for i, source in enumerate(targets):
        path = Path(source.path)
        if str(path) in seen:
            skipped += 1
            _log(window, f"跳过 {source.display}：同一文件在批里只扣一份"
                         "（另一条已经算过了）")
            continue
        seen.add(str(path))
        if source.kind == gui_sources.RAW:
            tth, intensity = _curve_source(window, path, kw)
        else:
            # 1D 产物：读那一份（钉住的键），不按当前设置重算
            got = gui_sources.load_product(source)
            tth, intensity = got if got is not None else (None, None)
        if tth is None:
            skipped += 1
            continue
        per_file = [(x, float(np.interp(x, tth, intensity))) for x in xs]
        params = {**params0, "anchors": per_file}
        # 整条链一次跑完（背景 → 平滑 → 裁剪）：屏幕上的曲线与写进产物的
        # 这一份是同一个函数的输出，不存在两处实现漂移
        processed, base = process.apply_chain(tth, intensity, params,
                                              blank_curve=blank_curve)
        if base is None and settings["mode"] != "off":
            skipped += 1
            continue
        proc_settings = {**settings, "anchors": per_file}
        if source.kind == gui_sources.RAW:
            produced = stage_cache.store_proc(path, tth, processed, **kw,
                                              settings=proc_settings)
        else:
            # 挂在**那份 1D 产物**的键下面：勾的是哪一条，处理的就是哪一条
            produced = stage_cache.store_proc_by_key(
                source.key, tth, processed, settings=proc_settings,
                source=path.name)
            from_product += 1
        records.append((path, Path(produced).stem))
        if getattr(window, "bg_anchors", None) is None:
            window.bg_anchors = {}
        window.bg_anchors[str(path)] = per_file   # 面板跟着用同一套锚点
        panel = window.plot_docks.get("1D|" + str(path))
        snap = getattr(panel, "params_snapshot", None)
        if isinstance(snap, dict):                # 开着的面板：设置也写成同一套
            snap["背景扣除模式"] = "anchor"
            snap["背景窗口 (°)"] = settings["window_deg"]
            snap["锚点拟合方式"] = settings["anchor_method"]
            snap["负值截断为 0"] = settings["clip"]
            snap["平滑曲线"] = bool(params0["smooth_deg"] > 0)
            snap["平滑窗口 (°)"] = settings.get("smooth_deg") or 0.10
            cuts = settings.get("cut_ranges") or []
            snap["裁剪区间"] = list(cuts) if cuts else False
            snap["平滑方法"] = settings.get("smooth_method") or "boxcar"
            snap["平滑阶数"] = int(settings.get("smooth_order") or 3)
        done += 1
        if (i + 1) % 20 == 0:
            _log(window, f"批量处理：{i + 1}/{len(targets)}…")
    tail = (f"，跳过 {skipped} 个（还没有 1D 结果，先点 [1D] 出图）"
            if skipped else "")
    via = f"，其中 {from_product} 条来自 1D 产物" if from_product else ""
    _log(window, f"批量处理完成：{done}/{len(targets)} 个文件"
                 f"（{process.chain_label(settings)}）{tail}{via}")
    # 记台账：这一批 = 文件坞里的一个"扣背景"分组（用户 2026-09-25 定：
    # 每次 [批量扣背景] 一组）。整批写一次，中途不留半截台账。跳过的
    # 文件不进台账（它们没有产物，进组了也是空壳）
    if records:
        label, batch = _proc_batch_label(settings)
        stage_cache.record_batch(
            "bg", batch, label=label, items=records,
            config=window.config_name, npt=npt,
            tth_min=kw.get("tth_min"), tth_max=kw.get("tth_max"),
            settings={k: v for k, v in settings.items() if k != "anchors"},
            note=f"{len(records)} 个文件")
        _log(window, f"产物分组：{label}（{len(records)} 个文件，"
                     f"文件栏里可整组勾选去 [对比]/[热图]）")
        window.refresh_groups()   # 文件栏里立刻长出这一组


def _proc_batch_label(settings: dict) -> tuple:
    """这一批处理的标签与批次号。

    标签给人看（进文件坞的分组名 + 日志）：「处理后 09-25 16:40（锚点 5 个、
    窗口 2°、平滑 0.15°、删 2–3°）」——三项都真的作用在数据上，所以三项都
    写；组名必须描述数据本身，不能写没生效的东西（见 process.chain_label）。
    批次号给程序用 = 时间戳 + 处理链哈希前 6 位：同一套设置在同一个时间戳上
    下标 → 同号（幂等，重复点不会长出重复分组），换了设置就是另一批（两套
    参数的结果并存，正是拿来对比的用法）。
    """
    label = (f"处理后 {time.strftime('%m-%d %H:%M')}"
             f"（{process.chain_label(settings)}）")
    batch = (f"{time.strftime('%Y%m%d-%H%M%S')}-"
             f"{stage_cache.proc_settings_hash(settings)[:6]}")
    return label, batch


def _refresh_proc(window: QMainWindow) -> None:
    """「处理」页任一参数一变就立刻重画（不重新积分）。

    这是全代码库唯一的"改控件即重画"通路：其余显示参数都等图像组
    [应用]。锚点点选本身是点击驱动的，每点一次都要 [应用] 不可接受，
    所以处理这三项（背景扣除 / 平滑 / 裁剪）都走实时。三项都是纯函数、
    毫秒级（基线估计实测 3000 点 1.4 ms；滑动平均是卷积，更快），直接拿
    缓存里的曲线重画一遍就够。

    三步：① 把参数坞里处理控件的当前值推进**编辑对象**面板的快照
    （与图像组 [应用] 同一个动作，见 _apply_image_params——显示参数按
    面板各记各的，不推进去的话画图读到的还是旧快照）；② 顺手把"平滑
    窗口折成几个点"的灰度提示填上（教学用：窗口宽度是度、曲线是点）；
    ③ 按各面板自己的快照重画全部曲线面板，编辑对象跟着控件实时走，其余
    面板维持各自已设的显示参数。
    """
    # 对比/热图的重画入口在 plot_compare（模块级导入成环），只取本函数
    # 用得到的三个
    from xrd_toolkit.gui.plot_compare import (
        _draw_heatmap, _heat_data, _redraw_compare)
    dock = window.plot_docks.get(window.focus_panel)
    # 回放面板快照期间不许回写快照：那一刻控件值正被程序逐个改写（切焦点
    # 时 _load_params_snapshot 在跑），把"回放了一半"的控件状态当成用户的
    # 设置写进当前面板，就会把上一个面板的显示参数串过来（实测串的是
    # 热图色图等注册在背景组之后的几项），且不可逆。重画本身照做
    if dock is not None and not getattr(window, "_param_replaying", False):
        snap = _display_snapshot(window, dock.params_snapshot)
        # 裁剪清单是窗口级的（不是控件值），这里显式拷进快照——快照才是
        # "这张图用什么画的"的权威来源（批量处理时也要逐面板写一遍）
        if snap.get("裁剪区间"):
            snap["裁剪区间"] = list(getattr(window, "cut_list", []) or [])
        dock.params_snapshot = snap
    _update_smooth_points(window, dock)
    for key, dock in list(window.plot_docks.items()):
        view = key.split("|", 1)[0]
        try:
            if view == "1D" and getattr(dock, "last_tth", None) is not None:
                _draw_1d(window, dock, dock.last_tth, dock.last_intensity)
            elif view == "对比":
                _redraw_compare(window, key)
            elif view == "瀑布" and getattr(dock, "last_waterfall", None):
                tth, i2d, chi = dock.last_waterfall
                _draw_waterfall(window, dock, tth, i2d, chi)
            elif view == "热图" and getattr(dock, "heat_results", None):
                data = _heat_data(window, dock)
                if data is not None:
                    dock.heat_data = data   # 同 _apply_image_params：与画的同源
                    _draw_heatmap(window, dock, data[0], data[1], data[2])
        except Exception as err:                      # 重画失败不该拖垮整窗
            _log(window, f"背景扣除重画失败：{type(err).__name__}: {err}")


def _update_smooth_points(window: QMainWindow, dock) -> None:
    """把"平滑窗口 ▲° ≈ 几个点"的提示填进参数坞（教学用，不参与计算）。

    窗口按度给（换点数不失效），但真正做平均的是点——两个数字摆在一起，
    "窗口取到峰宽量级会明显削峰"才有可操作的手感。面板还没算过就留空。
    """
    lbl = getattr(window, "smooth_points_lbl", None)
    if lbl is None:
        return
    deg = float(window.params["平滑窗口 (°)"].value() or 0.0)
    tth = getattr(dock, "last_tth", None) if dock is not None else None
    if tth is None or deg <= 0:
        lbl.setText("")
        return
    from xrd_toolkit.services.background import _window_to_points
    n = _window_to_points(tth, deg)
    lbl.setText(f"≈ {2 * n + 1} 点")


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
    # 背景扣除：在**绘制时**扣，缓存里的原始曲线不动。重绑 intensity，
    # 后面的纵轴自动范围（_auto_y_range）跟着用扣除后的数据——图与范围
    # 必须同一个口径
    path = _bg_path_of(dock)
    raw = intensity          # 辅助线里的"原始曲线"要的是未扣的那份
    tth, intensity, base = _proc_curve(window, dock, path, tth, intensity)
    window._setting_limits = True
    try:
        ax.clear()
        # 单曲线颜色跟配色参数走（高对比第 1 槽蓝 / 默认 = 传统蓝 b）
        palette = _panel_param(window, dock, "曲线配色", "高对比")
        ax.plot(tth, intensity, color=_curve_color(palette, 0, single=True),
                lw=0.8)
        if base is not None:
            _draw_bg_overlay(window, dock, ax, tth, raw, base, path)
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
        # 颜色跟"曲线配色"参数走（参数本身就是用户改色的入口），
        # 线型/线宽/标记照旧保护
        _restore_line_styles(ax, old_lines, restore_color=False)
        ax.grid(alpha=0.3)
        _content(dock).draw()
    finally:
        window._setting_limits = False
    _connect_axis_sync(window, dock.panel_key)   # ax.clear() 清掉了回调（见 helper 注释）
    _refresh_home(dock, ax)   # 程序重画 = 新"家"（见 helper 注释）
    dock.figure_saved = False   # 重画 = 新内容还没存盘


def _draw_2d(window: QMainWindow, dock, image) -> None:
    """在指定的 2D 面板画出衍射图：对数色标 + 颜色条 + 对比度参数 + 束心十字。

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
        im = ax.imshow(image, cmap="magma", norm=LogNorm(vmin=vmin, vmax=vmax),
                       origin="lower")
        ax.set_aspect("equal")
        # 颜色条（作业规格"带颜色条"）：ax.clear() 不清 colorbar（它是
        # 图上的另一个坐标系）。只建一次、之后 update_normal 复用
        # ——remove+重建每做一次，fig.colorbar 就把主坐标轴再让出
        # 20% 宽度且 remove 不退还（累积缩小，教训 13）；复用时
        # 几何只算一次，数据/norm/色图跟着新 im 走。挂在 dock 上：
        # 关面板随 figure 一起销毁，不用清理
        cb = getattr(dock, "_colorbar_2d", None)
        if cb is None:
            dock._colorbar_2d = ax.figure.colorbar(im, ax=ax)
        else:
            cb.update_normal(im)
        dock._colorbar_2d.ax.tick_params(labelsize=7)
        cy, cx = window.config["beam_center"]
        ax.plot([cx], [cy], "+", color="white", ms=10, mew=1.2)
        _apply_text_guards(dock, ax, keep_title, keep_xlabel, keep_ylabel)
        _content(dock).draw()
    finally:
        window._setting_limits = False
    _refresh_home(dock, ax)   # 程序重画 = 新"家"（见 helper 注释）
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
        palette = _panel_param(window, dock, "曲线配色", "高对比")
        ax.plot(t, intensity, color=_curve_color(palette, 0, single=True),
                lw=0.8)
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
        # 颜色跟"曲线配色"参数走（参数本身就是用户改色的入口），
        # 线型/线宽/标记照旧保护
        _restore_line_styles(ax, old_lines, restore_color=False)
        ax.grid(alpha=0.3)
        _content(dock).draw()
    finally:
        window._setting_limits = False
    _connect_axis_sync(window, dock.panel_key, ax=ax,
                       sync_x=False)   # 只写回纵轴（x = 像素距离）
    _refresh_home(dock, ax)
    dock.figure_saved = False


def _chi_tick_labels(ax, chi) -> list:
    """瀑布图的 y 刻度标签：36 个 χ 值挤在矮面板上会叠成一团，按高度抽稀。

    面板高度按 英寸 × dpi 估（不用渲染器：画的时候窗口还没上屏，取不到
    真实像素高），每个标签留 ~9 px；放得下就全标（CLI 那张 12×8 英寸的
    36 个全在），放不下就每隔 k 个标一个——**抽掉的标签留空串**，刻度线
    还在，读数靠悬停（曲线名仍是各自的 χ）。
    """
    n = len(chi)
    if not n:
        return []
    h_px = float(ax.figure.get_size_inches()[1]) * float(ax.figure.dpi)
    every = max(1, int(np.ceil(n * 9.0 / max(h_px * 0.8, 1.0))))
    return [f"{float(c):.0f}°" if k % every == 0 else ""
            for k, c in enumerate(chi)]


def _draw_waterfall(window: QMainWindow, dock, tth, i2d, chi) -> None:
    """在指定的瀑布面板画出 36 扇区堆叠瀑布。

    画法（2026-09-26 晚按用户口径定了两处）：
      - **行距统一**：所有行同一个行高（全场峰值 × 0.7），不再按各扇区
        自己的峰值定行高——那等于把每行都缩到各自的高度，强弱没法横向
        比（用户："不要按照各自的最高峰归一化，所有的图"）。
      - **处理链照跑**：背景用**扇区均值**估一条共同基线、所有扇区减同
        一条（不逐扇区各估各的——空气散射在方位角上均匀，逐扇区各扣会
        把"哪个扇区强"这个瀑布图存在的理由抹平）；**平滑与裁剪逐扇区
        跑**（[处理] 页那两项），于是被裁掉的巨峰在所有行上一起消失、
        剩下的弱扇区才看得清（用户："瀑布图也是在处理后画，去掉无效的
        峰就看得清了"）。

    每条曲线画到自身第一个 0（被探测器切掉的位置）——右端阶梯即截断
    几何；y 刻度 = 各扇区 χ 基线，曲线名 = 扇区 χ（悬停读数用，无图例）。
    纵轴显示参数不适用（行偏移由数据决定），范围写回不连（同 2D）。
    """
    ax = _content(dock).axes_waterfall
    keep_title, keep_xlabel, keep_ylabel, keep_scale, old_lines = \
        _snapshot_canvas(ax)
    window._setting_limits = True
    try:
        ax.clear()
        raw2d = np.asarray(i2d, dtype=float)
        # 背景：共同基线，理由见 docstring。
        # 扇区均值自己算而不用 np.nanmean：坏扇区整行 NaN 时 nanmean 返回
        # NaN 并往 stderr 打 RuntimeWarning（本模块的坏扇区 NaN 是预期输入）。
        # 这里逐 2θ 在有效扇区上取均值，全坏的位置取 0 = 该处不扣
        path = _bg_path_of(dock)
        finite = np.isfinite(raw2d)
        counts = finite.sum(axis=1)
        sums = np.where(finite, raw2d, 0.0).sum(axis=1)
        mean_curve = np.divide(sums, counts, out=np.zeros_like(sums),
                               where=counts > 0)
        params = _proc_params(window, dock, path)
        _, _, base = _proc_curve(window, dock, path, tth, mean_curve)
        i2d = raw2d - np.asarray(base, dtype=float)[:, None] \
            if base is not None else raw2d
        # 平滑 + 裁剪：逐扇区跑链的后半段（背景位关掉：上面已经按共同
        # 基线扣过，不能 36 个扇区各估各的）。两项都没开就整段跳过
        sc = dict(params, mode="off")
        if (float(sc.get("smooth_deg") or 0.0) > 0
                or sc.get("cut_ranges")):
            i2d = np.column_stack([
                process.apply_chain(tth, i2d[:, k], sc)[0]
                for k in range(i2d.shape[1])])
        n = i2d.shape[1]
        colors = cm.viridis(np.linspace(0, 1, n))
        # 每条曲线画到自身第一个 0（截断几何）；未截断的画到末尾。
        # 截断位置要看**原始**数据：扣背景后原来的 0 会变成 -基线（非零），
        # 拿扣除后的值判 0 会把所有曲线都画到末尾
        curves = []
        for k in range(n):
            v = i2d[:, k]
            dead = ~np.isfinite(raw2d[:, k]) | (raw2d[:, k] == 0)
            end = int(np.argmax(dead)) if dead.any() else len(v)
            curves.append((tth[:end], v[:end], k))
        # 行距统一：所有行同一个行高（等行距 → 各行的强弱能横向比，
        # y 刻度也均匀分布；裁剪过的巨峰不再撑高自己那一行）
        i_pos = np.clip(i2d, 0.0, None)
        peak = float(np.nanmax(i_pos)) if np.isfinite(i_pos).any() else 0.0
        step = peak * 0.7 if peak > 0 else 1.0
        offsets = np.arange(n, dtype=float) * step
        for t_cut, v_cut, k in curves:
            ax.plot(t_cut, np.clip(v_cut, 0.0, None) + offsets[k],
                    color=colors[k], lw=0.5)
        ax.set_yticks(offsets)
        ax.set_yticklabels(_chi_tick_labels(ax, chi), fontsize=6)
        _apply_text_guards(dock, ax, keep_title, keep_xlabel, keep_ylabel)
        _restore_line_styles(ax, old_lines)
        for line, c in zip(_data_lines(ax), chi):
            line.set_label(f"{c:.0f}°")   # 重画后重贴扇区名（悬停读数）
        ax.grid(alpha=0.2)
        _content(dock).draw()
    finally:
        window._setting_limits = False
    _refresh_home(dock, ax)
    dock.figure_saved = False


# 一次批量作图最多画**前多少张**（按文件列表顺序，2026-09-24 用户"图一多
# 就很卡"）。开图是唯一随数量变慢的成本：第 1 张 136 ms、第 100 张 287 ms
# （每开一张都要把已经开着的子窗口重排一遍），而且每张 ≈15 MB（真机 81 张
# ≈1.4 GB）。超出的文件不是不能看——日志写清画了几张、出口在哪（[热图]/
# [对比] 一张图放完整批，还会把整批算完落盘，之后单独点开是秒开）。
MAX_PANELS_PER_BATCH = 24

# 批量多大之后"日志合并"（2026-09-25 用户："81 张 = 81 行「打开面板」+
# 81 行「积分完成」，把日志刷没了"）：超过这个张数就不再逐张写，改成
# 开面板一行汇总 + 每 8 张一行进度 + 收尾一行汇总（用时/复用缓存张数）；
# **失败永远逐条写**（那是要看的）。小批维持逐张写，一眼看清哪张好了。
BATCH_LOG_MERGE_AFTER = 8


def _resolve_dock(window: QMainWindow, name: str, item):
    """按「视图 + 文件条目」找已有面板：返回 (键, 面板或 None)。

    键 = "视图|路径"。同一文件可以有多条条目（重复文件改名加入）：短键
    归**先开出面板的那条**，后来者键补显示名区分，各自成图、互不当过期；
    条目删后重加 → 面板归位到新条目（显示名一样就是同一条）。

    注意这条规则**依赖调用时机**：第二条条目的键要靠"短键上已有面板"
    才补显示名，所以必须在**同一次单遍循环**里边找边建（2026-09-24 踩过：
    先跑一遍预算、再跑一遍建面板，第二条条目预算时看不到第一条刚建的
    面板 → 两条抢同一个键、只出一张图）。
    """
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
    return key, dock


def _pending_products(window: QMainWindow, name: str, rest: list) -> list:
    """超限文件里**还需要算**的那些（1D 专用；其余视图返回空表）。

    为什么只认 1D：只有 1D 有产物可留（2D/剖面/瀑布是显示阶段，见
    services/stage_cache 的说明），"只算不画"对它们没有意义。
    已有产物的直接跳过——不重算，**也不计进这一批的总数**：总数为 0
    的批不该开进度条，更不该让 k/n 永远到不了 n。
    """
    if name != "1D" or not rest:
        return []
    geom = _collect_geometry(window)
    npt = int(window.params["输出点数"].value())
    kw = dict(config=window.config_name, npt=npt,
              tth_min=geom.get("tth_min_deg"), tth_max=geom.get("tth_max_deg"))
    todo = []
    for source in rest:                       # rest = 来源对象（已按原始数据过滤）
        path = Path(source.path)
        if not stage_cache.has_1d(path, **kw):
            todo.append(path)
    return todo


def _on_headless_done(window: QMainWindow, key: str, task, result) -> None:
    """"只算不画"的完成回调：只记账（不画图、不写逐张日志）。"""
    _batch_step(window, key, Path(key.split("|", 1)[1]).name)


def _spawn_headless(window: QMainWindow, name: str, path: Path) -> None:
    """只算不画：后台算 1D 并把产物落盘，**不建面板**（用户 2026-09-25 的 B 项）。

    worker 与画面板那条完全共用（_spawn：算完顺手 stage_cache.store_1d），
    差别只在回调——这里不画任何东西。记账归到**同一批**（键的视图段与
    画面板一致），所以进度条与日志是一条线走完；失败照旧逐条报
    （_spawn 的默认 error 回调）。
    """
    geom = _collect_geometry(window)
    npt = int(window.params["输出点数"].value())
    key = f"{name}|{path}"
    _spawn(window, path, geom, npt, key, on_done=_on_headless_done)


def _open_product_panel(window: QMainWindow, source) -> str:
    """产物条目出一张 1D 面板：直接读产物画线（不重算）。

    用户 2026-09-25 定："照旧用，日志说一句"——勾的就是那一份产物，所以
    不比对当前设置、不重新积分，读到什么画什么（读不到就记日志说清楚）。

    两类产物在这一步的待遇不同：
      - **扣背景产物**：快照里的背景扣除置「不扣」——它本身已经是扣完的，
        再扣一遍就是二次相减（图上会往下掉一截，看着还挺像"扣得更干净"）；
      - **1D 产物**：就是那条原始积分曲线（只是钉在某一份缓存上），快照
        照常走默认，**锚点/自动基线一样能用**、也能被 [批量扣背景] 扣
        （用户 2026-09-25 问起才把这条掰正：原先一刀切当成了"已完成"）。
    """
    key = f"1D|{gui_sources.source_id(source)}"
    got = gui_sources.load_product(source)
    if got is None:
        _log(window, f"{source.display}：产物读不到了（被删了？）"
                     "——重算一次，或右键分组删掉这一条")
        return key
    dock = window.plot_docks.get(key)
    if dock is None:
        title = f"1D_{source.display}"
        dock = _open_plot_panel(window, "1D", key, title)
        dock.panel_display = title
        dock.figure_saved = False
        snap = _data_snapshot(window)
        if source.kind == gui_sources.BG:
            snap["背景扣除模式"] = "off"     # 已经扣过，别再扣（见上）
        dock.params_snapshot = snap
        _log(window, f"打开1D面板：{source.display}"
                     f"（{gui_sources.describe_source(source)}，直接读盘不重算"
                     + ("；面板背景扣除已置「不扣」）"
                        if source.kind == gui_sources.BG else "）"))
    dock.setVisible(True)
    dock.panel_file = Path(source.path)   # 锚点/另存名用的源文件
    dock.panel_source = source            # 面板记住自己的产物来源
    tth, intensity = got
    dock.last_tth, dock.last_intensity = tth, intensity
    _set_focus(window, key, dock.windowTitle())
    _draw_1d(window, dock, tth, intensity)
    return key


def _plot_view(window: QMainWindow, name: str) -> None:
    """工具栏作图按钮的动作：对每个对号文件开面板（或复用）并计算。

    支持批量：勾选几个文件（选入/拖入即全勾），点一下视图按钮 =
    一次开出多张图。面板按「视图 + 文件条目」成对创建：同一文件
    重复点 = 刷新那张图；面板标题 = 视图_显示名（如 1D_lab6-
    00024.tif），多张图一眼分清。重复文件改名加入的条目显示名不同
    （xxx (1).tif），面板键补显示名区分，各自成图、互不当过期。
    按钮是纯动作不是开关——点一下算一下，重复点击安全；面板的
    开/关只由 × 和拖动管理。

    一次批量最多画**前 MAX_PANELS_PER_BATCH 张**（按文件列表顺序，
    理由见常量旁的注释）：超出的文件记一行日志并指出 [热图]/[对比]
    这两条出口，不静默少画一半。进度记账的总数 = 这一批真画的张数，
    否则 k/n 永远到不了 n、批也不清账。
    """
    checked = gui_sources.checked_sources(window)
    if not checked:
        _log(window, "没有选中的文件")
        return
    # 产物条目（"1D"/"扣背景"）是现成的 1D 曲线，没有"从原始图算"这一步：
    # 只有 1D 视图能出，其余视图点名跳过（不静默少画）
    raw = [s for s in checked if s.kind == gui_sources.RAW]
    products = [s for s in checked if s.kind != gui_sources.RAW]
    if products and name != "1D":
        _log(window, f"跳过 {len(products)} 个产物条目：{name} 要从原始图像"
                     "算（产物是 1D 曲线，只能出 1D 图 / 对比 / 热图）")
        products = []
    targets = raw[:MAX_PANELS_PER_BATCH]
    rest = raw[len(targets):]
    # 超出的文件**照样算完入库**（只算不画）：1D 有产物可留，之后单独
    # 点开就是复用缓存；已有产物的直接跳过（不重算、也不占进度总数）。
    # 其余视图（2D/剖面/瀑布）是显示阶段、没有产物可留，就只记日志。
    pending = _pending_products(window, name, rest)
    if rest:
        tail = (f"其余 {len(rest)} 张后台算完入库、点开即看"
                if name == "1D" else "要看全部：[热图] / [对比] 一张图看完整批")
        _log(window, f"这批 {len(raw)} 张里先画前 {len(targets)} 张"
                     "（按文件列表顺序，一次最多画 "
                     f"{MAX_PANELS_PER_BATCH} 张：每张 ≈15 MB、越开越慢）；"
                     f"{tail}")
    total_tasks = len(targets) + len(pending)
    if total_tasks > 1:
        # 批量进度记账：这一批的总数/视图名/起算时刻；每个任务结束回调
        # 计数一次（k/n 后缀与进度条都靠它，批走完自动清账）
        window._batch = {"view": name, "total": total_tasks, "done": 0,
                         "start": time.time(), "cached": 0}
        _progress_show(window, total_tasks)
    opened = []          # 新开的面板显示名（大批量时合并成一行）
    merged = total_tasks > BATCH_LOG_MERGE_AFTER
    # 产物条目先画：读盘画线（毫秒级）不需要排队等积分，也不进进度条
    for source in products:
        _open_product_panel(window, source)
    for i, source in enumerate(targets):
        path = Path(source.path)
        display = source.display
        key, dock = _resolve_dock(window, name, source.item)
        if dock is None:
            if i and i % 8 == 0:
                # 开面板是主线程上的活（每块 130~290 ms）：每 8 块报一次
                # 进度、顺手消化事件，界面不会一口气闷十几秒没反应
                # （用户反馈"图一多就很卡"——开 81 张时的观感）
                _log(window, f"正在开面板：{i + 1}/{len(targets)}…")
                _progress_show(window, len(targets), i + 1)   # 开面板也有进度
                _settle(window)
            title = f"{name}_{display}"
            # 新面板级联摆放，现有面板原地不动（开新图不再重排旧图）
            dock = _open_plot_panel(window, name, key, title)
            dock.panel_file = path   # 面板绑定自己的文件（删文件不影响已开的面板）
            dock.panel_item = source.item   # 面板绑定自己的列表条目（重名条目各自成图）
            dock.panel_display = display   # 显示名（标题/日志/默认存盘名用）
            dock.figure_saved = False   # 有没有存过盘（关窗询问用）
            dock.params_snapshot = _data_snapshot(window)   # 开图快照：数据用当前值，显示从默认起步
            if merged:
                opened.append(display)      # 大批量：攒着，循环后一行写完
            else:
                _log(window, f"打开{name}面板：{display}")
        dock.setVisible(True)
        _run_view(window, name, path, key)
    if opened:
        head = "、".join(opened[:5]) + ("…" if len(opened) > 5 else "")
        _log(window, f"打开{name}面板 {len(opened)} 张：{head}")
    if len(targets) + len(pending) > 1:
        _progress_show(window, len(targets) + len(pending))   # 进入计算阶段
    for path in pending:
        _spawn_headless(window, name, path)
