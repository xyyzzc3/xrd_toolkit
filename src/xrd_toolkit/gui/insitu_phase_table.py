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
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QHeaderView, QInputDialog,
    QTableWidget, QTableWidgetItem)

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
        for col in (0, 2, 3, 4, 5):
            hh.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        self.setToolTip(
            "每相一行：角色（样品相/参考相）、晶胞是否自由、强度用结构因子"
            "还是 Le Bail、是否计入样品分数分母；B/峰宽 = 额外相自己的"
            "温度因子/尺寸/应变也参与精修（默认固定，更快更稳）。"
            "第一相样品相是主相；用 [上移/下移] 调顺序。")

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
        # 切角色时套默认：参考相 = 固定晶胞 + Le Bail + 不进分母
        self.cellWidget(row, 2).setChecked(not is_sample)
        self.cellWidget(row, 3).setCurrentIndex(1 if is_sample else 0)
        self.cellWidget(row, 4).setChecked(not is_sample)
        self.dirty = True
        return True

    def rename_selected(self, parent):
        row = self.currentRow()
        item = self.item(row, 1) if row >= 0 else None
        if item is None:
            return False
        text, ok = QInputDialog.getText(
            parent, "相的名字", "界面/图里显示的名字：", text=item.text())
        if not ok or not str(text).strip():
            return False
        item.setText(str(text).strip())
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
