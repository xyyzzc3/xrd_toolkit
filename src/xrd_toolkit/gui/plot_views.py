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
    线，统一带 _AUX_GID_PREFIX）+ _bg_path_of + _refresh_bg（按各
    面板快照重画全部曲线面板）；
  - 文件 → 视图闭环：_plot_view（作图按钮的动作：对每个对号文件开
    面板并计算）+ _batch_step（批量进度记账）。

面板壳（画布容器、手势、悬停取点、每面板工具栏）在 plot_panels；
多文件视图（对比 / 热图 / 锚点拾取）在 plot_compare。
"""
from pathlib import Path

import numpy as np
from matplotlib import cm
from matplotlib.colors import LogNorm
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.core.processor import line_profile
from xrd_toolkit.gui.panel_state import (
    _auto_contrast_values, _auto_y_range, _AUX_GID_PREFIX, _bg_curve,
    _bg_params, _bg_settings, _collect_geometry, _content, _curve_color,
    _data_snapshot, _display_snapshot, _log, _panel_param, _set_focus)
from xrd_toolkit.gui.panels import _settle
from xrd_toolkit.gui.plot_panels import (
    _apply_text_guards, _connect_axis_sync, _data_lines, _open_plot_panel,
    _refresh_home, _restore_line_styles, _settle_scale, _snapshot_canvas)
from xrd_toolkit.gui.tasks import BackgroundTask
from xrd_toolkit.services import stage_cache
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
        _log(window, f"复用缓存：{path.name}（几何 {window.config_name}，"
                     f"{len(tth)} 点，2θ {tth[0]:.3f}~{tth[-1]:.3f}°）"
                     f"{_batch_step(window, key)}")
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
        # 设置模板 = 当前编辑对象那份（模式/窗口/拟合/截断），锚点按**该
        # 文件自己的**取（_bg_settings 内部按 path 查 window.bg_anchors）
        settings = _bg_settings(window, dock, path)
        if settings["mode"] != "off":
            got = stage_cache.load_bg(path, **kw, settings=settings)
            if got is not None:
                return got[0], got[1], "扣背景产物"
    got = stage_cache.load_1d(path, **kw)
    if got is not None:
        return got[0], got[1], "1D 产物"
    return None


def _bg_batch_apply(window: QMainWindow) -> None:
    """[批量扣背景]：给勾选文件各生成一份扣后曲线产物。

    锚点**只传 2θ 位置**，强度到每个文件自己的曲线上重新取：一批数据的
    背景**形状**（空气散射 / 光路 / 探测器）是共同的，绝对强度不是——
    直接套 A 的强度会把 B 的基线抬错几倍（与用户 2026-09-24 讨论定稿）。
    空扫模式本来就整批共用一条空扫曲线，直接照各自的设置扣。

    扣完存进分阶段产物（kind=bg）：对比 / 热图下次直接读它，跨会话秒开。
    每个目标面板的参数快照也写成同一套设置——这样"面板上看到的曲线"与
    "对比里用的曲线"是同一条（否则两处数字对不上，最容易让人怀疑自己）。
    """
    dock = window.plot_docks.get(window.focus_panel)
    focus_path = _bg_path_of(dock) if dock is not None else None
    if dock is None or focus_path is None:
        _log(window, "先点一张 1D 图（编辑对象），再点 [批量扣背景]")
        return
    settings = _bg_settings(window, dock, focus_path)
    if settings["mode"] == "off":
        _log(window, "先把背景扣除模式切到「自动基线」或「手动锚点」"
                     "（空扫相减也行），再点 [批量扣背景]")
        return
    xs = [x for x, _ in settings["anchors"]]
    if settings["mode"] == "anchor" and not xs:
        _log(window, "先在图上点几个锚点（背景扣除模式 = 手动锚点），"
                     "再点 [批量扣背景]")
        return
    targets = [Path(window.file_list.item(i).data(Qt.UserRole))
               for i in range(window.file_list.count())
               if window.file_list.item(i).checkState() == Qt.Checked]
    if not targets:
        _log(window, "没有选中的文件")
        return
    geom = _collect_geometry(window)
    npt = int(window.params["输出点数"].value())
    kw = dict(config=window.config_name, npt=npt,
              tth_min=geom.get("tth_min_deg"), tth_max=geom.get("tth_max_deg"))
    blank = getattr(window, "bg_blank", None)
    blank_curve = ((blank["tth"], blank["intensity"])
                   if settings["mode"] == "blank" and blank is not None
                   else None)
    done = skipped = 0
    for i, path in enumerate(targets):
        tth, intensity = _curve_source(window, path, kw)
        if tth is None:
            skipped += 1
            continue
        per_file = [(x, float(np.interp(x, tth, intensity))) for x in xs]
        params = {**_bg_params(window, dock, focus_path),
                  "anchors": per_file}
        base = compute_baseline(tth, intensity, params,
                                blank_curve=blank_curve)
        if base is None:
            skipped += 1
            continue
        sub = subtract_background(intensity, base,
                                  clip_negative=settings["clip"])
        stage_cache.store_bg(path, tth, sub, **kw,
                            settings={**settings, "anchors": per_file})
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
        done += 1
        if (i + 1) % 20 == 0:
            _log(window, f"批量扣背景：{i + 1}/{len(targets)}…")
    tail = (f"，跳过 {skipped} 个（还没有 1D 结果，先点 [1D] 出图）"
            if skipped else "")
    _log(window, f"批量扣背景完成：{done}/{len(targets)} 个文件（"
                 + (f"锚点 {len(xs)} 个，" if xs else "")
                 + f"窗口 {settings['window_deg']:g}°）{tail}")


def _refresh_bg(window: QMainWindow) -> None:
    """背景扣除参数/锚点一变就立刻重画（不重新积分）。

    这是全代码库唯一的"改控件即重画"通路：其余显示参数都等图像组
    [应用]。锚点点选本身是点击驱动的，每点一次都要 [应用] 不可接受，
    所以背景这块走实时。基线估计是纯函数、毫秒级（实测 3000 点 1.4 ms），
    直接拿缓存里的曲线重画一遍就够。

    两步：① 把参数坞里背景控件的当前值推进**编辑对象**面板的快照
    （与图像组 [应用] 同一个动作，见 _apply_image_params——显示参数按
    面板各记各的，不推进去的话画图读到的还是旧快照）；② 按各面板
    自己的快照重画全部曲线面板，编辑对象跟着控件实时走，其余面板
    维持各自已设的显示参数。
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
        dock.params_snapshot = _display_snapshot(window, dock.params_snapshot)
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
    tth, intensity, base = _bg_curve(window, dock, path, tth, intensity)
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
        raw2d = np.asarray(i2d, dtype=float)
        # 背景扣除：用**扇区均值**估一条共同基线，再给每个扇区减同一条。
        # 不逐扇区各估各的——空气散射这类背景在方位角上是均匀的，逐扇区
        # 各扣各的会把扇区之间的真实强度差抹平，而"哪个扇区强"正是瀑布图
        # 存在的意义（择优取向、大晶粒）。
        # 扇区均值自己算而不用 np.nanmean：坏扇区整行 NaN 时 nanmean 返回
        # NaN 并往 stderr 打 RuntimeWarning（本模块的坏扇区 NaN 是预期输入）。
        # 这里逐 2θ 在有效扇区上取均值，全坏的位置取 0 = 该处不扣
        finite = np.isfinite(raw2d)
        counts = finite.sum(axis=1)
        sums = np.where(finite, raw2d, 0.0).sum(axis=1)
        mean_curve = np.divide(sums, counts, out=np.zeros_like(sums),
                               where=counts > 0)
        _, _, base = _bg_curve(window, dock, _bg_path_of(dock), tth,
                               mean_curve)
        i2d = raw2d - np.asarray(base, dtype=float)[:, None] \
            if base is not None else raw2d
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
        for line, c in zip(_data_lines(ax), chi):
            line.set_label(f"{c:.0f}°")   # 重画后重贴扇区名（悬停读数）
        ax.grid(alpha=0.2)
        _content(dock).draw()
    finally:
        window._setting_limits = False
    _refresh_home(dock, ax)
    dock.figure_saved = False


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
    for i, item in enumerate(checked):
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
            if i and i % 8 == 0:
                # 开面板是主线程上的活（每块 130~290 ms）：每 8 块报一次
                # 进度、顺手消化事件，界面不会一口气闷十几秒没反应
                # （用户反馈"图一多就很卡"——开 81 张时的观感）
                _log(window, f"正在开面板：{i + 1}/{len(checked)}…")
                _settle(window)
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
