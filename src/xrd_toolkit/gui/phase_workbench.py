"""物相筛选中央工作台（2026-10-30）。

打开方式：工具栏 [物相]。与 [掩膜]、[精修] 同一套中央 MDI 子窗口：
进入时收起参数坞和日志坞，退出只隐藏，曲线/相库/结果都留着。

流程：

    选一条 1D 曲线 → 选相库目录（默认 data/structures）
    → [开始筛相]（后台线程：寻峰 → 对目录里每张 CIF 粗扫晶胞缩放打分）
    → 候选表按 score 排序；点一行看该相的理论峰位叠在观测曲线上
    → [送去精修] 把曲线和这张 CIF 带进精修工作台

打分与命令行 `scripts/analyze_pattern.py` 同源
（`simulation.phase_search`）：coverage（观测峰被覆盖的比例）+
0.2 × purity（理论峰被匹配的比例）；晶胞缩放 0.96–1.05。

数据来源：选中 1D 面板的积分曲线（`last_tth` / `last_intensity`）。
寻峰参数在左栏：显著度（σ 倍数）与最小峰距；匹配容差是峰位允许偏差。
"""
import csv
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QDoubleSpinBox,
    QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMdiSubWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSizePolicy, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget)

from xrd_toolkit import paths
from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui.panel_state import _collect_geometry, _log
from xrd_toolkit.gui.tasks import BackgroundTask, user_error_text
from xrd_toolkit.presets import PROJECT_PRESET, preset_phase_library

_OBS_COLOR = "#333333"
# 多相叠加时按选中顺序轮换；与精修图的布拉格刻度同一套色系
_PHASE_COLORS = ("#1f5fa8", "#e08a1e", "#2a9d8f", "#8e5aa8",
                 "#c0392b", "#16a085", "#7f8c8d", "#d35400")


def _load_text_curve(path):
    """读两列文本曲线（2θ, 强度）：跳过 # 注释与读不动的行。

    GUI 导出的 txt 就是这种格式（# 说明块 + 两列）；CSV/制表符/空格
    分隔都认。少于 20 个有效点视为坏文件。
    """
    rows = []
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = re.split(r"[,\s;]+", line)
            if len(parts) < 2:
                continue
            try:
                rows.append((float(parts[0]), float(parts[1])))
            except ValueError:
                continue
    if len(rows) < 20:
        raise ValueError("有效数据行少于 20 行")
    arr = np.asarray(rows, dtype=float)
    arr = arr[np.argsort(arr[:, 0])]
    return arr[:, 0].copy(), arr[:, 1].copy()


@dataclass
class PhaseRow:
    """候选相的一行结果（只带画图需要的数组，不把结构对象带过线程）。"""
    path: Path
    score: float
    scale: float
    coverage: float
    purity: float
    tth: np.ndarray
    intensity: np.ndarray


def _screen_worker(tth, y, lib, elements, wavelength, tol,
                   prominence, min_distance, skip_names=None):
    """后台线程：寻峰 → 对相库打分 → 返回每相的理论峰位。

    不碰界面控件。pymatgen 是可选依赖，缺了要保留它自己的安装提示，
    所以 ImportError 转成 RuntimeError（否则会被当成内部 bug 包装掉）。
    skip_names 是"不参与主相候选"的第二相文件名（预设给的）。
    """
    try:
        from xrd_toolkit.services.background import estimate_baseline_sliding
        from xrd_toolkit.services.peak_finding import find_peaks_1d
        from xrd_toolkit.simulation import phase_search as ps
    except ImportError as err:
        raise RuntimeError(str(err)) from err

    tth = np.asarray(tth, dtype=float)
    y = np.asarray(y, dtype=float)
    good = np.isfinite(tth) & np.isfinite(y)
    tth, y = tth[good], y[good]
    if tth.size < 20:
        raise RuntimeError("这条曲线的有效点太少，无法筛相")
    background = estimate_baseline_sliding(tth, y, 0.2)
    peaks = find_peaks_1d(
        tth, y, baseline=background,
        prominence_factor=float(prominence),
        min_distance_deg=float(min_distance), max_peaks=80)
    obs_t = np.array([p.center for p in peaks], dtype=float)
    if obs_t.size < 3:
        raise RuntimeError("观测峰少于 3 根，无法筛相（可降低显著度）")

    lam = float(wavelength)
    tmin, tmax = float(tth.min()), float(tth.max())
    matches = ps.screen_phases(
        obs_t, lib, lam, tmin, tmax, 0.0, elements=elements,
        tol=float(tol), skip_names=skip_names)
    # 拿最佳候选估一个全局 2θ 零位，只用于叠图/后续输出——不重新跑一遍
    # 全库打分：零位主要影响"看起来贴不贴"，排序仍是零位 0 下的结果；
    # 重打分会让大库筛相变慢一倍（2026-11-11 实测小库都跑超 30 s）。
    zero = 0.0
    if matches:
        zero = ps.estimate_zero(
            obs_t, matches[0].structure, lam, tmin, tmax,
            scale=matches[0].scale)
    rows = []
    for match in matches:
        d0, inten = ps.reflection_data(match.structure, lam, tmin, tmax)
        sticks_t, sticks_i = ps.scaled_positions(
            d0, inten, lam, match.scale, zero)
        rows.append(PhaseRow(
            match.path, match.score, match.scale, match.coverage,
            match.purity, np.asarray(sticks_t, dtype=float),
            np.asarray(sticks_i, dtype=float)))
    return obs_t, rows, zero


class PhaseWorkbench(QWidget):
    """物相筛选工作台的界面与流程。"""

    def __init__(self, main_window):
        super().__init__()
        self.main = main_window
        self.setObjectName("phase_workbench")

        self.lib_dir = self._library_for_preset(
            getattr(main_window, "preset", None) or PROJECT_PRESET)
        self.rows = []
        self.curve = None
        self.obs_t = None
        self._zero_used = None
        self._task = None
        self._loaded_curves = {}       # {"file:路径": {"tth", "intensity", "label"}}
        self._product_sources = {}     # {"product:ID": Source}（文件栏 1D 产物）
        self._last_wavelength = None

        self._build_ui()
        self.refresh_curves()
        self._refresh_lib_label()

    # ── 项目预设 ─────────────────────────────────────────────
    def _preset(self):
        """当前窗口的项目预设（没设置时退回内置默认）。"""
        return getattr(self.main, "preset", None) or PROJECT_PRESET

    def _second_phase_cif(self):
        return str(self._preset().get("second_phase_cif") or "").strip()

    @staticmethod
    def _library_for_preset(preset):
        return preset_phase_library(preset or PROJECT_PRESET)

    def apply_preset(self, preset):
        """应用项目预设：换相库 + 更新第二相提示 + 清掉旧候选。"""
        requested = str(preset.get("phase_library") or "").strip()
        self.lib_dir = self._library_for_preset(preset)
        self.rows = []
        self.table.setRowCount(0)
        self.send_btn.setEnabled(False)
        self.best_label.setText("还没有筛相")
        self.best_label.setStyleSheet("color: gray;")
        self._refresh_lib_label()
        status = (f"已应用预设「{preset.get('name', '')}」："
                  f"相库 {self.lib_dir.name}")
        if (requested and not Path(requested).expanduser().is_dir()):
            status += f"（配置的相库 {requested} 不存在，先用默认）"
        self._set_status(status)

    def _refresh_lib_label(self):
        try:
            cifs = list(self.lib_dir.glob("*.cif"))
        except OSError:
            cifs = []
        if cifs:
            second = self._second_phase_cif()
            tail = (f"，第二相 {second} 排除"
                    if second and any(p.name == second for p in cifs) else "")
            self.lib_label.setText(
                f"{self.lib_dir.name}（{len(cifs)} 张 CIF{tail}）")
        else:
            self.lib_label.setText(f"{self.lib_dir}（没有 CIF）")
        self.lib_label.setToolTip(str(self.lib_dir))
        self._refresh_preset_hint()

    def _refresh_preset_hint(self):
        preset = self._preset()
        second = self._second_phase_cif()
        if second:
            text = (f"当前预设「{preset.get('name', '')}」：{second} 视为"
                    "第二相，不参与主相候选，由精修工作台按额外相处理。")
        else:
            text = (f"当前预设「{preset.get('name', '')}」：没有指定第二相，"
                    "相库里所有 CIF 都参与主相候选。")
        self.preset_hint.setText(text)

    # ── 界面组装 ─────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(QLabel("曲线"))
        self.curve_combo = QComboBox()
        self.curve_combo.setMinimumWidth(220)
        self.curve_combo.currentIndexChanged.connect(
            lambda _i: self._on_curve_changed(clear=True))
        top.addWidget(self.curve_combo, 1)
        self.use_focus_btn = QPushButton("用当前图")
        self.use_focus_btn.clicked.connect(self._use_focus_curve)
        top.addWidget(self.use_focus_btn)
        self.open_curve_btn = QPushButton("打开 1D…")
        self.open_curve_btn.setToolTip(
            "读两列文本（2θ, 强度），不用先在 1D 页出图")
        self.open_curve_btn.clicked.connect(self._choose_curve_file)
        top.addWidget(self.open_curve_btn)
        top.addSpacing(10)
        top.addWidget(QLabel("相库"))
        self.lib_label = QLabel("")
        self.lib_label.setStyleSheet("color: gray;")
        top.addWidget(self.lib_label, 1)
        self.lib_btn = QPushButton("选目录…")
        self.lib_btn.clicked.connect(self._choose_lib)
        top.addWidget(self.lib_btn)
        root.addLayout(top)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        row2.addWidget(QLabel("元素"))
        self.elements_edit = QLineEdit()
        self.elements_edit.setPlaceholderText("例如 Fe Co Ni（留空不筛）")
        self.elements_edit.setToolTip(
            "只保留元素全在这张表里的相；留空 = 相库里全部参与")
        row2.addWidget(self.elements_edit, 1)
        self.screen_btn = QPushButton("开始筛相")
        self.screen_btn.setToolTip("对相库目录里的每张 CIF 打峰位匹配分")
        self.screen_btn.clicked.connect(self._start_screen)
        row2.addWidget(self.screen_btn)
        root.addLayout(row2)

        split = QSplitter(Qt.Horizontal)
        root.addWidget(split, 1)

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setMinimumWidth(236)
        left_scroll.setMaximumWidth(330)
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(2, 2, 8, 2)
        left_lay.setSpacing(6)

        left_lay.addWidget(_section("寻峰"))
        self.prominence = QDoubleSpinBox()
        self.prominence.setRange(1.0, 50.0)
        self.prominence.setDecimals(1)
        self.prominence.setSingleStep(0.5)
        self.prominence.setValue(5.0)
        self.prominence.setSuffix(" σ")
        self.prominence.setToolTip("噪声的倍数：越高越只留明显的峰")
        left_lay.addLayout(_labeled_row("显著度", self.prominence))
        self.min_distance = QDoubleSpinBox()
        self.min_distance.setRange(0.001, 1.0)
        self.min_distance.setDecimals(4)
        self.min_distance.setSingleStep(0.001)
        self.min_distance.setValue(0.008)
        self.min_distance.setSuffix(" °")
        self.min_distance.setToolTip("两根峰之间至少隔多少度")
        left_lay.addLayout(_labeled_row("最小峰距", self.min_distance))
        self.tolerance = QDoubleSpinBox()
        self.tolerance.setRange(0.02, 0.30)
        self.tolerance.setDecimals(3)
        self.tolerance.setSingleStep(0.01)
        self.tolerance.setValue(0.04)
        self.tolerance.setSuffix(" °")
        self.tolerance.setToolTip("理论峰与观测峰允许的最大偏差")
        left_lay.addLayout(_labeled_row("匹配容差", self.tolerance))

        left_lay.addWidget(_section("结果"))
        self.best_label = QLabel("还没有筛相")
        self.best_label.setWordWrap(True)
        self.best_label.setStyleSheet("color: gray;")
        left_lay.addWidget(self.best_label)
        self.send_btn = QPushButton("送去精修")
        self.send_btn.setEnabled(False)
        self.send_btn.setToolTip("把当前曲线和选中的 CIF 带进精修工作台")
        self.send_btn.clicked.connect(self._send_to_refine)
        left_lay.addWidget(self.send_btn)
        self.cell_btn = QPushButton("峰位精修晶胞")
        self.cell_btn.setEnabled(False)
        self.cell_btn.setToolTip(
            "用观测峰位对选中的 CIF 做峰位级晶胞精修（七个晶系都可，"
            "同时修 2θ 零位）；结果含 a/b/c/α/β/γ 与匹配率，"
            "只出报告、不写回 CIF 或候选")
        self.cell_btn.clicked.connect(self._refine_selected_cell)
        left_lay.addWidget(self.cell_btn)
        self.export_default_btn = QPushButton("导出到默认位置")
        self.export_default_btn.setToolTip(
            "把候选表写成 CSV，直接写到默认输出目录；先 [开始筛相] 出候选")
        self.export_default_btn.clicked.connect(self._export_default)
        left_lay.addWidget(self.export_default_btn)
        self.preset_hint = QLabel("")
        self.preset_hint.setWordWrap(True)
        self.preset_hint.setStyleSheet("color: gray;")
        left_lay.addWidget(self.preset_hint)
        left_lay.addStretch(1)
        left_scroll.setWidget(left)
        split.addWidget(left_scroll)

        right = QSplitter(Qt.Vertical)
        self.fig = Figure(figsize=(6.8, 4.2))
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right.addWidget(self.canvas)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["相", "score", "coverage", "purity", "scale"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._draw_selected)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        right.addWidget(self.table)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 1)
        right.setSizes([420, 200])
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([270, 780])

        self.status = QLabel("选一条 1D 曲线，点 [开始筛相]")
        self.status.setStyleSheet("color: gray;")
        root.addWidget(self.status)

    # ── 曲线与相库 ──────────────────────────────────────────
    def refresh_curves(self):
        """列出可用的 1D 曲线：打开的面板 + 文件栏里的 1D 产物。

        2026-11-02 用户："物相为什么选不了 1D……必须这样吗，很不顺手"——
        以前只列打开的面板；现在文件栏里的「1D 产物」也直接可读（选中时
        读缓存，不重新积分），不需要先双击打开面板。
        """
        keep = self.curve_combo.currentData()
        self.curve_combo.blockSignals(True)
        self.curve_combo.clear()
        self._product_sources = {}
        docks = getattr(self.main, "plot_docks", {}) or {}
        for key, dock in docks.items():
            if not str(key).startswith("1D|"):
                continue
            if getattr(dock, "last_tth", None) is None:
                continue
            title = dock.windowTitle() or str(key).split("|", 1)[-1]
            self.curve_combo.addItem(title, key)
        for source in gui_sources.all_sources(self.main):
            if source.kind not in (gui_sources.ONED, gui_sources.BG):
                continue
            panel_key = f"1D|{gui_sources.source_id(source)}"
            if source.kind == gui_sources.ONED and panel_key in docks:
                continue                      # 已有面板，上面列过了
            key = f"product:{gui_sources.source_id(source)}"
            self._product_sources[key] = source
            kind_text = ("1D 产物" if source.kind == gui_sources.ONED
                         else "处理产物")
            self.curve_combo.addItem(
                f"{gui_sources.panel_display(source)}（{kind_text}）", key)
        for key, entry in self._loaded_curves.items():
            self.curve_combo.addItem(entry["label"], key)
        self.curve_combo.blockSignals(False)
        idx = self.curve_combo.findData(keep) if keep else -1
        if idx < 0 and self.curve_combo.count():
            idx = 0
        if idx >= 0:
            self.curve_combo.setCurrentIndex(idx)
        self._on_curve_changed(clear=False)

    def select_source(self, source):
        """文件栏双击：把这一条 1D/处理产物设为当前曲线。"""
        self.refresh_curves()
        sid = gui_sources.source_id(source)
        for key in (f"1D|{sid}", f"product:{sid}"):
            idx = self.curve_combo.findData(key)
            if idx >= 0:
                self.curve_combo.setCurrentIndex(idx)
                return True
        self._set_status("这条数据还没有可用的 1D 曲线；先在 1D 页出图")
        return False

    def _on_curve_changed(self, clear=False):
        curve = self._selected_curve()
        tth = curve[0] if curve is not None else None
        if tth is not None:
            tth = np.asarray(tth, dtype=float)
            self.status.setText(
                f"曲线 {tth.size} 点，2θ {tth.min():.3f}–{tth.max():.3f}°")
        elif str(self.curve_combo.currentData() or "").startswith("product:"):
            self.status.setText("这条 1D 产物读不到了（被删了？）")
        if clear:
            # 换曲线后旧候选不再对应这条曲线（[送去精修] 会送错配对），
            # 清空结果等用户重新筛；进入工作台时的 refresh 不清（状态保留）
            self.rows = []
            self.obs_t = None
            self.table.setRowCount(0)
            self.send_btn.setEnabled(False)
            self.cell_btn.setEnabled(False)
            self.export_default_btn.setEnabled(True)
            self.best_label.setText("还没有筛相")
            self.best_label.setStyleSheet("color: gray;")
            if curve is not None:
                self.curve = (np.asarray(curve[0], dtype=float),
                              np.asarray(curve[1], dtype=float))
                self._draw(self.curve, [])
            else:
                self.curve = None
                self._draw_placeholder()
        else:
            # 进入工作台/刷新下拉：不清结果，但要把当前曲线画出来
            if curve is not None:
                self.curve = (np.asarray(curve[0], dtype=float),
                              np.asarray(curve[1], dtype=float))
                self._draw_selected()
            else:
                self.curve = None
                self._draw_placeholder()

    def showEvent(self, event):
        """重新显示时补一次绘制（MDI 隐藏期间 draw_idle 可能被吞）。"""
        super().showEvent(event)
        if getattr(self, "curve", None) is None:
            self._draw_placeholder()
        else:
            self._draw_selected()

    def _selected_dock(self):
        key = self.curve_combo.currentData()
        if not key:
            return None
        return getattr(self.main, "plot_docks", {}).get(key)

    def _selected_curve(self):
        """当前曲线的 (2θ, 强度)：面板里的或从文本文件载入的。"""
        key = self.curve_combo.currentData()
        if not key:
            return None
        if str(key).startswith("file:"):
            entry = self._loaded_curves.get(key)
            if entry is None:
                return None
            return entry["tth"], entry["intensity"]
        if str(key).startswith("product:"):
            source = self._product_sources.get(key)
            if source is None:
                return None
            return gui_sources.load_product(source)
        dock = getattr(self.main, "plot_docks", {}).get(key)
        if dock is None or getattr(dock, "last_tth", None) is None:
            return None
        return (np.asarray(dock.last_tth, dtype=float),
                np.asarray(dock.last_intensity, dtype=float))

    def _choose_curve_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "打开 1D 曲线", str(paths.default_outputs_dir()),
            "文本 (*.txt *.csv *.dat);;所有文件 (*)")
        if path:
            self.load_curve_file(path)

    def load_curve_file(self, path):
        """载入两列文本曲线（公开给测试/脚本调用）。"""
        path = Path(path)
        try:
            tth, intensity = _load_text_curve(path)
        except Exception as err:                   # noqa: BLE001
            self._set_status(f"读取曲线失败：{user_error_text(err)}")
            return False
        key = f"file:{path}"
        self._loaded_curves[key] = {
            "tth": tth, "intensity": intensity, "label": path.name}
        self.refresh_curves()
        idx = self.curve_combo.findData(key)
        if idx >= 0:
            self.curve_combo.setCurrentIndex(idx)
        self._set_status(f"已载入 {path.name}（{tth.size} 点）")
        return True

    def _use_focus_curve(self):
        key = getattr(self.main, "focus_panel", None)
        idx = self.curve_combo.findData(key)
        if idx < 0 and self.curve_combo.count() == 1:
            idx = 0
            self._set_status("当前焦点不是 1D 图；已选下拉里唯一的曲线")
        if idx < 0:
            self._set_status("当前焦点不是 1D 图；请在下拉里选一条 1D 曲线")
            return
        self.curve_combo.setCurrentIndex(idx)

    def _choose_lib(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "选择相库目录", str(self.lib_dir))
        if not chosen:
            return
        self.lib_dir = Path(chosen)
        self._refresh_lib_label()
        # 换库后旧候选不再对应当前库，清空结果，等用户重新筛
        self.rows = []
        self.table.setRowCount(0)
        self.send_btn.setEnabled(False)
        self.best_label.setText("还没有筛相")
        self.best_label.setStyleSheet("color: gray;")
        self._set_status("相库已切换，点 [开始筛相]")

    # ── 筛相 ────────────────────────────────────────────────
    def _start_screen(self):
        if self._task is not None:
            self._set_status("上一轮筛相还在跑，请稍候")
            return
        curve = self._selected_curve()
        if curve is None:
            self._set_status("先选一条已经算完的 1D 曲线")
            return
        tth = np.asarray(curve[0], dtype=float).copy()
        y = np.asarray(curve[1], dtype=float).copy()
        if tth.size != y.size or tth.size < 20:
            self._set_status("这条曲线点太少或长度不一致，无法筛相")
            return
        try:
            wavelength = float(
                _collect_geometry(self.main)["wavelength_m"] * 1e10)
        except (KeyError, TypeError):
            self._set_status("当前几何配置没有波长，先去校准页选一条配置")
            return
        self._last_wavelength = wavelength
        self.curve = (tth, y)
        self.screen_btn.setEnabled(False)
        self.send_btn.setEnabled(False)
        self.cell_btn.setEnabled(False)
        self.export_default_btn.setEnabled(False)
        self._set_status("筛相中，请稍候…")
        _log(self.main, f"物相筛选：{self.lib_dir} → "
                        f"{self.curve_combo.currentText()}")

        task = None

        def done(payload):
            self.main._tasks.remove(task)
            self._task = None
            self.screen_btn.setEnabled(True)
            obs_t, rows, zero = payload
            self._on_screen_done(obs_t, rows, zero)

        def error(message):
            self.main._tasks.remove(task)
            self._task = None
            self.screen_btn.setEnabled(True)
            self._set_status(f"筛相失败：{message}")
            _log(self.main, f"物相筛选失败：{message}")

        second = self._second_phase_cif()
        task = BackgroundTask(
            _screen_worker, tth, y, str(self.lib_dir),
            self.elements_edit.text(), wavelength,
            float(self.tolerance.value()), float(self.prominence.value()),
            float(self.min_distance.value()),
            (second,) if second else (),
            on_done=done, on_error=error)
        self._task = task
        self.main._tasks.append(task)
        task.start()

    def _on_screen_done(self, obs_t, rows, zero=0.0):
        self.rows = rows
        self.obs_t = np.asarray(obs_t, dtype=float)
        self._zero_used = float(zero)
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            values = (
                (row.path.stem, None),
                (f"{row.score:.3f}", None),
                (f"{row.coverage:.2f}", None),
                (f"{row.purity:.2f}", None),
                (f"{row.scale:.4f}", None),
            )
            for col, (text, _v) in enumerate(values):
                item = QTableWidgetItem(text)
                if col:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, col, item)
        self.table.blockSignals(False)
        if rows:
            self.table.selectRow(0)
            best = rows[0]
            self.best_label.setText(
                f"最佳匹配：{best.path.stem}\nscore {best.score:.3f}"
                f"（coverage {best.coverage:.2f} / purity {best.purity:.2f}）")
            self.best_label.setStyleSheet("color: #1f5fa8; font-weight: 600;")
            self.send_btn.setEnabled(True)
            self.cell_btn.setEnabled(True)
            self.export_default_btn.setEnabled(True)
            self._set_status(
                f"筛相完成：{len(rows)} 个候选，观测峰 {obs_t.size} 根"
                + (f"；已对齐 2θ 零位 {zero:+.3f}°"
                   if abs(zero) > 0.005 else ""))
            _log(self.main, f"物相筛选完成：{len(rows)} 个候选，"
                            f"最佳 {best.path.stem}（score {best.score:.3f}）")
        else:
            self.best_label.setText("没有候选相能匹配观测峰")
            self.best_label.setStyleSheet("color: gray;")
            self.send_btn.setEnabled(False)
            self.cell_btn.setEnabled(False)
            self.export_default_btn.setEnabled(True)
            self._set_status("筛相完成：没有候选相")
        self._draw_selected()

    # ── 画图 ────────────────────────────────────────────────
    def _draw_placeholder(self):
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        ax.set_axis_off()
        ax.text(0.5, 0.55, "物相筛选", ha="center", va="center",
                fontsize=15, color="#666666", transform=ax.transAxes)
        ax.text(0.5, 0.44, "选一条 1D 曲线，点 [开始筛相]",
                ha="center", va="center", fontsize=10, color="#888888",
                transform=ax.transAxes)
        self.canvas.draw_idle()

    def _selected_row(self):
        idx = self.table.currentRow()
        if 0 <= idx < len(self.rows):
            return self.rows[idx]
        return None

    def _selected_rows(self):
        """表里所有选中的候选行（Ctrl/Cmd 多选时按行号排序）。"""
        model = self.table.selectionModel()
        indexes = (sorted({i.row() for i in model.selectedRows()})
                   if model is not None else [])
        rows = [self.rows[i] for i in indexes if 0 <= i < len(self.rows)]
        if not rows:
            current = self._selected_row()
            if current is not None:
                rows = [current]
        return rows

    def _draw_selected(self):
        if self.curve is None:
            return
        self._draw(self.curve, self._selected_rows())

    def _draw(self, curve, rows):
        tth, y = curve
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        ax.plot(tth, y, "-", lw=0.9, color=_OBS_COLOR, label="观测")
        span = float(np.nanmax(y) - np.nanmin(y))
        amp = 0.55 * span if span > 0 else 1.0
        y0 = float(np.nanmin(y))
        for i, row in enumerate(rows):
            keep = row.intensity >= 0.02
            sticks_t = row.tth[keep]
            sticks_i = row.intensity[keep]
            in_range = (sticks_t >= tth.min()) & (sticks_t <= tth.max())
            sticks_t, sticks_i = sticks_t[in_range], sticks_i[in_range]
            color = _PHASE_COLORS[i % len(_PHASE_COLORS)]
            ax.vlines(sticks_t, y0, y0 + sticks_i * amp,
                      color=color, lw=1.0, alpha=0.9,
                      label=f"{row.path.stem} 理论峰位")
        if len(rows) == 1:
            row = rows[0]
            ax.set_title(
                f"{row.path.stem}   score {row.score:.3f}   "
                f"（coverage {row.coverage:.2f} / purity {row.purity:.2f}）",
                fontsize=10)
        elif rows:
            ax.set_title(f"{len(rows)} 个候选相叠加", fontsize=10)
        else:
            ax.set_title("观测曲线", fontsize=10)
        ax.set_xlabel("2θ (°)", fontsize=8)
        ax.set_ylabel("counts", fontsize=8)
        ax.tick_params(labelsize=7)
        ax.grid(alpha=0.25, linestyle=":")
        ax.legend(fontsize=8, loc="best")
        self.fig.subplots_adjust(left=0.10, right=0.98, top=0.93, bottom=0.12)
        self.canvas.draw_idle()

    # ── 候选表复制 ──────────────────────────────────────────
    def _table_tsv(self, selected_only=False):
        """候选表的 TSV：表头 + 行（右键菜单与测试共用）。"""
        model = self.table.selectionModel()
        rows = (sorted({i.row() for i in model.selectedRows()})
                if selected_only and model is not None
                else list(range(self.table.rowCount())))
        lines = ["相\tscore\tcoverage\tpurity\tscale"]
        for row in rows:
            values = []
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                values.append(item.text() if item is not None else "")
            lines.append("\t".join(values))
        return "\n".join(lines)

    def _copy_table(self, selected_only=False):
        text = self._table_tsv(selected_only)
        n = max(0, len(text.splitlines()) - 1)
        if n == 0:
            self._set_status("候选表没有可复制的行")
            return 0
        QApplication.clipboard().setText(text)
        _log(self.main, f"已复制候选表 {n} 行")
        return n

    def _table_menu(self, pos):
        menu = QMenu(self.table)
        act_sel = menu.addAction("复制选中行")
        act_all = menu.addAction("复制全部")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is act_sel:
            self._copy_table(True)
        elif chosen is act_all:
            self._copy_table(False)

    # ── 送去精修 ────────────────────────────────────────────
    def _refine_selected_cell(self):
        """峰位级晶胞精修（七个晶系）；结果显示在非模态对话框。"""
        row = self._selected_row()
        if row is None:
            self._set_status("先在候选表里选一个相")
            return None
        if self.obs_t is None or np.asarray(self.obs_t).size < 3:
            self._set_status("还没有观测峰：先点 [开始筛相]")
            return None
        wavelength = self._last_wavelength
        if wavelength is None:
            try:
                wavelength = float(
                    _collect_geometry(self.main)["wavelength_m"] * 1e10)
            except (KeyError, TypeError):
                self._set_status("当前几何配置没有波长，先去校准页选一条配置")
                return None
        try:
            from xrd_toolkit.simulation import load_structure
            from xrd_toolkit.simulation.cell_refine import refine_cell
            structure = load_structure(str(row.path))
            result = refine_cell(
                structure, self.obs_t, wavelength=float(wavelength),
                match_tol_deg=float(self.tolerance.value()), max_hkl=12)
        except Exception as err:                   # noqa: BLE001
            self._set_status(f"峰位精修晶胞失败：{user_error_text(err)}")
            _log(self.main, f"峰位精修晶胞失败：{user_error_text(err)}")
            return None
        self._show_cell_refine(result, row)
        self._set_status(
            f"峰位精修晶胞：{row.path.stem} {result.system}，"
            f"匹配 {len(result.matches)} 根，RMS {result.rms_deg:.4f}°")
        _log(self.main, f"峰位精修晶胞：{row.path.stem}（{result.system}）"
                        f" 匹配 {len(result.matches)} 根，"
                        f"RMS {result.rms_deg:.4f}°")
        return result

    def _show_cell_refine(self, result, row):
        old = getattr(self, "_cell_dialog", None)
        if old is not None:
            try:
                old.close()
            except Exception:                      # noqa: BLE001
                pass
        dlg = QDialog(self)
        dlg.setWindowTitle(f"峰位精修晶胞：{row.path.stem}")
        dlg.resize(760, 560)
        lay = QVBoxLayout(dlg)
        note = "结果不写回 CIF/候选，只作检查与交接。\n\n"
        text = note + result.report()
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(text)
        lay.addWidget(view, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        copy = QPushButton("复制报告")
        copy.clicked.connect(
            lambda: QApplication.clipboard().setText(text))
        buttons.addWidget(copy)

        def _save():
            stamp = time.strftime("%Y%m%d-%H%M%S")
            default = (paths.default_outputs_dir()
                       / f"cell_refine_{row.path.stem}_{stamp}.txt")
            path, _ = QFileDialog.getSaveFileName(
                dlg, "保存峰位精修晶胞报告", str(default), "文本 (*.txt)",
                options=QFileDialog.DontUseNativeDialog)
            if not path:
                return
            p = Path(path).expanduser()
            if p.is_dir():
                p = p / f"cell_refine_{row.path.stem}_{stamp}.txt"
            if p.suffix.lower() != ".txt":
                p = p.with_suffix(".txt")
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text, encoding="utf-8")
            except OSError as err:                 # noqa: BLE001
                QMessageBox.critical(dlg, "保存失败", user_error_text(err))
                return
            self._set_status(f"已保存报告：{p.name}")

        save = QPushButton("保存报告…")
        save.clicked.connect(_save)
        buttons.addWidget(save)
        close = QPushButton("关闭")
        close.clicked.connect(dlg.close)
        buttons.addWidget(close)
        lay.addLayout(buttons)
        self._cell_dialog = dlg
        dlg.show()

    def _send_to_refine(self):
        row = self._selected_row()
        if row is None:
            self._set_status("先在候选表里点一行")
            return
        curve_key = self.curve_combo.currentData()
        if str(curve_key or "").startswith("product:"):
            # 文件栏的 1D 产物：先读缓存开一张 1D 面板，再交给精修。
            source = self._product_sources.get(curve_key)
            if source is None:
                self._set_status("这条 1D 产物已失效，请重新选曲线")
                return
            from xrd_toolkit.gui.plot_views import _open_product_panel
            panel_key = _open_product_panel(self.main, source)
            if panel_key not in self.main.plot_docks:
                self._set_status("这条 1D 产物读不到了（被删了？）")
                return
            self.refresh_curves()
            idx = self.curve_combo.findData(panel_key)
            if idx >= 0:
                self.curve_combo.setCurrentIndex(idx)
            curve_key = panel_key
        if str(curve_key or "").startswith("file:") or self._selected_dock() is None:
            self._set_status("文件曲线没有对应的 1D 面板，不能直接送去精修")
            return
        self.main.phase_btn.setChecked(False)      # 退出物相模式
        self.main.refine_btn.setChecked(True)      # 进入精修模式
        editor = getattr(self.main, "refine_editor", None)
        if editor is None:
            self._set_status("精修工作台没打开")
            return
        editor.set_source(curve_key, str(row.path))
        _log(self.main, f"已把 {row.path.stem} 送去精修"
                        f"（{self.curve_combo.currentText()}）")

    def _write_phase_csv(self, path):
        """写候选表 CSV；返回实际路径（校验/建目录/原子替换）。

        路径是文件夹或没有后缀时自动补文件名 / .csv；先写同目录临时文件
        再 os.replace，避免半截文件，也把 EBADF 这类怪路径挡在 try 里。
        """
        p = Path(path).expanduser()
        if p.is_dir():
            stamp = time.strftime("%Y%m%d-%H%M%S")
            p = p / f"phase_screen_{stamp}.csv"
        if p.suffix.lower() not in (".csv", ".txt"):
            p = p.with_suffix(".csv")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
                fh.write("# XRD Toolkit phase screen\n")
                fh.write(f"# curve: {self.curve_combo.currentText()}\n")
                fh.write(f"# library: {self.lib_dir}\n")
                fh.write(
                    "# elements: "
                    f"{self.elements_edit.text().strip() or '(all)'}\n")
                fh.write("# wavelength_A: "
                         f"{float(self._last_wavelength or 0.0):.6f}\n")
                fh.write("# generated: "
                         f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                writer = csv.writer(fh)
                writer.writerow(["phase", "score", "coverage", "purity",
                                 "scale", "path"])
                for row in self.rows:
                    writer.writerow([
                        row.path.stem, f"{row.score:.6f}",
                        f"{row.coverage:.6f}", f"{row.purity:.6f}",
                        f"{row.scale:.6f}", str(row.path)])
            os.replace(tmp, p)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        return p

    def _export_results(self, path, *, fallback=True):
        """把候选表写成 CSV（path 必给；界面按钮走 _export_default）。"""
        if not self.rows:
            self._set_status("还没有可导出的筛相结果；先点 [开始筛相] 出候选")
            return
        try:
            actual = self._write_phase_csv(path)
        except OSError as err:
            if not fallback:
                self._set_status(f"导出失败：{user_error_text(err)}")
                return
            fallback_dir = paths.default_outputs_dir()
            stamp = time.strftime("%Y%m%d-%H%M%S")
            try:
                actual = self._write_phase_csv(
                    fallback_dir / f"phase_screen_{stamp}.csv")
            except OSError as err2:
                self._set_status(f"导出失败：{user_error_text(err2)}")
                return
            self._set_status(
                f"原位置不可用（{user_error_text(err)}），"
                f"已导出到 {actual}")
            _log(self.main,
                 f"筛相结果原位置写不进（{err}），已回退到 {actual}")
            return
        self._set_status(f"已导出 {actual.name}（{len(self.rows)} 行）")
        _log(self.main, f"筛相结果已导出：{actual}")

    def _export_default(self):
        """不弹保存框，直接写到默认输出目录。"""
        if not self.rows:
            self._set_status("还没有可导出的筛相结果；先点 [开始筛相] 出候选")
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self._export_results(
            paths.default_outputs_dir() / f"phase_screen_{stamp}.csv",
            fallback=False)

    def _set_status(self, text):
        self.status.setText(text)


def _section(text):
    label = QLabel(text)
    label.setStyleSheet("color: gray; font-weight: 600;")
    return label


def _labeled_row(text, widget):
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    row.addWidget(QLabel(text))
    row.addWidget(widget, 1)
    return row


class _PhaseSubWindow(QMdiSubWindow):
    """物相工作台的中央子窗口：× 关闭 = 退出物相模式，内容不销毁。"""

    def __init__(self, window):
        super().__init__()
        self._window = window
        self.panel_key = "Phase|workbench"

    def closeEvent(self, event):
        window = self._window
        if (not getattr(window, "_closing", False)
                and getattr(window, "phase_btn", None) is not None
                and window.phase_btn.isChecked()):
            window.phase_btn.setChecked(False)     # toggled → 退出物相模式
        event.ignore()
        self.hide()
