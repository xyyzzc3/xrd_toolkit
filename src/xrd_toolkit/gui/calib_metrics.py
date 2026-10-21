"""校准指标：环位偏差 / 完整度 / a 离散（结果日志后缀）+ 当前配置异步指标。

2026-10-22 从 calib.py 原样搬出（只改结构，不动逻辑）。
calib 再导出；对 _calib_sync 直接导入，对 _load_image / _geom_px_keys 取面板层同名函数。
"""
from PySide6.QtWidgets import QMainWindow

import numpy as np

from xrd_toolkit.gui.calib_form import _calib_sync
from xrd_toolkit.gui.calib_model import _calib_state
from xrd_toolkit.gui.calib_panel import _geom_px_keys, _load_image
from xrd_toolkit.gui.tasks import BackgroundTask, user_error_text
from xrd_toolkit.services.calibrants import DEFAULT_CALIBRANT
from xrd_toolkit.services.ring_metrics import ring_metrics


def _metrics_worker(path_str: str, geom: dict,
                    calibrant: str, n_rings: int) -> dict:
    """后台线程：算一份几何的指标（当前配置的环位偏差专用）。

    `_attach_metrics(result, ...)` 的第一个参数就是**被量的那份几何**，
    所以这里用几何本身当载体（空 dict 会 KeyError: 'dist_m'——踩过）。
    """
    image = _load_image(path_str)
    out = dict(geom)
    _attach_metrics(out, image, geom, calibrant=calibrant,
                    n_rings=n_rings)      # initial=None：不重复算初值那一份
    return out

def _stack_custom_metrics(state: dict) -> None:
    """把当前配置的指标**也写进最后那条"自定义"结果**。

    为什么需要（用户 2026-09-28 第 1 条的后半截）：手输几何现在会进结果
    列表，用户就能把它放进 A / B 槽去跟自动/手动结果比。但指标是异步算
    出来、记在 `state["current_metrics"]` 里的（那是"当前配置"那一列的
    数据源），结果条目自带的那份还是 None——不补这一笔，放进槽里就显示
    "无可用环位偏差、判不了"，等于选了个哑巴。

    只在"当前配置就是那条手输几何"时补（`slots["current"]` 为空 + custom
    = True）：一旦采纳了别的结果，当前配置与那条自定义结果就不是一回事了。
    """
    if state.get("slots", {}).get("current") is not None \
            or not state.get("custom"):
        return
    for item in reversed(state.get("results") or []):
        if item.get("kind") == "custom":
            item["result"]["metrics"] = state.get("current_metrics")
            item["result"]["metrics_error"] = state.get("current_error")
            return

def _refresh_current_metrics(window: QMainWindow) -> None:
    """异步算当前配置的环位偏差（换条目 / 手改之后要重算）。

    算不出来（没开面板、图读不到、几何离谱）只让那一格显示"—"，绝不阻塞
    界面、也不动别的槽。
    """
    state = _calib_state(window)
    path = getattr(window, "calib_path", None)
    geom = state.get("current_geom")
    if path is None or geom is None:
        return
    gen = getattr(window, "calib_gen", 0)
    key = "calib_metrics"
    task = None

    def done(result):
        window._tasks.remove(task)
        if window._latest_task.get(key) is not task:
            return
        del window._latest_task[key]
        if gen != getattr(window, "calib_gen", -1):
            return
        state["current_metrics"] = result.get("metrics")
        state["current_error"] = result.get("metrics_error")
        _stack_custom_metrics(state)
        _calib_sync(window)

    def error(msg):
        window._tasks.remove(task)
        window._latest_task.pop(key, None)
        if gen != getattr(window, "calib_gen", -1):
            return
        state["current_metrics"] = None
        state["current_error"] = msg
        _stack_custom_metrics(state)
        _calib_sync(window)

    task = BackgroundTask(_metrics_worker, str(path), dict(geom),
                          state.get("calibrant") or DEFAULT_CALIBRANT,
                          int(state.get("n_rings") or 16),
                          on_done=done, on_error=error)
    window._latest_task[key] = task
    window._tasks.append(task)
    task.start()

# ══ 引擎指标：环位偏差 / 完整度 / a 离散（结果日志后缀）══════════
def _attach_metrics(result: dict, image, geom: dict,
                    initial: dict = None, calibrant=DEFAULT_CALIBRANT,
                    n_rings: int = 16) -> None:
    """给校准结果就地附引擎指标，**绝不抛出**（失败记 metrics_error）。

    geom / initial 都是 _collect_geometry 形状的字典（initial = 预精修
    的初值几何，米制 PONI）；result 是引擎结果（px 键，**不含**像素
    尺寸与波长——它们不参与拟合）。指标要的像素尺寸/波长一律取自
    geom，距离/PONI/倾斜角取自被评估的那一份；calibrant/n_rings 决定
    "理论环"是哪一套（标样只影响指标，不影响几何）。

    写三个键：
      metrics          结果几何下的指标（ring_metrics 的输出）
      metrics_initial  初值几何下的指标；没给初值时 None——两者并排才
                       看得出"精修到底把环往图像的真环上挪了多少像素"
      metrics_error    失败原因（成功时不写这个键）

    设计：指标是**显示器**，不是校准本身。算不出来（几何键缺失、几何
    离谱、图像太小、引擎内部异常）时校准结果照常有效，日志如实说明——
    静默失败会让用户以为"指标说没问题"。
    """
    def _one(src: dict) -> dict:
        # 米制面板几何先转 px 键（转换也在守卫内：残缺字典不许穿透）
        g = _geom_px_keys(src) if "poni1_m" in src else src
        return ring_metrics(
            image, pixel_size_m=geom["pixel_size_m"],
            wavelength_m=geom["wavelength_m"], dist_m=g["dist_m"],
            poni1_px=g["poni1_px"], poni2_px=g["poni2_px"],
            rot1_deg=g["rot1_deg"], rot2_deg=g["rot2_deg"],
            calibrant=calibrant, n_rings=n_rings)

    result["metrics"] = None
    result["metrics_initial"] = None
    for key, src in (("metrics", result), ("metrics_initial", initial)):
        if src is None:
            continue
        try:
            result[key] = _one(src)      # 引擎结果本来不带指标，只管覆盖
        except Exception as exc:                      # noqa: BLE001
            result[key] = None
            # 措辞走统一出口：内部错误说明"不是你操作的问题"（2026-10-05）
            result["metrics_error"] = user_error_text(exc)

def _metrics_note(result: dict) -> str:
    """结果日志的中文指标后缀（没指标时尽量说明原因，不静默）。

    措辞约定：几何离谱时宁可说"无可用环信号 + 贴窗边比例"——**不用
    _warn_rings_off_image 那句"全部落在图像外"**，那句有守卫测试在数
    出现次数，混用会让计数含义变糊。
    """
    if result.get("metrics_error"):
        return f"｜指标不可用：{result['metrics_error']}"
    m = result.get("metrics")
    if m is None:
        return ""
    n = len(m["rings"])
    if not np.isfinite(m["dev_px"]):
        # clip_frac 也可能无值（连搜索窗都放不进图像）——不能给用户看 nan%
        clip = m["clip_frac"]
        clip_txt = (f"峰值贴搜索窗边界 {clip:.0%}" if np.isfinite(clip)
                    else "搜索窗在图像内放不下")
        return (f"｜无可用环信号（{clip_txt}、完整环 {m['n_complete']}/{n}）")
    a = m["a"]
    a_txt = (f"标样 d 值离散度 {a['spread_ppm']:.0f} ppm"
             if np.isfinite(a["spread_ppm"]) else "标样 d 值离散度 —")
    init = result.get("metrics_initial")
    init_txt = (f"（初值 {init['dev_px']:.2f} px）"
                if init is not None and np.isfinite(init["dev_px"]) else "")
    return (f"｜环位偏差中位 {m['dev_px']:.2f} px{init_txt}、"
            f"完整环 {m['n_complete']}/{n}、{a_txt}")
