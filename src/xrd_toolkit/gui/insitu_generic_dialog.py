"""通用多相原位精修对话框（2026-11-11 用户第 14 条）。

专用 LMFP 三相快速模式留在原位工作台；这里给"任意相列表"的入口：
选帧目录 → 按顺序加 CIF（第一张 = 主相，其余 = 额外相）→ [开始]。
每帧一拟合，表格与 CSV 里有每相的相对强度分数和晶胞。

设计取舍：
  * 不重画趋势图（第一版先保证能跑通、能导出；专用模式已有四视图）；
  * 相位/峰强/晶胞都按用户勾选：额外相默认结构因子（快），可勾 Le Bail；
  * 仪器基线优先用窗口里已保存的 LaB₆ 基线；没有就只精修零位。
"""
import time
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import Qt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFileDialog, QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton,
    QSizePolicy, QSpinBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget)

from xrd_toolkit import paths
from xrd_toolkit.config import get_config
from xrd_toolkit.gui.panel_state import _log
from xrd_toolkit.gui.tasks import BackgroundTask


def _prepare_worker(phase_specs, folder, config_name, mask_path, instrument,
                    max_rwp, limit, npt, tth_min, tth_max, policy,
                    views=None):
    """后台准备：按每相 PhaseSpec 读结构/mask、列帧。不能碰界面控件。"""
    from xrd_toolkit.simulation.insitu_generic import (
        GenericBatch, frames_for_dir, load_mask_file)
    from xrd_toolkit.simulation.phase_set import (
        BatchPolicy, PeakWindow, PhaseSet, ViewConfig, spec_from_path)
    geom = dict(get_config(config_name)["geometry"])
    lam = float(geom["wavelength_m"]) * 1e10
    mask = load_mask_file(mask_path) if mask_path else None
    phases = [spec_from_path(
        item["path"], label=item.get("label"), display_name=item.get(
            "display_name", ""), role=item.get("role", "sample"),
        cell_free=item.get("cell_free", True),
        intensity=item.get("intensity", "structure"),
        include_in_fraction=item.get("include_in_fraction", True),
        b_iso=item.get("b_iso"), size_um=item.get("size_um", 1e4),
        mustrain=item.get("mustrain", 0.0),
        cell_bounds=item.get("cell_bounds"), jump_tol=item.get("jump_tol"),
        color=item.get("color"),
        free_extra=item.get("free_extra", ())) for item in phase_specs]
    pol = BatchPolicy.generic()
    for key, value in (policy or {}).items():
        if hasattr(pol, key):
            setattr(pol, key, value)
    vraw = dict(views or {})
    windows = [PeakWindow(**w) for w in vraw.pop("peak_windows", [])]
    vcfg = ViewConfig(peak_windows=windows, **vraw)
    batch = GenericBatch(
        [], geom, lam, instrument=instrument, mask=mask,
        max_rwp=max_rwp, phase_set=PhaseSet(name="通用多相", phases=phases,
                                            policy=pol, views=vcfg),
        first_nfev=pol.first_nfev, frame_nfev=pol.frame_nfev,
        npt=npt, tth_min=tth_min, tth_max=tth_max)
    frames = frames_for_dir(folder, limit=limit)
    return batch, frames


class GenericInsituDialog(QDialog):
    """任意 N 相的原位顺序精修。"""

    def __init__(self, main_window, workbench):
        super().__init__(main_window)
        self.main = main_window
        self.workbench = workbench
        self.batch = None
        self._frames = []
        self._index = 0
        self._task = None
        self._stop = False
        self._instrument = self._baseline_params()
        self._scheme_policy = None
        self._scheme_views = None
        self.setWindowTitle("通用多相原位精修")
        self.resize(1040, 680)
        self._build_ui()
        self._seed_phases()

    # ── 仪器基线 ───────────────────────────────────────────
    def _current_wavelength(self):
        try:
            geom = get_config(self.main.config_name)["geometry"]
            return float(geom["wavelength_m"]) * 1e10
        except (KeyError, TypeError, ValueError):
            return float("nan")

    def _baseline_params(self):
        record = getattr(self.main, "instrument_baseline", None)
        if not record:
            return None
        try:
            wl = float(record.get("wavelength"))
        except (TypeError, ValueError):
            return None
        current = self._current_wavelength()
        if not (current == current and abs(wl - current) <= 0.01):
            return None
        params = record.get("params")
        return dict(params) if isinstance(params, dict) else None

    # ── 界面 ───────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        row = QHBoxLayout()
        row.addWidget(QLabel("帧目录"))
        self.folder_edit = QLineEdit(
            str(getattr(self.workbench, "data_dir", paths.ROOT / "data")))
        row.addWidget(self.folder_edit, 1)
        browse = QPushButton("选文件夹…")
        browse.clicked.connect(self._choose_folder)
        row.addWidget(browse)
        root.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("相"), 0)
        self.phase_table = QTableWidget(0, 6)
        self.phase_table.setObjectName("generic_phase_table")
        self.phase_table.setHorizontalHeaderLabels(
            ["角色", "相", "晶胞", "强度", "计入分数", "B/峰宽"])
        self.phase_table.verticalHeader().setVisible(False)
        self.phase_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.phase_table.setSelectionMode(
            QAbstractItemView.SingleSelection)
        self.phase_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.phase_table.setMaximumHeight(130)
        hh = self.phase_table.horizontalHeader()
        for col in (0, 2, 3, 4, 5):
            hh.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        self.phase_table.setToolTip(
            "每相一行：角色（样品相/参考相）、晶胞是否自由、强度用结构因子"
            "还是 Le Bail、是否计入样品分数分母。新加的相默认是样品相。")
        row.addWidget(self.phase_table, 1)
        side = QVBoxLayout()
        for text, slot in (("加 CIF…", self._add_phases),
                           ("移除", self._remove_phase),
                           ("切换角色", self._toggle_role),
                           ("改名…", self._rename_phase),
                           ("存方案…", self._save_scheme),
                           ("载方案…", self._load_scheme)):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            side.addWidget(btn)
        side.addStretch(1)
        row.addLayout(side)
        root.addLayout(row)

        opts = QHBoxLayout()
        opts.addWidget(QLabel("帧数上限"))
        self.limit = QSpinBox()
        self.limit.setRange(0, 9999)
        self.limit.setSpecialValueText("全部")
        self.limit.setValue(3)
        opts.addWidget(self.limit)
        opts.addWidget(QLabel("最大 Rwp"))
        self.max_rwp = QDoubleSpinBox()
        self.max_rwp.setRange(2.0, 50.0)
        self.max_rwp.setDecimals(1)
        self.max_rwp.setValue(20.0)
        self.max_rwp.setSuffix(" %")
        opts.addWidget(self.max_rwp)
        opts.addStretch(1)
        root.addLayout(opts)

        row = QHBoxLayout()
        row.addWidget(QLabel("掩膜"))
        self.mask_edit = QLineEdit(
            str(getattr(self.workbench, "mask_path", "") or ""))
        row.addWidget(self.mask_edit, 1)
        mask_btn = QPushButton("选掩膜…")
        mask_btn.clicked.connect(self._choose_mask)
        row.addWidget(mask_btn)
        root.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("输出目录"))
        self.out_edit = QLineEdit(
            str(paths.default_outputs_dir() / "insitu_generic"))
        row.addWidget(self.out_edit, 1)
        out_btn = QPushButton("选目录…")
        out_btn.clicked.connect(self._choose_outdir)
        row.addWidget(out_btn)
        root.addLayout(row)

        info = QHBoxLayout()
        self.instrument_label = QLabel("")
        self.instrument_label.setStyleSheet("color: gray;")
        info.addWidget(self.instrument_label, 1)
        self.start_btn = QPushButton("开始")
        self.start_btn.clicked.connect(self._start)
        info.addWidget(self.start_btn)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._request_stop)
        info.addWidget(self.stop_btn)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        info.addWidget(close_btn)
        root.addLayout(info)

        # 进度：文字 + 帧进度条（2026-11-17；总帧数列完就知道，给 k/n）
        progress_row = QHBoxLayout()
        progress_row.setContentsMargins(0, 0, 0, 0)
        progress_row.setSpacing(6)
        self.progress = QLabel("选好帧目录与相，点 [开始]")
        self.progress.setStyleSheet("color: gray;")
        self.progress.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        progress_row.addWidget(self.progress, 1)
        self.frame_progress = QProgressBar()
        self.frame_progress.setObjectName("insitu_generic_progress")
        self.frame_progress.setFixedWidth(170)
        self.frame_progress.setMaximumHeight(14)
        self.frame_progress.setFormat("%v/%m")
        self.frame_progress.setVisible(False)
        progress_row.addWidget(self.frame_progress)
        root.addLayout(progress_row)

        # 四视图 + 数据表：通用趋势/汇总/单帧/峰窗跟踪。
        self.view_tabs = QTabWidget()
        self.trend_fig = Figure(figsize=(6.0, 4.2))
        self.trend_canvas = FigureCanvasQTAgg(self.trend_fig)
        self.summary_fig = Figure(figsize=(6.0, 4.2))
        self.summary_canvas = FigureCanvasQTAgg(self.summary_fig)
        self.fit_fig = Figure(figsize=(6.0, 4.2))
        self.fit_canvas = FigureCanvasQTAgg(self.fit_fig)
        self.win_fig = Figure(figsize=(6.0, 4.2))
        self.win_canvas = FigureCanvasQTAgg(self.win_fig)
        self.view_tabs.addTab(self.trend_canvas, "趋势")
        self.view_tabs.addTab(self.summary_canvas, "汇总")
        self.view_tabs.addTab(self.fit_canvas, "单帧拟合")
        self.view_tabs.addTab(self.win_canvas, "峰窗跟踪")
        self.table = QTableWidget(0, 0)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.itemDoubleClicked.connect(self._on_table_double_clicked)
        self.view_tabs.addTab(self.table, "数据")
        root.addWidget(self.view_tabs, 1)
        # 空状态
        from xrd_toolkit.simulation.insitu_multi_plots import (
            draw_multi_summary, draw_multi_trends, draw_window_tracker)
        draw_multi_trends(self.trend_fig, [], None)
        draw_multi_summary(self.summary_fig, [], None)
        draw_window_tracker(self.win_fig, [], None)
        self.trend_canvas.draw_idle()
        self.summary_canvas.draw_idle()
        self.win_canvas.draw_idle()

        if self._instrument is None:
            self.instrument_label.setText(
                "仪器基线：无（只精修零位；峰宽按默认估计）")
        else:
            self.instrument_label.setText(
                f"仪器基线：已加载（λ {self._current_wavelength():.4f} Å）")

    def _seed_phases(self):
        """默认带入原位工作台的三相：rich/poor 样品相、Al 参考相。"""
        paths_now = getattr(self.workbench, "phase_paths", {}) or {}
        for key in ("rich", "poor", "al"):
            path = paths_now.get(key)
            if path and Path(path).is_file():
                is_al = key == "al"
                self._append_phase(
                    Path(path), label=key,
                    role="reference" if is_al else "sample",
                    cell_free=not is_al,
                    intensity="lebail" if is_al else "structure",
                    include_in_fraction=not is_al)

    def _append_phase(self, path, *, label=None, role="sample",
                      cell_free=True, intensity="structure",
                      include_in_fraction=True, free_extra=False):
        table = self.phase_table
        row = table.rowCount()
        table.insertRow(row)
        role_box = QComboBox()
        role_box.addItem("样品相", "sample")
        role_box.addItem("参考相", "reference")
        role_box.setCurrentIndex(0 if role == "sample" else 1)
        table.setCellWidget(row, 0, role_box)
        item = QTableWidgetItem(Path(path).stem)
        item.setData(Qt.UserRole, str(path))
        if label:
            item.setData(Qt.UserRole + 1, str(label))
        item.setToolTip(str(path))
        table.setItem(row, 1, item)
        cell = QCheckBox()
        cell.setChecked(bool(cell_free))
        cell.setToolTip("勾上 = 该相 a/b/c 参与精修（只对正交/立方开放）")
        table.setCellWidget(row, 2, cell)
        kind = QComboBox()
        kind.addItem("结构因子", "structure")
        kind.addItem("Le Bail", "lebail")
        kind.setCurrentIndex(0 if intensity == "structure" else 1)
        kind.setToolTip("参考相/织构强时用 Le Bail：每根峰强度单独解出")
        table.setCellWidget(row, 3, kind)
        include = QCheckBox()
        include.setChecked(bool(include_in_fraction))
        include.setToolTip("参考相（如 Al）取消勾选：不进入样品分数分母")
        table.setCellWidget(row, 4, include)
        bfree = QCheckBox()
        bfree.setChecked(bool(free_extra))
        bfree.setToolTip(
            "额外相自己的 B/尺寸/应变也参与精修；默认固定（更快、更稳）")
        table.setCellWidget(row, 5, bfree)
        self._refresh_phase_items()

    def _refresh_phase_items(self):
        # 角色/强度/勾选都在 cell widget 里；这里只保证行高与 tooltip
        for row in range(self.phase_table.rowCount()):
            item = self.phase_table.item(row, 1)
            if item is not None:
                item.setToolTip(item.data(Qt.UserRole) or "")

    def _rename_phase(self):
        row = self.phase_table.currentRow()
        if row < 0:
            return
        item = self.phase_table.item(row, 1)
        if item is None:
            return
        text, ok = QInputDialog.getText(
            self, "相的名字", "界面/图里显示的名字：", text=item.text())
        if ok and text.strip():
            item.setText(text.strip())

    def _save_scheme(self):
        specs = self._phase_specs()
        if not specs:
            self._set_status("相表为空，没有可保存的方案")
            return
        start = str(Path.home() / "Data" / "XRD" / "insitu_scheme.json")
        name, _ = QFileDialog.getSaveFileName(
            self, "保存原位方案", start, "方案 (*.json);;所有文件 (*)")
        if not name:
            return
        path = Path(name)
        if path.suffix.lower() != ".json":
            path = path.with_suffix(".json")
        try:
            from xrd_toolkit.simulation.phase_set import (
                BatchPolicy, PhaseSet, ViewConfig, save_phase_set,
                spec_from_path)
            phases = [spec_from_path(
                item["path"], label=item.get("label"),
                display_name=item.get("display_name", ""),
                role=item.get("role", "sample"),
                cell_free=item.get("cell_free", True),
                intensity=item.get("intensity", "structure"),
                include_in_fraction=item.get("include_in_fraction", True),
                free_extra=item.get("free_extra", ()))
                for item in specs]
            pset = PhaseSet(
                name=path.stem, phases=phases,
                policy=(self._scheme_policy or BatchPolicy.generic()),
                views=(self._scheme_views or ViewConfig()))
            save_phase_set(pset, path)
        except Exception as err:                    # noqa: BLE001
            QMessageBox.warning(self, "保存方案失败", str(err))
            return
        self._set_status(f"方案已保存：{path}")
        _log(self.main, f"原位方案已保存：{path}")

    def _load_scheme(self):
        start = str(Path.home() / "Data" / "XRD")
        name, _ = QFileDialog.getOpenFileName(
            self, "载入原位方案", start, "方案 (*.json);;所有文件 (*)")
        if not name:
            return
        try:
            from xrd_toolkit.simulation.phase_set import load_phase_set
            pset = load_phase_set(name)
        except Exception as err:                    # noqa: BLE001
            QMessageBox.warning(self, "载入方案失败", str(err))
            return
        self.phase_table.setRowCount(0)
        for phase in pset.phases:
            self._append_phase(
                phase.path, label=phase.label, role=phase.role,
                cell_free=phase.cell_free, intensity=phase.intensity,
                include_in_fraction=phase.include_in_fraction,
                free_extra=bool(phase.free_extra))
            item = self.phase_table.item(self.phase_table.rowCount() - 1, 1)
            if item is not None and phase.display_name:
                item.setText(phase.display_name)
        self._scheme_policy = pset.policy
        self._scheme_views = pset.views
        self.setWindowTitle(f"通用多相原位精修：{pset.name}")
        self._set_status(
            f"已载入方案：{Path(name).name}（{len(pset.phases)} 相）")
        _log(self.main, f"原位方案已载入：{name}")

    def _add_phases(self):
        start = (str(paths.phase_library_dir())
                 if paths.phase_library_dir().is_dir()
                 else str(paths.structures_dir()))
        files, _ = QFileDialog.getOpenFileNames(
            self, "加 CIF（默认样品相，可再切换角色）", start,
            "CIF (*.cif);;所有文件 (*)")
        for name in files:
            self._append_phase(Path(name))

    def _remove_phase(self):
        row = self.phase_table.currentRow()
        if row >= 0:
            self.phase_table.removeRow(row)
            self._refresh_phase_items()

    def _toggle_role(self):
        row = self.phase_table.currentRow()
        if row < 0:
            return
        role_box = self.phase_table.cellWidget(row, 0)
        if role_box is None:
            return
        is_sample = role_box.currentData() == "sample"
        role_box.setCurrentIndex(1 if is_sample else 0)
        # 切角色时套默认：参考相 = 固定晶胞 + Le Bail + 不进分母
        cell = self.phase_table.cellWidget(row, 2)
        kind = self.phase_table.cellWidget(row, 3)
        include = self.phase_table.cellWidget(row, 4)
        if cell is not None:
            cell.setChecked(not is_sample)
        if kind is not None:
            kind.setCurrentIndex(1 if is_sample else 0)
        if include is not None:
            include.setChecked(not is_sample)
        self._refresh_phase_items()

    def _phase_specs(self):
        """表格 → worker 用的 PhaseSpec 字典列表。"""
        specs = []
        for row in range(self.phase_table.rowCount()):
            item = self.phase_table.item(row, 1)
            if item is None:
                continue
            role_box = self.phase_table.cellWidget(row, 0)
            cell = self.phase_table.cellWidget(row, 2)
            kind = self.phase_table.cellWidget(row, 3)
            include = self.phase_table.cellWidget(row, 4)
            bfree = self.phase_table.cellWidget(row, 5)
            specs.append({
                "path": item.data(Qt.UserRole),
                "label": item.data(Qt.UserRole + 1),
                "display_name": item.text(),
                "role": role_box.currentData() if role_box is not None
                else "sample",
                "cell_free": bool(cell.isChecked()) if cell is not None
                else True,
                "intensity": kind.currentData() if kind is not None
                else "structure",
                "include_in_fraction": bool(include.isChecked())
                if include is not None else True,
                "free_extra": (("b_iso", "size_um", "mustrain")
                               if bfree is not None and bfree.isChecked()
                               else ()),
            })
        return specs

    def _choose_folder(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "选帧目录", self.folder_edit.text())
        if chosen:
            self.folder_edit.setText(chosen)

    def _choose_mask(self):
        start = self.mask_edit.text() or str(paths.OUTPUTS_DIR)
        name, _ = QFileDialog.getOpenFileName(
            self, "选掩膜", start,
            "掩膜 (*.mask.npz *.npy *.edf);;所有文件 (*)")
        if name:
            self.mask_edit.setText(name)

    def _choose_outdir(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "选输出目录", self.out_edit.text())
        if chosen:
            self.out_edit.setText(chosen)

    # ── 运行 ───────────────────────────────────────────────
    def _phase_paths(self):
        """兼容旧测试/调用：表格里的 CIF 路径（按行）。"""
        out = []
        for row in range(self.phase_table.rowCount()):
            item = self.phase_table.item(row, 1)
            if item is not None:
                out.append(item.data(Qt.UserRole))
        return out

    def _start(self):
        if self._task is not None:
            return
        folder = Path(self.folder_edit.text()).expanduser()
        if not folder.is_dir():
            self._set_status("帧目录不存在")
            return
        phase_specs = self._phase_specs()
        if not phase_specs:
            self._set_status("先 [加 CIF…] 至少加一张相")
            return
        if not any(item.get("role") == "sample" for item in phase_specs):
            self._set_status("至少要有一相是「样品相」（不能全是参考相）")
            return
        outdir = Path(self.out_edit.text()).expanduser()
        self._outdir = outdir
        self._rows = []
        self._index = 0
        self._stop = False
        self._frames = []
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._hide_progress()
        self._set_status("准备中（读结构 + 掩膜 + 列帧）…")
        _log(self.main, "通用原位多相：准备开始")
        self._task = BackgroundTask(
            _prepare_worker, phase_specs, folder, self.main.config_name,
            self.mask_edit.text().strip(), self._instrument,
            float(self.max_rwp.value()), int(self.limit.value()),
            int(self.workbench.npt.value()),
            float(self.workbench.tth_min.value()),
            float(self.workbench.tth_max.value()),
            (asdict(self._scheme_policy) if self._scheme_policy is not None
             else {"first_nfev": 120, "frame_nfev": 60,
                   "dual_start": "off", "pending_frames": 0}),
            (asdict(self._scheme_views) if self._scheme_views is not None
             else None),
            on_done=self._prepared, on_error=self._failed)
        self._task.start()

    def _prepared(self, payload):
        self._task = None
        self.batch, self._frames = payload
        if not self._frames:
            self._set_status("帧目录里没有可用的图")
            self._reset_buttons()
            return
        self._build_table()
        self._phase_set = self.batch.phase_set
        self._refresh_views()
        self._show_progress(len(self._frames), 0)
        samples = [p.display() for p in self.batch.phases if p.is_sample()]
        self._set_status(f"开始：{len(self._frames)} 帧，"
                         f"样品相 {'、'.join(samples)}")
        self._start_next()

    def _build_table(self):
        columns = self.batch.columns()
        self._columns = columns
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels(columns)

    def _start_next(self):
        if self._stop or self._index >= len(self._frames):
            self._finish()
            return
        path = self._frames[self._index]
        self._set_progress(self._index)
        self._set_status(
            f"第 {self._index + 1}/{len(self._frames)} 帧：{Path(path).name}")
        self._task = BackgroundTask(
            self.batch.fit_frame, path,
            on_done=self._frame_done, on_error=self._frame_failed)
        self._task.start()

    def _frame_done(self, row):
        self._task = None
        self._append_row(row)
        self._rows.append(row)
        self._index += 1
        self._set_progress(self._index)
        self._refresh_views()
        self._start_next()

    def _frame_failed(self, message):
        self._task = None
        self._set_status(f"这一帧失败：{message}")
        self._index += 1
        self._set_progress(self._index)
        self._start_next()

    def _append_row(self, row):
        i = self.table.rowCount()
        self.table.insertRow(i)
        for j, name in enumerate(self._columns):
            value = row.get(name, "")
            if isinstance(value, float):
                text = ("—" if value != value else f"{value:.5g}")
            else:
                text = str(value)
            item = QTableWidgetItem(text)
            if j >= 4:
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(i, j, item)
        self.table.scrollToBottom()

    def _refresh_views(self):
        """把当前 rows 重画到通用四视图（LMFP 预设只是配置不同）。"""
        from xrd_toolkit.simulation.insitu_multi_plots import (
            draw_multi_summary, draw_multi_trends, draw_window_tracker)
        pset = getattr(self, "_phase_set", None)
        if pset is None:
            return
        draw_multi_trends(self.trend_fig, self._rows, pset)
        draw_multi_summary(self.summary_fig, self._rows, pset)
        draw_window_tracker(self.win_fig, self._rows, pset)
        self.trend_canvas.draw_idle()
        self.summary_canvas.draw_idle()
        self.win_canvas.draw_idle()

    def _on_table_double_clicked(self, item):
        row = item.row() if item is not None else -1
        if row < 0 or row >= len(self._rows):
            return
        self._show_frame_fit(int(self._rows[row]["frame"]))

    def _show_frame_fit(self, frame):
        from xrd_toolkit.simulation.plots import draw_fit
        entry = next((x for x in self.batch.fitted if x[0] == frame), None)
        self.fit_fig.clear()
        if entry is None or entry[2] is None:
            from xrd_toolkit.simulation.insitu_plots import _placeholder
            _placeholder(self.fit_fig, "Single-frame fit",
                         "Double-click a row in the 数据 tab")
        else:
            _i, path, result, _fracs, _status = entry
            title = (f"{Path(path).name} — frame {frame}/"
                     f"{len(self._frames)}, Rwp={result.rwp:.2f}%")
            draw_fit(self.fit_fig, result, title, bragg="auto")
        self.fit_canvas.draw_idle()
        self.view_tabs.setCurrentWidget(self.fit_canvas)

    def _request_stop(self):
        self._stop = True
        self._set_status("已请求停止：当前帧算完就停")

    def _finish(self):
        self._reset_buttons()
        self._hide_progress()
        self._refresh_views()
        if not self._rows:
            self._set_status("没有完成任何帧")
            return
        try:
            csv_path = self._outdir / "insitu_generic.csv"
            self.batch.write_csv(csv_path)
        except OSError as err:
            self._set_status(f"CSV 写入失败：{err}")
            return
        self._set_status(
            f"完成：{len(self._rows)} 帧；"
            f"{'已停止（部分结果）' if self._stop else '全部完成'}，"
            f"CSV → {csv_path}")
        _log(self.main, f"通用原位多相完成：{csv_path}")

    def _reset_buttons(self):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def _failed(self, message):
        self._task = None
        self._reset_buttons()
        self._hide_progress()
        self._set_status(f"准备失败：{message}")

    def _set_status(self, text):
        self.progress.setText(text)

    # ── 帧进度条（2026-11-17）───────────────────────────────
    def _show_progress(self, total: int, value: int = 0) -> None:
        bar = getattr(self, "frame_progress", None)
        if bar is None:
            return
        bar.setRange(0, max(1, int(total)))
        bar.setValue(int(value))
        bar.setVisible(True)

    def _set_progress(self, value: int) -> None:
        bar = getattr(self, "frame_progress", None)
        if bar is not None and bar.isVisible():
            bar.setValue(int(value))

    def _hide_progress(self) -> None:
        bar = getattr(self, "frame_progress", None)
        if bar is not None:
            bar.setVisible(False)

    def closeEvent(self, event):
        if self._task is not None:
            self._set_status("还在跑：先点 [停止]，等当前帧结束再关")
            event.ignore()
            return
        if getattr(self.main, "_generic_insitu_dialog", None) is self:
            self.main._generic_insitu_dialog = None
        sync = getattr(self.workbench, "_sync_preset_combo", None)
        if callable(sync):
            sync(False)
        super().closeEvent(event)
