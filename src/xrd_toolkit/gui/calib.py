"""校准工作台：自动校准 + 手动选点校准（参数坞第 2 页 + 中央校准图面板）。

模块图见 app.py docstring（本模块与 plot_views.py 并列，只依赖下层
panels / panel_state / tasks 与服务层引擎，单向无环）。

两种模式（按数据质量任选一种，也可都跑、结果三列并列对比）：
  - 自动校准 [开始自动校准]：后台线程 fit_center_from_rings 自动定
    环心（失败 find_ring_center 兜底）→ calibrate_geometry pyFAI 迭代
    精修，输出距离/束心/倾斜角/残余误差；
  - 手动选点校准：中央校准图上点击至少 3 个点、覆盖 ≥2 个不同衍射
    环，点自动吸附最近理论环（snap_ring，容差 0.5°），
    [开始手动校准] 交给 refine_geometry_from_points 反推几何。

校准完成后**引擎指标**（services/ring_metrics：环位偏差中位 px、完整
环数、a 离散 ppm）附在结果 dict 上并写进日志后缀。它量的是"几何有没
有把理论环放到图像的真环上"，与 pyFAI 的收敛残差互补——后者只说明
迭代自洽（两种解分支都能报 0.004°），不说明环对不对。指标算不出来时
只记 metrics_error、日志如实说明，不影响校准结果本身。

三条来源（自动定位 / 手动选点 / 二次精修）各出一个结果，**"当前使用"
由"谁好用谁"决定**：新来源的环位偏差比当前的好 ≥ SOURCE_IMPROVE_MIN_PX
才替换（用户手动指定过的则永不自动替换，标成"自定义"）。画图与
[保存为配置] 都取"当前使用"那一份。

界面分工：
  - 参数坞第 0 页 = _build_calib_form（自上而下：配置条目操作区 +
    结果三列区 自动|手动|Δ偏差 + 自动/手动两个动作区）。各来源共用
    同一结果区与保存机制："当前使用"的结果可直接存成命名用户条目
    （config_user.json，不进 git），保存后坞顶"几何配置"下拉框
    立即出现并自动选中（几何填进参数坞）。内置 config.py 注册表
    仍走 CLI 模板人工登记（见 config.py 文件头）。
  - 中央校准图面板 = _CalibSubWindow（MDI 子窗口，imshow + 理论环
    路径 + 控制点/用户点标记 + [看环全貌] 视野开关 + [放大镜]，接
    鼠标点击（选点）与放大镜手势）。理论环由 theoretical_ring_paths
    精确反解（倾斜时是椭圆、圆心是直射束落点），不用"圆心 + 半径"
    的正圆近似——后者会整体偏 8~23 px。视野默认锁在图像那一框
    （图像是唯一不动的参照系，环跑到框外由红字说明）；放大镜点亮后
    可框选放大 / 滚轮缩放（2026-10-13 用户："校准图添加放大功能"），
    [看环全貌] 照旧是复位/看全部环的开关。不进 plot_docks：不掺和
    编辑对象焦点、平铺、总缩放；无抓手（v1 从简）。

状态（都挂在 window 上）：
  calib_dock / calib_key / calib_path / calib_display / calib_canvas
  / calib_ax   面板与画布（None = 没开面板）
  calib_gen    代计数：面板关闭/重开/退出模式时 +1，在飞任务回调
               核对代数，迟到结果静默作废
  calib_state  选点 + 累积结果 + 三个槽（懒创建）——关闭面板即全清
                （关闭即遗忘）；各键说明见 calib_model._calib_state
"""
import re
from datetime import datetime
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.colors import LogNorm
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMdiSubWindow, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget)

# 常量都住在 calib_model（纯逻辑层）。这里把面板/表格用的几个也一并
# 导入，保持『经 gui_calib 取用』的历史引用可用（同 app.py 的再导出惯例）。
from xrd_toolkit.gui.calib_model import (
    CALIB_PANEL_RESERVE_PX, COMPARE_HINT, COMPARE_ROWS, CP_COLOR,
    KIND_LABELS, MIN_POINTS, MIN_RINGS, PANEL_SCALE, RING_COLOR,
    SLOT_LABELS, SNAP_TOL_DEG,
    SOURCE_IMPROVE_MIN_PX, _add_custom_result, _add_result, _adopt_decision,
    _calib_state, _clear_calib_session, _fill_slot, _geom_to_result_shape,
    _next_name, _result_by_name, _result_dev, _result_to_geom, _slot_label,
    _verdict)
from xrd_toolkit.gui.calib_table import (
    _on_slot_changed, _refresh_table, _sync_slot_combos)
from xrd_toolkit.gui.calib_panel import (
    _CalibSubWindow, _apply_selected_ring, _calib_image,
    _calib_standard_path, _clear_calib_points, _close_calib_panel,
    _geom_px_keys, _load_image, _open_calib_panel, _redraw_calib,
    _redraw_calib_if_open, _undo_calib_point, _warn_rings_off_image)
# 标样辅助（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.calib_calibrant import (
    _adopt_result, _borrow_entry, _current_geom_text, _ensure_current,
    _init_calibrant_for_path, _on_calibrant_changed, _on_ring_count_changed,
    _pixel_ok, _set_pixel_ok, _sync_calibrant_widgets, _update_ring_count,
    _warn_pixel_unchecked)
# 校准表单（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.calib_form import (
    _build_calib_form, _calib_sync, _edit_current, _reset_calib_form)
from xrd_toolkit.gui.config_ops import (
    _delete_config, _import_poni, _save_calib_config, _save_poni,
    _suggest_config_key, _sync_del_config_btn)

from xrd_toolkit import config
from xrd_toolkit.core.processor import find_ring_center, fit_center_from_rings
from xrd_toolkit.gui.panels import _settle
from xrd_toolkit.gui.panel_state import (
    _auto_contrast_values, _collect_geometry, _log, _reload_config_combo)
from xrd_toolkit.gui.tasks import BackgroundTask, user_error_text
from xrd_toolkit.services.calibrants import (
    COMMON_CALIBRANTS, DEFAULT_CALIBRANT, MAX_GUI_RINGS,
    calibrant_label, calibrant_short_label, calibrant_tooltip,
    complete_ring_count, detect_calibrant_from_stem, ring_total)
from xrd_toolkit.services.integrator import (
    calibrate_geometry, refine_geometry_from_points, snap_ring)
from xrd_toolkit.services.ring_metrics import ring_metrics

# ── 结果列表与三个槽 ────────────────────────────────────────

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

# ══ 中央校准图面板 ═══════════════════════════════════════════

# ══ 手动选点：点击判环 / 撤销 / 清空 ══════════════════════════

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

# 判环可疑时按哪些距离尺度重试（见 _reindex_by_scale）。距离是这套几何里
# **整体缩放环位置**的那个量：它错一个比例，低角侧的真实环就会被压到"第 0
# 环"以下、判环全挤在一起。
_MANUAL_SCALE_PROBE = (0.85, 0.90, 0.95, 1.0, 1.05, 1.10, 1.20)
# 同一环号被点到的两个半径差超过这个比例 = 判环挤在一起了
# （同一个环上点两次半径只差几个百分点，不会误报）
_SUSPICIOUS_RADIUS_RATIO = 1.15

def _ring_radius(p, center_px) -> float:
    """点到束心的半径（px）——判"同一个环号是不是横跨了两个半径"。"""
    return float(np.hypot(float(p[0]) - float(center_px[0]),
                          float(p[1]) - float(center_px[1])))

def _rings_look_suspicious(points, rings, center_px) -> bool:
    """同一个环号被点在明显不同的半径上 → 判环很可能挤在一起了。

    用户 2026-09-30 报的正是这个：第 0 青环里套了两个真实环，点它们都判成
    环 0（半径差一倍）。这是"几何尺度偏了"的火警，触发下面的尺度扫描。
    """
    by_ring = {}
    for p, k in zip(points, rings):
        by_ring.setdefault(int(k), []).append(_ring_radius(p, center_px))
    for radii in by_ring.values():
        if len(radii) >= 2 \
                and max(radii) / max(min(radii), 1e-9) > _SUSPICIOUS_RADIUS_RATIO:
            return True
    return False

def _rings_at_scale(points, geom, center_px, scale,
                    calibrant=DEFAULT_CALIBRANT, n_rings: int = 16) -> list:
    """把距离乘 scale 后重新判环；有任何一点判不到环就返回空表（那套不能用）。

    geom 的形状约定与 _attach_metrics 相同（GUI 交出来的是米制键
    poni1_m/poni2_m），判环要的却是 px 键——先归一化再读，两种形状都收。
    别忘了这一步（2026-10-05 踩过：只认 px 键 → 手动校准一触发尺度扫描
    就 KeyError: 'poni1_px'，正好把这个兜底功能该干活的时候打死了）。
    """
    g = _geom_px_keys(geom) if "poni1_m" in geom else dict(geom)
    g["dist_m"] = float(geom["dist_m"]) * float(scale)
    px = float(g["pixel_size_m"])
    out = []
    for x, y in points:
        k = snap_ring(
            float(x), float(y), pixel_size_m=px,
            wavelength_m=float(g["wavelength_m"]), dist_m=float(g["dist_m"]),
            poni1_m=float(g["poni1_px"]) * px, poni2_m=float(g["poni2_px"]) * px,
            rot1_deg=float(g.get("rot1_deg") or 0.0),
            rot2_deg=float(g.get("rot2_deg") or 0.0), tol_deg=SNAP_TOL_DEG,
            calibrant=calibrant, max_rings=n_rings)
        if k is None:
            return []
        out.append(int(k))
    return out

def _reindex_by_scale(points, rings, geom, center0_px, image,
                      calibrant=DEFAULT_CALIBRANT, n_rings: int = 16):
    """判环可疑时：扫几个尺度重判环、各拟合一次，挑**环位偏差最小**的那套。

    返回 (rings, 说明, dist0_m)；不需要扫 / 扫不动时返回 (rings, "", 原距离)
    ——不猜、不动。换了判环就**连赢家那套的起点距离一起交出来**：只换判环、
    起点还用原来那个错的，重拟合会从错的初值出发落回另一支更差的解——真数据
    实测 0.31 px vs 7.55 px，日志写的和最终拿到的对不上（2026-10-05 查修）。

    为什么扫尺度：判环是"拿当前几何把点击点换算成 2θ、再找最近的理论环"，
    而低角侧没有更低的环可判——距离错一个比例时最里面几个真实环全被压到
    第 0 环。距离是唯一整体缩放环位置的量，所以按比例扫它、每个候选重新判环
    再拟合，用 16 个环的环位偏差挑解（单看 2~3 个点会过拟合，指标才有分辨力）。

    门槛：只有比原尺度（1.0）好 **0.05 px 以上**才换——与"当前配置要不要
    换"同一个噪声口径（calib_model.SOURCE_IMPROVE_MIN_PX），免得在噪声里跳。
    """
    if image is None or not _rings_look_suspicious(points, rings, center0_px):
        return rings, "", float(geom["dist_m"])

    def _fit(scale, screen=True):
        cand = _rings_at_scale(points, geom, center0_px, scale,
                               calibrant=calibrant, n_rings=n_rings)
        if not cand:
            return None
        if screen and _rings_look_suspicious(points, cand, center0_px):
            # 便宜的先筛一遍（几步判环，~10 ms）：环号还是挤在一起的那套
            # 不可能对，别花 200 ms 去拟合它（用户 2026-10-01："按优化来"）
            return None
        try:
            res = refine_geometry_from_points(
                points, cand, pixel_size_m=float(geom["pixel_size_m"]),
                wavelength_m=float(geom["wavelength_m"]),
                dist0_m=float(geom["dist_m"]) * float(scale),
                center0_px=center0_px, calibrant=calibrant,
                max_rings=n_rings)
        except Exception:                              # noqa: BLE001
            return None
        _attach_metrics(res, image, geom, initial=geom, calibrant=calibrant,
                        n_rings=n_rings)
        dev = _result_dev(res)
        return None if dev is None else (dev, scale, cand)

    # 基准（1.0）**不筛**：它就是"原判法"，得量出它的环位偏差当比较基准
    base = _fit(1.0, screen=False)
    if base is None:
        return rings, "", float(geom["dist_m"])   # 指标算不出来 = 没有判据：老实地不动
    best = base
    for s in _MANUAL_SCALE_PROBE:
        if abs(s - 1.0) < 1e-9:
            continue
        got = _fit(s)
        if got is not None and got[0] <= best[0] - SOURCE_IMPROVE_MIN_PX:
            best = got
    if best[1] == base[1]:
        return rings, "", float(geom["dist_m"])
    dev, scale, cand = best
    return cand, (f"判环可疑（同一环号横跨了两个半径）：按距离尺度 {scale:g} 重判"
                  f"更合理——环位偏差 {dev:.2f} px，原判法 {base[0]:.2f} px"), \
        float(geom["dist_m"]) * float(scale)

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

# ══ 结果 / Δ / 保存为配置 ════════════════════════════════════
def _on_calib_result(window: QMainWindow, kind: str, result: dict) -> None:
    """一次校准结果落地（主线程）：进累积列表 → 判要不要换当前配置 → 填槽。

    顺序有讲究：先把结果挂进列表（这样日志与槽都有名字可用），再判
    "拟合得好不好"（_adopt_decision），采纳就换当前配置并重画青线，最后
    按 A→B 轮换填没被钉住的槽。
    """
    state = _calib_state(window)
    got_cal = result.get("calibrant")
    if got_cal and got_cal != state.get("calibrant"):
        # 任务在飞时换过标样：这是一份旧标样的结果，进列表只会误导
        _log(window, f"{KIND_LABELS.get(kind, kind)}结果作废：标样已从 "
                     f"{calibrant_short_label(got_cal)} 切到 "
                     f"{calibrant_short_label(state.get('calibrant'))}")
        return
    name = _add_result(state, kind, result)
    label = KIND_LABELS.get(kind, kind)
    if result.get("reindex_note"):
        # 判环换过一套（尺度扫描赢的）：先写这句，下面的结果才读得懂
        _log(window, result["reindex_note"])
    cal_txt = calibrant_short_label(got_cal or state.get("calibrant")
                                    or DEFAULT_CALIBRANT)
    rings_txt = (f"{result['n_rings']} 环"
                 if result.get("n_rings") else "")
    _log(window, f"{label}完成（{name}，{cal_txt}{'，' + rings_txt if rings_txt else ''}）："
                 f"距离 {result['dist_m'] * 1000:.2f} mm，"
                 f"PONI ({result['poni1_px']:.2f}, {result['poni2_px']:.2f}) px，"
                 f"残差 {result['residual_deg']:.4f}°"
                 f"{_metrics_note(result)}")
    take, note = _adopt_decision(state, name)
    if take:
        # 一律采纳（用户 2026-09-30："只要是用户操作的……都填入当前，
        # 让用户看到变化"）——青环立刻跟着动，说明里写明好了/差了多少
        _adopt_result(window, name, why="自动采纳")
    _log(window, note)
    _log(window, _fill_slot(state, name))
    _calib_sync(window)
    if kind == "manual" and state.get("manual_n", 99) < 6:
        # 点数少时最小二乘对单点点击误差敏感，残差小也不代表可信
        _log(window, "提示：点数较少（<6）时手动结果可能不稳，建议多点"
                     "几个环上的点再跑一次，以环位偏差判断可信度")

# ══ 几何配置条目：加载 .poni / 保存 .poni / 删除 ═══════════════
# （从 app.py 搬来：几何配置的增删改查归校准页——分析页只读，下拉框
# 选条目；按钮的工作对象仍是"分析页当前选中的那一条"。）

# ══ 模式进出（app._on_mode 调用）══════════════════════════════
def _default_config_name(window: QMainWindow) -> str:
    """「条目名称」的预填值：标样文件名去掉末尾的「-数字」段。

    用户 2026-10-07："预填文件名的不含-后的长数字部分"——lab6-00024.tif
    → lab6；LMFP_1_atten0-00029.tif → LMFP_1_atten0。没有标样（只在
    校准页里借条目 / 手输几何）时退回按当前条目递推（lmfp1_lab6 →
    lmfp2_lab6，见 config_ops._suggest_config_key）。
    """
    path = getattr(window, "calib_path", None)
    if path:
        stem = Path(path).stem
        cleaned = re.sub(r"-\d+$", "", stem)
        return cleaned or stem
    return _suggest_config_key(config.DEFAULT_CONFIG)

def _refresh_config_name_prefill(window: QMainWindow) -> None:
    """进入校准时把「条目名称」按当前标样文件名重填。

    只覆盖"还挂着上一次预填值"的框（或空框）——用户自己改过的名字不动。
    """
    box = getattr(window, "calib_name_edit", None)
    if box is None:
        return
    if box.text().strip() not in ("", getattr(window, "_calib_name_auto", None)):
        return   # 用户改过：不动
    box.setText(_default_config_name(window))
    window._calib_name_auto = box.text()

def _enter_calib(window: QMainWindow) -> None:
    """进入校准模式（[校准] 按下）：开校准面板 + 按校准页内容拉宽参数坞。

    没勾文件只记日志提示（不崩）——用户勾好文件后点 [定位束心并精修]
    也能开面板。宽度只在够得着时拉：给绘图区留 CALIB_PANEL_RESERVE_PX
    （点环选点是在图上做的，坞太宽就没法点了）。
    """
    # 先让参数坞露出来再量宽：开局它是收起的（_clear_entrance），
    # 隐藏时量到的是旧值/默认值 → 拉宽和"退出还原"都会拿假数字
    # （[校准] 的 toggled 先于 clicked 触发，所以不能指望入口那边先
    # 露坞——2026-09-26 实测：以前量出来 327 px，远没到该有的宽度）
    window.param_dock.setVisible(True)
    # 进校准露坞同理记意图（见 app._switch_entrance 的同款记账）：不记的话
    # 意图还停在开局的 False，窗口状态一变（全屏/最小化恢复）就会被
    # _resync_docks 收回去
    if hasattr(window, "_dock_intent"):
        window._dock_intent["参数"] = True
    # 出口按钮（坞顶那一行）：本模式的显式退出口，进来就亮出来。
    # 放在"没勾文件"的提前返回之前——没文件也要能退出去
    window.calib_exit_btn.setVisible(True)
    path = _calib_standard_path(window)
    if path is None:
        _log(window, "请先在文件列表勾选标样文件")
        return
    _open_calib_panel(window, path)
    _refresh_config_name_prefill(window)   # 「条目名称」跟着这个标样重填
    _widen_dock_for_calib(window)

def _exit_calib(window: QMainWindow) -> None:
    """退出校准模式：关校准面板（关闭即遗忘）+ 参数坞宽度还原。"""
    window.calib_exit_btn.setVisible(False)
    _close_calib_panel(window)
    _restore_dock_width(window)

def _widen_dock_for_calib(window: QMainWindow) -> None:
    """校准模式下把参数坞拉宽到"放下校准页所有内容"（不超过窗口上限）。"""
    dock = getattr(window, "param_dock", None)
    page = getattr(window, "calib_scroll", None)
    if dock is None or page is None:
        return
    if getattr(window, "_dock_w_before", None) is None:
        # 记住分析模式的宽度。参数坞开局是收起的（app._clear_entrance，
        # 用户 2026-09-25 定），点 [校准] 时才刚由隐藏转可见、还没走
        # 布局，此刻 width() 是旧值/默认值 → 先手动走一遍布局再量，
        # 否则"退出校准还原宽度"会还原成一个假数字
        lay = window.layout()
        if lay is not None:
            lay.activate()
        window._dock_w_before = dock.width()
    need = page.widget().sizeHint().width() + 24  # 滚动区边框/条余量
    limit = max(360, window.width() - CALIB_PANEL_RESERVE_PX)
    target = int(min(need, limit))
    if dock.isFloating():
        dock.resize(target, dock.height())
    else:
        window.resizeDocks([dock], [target], Qt.Horizontal)
    if need > limit:
        _log(window, f"校准页需要 {need} px，但为保住绘图区（点环用）只给到 "
                     f"{target} px：窄窗口下校准页里可以横向滚动")

def _restore_dock_width(window: QMainWindow) -> None:
    """出校准模式：参数坞宽度还原成进之前那一条。"""
    was = getattr(window, "_dock_w_before", None)
    dock = getattr(window, "param_dock", None)
    if was is None or dock is None:
        return
    if dock.isFloating():
        dock.resize(was, dock.height())
    else:
        window.resizeDocks([dock], [was], Qt.Horizontal)
    window._dock_w_before = None
