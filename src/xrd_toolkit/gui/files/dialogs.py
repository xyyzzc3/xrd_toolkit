"""文件栏重名对话框：重复文件询问（覆盖/改名/取消）与改名输入。

2026-10-21 从 file_dock.py 原样搬出（只改结构，不动逻辑）。
file_dock 再导出；add_files 仍从 file_dock 延迟导入，测试 patch 不变。
"""
from PySide6.QtWidgets import QInputDialog, QMainWindow, QMessageBox


def _ask_duplicate(window: QMainWindow, name: str) -> str:
    """同一文件再次加入时的询问弹窗。返回 "overwrite" / "rename" / "cancel"。

    窗口没显示（测试环境）时不弹模态框，默认 "overwrite"（保留原
    条目）——与 _confirm_close 的可见性护栏同理，防测试挂死。
    """
    if not window.isVisible():
        return "overwrite"
    box = QMessageBox(window)
    box.setWindowTitle("文件已存在")
    box.setText(f"{name} 已经在文件栏里了。")
    box.setInformativeText(
        "保留原条目 = 新文件这次不加；改名后加入 = 弹输入框起个新名字"
        "（预填编号名，可自己改）；这次不加 = 跳过这个文件。")
    btn_overwrite = box.addButton("保留原条目", QMessageBox.AcceptRole)
    btn_rename = box.addButton("改名后加入", QMessageBox.ActionRole)
    btn_cancel = box.addButton("这次不加", QMessageBox.RejectRole)
    box.setDefaultButton(btn_overwrite)
    box.exec()
    clicked = box.clickedButton()
    if clicked is btn_rename:
        return "rename"
    if clicked is btn_overwrite:
        return "overwrite"
    return "cancel"

def _ask_rename(window: QMainWindow, default_name: str):
    """改名输入框：预填默认编号名，用户可以改成自己想要的名字。

    返回用户输入（去首尾空格）；取消或输入为空 → None。窗口没显示
    （测试环境）不弹模态框，直接返回默认名（防挂死）。
    """
    if not window.isVisible():
        return default_name
    text, ok = QInputDialog.getText(
        window, "改名", "给这个重复文件起个新名字：", text=default_name)
    text = text.strip()
    if not ok or not text:
        return None
    return text

