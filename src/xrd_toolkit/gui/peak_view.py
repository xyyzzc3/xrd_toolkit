"""1D 面板的寻峰标记与峰表（2026-10-23）。

界面里的"寻峰"只做**显示**：按该面板快照里的参数调用
`services.peak_finding.find_peaks_1d`，在曲线上画峰位短线和 2θ 标签，
并把结果填进「1D」页下方的峰表。不写产物、不改曲线、不重算积分。

口径：
  * 用**画出来的那条曲线**（扣背景/平滑/裁剪之后），和眼睛看到的一致；
  * 参数按面板各记各的（快照键 `寻峰*` / `显示峰位`）；
  * 裁剪留下的断点（NaN）按连续段分别寻峰，不会在断点处造出假峰；
  * 峰表双击一行 = 把视图缩放到那根峰（写回该面板的"视图 2θ"）。
"""
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QTableWidgetItem

from xrd_toolkit.gui.state.base import _panel_param, _param_box_set
from xrd_toolkit.gui.state.config import _AUX_GID_PREFIX
from xrd_toolkit.gui.state.processing import _proc_curve
from xrd_toolkit.services.peak_finding import find_peaks_1d

PEAK_COLOR = "#E69F00"          # Okabe–Ito 橙：与曲线蓝、背景绿都分得开
PEAK_KEYS = ("寻峰显著度", "寻峰最小峰距 (°)", "寻峰最小 FWHM (°)",
             "寻峰最多根数", "显示峰位", "标注全部峰位")
TABLE_COLUMNS = ("2θ (°)", "FWHM (°)", "高度")


def _finite_segments(tth, intensity):
    """把裁剪造成的 NaN 断点拆成连续段（每段 ≥10 点才寻峰）。"""
    t = np.asarray(tth, dtype=float)
    y = np.asarray(intensity, dtype=float)
    ok = np.isfinite(y)
    if ok.all():
        if y.size >= 10:
            yield t, y
        return
    start = None
    for i, good in enumerate(ok):
        if good and start is None:
            start = i
        elif not good and start is not None:
            if i - start >= 10:
                yield t[start:i], y[start:i]
            start = None
    if start is not None and y.size - start >= 10:
        yield t[start:], y[start:]


def _settings(window, dock):
    """该面板的寻峰参数（快照优先，见 _panel_param）。"""
    factor = float(_panel_param(window, dock, "寻峰显著度", 5.0))
    distance = float(_panel_param(window, dock, "寻峰最小峰距 (°)", 0.008))
    min_fwhm = float(_panel_param(window, dock, "寻峰最小 FWHM (°)", 0.0))
    max_peaks = int(_panel_param(window, dock, "寻峰最多根数", 20))
    return (factor, distance, None if min_fwhm <= 0 else min_fwhm,
            max(1, max_peaks))


def detect_peaks_for_dock(window, dock, tth=None, intensity=None):
    """在面板曲线上找峰；`显示峰位` 关着时返回空列表。

    tth/intensity 省略时用面板缓存的数据并跑一遍处理链（扣背景/
    平滑/裁剪）；绘图路径已经把处理后的曲线递进来，避免重复计算。
    """
    if not _panel_param(window, dock, "显示峰位", False):
        return []
    if tth is None or intensity is None:
        tth = getattr(dock, "last_tth", None)
        intensity = getattr(dock, "last_intensity", None)
        if tth is None or intensity is None:
            return []
        tth, intensity, _base = _proc_curve(
            window, dock, getattr(dock, "panel_file", None), tth, intensity)
    factor, distance, min_fwhm, max_peaks = _settings(window, dock)
    found = []
    for seg_t, seg_y in _finite_segments(tth, intensity):
        found.extend(find_peaks_1d(
            seg_t, seg_y, prominence_factor=factor,
            min_distance_deg=distance, min_fwhm_deg=min_fwhm,
            max_peaks=max_peaks))
    if len(found) > max_peaks:
        found = sorted(found, key=lambda p: -p.height)[:max_peaks]
    found.sort(key=lambda p: p.center)
    return found


def _bbox_overlap(a, b, pad=2.0):
    """两个显示坐标包围盒是否相交（留 pad 像素）。"""
    return not (a.x1 + pad < b.x0 or b.x1 + pad < a.x0
                or a.y1 + pad < b.y0 or b.y1 + pad < a.y0)


def _plotted_points(ax):
    """主曲线在显示坐标里的全部点（原始/基线辅助线不参与避让）。

    标签要避开**画出来的那条谱**（含窄峰顶部），所以不抽稀到几十点；
    辅助线是虚线/点线，压到字上不影响读数，反而会把标签逼得没地方。
    """
    chunks = []
    for line in ax.lines:
        gid = str(line.get_gid() or "")
        if gid.startswith(_AUX_GID_PREFIX):
            continue
        x = np.asarray(line.get_xdata(), dtype=float)
        y = np.asarray(line.get_ydata(), dtype=float)
        if x.size < 5 or x.size != y.size:
            continue
        keep = np.isfinite(x) & np.isfinite(y)
        if np.any(keep):
            chunks.append(np.column_stack([x[keep], y[keep]]))
    if not chunks:
        return np.zeros((0, 2))
    pts = np.vstack(chunks)
    if pts.shape[0] > 8000:
        pts = pts[::max(1, pts.shape[0] // 8000)]
    disp = ax.transData.transform(pts)
    return disp[np.isfinite(disp).all(axis=1)]


def _place_labels(ax, items, label_all):
    """在显示坐标里给峰位标签找位置，返回 [(text, peak, y, bbox)]。

    规则（2026-10-24 用户拍板，业内主流做法）：先保证标签之间、标签
    与峰线/已画曲线不相交；放不下的标签默认不画（峰表里仍然列全），
    勾「标注全部峰位」时改为"尽量避让、实在放不下就允许重叠"。
    """
    try:
        renderer = ax.figure.canvas.get_renderer()
    except Exception:                    # pragma: no cover - 极端后端
        return []
    ax_box = ax.bbox
    pts_disp = _plotted_points(ax)

    texts = []
    ticks = {}
    for peak, yy in items:
        txt = ax.text(peak.center, yy, f"{peak.center:.3f}", rotation=90,
                      fontsize=7, color=PEAK_COLOR, ha="center",
                      va="bottom", clip_on=True, zorder=6,
                      label="_peak_label")
        txt.set_gid(_AUX_GID_PREFIX + "peak_label")
        texts.append((txt, peak, yy))
        d = ax.transData.transform((peak.center, yy))
        ticks[id(peak)] = [d[0], d[1], d[1] + 6.0]
    if not texts:
        return []
    step = max(txt.get_window_extent(renderer).height
               for txt, _peak, _yy in texts) + 2.0
    step = max(step, 6.0)

    placed = []
    out = []
    for txt, peak, yy in sorted(texts, key=lambda it: -it[1].height):
        d = ax.transData.transform((peak.center, yy))
        x_disp = d[0]
        best = None
        # 默认模式不许把标签抬得太远（长引线会横穿别的峰）；标注全部
        # 时放宽到坐标框能容纳为止
        max_k = 40 if label_all else max(2, int(0.35 * ax_box.height / step))
        for k in range(max_k):
            shift = 6.0 + k * step
            y_data = ax.transData.inverted().transform(
                (x_disp, d[1] + shift))[1]
            txt.set_position((peak.center, y_data))
            bb = txt.get_window_extent(renderer)
            if bb.y1 > ax_box.y1:
                break
            bad = any(_bbox_overlap(bb, other) for other in placed)
            if not bad:
                # 避开别的峰的竖线/峰顶
                for pid, seg in ticks.items():
                    if pid == id(peak):
                        continue
                    if (bb.x0 - 2 <= seg[0] <= bb.x1 + 2
                            and not (bb.y1 < seg[1] - 2
                                     or bb.y0 > seg[2] + 2)):
                        bad = True
                        break
            if not bad and pts_disp.size:
                # 避开曲线本身（峰体），否则标签会压在邻峰上
                inside = ((pts_disp[:, 0] >= bb.x0 - 2)
                          & (pts_disp[:, 0] <= bb.x1 + 2)
                          & (pts_disp[:, 1] >= bb.y0 - 2)
                          & (pts_disp[:, 1] <= bb.y1 + 2))
                bad = bool(np.any(inside))
                if not bad:
                    # 自己的引线也不许横穿曲线
                    col = np.abs(pts_disp[:, 0] - x_disp) <= 2.0
                    span = ((pts_disp[:, 1] > d[1] + 2.0)
                            & (pts_disp[:, 1] < bb.y0 - 2.0))
                    bad = bool(np.any(col & span))
            if not bad and placed:
                # 自己的引线不许穿过已放好的标签
                for other in placed:
                    if (other.x0 - 2 <= x_disp <= other.x1 + 2
                            and not (other.y1 < d[1]
                                     or other.y0 > bb.y0)):
                        bad = True
                        break
            if not bad:
                best = (txt, peak, yy, bb)
                break

        if best is None:
            if not label_all:
                txt.remove()               # A：放不下就不标，峰表仍列全
                continue
            # B：放到坐标范围内最高处（允许重叠，但保证看得见）
            last = None
            for k in range(60):
                shift = 6.0 + k * step
                y_data = ax.transData.inverted().transform(
                    (x_disp, d[1] + shift))[1]
                txt.set_position((peak.center, y_data))
                bb = txt.get_window_extent(renderer)
                if bb.y1 > ax_box.y1:
                    break
                last = bb
            if last is None:               # 连一格都放不下：贴顶但别出框
                bb = txt.get_window_extent(renderer)
                for _ in range(3):
                    y_data = ax.transData.inverted().transform(
                        (x_disp, ax_box.y1 - 1.0 - bb.height))[1]
                    txt.set_position((peak.center, y_data))
                    bb = txt.get_window_extent(renderer)
            else:
                bb = last
            best = (txt, peak, yy, bb)
        out.append(best)
        placed.append(best[3])
        ticks[id(peak)][2] = best[3].y0 - 1.0
    return out


def draw_peak_markers(window, dock, ax, tth, intensity):
    """在 1D 轴上画峰位竖线与 2θ 标签，返回检出的峰。

    在坐标范围（含纵轴自动）定好之后调用；`ax.clear()` 会清掉旧标记，
    所以每次重画都重建。**所有峰都有竖线**；标签按屏幕位置避让，
    默认放不下就不标（峰表列全），「标注全部峰位」打开时允许重叠。
    """
    peaks = detect_peaks_for_dock(window, dock, tth, intensity)
    dock.detected_peaks = peaks
    if not peaks:
        dock.labeled_peaks = 0
        return peaks
    y = np.asarray(intensity, dtype=float)
    t = np.asarray(tth, dtype=float)
    log = ax.get_yscale() == "log"
    items = []
    for peak in peaks:
        i = int(np.clip(np.searchsorted(t, peak.center), 0, t.size - 1))
        yy = float(y[i]) if np.isfinite(y[i]) else float(peak.height)
        if np.isfinite(yy) and not (log and yy <= 0):
            items.append((peak, yy))
    if not items:
        return peaks

    label_all = bool(_panel_param(window, dock, "标注全部峰位", False))
    labels = _place_labels(ax, items, label_all)
    dock.labeled_peaks = len(labels)
    labeled = {id(peak) for _txt, peak, _yy, _bb in labels}

    # 竖线最后画：有标签的画到标签下沿，没标签的只画短刻度——
    # 后画可以盖在标签底下（zorder 更低），不会把字截断
    for peak, yy in items:
        d = ax.transData.transform((peak.center, yy))
        if id(peak) in labeled:
            bb = next(b for _t, p, _y, b in labels if p is peak)
            top = ax.transData.inverted().transform(
                (d[0], bb.y0 - 1.0))[1]
        else:
            span = ax.get_ylim()[1] - ax.get_ylim()[0]
            top = yy + (0.03 * span if not log else 0.0)
            if log:
                top = ax.transData.inverted().transform(
                    (d[0], d[1] + 8.0))[1]
        line, = ax.plot([peak.center, peak.center], [yy, top],
                        color=PEAK_COLOR, lw=1.0, zorder=5,
                        label="_peak_marker")
        # 标成辅助线：重画的 Customize 保护按"数据线"顺序回填样式，
        # 把标记线算进去会让曲线线型错位（同背景辅助线，见 _is_aux_line）
        line.set_gid(_AUX_GID_PREFIX + "peak_marker")
    return peaks


def fill_peak_table(window, dock, peaks):
    """把峰填进「1D」页的峰表；没有峰时整块隐藏。"""
    table = getattr(window, "peak_table", None)
    box = getattr(window, "peak_box", None)
    if table is None or box is None:
        return
    table.setRowCount(0)
    if not peaks:
        box.setVisible(False)
        return
    caption = getattr(window, "peak_caption", None)
    if caption is not None:
        # 窄坞：标题只留「峰表 · N 根」；双击提示放进表格 tooltip
        caption.setText(f"峰表 · {len(peaks)} 根")
    table.setToolTip("双击一行放大到该峰")
    table.setRowCount(len(peaks))
    for row, peak in enumerate(peaks):
        for col, text in enumerate((
                f"{peak.center:.4f}", f"{peak.fwhm:.4f}",
                f"{peak.height:.0f}")):
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if col == 0:
                item.setData(Qt.UserRole, (peak.center, peak.fwhm))
            table.setItem(row, col, item)
    box.setVisible(True)


def refresh_peak_table(window):
    """焦点切换/显隐变化后刷新峰表（只认 1D 面板）。"""
    dock = window.plot_docks.get(window.focus_panel)
    if dock is None or not str(window.focus_panel).startswith("1D|"):
        fill_peak_table(window, None, [])
        return
    peaks = detect_peaks_for_dock(window, dock)
    dock.detected_peaks = peaks
    fill_peak_table(window, dock, peaks)


def zoom_to_peak(window, row):
    """双击峰表某行：把该面板的视图缩到这个峰附近。"""
    table = getattr(window, "peak_table", None)
    dock = window.plot_docks.get(window.focus_panel)
    if table is None or dock is None:
        return
    item = table.item(row, 0)
    if item is None:
        return
    data = item.data(Qt.UserRole)
    if not data:
        return
    center, fwhm = (float(v) for v in data)
    half = max(10.0 * fwhm, 0.05)
    lo, hi = center - half, center + half
    snap = dict(getattr(dock, "params_snapshot", None) or {})
    snap["视图 2θ 下限 (°)"] = lo
    snap["视图 2θ 上限 (°)"] = hi
    dock.params_snapshot = snap
    _param_box_set(window, "视图 2θ 下限 (°)", lo)
    _param_box_set(window, "视图 2θ 上限 (°)", hi)
    from xrd_toolkit.gui.plot.refresh import _redraw_panel   # 破循环
    _redraw_panel(window, window.focus_panel)


def install_peak_view(window):
    """把峰表接到焦点切换上（在参数坞建好之后调用一次）。"""
    prev = getattr(window, "_on_focus_changed", None)

    def hook(previous, current):
        if prev is not None:
            prev(previous, current)
        refresh_peak_table(window)

    window._on_focus_changed = hook
