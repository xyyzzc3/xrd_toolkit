"""一键分析报告：GUI 侧的收集与导出流程（2026-11-08）。

分工：
  * `services/analysis_report.py` 是纯生成器（快照 → 自包含 HTML）；
  * 本模块只做"从当前会话读快照 + 弹窗 + 写文件"，不重新积分、不重跑
    拟合。1D 曲线与处理链走 `panel_state._proc_curve`（与屏幕同一个口径），
    峰表按该面板的寻峰参数重算（与 [显示峰位] 同一套参数），单峰拟合与
    W-H 直接读面板上已经算好的结果；精修读 `refine_editor.result`，
    原位读 `insitu_editor.batch`（都只复制快照，不重跑）。

入口：工具栏 [报告]，默认导出到 `outputs/reports/`。
"""
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressDialog,
    QPushButton, QVBoxLayout, QWidget)

from xrd_toolkit import __version__, paths
from xrd_toolkit.gui.fit_view import BACKGROUND_LABELS, SHAPE_LABELS
from xrd_toolkit.gui.panel_state import (
    _collect_geometry, _log, _proc_curve, _proc_settings)
from xrd_toolkit.gui.peak_view import _settings as _peak_settings
from xrd_toolkit.gui.tasks import user_error_text
from xrd_toolkit.services import analysis_report as report
from xrd_toolkit.services import export as data_export
from xrd_toolkit.services import process
from xrd_toolkit.services.peak_finding import find_peaks_1d

# 与 analysis_report.SECTIONS 的键一一对应；顺序 = 弹窗里的勾选顺序
_SECTION_CHOICES = (
    ("curve", "1D 曲线"),
    ("peaks", "峰表"),
    ("fits", "单峰拟合"),
    ("wh", "W-H / Scherrer"),
    ("phases", "物相筛选"),
    ("refine", "全谱精修"),
    ("insitu", "原位三相批量"),
)


# ══ 可用的 1D 曲线 ═════════════════════════════════════════
def available_curves(window) -> list:
    """当前会话里已经算好的 1D 面板 → [(面板键, 标题), …]。

    焦点面板排第一（用户刚在看的那张通常就是要写进报告的），其余按
    标题排序。只认 `last_tth` 已经有的（没算完/没算过的不列）。
    """
    docks = getattr(window, "plot_docks", {}) or {}
    focus = getattr(window, "focus_panel", None)
    keys = sorted(docks)
    if focus in keys:
        keys.remove(focus)
        keys.insert(0, focus)
    out = []
    for key in keys:
        if not str(key).startswith("1D|"):
            continue
        dock = docks.get(key)
        if getattr(dock, "last_tth", None) is None:
            continue
        title = dock.windowTitle() or str(key).split("|", 1)[-1]
        out.append((key, title))
    return out


def _default_sample(title: str) -> str:
    """面板标题 → 默认样品名（去掉 1D_ 前缀与扩展名）。"""
    text = str(title or "")
    for prefix in ("1D_", "处理产物_"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    stem = Path(text).stem if text else ""
    return stem if stem and stem != "1D" else ""


def _finite_runs(tth, y):
    """把曲线按非有限点切成连续段（与 peak_view._finite_segments 同口径）。"""
    ok = np.isfinite(tth) & np.isfinite(y)
    start = None
    for i, good in enumerate(ok):
        if good and start is None:
            start = i
        elif not good and start is not None:
            if i - start >= 10:
                yield tth[start:i], y[start:i]
            start = None
    if start is not None and y.size - start >= 10:
        yield tth[start:], y[start:]


def _detect_peaks(window, dock, tth, y) -> list:
    """按该面板的寻峰参数在当前（处理后）曲线上找峰。

    与 [显示峰位] 的区别：不要求开关打开——报告要的是"当时这套参数会
    检出哪些峰"，不是"屏幕上有没有画标记"。参数仍从面板快照读
    （`_panel_param`），所以换面板/换设置不会串。
    """
    factor, distance, min_fwhm, max_peaks = _peak_settings(window, dock)
    found = []
    for seg_t, seg_y in _finite_runs(np.asarray(tth, dtype=float),
                                     np.asarray(y, dtype=float)):
        found.extend(find_peaks_1d(
            seg_t, seg_y, prominence_factor=factor,
            min_distance_deg=distance, min_fwhm_deg=min_fwhm,
            max_peaks=max_peaks))
    if len(found) > max_peaks:
        found = sorted(found, key=lambda p: -p.height)[:max_peaks]
    found.sort(key=lambda p: p.center)
    return [report.ReportPeak(
        center=float(peak.center), height=float(peak.height),
        fwhm=float(peak.fwhm), area=float(peak.area),
        prominence=float(peak.prominence)) for peak in found]


def _curve_sig(tth, y) -> tuple:
    """曲线指纹（与 fit_view._curve_sig 同式）：处理链一改就会变。"""
    arr = np.asarray(y, dtype=float)
    return (int(np.size(tth)), float(np.nansum(np.nan_to_num(arr, nan=0.0))))


def _wavelength(window, config_name=None):
    """这张面板对应配置的波长（Å）；配置条目读不到就返回 None。

    优先用面板快照里的配置名（这张图当时是用哪条几何算的），别拿当前
    下拉框的值顶替——用户换了配置之后，旧面板的波长仍是旧值。
    """
    name = config_name or getattr(window, "config_name", None)
    try:
        if name == getattr(window, "config_name", None):
            geom = _collect_geometry(window)
        else:
            from xrd_toolkit import config as xrd_config
            geom = xrd_config.CONFIGS[name]["geometry"]
        return float(geom["wavelength_m"]) * 1e10
    except (KeyError, TypeError, ValueError):
        return None


# ══ 精修 / 原位快照 ═══════════════════════════════════════
def _safe_float(value, default=float("nan")) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if np.isfinite(number) else default


def _bragg_rows(result) -> list:
    """FitResult 的布拉格峰位 → [(标签, 数组)]（主相 + 额外相）。"""
    rows = []
    peak_tth = getattr(result, "peak_tth", None)
    if peak_tth is not None and len(peak_tth):
        rows.append((str(getattr(result, "phase_label", "") or "主相"),
                     np.asarray(peak_tth, dtype=float).copy()))
    for label, pos in (getattr(result, "extra_peak_tth", None) or {}).items():
        rows.append((str(label), np.asarray(pos, dtype=float).copy()))
    return rows


def _profile_from_result(result, title):
    """FitResult → 报告里的全谱拟合图快照（只复制数组）。"""
    if result is None or getattr(result, "tth", None) is None:
        return None
    background = getattr(result, "background", None)
    return report.ReportProfileFit(
        tth=np.asarray(result.tth, dtype=float).copy(),
        y_obs=np.asarray(result.y_obs, dtype=float).copy(),
        y_calc=np.asarray(result.y_calc, dtype=float).copy(),
        residual=np.asarray(result.residual, dtype=float).copy(),
        rwp=_safe_float(getattr(result, "rwp", float("nan"))),
        rp=_safe_float(getattr(result, "rp", float("nan"))),
        title=str(title),
        background=(None if background is None
                    else np.asarray(background, dtype=float).copy()),
        bragg=_bragg_rows(result))


def _refine_snapshot(window):
    """精修工作台的当前结果 → ReportRefine；没结果返回 None。

    参数与方法优先读 `_fit_meta`（拟合开始那一刻的快照），避免用户拟合
    完又改了下拉框/勾选项，报告把"当时用的参数"写错。
    """
    editor = getattr(window, "refine_editor", None)
    result = getattr(editor, "result", None) if editor is not None else None
    if editor is None or result is None:
        return None
    meta = dict(getattr(editor, "_fit_meta", None) or {})
    cif = str(meta.get("cif") or editor.cif_combo.currentData() or "")
    phase = (str(getattr(result, "phase_label", "") or "")
             or (Path(cif).stem if cif else "主相"))

    params = []
    initial = getattr(result, "initial", None) or {}
    stderr = getattr(result, "stderr", None) or {}
    skip = {"scale", "size_mix", "strain_mix"}
    for name, value in (getattr(result, "params", None) or {}).items():
        if name in skip or name.startswith("intensity_a"):
            continue
        # 额外相只保留 scale（Al:scale）；逐反射强度/微结构不进表
        if ":" in name and not name.endswith(":scale"):
            continue
        params.append((str(name), _safe_float(initial.get(name)),
                       _safe_float(value), _safe_float(stderr.get(name))))

    weights = []
    for row in (getattr(result, "phase_weights", None) or []):
        weights.append(report.ReportPhaseWeight(
            label=str(row.label),
            weight_percent=_safe_float(row.weight_percent),
            scale=_safe_float(row.scale), z=_safe_float(row.z),
            molar_mass=_safe_float(row.molar_mass),
            volume=_safe_float(row.volume),
            source=str(getattr(row, "source", "structure")),
            warning=str(getattr(row, "warning", "") or "")))

    sites = []
    for label, values in (getattr(result, "site_coords", None) or {}).items():
        try:
            wyckoff, x, y, z = values
            sites.append((str(label), str(wyckoff), float(x), float(y),
                          float(z)))
        except (TypeError, ValueError):
            continue

    free = list(meta.get("free") or [])
    extra_paths = list(meta.get("extra_paths") or [])
    wavelength = _safe_float(meta.get("wavelength"))
    methods = [
        ("分析曲线", str(meta.get("curve_title")
                       or editor.curve_combo.currentText() or "—")),
        ("结构", Path(cif).name if cif else "—"),
        ("模式", ("Le Bail（强度自由）" if meta.get("lebail")
                  else "结构精修")),
        ("自由参数", "、".join(free) if free else "—"),
        ("背景", (f"自动基线 {float(meta.get('bg_window') or 0):g}°"
                  if meta.get("bg_mode") == "auto" else "无")),
        ("额外相", "、".join(Path(path).name for path in extra_paths)
         or "无"),
        ("额外相强度", ("逐反射自由（Le Bail）"
                       if meta.get("extra_lebail") else "结构因子")),
        ("仪器", ("固定 LaB₆ 基线" if meta.get("fixed")
                  else "精修 zero/U/V/W/X/Y")),
        ("波长", (f"{wavelength:.4f} Å"
                  if np.isfinite(wavelength) else "—")),
    ]
    title = f"Whole-pattern refinement — {phase}"
    return report.ReportRefine(
        phase_label=phase, cif=cif,
        rwp=_safe_float(getattr(result, "rwp", float("nan"))),
        rp=_safe_float(getattr(result, "rp", float("nan"))),
        reduced_chi2=_safe_float(getattr(result, "reduced_chi2",
                                         float("nan"))),
        n_obs=int(getattr(result, "n_obs", 0) or 0),
        n_params=int(getattr(result, "n_params", 0) or 0),
        success=bool(getattr(result, "success", True)),
        message=str(getattr(result, "message", "") or ""),
        lebail=bool(meta.get("lebail")),
        profile=_profile_from_result(result, title),
        params=params, weights=weights,
        weight_note=str(getattr(result, "phase_weight_note", "") or ""),
        site_coords=sites, methods=methods,
        note=str(getattr(editor, "_fit_result_note", "") or ""))


def _insitu_snapshot(window):
    """原位工作台的当前批次 → ReportInSitu；没跑过/没结果返回 None。"""
    editor = getattr(window, "insitu_editor", None)
    batch = getattr(editor, "batch", None) if editor is not None else None
    rows = [dict(row) for row in (getattr(batch, "rows", None) or [])]
    if editor is None or batch is None or not rows:
        return None
    meta = dict(getattr(editor, "_batch_meta", None) or {})
    phase_paths = dict(meta.get("phases")
                       or getattr(editor, "phase_paths", {}) or {})

    mask_path = str(meta.get("mask") or "")
    if not mask_path:
        current_mask = getattr(editor, "mask_path", None)
        mask_path = str(current_mask) if current_mask else ""
    mask_note = (f"已应用：{Path(mask_path).name}" if mask_path
                 else ("已应用" if getattr(batch, "mask", None) is not None
                       else "未使用"))

    methods = [
        ("数据目录", str(meta.get("data_dir")
                       or getattr(editor, "data_dir", "") or "—")),
        ("帧数", str(len(rows))),
        ("mask", mask_note),
    ]
    for key, label in (("rich", "富锂相"), ("poor", "贫锂相"),
                       ("al", "Al 参考相")):
        path = phase_paths.get(key)
        methods.append((label, Path(path).stem if path else "—"))
    lab6 = str(meta.get("lab6") or "")
    if lab6:
        methods.append(("LaB₆ 标样", Path(lab6).name))
    methods += [
        ("2θ 范围", f"{batch.tth_min:g}–{batch.tth_max:g}°"),
        ("输出点数", str(batch.npt)),
        ("最大 Rwp", f"{batch.max_rwp:g}%"),
        ("背景模型", str(batch.background_model)),
        ("强度修正阶数", str(batch.intensity_poly)),
        ("迭代上限", f"首帧 {batch.first_nfev} / 后续 {batch.frame_nfev}"),
        ("双起点", "每帧（上一帧种子 + CIF 端元）"),
    ]
    instrument = getattr(batch, "instrument", None)
    if instrument is not None:
        rwp_text = report._num(getattr(instrument, "rwp", float("nan")),
                               "{:.2f}")
        methods.append(("仪器标样", f"LaB₆ 精修 Rwp {rwp_text}%"))
        params = getattr(instrument, "params", None) or {}
        bits = [f"{name} {report._num(params.get(name), '{:.4g}')}"
                for name in ("zero", "u", "v", "w", "x", "y")
                if name in params]
        if bits:
            methods.append(("仪器参数", "、".join(bits)))

    frame_fit = None
    current = getattr(editor, "_fit_res", None)
    if current is not None:
        frame_no = getattr(editor, "_fit_frame", None)
        frame_file = Path(str(getattr(editor, "_fit_file", "") or "")).name
        frame_fit = _profile_from_result(
            current, f"In-situ frame {frame_no} — {frame_file}")
    else:
        candidates = [entry for entry in (getattr(batch, "fitted", None) or [])
                      if str(entry[4]) in ("ok", "ok_soft")
                      and entry[2] is not None]
        if candidates:
            entry = min(candidates, key=lambda item: _safe_float(
                getattr(item[2], "rwp", float("inf")), float("inf")))
            frame_no, path, result, _f_rich, _status = entry
            frame_fit = _profile_from_result(
                result, f"In-situ frame {frame_no} — {Path(path).name}")

    mi_rows = []
    fitted = list(getattr(batch, "fitted", None) or [])
    if len(fitted) >= 3:
        cached = getattr(editor, "_mi_rows", None)
        if cached:
            mi_rows = [dict(row) for row in cached]
        else:
            try:
                from xrd_toolkit.simulation.insitu_plots import (
                    model_independent_rows)
                mi_rows = model_independent_rows(fitted)
            except Exception as err:             # noqa: BLE001
                mi_rows = []
                methods.append(("模型无关检查",
                                f"未收录：{user_error_text(err)}"))

    return report.ReportInSitu(
        rows=rows, methods=methods, frame_fit=frame_fit, mi_rows=mi_rows)


# ══ 收集快照 ═════════════════════════════════════════════
def collect_report_snapshot(window, *, title, sample, curve_key,
                            sections) -> report.ReportSnapshot:
    """把窗口里的当前结果读成 ReportSnapshot（只读，不改任何状态）。

    `sections` 是用户勾的节（键见 `analysis_report.SECTIONS`）。没勾的节
    不收集；勾了但没有结果的节由生成器在报告里写"本次未做"。
    """
    sections = tuple(key for key in sections if key in report._ALL_KEYS)
    if not sections:
        raise ValueError("至少勾选一节")
    docks = getattr(window, "plot_docks", {}) or {}
    dock = docks.get(curve_key)
    if dock is None or getattr(dock, "last_tth", None) is None:
        raise ValueError("选中的 1D 曲线已经不在了（重新点 [1D] 出图再试）")

    tth = np.asarray(dock.last_tth, dtype=float)
    intensity = np.asarray(dock.last_intensity, dtype=float)
    path = getattr(dock, "panel_file", None)
    settings = _proc_settings(window, dock, path)
    proc_tth, proc_y, base = _proc_curve(
        window, dock, path, tth, intensity)
    mode = str(settings.get("mode") or "off")
    if base is None:
        # 背景模式开着但没生效（锚点没点/空扫没选）：照实说没处理
        settings = {**settings, "mode": "off"}
    chain = process.chain_desc(settings)
    if chain == "none":
        raw_tth = raw_y = None
    else:
        raw_tth, raw_y = tth, intensity

    name = getattr(dock, "windowTitle", lambda: "")() or _default_sample(
        curve_key)
    source_path = str(path) if path else str(curve_key).split("|", 1)[-1]
    snapshot_meta = getattr(dock, "params_snapshot", None) or {}
    config_name = str(snapshot_meta.get("config")
                      or getattr(window, "config_name", "") or "")
    curve = report.ReportCurve(
        name=name, tth=proc_tth, intensity=proc_y, path=source_path,
        raw_tth=raw_tth, raw_intensity=raw_y, chain=chain,
        config=config_name, wavelength=_wavelength(window, config_name))

    # 峰表：勾了才收集（图上的峰标记也跟着这个开关）
    if "peaks" in sections:
        curve.peaks = _detect_peaks(window, dock, proc_tth, proc_y)

    # 单峰拟合：直接读面板结果；曲线变了（重新积分/改处理参数）就作废，
    # 与 gui/fit_view.draw_fit_overlays 的判定同一个指纹。
    entries = list(getattr(dock, "fit_results", None) or [])
    need_fits = "fits" in sections or "wh" in sections
    stale = (bool(entries) and need_fits
             and getattr(dock, "fit_sig", None) != _curve_sig(
                 proc_tth, proc_y))
    if stale:
        entries = []
        curve.notes.append("曲线已变，旧的单峰拟合结果未计入报告")
    if need_fits:          # W-H 也需要这些拟合（方法表与摘要要用）
        for entry in entries:
            fit = getattr(entry, "fit", None)
            if fit is None:
                continue
            center0 = float(entry.center0)
            half = float(entry.half_window)
            mask = (proc_tth > center0 - half) & (proc_tth < center0 + half)
            window_tth = proc_tth[mask]
            background = np.asarray(fit.background, dtype=float)
            if window_tth.size != background.size:
                curve.notes.append(
                    f"峰 {center0:.4f}° 的拟合窗口与当前曲线对不上，已跳过")
                continue
            curve.fits.append(report.ReportFit(
                center0=center0, half_window=half,
                shape=str(getattr(fit, "shape", "")),
                center=float(fit.center), fwhm=float(fit.fwhm),
                fwhm_left=float(getattr(fit, "fwhm_left", float("nan"))),
                fwhm_right=float(getattr(fit, "fwhm_right", float("nan"))),
                eta=float(getattr(fit, "eta", float("nan"))),
                m=float(getattr(fit, "m", float("nan"))),
                height=float(fit.height), area=float(fit.area),
                rwp=float(fit.rwp), window_tth=window_tth,
                background=background, wh=bool(getattr(entry, "wh", True)),
                background_mode=str(getattr(entry, "background", "linear")),
                window_intensity=proc_y[mask]))

    # W-H：读面板上已经算好的结果（refresh_wh 在拟合/勾选变化时自动重算）
    result = getattr(dock, "wh_result", None)
    if "wh" in sections and result is not None and not stale:
        curve.wh = report.ReportWH(
            size_nm=float(result.size_nm), strain=float(result.strain),
            intercept=float(result.intercept), slope=float(result.slope),
            x=np.asarray(result.x, dtype=float),
            y=np.asarray(result.y, dtype=float),
            r2=report._wh_r2(result),
            instrument_note=str(getattr(dock, "wh_inst_note", "") or ""),
            warning=str(getattr(result, "warning", "") or ""),
            n_used=int(np.size(result.x)))

    # 方法表：把"这次到底用了什么参数"写清楚。峰/拟合参数从面板快照读，
    # 拟合背景按每根 FitEntry 实际用的值——不是当前设置（拟合完改过设置
    # 会把旧结果的方法写错）。
    methods = []
    if "peaks" in sections or need_fits:
        factor, distance, min_fwhm, max_peaks = _peak_settings(window, dock)
        methods += [
            ("寻峰", "显著度", f"{factor:g} σ"),
            ("寻峰", "最小峰距", f"{distance:g}°"),
            ("寻峰", "最小 FWHM",
             "不限" if min_fwhm is None else f"{min_fwhm:g}°"),
            ("寻峰", "最多根数", f"{max_peaks} 根"),
        ]
    if curve.fits:
        shapes = sorted({fit.shape for fit in curve.fits})
        halfs = sorted({fit.half_window for fit in curve.fits})
        bgs = sorted({fit.background_mode for fit in curve.fits})
        methods += [
            ("单峰拟合", "线型",
             "、".join(SHAPE_LABELS.get(s, s) for s in shapes)),
            ("单峰拟合", "半窗", "、".join(f"{h:g}°" for h in halfs)),
            ("单峰拟合", "局部背景",
             "、".join(BACKGROUND_LABELS.get(b, b) for b in bgs)),
        ]
    if curve.wh is not None:
        params = getattr(result, "params", {}) or {}
        wavelength = params.get("wavelength", curve.wavelength)
        k = params.get("K")
        methods += [
            ("W-H", "波长",
             f"{float(wavelength):.4f} Å" if wavelength else "—"),
            ("W-H", "K", f"{float(k):g}" if k is not None else "—"),
            ("W-H", "仪器展宽",
             curve.wh.instrument_note or "未扣仪器"),
        ]
    curve.methods = methods

    # 物相：物相工作台的结果是"当前曲线"的，跟曲线下拉对应；没开过工作台
    # 或没筛过相就是空
    phases, phase_tth, phase_y, phase_source = [], None, None, ""
    phase_methods = []
    editor = getattr(window, "phase_editor", None)
    if ("phases" in sections and editor is not None
            and getattr(editor, "rows", None)):
        curve_pair = getattr(editor, "curve", None)
        if curve_pair is not None:
            phase_tth = np.asarray(curve_pair[0], dtype=float)
            phase_y = np.asarray(curve_pair[1], dtype=float)
            phase_source = editor.curve_combo.currentText()
            try:
                chosen = {id(row) for row in editor._selected_rows()}
            except Exception:                          # noqa: BLE001
                chosen = set()
            for row in editor.rows:
                phases.append(report.ReportPhase(
                    name=Path(str(row.path)).stem,
                    score=float(row.score), coverage=float(row.coverage),
                    purity=float(row.purity), scale=float(row.scale),
                    tth=np.asarray(row.tth, dtype=float),
                    intensity=np.asarray(row.intensity, dtype=float),
                    selected=id(row) in chosen))
            phase_methods = [
                ("相库", str(getattr(editor, "lib_dir", "—"))),
                ("元素过滤", editor.elements_edit.text().strip() or "不限"),
                ("匹配容差", f"{editor.tolerance.value():g}°"),
                ("寻峰显著度", f"{editor.prominence.value():g} σ"),
                ("寻峰最小峰距", f"{editor.min_distance.value():g}°"),
            ]

    refine = _refine_snapshot(window) if "refine" in sections else None
    insitu = _insitu_snapshot(window) if "insitu" in sections else None

    return report.ReportSnapshot(
        title=str(title).strip() or "XRD 分析报告",
        sample=str(sample).strip(),
        generated=time.strftime("%Y-%m-%d %H:%M:%S"),
        version=str(__version__),
        curve=curve, phases=phases, phase_tth=phase_tth,
        phase_intensity=phase_y, phase_source=phase_source,
        refine=refine, insitu=insitu,
        sections=sections, phase_methods=phase_methods)


# ══ 路径 / 弹窗 / 导出 ════════════════════════════════════
def default_report_path(sample: str) -> Path:
    """默认报告路径：`outputs/reports/<样品>_分析报告_<时间>.html`。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    raw = f"{sample or 'XRD'}_分析报告_{stamp}"
    name = data_export.safe_filename(raw, fallback=f"分析报告_{stamp}")
    return paths.default_outputs_dir() / "reports" / f"{name}.html"


class ReportDialog(QDialog):
    """一键分析报告的小弹窗：标题、样品、曲线、要包含的节、输出路径。"""

    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle("一键分析报告")
        self.setMinimumWidth(460)
        self._window = window
        self.curves = available_curves(window)

        root = QVBoxLayout(self)
        form = QFormLayout()
        self.title_edit = QLineEdit("XRD 分析报告")
        self.title_edit.setObjectName("report_title_edit")
        self._auto_title = "XRD 分析报告"   # 用户没改过标题时跟着样品走
        form.addRow("标题", self.title_edit)

        self.sample_edit = QLineEdit()
        self.sample_edit.setObjectName("report_sample_edit")
        form.addRow("样品", self.sample_edit)

        self.curve_combo = QComboBox()
        self.curve_combo.setObjectName("report_curve_combo")
        for key, title in self.curves:
            self.curve_combo.addItem(title, key)
        form.addRow("分析曲线", self.curve_combo)
        if not self.curves:
            hint = QLabel("还没有算好的 1D 曲线：先勾选数据、点 [1D] 出图")
            hint.setStyleSheet("color: gray;")
            form.addRow("", hint)

        out_row = QWidget()
        out_lay = QHBoxLayout(out_row)
        out_lay.setContentsMargins(0, 0, 0, 0)
        self.path_edit = QLineEdit()
        self.path_edit.setObjectName("report_path_edit")
        self.path_edit.setText(str(default_report_path("")))
        browse = QPushButton("浏览…")
        browse.setObjectName("report_browse_btn")
        browse.clicked.connect(self._browse)
        out_lay.addWidget(self.path_edit, 1)
        out_lay.addWidget(browse)
        form.addRow("输出文件", out_row)
        root.addLayout(form)

        box = QGroupBox("包含的节")
        box_lay = QVBoxLayout(box)
        self.section_boxes = {}
        for key, label in _SECTION_CHOICES:
            check = QCheckBox(label)
            check.setObjectName(f"report_{key}_chk")
            check.setChecked(True)
            check.toggled.connect(self._sync)
            box_lay.addWidget(check)
            self.section_boxes[key] = check
        root.addWidget(box)

        self.open_after = QCheckBox("生成后用浏览器打开")
        self.open_after.setChecked(True)
        self.open_after.setObjectName("report_open_after_chk")
        root.addWidget(self.open_after)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #b45309;")
        root.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.generate_btn = QPushButton("生成报告")
        self.generate_btn.setObjectName("report_generate_btn")
        self.generate_btn.setDefault(True)
        self.generate_btn.setEnabled(bool(self.curves))
        self.generate_btn.clicked.connect(self._generate)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(self.generate_btn)
        buttons.addWidget(cancel)
        root.addLayout(buttons)

        if self.curves:
            self._set_curve_defaults()

    def _set_curve_defaults(self):
        key = self.curve_combo.currentData()
        title = self.curve_combo.currentText()
        sample = _default_sample(title)
        auto = f"{sample} XRD 分析报告" if sample else "XRD 分析报告"
        self.title_edit.setText(auto)
        self._auto_title = auto
        self.sample_edit.setText(sample)
        self.path_edit.setText(str(default_report_path(sample)))
        self.curve_combo.currentIndexChanged.connect(
            lambda _idx: self._on_curve_changed())
        self._curve_key = key

    def _on_curve_changed(self):
        title = self.curve_combo.currentText()
        sample = _default_sample(title)
        auto = f"{sample} XRD 分析报告" if sample else "XRD 分析报告"
        if self.title_edit.text() == self._auto_title:
            self.title_edit.setText(auto)      # 没改过标题：跟着新样品走
        self._auto_title = auto
        self.sample_edit.setText(sample)
        self.path_edit.setText(str(default_report_path(sample)))

    def _browse(self):
        start = self.path_edit.text() or str(
            paths.default_outputs_dir() / "reports")
        path, _ = QFileDialog.getSaveFileName(
            self, "保存分析报告", start, "HTML (*.html)")
        if path:
            if not path.lower().endswith(".html"):
                path += ".html"
            self.path_edit.setText(path)

    def _sync(self):
        self.generate_btn.setEnabled(
            bool(self.curves) and any(
                box.isChecked() for box in self.section_boxes.values()))

    def _generate(self):
        sections = tuple(key for key, box in self.section_boxes.items()
                         if box.isChecked())
        if not sections:
            self.status.setText("至少勾选一节")
            return
        path = Path(self.path_edit.text().strip()).expanduser()
        if not str(path) or path.name in ("", "."):
            self.status.setText("先选一个输出文件")
            return
        if path.suffix.lower() != ".html":
            path = path.with_suffix(".html")
        try:
            snapshot = collect_report_snapshot(
                self._window,
                title=self.title_edit.text(),
                sample=self.sample_edit.text(),
                curve_key=self.curve_combo.currentData(),
                sections=sections)
        except Exception as err:                       # noqa: BLE001
            self.status.setText(user_error_text(err))
            return

        # 生成是纯 CPU + Matplotlib Agg，通常 1–3 秒；用模态进度框让用户
        # 看到"在做事"，同时避免和主窗口的 Qt 画布重绘并发（Matplotlib
        # 的全局状态不适合跨线程同时用）。
        progress = QProgressDialog("正在生成报告…", "", 0, 0, self)
        progress.setWindowTitle("一键分析报告")
        progress.setWindowModality(Qt.WindowModal)
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.show()
        QApplication.processEvents()
        try:
            target = data_export.unique_path(path)
            report.write_report(snapshot, target)
        except Exception as err:                       # noqa: BLE001
            progress.close()
            text = user_error_text(err)
            self.status.setText(text)
            _log(self._window, f"报告生成失败：{text}")
            return
        progress.close()
        _log(self._window, f"分析报告已生成：{target}")
        if self.open_after.isChecked():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        self.accept()


def open_report_dialog(window):
    """工具栏 [报告]：没有可用曲线先给一句人话，否则打开弹窗。"""
    if not available_curves(window):
        QMessageBox.information(
            window, "一键分析报告",
            "还没有算好的 1D 曲线。先在文件栏勾选数据、点 [1D] 出图，"
            "再生成报告。")
        return None
    dialog = ReportDialog(window)
    dialog.exec()
    return dialog
