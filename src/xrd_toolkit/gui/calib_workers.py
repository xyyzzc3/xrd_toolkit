"""校准后台任务：自动定位环心 + 精修 / 手动选点精修 / 两个启动入口。

2026-10-22 从 calib.py 原样搬出（只改结构，不动逻辑）。
calib 再导出；对 _on_calib_result 用函数内延迟导入破环。
"""
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.core.processor import find_ring_center, fit_center_from_rings
from xrd_toolkit.gui.calib_calibrant import _warn_pixel_unchecked
from xrd_toolkit.gui.calib_metrics import _attach_metrics
from xrd_toolkit.gui.calib_model import MIN_POINTS, MIN_RINGS, _calib_state
from xrd_toolkit.gui.calib_panel import (
    _calib_standard_path, _load_image, _open_calib_panel)
from xrd_toolkit.gui.calib_scale import _reindex_by_scale
from xrd_toolkit.gui.panel_state import _log
from xrd_toolkit.gui.tasks import BackgroundTask, user_error_text
from xrd_toolkit.services.calibrants import (
    DEFAULT_CALIBRANT, calibrant_short_label)
from xrd_toolkit.services.integrator import (
    calibrate_geometry, refine_geometry_from_points)


# ══ 后台任务：自动 / 手动（_spawn 同款守卫）═══════════════════
def _auto_calib_worker(path_str: str, geom: dict,
                       calibrant: str = DEFAULT_CALIBRANT,
                       n_rings: int = 16) -> dict:
    """后台线程纯计算：读标样 → 自动定环心 → pyFAI 精修。

    环心先用取点拟合自动定位（fit_center_from_rings），失败再 FFT
    兜底（find_ring_center）。（2026-10-08 删 [再精修] 前这里还收一个
    给定的环心跳过定位——实测那条路与自动定位等价，就不留了。）

    结果附 beam_center_rc：(行, 列) 像素——这次新拟合的环心就是直射
    束落点 B（比沿用配置条目的旧 B 更准），[保存为配置] 用它入条目。

    图像已经在手，顺手附引擎指标（metrics / metrics_initial）——环位
    偏差量的是"精修后几何把理论环放到图像真环上了没有"，是用户在校
    准图上看得见的那件事。calibrant/n_rings 是本次会话选定的标样与环数。
    """
    image = _load_image(path_str)
    center = fit_center_from_rings(image)
    if center is None:
        cy, cx = find_ring_center(image)
    else:
        cy, cx = center["cy"], center["cx"]
    result = calibrate_geometry(
        image, pixel_size_m=geom["pixel_size_m"],
        wavelength_m=geom["wavelength_m"], dist0_m=geom["dist_m"],
        center0_px=(cx, cy), calibrant=calibrant, max_rings=n_rings)
    _attach_metrics(result, image, geom, initial=geom, calibrant=calibrant,
                    n_rings=n_rings)
    result["beam_center_rc"] = (cy, cx)
    return result

def _manual_calib_worker(path_str, points, rings, geom: dict,
                         center0_px: tuple,
                         calibrant: str = DEFAULT_CALIBRANT,
                         n_rings: int = 16) -> dict:
    """后台线程纯计算：用户点 → pyFAI refine2 单轮精修 → 附引擎指标。

    结果附 beam_center_rc：手动精修不动束心（初值 = 配置条目 B），
    保存时沿用初值 B（与旧模板机制一致）。

    path_str = 标样文件路径（window.calib_path）：手动链路本身只要点
    坐标，指标却需要图像本身，所以这里多读一次图（后台线程，读失败
    只让指标缺失，不影响校准结果）。

    判环可疑时（同一环号被点在明显不同的半径上）先按尺度重判一遍再拟合
    ——见 _reindex_by_scale（用户 2026-09-30："第 0 青环里套了两个真实的环，
    点它们都判成第 0 环"）。重判赢了就连**赢家那套的起点距离**一起用
    （dist0_m = 原距离 × 赢家尺度）：只换判环、起点还用原来那个错的，拟合
    会落回另一支更差的解（真数据实测 0.31 vs 7.55 px，2026-10-05）。
    """
    image = None
    load_error = None
    if path_str is not None:
        try:
            image = _load_image(path_str)
        except Exception as exc:                      # noqa: BLE001
            load_error = user_error_text(exc)
    rings, note, dist0_m = _reindex_by_scale(
        points, rings, geom, center0_px, image,
        calibrant=calibrant, n_rings=n_rings)
    result = refine_geometry_from_points(
        points, rings, pixel_size_m=geom["pixel_size_m"],
        wavelength_m=geom["wavelength_m"], dist0_m=dist0_m,
        center0_px=center0_px, calibrant=calibrant, max_rings=n_rings)
    result["beam_center_rc"] = (center0_px[1], center0_px[0])
    if note:
        result["reindex_note"] = note    # 主线程写进日志（不悄悄换判法）
    if path_str is not None:
        if image is None:
            result["metrics"] = None
            result["metrics_initial"] = None
            result["metrics_error"] = load_error
        else:
            _attach_metrics(result, image, geom, initial=geom,
                            calibrant=calibrant, n_rings=n_rings)
    return result

# ══ 启动入口：自动 / 手动 ═══════════════════════════════════════
def _start_auto_calib(window: QMainWindow) -> None:
    """[定位束心并精修]：从当前配置出发，自动定位环心（取点拟合，FFT
    兜底）→ pyFAI 精修。

    2026-10-08：删掉旧的 [再精修]（target="refined"，绕过定位、直接以
    当前配置的束心与距离为初值再精修一轮）——真 lab6 实测两者等价：
    环位偏差 0.24 vs 0.23 px（重复跑本身就抖 0.014~0.029 px），连按
    两个按钮结果不变，两个入口收敛成一个。
    """
    path = _calib_standard_path(window)
    if path is None:
        _log(window, "请先在文件列表勾选标样文件")
        return
    _open_calib_panel(window, path)   # 面板关了/没开过：重开
    if getattr(window, "calib_dock", None) is None:
        return   # 图像读取失败（_open_calib_panel 已记日志）
    _warn_pixel_unchecked(window)     # 没核对像素只提醒，不拦（2026-10-05）
    geom = dict(_calib_state(window).get("current_geom") or {})
    gen = window.calib_gen
    key = "calib_auto"
    task = None

    def done(result):
        window._tasks.remove(task)
        if window._latest_task.get(key) is not task:
            return   # 已有更新的任务：旧结果静默
        del window._latest_task[key]
        if gen != getattr(window, "calib_gen", -1) \
                or getattr(window, "calib_dock", None) is None:
            return   # 面板关过/退出过模式：迟到结果作废
        from xrd_toolkit.gui.calib import _on_calib_result  # 破循环
        _on_calib_result(window, "auto", result)

    def error(msg):
        window._tasks.remove(task)
        if window._latest_task.get(key) is not task:
            return
        del window._latest_task[key]
        if gen != getattr(window, "calib_gen", -1) \
                or getattr(window, "calib_dock", None) is None:
            return
        _log(window, f"校准失败（定位束心并精修）：{msg}")

    state = _calib_state(window)
    calibrant = state.get("calibrant") or DEFAULT_CALIBRANT
    n_rings = int(state.get("n_rings") or 16)
    task = BackgroundTask(_auto_calib_worker, str(path), geom, calibrant,
                          n_rings, on_done=done, on_error=error)
    window._latest_task[key] = task
    window._tasks.append(task)
    _log(window, f"开始定位束心并精修：{path.name}（标样 "
                 f"{calibrant_short_label(calibrant)}，{n_rings} 环；后台运行）")
    task.start()

def _start_manual_calib(window: QMainWindow) -> None:
    """[用选点精修]：用户点后台精修（初值 = 当前配置；环心 = 分析条目束心）。"""
    state = _calib_state(window)
    _pts = state["points"]
    if len(_pts) < MIN_POINTS or len({p[2] for p in _pts}) < MIN_RINGS:
        _log(window, f"手动校准至少需要 {MIN_POINTS} 个点、覆盖 {MIN_RINGS} 个"
                     f"不同的环——现在是 {len(_pts)} 个点 / "
                     f"{len({p[2] for p in _pts})} 个环；都挤在同一个环号上时，"
                     f"右键选中那个点，把环号改掉")
        return
    _warn_pixel_unchecked(window)     # 没核对像素只提醒，不拦（2026-10-05）
    geom = dict(state.get("current_geom") or {})
    beam = window.config["beam_center"]   # (行, 列) = 直射束落点 B
    center0_px = (beam[1], beam[0])       # (列, 行) 换序
    # 快照当前选点（任务运行中点列表可能被撤销/清空，不影响本次计算）
    points = [(float(p[0]), float(p[1])) for p in state["points"]]
    rings = [int(p[2]) for p in state["points"]]
    state["manual_n"] = len(points)   # 完成回调里按点数提示可信度
    gen = window.calib_gen
    key = "calib_manual"
    task = None

    def done(result):
        window._tasks.remove(task)
        if window._latest_task.get(key) is not task:
            return
        del window._latest_task[key]
        if gen != getattr(window, "calib_gen", -1) \
                or getattr(window, "calib_dock", None) is None:
            return
        from xrd_toolkit.gui.calib import _on_calib_result  # 破循环
        _on_calib_result(window, "manual", result)

    def error(msg):
        window._tasks.remove(task)
        if window._latest_task.get(key) is not task:
            return
        del window._latest_task[key]
        if gen != getattr(window, "calib_gen", -1) \
                or getattr(window, "calib_dock", None) is None:
            return
        _log(window, f"校准失败（手动选点）：{msg}")

    path = getattr(window, "calib_path", None)   # 指标要图像；没面板时为 None
    calibrant = state.get("calibrant") or DEFAULT_CALIBRANT
    n_rings = int(state.get("n_rings") or 16)
    task = BackgroundTask(_manual_calib_worker, str(path) if path else None,
                          points, rings, geom, center0_px, calibrant,
                          n_rings, on_done=done, on_error=error)
    window._latest_task[key] = task
    window._tasks.append(task)
    _log(window, f"开始手动校准（{len(points)} 个点，标样 "
                 f"{calibrant_short_label(calibrant)}，{n_rings} 环；后台运行）")
    task.start()
