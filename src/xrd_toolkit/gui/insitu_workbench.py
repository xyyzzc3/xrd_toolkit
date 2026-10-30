"""原位三相批量中央工作台（2026-10-30）。

打开方式：工具栏 [原位]。与 [Mask]、[精修]、[物相] 同一套中央 MDI
子窗口：进入时收起参数坞和日志坞，退出只隐藏，选中的目录/参数/结果
都留着。

流程（引擎在 `simulation.insitu`，命令行 `scripts/fit_insitu.py` 共用）：

    选帧目录与 mask → [开始]
    → 后台准备（读三相 CIF + LaB₆ 定仪器，数秒）
    → 逐帧后台精修：读图 → 积分 → 三相拟合 → 结果进表/趋势图
    → 结束写 CSV 与 fit_summary.txt

相分数口径：A_p = scale_p × Σ(table.inten_p) × norm_p / 100，
f_rich = A_rich / (A_rich + A_poor)。这是**相对散射强度分数**，不是
Rietveld 重量分数；看趋势可以，别当 wt% 写。
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
    QScrollArea, QSizePolicy, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget)

from xrd_toolkit import paths
from xrd_toolkit.gui.panel_state import _log
from xrd_toolkit.gui.tasks import BackgroundTask, user_error_text

_COLUMNS = ("帧", "文件", "状态", "Rwp %", "f_rich",
            "rich a", "rich c", "poor a", "poor c", "拟合 s")
_RICH_COLOR = "#1f5fa8"
_POOR_COLOR = "#e08a1e"
_RWP_COLOR = "#c0392b"


def _fmt(value, digits=3):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{value:.{digits}f}" if np.isfinite(value) else "—"


def _prepare_worker(config_name, rich_path, poor_path, al_path, mask_path,
                    max_rwp, first_nfev, frame_nfev, npt, tth_min, tth_max):
    """后台准备：读结构 + LaB₆ 定仪器 + 读 mask，返回 InSituBatch。"""
    try:
        from xrd_toolkit.simulation.insitu import prepare_setup
    except ImportError as err:
        raise RuntimeError(str(err)) from err
    return prepare_setup(
        config_name, rich_path=rich_path, poor_path=poor_path,
        al_path=al_path, mask_path=mask_path, max_rwp=max_rwp,
        first_nfev=first_nfev, frame_nfev=frame_nfev,
        npt=npt, tth_min=tth_min, tth_max=tth_max)


class InSituWorkbench(QWidget):
    """原位三相批量工作台。"""

    def __init__(self, main_window):
        super().__init__()
        self.main = main_window
        self.setObjectName("insitu_workbench")

        try:
            from xrd_toolkit.simulation.insitu import (
                DEFAULT_AL, DEFAULT_POOR, DEFAULT_RICH, frames_in_dir)
            self._frames_in_dir = frames_in_dir
        except ImportError:
            DEFAULT_RICH = DEFAULT_POOR = DEFAULT_AL = None
            self._frames_in_dir = lambda *a, **k: []

        default_data = Path.home() / "Desktop" / "lmfpdata"
        if not default_data.is_dir():
            default_data = paths.ROOT / "data"
        self.data_dir = default_data
        # 与命令行同一个默认 mask（只在 LMFP 几何下自动带；其它配置按不遮）
        default_mask = (paths.default_outputs_dir() / "masks"
                        / "lmfp1_lab6_mask.npy")
        self.mask_path = (default_mask
                          if (self.main.config_name == "lmfp1_lab6"
                              and default_mask.is_file()) else None)
        self.phase_paths = {
            "rich": Path(DEFAULT_RICH) if DEFAULT_RICH else None,
            "poor": Path(DEFAULT_POOR) if DEFAULT_POOR else None,
            "al": Path(DEFAULT_AL) if DEFAULT_AL else None,
        }
        self.outdir = paths.default_outputs_dir() / "insitu"
        self.frames = []
        self.batch = None
        self._task = None
        self._stop = False
        self._index = 0

        self._build_ui()
        if self.mask_path is not None:
            self.mask_label.setText(f"mask：{self.mask_path.name}")
            self.mask_label.setToolTip(str(self.mask_path))
        self._refresh_data_label()
        self._refresh_phase_labels()
        self._draw_trends()

    # ── 界面组装 ─────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(QLabel("数据"))
        self.data_label = QLabel("")
        self.data_label.setStyleSheet("color: gray;")
        self.data_label.setMinimumWidth(0)
        self.data_label.setSizePolicy(QSizePolicy.Ignored,
                                      QSizePolicy.Preferred)
        top.addWidget(self.data_label, 1)
        self.data_btn = QPushButton("选文件夹…")
        self.data_btn.clicked.connect(self._choose_data)
        top.addWidget(self.data_btn)
        self.mask_label = QLabel("mask：不遮")
        self.mask_label.setStyleSheet("color: gray;")
        self.mask_label.setMinimumWidth(170)
        self.mask_label.setSizePolicy(QSizePolicy.Ignored,
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
        self.save_btn = QPushButton("保存趋势图…")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save_trend)
        left_lay.addWidget(self.save_btn)
        hint = QLabel("相分数是相对散射强度分数，不是 wt%；看趋势可以，"
                      "别当定量结果。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        hint.setMinimumWidth(0)
        hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        left_lay.addWidget(hint)
        left_lay.addStretch(1)
        left_scroll.setWidget(left)
        split.addWidget(left_scroll)

        right = QSplitter(Qt.Vertical)
        self.fig = Figure(figsize=(6.8, 4.2))
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right.addWidget(self.canvas)

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
        start = paths.ROOT / "data" / "structures"
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
        self._stop = False
        self._index = 0
        self.batch = None
        self.table.setRowCount(0)
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
        self._draw_trends()
        self._write_csv()
        self._set_status(
            f"第 {row['frame']}/{len(self.frames)} 帧：{row['status']}  "
            f"Rwp {_fmt(row['rwp'])}%  f_rich {_fmt(row['f_rich'])}")
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

    def _draw_trends(self):
        rows = (self.batch.rows if self.batch is not None else [])
        self.fig.clear()
        if not rows:
            ax = self.fig.add_subplot(111)
            ax.set_axis_off()
            ax.text(0.5, 0.55, "原位三相批量", ha="center", va="center",
                    fontsize=15, color="#666666", transform=ax.transAxes)
            ax.text(0.5, 0.44, "选帧目录，点 [开始]",
                    ha="center", va="center", fontsize=10, color="#888888",
                    transform=ax.transAxes)
            self.canvas.draw_idle()
            return
        good = [r for r in rows
                if np.isfinite(r["f_rich"]) and np.isfinite(r["rwp"])]
        gs = self.fig.add_gridspec(2, 1, height_ratios=[1.25, 1.0],
                                   hspace=0.16)
        ax1 = self.fig.add_subplot(gs[0])
        ax2 = self.fig.add_subplot(gs[1], sharex=ax1)
        ax1.tick_params(labelbottom=False)
        if good:
            x = [r["frame"] for r in good]
            ax1.plot(x, [r["f_rich"] for r in good], "o-", ms=2.5,
                     color=_RICH_COLOR, label="富锂相")
            ax1.plot(x, [r["f_poor"] for r in good], "s-", ms=2.5,
                     color=_POOR_COLOR, label="贫锂相")
            yerr = [r["f_rich_esd"] if np.isfinite(r["f_rich_esd"]) else 0.0
                    for r in good]
            if any(yerr):
                ax1.errorbar(x, [r["f_rich"] for r in good], yerr=yerr,
                             fmt="none", ecolor=_RICH_COLOR, capsize=2,
                             alpha=0.6)
            ax2.plot(x, [r["rwp"] for r in good], "o-", ms=2.5,
                     color=_RWP_COLOR)
            if len(x) == 1:
                # 单帧时 matplotlib 会把 x 轴缩到 1±0.04，看起来像刻度坏了
                ax2.set_xlim(x[0] - 0.5, x[0] + 0.5)
        else:
            ax1.text(0.5, 0.5, "还没有可用帧", ha="center", va="center",
                     fontsize=10, color="#888888", transform=ax1.transAxes)
        ax1.set_ylabel("relative fraction", fontsize=8)
        ax1.legend(fontsize=8, loc="best")
        ax1.grid(alpha=0.25, linestyle=":")
        ax2.set_ylabel("Rwp (%)", fontsize=8)
        ax2.set_xlabel("frame", fontsize=8)
        ax2.grid(alpha=0.25, linestyle=":")
        ax1.tick_params(labelsize=7)
        ax2.tick_params(labelsize=7)
        self.fig.subplots_adjust(left=0.10, right=0.98, top=0.95, bottom=0.10)
        self.canvas.draw_idle()

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

    def _save_trend(self):
        if self.batch is None or not self.batch.rows:
            self._set_status("还没有趋势图")
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        default = Path(self.outdir) / f"insitu_trends_{stamp}.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "保存趋势图", str(default), "PNG (*.png);;PDF (*.pdf)")
        if not path:
            return
        try:
            self.fig.savefig(path, dpi=200)
        except Exception as err:                   # noqa: BLE001
            self._set_status(f"保存失败：{user_error_text(err)}")
            return
        self._set_status(f"已保存 {Path(path).name}")
        _log(self.main, f"原位趋势图已保存：{path}")

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
        menu = QMenu(self.table)
        act_sel = menu.addAction("复制选中行")
        act_all = menu.addAction("复制全部")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is act_sel:
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
