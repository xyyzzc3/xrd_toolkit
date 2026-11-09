"""原位三相批量中央工作台（2026-10-30）。

打开方式：工具栏 [原位]。与 [Mask]、[精修]、[物相] 同一套中央 MDI
子窗口：进入时收起参数坞和日志坞，退出只隐藏，选中的目录/参数/结果
都留着。

流程（引擎在 `simulation.insitu`，命令行 `scripts/fit_insitu.py` 共用）：

    选帧目录与 mask → [开始]
    → 后台准备（读三相 CIF + LaB₆ 定仪器，数秒）
    → 逐帧后台精修：读图 → 积分 → 三相拟合 → 结果进表
    → 右侧四个视图：趋势 / 汇总 / 单帧拟合 / 模型无关
    → 结束写 CSV 与 fit_summary.txt

    单帧拟合（2026-11-02）：双击表格行看这一帧的观测/计算/背景 +
    残差 + 布拉格刻度；[复制这一帧报告] 调 FitResult.report()。
    [保存当前图…] 会按脚本同尺寸重画一张再存（Qt 画布会被拉成窗口
    大小，直接存画布会低分辨率、挤在一起）。

相分数口径：f_rich = A_rich / (A_rich + A_poor)，A_p = scale_p ×
Σ(table.inten_p) × norm_p / 100，是**相对散射强度分数**；表里另有
`wt rich%` / `wt poor%`（Rietveld 重量分数，只对晶态相；Al 单列，
不进 LMFP 分母）。趋势图仍画 f_rich，两种口径别混着引用。
"""
import csv
import time
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDoubleSpinBox, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QMdiSubWindow, QMenu, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QSplitter, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from xrd_toolkit import paths
from xrd_toolkit.gui.panel_state import _log
from xrd_toolkit.gui.panel_widgets import _ElideLabel
from xrd_toolkit.gui.tasks import BackgroundTask, user_error_text
from xrd_toolkit.presets import PROJECT_PRESET

_COLUMNS = ("帧", "文件", "状态", "Rwp %", "f_rich",
            "wt rich%", "wt poor%", "rich a", "rich c", "poor a",
            "poor c", "拟合 s")


def _fmt(value, digits=3):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{value:.{digits}f}" if np.isfinite(value) else "—"


def _prepare_worker(config_name, rich_path, poor_path, al_path, mask_path,
                    lab6_path, max_rwp, first_nfev, frame_nfev, npt,
                    tth_min, tth_max):
    """后台准备：读结构 + LaB₆ 定仪器 + 读 mask，返回 InSituBatch。"""
    try:
        from xrd_toolkit.simulation.insitu import prepare_setup
    except ImportError as err:
        raise RuntimeError(str(err)) from err
    return prepare_setup(
        config_name, rich_path=rich_path, poor_path=poor_path,
        al_path=al_path, mask_path=mask_path, lab6_tif=lab6_path,
        max_rwp=max_rwp, first_nfev=first_nfev, frame_nfev=frame_nfev,
        npt=npt, tth_min=tth_min, tth_max=tth_max)


class InSituWorkbench(QWidget):
    """原位三相批量工作台。"""

    def __init__(self, main_window):
        super().__init__()
        self.main = main_window
        self.setObjectName("insitu_workbench")

        try:
            from xrd_toolkit.simulation.insitu import (
                DEFAULT_AL, DEFAULT_POOR, DEFAULT_RICH, LAB6_TIF,
                frames_in_dir)
            self._frames_in_dir = frames_in_dir
        except ImportError:
            DEFAULT_RICH = DEFAULT_POOR = DEFAULT_AL = None
            LAB6_TIF = None
            self._frames_in_dir = lambda *a, **k: []

        default_data = Path(PROJECT_PRESET["insitu_dir"]).expanduser()
        if not default_data.is_dir():
            repo_data = paths.ROOT / "data"
            default_data = (repo_data if repo_data.is_dir()
                            else Path.home())
        self.data_dir = default_data
        # 仓库样例只在开发机有；打包后由用户选自己的 LaB₆ 标样帧
        self.lab6_path = (Path(LAB6_TIF)
                          if LAB6_TIF is not None and Path(LAB6_TIF).is_file()
                          else None)
        # 与命令行同一个默认 mask（只在项目预设的几何下自动带；其它配置按不遮）
        default_mask = (paths.default_outputs_dir() / "masks"
                        / PROJECT_PRESET["insitu_mask_name"])
        self.mask_path = (default_mask
                          if (self.main.config_name
                              == PROJECT_PRESET["geometry"]
                              and default_mask.is_file()) else None)
        self.phase_paths = {
            "rich": Path(DEFAULT_RICH) if DEFAULT_RICH else None,
            "poor": Path(DEFAULT_POOR) if DEFAULT_POOR else None,
            "al": Path(DEFAULT_AL) if DEFAULT_AL else None,
        }
        self.outdir = paths.default_outputs_dir() / "insitu"
        self.frames = []
        self.batch = None
        # 批次开始那一刻的输入快照：报告用（跑完再改下拉框也不会写错）
        self._batch_meta = None
        self._task = None
        self._stop = False
        self._index = 0
        # 单帧拟合视图（2026-11-02）：double-click 表格行时填；只在当前
        # 会话有效（FitResult 曲线没有持久化）
        self._fit_frame = None
        self._fit_res = None
        self._fit_status = ""
        self._fit_file = ""
        self._fit_frich = float("nan")
        # 模型无关视图的按帧缓存：每次新帧/新批次清空
        self._mi_rows = None
        self._mi_len = -1

        self._build_ui()
        if self.mask_path is not None:
            self.mask_label.setText(f"mask：{self.mask_path.name}")
            self.mask_label.setToolTip(str(self.mask_path))
        self._refresh_data_label()
        self._refresh_phase_labels()
        self._refresh_lab6_label()
        self._draw_trends()

    # ── 界面组装 ─────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(QLabel("数据"))
        # 长路径用省略号标签，并给数据/掩膜各留上限；否则长路径会把
        # 右上角的 mask 灰字压到按钮底下（2026-11-03 用户报“遮盖灰字”）。
        self.data_label = _ElideLabel("", Qt.ElideMiddle)
        self.data_label.setStyleSheet("color: gray;")
        self.data_label.setMinimumWidth(0)
        self.data_label.setMaximumWidth(420)
        top.addWidget(self.data_label, 1)
        self.data_btn = QPushButton("选文件夹…")
        self.data_btn.clicked.connect(self._choose_data)
        top.addWidget(self.data_btn)
        top.addStretch(1)
        self.mask_label = _ElideLabel("mask：不遮", Qt.ElideMiddle)
        self.mask_label.setStyleSheet("color: gray;")
        self.mask_label.setMinimumWidth(170)
        self.mask_label.setMaximumWidth(260)
        # 必须用 Preferred：_ElideLabel 默认 Ignored，布局会把它的宽度
        # 算成 0，再被 minimumWidth 撑回 170，压到后面的按钮底下。
        self.mask_label.setSizePolicy(QSizePolicy.Preferred,
                                      QSizePolicy.Preferred)
        top.addWidget(self.mask_label)
        self.mask_btn = QPushButton("选 mask…")
        self.mask_btn.clicked.connect(self._choose_mask)
        top.addWidget(self.mask_btn)
        self.mask_clear_btn = QPushButton("清除")
        self.mask_clear_btn.clicked.connect(self._clear_mask)
        top.addWidget(self.mask_clear_btn)
        root.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        root.addWidget(split, 1)

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setMinimumWidth(250)
        left_scroll.setMaximumWidth(340)
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(2, 2, 8, 2)
        left_lay.setSpacing(6)

        left_lay.addWidget(_section("三相结构"))
        self.phase_labels = {}
        for key, text in (("rich", "富锂相"), ("poor", "贫锂相"),
                          ("al", "Al 参考相")):
            row = QHBoxLayout()
            row.setSpacing(4)
            row.addWidget(QLabel(text))
            label = QLabel("—")
            label.setStyleSheet("color: gray;")
            label.setWordWrap(True)
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Ignored,
                                QSizePolicy.Preferred)
            self.phase_labels[key] = label
            row.addWidget(label, 1)
            btn = QPushButton("换…")
            btn.clicked.connect(
                lambda _=False, k=key: self._choose_phase(k))
            row.addWidget(btn)
            left_lay.addLayout(row)

        lab6_row = QHBoxLayout()
        lab6_row.setSpacing(4)
        lab6_row.addWidget(QLabel("LaB₆ 标样图"))
        self.lab6_label = QLabel("未选")
        self.lab6_label.setStyleSheet("color: gray;")
        self.lab6_label.setWordWrap(True)
        self.lab6_label.setMinimumWidth(0)
        self.lab6_label.setSizePolicy(QSizePolicy.Ignored,
                                      QSizePolicy.Preferred)
        lab6_row.addWidget(self.lab6_label, 1)
        lab6_btn = QPushButton("换…")
        lab6_btn.clicked.connect(self._choose_lab6)
        lab6_row.addWidget(lab6_btn)
        left_lay.addLayout(lab6_row)

        left_lay.addWidget(_section("参数"))
        self.max_rwp = QDoubleSpinBox()
        self.max_rwp.setRange(2.0, 50.0)
        self.max_rwp.setDecimals(1)
        self.max_rwp.setSingleStep(1.0)
        self.max_rwp.setValue(20.0)
        self.max_rwp.setSuffix(" %")
        left_lay.addLayout(_labeled_row("最大 Rwp", self.max_rwp))
        self.first_nfev = QSpinBox()
        self.first_nfev.setRange(20, 2000)
        self.first_nfev.setValue(120)
        left_lay.addLayout(_labeled_row("首帧迭代", self.first_nfev))
        self.frame_nfev = QSpinBox()
        self.frame_nfev.setRange(10, 1000)
        self.frame_nfev.setValue(40)
        left_lay.addLayout(_labeled_row("后续帧迭代", self.frame_nfev))
        self.tth_min = QDoubleSpinBox()
        self.tth_min.setRange(0.0, 20.0)
        self.tth_min.setDecimals(2)
        self.tth_min.setSingleStep(0.1)
        self.tth_min.setValue(1.0)
        left_lay.addLayout(_labeled_row("2θ 下限", self.tth_min))
        self.tth_max = QDoubleSpinBox()
        self.tth_max.setRange(1.0, 40.0)
        self.tth_max.setDecimals(2)
        self.tth_max.setSingleStep(0.1)
        self.tth_max.setValue(8.0)
        left_lay.addLayout(_labeled_row("2θ 上限", self.tth_max))
        self.npt = QSpinBox()
        self.npt.setRange(200, 20000)
        self.npt.setSingleStep(100)
        self.npt.setValue(3000)
        left_lay.addLayout(_labeled_row("输出点数", self.npt))
        self.limit = QSpinBox()
        self.limit.setRange(0, 9999)
        self.limit.setSpecialValueText("全部")
        self.limit.setValue(0)
        self.limit.setToolTip("只跑前 N 帧；0 = 全部（先跑 3 帧看流程）")
        self.limit.valueChanged.connect(
            lambda _v: self._refresh_data_label())
        left_lay.addLayout(_labeled_row("帧数上限", self.limit))

        left_lay.addWidget(_section("输出"))
        self.out_label = QLabel(str(self.outdir))
        self.out_label.setStyleSheet("color: gray;")
        self.out_label.setWordWrap(True)
        self.out_label.setMinimumWidth(0)
        self.out_label.setSizePolicy(QSizePolicy.Ignored,
                                     QSizePolicy.Preferred)
        left_lay.addWidget(self.out_label)
        self.out_btn = QPushButton("选目录…")
        self.out_btn.clicked.connect(self._choose_outdir)
        left_lay.addWidget(self.out_btn)

        self.start_btn = QPushButton("开始")
        self.start_btn.clicked.connect(self._start)
        left_lay.addWidget(self.start_btn)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setToolTip("当前帧算完后停，已完成的帧保留")
        self.stop_btn.clicked.connect(self._stop_run)
        left_lay.addWidget(self.stop_btn)
        self.save_btn = QPushButton("保存当前图…")
        self.save_btn.setToolTip(
            "保存当前标签页的图（趋势 / 汇总 / 单帧拟合 / 模型无关）")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save_view)
        left_lay.addWidget(self.save_btn)
        self.copy_report_btn = QPushButton("复制这一帧报告")
        self.copy_report_btn.setToolTip(
            "复制单帧拟合的完整参数报告（先在表格里双击一帧）")
        self.copy_report_btn.setEnabled(False)
        self.copy_report_btn.clicked.connect(self._copy_fit_report)
        left_lay.addWidget(self.copy_report_btn)
        hint = QLabel("双击表格行看单帧拟合；f_rich 是相对散射强度分数，"
                      "看趋势用；wt rich% / wt poor% 是晶态相的 Rietveld "
                      "重量分数（Al 单列、不进两相分母）。每帧双起点，"
                      "约 15–20 s/帧；晶胞只在相占多数时可信。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        hint.setMinimumWidth(0)
        hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        left_lay.addWidget(hint)
        left_lay.addStretch(1)
        left_scroll.setWidget(left)
        split.addWidget(left_scroll)

        right = QSplitter(Qt.Vertical)
        self.view_tabs = QTabWidget()
        self.view_tabs.setObjectName("insitu_view_tabs")
        # figsize 与脚本产物一致：画布缩小显示，[保存当前图…] 仍按
        # 同一套尺寸/字体出报告图（2026-11-02）。
        self.fig = Figure(figsize=(11.0, 7.0))
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.summary_fig = Figure(figsize=(12.5, 8.0))
        self.summary_canvas = FigureCanvasQTAgg(self.summary_fig)
        self.fit_fig = Figure(figsize=(11.5, 7.6))
        self.fit_canvas = FigureCanvasQTAgg(self.fit_fig)
        self.mi_fig = Figure(figsize=(15.0, 4.6))
        self.mi_canvas = FigureCanvasQTAgg(self.mi_fig)
        for canvas in (self.canvas, self.summary_canvas,
                       self.fit_canvas, self.mi_canvas):
            canvas.setSizePolicy(QSizePolicy.Expanding,
                                 QSizePolicy.Expanding)
        self.view_tabs.addTab(self.canvas, "趋势")
        self.view_tabs.addTab(self.summary_canvas, "汇总")
        self.view_tabs.addTab(self.fit_canvas, "单帧拟合")
        self.view_tabs.addTab(self.mi_canvas, "模型无关")
        self.view_tabs.setCurrentIndex(0)
        self.view_tabs.currentChanged.connect(self._on_view_changed)
        right.addWidget(self.view_tabs)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(list(_COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch)
        self.table.setToolTip("双击一行看这一帧的三相拟合")
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        right.addWidget(self.table)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 1)
        right.setSizes([420, 220])
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([290, 760])

        self.status = QLabel("选帧目录，点 [开始]")
        self.status.setStyleSheet("color: gray;")
        root.addWidget(self.status)

    # ── 目录与文件 ──────────────────────────────────────────
    def _refresh_data_label(self):
        limit = int(self.limit.value()) if hasattr(self, "limit") else 0
        try:
            self.frames = list(
                self._frames_in_dir(self.data_dir, limit, 1))
        except OSError:
            self.frames = []
        self.data_label.setText(
            f"{self.data_dir}（{len(self.frames)} 帧）")
        self.data_label.setToolTip(str(self.data_dir))

    def _choose_data(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "选择原位帧目录", str(self.data_dir))
        if not chosen:
            return
        self.data_dir = Path(chosen)
        self._refresh_data_label()
        self._set_status(f"已选目录：{len(self.frames)} 帧")

    def _choose_mask(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 mask 文件", str(self.data_dir),
            "mask (*.npy *.edf);;所有文件 (*)")
        if not path:
            return
        self.mask_path = Path(path)
        self.mask_label.setText(f"mask：{self.mask_path.name}")
        self.mask_label.setToolTip(str(self.mask_path))

    def _clear_mask(self):
        self.mask_path = None
        self.mask_label.setText("mask：不遮")
        self.mask_label.setToolTip("")

    def _choose_phase(self, key):
        start = paths.structures_dir()
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 CIF 结构", str(start), "CIF (*.cif);;所有文件 (*)")
        if not path:
            return
        self.phase_paths[key] = Path(path)
        self._refresh_phase_labels()

    def _refresh_phase_labels(self):
        for key, label in self.phase_labels.items():
            path = self.phase_paths.get(key)
            name = path.stem if path else "未选"
            if len(name) > 18:
                name = name[:17] + "…"
            label.setText(name)
            label.setToolTip(str(path) if path else "")

    def _choose_lab6(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 LaB₆ 标样帧", str(self.data_dir),
            "衍射图 (*.tif *.tiff *.edf *.cbf);;所有文件 (*)")
        if not path:
            return
        self.lab6_path = Path(path)
        self._refresh_lab6_label()

    def _refresh_lab6_label(self):
        path = self.lab6_path
        name = path.name if path else "未选"
        if len(name) > 18:
            name = name[:17] + "…"
        self.lab6_label.setText(name)
        self.lab6_label.setToolTip(
            str(path) if path else
            "原位工作台需要一张 LaB₆ 标样帧来定仪器峰形")

    def _choose_outdir(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "选择输出目录", str(self.outdir))
        if not chosen:
            return
        self.outdir = Path(chosen)
        self.out_label.setText(str(self.outdir))

    # ── 运行 ────────────────────────────────────────────────
    def _start(self):
        if self._task is not None:
            self._set_status("上一轮还在跑，请稍候")
            return
        self._refresh_data_label()
        if not self.frames:
            self._set_status("先选一个包含 .tif 的文件夹")
            return
        for key, text in (("rich", "富锂相"), ("poor", "贫锂相"),
                          ("al", "Al 参考相")):
            if not self.phase_paths.get(key):
                self._set_status(f"先给{text}选一张 CIF")
                return
        if self.lab6_path is None:
            self._set_status("先选一张 LaB₆ 标样帧（用来定仪器峰形）")
            return
        self._stop = False
        self._index = 0
        self.batch = None
        self._batch_meta = {
            "data_dir": str(self.data_dir),
            "mask": str(self.mask_path) if self.mask_path else "",
            "phases": {key: str(path)
                       for key, path in self.phase_paths.items() if path},
            "lab6": str(self.lab6_path) if self.lab6_path else "",
        }
        self.table.setRowCount(0)
        self._clear_fit_view()
        self._mi_rows = None
        self._mi_len = -1
        self.copy_report_btn.setEnabled(False)
        self.view_tabs.setCurrentIndex(0)
        self._draw_trends()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.save_btn.setEnabled(False)
        self._set_status("准备中（读结构 + LaB₆ 定仪器）…")
        _log(self.main, f"原位批量：{self.data_dir}（{len(self.frames)} 帧）")

        task = None

        def done(batch):
            self.main._tasks.remove(task)
            self._task = None
            self.batch = batch
            self._on_prepared()

        def error(message):
            self.main._tasks.remove(task)
            self._task = None
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self._set_status(f"准备失败：{message}")
            _log(self.main, f"原位批量准备失败：{message}")

        task = BackgroundTask(
            _prepare_worker, self.main.config_name,
            str(self.phase_paths["rich"]), str(self.phase_paths["poor"]),
            str(self.phase_paths["al"]),
            (str(self.mask_path) if self.mask_path else None),
            str(self.lab6_path),
            float(self.max_rwp.value()), int(self.first_nfev.value()),
            int(self.frame_nfev.value()), int(self.npt.value()),
            float(self.tth_min.value()), float(self.tth_max.value()),
            on_done=done, on_error=error)
        self._task = task
        self.main._tasks.append(task)
        task.start()

    def _on_prepared(self):
        self.outdir.mkdir(parents=True, exist_ok=True)
        self._set_status("已就绪，开始逐帧精修…")
        self._start_next()

    def _start_next(self):
        if self._stop:
            self._finish("已停止")
            return
        if self._index >= len(self.frames):
            self._finish("完成")
            return
        path = self.frames[self._index]
        self._set_status(
            f"第 {self._index + 1}/{len(self.frames)} 帧：{path.name}")
        task = None

        def done(row):
            self.main._tasks.remove(task)
            self._task = None
            self._on_frame(row)

        def error(message):
            self.main._tasks.remove(task)
            self._task = None
            self._set_status(f"第 {self._index + 1} 帧失败：{message}")
            _log(self.main, f"原位批量第 {self._index + 1} 帧失败：{message}")
            self._finish("出错停止")

        task = BackgroundTask(
            self.batch.fit_frame, path, on_done=done, on_error=error)
        self._task = task
        self.main._tasks.append(task)
        task.start()

    def _on_frame(self, row):
        self._index += 1
        self._append_row(row)
        # 任意新帧都会让模型无关缓存失效；趋势图始终跟着更新
        self._mi_rows = None
        self._mi_len = -1
        self._draw_trends()
        if self.view_tabs.currentIndex() != 0:
            self._refresh_active_view()
        self._write_csv()
        self._set_status(
            f"第 {row['frame']}/{len(self.frames)} 帧：{row['status']}  "
            f"Rwp {_fmt(row['rwp'])}%  f_rich {_fmt(row['f_rich'])}  "
            f"wt {_fmt(row.get('wt_rich', float('nan')), 1)}%")
        self._start_next()

    def _stop_run(self):
        self._stop = True
        self.stop_btn.setEnabled(False)
        self._set_status("正在停止（当前帧算完后停）…")

    def _finish(self, reason):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._task = None
        if self.batch is not None and self.batch.rows:
            self._write_csv()
            try:
                from xrd_toolkit.simulation.insitu import write_summary
                write_summary(self.outdir, self.batch.rows,
                              self.batch.mask is not None)
            except OSError as err:                 # noqa: BLE001
                _log(self.main, f"摘要写入失败：{user_error_text(err)}")
        usable = (sum(1 for r in (self.batch.rows if self.batch else [])
                      if r.get("usable")))
        total = len(self.batch.rows) if self.batch else 0
        self.save_btn.setEnabled(total > 0)
        self._set_status(f"{reason}：{total} 帧，usable {usable}")
        _log(self.main, f"原位批量{reason}：{total} 帧，usable {usable}")

    # ── 结果表与趋势图 ──────────────────────────────────────
    def _append_row(self, row):
        table = self.table
        i = table.rowCount()
        table.insertRow(i)
        values = (
            str(row["frame"]), row["file"], row["status"],
            _fmt(row["rwp"], 2), _fmt(row["f_rich"], 3),
            _fmt(row.get("wt_rich", float("nan")), 2),
            _fmt(row.get("wt_poor", float("nan")), 2),
            _fmt(row["rich_a"], 4), _fmt(row["rich_c"], 4),
            _fmt(row["poor_a"], 4), _fmt(row["poor_c"], 4),
            _fmt(row["t_fit_s"], 1),
        )
        status = str(row["status"])
        for col, text in enumerate(values):
            item = QTableWidgetItem(text)
            if col not in (1, 2):
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if col == 2 and (status.startswith("error")
                             or status.startswith("high_rwp")):
                item.setForeground(QBrush(QColor("#c0392b")))
            elif col == 2 and status != "ok":
                item.setForeground(QBrush(QColor("#b7791f")))
            table.setItem(i, col, item)
        table.scrollToBottom()

    # ── 四个视图：趋势 / 汇总 / 单帧拟合 / 模型无关 ──────────
    def _on_view_changed(self, _index):
        self._refresh_active_view()

    def _refresh_active_view(self):
        index = self.view_tabs.currentIndex()
        if index == 0:
            self._draw_trends()
        elif index == 1:
            self._draw_summary()
        elif index == 2:
            self._draw_fit()
        else:
            self._draw_model_independent()

    def _draw_trends(self):
        from xrd_toolkit.simulation.insitu_plots import draw_trends
        rows = (self.batch.rows if self.batch is not None else [])
        draw_trends(self.fig, rows)
        self.canvas.draw_idle()

    def _draw_summary(self):
        from xrd_toolkit.simulation.insitu_plots import draw_summary
        rows = (self.batch.rows if self.batch is not None else [])
        draw_summary(self.summary_fig, rows)
        self.summary_canvas.draw_idle()

    def _fit_title(self):
        total = len(self.frames) or self._fit_frame
        return (f"{self._fit_file} — frame {self._fit_frame}/{total}, "
                f"f_rich={_fmt(self._fit_frich)}, "
                f"Rwp={self._fit_res.rwp:.2f}% ({self._fit_status})")

    def _draw_fit(self):
        from xrd_toolkit.simulation.plots import draw_fit
        self.fit_fig.clear()
        if self._fit_res is None:
            ax = self.fit_fig.add_subplot(111)
            ax.set_axis_off()
            ax.text(0.5, 0.55, "单帧拟合", ha="center", va="center",
                    fontsize=15, color="#666666", transform=ax.transAxes)
            ax.text(0.5, 0.44, "双击表格里的一行看这一帧的拟合",
                    ha="center", va="center", fontsize=10,
                    color="#888888", transform=ax.transAxes)
        else:
            draw_fit(self.fit_fig, self._fit_res, self._fit_title(),
                     bragg="auto")
        self.fit_canvas.draw_idle()

    def _draw_model_independent(self):
        from xrd_toolkit.simulation.insitu_plots import (
            draw_model_independent, model_independent_rows)
        fitted = (self.batch.fitted if self.batch is not None else [])
        if self._mi_rows is None or self._mi_len != len(fitted):
            self._mi_rows = model_independent_rows(fitted)
            self._mi_len = len(fitted)
        draw_model_independent(self.mi_fig, self._mi_rows)
        self.mi_canvas.draw_idle()

    def _clear_fit_view(self):
        self._fit_frame = None
        self._fit_res = None
        self._fit_status = ""
        self._fit_file = ""
        self._fit_frich = float("nan")
        self.copy_report_btn.setEnabled(False)
        self._draw_fit()

    def _frame_entry(self, frame):
        if self.batch is None:
            return None
        # 快照：后台线程可能正往 fitted 追加当前帧
        for entry in list(self.batch.fitted):
            if entry[0] == frame:
                return entry
        return None

    def _show_frame_fit(self, frame):
        entry = self._frame_entry(frame)
        if entry is None:
            self._set_status(f"第 {frame} 帧没有拟合结果（看状态列）")
            return
        i, path, res, f_rich, status = entry
        self._fit_frame = i
        self._fit_res = res
        self._fit_status = status
        self._fit_file = Path(path).name
        self._fit_frich = float(f_rich)
        self.copy_report_btn.setEnabled(True)
        self.view_tabs.setCurrentWidget(self.fit_canvas)
        self._draw_fit()
        self._set_status(
            f"第 {i} 帧：{status}  Rwp {res.rwp:.2f}%  "
            f"f_rich {_fmt(f_rich)}")

    def _on_cell_double_clicked(self, row, _col):
        if self.batch is None or row < 0 or row >= len(self.batch.rows):
            return
        self._show_frame_fit(self.batch.rows[row]["frame"])

    def _copy_fit_report(self):
        if self._fit_res is None:
            self._set_status("先在表格里双击一帧")
            return
        QApplication.clipboard().setText(self._fit_res.report())
        self._set_status("已复制单帧拟合报告")
        _log(self.main, f"已复制第 {self._fit_frame} 帧拟合报告")

    # ── 导出与复制 ──────────────────────────────────────────
    @property
    def csv_path(self):
        return Path(self.outdir) / "insitu_fit.csv"

    def _write_csv(self):
        rows = (self.batch.rows if self.batch is not None else [])
        if not rows:
            return
        try:
            Path(self.outdir).mkdir(parents=True, exist_ok=True)
            with open(self.csv_path, "w", newline="",
                      encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        except OSError as err:                     # noqa: BLE001
            _log(self.main, f"CSV 写入失败：{user_error_text(err)}")

    def _save_view(self):
        """按脚本同尺寸/同布局重画一张再存——画布会被 Qt 拉成窗口大小，
        直接存画布会得到低分辨率、挤在一起的图（2026-11-02 实测）。"""
        from xrd_toolkit.simulation.insitu_plots import (
            draw_model_independent, draw_summary, draw_trends,
            model_independent_rows)
        from xrd_toolkit.simulation.plots import draw_fit

        index = self.view_tabs.currentIndex()
        if index == 0:
            if self.batch is None or not self.batch.rows:
                self._set_status("还没有趋势图")
                return
            fig = Figure(figsize=(11.0, 7.0))
            draw_trends(fig, self.batch.rows)
            name = "insitu_trends"
        elif index == 1:
            if self.batch is None or not self.batch.rows:
                self._set_status("还没有汇总图")
                return
            fig = Figure(figsize=(12.5, 8.0))
            draw_summary(fig, self.batch.rows)
            name = "insitu_summary"
        elif index == 2:
            if self._fit_res is None:
                self._set_status("先在表格里双击一帧")
                return
            fig = Figure(figsize=(11.5, 7.6))
            draw_fit(fig, self._fit_res, self._fit_title(), bragg="auto")
            name = f"insitu_fit_frame_{self._fit_frame:03d}"
        else:
            if self.batch is None or not self.batch.fitted:
                self._set_status("还没有模型无关数据")
                return
            if self._mi_rows is None:
                self._mi_rows = model_independent_rows(self.batch.fitted)
                self._mi_len = len(self.batch.fitted)
            fig = Figure(figsize=(15.0, 4.6))
            draw_model_independent(fig, self._mi_rows)
            name = "insitu_model_independent"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        default = Path(self.outdir) / f"{name}_{stamp}.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "保存当前图", str(default), "PNG (*.png);;PDF (*.pdf)",
            options=QFileDialog.DontUseNativeDialog)
        if not path:
            return
        try:
            fig.savefig(path, dpi=200)
        except Exception as err:                   # noqa: BLE001
            self._set_status(f"保存失败：{user_error_text(err)}")
            return
        self._set_status(f"已保存 {Path(path).name}")
        _log(self.main, f"原位当前图已保存：{path}")

    def _table_tsv(self, selected_only=False):
        model = self.table.selectionModel()
        rows = (sorted({i.row() for i in model.selectedRows()})
                if selected_only and model is not None
                else list(range(self.table.rowCount())))
        lines = ["\t".join(_COLUMNS)]
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
            self._set_status("结果表没有可复制的行")
            return 0
        QApplication.clipboard().setText(text)
        _log(self.main, f"已复制原位结果 {n} 行")
        return n

    def _table_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            row = self.table.currentRow()
        frame = None
        if (self.batch is not None and 0 <= row < len(self.batch.rows)):
            frame = self.batch.rows[row]["frame"]
        has_fit = frame is not None and self._frame_entry(frame) is not None

        menu = QMenu(self.table)
        act_view = menu.addAction("查看这一帧拟合")
        act_save = menu.addAction("保存这一帧拟合图…")
        act_report = menu.addAction("复制这一帧报告")
        act_view.setEnabled(has_fit)
        act_save.setEnabled(has_fit)
        act_report.setEnabled(has_fit)
        menu.addSeparator()
        act_sel = menu.addAction("复制选中行")
        act_all = menu.addAction("复制全部")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is act_view:
            self._show_frame_fit(frame)
        elif chosen is act_save:
            self._show_frame_fit(frame)
            if self._fit_res is not None:
                self._save_view()
        elif chosen is act_report:
            self._show_frame_fit(frame)
            self._copy_fit_report()
        elif chosen is act_sel:
            self._copy_table(True)
        elif chosen is act_all:
            self._copy_table(False)

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


class _InSituSubWindow(QMdiSubWindow):
    """原位工作台的中央子窗口：× 关闭 = 退出原位模式，内容不销毁。"""

    def __init__(self, window):
        super().__init__()
        self._window = window
        self.panel_key = "InSitu|workbench"

    def closeEvent(self, event):
        window = self._window
        if (not getattr(window, "_closing", False)
                and getattr(window, "insitu_btn", None) is not None
                and window.insitu_btn.isChecked()):
            window.insitu_btn.setChecked(False)    # toggled → 退出原位模式
        event.ignore()
        self.hide()
