"""项目预设的 GUI 操作（v0.3 B1，2026-11-10）。

分工：
  * `presets.py` 管数据（内置/用户预设、读写 `presets_user.json`）；
  * 本模块管界面：参数坞的预设下拉、[保存…]/[管理…] 弹窗，以及把预设
    应用到当前窗口（几何 + 物相/精修相库 + 原位目录/mask）。

应用规则：
  * 切预设会先检查物相/精修/原位三个工作台有没有后台任务在跑——有就
    拒绝切换，避免"报告写到一半输入被换掉"。
  * 几何配置不存在（例如用户删了那条 config）时，`presets.resolve_preset`
    已经退回默认；这里只按退回后的值应用。
  * 预设只改默认输入，不重跑任何计算；已有结果留着，物相旧候选会被
    清掉（相库/第二相变了，旧候选不再对应）。
"""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout)

from xrd_toolkit import paths
from xrd_toolkit.config import CONFIGS, entry_display
from xrd_toolkit.gui.panel_state import _log
from xrd_toolkit.gui.tasks import user_error_text
from xrd_toolkit.presets import (
    DEFAULT_PRESET_NAME, get_preset, is_builtin, preset_names,
    preset_phase_library, remove_user_preset, save_user_preset,
    set_active_preset)


def _busy_workbenches(window) -> list:
    """正在跑后台任务的物相/精修/原位工作台（中文名）。"""
    out = []
    for attr, label in (("phase_editor", "物相"),
                        ("refine_editor", "精修"),
                        ("insitu_editor", "原位")):
        editor = getattr(window, attr, None)
        if editor is not None and getattr(editor, "_task", None) is not None:
            out.append(label)
    return out


def _portable_dir(path) -> str:
    """家目录下的路径写成 ~/…（预设文件可搬）；其它保持绝对路径。"""
    path = Path(path).expanduser()
    try:
        rel = path.relative_to(Path.home())
    except ValueError:
        return str(path)
    return "~" if str(rel) == "." else "~/" + str(rel)


def current_preset_values(window) -> dict:
    """把当前窗口实际生效的五项设置读成预设 dict。

    相库优先取已打开的物相/精修工作台；原位目录/mask 取原位工作台的
    当前值；第二相没有独立控件，沿用当前预设里的值。
    """
    base = get_preset(getattr(window, "preset_name", DEFAULT_PRESET_NAME))
    phase_lib = preset_phase_library(base)
    editor = (getattr(window, "phase_editor", None)
              or getattr(window, "refine_editor", None))
    if editor is not None and getattr(editor, "lib_dir", None) is not None:
        phase_lib = Path(editor.lib_dir)
    values = {
        "geometry": (getattr(window, "config_name", "")
                     or base["geometry"]),
        "second_phase_cif": base.get("second_phase_cif", ""),
        "insitu_dir": base.get("insitu_dir", ""),
        "insitu_mask_name": base.get("insitu_mask_name", ""),
        "phase_library": ("" if phase_lib == paths.structures_dir()
                          else _portable_dir(phase_lib)),
    }
    insitu = getattr(window, "insitu_editor", None)
    if insitu is not None:
        values["insitu_dir"] = _portable_dir(insitu.data_dir)
        values["insitu_mask_name"] = (insitu.mask_path.name
                                      if insitu.mask_path else "")
    return values


def refresh_preset_combo(window, select=None) -> None:
    """重建预设下拉（内置在前、用户预设排序），并选中 select。"""
    combo = getattr(window, "preset_combo", None)
    if combo is None:
        return
    current = str(select or getattr(window, "preset_name",
                                   DEFAULT_PRESET_NAME))
    combo.blockSignals(True)
    combo.clear()
    for name in preset_names():
        combo.addItem(name, name)
        combo.setItemData(
            combo.count() - 1,
            "内置预设（不可删除）" if is_builtin(name) else "用户预设",
            Qt.ToolTipRole)
    idx = combo.findData(current)
    combo.setCurrentIndex(idx if idx >= 0 else 0)
    combo.blockSignals(False)


def apply_preset(window, name, *, persist=True) -> bool:
    """应用一份预设到当前窗口；成功 True。

    失败只做提示、不改状态：有工作台任务在跑，或预设里的几何配置
    不存在。
    """
    preset = get_preset(name)
    busy = _busy_workbenches(window)
    if busy:
        QMessageBox.information(
            window, "项目预设",
            "、".join(busy) + " 工作台还有任务在跑。\n"
            "等它结束（或先停止）再切换预设。")
        return False
    combo = getattr(window, "config_combo", None)
    idx = combo.findData(preset["geometry"]) if combo is not None else -1
    if idx < 0:
        QMessageBox.warning(
            window, "项目预设",
            f"预设「{preset['name']}」里的几何配置 "
            f"{preset['geometry']!r} 不在当前注册表里。")
        return False

    window.preset_name = preset["name"]
    window.preset = preset
    if combo.currentIndex() != idx:
        combo.setCurrentIndex(idx)          # 触发 _apply_config
    for attr, method in (("phase_editor", "apply_preset"),
                         ("refine_editor", "apply_preset"),
                         ("insitu_editor", "apply_preset")):
        editor = getattr(window, attr, None)
        if editor is not None and hasattr(editor, method):
            getattr(editor, method)(preset)
    if persist:
        try:
            set_active_preset(preset["name"])
        except (OSError, ValueError) as err:
            _log(window, f"预设状态写入失败：{user_error_text(err)}")
    refresh_preset_combo(window, preset["name"])
    for warning in preset.get("warnings") or []:
        _log(window, f"预设提示：{warning}")
    _log(window, f"已应用项目预设：{preset['name']}"
                 f"（几何 {preset['geometry']}）")
    return True


def _on_preset_combo(window, index) -> None:
    """下拉换预设：应用失败就退回原选择。"""
    combo = getattr(window, "preset_combo", None)
    if combo is None:
        return
    name = combo.itemData(index) or combo.currentText()
    if not name or name == getattr(window, "preset_name", None):
        return
    if not apply_preset(window, name):
        refresh_preset_combo(window, getattr(window, "preset_name",
                                             DEFAULT_PRESET_NAME))


def save_current_preset(window, name, values=None) -> bool:
    """把当前设置存成用户预设并应用；返回是否保存成功。

    values 给定时用它（保存对话框里改过第二相），否则现场读窗口。
    """
    name = str(name).strip()
    if not name:
        return False
    try:
        is_new = save_user_preset(
            name, values if values is not None
            else current_preset_values(window))
    except (OSError, ValueError) as err:
        QMessageBox.warning(window, "项目预设", user_error_text(err))
        return False
    refresh_preset_combo(window, name)
    _log(window, f"预设已{'新建' if is_new else '更新'}：{name}")
    if not apply_preset(window, name):
        refresh_preset_combo(window, getattr(window, "preset_name",
                                             DEFAULT_PRESET_NAME))
    return True


def save_preset_dialog(window) -> None:
    """[保存…]：问名字 → 保存当前设置 → 应用。"""
    default = f"{getattr(window, 'preset_name', '')} 副本"
    name, ok = QInputDialog.getText(
        window, "保存为预设", "预设名称：", text=default)
    if not ok:
        return
    name = name.strip()
    if not name:
        return
    if is_builtin(name):
        QMessageBox.warning(
            window, "项目预设",
            f"「{name}」是内置预设，不能覆盖；换个名字。")
        return
    if name in preset_names():
        answer = QMessageBox.question(
            window, "项目预设",
            f"用户预设「{name}」已存在，覆盖它？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return

    values = current_preset_values(window)
    library = (Path(values["phase_library"]).expanduser()
               if values["phase_library"] else paths.structures_dir())
    try:
        cifs = sorted(p.name for p in library.glob("*.cif"))
    except OSError:
        cifs = []
    current = values.get("second_phase_cif", "")
    items = ["（无）"] + cifs
    if current and current not in items:
        items.append(current)
    current_index = items.index(current) if current in items else 0
    second, ok = QInputDialog.getItem(
        window, "第二相", "第二相 CIF（不参与主相候选）：",
        items, current_index, False)
    if not ok:
        return
    values["second_phase_cif"] = "" if second == "（无）" else second
    save_current_preset(window, name, values=values)


def _summary_text(preset) -> str:
    geom = entry_display(preset["geometry"],
                         CONFIGS.get(preset["geometry"], {}))
    library = (preset.get("phase_library")
               or str(paths.structures_dir()))
    return (
        f"几何配置：{geom}\n"
        f"第二相 CIF：{preset.get('second_phase_cif') or '（无）'}\n"
        f"原位帧目录：{preset.get('insitu_dir') or '（未设置）'}\n"
        f"原位 mask：{preset.get('insitu_mask_name') or '（不遮）'}\n"
        f"相库目录：{library}")


class PresetManagerDialog(QDialog):
    """[管理…]：查看 / 应用 / 删除预设；内置预设只读。"""

    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle("管理项目预设")
        self.setMinimumSize(560, 420)
        self.window = window

        root = QVBoxLayout(self)
        hint = QLabel(
            "预设保存五项输入：几何配置、第二相 CIF、原位帧目录、"
            "原位 mask、相库目录。\n"
            "先把工作台里的目录/mask 调好，再点 [保存当前…]。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        root.addWidget(hint)

        body = QHBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._on_selected)
        body.addWidget(self.list, 1)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.summary.setMinimumWidth(260)
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.addWidget(self.summary, 1)
        root.addLayout(body, 1)

        buttons = QHBoxLayout()
        self.apply_btn = QPushButton("应用")
        self.apply_btn.clicked.connect(self._apply)
        self.delete_btn = QPushButton("删除")
        self.delete_btn.clicked.connect(self._delete)
        save_btn = QPushButton("保存当前…")
        save_btn.clicked.connect(self._save_current)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(self.apply_btn)
        buttons.addWidget(self.delete_btn)
        buttons.addWidget(save_btn)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

        self._refresh()

    def _refresh(self):
        current = getattr(self.window, "preset_name", DEFAULT_PRESET_NAME)
        self.list.blockSignals(True)
        self.list.clear()
        for name in preset_names():
            item = QListWidgetItem(
                name + ("    （内置）" if is_builtin(name) else ""))
            item.setData(Qt.UserRole, name)
            self.list.addItem(item)
        self.list.blockSignals(False)
        row = 0
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == current:
                row = i
                break
        self.list.setCurrentRow(row)
        if self.list.count() == 0:
            self._on_selected(-1)

    def _selected_name(self):
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _on_selected(self, _row):
        name = self._selected_name()
        if not name:
            self.summary.setText("")
            self.apply_btn.setEnabled(False)
            self.delete_btn.setEnabled(False)
            return
        self.summary.setText(_summary_text(get_preset(name)))
        self.apply_btn.setEnabled(True)
        self.delete_btn.setEnabled(not is_builtin(name))

    def _apply(self):
        name = self._selected_name()
        if name:
            apply_preset(self.window, name)
            self._refresh()

    def _delete(self):
        name = self._selected_name()
        if not name or is_builtin(name):
            return
        answer = QMessageBox.question(
            self, "删除预设", f"删除用户预设「{name}」？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        try:
            remove_user_preset(name)
        except (OSError, ValueError) as err:
            QMessageBox.warning(self, "项目预设", user_error_text(err))
            return
        _log(self.window, f"预设已删除：{name}")
        if getattr(self.window, "preset_name", None) == name:
            apply_preset(self.window, DEFAULT_PRESET_NAME)
        self._refresh()

    def _save_current(self):
        save_preset_dialog(self.window)
        self._refresh()


def open_preset_manager(window) -> None:
    """[管理…] 入口。"""
    PresetManagerDialog(window).exec()
