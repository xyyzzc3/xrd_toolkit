"""原位方案里的相表（2026-11-18）。

从「通用多相」对话框里抽出来的控件：角色 / 相 / 晶胞 / 强度 / 计入
分数 / B·峰宽 六列，每行同时**完整保留 PhaseSpec 字段**（B、尺寸、
应变、晶胞边界、jump 容差、颜色、cell0、width_free、plot_name）。

为什么留着隐藏字段：旧对话框载入方案时只回填六列，LMFP 预设的
`size_um=0.093`、`cell0=(4.061,...)` 这些字段会被悄悄丢掉，跑出来
就不是金标准口径了。表只负责"相"这一层；策略/视图/仪器由工作台保存。
"""
import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGridLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QWidget)

COLUMNS = ("角色", "相", "晶胞", "强度", "计入分数", "B/峰宽")
ROLE_SAMPLE = "sample"
ROLE_REFERENCE = "reference"
FREE_EXTRA = ("b_iso", "size_um", "mustrain")

_PATH_ROLE = Qt.UserRole
_SPEC_ROLE = Qt.UserRole + 1


def _safe_label(path, used):
    """文件名 → 唯一机器标签（与 phase_set._safe_label 同规则）。"""
    base = re.sub(r"[^0-9A-Za-z_]", "_", Path(path).stem) or "phase"
    label = base
    i = 2
    while label in used:
        label = f"{base}_{i}"
        i += 1
    used.add(label)
    return label


class _NamesDialog(QDialog):
    """改一个相的两个名字：界面显示名、图内名称（2026-11-18）。

    为什么分开：界面/表格用中文名，图例和布拉格刻度行按论文习惯用英文
    （如 "Li-rich phase"）。机器标签（rich/poor）不动——CSV 列名与
    引擎参数都靠它。
    """

    def __init__(self, parent, display, plot):
        super().__init__(parent)
        self.setWindowTitle("相的名字")
        form = QFormLayout(self)
        self.display_edit = QLineEdit(str(display or ""))
        self.display_edit.setPlaceholderText("界面/表格/报告里显示的名字")
        form.addRow("界面显示名", self.display_edit)
        self.plot_edit = QLineEdit(str(plot or ""))
        self.plot_edit.setPlaceholderText("图例/布拉格刻度行用，建议英文")
        form.addRow("图内名称", self.plot_edit)
        hint = QLabel("图内名称留空 = 用界面显示名。")
        hint.setStyleSheet("color: gray;")
        hint.setWordWrap(True)
        form.addRow(hint)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self):
        return (self.display_edit.text().strip(),
                self.plot_edit.text().strip())


class _DetailsDialog(QDialog):
    """每相细节（2026-11-18）：B / 尺寸 / 应变 / 晶胞范围 / jump / 颜色。

    这些字段原本只能改方案 JSON；原位页补上入口后，和精修页的"每相自由"
    对齐（精修页也只有开关，没有数值框）。
    """

    def __init__(self, parent, spec):
        super().__init__(parent)
        self.spec = dict(spec or {})
        self.setWindowTitle("相细节")
        form = QFormLayout(self)

        self.use_cif_b = QCheckBox("从 CIF 取 Uiso")
        self.b_iso = QDoubleSpinBox()
        self.b_iso.setRange(-50.0, 100.0)
        self.b_iso.setDecimals(3)
        self.b_iso.setSingleStep(0.1)
        b_row = QHBoxLayout()
        b_row.addWidget(self.use_cif_b)
        b_row.addWidget(self.b_iso, 1)
        form.addRow("温度因子 B (Å²)", b_row)

        self.size_um = QDoubleSpinBox()
        self.size_um.setRange(1e-4, 1e6)
        self.size_um.setDecimals(4)
        self.size_um.setSingleStep(0.01)
        self.size_um.setSuffix(" µm")
        form.addRow("尺寸", self.size_um)

        self.mustrain = QDoubleSpinBox()
        self.mustrain.setRange(0.0, 1e6)
        self.mustrain.setDecimals(1)
        form.addRow("应变 mustrain", self.mustrain)

        self.width_free = QCheckBox("峰宽随数据精修（结构精修相）")
        form.addRow("", self.width_free)

        bounds = QWidget()
        grid = QGridLayout(bounds)
        grid.setContentsMargins(0, 0, 0, 0)
        self.use_bounds = QCheckBox("限晶胞范围 (Å)")
        grid.addWidget(self.use_bounds, 0, 0, 1, 3)
        grid.addWidget(QLabel("下限"), 1, 1)
        grid.addWidget(QLabel("上限"), 1, 2)
        self.bound_spins = {}
        for row, axis in enumerate(("a", "b", "c"), start=2):
            grid.addWidget(QLabel(axis), row, 0)
            lo = QDoubleSpinBox()
            lo.setRange(0.0, 50.0)
            lo.setDecimals(3)
            hi = QDoubleSpinBox()
            hi.setRange(0.0, 50.0)
            hi.setDecimals(3)
            self.bound_spins[axis] = (lo, hi)
            grid.addWidget(lo, row, 1)
            grid.addWidget(hi, row, 2)
        form.addRow("晶胞范围", bounds)

        self.use_default_jump = QCheckBox("用方案默认")
        self.jump_tol = QDoubleSpinBox()
        self.jump_tol.setRange(0.001, 2.0)
        self.jump_tol.setDecimals(3)
        self.jump_tol.setSingleStep(0.01)
        jump_row = QHBoxLayout()
        jump_row.addWidget(self.use_default_jump)
        jump_row.addWidget(self.jump_tol, 1)
        form.addRow("晶胞跳变容差 (Å)", jump_row)

        self.color_edit = QLineEdit()
        self.color_edit.setPlaceholderText("#1f5fa8（留空 = 自动配色）")
        color_btn = QPushButton("选…")
        color_btn.clicked.connect(self._pick_color)
        color_row = QHBoxLayout()
        color_row.addWidget(self.color_edit, 1)
        color_row.addWidget(color_btn)
        form.addRow("图里的颜色", color_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self._prefill()
        self.use_cif_b.toggled.connect(
            lambda on: self.b_iso.setEnabled(not on))
        self.use_bounds.toggled.connect(self._toggle_bounds)
        self.use_default_jump.toggled.connect(
            lambda on: self.jump_tol.setEnabled(not on))

    def _prefill(self):
        b_iso = self.spec.get("b_iso")
        self.use_cif_b.setChecked(b_iso is None)
        self.b_iso.setEnabled(b_iso is not None)
        self.b_iso.setValue(0.0 if b_iso is None else float(b_iso))
        self.size_um.setValue(float(self.spec.get("size_um", 1e4)))
        self.mustrain.setValue(float(self.spec.get("mustrain", 0.0)))
        self.width_free.setChecked(bool(self.spec.get("width_free", True)))
        bounds = dict(self.spec.get("cell_bounds") or {})
        self.use_bounds.setChecked(bool(bounds))
        for axis, (lo, hi) in self.bound_spins.items():
            pair = bounds.get(axis)
            if pair:
                lo.setValue(float(pair[0]))
                hi.setValue(float(pair[1]))
            else:
                lo.setValue(0.0)
                hi.setValue(30.0)
        self._toggle_bounds(bool(bounds))
        jump = self.spec.get("jump_tol")
        self.use_default_jump.setChecked(jump is None)
        self.jump_tol.setEnabled(jump is not None)
        self.jump_tol.setValue(0.3 if jump is None else float(jump))
        self.color_edit.setText(str(self.spec.get("color") or ""))

    def _toggle_bounds(self, on):
        for lo, hi in self.bound_spins.values():
            lo.setEnabled(bool(on))
            hi.setEnabled(bool(on))

    def _pick_color(self):
        start = QColor(self.color_edit.text().strip() or "#1f5fa8")
        color = QColorDialog.getColor(start, self, "相的颜色")
        if color.isValid():
            self.color_edit.setText(color.name())

    def values(self):
        spec = dict(self.spec)
        spec["b_iso"] = (
            None if self.use_cif_b.isChecked() else float(self.b_iso.value()))
        spec["size_um"] = float(self.size_um.value())
        spec["mustrain"] = float(self.mustrain.value())
        spec["width_free"] = bool(self.width_free.isChecked())
        if self.use_bounds.isChecked():
            spec["cell_bounds"] = {
                axis: (float(lo.value()), float(hi.value()))
                for axis, (lo, hi) in self.bound_spins.items()}
        else:
            spec["cell_bounds"] = {}
        spec["jump_tol"] = (
            None if self.use_default_jump.isChecked()
            else float(self.jump_tol.value()))
        text = self.color_edit.text().strip()
        spec["color"] = text or None
        return spec


def default_phase(path, used):
    """新加一相的默认字段（与对话框一致：样品相、晶胞自由、结构因子）。"""
    path = Path(path)
    return {
        "path": str(path),
        "label": _safe_label(path, used),
        "display_name": path.stem,
        "plot_name": "",
        "role": ROLE_SAMPLE,
        "cell_free": True,
        "width_free": True,
        "intensity": "structure",
        "include_in_fraction": True,
        "b_iso": None,          # None = 读 CIF 的 Uiso（phase_set_from_dict 解析）
        "size_um": 1e4,
        "mustrain": 0.0,
        "cell_bounds": {},
        "jump_tol": None,
        "color": None,
        "cell0": None,
        "free_extra": (),
    }


class InsituPhaseTable(QTableWidget):
    """相列表：可见六列编辑 + 每行隐藏的完整 PhaseSpec 字段。"""

    def __init__(self, parent=None):
        super().__init__(0, len(COLUMNS), parent)
        self.dirty = False
        self._loading = False
        self.setObjectName("insitu_phase_table")
        self.setHorizontalHeaderLabels(list(COLUMNS))
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.horizontalHeader()
        # 这些列里放的是控件（下拉/复选），ResizeToContents 只量"表头
        # 文字"，会把下拉里的"结构因子"裁掉（2026-11-18 用户反馈）。
        # 给按控件内容定的固定宽度，用户仍可拖动列宽；相名占剩余宽度。
        hh.setMinimumSectionSize(72)
        for col, width in ((0, 96), (2, 76), (3, 118), (4, 100), (5, 96)):
            hh.setSectionResizeMode(col, QHeaderView.Interactive)
            hh.resizeSection(col, width)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setToolTip(
            "每相一行：角色（样品相/参考相）、晶胞是否自由、强度用结构因子"
            "还是 Le Bail、是否计入样品分数分母；B/峰宽 = 该相自己的"
            "温度因子/尺寸/应变也参与精修（默认固定，更快更稳）。"
            "顺序只决定哪条样品相走完整结构精修（原子坐标/占位）；"
            "其余样品相照样计入分数、各有晶胞/强度。用 [上移/下移] 调整；"
            "双击相名可改 B / 尺寸 / 应变 / 晶胞范围 / 颜色。")

    # ── 行内字段 ─────────────────────────────────────────────
    def _add_row(self, spec):
        row = self.rowCount()
        self.insertRow(row)
        role = QComboBox()
        role.addItem("样品相", ROLE_SAMPLE)
        role.addItem("参考相", ROLE_REFERENCE)
        role.setCurrentIndex(
            0 if str(spec.get("role") or ROLE_SAMPLE) == ROLE_SAMPLE else 1)
        role.currentIndexChanged.connect(self._mark_dirty)
        # 用户直接在下拉里改角色也要套参考相默认（2026-11-19 手测：
        # 只改下拉时晶胞/强度/计入分数不动，看起来像"没生效"）。
        role.currentIndexChanged.connect(
            lambda _index, r=row: self._on_role_changed(r))
        self.setCellWidget(row, 0, role)

        name = (spec.get("display_name") or spec.get("label")
                or Path(str(spec.get("path") or "")).stem)
        item = QTableWidgetItem(str(name))
        item.setData(_PATH_ROLE, str(spec.get("path") or ""))
        item.setData(_SPEC_ROLE, dict(spec))
        item.setToolTip(str(spec.get("path") or ""))
        self.setItem(row, 1, item)

        cell = QCheckBox()
        cell.setChecked(bool(spec.get("cell_free", True)))
        cell.setToolTip("勾上 = 该相 a/b/c 参与精修（只对正交/立方开放）")
        cell.toggled.connect(self._mark_dirty)
        self.setCellWidget(row, 2, cell)

        kind = QComboBox()
        kind.addItem("结构因子", "structure")
        kind.addItem("Le Bail", "lebail")
        kind.setCurrentIndex(
            1 if str(spec.get("intensity") or "structure") == "lebail" else 0)
        kind.setToolTip("参考相/织构强时用 Le Bail：每根峰强度单独解出")
        kind.currentIndexChanged.connect(self._mark_dirty)
        self.setCellWidget(row, 3, kind)

        include = QCheckBox()
        include.setChecked(bool(spec.get("include_in_fraction", True)))
        include.setToolTip("参考相（如 Al）取消勾选：不进入样品分数分母")
        include.toggled.connect(self._mark_dirty)
        self.setCellWidget(row, 4, include)

        bfree = QCheckBox()
        bfree.setChecked(bool(spec.get("free_extra")))
        bfree.setToolTip(
            "额外相自己的 B/尺寸/应变也参与精修；默认固定（更快、更稳）")
        bfree.toggled.connect(self._mark_dirty)
        self.setCellWidget(row, 5, bfree)

    def _mark_dirty(self, *_args):
        if not self._loading:
            self.dirty = True

    def _on_role_changed(self, row):
        """行内下拉改角色时套上该角色的默认设置（2026-11-19 用户手测）。"""
        if self._loading or row < 0:
            return
        role = self.cellWidget(row, 0)
        if role is None:
            return
        self._apply_role_defaults(row, role.currentData() == ROLE_SAMPLE)

    def _apply_role_defaults(self, row, is_sample):
        """参考相 = 固定晶胞 + Le Bail + 不进分母；样品相恢复默认。"""
        cell = self.cellWidget(row, 2)
        kind = self.cellWidget(row, 3)
        include = self.cellWidget(row, 4)
        if cell is None or kind is None or include is None:
            return
        cell.setChecked(bool(is_sample))
        kind.setCurrentIndex(0 if is_sample else 1)
        include.setChecked(bool(is_sample))
        self.dirty = True

    # ── 载入 / 导出 ──────────────────────────────────────────
    def load(self, phases):
        """用字段字典列表重填（来自 PhaseSet / 方案文件）。"""
        self._loading = True
        try:
            self.setRowCount(0)
            for spec in phases or ():
                self._add_row(spec)
            self.dirty = False
        finally:
            self._loading = False

    def is_empty(self):
        return self.rowCount() == 0

    def phases(self):
        """当前相表 → JSON 友好的字段字典列表（可直接拼方案）。"""
        out = []
        for row in range(self.rowCount()):
            item = self.item(row, 1)
            if item is None:
                continue
            spec = dict(item.data(_SPEC_ROLE) or {})
            path = str(item.data(_PATH_ROLE) or "")
            if not path:
                continue
            spec["path"] = path
            spec["display_name"] = item.text()
            spec["role"] = (
                self.cellWidget(row, 0).currentData() or ROLE_SAMPLE)
            spec["cell_free"] = bool(self.cellWidget(row, 2).isChecked())
            spec["intensity"] = (
                self.cellWidget(row, 3).currentData() or "structure")
            spec["include_in_fraction"] = bool(
                self.cellWidget(row, 4).isChecked())
            spec["free_extra"] = (
                FREE_EXTRA if self.cellWidget(row, 5).isChecked() else ())
            out.append(spec)
        return out

    # ── 按钮动作 ────────────────────────────────────────────
    def add_paths(self, paths):
        used = {phase["label"] for phase in self.phases()}
        added = 0
        for path in paths or ():
            if not Path(path).is_file():
                continue
            self._loading = True
            try:
                self._add_row(default_phase(path, used))
            finally:
                self._loading = False
            added += 1
        if added:
            self.dirty = True
        return added

    def remove_selected(self):
        row = self.currentRow()
        if row < 0:
            return False
        self.removeRow(row)
        self.dirty = True
        return True

    def toggle_role(self):
        row = self.currentRow()
        if row < 0:
            return False
        role = self.cellWidget(row, 0)
        if role is None:
            return False
        is_sample = role.currentData() == ROLE_SAMPLE
        role.setCurrentIndex(1 if is_sample else 0)
        # 上面的下标变化会触发 _on_role_changed，自动套角色默认值。
        self.dirty = True
        return True

    def rename_selected(self, parent):
        row = self.currentRow()
        item = self.item(row, 1) if row >= 0 else None
        if item is None:
            return False
        spec = dict(item.data(_SPEC_ROLE) or {})
        dlg = _NamesDialog(parent, item.text(), spec.get("plot_name", ""))
        if dlg.exec() != QDialog.Accepted:
            return False
        display, plot = dlg.values()
        if not display:
            display = str(spec.get("label") or item.text())
        item.setText(display)
        spec["plot_name"] = plot
        item.setData(_SPEC_ROLE, spec)
        self.dirty = True
        return True

    def edit_selected_details(self, parent):
        row = self.currentRow()
        item = self.item(row, 1) if row >= 0 else None
        if item is None:
            return False
        spec = dict(item.data(_SPEC_ROLE) or {})
        dlg = _DetailsDialog(parent, spec)
        if dlg.exec() != QDialog.Accepted:
            return False
        item.setData(_SPEC_ROLE, dlg.values())
        self.dirty = True
        return True

    def move_selected(self, delta):
        row = self.currentRow()
        target = row + int(delta)
        if row < 0 or target < 0 or target >= self.rowCount():
            return False
        phases = self.phases()
        phases[row], phases[target] = phases[target], phases[row]
        self.load(phases)
        self.dirty = True          # 换顺序 = 有未保存修改
        self.setCurrentCell(target, 1)
        return True

    def sample_count(self):
        return sum(1 for phase in self.phases()
                   if phase.get("role") == ROLE_SAMPLE)
