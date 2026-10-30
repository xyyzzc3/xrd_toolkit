"""全谱精修中央工作台（2026-10-30）。

打开方式：工具栏 [精修]。内容和 Mask 工作台一样装进中央 MDI 子窗口
（不是独立窗口，也不占参数面板的一页）：精修要同时看曲线、参数表和
差值图，塞进窄面板会太挤。进入时收起参数坞和日志坞，退出只隐藏
子窗口，曲线/结果都留着。

布局：

    顶部  曲线 [下拉] [用当前图]   结构 [下拉] [打开 CIF…]   λ
    ┌──────────────┬────────────────────────────────────┐
    │ 精修选项      │ 观测 / 计算 / 背景                │
    │ 背景          │ obs − calc 差值                    │
    │ [开始精修]    │ 布拉格刻度                         │
    │ Rwp           ├────────────────────────────────────┤
    │               │ 参数表 | 报告                      │
    └──────────────┴────────────────────────────────────┘
    状态行

数据来源：选中的 1D 面板的**积分曲线**（`last_tth` / `last_intensity`，
不是画出来的处理链结果）。背景由工作台自己估计，避免和处理页的扣背景
叠成二次相减。波长取当前几何配置条目。

精修本身走 `simulation.fit.fit_pattern`（与命令行脚本同一个引擎），
放在后台线程跑，界面不卡。
"""
import time
import time
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
    QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QListWidget,
    QMdiSubWindow, QMenu, QPlainTextEdit, QPushButton, QScrollArea,
    QSizePolicy, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget)

from xrd_toolkit import paths
from xrd_toolkit.gui.panel_state import (
    _collect_geometry, _data_snapshot, _log, _set_focus)
from xrd_toolkit.gui.tasks import BackgroundTask, user_error_text
from xrd_toolkit.services.background import estimate_baseline_sliding

# 图配色与 simulation.plots.plot_fit 一致：观测灰、计算蓝、背景红、
# 布拉格刻度按相轮换。
_OBS_COLOR = "#333333"
_CALC_COLOR = "#1f5fa8"
_BG_COLOR = "#d0544e"
_PHASE_COLORS = ("#1f5fa8", "#e08a1e", "#2a9d8f", "#8e5aa8")


def _section(text):
    """左栏的小节标题（灰字加粗，与 1D 页的「峰表」「峰形拟合」同款）。"""
    label = QLabel(text)
    label.setStyleSheet("color: gray; font-weight: 600;")
    return label


def _fmt(value):
    """参数表里的数字格式：None 画破折号，其余尽量用 6 位有效数字。"""
    if value is None:
        return "—"
    try:
        return f"{float(value):.6g}"
    except (TypeError, ValueError):
        return str(value)


def _cell_free(structure, free):
    """按晶系裁剪晶胞参数：立方只留 a；非正交晶系不精修晶胞。

    返回 (新的 free 列表, 给用户看的说明或 None)。引擎只支持
    正交（a/b/c）与立方（a）两种；六方/单斜等直接跳过，不然
    fit_pattern 会抛错。
    """
    if not any(name in free for name in ("a", "b", "c")):
        return list(free), None
    rest = [name for name in free if name not in ("a", "b", "c")]
    lat = structure.lattice
    ortho = all(abs(float(ang) - 90.0) < 1e-3
                for ang in (lat.alpha, lat.beta, lat.gamma))
    if not ortho:
        return rest, "该晶系不支持晶胞精修，已跳过 a/b/c"
    same = (abs(float(lat.a) - float(lat.b)) < 1e-3
            and abs(float(lat.b) - float(lat.c)) < 1e-3)
    if same:
        return rest + ["a"], "立方晶胞只精修 a（b = c = a）"
    return list(free), None


def _fit_worker(cif_path, tth, y, wavelength, free, lebail, bg_window,
                max_nfev, extra_paths=(), extra_lebail=True,
                instrument=None):
    """后台线程里的纯计算：读 CIF → 估背景 → 全谱最小二乘。

    不能碰任何界面控件（见 gui.tasks 的线程规则）。pymatgen 属可选
    依赖，缺了要把它自己的安装提示原样交给用户，所以这里把 ImportError
    转成 RuntimeError（ImportError 会被当成"程序内部错误"包装掉）。

    extra_paths 是额外相 CIF（如 Al 集流体）；instrument 是固定的
    仪器参数字典（zero/U/V/W/X/Y），给定时按 initial 传进去、调用方
    已把它们从 free 里拿掉。

    返回 (FitResult, 说明或 None)：说明用来告诉用户晶胞参数被按晶系
    裁剪过（如立方晶胞只精修 a）。
    """
    try:
        from xrd_toolkit.simulation import b_iso_from_cif, fit_pattern, load_structure
    except ImportError as err:
        raise RuntimeError(str(err)) from err

    structure = load_structure(str(cif_path))
    free, note = _cell_free(structure, free)
    b_iso = b_iso_from_cif(str(cif_path)) or None
    background = None
    if bg_window > 0:
        background = estimate_baseline_sliding(tth, y, float(bg_window))
    extras = []
    for path in extra_paths:
        extras.append({
            "structure": load_structure(str(path)),
            "label": Path(path).stem.split("_")[0],
            "b_iso": b_iso_from_cif(str(path)) or None,
            "refine_intensities": bool(extra_lebail),
        })
    kwargs = {
        "wavelength": float(wavelength),
        "b_iso": b_iso,
        "background": background,
        "weights": "poisson",
        "refine_intensities": bool(lebail),
        "free": tuple(free),
        "max_nfev": int(max_nfev),
    }
    if extras:
        kwargs["extra_phases"] = extras
    if instrument:
        kwargs["initial"] = dict(instrument)
    if background is not None:
        kwargs["background_knots"] = 0.25
        kwargs["background_smooth"] = 0.1
    if lebail:
        # Le Bail 下晶胞不参与非线性优化，但要按 initial 的晶胞重建峰位
        kwargs["cell_refine"] = True
    return fit_pattern(structure, tth, y, **kwargs), note


class RefineWorkbench(QWidget):
    """精修工作台的界面与流程（由主窗口装进 MDI 子窗口）。"""

    def __init__(self, main_window):
        super().__init__()
        self.main = main_window
        self.setObjectName("refine_workbench")

        self.result = None
        self._task = None
        self._extra_cifs = []
        self._extra_phase_paths = []
        self._instrument = None          # {"params": {...}, "wavelength": Å}
        self._last_wavelength = None

        self._build_ui()
        self.refresh_curves()
        self.refresh_cifs()
        self._draw_placeholder()

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
        self.curve_combo.currentIndexChanged.connect(self._on_curve_changed)
        top.addWidget(self.curve_combo, 1)
        self.use_focus_btn = QPushButton("用当前图")
        self.use_focus_btn.setToolTip("把焦点所在的 1D 图选进左边的曲线框")
        self.use_focus_btn.clicked.connect(self._use_focus_curve)
        top.addWidget(self.use_focus_btn)
        top.addSpacing(12)
        top.addWidget(QLabel("结构"))
        self.cif_combo = QComboBox()
        self.cif_combo.setMinimumWidth(220)
        top.addWidget(self.cif_combo, 1)
        self.open_cif_btn = QPushButton("打开 CIF…")
        self.open_cif_btn.setToolTip("从任意位置加入一张 CIF 结构")
        self.open_cif_btn.clicked.connect(self._open_cif)
        top.addWidget(self.open_cif_btn)
        self.lambda_label = QLabel("λ")
        self.lambda_label.setToolTip("波长取参数面板顶部选中的几何配置")
        top.addWidget(self.lambda_label)
        root.addLayout(top)

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

        left_lay.addWidget(_section("数据"))
        self.range_label = QLabel("还没有选曲线")
        self.range_label.setWordWrap(True)
        self.range_label.setStyleSheet("color: gray;")
        left_lay.addWidget(self.range_label)

        left_lay.addWidget(_section("背景"))
        bg_row = QHBoxLayout()
        bg_row.setSpacing(4)
        self.bg_combo = QComboBox()
        self.bg_combo.addItem("自动基线", "auto")
        self.bg_combo.addItem("无", "none")
        bg_row.addWidget(self.bg_combo, 1)
        bg_row.addWidget(QLabel("窗口"))
        self.bg_window = QDoubleSpinBox()
        self.bg_window.setRange(0.05, 2.0)
        self.bg_window.setSingleStep(0.05)
        self.bg_window.setDecimals(2)
        self.bg_window.setValue(0.2)
        self.bg_window.setSuffix(" °")
        self.bg_window.setToolTip("滑动窗口宽度：越小越贴峰底，越大越平缓")
        bg_row.addWidget(self.bg_window)
        left_lay.addLayout(bg_row)

        left_lay.addWidget(_section("精修选项"))
        self.chk_cell = QCheckBox("晶胞 a/b/c")
        self.chk_cell.setChecked(True)
        self.chk_cell.setToolTip("只对正交或立方晶胞开放；其他晶系请关掉")
        self.chk_instr = QCheckBox("仪器峰形 U/V/W/X/Y")
        self.chk_instr.setChecked(True)
        self.chk_instr.setToolTip("Caglioti 与 Lorentzian 峰宽项")
        self.chk_micro = QCheckBox("样品微结构 Size/Mustrain")
        self.chk_micro.setChecked(True)
        self.chk_b = QCheckBox("温度因子 B")
        self.chk_b.setToolTip("从 CIF 的 Uiso 起步；Le Bail 模式下不可用")
        self.chk_lebail = QCheckBox("Le Bail（强度自由）")
        self.chk_lebail.setToolTip(
            "每条峰的强度单独解出，不看结构因子；适合只看峰形与晶胞")
        for widget in (self.chk_cell, self.chk_instr, self.chk_micro,
                       self.chk_b, self.chk_lebail):
            left_lay.addWidget(widget)

        left_lay.addWidget(_section("仪器"))
        self.chk_fix_instrument = QCheckBox("固定仪器（用上次 LaB₆ 基线）")
        self.chk_fix_instrument.setEnabled(False)
        self.chk_fix_instrument.setToolTip(
            "先精修一张 LaB₆ 并点 [存为仪器基线]；之后精修样品时固定 "
            "zero/U/V/W/X/Y，只精修样品的晶胞与微结构")
        left_lay.addWidget(self.chk_fix_instrument)
        self.instrument_label = QLabel("仪器基线：未保存")
        self.instrument_label.setWordWrap(True)
        self.instrument_label.setStyleSheet("color: gray;")
        left_lay.addWidget(self.instrument_label)
        self.save_instrument_btn = QPushButton("存为仪器基线")
        self.save_instrument_btn.setEnabled(False)
        self.save_instrument_btn.setToolTip(
            "把这次 LaB₆ 精修得到的仪器峰形参数存起来，供样品精修复用")
        self.save_instrument_btn.clicked.connect(self._save_instrument_baseline)
        left_lay.addWidget(self.save_instrument_btn)
        self.chk_fix_instrument.toggled.connect(
            lambda on: self.chk_instr.setEnabled(not on))

        left_lay.addWidget(_section("额外相"))
        self.extra_list = QListWidget()
        self.extra_list.setMaximumHeight(72)
        self.extra_list.setToolTip("额外相（如 Al 集流体）：峰位与峰形跟随"
                                   "主相，强度默认逐反射自由")
        left_lay.addWidget(self.extra_list)
        extra_row = QHBoxLayout()
        extra_row.setSpacing(4)
        self.add_extra_btn = QPushButton("加 CIF…")
        self.add_extra_btn.clicked.connect(self._choose_extra_cif)
        self.remove_extra_btn = QPushButton("移除")
        self.remove_extra_btn.clicked.connect(self._remove_extra_cif)
        extra_row.addWidget(self.add_extra_btn, 1)
        extra_row.addWidget(self.remove_extra_btn, 1)
        left_lay.addLayout(extra_row)
        self.chk_extra_lebail = QCheckBox("额外相强度自由（Le Bail）")
        self.chk_extra_lebail.setChecked(True)
        self.chk_extra_lebail.setToolTip(
            "第二相（如 Al 箔）织构强时，峰强交给数据逐反射解出")
        left_lay.addWidget(self.chk_extra_lebail)

        self.run_btn = QPushButton("开始精修")
        self.run_btn.setToolTip("用选中的 1D 曲线和 CIF 做全谱最小二乘拟合")
        self.run_btn.clicked.connect(self._start_fit)
        left_lay.addWidget(self.run_btn)

        save_row = QHBoxLayout()
        save_row.setSpacing(4)
        self.save_btn = QPushButton("保存图片…")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save_figure)
        self.copy_btn = QPushButton("复制报告")
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._copy_report)
        save_row.addWidget(self.save_btn, 1)
        save_row.addWidget(self.copy_btn, 1)
        left_lay.addLayout(save_row)
        self.adopt_btn = QPushButton("采纳为图面板")
        self.adopt_btn.setEnabled(False)
        self.adopt_btn.setToolTip(
            "把精修结果开成一张普通 1D 图：观测/计算/背景叠在一起，"
            "可保存、参与对比")
        self.adopt_btn.clicked.connect(self._adopt_panel)
        left_lay.addWidget(self.adopt_btn)

        self.rwp_label = QLabel("Rwp = —")
        self.rwp_label.setStyleSheet("font-size: 15px; font-weight: 600;")
        left_lay.addWidget(self.rwp_label)
        hint = QLabel("精修用这条 1D 曲线的积分数据，背景由工作台自己估计；"
                      "波长取顶部选中的几何配置。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        left_lay.addWidget(hint)
        left_lay.addStretch(1)
        left_scroll.setWidget(left)
        split.addWidget(left_scroll)

        right = QSplitter(Qt.Vertical)
        self.fig = Figure(figsize=(6.6, 4.4))
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right.addWidget(self.canvas)

        self.tabs = QTabWidget()
        self.param_table = QTableWidget(0, 4)
        self.param_table.setHorizontalHeaderLabels(
            ["参数", "初值", "拟合值", "esd"])
        self.param_table.verticalHeader().setVisible(False)
        self.param_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.param_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.param_table.setAlternatingRowColors(True)
        self.param_table.setShowGrid(False)
        self.param_table.setFrameShape(QFrame.NoFrame)
        self.param_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch)
        self.param_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.param_table.customContextMenuRequested.connect(self._param_menu)
        self.report_view = QPlainTextEdit()
        self.report_view.setReadOnly(True)
        self.report_view.setPlaceholderText("精修完成后这里显示完整报告")
        self.tabs.addTab(self.param_table, "参数表")
        self.tabs.addTab(self.report_view, "报告")
        right.addWidget(self.tabs)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 1)
        # 参数表至少露出五六行：图上三栏 + 残差 + 刻度已经很高，不显式
        # 分配的话分给下方标签页的只剩一条缝。
        right.setSizes([420, 220])
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([270, 760])

        self.status = QLabel("选一条 1D 曲线和一张 CIF，点 [开始精修]")
        self.status.setStyleSheet("color: gray;")
        root.addWidget(self.status)

    # ── 曲线与结构列表 ───────────────────────────────────────
    def refresh_curves(self):
        """把当前打开、已经算完的 1D 面板列进下拉框（保留原选择）。"""
        keep = self.curve_combo.currentData()
        self.curve_combo.blockSignals(True)
        self.curve_combo.clear()
        for key, dock in getattr(self.main, "plot_docks", {}).items():
            if not str(key).startswith("1D|"):
                continue
            if getattr(dock, "last_tth", None) is None:
                continue
            title = dock.windowTitle() or str(key).split("|", 1)[-1]
            self.curve_combo.addItem(title, key)
        self.curve_combo.blockSignals(False)
        idx = self.curve_combo.findData(keep) if keep else -1
        if idx < 0 and self.curve_combo.count():
            idx = 0
        if idx >= 0:
            self.curve_combo.setCurrentIndex(idx)
        self._on_curve_changed()

    def refresh_cifs(self):
        """列出仓库自带的参考 CIF，外加用户自己打开过的。"""
        keep = self.cif_combo.currentData()
        self.cif_combo.blockSignals(True)
        self.cif_combo.clear()
        seen = []

        def add(path):
            path = Path(path)
            if not path.is_file() or path in seen:
                return
            seen.append(path)
            self.cif_combo.addItem(path.stem, str(path))

        base = paths.ROOT / "data" / "structures"
        if base.is_dir():
            for path in sorted(base.glob("*.cif")):
                add(path)
        for path in self._extra_cifs:
            add(path)
        self.cif_combo.blockSignals(False)
        idx = self.cif_combo.findData(keep) if keep else -1
        if idx >= 0:
            self.cif_combo.setCurrentIndex(idx)

    def set_source(self, curve_key=None, cif_path=None):
        """外部入口（物相工作台的 [送去精修]）预选曲线与 CIF。

        曲线按面板 key 找；CIF 不在当前列表里就加进「用户打开过的」
        那一份再刷新——物相工作台可以用任意相库目录。
        """
        if curve_key:
            idx = self.curve_combo.findData(curve_key)
            if idx >= 0:
                self.curve_combo.setCurrentIndex(idx)
        if cif_path:
            path = Path(cif_path)
            idx = self.cif_combo.findData(str(path))
            if idx < 0:
                if path not in self._extra_cifs:
                    self._extra_cifs.append(path)
                self.refresh_cifs()
                idx = self.cif_combo.findData(str(path))
            if idx >= 0:
                self.cif_combo.setCurrentIndex(idx)
        self._set_status("已选好曲线与结构，点 [开始精修]")

    def _open_cif(self):
        start = paths.ROOT / "data" / "structures"
        path, _ = QFileDialog.getOpenFileName(
            self, "打开 CIF", str(start),
            "CIF (*.cif);;所有文件 (*)")
        if not path:
            return
        chosen = Path(path)
        if chosen not in self._extra_cifs:
            self._extra_cifs.append(chosen)
        self.refresh_cifs()
        idx = self.cif_combo.findData(str(chosen))
        if idx >= 0:
            self.cif_combo.setCurrentIndex(idx)
        self._set_status(f"已加入结构 {chosen.name}")

    # ── 额外相与仪器基线 ────────────────────────────────────
    def add_extra_phase(self, path):
        """加入一张额外相 CIF（如 Al 集流体）；重复加入返回 False。"""
        path = Path(path)
        if path in self._extra_phase_paths:
            return False
        self._extra_phase_paths.append(path)
        self.extra_list.addItem(path.stem)
        item = self.extra_list.item(self.extra_list.count() - 1)
        item.setToolTip(str(path))
        self._set_status(f"已加入额外相 {path.stem}")
        return True

    def _choose_extra_cif(self):
        start = paths.ROOT / "data" / "structures"
        path, _ = QFileDialog.getOpenFileName(
            self, "加入额外相 CIF", str(start),
            "CIF (*.cif);;所有文件 (*)")
        if path:
            self.add_extra_phase(path)

    def _remove_extra_cif(self):
        row = self.extra_list.currentRow()
        if row < 0:
            return
        self.extra_list.takeItem(row)
        del self._extra_phase_paths[row]

    def _update_instrument_label(self):
        if self._instrument is None:
            self.instrument_label.setText("仪器基线：未保存")
            return
        p = self._instrument["params"]
        self.instrument_label.setText(
            "仪器基线：zero {zero:.4f}°，U {u:.3g}，V {v:.3g}，"
            "W {w:.3g}，X {x:.3g}，Y {y:.3g}（λ {lam:.4f} Å）".format(
                zero=p.get("zero", float("nan")),
                u=p.get("u", float("nan")),
                v=p.get("v", float("nan")),
                w=p.get("w", float("nan")),
                x=p.get("x", float("nan")),
                y=p.get("y", float("nan")),
                lam=self._instrument["wavelength"]))

    def _save_instrument_baseline(self):
        """把这次 LaB₆ 精修的仪器峰形参数存成基线。"""
        if self.result is None:
            self._set_status("先精修一张 LaB₆ 再存仪器基线")
            return
        cif = self.cif_combo.currentData() or ""
        if "lab6" not in Path(cif).name.lower():
            self._set_status("当前结构不是 LaB₆，仪器基线要从 LaB₆ 精修存")
            return
        names = ("zero", "u", "v", "w", "x", "y")
        params = {name: float(self.result.params[name]) for name in names
                  if name in self.result.params}
        self._instrument = {
            "params": params,
            "wavelength": float(self._last_wavelength or 0.0),
        }
        self._update_instrument_label()
        self.chk_fix_instrument.setEnabled(True)
        self.chk_fix_instrument.setChecked(True)
        self._set_status("仪器基线已保存，下次精修样品可用「固定仪器」")

    def _fixed_instrument(self, wavelength):
        """勾了「固定仪器」就返回参数字典；波长不符返回 "mismatch"。"""
        if not self.chk_fix_instrument.isChecked() or self._instrument is None:
            return None
        stored = float(self._instrument["wavelength"])
        if abs(stored - float(wavelength)) > 0.01:
            return "mismatch"
        return dict(self._instrument["params"])

    def _selected_dock(self):
        key = self.curve_combo.currentData()
        if not key:
            return None
        return getattr(self.main, "plot_docks", {}).get(key)

    def _use_focus_curve(self):
        key = getattr(self.main, "focus_panel", None)
        idx = self.curve_combo.findData(key)
        if idx < 0:
            self._set_status("当前焦点不是 1D 图；先在文件栏双击一张 1D 产物")
            return
        self.curve_combo.setCurrentIndex(idx)

    def _on_curve_changed(self):
        dock = self._selected_dock()
        tth = getattr(dock, "last_tth", None) if dock is not None else None
        if tth is None:
            self.range_label.setText("还没有选曲线")
        else:
            tth = np.asarray(tth, dtype=float)
            self.range_label.setText(
                f"2θ {tth.min():.3f}–{tth.max():.3f}°，{tth.size} 点")
        try:
            wavelength = _collect_geometry(self.main)["wavelength_m"] * 1e10
            self.lambda_label.setText(f"λ = {wavelength:.4f} Å")
        except (KeyError, TypeError):
            self.lambda_label.setText("λ")

    # ── 精修 ────────────────────────────────────────────────
    def _free_params(self):
        free = ["zero"]
        if self.chk_instr.isChecked():
            free += ["u", "v", "w", "x", "y"]
        if self.chk_cell.isChecked():
            free += ["a", "b", "c"]
        if self.chk_micro.isChecked():
            free += ["size_um", "mustrain"]
        if self.chk_b.isChecked() and not self.chk_lebail.isChecked():
            free.append("b_iso")
        return free

    def _start_fit(self):
        if self._task is not None:
            self._set_status("上一轮精修还在跑，请稍候")
            return
        dock = self._selected_dock()
        if dock is None or getattr(dock, "last_tth", None) is None:
            self._set_status("先选一条已经算完的 1D 曲线")
            return
        cif_path = self.cif_combo.currentData()
        if not cif_path:
            self._set_status("先选一张 CIF 结构")
            return
        tth = np.asarray(dock.last_tth, dtype=float).copy()
        y = np.asarray(dock.last_intensity, dtype=float).copy()
        if tth.size != y.size or tth.size < 20:
            self._set_status("这条曲线点太少或长度不一致，无法精修")
            return
        try:
            wavelength = float(
                _collect_geometry(self.main)["wavelength_m"] * 1e10)
        except (KeyError, TypeError):
            self._set_status("当前几何配置没有波长，先去校准页选一条配置")
            return
        free = self._free_params()
        lebail = self.chk_lebail.isChecked()
        bg_window = (float(self.bg_window.value())
                     if self.bg_combo.currentData() == "auto" else 0.0)
        fixed = self._fixed_instrument(wavelength)
        if fixed == "mismatch":
            stored = self._instrument["wavelength"]
            self._set_status(
                f"仪器基线来自 λ {stored:.4f} Å，与当前 λ {wavelength:.4f} Å "
                f"不符；请取消「固定仪器」再精修")
            return
        if fixed:
            free = [name for name in free
                    if name not in ("zero", "u", "v", "w", "x", "y")]
        self._last_wavelength = wavelength
        extra_paths = tuple(self._extra_phase_paths)
        extra_lebail = self.chk_extra_lebail.isChecked()

        self.run_btn.setEnabled(False)
        self._set_status("精修中，请稍候…")
        _log(self.main, f"精修：{Path(cif_path).name} → "
                        f"{self.curve_combo.currentText()}")

        task = None

        def done(payload):
            self.main._tasks.remove(task)
            self._task = None
            self.run_btn.setEnabled(True)
            result, note = payload
            self._on_fit_done(result, note)

        def error(message):
            self.main._tasks.remove(task)
            self._task = None
            self.run_btn.setEnabled(True)
            self._set_status(f"精修失败：{message}")
            _log(self.main, f"精修失败：{message}")

        task = BackgroundTask(
            _fit_worker, cif_path, tth, y, wavelength, free, lebail,
            bg_window, 800, extra_paths, extra_lebail, fixed,
            on_done=done, on_error=error)
        self._task = task
        self.main._tasks.append(task)
        task.start()

    def _on_fit_done(self, result, note=None):
        self.result = result
        self._draw(result)
        self._fill_params(result)
        self.report_view.setPlainText(result.report())
        self.rwp_label.setText(
            f"Rwp = {result.rwp:.3f}%   Rp = {result.rp:.3f}%")
        self.save_btn.setEnabled(True)
        self.copy_btn.setEnabled(True)
        self.adopt_btn.setEnabled(True)
        cif = self.cif_combo.currentData() or ""
        self.save_instrument_btn.setEnabled(
            "lab6" in Path(cif).name.lower())
        self.tabs.setCurrentIndex(0)
        suffix = f"；{note}" if note else ""
        self._set_status(
            f"精修完成：{result.n_obs} 点 / {result.n_params} 参数"
            f"（{result.message}）{suffix}")
        _log(self.main, f"精修完成：Rwp {result.rwp:.3f}%"
                        f"（{self.cif_combo.currentText()}）{suffix}")

    # ── 画图与结果表 ────────────────────────────────────────
    def _draw_placeholder(self):
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        ax.set_axis_off()
        ax.text(0.5, 0.55, "全谱精修", ha="center", va="center",
                fontsize=15, color="#666666", transform=ax.transAxes)
        ax.text(0.5, 0.44, "选一条 1D 曲线和一张 CIF，点 [开始精修]",
                ha="center", va="center", fontsize=10, color="#888888",
                transform=ax.transAxes)
        self.canvas.draw_idle()

    def _bragg_rows(self, result):
        rows = []
        peak_tth = getattr(result, "peak_tth", None)
        if peak_tth is not None and len(peak_tth):
            rows.append((result.phase_label or "主相",
                         np.asarray(peak_tth, dtype=float)))
        for label, pos in (getattr(result, "extra_peak_tth", None) or {}).items():
            rows.append((str(label), np.asarray(pos, dtype=float)))
        return rows

    def _draw(self, result):
        rows = self._bragg_rows(result)
        self.fig.clear()
        gs = self.fig.add_gridspec(3, 1, height_ratios=[3.2, 0.9, 0.45],
                                   hspace=0.06)
        ax = self.fig.add_subplot(gs[0])
        axr = self.fig.add_subplot(gs[1], sharex=ax)
        axb = self.fig.add_subplot(gs[2], sharex=ax)
        ax.tick_params(labelbottom=False)
        axr.tick_params(labelbottom=False)

        ax.plot(result.tth, result.y_obs, ".", ms=1.6, color=_OBS_COLOR,
                label="观测")
        ax.plot(result.tth, result.y_calc, "-", lw=1.1, color=_CALC_COLOR,
                label="计算")
        background = getattr(result, "background", None)
        if background is not None and np.size(background):
            ax.plot(result.tth, background, "--", lw=0.9, color=_BG_COLOR,
                    label="背景")
        ax.set_ylabel("counts", fontsize=8)
        ax.tick_params(labelsize=7)
        ax.grid(alpha=0.25, linestyle=":")
        ax.legend(fontsize=8, loc="best")
        ax.set_title(
            f"{result.phase_label or ''}   Rwp = {result.rwp:.2f}%",
            fontsize=10)

        axr.plot(result.tth, result.residual, ".", ms=1.4, color="#555555")
        axr.axhline(0.0, color="#999999", lw=0.8)
        axr.set_ylabel("obs − calc", fontsize=8)
        axr.tick_params(labelsize=7)
        axr.grid(alpha=0.25, linestyle=":")

        if rows:
            axb.set_yticks(range(len(rows)))
            axb.set_yticklabels([row[0] for row in rows], fontsize=7)
            for i, (_label, pos) in enumerate(rows):
                color = _PHASE_COLORS[i % len(_PHASE_COLORS)]
                axb.plot(pos, np.full(len(pos), i), "|", ms=10, mew=1.0,
                         color=color)
            axb.set_ylim(-0.6, len(rows) - 0.4)
        else:
            axb.set_yticks([])
        axb.tick_params(axis="x", labelsize=7)
        axb.set_xlabel("2θ (°)", fontsize=8)

        # 共享 x 轴 + 三行网格与 tight_layout 不兼容（会报警告），
        # 直接给边距。
        self.fig.subplots_adjust(left=0.11, right=0.98, top=0.93, bottom=0.10)
        self.canvas.draw_idle()

    def _fill_params(self, result):
        skip = {"scale", "size_mix", "strain_mix"}
        rows = []
        for name, value in result.params.items():
            if name in skip or name.startswith("intensity_a"):
                continue
            # 额外相只保留 scale（Al:scale）；逐反射强度/微结构不进表
            if ":" in name and not name.endswith(":scale"):
                continue
            rows.append((name, value))
        table = self.param_table
        table.setRowCount(len(rows))
        for i, (name, value) in enumerate(rows):
            init = result.initial.get(name)
            esd = result.stderr.get(name)
            for col, text in enumerate(
                    (name, _fmt(init), _fmt(value), _fmt(esd))):
                item = QTableWidgetItem(text)
                if col:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(i, col, item)

    def _param_tsv(self, selected_only=False):
        """参数表的 TSV：表头 + 行（右键菜单与测试共用）。"""
        model = self.param_table.selectionModel()
        rows = (sorted({i.row() for i in model.selectedRows()})
                if selected_only and model is not None
                else list(range(self.param_table.rowCount())))
        lines = ["参数\t初值\t拟合值\tesd"]
        for row in rows:
            values = []
            for col in range(self.param_table.columnCount()):
                item = self.param_table.item(row, col)
                values.append(item.text() if item is not None else "")
            lines.append("\t".join(values))
        return "\n".join(lines)

    def _copy_params(self, selected_only=False):
        text = self._param_tsv(selected_only)
        n = max(0, len(text.splitlines()) - 1)
        if n == 0:
            self._set_status("参数表没有可复制的行")
            return 0
        QApplication.clipboard().setText(text)
        _log(self.main, f"已复制参数表 {n} 行")
        return n

    def _param_menu(self, pos):
        menu = QMenu(self.param_table)
        act_sel = menu.addAction("复制选中行")
        act_all = menu.addAction("复制全部")
        chosen = menu.exec(self.param_table.viewport().mapToGlobal(pos))
        if chosen is act_sel:
            self._copy_params(True)
        elif chosen is act_all:
            self._copy_params(False)

    def _save_figure(self):
        if self.result is None:
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        default = paths.default_outputs_dir() / f"refine_{stamp}.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "保存精修图", str(default), "PNG (*.png);;PDF (*.pdf)")
        if not path:
            return
        try:
            self.fig.savefig(path, dpi=200)
        except Exception as err:                   # noqa: BLE001
            self._set_status(f"保存失败：{user_error_text(err)}")
            return
        self._set_status(f"已保存 {Path(path).name}")
        _log(self.main, f"精修图已保存：{path}")

    def _copy_report(self):
        if self.result is None:
            return
        QApplication.clipboard().setText(self.result.report())
        self._set_status("报告已复制到剪贴板")

    def _adopt_panel(self):
        """把精修结果开成一张普通 1D 图面板（参与保存/对比/导出）。

        面板数据 = 拟合用的那条曲线（观测），`dock.fit_overlay` 带
        计算线/背景/布拉格刻度，`_draw_1d` 每次重画都会把它叠回去。
        处理链在这个面板上默认全关——否则扣背景/平滑会把观测线改了，
        和计算线不在一个尺度上。
        """
        if self.result is None:
            self._set_status("先精修再采纳")
            return
        from xrd_toolkit.gui.plot.drawing import _draw_1d
        from xrd_toolkit.gui.plot.panel_build import _open_plot_panel
        result = self.result
        cif = self.cif_combo.currentData() or ""
        phase = Path(cif).stem or "fit"
        title = f"精修 {phase}  Rwp {result.rwp:.2f}%"
        key = f"1D|fit:{time.time_ns()}"
        dock = _open_plot_panel(self.main, "1D", key, title)
        snap = _data_snapshot(self.main)
        snap.update({
            "2θ 下限 (°)": float(np.min(result.tth)),
            "2θ 上限 (°)": float(np.max(result.tth)),
            "视图 2θ 下限 (°)": None,
            "视图 2θ 上限 (°)": None,
            "背景扣除模式": "off",
            "平滑曲线": False,
            "裁剪区间": [],
            "负值截断为 0": False,
        })
        dock.params_snapshot = snap
        dock.panel_file = None
        dock.panel_display = f"{phase}_fit"
        tth = np.asarray(result.tth, dtype=float).copy()
        y = np.asarray(result.y_obs, dtype=float).copy()
        dock.last_tth = tth
        dock.last_intensity = y
        bragg = [(label, pos, _PHASE_COLORS[i % len(_PHASE_COLORS)])
                 for i, (label, pos) in enumerate(self._bragg_rows(result))]
        dock.fit_overlay = {
            "sig": (int(tth.size), float(np.nansum(np.nan_to_num(y)))),
            "tth": tth,
            "y_calc": np.asarray(result.y_calc, dtype=float).copy(),
            "background": (None if result.background is None else
                           np.asarray(result.background, dtype=float).copy()),
            "bragg": bragg,
            "label": phase,
            "rwp": result.rwp,
        }
        dock.figure_saved = False
        _draw_1d(self.main, dock, tth, y)
        _set_focus(self.main, key, title)
        dock.raise_()
        self._set_status("已采纳为图面板；回分析模式就能看到")
        _log(self.main, f"精修结果已采纳为图面板：{title}")

    def _set_status(self, text):
        self.status.setText(text)


class _RefineSubWindow(QMdiSubWindow):
    """精修工作台的中央子窗口。

    × 关闭 = 退出精修模式（参数坞/日志坞恢复），但**不销毁内容**：
    选中的曲线、CIF 和上一次结果都留着，下次点 [精修] 原样继续。
    """

    def __init__(self, window):
        super().__init__()
        self._window = window
        self.panel_key = "Refine|workbench"

    def closeEvent(self, event):
        window = self._window
        if (not getattr(window, "_closing", False)
                and getattr(window, "refine_btn", None) is not None
                and window.refine_btn.isChecked()):
            window.refine_btn.setChecked(False)   # toggled → 退出精修模式
        event.ignore()
        self.hide()
