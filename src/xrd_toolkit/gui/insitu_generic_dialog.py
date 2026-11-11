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
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget)

from xrd_toolkit import paths
from xrd_toolkit.config import get_config
from xrd_toolkit.gui.panel_state import _log
from xrd_toolkit.gui.tasks import BackgroundTask


def _prepare_worker(phase_paths, folder, config_name, mask_path, instrument,
                    max_rwp, limit, npt, tth_min, tth_max,
                    extra_lebail, extra_cell):
    """后台准备：读结构/mask、列帧。不能碰界面控件。"""
    from xrd_toolkit.simulation.insitu_generic import (
        GenericBatch, frames_for_dir, load_mask_file)
    geom = dict(get_config(config_name)["geometry"])
    lam = float(geom["wavelength_m"]) * 1e10
    mask = load_mask_file(mask_path) if mask_path else None
    batch = GenericBatch(
        phase_paths, geom, lam, instrument=instrument, mask=mask,
        max_rwp=max_rwp, first_nfev=120, frame_nfev=60,
        npt=npt, tth_min=tth_min, tth_max=tth_max,
        extra_lebail=bool(extra_lebail), extra_cell=bool(extra_cell))
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
        row.addWidget(QLabel("相（第一行 = 主相）"))
        self.phase_list = QListWidget()
        self.phase_list.setMaximumHeight(110)
        self.phase_list.setToolTip(
            "第一张 CIF = 主相（用结构因子算强度）；后面的都是额外相")
        row.addWidget(self.phase_list, 1)
        side = QVBoxLayout()
        for text, slot in (("加 CIF…", self._add_phases),
                           ("移除", self._remove_phase),
                           ("设为主相", self._set_main_phase)):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            side.addWidget(btn)
        side.addStretch(1)
        row.addLayout(side)
        root.addLayout(row)

        opts = QHBoxLayout()
        self.chk_lebail = QCheckBox("额外相峰强自由（Le Bail）")
        self.chk_lebail.setToolTip(
            "未知相/织构强时勾上：每根峰强度单独解出；不勾 = 用 CIF 结构因子")
        self.chk_lebail.setChecked(False)
        opts.addWidget(self.chk_lebail)
        self.chk_extra_cell = QCheckBox("额外相晶胞自由")
        self.chk_extra_cell.setToolTip(
            "勾上 = 额外相的 a/b/c 也参与精修（只对正交/立方相开放）；"
            "默认固定，避免少相晶胞漂移")
        self.chk_extra_cell.setChecked(False)
        opts.addWidget(self.chk_extra_cell)
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

        self.progress = QLabel("选好帧目录与相，点 [开始]")
        self.progress.setStyleSheet("color: gray;")
        root.addWidget(self.progress)

        self.table = QTableWidget(0, 0)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setFrameShape(QFrame.NoFrame)
        root.addWidget(self.table, 1)

        if self._instrument is None:
            self.instrument_label.setText(
                "仪器基线：无（只精修零位；峰宽按默认估计）")
        else:
            self.instrument_label.setText(
                f"仪器基线：已加载（λ {self._current_wavelength():.4f} Å）")

    def _seed_phases(self):
        paths_now = getattr(self.workbench, "phase_paths", {}) or {}
        for path in paths_now.values():
            if path and Path(path).is_file():
                self._append_phase(Path(path))

    def _append_phase(self, path):
        item = QListWidgetItem(str(path))
        item.setData(Qt.UserRole, str(path))
        item.setToolTip(str(path))
        self.phase_list.addItem(item)
        self._refresh_phase_items()

    def _refresh_phase_items(self):
        for i in range(self.phase_list.count()):
            item = self.phase_list.item(i)
            path = Path(item.data(Qt.UserRole))
            prefix = "主相" if i == 0 else f"额外相 {i}"
            item.setText(f"{prefix}：{path.stem}")
            item.setToolTip(str(path))

    def _add_phases(self):
        start = (str(paths.phase_library_dir())
                 if paths.phase_library_dir().is_dir()
                 else str(paths.structures_dir()))
        files, _ = QFileDialog.getOpenFileNames(
            self, "加 CIF（按列表顺序：第一张是主相）", start,
            "CIF (*.cif);;所有文件 (*)")
        for name in files:
            self._append_phase(Path(name))

    def _remove_phase(self):
        row = self.phase_list.currentRow()
        if row >= 0:
            self.phase_list.takeItem(row)
            self._refresh_phase_items()

    def _set_main_phase(self):
        row = self.phase_list.currentRow()
        if row <= 0:
            return
        item = self.phase_list.takeItem(row)
        self.phase_list.insertItem(0, item)
        self.phase_list.setCurrentRow(0)
        self._refresh_phase_items()

    def _choose_folder(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "选帧目录", self.folder_edit.text())
        if chosen:
            self.folder_edit.setText(chosen)

    def _choose_mask(self):
        start = self.mask_edit.text() or str(paths.OUTPUTS_DIR)
        name, _ = QFileDialog.getOpenFileName(
            self, "选掩膜", start,
            "掩膜 (*.npy *.edf);;所有文件 (*)")
        if name:
            self.mask_edit.setText(name)

    def _choose_outdir(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "选输出目录", self.out_edit.text())
        if chosen:
            self.out_edit.setText(chosen)

    # ── 运行 ───────────────────────────────────────────────
    def _phase_paths(self):
        return [self.phase_list.item(i).data(Qt.UserRole)
                for i in range(self.phase_list.count())]

    def _start(self):
        if self._task is not None:
            return
        folder = Path(self.folder_edit.text()).expanduser()
        if not folder.is_dir():
            self._set_status("帧目录不存在")
            return
        phase_paths = self._phase_paths()
        if not phase_paths:
            self._set_status("先 [加 CIF…] 至少加一张相")
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
        self._set_status("准备中（读结构 + 掩膜 + 列帧）…")
        _log(self.main, "通用原位多相：准备开始")
        self._task = BackgroundTask(
            _prepare_worker, phase_paths, folder, self.main.config_name,
            self.mask_edit.text().strip(), self._instrument,
            float(self.max_rwp.value()), int(self.limit.value()),
            int(self.workbench.npt.value()),
            float(self.workbench.tth_min.value()),
            float(self.workbench.tth_max.value()),
            self.chk_lebail.isChecked(), self.chk_extra_cell.isChecked(),
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
        self._set_status(f"开始：{len(self._frames)} 帧，"
                         f"主相 {self.batch.phases[0].label}")
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
        self._start_next()

    def _frame_failed(self, message):
        self._task = None
        self._set_status(f"这一帧失败：{message}")
        self._index += 1
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

    def _request_stop(self):
        self._stop = True
        self._set_status("已请求停止：当前帧算完就停")

    def _finish(self):
        self._reset_buttons()
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
        self._set_status(f"准备失败：{message}")

    def _set_status(self, text):
        self.progress.setText(text)

    def closeEvent(self, event):
        if self._task is not None:
            self._set_status("还在跑：先点 [停止]，等当前帧结束再关")
            event.ignore()
            return
        if getattr(self.main, "_generic_insitu_dialog", None) is self:
            self.main._generic_insitu_dialog = None
        super().closeEvent(event)
