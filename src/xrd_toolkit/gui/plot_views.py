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
    _on_view_error；_apply_params（数据 [应用]；显示参数 2026-10-08 起
    全部即改即画，图像 [应用] 按钮已删——2D 走 _refresh_image_display、
    剖面角度走 _refresh_profile_angle）；
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
    DATA_PARAM_DEFAULTS, FOCUS_EMPTY_TEXT,
    _auto_contrast_values, _auto_y_range, _AUX_GID_PREFIX, _proc_curve,
    _collect_geometry, _content, _gradient_color, _proc_params, _proc_settings,
    _data_snapshot, _display_snapshot, _drop_pending_display, _log,
    _note_params_consumed,
    _panel_param, _param_box_set, _refresh_pending_labels,
    _set_focus, _stash_pending_display)
from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui.panels import _settle
from xrd_toolkit.gui.plot_panels import (
    _apply_text_guards, _connect_axis_sync, _data_lines, _open_plot_panel,
    _refresh_home, _restore_line_styles, _settle_scale, _snapshot_canvas)
# 绘制块（2026-10-21 从本文件原样搬出；再导出，测试与探针照旧）
from xrd_toolkit.gui.plot.drawing import (
    _apply_plain_view, _bg_path_of, _chi_tick_labels, _draw_1d, _draw_2d,
    _draw_bg_overlay, _draw_profile, _draw_waterfall, _update_smooth_points)
# 曲线/配方块（2026-10-21 从本文件原样搬出；再导出，测试与探针照旧）
from xrd_toolkit.gui.plot.curves import (
    _anchors_on_curve, _consume_data_params, _curve_for, _curve_source,
    _file_data_params, _panel_1d_key, _proc_recipe, _ready_curve,
    _refresh_proc, _retarget_product_panel_to_source, _set_recipe_controls,
    apply_recipe, chain_label_of, note_proc_recipe, recipe_text)
from xrd_toolkit.gui.tasks import BackgroundTask, user_error_text
from xrd_toolkit.services import process, stage_cache
from xrd_toolkit.services.stacking import row_step
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

def _apply_range_memory(window: QMainWindow, path) -> None:
    """开一张**新**图前：把 2θ / 点数换成"这个文件自己上次用过的"（甲，2026-10-03）。

    用户原话："2theta 的值现在全局统一，是不对的……我再次对原始数据进行
    处理时，默认使用了刚才对比的 2theta 也是不对的"。坞顶那一行是"最后
    一个动过的人"留下的值，新开图直接继承它，就会把别的图（比如对比）的
    范围漏进来。现在的口径：
      * 盒子里是"用户刚填、还没被用掉"的值（_pending_consumed["数据"] is
        False；回放快照不会误标，见 app._connect_pending_hooks）→ **不动**：
        那是指令（填数出图）；
      * 否则 → 用该文件最近一条产物的范围 / 点数（stage_cache.last_range_for）；
        从没算过 → 出厂默认（panel_state.DATA_PARAM_DEFAULTS）。
    写控件时挂 _param_replaying：程序自己的改写不算"用户改动"。
    """
    if (getattr(window, "_pending_consumed", None) or {}).get("数据") is False:
        return   # 刚填没被用掉 = 指令，照旧用盒子里的
    mem = stage_cache.last_range_for(path)
    lo = float(mem.get("tth_min") if mem.get("tth_min") is not None
               else DATA_PARAM_DEFAULTS["2θ 下限 (°)"])
    hi = float(mem.get("tth_max") if mem.get("tth_max") is not None
               else DATA_PARAM_DEFAULTS["2θ 上限 (°)"])
    npt = int(mem.get("npt") or DATA_PARAM_DEFAULTS["输出点数"])
    prev = getattr(window, "_param_replaying", False)
    window._param_replaying = True
    try:
        window.params["2θ 下限 (°)"].setValue(lo)
        window.params["2θ 上限 (°)"].setValue(hi)
        window.params["输出点数"].setValue(npt)
    finally:
        window._param_replaying = prev

def _run_view(window: QMainWindow, name: str, path: Path, key: str) -> None:
    """对 (视图, 文件) 面板跑对应的计算（注册表分发器，后台线程）。

    通用准备（快照/几何/点数）在这里做一次，具体计算按视图名查
    _VIEW_RUNNERS 交给各 runner。新视图接线 = 往表里加条目，本
    函数不用动。
    """
    runner = _VIEW_RUNNERS.get(name)
    if runner is None:
        _log(window, f"{name} 视图暂不可用（面板暂无内容）")
        return
    # 开工前先刷新参数快照：数据参数 = 控件当前值（计算就用它），
    # 显示参数沿用面板自己的旧快照（重算不改长相）；新面板旧快照
    # 里是默认值，正好从默认起步
    dock = window.plot_docks.get(key)
    if dock is not None:
        dock.params_snapshot = _data_snapshot(window, dock.params_snapshot)
        # 数据参数刚被写成控件当前值（[_plot_view] 出图/重算也走这里）→
        # 那张图的「未应用」灰字该灭了，而且这组值已经**被用掉**（不再算
        # 谁的待办改动——见 panel_state._capture_pending）
        _note_params_consumed(window, "数据")
        _refresh_pending_labels(window)
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
                         f"{len(tth)} 点，2θ {tth[0]:.3f}–{tth[-1]:.3f}°）"
                         f"{suffix}")
        window.status_text.setText(f"复用缓存：{path.name}"
                                   f"（{len(tth)} 点）")
        dock.last_tth, dock.last_intensity = tth, intensity
        dock._new_data = True        # 这份是新数据 → 面板 [Home] 的家跟着走
        _draw_1d(window, dock, tth, intensity)
        _set_focus(window, key, dock.windowTitle())
        return
    window.status_text.setText(f"正在积分 {path.name}…")
    _log(window, f"开始积分 {path.name}（后台运行）")
    _spawn(window, path, geom, npt, key)

def _run_2d(window: QMainWindow, path: Path, key: str,
            geom: dict, npt: int) -> None:
    """2D = 衍射图原图：后台读图，画布 imshow（对数色标 + 对比度参数）。"""
    window.status_text.setText(f"正在读取 {path.name}…")
    _log(window, f"开始读取 {path.name}（后台运行）")
    _spawn_task(window, key, _compute_image, (str(path),),
                _on_image_done,
                lambda msg: _on_view_error(window, path, key, "读取", msg))

def _run_profile(window: QMainWindow, path: Path, key: str,
                 geom: dict, npt: int) -> None:
    """剖面 = 过束心直线采样：中心取配置、角度取该面板快照。

    剖面角度是显示参数（每张图各记各的）：改角度即按新角度重算
    （见 _refresh_profile_angle，2026-10-08 起即改即画）；主 [应用]
    （数据参数）重算时沿用面板自己的旧角度。
    """
    dock = window.plot_docks.get(key)
    center = window.config["beam_center"]   # (行, 列)
    angle = _panel_param(window, dock, "剖面角度 (°)", 0.0)
    if dock is not None:
        dock.profile_angle = angle   # [应用] 比较用：角度没变只重画
    window.status_text.setText(f"正在计算剖面 {path.name}…")
    _log(window, f"开始计算剖面 {path.name}（后台运行，角度 {angle:g}°）")
    _spawn_task(window, key, _compute_profile, (str(path), center, angle),
                _on_profile_done,
                lambda msg: _on_view_error(window, path, key, "剖面计算", msg))

def _run_waterfall(window: QMainWindow, path: Path, key: str,
                   geom: dict, npt: int) -> None:
    """瀑布 = 36 扇区分区积分堆叠（对齐 CLI sector_waterfall）。"""
    window.status_text.setText(f"正在扇形积分 {path.name}…")
    _log(window, f"开始扇形积分 {path.name}（后台运行，36 扇区）")
    _spawn_task(window, key, _compute_waterfall, (str(path), geom, npt),
                _on_waterfall_done,
                lambda msg: _on_view_error(window, path, key, "扇形积分", msg))

# 视图注册表：视图名 → 计算 runner（签名 window/path/key/geom/npt）。
# 新视图接线 = 加条目，_run_view 分发骨架不动
_VIEW_RUNNERS = {"2D": _run_2d, "剖面": _run_profile, "1D": _run_1d,
                 "瀑布": _run_waterfall}

def _apply_params(window: QMainWindow) -> None:
    """[应用] / [重算这张图]：用当前参数重算焦点面板。

    焦点面板 = 参数坞顶部"编辑对象"（点图面板或计算完成时设定）；
    没选焦点时提示用户先点图。

    编辑对象若是**产物面板**，先摘掉它的"产物身份"再重算（见下面的说明）——
    产物是冻结点，重算出来的是另一条曲线，不能顶着旧身份继续走。
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
        window.focus_label.setText(FOCUS_EMPTY_TEXT)
        return
    view = key.split("|", 1)[0]
    # 产物面板重算前先摘掉"产物身份"（用户 2026-09-30："本功能的范围只影响
    # 本功能的产物"）：产物是**冻结点**，按新范围重算出来的这条已经不是那一份
    # 了。不摘有两个后果（① 探针实测，② 同一类）：
    #   ① 处理产物：原始文件会被按新范围重积分、**画进这张"处理后"面板**，
    #      而面板仍按产物身份参与后续 [套用]/[采用这份结果]/[批量处理]；
    #   ② 1D 产物：面板画着新范围的曲线，[采用这份结果] 却回头去读**旧范围**
    #      那一份（屏幕上看到的与存下来的不是同一条，这里最忌讳的一类错）。
    # 处理产物走 [套用] 那套"就地换成它底下的 1D 曲线"（血缘查不到会明确拒绝）。
    # 只有 1D 视图可能有产物面板（产物都是曲线条目），别的视图这里天然是空转。
    src = getattr(dock, "panel_source", None)
    if src is not None:
        if src.kind == gui_sources.BG:
            if not _retarget_product_panel_to_source(window, dock):
                return
        else:
            dock.panel_source = None
            dock.panel_display = f"1D_{Path(src.path).name}"
            dock.setWindowTitle(dock.panel_display)
            _log(window, f"这一条是 1D 产物（读的是 {src.display} 那一份）："
                         "按当前设置重算后它不再是那一份了，"
                         "面板改为这个文件的一条 1D 曲线")
    # 这行要在**所有**分支之前（对比/热图也要它：用户点 [应用] 时日志必须
    # 说清重算的是谁）；摘身份之后再写，标题才是摘完的那个
    _log(window, f"[应用] 重算编辑对象：{dock.windowTitle()}")
    if view == "对比":
        _run_compare(window, key)   # 一组文件全部重读+重画（栅栏：不现场积分）
    elif view == "热图":
        _run_heatmap(window, key)   # 同上（force 已于 2026-10-16 栅栏随退休）
    else:
        _run_view(window, view, dock.panel_file, key)
    # 上面这些都会把控件当前值写进那张图的快照 → 数据组的「未应用」灰字
    # 该灭了（它量的是"控件 vs 快照"的差，见 panel_state._refresh_pending_labels）
    _note_params_consumed(window, "数据")
    _refresh_pending_labels(window)

def _refresh_image_display(window: QMainWindow) -> None:
    """2D 显示参数（自动对比度 / 显示范围 / 显示束心）改了 → 就地重画编辑对象。

    2026-10-08：这三项从"要按 [应用显示设置]"改成即改即画——那个按钮
    随这次清扫删了（同 10-02 对比四参数、10-07 1D 显示与瀑布行距的老
    做法）。原先的 _apply_image_params 就是干这件事（只写快照 + 用已有
    数据重画、不重新积分），这里把触发从按钮换成值变。

    作用域 = **编辑对象这一个面板**（同 _refresh_1d_display 的判例）：
    2D 面板能同时开很多张、显示参数每张各记各的（色图/对比度/束心都按
    面板记），改这张不该顺带改别的张。焦点不是 2D/剖面 时同样只写快照
    ——与旧 [应用] 的口径一致（那几个键在别的视图里不参与画图）。
    """
    if getattr(window, "_param_replaying", False) \
            or getattr(window, "_param_box_sync", False):
        return   # 回放快照 / 程序回填（自动对比度算值进框）不是用户改动
    keys = ("自动对比度", "对比度下限", "对比度上限", "显示束心")
    key = window.focus_panel
    dock = window.plot_docks.get(key) if key is not None else None
    if dock is None:
        # 没有编辑对象：控件值先记进"待应用"账本——别的地方改法见
        # _stash_pending_display（用户 2026-10-13："出图之前调整参数会无效"）
        _stash_pending_display(window, keys)
        return
    _drop_pending_display(window, keys)   # 有面板接住：账本里这几个键作废
    dock.params_snapshot = _display_snapshot(window, dock.params_snapshot)
    _redraw_panel(window, key)   # 还没算完时它自己知道，这里不打扰

def _refresh_profile_angle(window: QMainWindow) -> None:
    """剖面角度改了 → 编辑对象是剖面面板就就地重算（后台），否则只写快照。

    剖面角度是这批显示参数里唯一的"要重算"（沿新角度重新取一条线），
    其余都是"用已有数据重画"。焦点是剖面面板 → 走 _run_profile（后台
    任务按面板 key 一场一任务，拨快了由新任务说了算）；焦点不是剖面 →
    只写快照，等将来算这条线时用它（与旧 [应用] 的口径一致）。
    """
    if getattr(window, "_param_replaying", False) \
            or getattr(window, "_param_box_sync", False):
        return   # 回放快照 / 程序同步写控件不是用户改动
    keys = ("剖面角度 (°)",)
    key = window.focus_panel
    dock = window.plot_docks.get(key) if key is not None else None
    if dock is None:
        _stash_pending_display(window, keys)   # 无编辑对象：记进待应用账本
        return
    _drop_pending_display(window, keys)        # 有面板接住：账本里的角度作废
    dock.params_snapshot = _display_snapshot(window, dock.params_snapshot)
    if key.split("|", 1)[0] != "剖面":
        return   # 焦点不是剖面：快照已带上新角度，将来算它时用
    angle = _panel_param(window, dock, "剖面角度 (°)", 0.0)
    if angle == getattr(dock, "profile_angle", None):
        return   # 角度没变（别的参数顺手带出来的刷新）：别白重算
    _run_profile(window, dock.panel_file, key,
                 _collect_geometry(window),
                 int(window.params["输出点数"].value()))

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
        return f"{view} 视图暂不可用"
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

    # 派活了：坞顶那组数据参数**被用掉**（见 _consume_data_params）
    _consume_data_params(window)
    _spawn_task(window, key, worker,
                (str(path), geom, npt, window.config_name), done, error)

def _on_integration_done(window: QMainWindow, key: str, task, result) -> None:
    """面板的计算完成（主线程）：画进它自己的面板。

    每个面板绑定自己的文件，结果永远画回自己的面板；唯一的过期
    情况是同一面板连点两次开了两个任务——先开的晚到会被丢弃
    （每面板只认最新任务，旧结果不得覆盖新图）。
    """
    # 单张 1D 完成要刷文件栏（用户 2026-10-05 报的 bug：打开单张算完 1D，
    # 文件栏不长「1D 产物」，再打开另一张才忽然出现——刷新原先只挂在
    # 批量收尾里（_batch_step），单张走的这条路从来没人刷）。批内的交给
    # 批收尾统一刷；放在最前：过期结果/面板已关这两条早退分支里，产物
    # 已经在盘上，文件栏同样该更新。走 quiet 入口：单张不算"上一轮的活
    # 干完了"，**不清勾选**（不清，用户才能再点一次 [1D] 看缓存——
    # 2026-10-05 实测：非 quiet 会顺手把勾清掉，那条路直接断）。
    if getattr(window, "_batch", None) is None and key.split("|", 1)[0] == "1D":
        window.refresh_groups_quiet()
    # 批量进度：完成任务即计数（大批量时 quiet=True → 不写张张一条）
    suffix, quiet = _batch_step(
        window, key, getattr(window.plot_docks.get(key), "panel_display", ""))
    if window._latest_task.get(key) is not task:
        dock = window.plot_docks.get(key)
        if dock is not None:   # 面板还开着才记日志；关了静默丢弃
            _log(window, f"已忽略 {dock.panel_display} 的过期结果"
                         f"（该图面板已有更新的计算）")
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
                     f"2θ {tth.min():.3f}–{tth.max():.3f}°）{suffix}")
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
            # 重复条目**这里再说一遍**：按 [1D] 那一刻的提示在几十行进度
            # 之前，用户盯的是尾巴（2026-10-09：83 条勾选 → 81 条新产物，
            # 用户问"这个昨天不是也做了吗"——做了，但没长在他看的地方）
            dupe = batch.get("dupe", 0)
            dupe_note = (f"；勾选里有 {dupe} 条是同一文件的重复条目"
                         "——同一份数据只算一遍") if dupe else ""
            _log(window, f"{batch['view']} 批完成：{total} 张"
                         f"（用时 {dt:.1f} s{extra}{dupe_note}）")
        del window._batch   # 批走完：清账，之后零散任务回到无计数
        # 1D 批走完 → 文件栏的「1D 产物」分组该长出来了（这次算的这批
        # 现在有产物了）；别的视图没有产物，刷了也没变化
        if batch["view"] == "1D":
            window.refresh_groups()
        # 重复条目的面板此刻补画（_batch 已清账 → 补画不会再计数；
        # 缓存命中，毫秒级。见 _plot_selected 的 deferred）
        _reload_deferred(window, batch["view"],
                         batch.get("deferred") or [])
    return suffix, quiet

def show_placeholder(dock, text: str) -> None:
    """面板还没算出数据时，在画布中间写一句话（别让人对着一块空白发懵）。

    用户 2026-09-27："重新打开原始文件…有的是空白的图"——那其实是**还在后台
    算**：面板先建出来，积分结果过一会儿才到（单张 ≈0.4–1 s；81 张排队时
    最后几张要等十几秒）。写一句"正在计算…"，真画上去时 `ax.clear()` 自然
    把它冲掉；算失败时换成"计算失败——见日志"。
    """
    try:
        from xrd_toolkit.gui.plot_panels import _panel_axes
        ax = _panel_axes(dock)
        if ax is None:
            return
        ax.text(0.5, 0.5, text, transform=ax.transAxes, ha="center",
                va="center", color="gray", fontsize=11)
        _content(dock).canvas.draw_idle()
    except Exception:                                    # noqa: BLE001
        pass      # 占位提示画不出来不算事，别拖垮出图这条主路

def _on_integration_error(window: QMainWindow, path: Path, key: str,
                          msg: str) -> None:
    """积分失败（主线程）：报错进日志区，不崩溃（批内带进度计数）。"""
    suffix, _ = _batch_step(window, key)   # 失败永远逐条写（不合并）
    _log(window, f"积分失败：{path.name} —— {msg}{suffix}")
    dock = window.plot_docks.get(key)
    if dock is not None:
        show_placeholder(dock, "计算失败，详见日志")

def _on_view_error(window: QMainWindow, path: Path, key: str, what: str,
                   msg: str) -> None:
    """新视图计算失败（主线程）：报错进日志区，不崩溃。

    what = 计算名（读取/剖面计算/瀑布积分），日志统一 "{what}失败：
    文件名 — 原因"；批内带进度计数后缀。
    """
    suffix, _ = _batch_step(window, key)   # 失败永远逐条写（不合并）
    dock = window.plot_docks.get(key)
    if dock is not None:
        show_placeholder(dock, f"{what}失败，详见日志")
    _log(window, f"{what}失败：{path.name} —— {msg}{suffix}")

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
                         f"（该图面板已有更新的计算）")
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
                     f"（{image.shape[0]}×{image.shape[1]} px）{suffix}")

def _on_profile_done(window: QMainWindow, key: str, task, result) -> None:
    """剖面计算完成（主线程）：结果留面板、画曲线（同 1D 的过期防护）。"""
    # 批量进度：完成任务即计数（大批量时 quiet=True → 不写张张一条）
    suffix, quiet = _batch_step(
        window, key, getattr(window.plot_docks.get(key), "panel_display", ""))
    if window._latest_task.get(key) is not task:
        dock = window.plot_docks.get(key)
        if dock is not None:
            _log(window, f"已忽略 {dock.panel_display} 的过期结果"
                         f"（该图面板已有更新的计算）")
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
                     f"距离 {t.min():.0f}–{t.max():.0f} px）{suffix}")
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
                         f"（该图面板已有更新的计算）")
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

def _process_source(window, source, kw, settings, params0, blank_curve,
                    anchor_source=None) -> dict:
    """把**一条**来源处理一遍并落成产物（[批量处理] 与 [采用这份结果] 共用）。

    为什么必须是同一份实现：屏幕上的曲线与写进产物的这一份必须来自同一个
    `apply_chain` 的输出（见 _proc_curve 的说明）——两处各写一遍必然漂移，
    而漂移的表现是"图上看着好好的、对比里数字对不上"，最难查。

    返回 dict：ok / why（没产出的原因，人话）/ path / key / anchors / dropped
    / kind（这条来源是哪一类条目）。锚点只传 2θ 位置、强度在这条来源自己的
    曲线上重取（跨批次复用与整批处理一贯的口径，见 services/recipes 与
    _proc_batch_apply）。
    """
    path = Path(source.path)
    # 落产物时挂在哪把键下面：RAW 按当前设置算，产物条目挂在它自己那份上
    base_key = None
    if source.kind == gui_sources.RAW:
        tth, intensity, why = _curve_source(window, path, kw)
        if why is not None:
            return {"ok": False, "why": why, "path": path}
    elif source.kind == gui_sources.BG:
        # 处理产物：**从它底下的原始 1D 曲线重做**（用户 2026-09-28 第 3 条）。
        # 不是拿扣完的曲线再扣一遍（那是二次相减，曲线会整体往下掉一截，
        # 看着还挺像"扣得更干净"）。血缘能查到哪把 1D 键就挂回哪把，
        # 查不到（老产物）就按当前设置那把——两种都是"这个文件的一条 1D"。
        got = stage_cache.load_proc_source(source.key)
        if "why" in got:
            return {"ok": False, "why": got["why"], "path": path}
        tth, intensity = got["tth"], got["intensity"]
        base_key = got.get("base")
    else:
        # 1D 产物：读那一份（钉住的键），不按当前设置重算
        got = gui_sources.load_product(source)
        if got is None:
            return {"ok": False, "why": "产物读不到了（被删了？）", "path": path}
        tth, intensity = got
    xs = [x for x, _ in settings["anchors"]]
    per_file, dropped = _anchors_on_curve(xs, tth, intensity)
    params = {**params0, "anchors": per_file}
    processed, base = process.apply_chain(tth, intensity, params,
                                          blank_curve=blank_curve)
    if base is None and settings["mode"] != "off":
        return {"ok": False, "path": path,
                "why": "背景拟合没成功（锚点太少 / 全落在数据范围外？）"}
    proc_settings = {**settings, "anchors": per_file}
    if source.kind == gui_sources.ONED:
        # 挂在**那份 1D 产物**的键下面：勾的是哪一条，处理的就是哪一条
        produced = stage_cache.store_proc_by_key(source.key, tth, processed,
                                                 settings=proc_settings,
                                                 source=path.name)
    elif base_key:
        # 处理产物重做：挂回它血缘上那把 1D 键（查得到的话）
        produced = stage_cache.store_proc_by_key(base_key, tth, processed,
                                                 settings=proc_settings,
                                                 source=path.name)
    else:
        produced = stage_cache.store_proc(path, tth, processed, **kw,
                                          settings=proc_settings)
    if getattr(window, "bg_anchors", None) is None:
        window.bg_anchors = {}
    window.bg_anchors[str(path)] = per_file   # 面板跟着用同一套锚点
    # 配方**按文件**记下来（处理页那行"本图配方"读它）。**不写进面板
    # 快照**：面板显示的曲线只由它自己的快照决定，处理不该偷偷改掉人家
    # 正在看的曲线（用户 2026-09-27 晚："原始数据会被改变"）
    note_proc_recipe(window, path,
                     _proc_recipe({**settings, "anchors": per_file},
                                  source=anchor_source))
    return {"ok": True, "why": None, "path": path, "key": Path(produced).stem,
            "anchors": per_file, "dropped": len(dropped),
            "kind": source.kind}

def _proc_save(window: QMainWindow) -> None:
    """处理页唯一的产出按钮（2026-10-07 用户定：两个合成一个）。

    勾了文件 = 批量处理勾选的每一份（原 [批量处理] 的语义——RAW /
    1D 产物 / 处理产物都能当输入）；一个没勾 = 存编辑对象这一张
    （原 [存成产物] 的语义——产物面板先就地换回它底下的原始曲线）。
    两种模式共用同一台机器（`_process_source` 逐条处理 + 同一套台账），
    只是目标集不同，所以这里只做分发。按钮文字跟着勾选数走（
    file_dock._refresh_check_labels）。
    """
    if gui_sources.checked_sources(window):
        _proc_batch_apply(window)
    else:
        _proc_keep_this(window)

def _proc_keep_this(window: QMainWindow) -> None:
    """[采用这份结果]：把编辑对象这张图上**眼下这条处理后的曲线**落成产物。

    用户 2026-09-28 第 4 条："处理单张 1d 数据应该在用户确定处理结果后出现在
    文件区的处理后数据中"——单张处理原先只活在画布上（_refresh_proc 只重画、
    什么都不写），想留住成果得"勾上自己再点 [批量处理]"，绕且不好理解。
    这里就是那一步的显式入口：一个来源、一次台账、文件栏立刻长出一组
    「处理后 …」。落的产物与 [批量处理] 完全同一种（同一份 apply_chain 输出、
    同一套台账），所以对比 / 热图 / 导出 / 双击打开全都能直接用它。
    """
    dock = window.plot_docks.get(window.focus_panel)
    path = _bg_path_of(dock) if dock is not None else None
    if dock is None or path is None:
        _log(window, "先点一张 1D 图（编辑对象），再点 [存成产物]")
        return
    # 处理产物面板：先就地换成它底下的原始 1D 曲线（用户 2026-09-28 第 3 条），
    # 换成功就照常"采用"——存下来的是一份**重做**的产物，不是二次相减
    if not _retarget_product_panel_to_source(window, dock):
        return
    settings = _proc_settings(window, dock, path)
    params0 = _proc_params(window, dock, path)
    if settings["mode"] == "off" and not process.chain_parts(settings):
        _log(window, "「处理」页里三项都关着（背景扣除 / 平滑 / 裁剪）——"
                     "先开一项，再点 [存成产物]（否则存下来的与原始曲线一样）")
        return
    src = getattr(dock, "panel_source", None)
    source = src if src is not None else gui_sources.make_source(path)
    geom = _collect_geometry(window)
    npt = int(window.params["输出点数"].value())
    kw = dict(config=window.config_name, npt=npt,
              tth_min=geom.get("tth_min_deg"), tth_max=geom.get("tth_max_deg"))
    _consume_data_params(window)   # 按这把范围取数：这组值用掉了
    blank = getattr(window, "bg_blank", None)
    blank_curve = ((blank["tth"], blank["intensity"])
                   if settings["mode"] == "blank" and blank is not None
                   else None)
    # 锚点来处 = 本图手点（None）：单张处理的锚点就是这个文件自己的
    res = _process_source(window, source, kw, settings, params0, blank_curve,
                          anchor_source=None)
    if not res["ok"]:
        _log(window, f"没存成产物：{res['why']}")
        return
    label, batch = _proc_batch_label(settings)
    stage_cache.record_batch(
        "bg", batch, label=label, items=[(res["path"], res["key"])],
        config=window.config_name, npt=npt,
        tth_min=kw.get("tth_min"), tth_max=kw.get("tth_max"),
        settings={k: v for k, v in settings.items() if k != "anchors"},
        note="1 个文件")
    _log(window, f"已存成产物：{Path(path).name}"
                 f"（{process.chain_label(settings)}）——文件栏「{label}」里"
                 f"可以整组拿去 [对比] / [热图]，双击看这一条")
    window.refresh_groups()       # 文件栏里立刻长出这一组

def _proc_ensure_curves(window: QMainWindow, missing: list, kw: dict,
                        geom: dict, npt: int, then) -> None:
    """把"按当前设置还没算过 1D"的文件后台补算出来，算完调 then()。

    为什么要有这一步（用户 2026-09-30 报的尾巴）：[批量处理] 的输入是"每个
    文件按**当前设置**在缓存里的那条 1D 曲线"（见 _curve_source 为什么必须
    同一把键）。改了坞顶的 2θ 范围直接点 [批量处理]，这些文件的曲线还是旧
    范围的——旧版**逐条跳过**并写明原因（2026-09-28 定的口径，底线是不许拿
    旧范围的曲线凑数，那条不变），但用户的期待很直白：改了范围，[批量处理]
    就该按新范围出产物，而不是每次先手动跑一遍 1D。

    所以这里补上：复用 [1D] 那条后台任务（同一个 _spawn，算完顺手
    stage_cache.store_1d），进度条与失败原因都走已有通路；全部落地后接着
    跑处理链（then）。补算出来的 1D 照常进文件栏「1D 产物」分组——它们
    本来就是这一趟要用的曲线，不是临时垃圾。

    同一时间只允许一拨：上一拨还在算时再点 [批量处理] 只记一句（否则
    两拨回调会各自接着跑一遍处理，产物与台账都翻倍）。
    """
    if getattr(window, "_proc_prep", None) is not None:
        _log(window, "上一批还在按当前设置重算 1D，等它跑完再点 [存成产物]")
        return
    lo, hi = kw.get("tth_min"), kw.get("tth_max")
    span = (f"2θ {lo:g}–{hi:g}°" if lo is not None and hi is not None
            else "当前设置")
    state = {"left": len(missing), "total": len(missing), "failed": 0}
    window._proc_prep = state
    _log(window, f"这批里有 {len(missing)} 个文件按{span}还没算过 1D"
                 "——先在后台补算，算完自动接着处理")
    _progress_show(window, len(missing))

    def one_finished() -> None:
        state["left"] -= 1
        _progress_show(window, state["total"], state["total"] - state["left"])
        if state["left"] > 0:
            return
        window._proc_prep = None
        _progress_hide(window)
        window.refresh_groups()      # 刚补算的 1D 该进文件栏了
        if state["failed"]:
            _log(window, f"其中 {state['failed']} 个文件没算成（见上面的日志），"
                         "它们会被跳过")
        _log(window, f"1D 补算完成（{state['total']} 个），接着处理…")
        then()

    def on_done(window_, key_, task, result) -> None:
        one_finished()

    def on_error(msg) -> None:
        state["failed"] += 1
        _log(window, f"1D 补算失败：{msg}")
        one_finished()

    for source in missing:
        _spawn(window, Path(source.path), geom, npt,
               "1D|" + str(source.path), on_done=on_done, on_error=on_error)

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

    改了坞顶 2θ 范围后直接点这里（2026-09-30 起）：这批文件按当前设置还没
    算过 1D 的，**先在后台补算再处理**（见 _proc_ensure_curves）——不再"逐条
    跳过"。补算出来的 1D 照常进文件栏「1D 产物」。

    可处理的条目 = **原始数据** + **1D 产物**（它就是那条原始积分曲线，只是
    钉在某一份缓存上，见 _open_product_panel 的说明）+ **处理产物**——后者
    按**它底下的原始 1D 曲线重做**（用户 2026-09-28 第 3 条："套用别的配方的
    处理图不能二次处理了，修正"）。旧版把处理产物整条跳过（"幂等，不再来
    一遍"），可用户要的是"换套参数重做"：现在输入换成没扣过背景的那条曲线，
    所以既做得到、又不会二次扣除/二次平滑（见 stage_cache.load_proc_source）。
    """
    dock = window.plot_docks.get(window.focus_panel)
    focus_path = _bg_path_of(dock) if dock is not None else None
    if dock is None or focus_path is None:
        _log(window, "先点一张 1D 图（编辑对象），再点 [存成产物]")
        return
    settings = _proc_settings(window, dock, focus_path)
    params0 = _proc_params(window, dock, focus_path)
    focus_display = getattr(dock, "panel_display", None) or Path(focus_path).name
    chain = process.chain_parts(settings)
    if settings["mode"] == "off" and not chain:
        _log(window, "「处理」页里三项都关着（背景扣除 / 平滑 / 裁剪）——"
                     "先开一项，再点 [存成产物]")
        return
    xs = [x for x, _ in settings["anchors"]]
    if settings["mode"] == "anchor" and not xs:
        _log(window, "先在图上点几个锚点（背景扣除模式为「手动锚点」），"
                     "再点 [存成产物]")
        return
    # 可处理的是原始数据 + 1D 产物 + 处理产物（后者按它底下的原始曲线重做，
    # 见 docstring——用户 2026-09-28 第 3 条）
    picked = gui_sources.checked_sources(window)
    targets = [s for s in picked
               if s.kind in (gui_sources.RAW, gui_sources.ONED, gui_sources.BG)]
    redone = [s for s in targets if s.kind == gui_sources.BG]
    if not targets:
        _log(window, "未勾选任何项")
        return
    if redone:
        _log(window, f"其中 {len(redone)} 条是处理产物：按它底下的原始 1D "
                     f"曲线重做（不是二次相减）")
    geom = _collect_geometry(window)
    npt = int(window.params["输出点数"].value())
    kw = dict(config=window.config_name, npt=npt,
              tth_min=geom.get("tth_min_deg"), tth_max=geom.get("tth_max_deg"))
    _consume_data_params(window)   # 按这把范围取数：这组值用掉了
    blank = getattr(window, "bg_blank", None)
    blank_curve = ((blank["tth"], blank["intensity"])
                   if settings["mode"] == "blank" and blank is not None
                   else None)
    # 改了 2θ 范围就直接点这里：这批文件按**当前设置**还没算过 1D
    # （判断口径与取数完全同一把键，见 _curve_source）。缺就先在后台补算，
    # 算完自动接着处理——用户 2026-09-30："改完 2θ 范围后 [批量处理]
    # 不会自动按新范围重积分，只逐条跳过并写明原因"。
    missing = [s for s in targets
               if s.kind == gui_sources.RAW
               and _curve_source(window, Path(s.path), kw)[2] is not None]
    if missing:
        _proc_ensure_curves(
            window, missing, kw, geom, npt,
            lambda: _proc_batch_run(window, targets, kw, settings, params0,
                                    blank_curve, focus_display))
        return
    _proc_batch_run(window, targets, kw, settings, params0, blank_curve,
                    focus_display)

def _proc_batch_run(window: QMainWindow, targets: list, kw: dict, settings: dict,
                    params0: dict, blank_curve, focus_display: str) -> None:
    """[批量处理] 的主循环：逐条取曲线 → 跑链 → 落产物 → 记台账。

    从 _proc_batch_apply 拆出来（2026-09-30）：它现在有两种到达方式——
    曲线都齐就直接跑；缺"按当前设置那条曲线"的（改了 2θ 还没重算的批）
    先由 _proc_ensure_curves 后台补算，算完在回调里再跑同一段。
    """
    npt = int(kw["npt"])
    done = skipped = from_product = from_bg = 0
    skip_why = {}                         # 跳过原因 → 个数（末尾各记一行）
    out_of_range = files_with_drop = 0    # 锚点落到数据范围外的统计
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
        # 一条来源的"取曲线 → 重取锚点强度 → 跑整条链 → 落产物 → 记配方"
        # 整段都在 _process_source 里（与 [采用这份结果] 共用同一份实现）
        res = _process_source(window, source, kw, settings, params0,
                              blank_curve, anchor_source=focus_display)
        if not res["ok"]:
            skipped += 1
            skip_why[res["why"]] = skip_why.get(res["why"], 0) + 1
            continue
        if res["dropped"]:
            # 锚点落在本图数据范围外：**丢掉**（np.interp 会静默钳到端点值，
            # 等于拿边缘那一点当锚点，图上完全看不出来）。跨批次复用配方时
            # 这条最常见——用户 2026-09-27 讨论时一起定的口径。
            out_of_range += res["dropped"]
            files_with_drop += 1
        records.append((res["path"], res["key"]))
        if res["kind"] == gui_sources.ONED:
            from_product += 1
        elif res["kind"] == gui_sources.BG:
            from_bg += 1
        done += 1
        if (i + 1) % 20 == 0:
            _log(window, f"批量处理：{i + 1}/{len(targets)}…")
    tail = f"，跳过 {skipped} 个" if skipped else ""
    via = ""
    if from_product:
        via += f"，其中 {from_product} 条来自 1D 产物"
    if from_bg:
        via += f"，其中 {from_bg} 条是处理产物（按各自的原始曲线重做）"
    _log(window, f"批量处理完成：{done}/{len(targets)} 个文件"
                 f"（{process.chain_label(settings)}）{tail}{via}")
    # 跳过原因逐条写清楚（不静默）：最常见的是"2θ 改过、按新范围还没算"
    # ——旧版这种情况会**拿面板里旧范围的曲线**接着扣，键与数据对不上
    for why, n in sorted(skip_why.items(), key=lambda kv: -kv[1]):
        _log(window, f"  跳过 {n} 个：{why}")
    if out_of_range:
        _log(window, f"注意：{files_with_drop} 个文件上有 {out_of_range} 个锚点"
                     f"落在数据 2θ 范围外，已跳过那些锚点"
                     f"（跨批次复用配方时常见——确认装置/几何没换）")
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

    标签给人看（进文件坞的分组名 + 日志）：「处理产物 09-25 16:40（锚点 5 个、
    窗口 2°、平滑 0.15°、删 2–3°）」——三项都真的作用在数据上，所以三项都
    写；组名必须描述数据本身，不能写没生效的东西（见 process.chain_label）。
    时间 = **最近一次保存的时刻**（同一配置重复点会把它刷新）。

    批次号给程序用 = **处理链参数的哈希（不含时间戳，2026-10-07 改）**：
    用户的原话——"每次只有当前配置的一批进入文件栏……不同参数多次点击，
    那就都进文件栏"。所以：
      * 同一套参数不管点几次、隔多久点，都归同一批：台账里再记一次 =
        那份节点原地更新（组名里的时间与排序刷新到最近这次，**不再冒
        "· 第 2 组"重复组**——改之前批次号带秒级时间戳，隔一秒再点就
        长出第二个组）；
      * 换了参数（模式/窗口/锚点/平滑/裁剪——proc_settings_hash 的全部门槛）
        就是另一批：各组并存，正是"不同参数分别点击"的保存。
    """
    label = (f"处理产物 {time.strftime('%m-%d %H:%M')}"
             f"（{process.chain_label(settings)}）")
    batch = stage_cache.proc_settings_hash(settings)
    return label, batch

def _refresh_1d_display(window: QMainWindow) -> None:
    """「1D 显示」参数（对数纵轴 / 纵轴自动 / 纵轴范围）改了 → 就地重画。

    2026-10-07 这一小节从「原图」页搬回「1D」页时从"要按 [应用显示设置]"
    改成**即改即画**：那个按钮住在「原图」页，搬过来就够不着了（这个应用
    反复在修的"够不着"病；对照 10-02 对比四参数、10-07 瀑布行距的同款处理）。

    作用域 = **编辑对象这一个面板**（显示参数每张图各记各的，10-02 起的
    规矩）：不学 _refresh_compare 的"所有同类面板"——1D 面板可以同时开
    很多张，每张各有各的缩放/刻度，改了这张不该顺带改其它张。对比面板
    非堆叠时也读这几个键 → 焦点是它时同样就地重画。
    """
    if getattr(window, "_param_replaying", False) \
            or getattr(window, "_param_box_sync", False):
        return   # 回放快照 / 程序同步写控件（缩放写回等）都不是用户改动
    keys = ("对数纵轴", "纵轴自动", "纵轴下限", "纵轴上限")
    dock = window.plot_docks.get(window.focus_panel)
    if dock is None:
        # 没有编辑对象：记进"待应用"账本，出图时当新面板的起点
        # （用户 2026-10-13："出图之前调整参数会无效"）
        _stash_pending_display(window, keys)
        return
    _drop_pending_display(window, keys)   # 有面板接住：账本里这几个键作废
    snap = _display_snapshot(window, dock.params_snapshot)
    if snap.get("裁剪区间"):
        snap["裁剪区间"] = list(getattr(window, "cut_list", []) or [])
    dock.params_snapshot = snap
    view = window.focus_panel.split("|", 1)[0]
    if view == "1D" and getattr(dock, "last_tth", None) is not None:
        _draw_1d(window, dock, dock.last_tth, dock.last_intensity)
    elif view == "对比" and getattr(dock, "compare_data", None):
        from xrd_toolkit.gui.plot_compare import (   # 破循环：延迟导入
            _redraw_compare)
        _redraw_compare(window, window.focus_panel)

def _refresh_waterfalls(window: QMainWindow) -> None:
    """瀑布的行距控件（[手动行距] + 倍数）改了 → 各瀑布面板就地重画。

    与 plot_compare._refresh_compare 同套路（那是用户 2026-10-02 报
    "堆叠显示无效"时定的：显示参数改了就该立刻看见，不能"够不着"）：
    把控件值写进各瀑布面板的快照，再用已有数据重画——纯显示、不重算。

    只写这两个键，**不整包套用别的显示参数**：控件是所有面板共用的
    一份，控件值可能正显示着别的图（编辑对象）的设置，整包写过去会把
    那张图的配色等串到所有瀑布上。
    """
    if getattr(window, "_param_replaying", False):
        return   # 回放快照期间控件值正被程序改写，不是用户改动
    keys = ("瀑布手动行距", "瀑布行距倍数")
    chk = window.params.get("瀑布手动行距")
    mult = window.params.get("瀑布行距倍数")
    if chk is None or mult is None:
        return   # 裸窗口（测试里没建这对控件）
    handled = 0
    for key, dock in list(window.plot_docks.items()):
        if not key.startswith("瀑布"):
            continue
        snap = getattr(dock, "params_snapshot", None)
        if snap is not None:
            snap["瀑布手动行距"] = bool(chk.isChecked())
            snap["瀑布行距倍数"] = float(mult.value())
        handled += 1     # 快照已收下（含还在算的：算完自己按新值画）
        data = getattr(dock, "last_waterfall", None)
        if data is None:
            continue     # 还没算完：等它自己画
        tth, i2d, chi = data
        _draw_waterfall(window, dock, tth, i2d, chi)
    if handled:
        _drop_pending_display(window, keys)
    else:
        _stash_pending_display(window, keys)   # 没有瀑布面板：记进待应用账本

def _refresh_heatmap_names(window: QMainWindow) -> None:
    """「热图显示」小节的 [显示数据名] 改了 → 各热图面板就地重画（不重算）。

    2026-10-12 用户拍板"甲，拆开"（原话："对比热图现在共用一个显示数据
    名，但是是在对比的板块"）——热图行名从「对比显示」小节那把开关里分
    出来，勾选框住在「热图显示」小节，键 =「热图显示数据名」，改哪边只
    动自己那一类图。做法同前身：只写这一个键、再按已有数据重画——
    **不整包套用别的显示参数**（控件是所有面板共用的一份，整包写会把
    别的图的设置串过来，同 _refresh_waterfalls 的判例）。
    """
    if getattr(window, "_param_replaying", False):
        return   # 回放快照期间控件值正被程序改写，不是用户改动
    chk = window.params.get("热图显示数据名")
    if chk is None:
        return   # 裸窗口（测试里没建这个控件）
    on = bool(chk.isChecked())
    from xrd_toolkit.gui.plot_compare import (   # 破循环：只取这一个
        _draw_heatmap)
    handled = 0
    for key, dock in list(window.plot_docks.items()):
        if not key.startswith("热图"):
            continue
        snap = getattr(dock, "params_snapshot", None)
        if snap is not None:
            snap["热图显示数据名"] = on
        handled += 1     # 快照已收下（含还在算的）
        data = getattr(dock, "heat_data", None)
        if data is not None:
            _draw_heatmap(window, dock, data[0], data[1], data[2])
    if handled:
        _drop_pending_display(window, ("热图显示数据名",))
    else:
        _stash_pending_display(window, ("热图显示数据名",))

# 一次批量作图的防爆线（2026-09-24 用户"图一多就很卡"；开图是唯一随数量
# 变慢的成本：第 1 张 136 ms、第 100 张 287 ms——每开一张都要把已开的子窗口
# 重排一遍——而且每张 ≈15 MB，真机 81 张 ≈1.4 GB）。**超过这个数一张都不画**
# （边界 = **25 张全画、26 张起防爆**——2026-10-08 晚用户先拍的"24 全画"，
# 2026-10-09 用户改为"最多 25"："超过一次最多画的 24 张改为最多25"；
# 10-08 同一天从"只有 1D 防爆、原图只画前 N 张"统一到**所有视图同款防爆**，
# 用户："原图批量出图没有防爆图"）。1D 超限照旧全部只算不画、进文件栏
# 「1D 产物」；其余视图没有产物可留，直接不弹面板——出口都在日志里写明
# （[热图]/[对比] 一张图放完整批；想一张张全开走文件栏右键的
# 「打开勾选的 N 张图」→ 选视图，没有上限、只弹确认——2026-10-09 起
# 四种视图都有，见 file_dock._open_checked_views）。
MAX_PANELS_PER_BATCH = 25

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

def _pending_products(window: QMainWindow, name: str, rest: list,
                      seen=None) -> list:
    """超限文件里**还需要算**的那些（1D 专用；其余视图返回空表）。

    为什么只认 1D：只有 1D 有产物可留（2D/剖面/瀑布是显示阶段，见
    services/stage_cache 的说明），"只算不画"对它们没有意义。
    已有产物的直接跳过——不重算，**也不计进这一批的总数**：总数为 0
    的批不该开进度条，更不该让 k/n 永远到不了 n。
    **同一路径的重复条目也只算一遍**（2026-10-09 用户："去重，减少
    工作量，出图速度永远是越快越好"）——勾 83 条里有 2 条重复，这批
    就是 81 张，产物也是 81 条，数字处处对得上。
    """
    if name != "1D" or not rest:
        return []
    geom = _collect_geometry(window)
    npt = int(window.params["输出点数"].value())
    kw = dict(config=window.config_name, npt=npt,
              tth_min=geom.get("tth_min_deg"), tth_max=geom.get("tth_max_deg"))
    _consume_data_params(window)   # 按这把范围取数：这组值用掉了
    todo = []
    done = set(seen or ())    # 已在面板那批开算的路径（重复条目被防爆线
    #                           劈开时，后半截也不能再算一遍）；同批内同理
    for source in rest:                       # rest = 来源对象（已按原始数据过滤）
        path = Path(source.path)
        if path in done:
            continue
        done.add(path)
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

def _open_source_view(window: QMainWindow, name: str, source) -> str:
    """按需打开**一条**条目的图面板（文件栏双击 / 右键 [打开 1D 图]）。

    用户 2026-09-26 定："选完原始图，进行 1d 后不弹框，直接列出在文件区，
    然后可以点开看"——大于上限的批次只算不画，看哪张就点哪张。
    不碰勾选状态（双击的第一次单击可能顺手改了勾选，但不影响这里）；已有
    面板就前置，没有就建；数据走同一条 _run_view（1D 命中缓存即秒开）。
    """
    if source.kind != gui_sources.RAW:
        return _open_product_panel(window, source)   # 产物：直接读盘画线
    path = Path(source.path)
    key, dock = _resolve_dock(window, name, source.item)
    if dock is None:
        _apply_range_memory(window, path)   # 甲：新图用该文件自己的范围，不用盒子里的遗留值
        dock = _open_plot_panel(window, name, key, f"{name}_{source.display}")
        dock.panel_file = path
        dock.panel_item = source.item
        dock.panel_display = source.display
        dock.figure_saved = False
        dock.params_snapshot = _data_snapshot(window)
        # 面板先建、数据后到（后台积分）——中间那段别让人对着一块空白
        show_placeholder(dock, "正在计算…")
        view_word = f" {name} " if name.isascii() else name   # 中英混排：1D/2D 两侧留空格
        _log(window, f"打开{view_word}面板：{source.display}")
    dock.setVisible(True)
    dock.raise_()          # 从文件栏点开的图，摆到最前面
    _run_view(window, name, path, key)
    return key

def _reload_deferred(window: QMainWindow, name: str, keys) -> None:
    """批尾补画"重复条目"的面板：从产物缓存读（毫秒级），不重算。

    两条到达路径（见 _plot_selected 尾部）：批正常收尾时由 _batch_step
    调（此时 _batch 已清账 → 补画不会二次计数）；整批在循环里就收完尾
    （全缓存命中、同步完成）时由 _plot_selected 自己调。面板已被关掉
    的键跳过；缓存万一没命中会走一次正常积分（可接受，且有日志）。
    """
    for key in keys:
        dock = window.plot_docks.get(key)
        if dock is None:
            continue
        _run_view(window, name, Path(dock.panel_file), key)

def _open_source_group(window: QMainWindow, name: str, sources) -> int:
    """整组打开（右键 [打开整组 N 张]）：逐条走 _open_source_view。

    开面板是主线程上的活、而且每张 ≈15 MB，所以每 8 张报一次进度并消化
    事件（同批量开图的老套路，界面不会闷住）；返回真开了几条。
    """
    sources = list(sources)
    view_word = f" {name} " if name.isascii() else name   # 中英混排：1D/2D 两侧留空格
    _log(window, f"打开整组{view_word}图：{len(sources)} 张…")
    for i, source in enumerate(sources):
        try:
            _open_source_view(window, name, source)
        except Exception as err:                       # 单张失败不拖垮整组
            _log(window, f"{source.display}：打开失败"
                         f"（{user_error_text(err)}）")
        if i and (i + 1) % 8 == 0:
            _log(window, f"  已开 {i + 1}/{len(sources)}…")
            _settle(window)
    _log(window, f"打开整组{view_word}图完成：{len(sources)} 张")
    return len(sources)

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
        _log(window, f"打开 1D 面板：{source.display}"
                     f"（{gui_sources.describe_source(source)}，直接读取，不重算"
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

    超过 MAX_PANELS_PER_BATCH 张**一张都不画**（防爆图，边界与理由见
    常量旁的注释）：日志写明这批多少张、出口在哪（1D 只算不画、进
    「1D 产物」；其余视图用 [热图]/[对比] 看整批），不静默少画。进度
    记账的总数 = 这一批真要跑的张数，否则 k/n 永远到不了 n、批也不清账。
    """
    checked = gui_sources.checked_sources(window)
    if not checked:
        _log(window, "未勾选任何项")
        return
    # 产物条目（"1D"/"扣背景"）是现成的 1D 曲线，没有"从原始图算"这一步：
    # 只有 1D 视图能出，其余视图点名跳过（不静默少画）
    raw = [s for s in checked if s.kind == gui_sources.RAW]
    products = [s for s in checked if s.kind != gui_sources.RAW]
    n_dupe = 0            # 同一路径的重复条目数（批完成时还要再提一句）
    if name == "1D" and raw:
        # 重复条目（同一条数据以 xxx.tif / xxx (1).tif 加了两次）：本批
        # **只算一遍**（2026-10-09 用户："去重，减少工作量，出图速度永远
        # 是越快越好"）——第一次出现照常，后来的条目整条跳过（下面的
        # repeat_items），产物也按**数据**只存一份（键 = 文件指纹）。
        # 以前一个字都不说，只看见"勾了两条、文件栏只长出 1 条"（用户
        # 2026-10-07："重复的原始数据生成产物时会只生成一个，在日志里
        # 提到这个事情"）。[批量处理] 那条路已有逐条"同一文件只扣一份"
        # 的日志（_proc_batch_run 的 seen），这里给 1D 补一句汇总。
        paths = [str(Path(s.path)) for s in raw]
        n_dupe = len(paths) - len(set(paths))
        if n_dupe:
            _log(window, f"注意：勾选里有 {n_dupe} 条是同一文件的重复条目"
                         "——同一份数据只算一遍（图照开，重复条目的面板"
                         "等本批算完从缓存补画），产物共用")
    if products and name != "1D":
        _log(window, f"跳过 {len(products)} 个产物条目：{name} 要从原始图像"
                     "算（产物是 1D 曲线，只能出 1D 图 / 对比 / 热图）")
        products = []
    targets = raw[:MAX_PANELS_PER_BATCH]
    rest = raw[len(targets):]
    if len(raw) > MAX_PANELS_PER_BATCH:
        # 勾得比防爆线还多 → **一张都不画**（用户 2026-09-26 定："防爆图"；
        # 2026-10-08 从"只有 1D、且原图只画前 N 张"统一到所有视图——用户：
        # "原图批量出图没有防爆图"）。1D 全部只算不画、进「1D 产物」；
        # 其余视图（2D/剖面/瀑布）是显示阶段没有产物可留，直接不弹面板。
        targets, rest = [], list(raw)
    # 只算不画的文件**照样算完入库**：1D 有产物可留，之后单独点开就是
    # 复用缓存；已有产物的直接跳过（不重算、也不占进度总数）。
    # 其余视图（2D/剖面/瀑布）是显示阶段、没有产物可留，就只记日志。
    pending = _pending_products(window, name, rest,
                                seen={Path(s.path) for s in targets})
    if rest:
        why = (f"这批 {len(raw)} 张超过一次最多画的 {MAX_PANELS_PER_BATCH} 张"
               "（每张 ≈ 15 MB、越开越慢）")
        # 出口：右键「打开勾选的 N 张图」→ 选视图（2026-10-09 起四种视图
        # 都有——此前只有 1D 一条入口，原图三类超限后没有任何手动全开的
        # 路，用户："原图功能无法批量出超过防爆图数字"）
        if name == "1D":
            _log(window, f"{why}：全部只算不画——结果进文件栏"
                         "「1D 产物」，双击看一张；想一次全开：右键文件栏"
                         "（或「原始数据」组）→「打开勾选的 N 张图」→ 1D")
        else:
            _log(window, f"{why}：这次一张都不弹——要看整批用 [热图] / "
                         f"[对比]（一张图看完）；想一张张全开：右键文件栏 →"
                         f"「打开勾选的 N 张图」→ {name}（先弹确认）；"
                         "或减少勾选分批看")
    # 同一文件的重复条目：**本批只算一遍**（2026-10-09 用户："去重，减少
    # 工作量，出图速度永远是越快越好"），但**图仍是每条一张**（2026-10-10
    # 用户："出 1d 图变少，这个怎么还没修复"——上一版把面板也省了，
    # 勾 5 条出 4 张，看着就是少画了）：第一次出现照常算；后来的条目
    # 照常建面板、只是**不排积分**，批收尾时从产物缓存补画（毫秒级）。
    repeat_items = set()
    if n_dupe:
        seen_paths = set()
        for source in targets:
            p = str(Path(source.path))
            if p in seen_paths:
                repeat_items.add(source.item)
            else:
                seen_paths.add(p)
    total_tasks = len(targets) - len(repeat_items) + len(pending)
    # 只开**一张**新图：2θ/点数默认用该文件自己上次的（甲，见 _apply_range_memory）；
    # 批量（≥2 张）仍用坞顶那行当输入——一组图总得有一个共同范围
    single_new = total_tasks == 1
    if total_tasks > 1:
        # 批量进度记账：这一批的总数/视图名/起算时刻；每个任务结束回调
        # 计数一次（k/n 后缀与进度条都靠它，批走完自动清账）
        window._batch = {"view": name, "total": total_tasks, "done": 0,
                         "start": time.time(), "cached": 0, "dupe": n_dupe}
        _progress_show(window, total_tasks)
    opened = []          # 新开的面板显示名（大批量时合并成一行）
    merged = total_tasks > BATCH_LOG_MERGE_AFTER
    view_word = f" {name} " if name.isascii() else name   # 中英混排：1D/2D 两侧留空格
    # 产物条目先画：读盘画线（毫秒级）不需要排队等积分，也不进进度条
    for source in products:
        _open_product_panel(window, source)
    skipped_repeats = 0
    deferred = []        # 重复条目的面板键：不排积分，批尾从缓存补画
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
            if single_new and source.kind == gui_sources.RAW:
                _apply_range_memory(window, path)   # 甲：单张新图用该文件自己的范围
            # 新面板级联摆放，现有面板原地不动（开新图不再重排旧图）
            dock = _open_plot_panel(window, name, key, title)
            dock.panel_file = path   # 面板绑定自己的文件（删文件不影响已开的面板）
            dock.panel_item = source.item   # 面板绑定自己的列表条目（重名条目各自成图）
            show_placeholder(dock, "正在计算…")   # 同上：算完自然被冲掉
            dock.panel_display = display   # 显示名（标题/日志/默认存盘名用）
            dock.figure_saved = False   # 有没有存过盘（关窗询问用）
            dock.params_snapshot = _data_snapshot(window)   # 开图快照：数据用当前值，显示从默认起步
            if merged:
                opened.append(display)      # 大批量：攒着，循环后一行写完
            else:
                _log(window, f"打开{view_word}面板：{display}")
        dock.setVisible(True)
        if source.item in repeat_items:
            # 同一文件本批已算/在算：面板照建，但不排任务——批尾从缓存补画
            skipped_repeats += 1
            if getattr(dock, "last_tth", None) is None:
                show_placeholder(dock, "同一文件本批只算一遍——算完自动补画")
            deferred.append(key)
            continue
        _run_view(window, name, path, key)
    if opened:
        head = "、".join(opened[:5]) + ("…" if len(opened) > 5 else "")
        _log(window, f"打开{view_word}面板 {len(opened)} 张：{head}")
    if skipped_repeats:
        _log(window, f"{skipped_repeats} 条重复条目本批只算一遍：它们的图"
                     "照开，等本批算完从缓存补画（毫秒级）")
    if total_tasks > 1:
        _progress_show(window, total_tasks)   # 进入计算阶段（总数已按去重算）
    for path in pending:
        _spawn_headless(window, name, path)
    if deferred:
        # 批还没收尾 → 交给收尾（_batch_step）补；已经在循环里收完尾
        # （全缓存命中、同步完成）→ 这里直接补。两条路都不落空
        if getattr(window, "_batch", None) is not None:
            window._batch["deferred"] = list(deferred)
        else:
            _reload_deferred(window, name, deferred)
    if name == "1D" and rest and not pending:
        # 超限 + 产物全都在（刚做过对比、或上一轮 1D 刚算完，缓存全热）：
        # 0 个任务 → 没有进度条、也不会有批收尾（_batch_step 的完成行与
        # 文件栏刷新都挂在那上面）——不在这儿收尾，点 [1D] 就是"什么都
        # 不发生"：日志只有上面那行防爆线提示、产物也不进文件栏。用户
        # 2026-10-15 现场："导入原始数据后，直接进行对比出图，然后再点
        # 击 1d 出图，就会卡住"——复现实测主线程没卡（看门狗 8 s 阈值
        # 没留档），就是这条全缓存静默路。这里补一次与批正常走完同一个
        # 出口的收尾：刷新文件栏（"文件栏出现 N 条新产物"由它写）+ 明说
        # 一句，让这一次点击有下文。
        window.refresh_groups()
        _log(window, f"这 {len(rest)} 张的 1D 结果都已经算好（复用缓存，"
                     "本次无需重算）——产物就在文件栏「1D 产物」：双击看"
                     "一张；想一次开多张：勾上要看的产物 → 右键 →「打开"
                     "勾选的 N 张图」→ 1D")
        return
