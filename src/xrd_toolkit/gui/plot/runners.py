"""单文件视图 runner：注册表分发 + 1D/2D/剖面/瀑布四个计算入口。

2026-10-22 从 plot_views.py 原样搬出（只改结构，不动逻辑）。
plot_views 再导出，测试与探针照旧经 gui_plot_views 取用。
"""
from pathlib import Path

from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.gui.panel_state import (
    DATA_PARAM_DEFAULTS, _collect_geometry, _data_snapshot, _log,
    _note_params_consumed, _panel_param, _refresh_pending_labels, _set_focus)
from xrd_toolkit.gui.plot.drawing import _draw_1d
from xrd_toolkit.gui.plot.spawn import (
    _batch_step, _on_image_done, _on_profile_done, _on_view_error,
    _on_waterfall_done, _spawn, _spawn_task)
from xrd_toolkit.gui.plot.workers import (
    _compute_image, _compute_profile, _compute_waterfall)
from xrd_toolkit.services import stage_cache


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
    mask_on = getattr(window, "current_mask", None) is not None
    cached = None
    if not mask_on:
        try:
            cached = stage_cache.load_1d(
                path, config=window.config_name, npt=npt,
                tth_min=geom.get("tth_min_deg"),
                tth_max=geom.get("tth_max_deg"))
        except Exception:                                # noqa: BLE001
            cached = None    # 缓存读失败（文件被删/权限）：当作没缓存
    else:
        _log(window, f"Mask 生效：{path.name} 重算 1D"
                     f"（不使用未遮罩的缓存）")
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
        dock.mask_epoch = int(getattr(window, "mask_epoch", 0))
        dock.mask_on = False          # 缓存只在不遮罩时才会命中
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
    mask = getattr(window, "current_mask", None)
    _log(window, f"开始扇形积分 {path.name}（后台运行，36 扇区）"
                 + ("（使用当前 Mask）" if mask is not None else ""))
    args = ((str(path), geom, npt) if mask is None
            else (str(path), geom, npt, mask))
    _spawn_task(window, key, _compute_waterfall, args,
                _on_waterfall_done,
                lambda msg: _on_view_error(window, path, key, "扇形积分", msg))

# 视图注册表：视图名 → 计算 runner（签名 window/path/key/geom/npt）。
# 新视图接线 = 加条目，_run_view 分发骨架不动
_VIEW_RUNNERS = {"2D": _run_2d, "剖面": _run_profile, "1D": _run_1d,
                 "瀑布": _run_waterfall}
