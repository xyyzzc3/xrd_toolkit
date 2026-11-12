"""校准模式进出：开/关校准面板 + 参数坞宽度拉宽/还原 + 条目名预填。

2026-10-22 从 calib.py 原样搬出（只改结构，不动逻辑）。
calib 再导出。
"""
import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow

from xrd_toolkit import config
from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui.calib_panel import (
    _calib_standard_path, _close_calib_panel, _open_calib_panel)
from xrd_toolkit.gui.config_ops import _suggest_config_key
from xrd_toolkit.gui.files.model import FILE_FILTER
from xrd_toolkit.gui.panel_layout import _set_bottom_strip_visible
from xrd_toolkit.gui.panel_state import _log


# ══ 模式进出（app._on_mode 调用）══════════════════════════════
def _default_config_name(window: QMainWindow) -> str:
    """「条目名称」的预填值：标样文件名去掉末尾的「-数字」段。

    用户 2026-10-07："预填文件名的不含-后的长数字部分"——lab6-00024.tif
    → lab6；LMFP_1_atten0-00029.tif → LMFP_1_atten0。没有标样（只在
    校准页里借条目 / 手输几何）时退回按当前条目递推（lmfp1_lab6 →
    lmfp2_lab6，见 config_ops._suggest_config_key）。
    """
    path = getattr(window, "calib_path", None)
    if path:
        stem = Path(path).stem
        cleaned = re.sub(r"-\d+$", "", stem)
        return cleaned or stem
    return _suggest_config_key(config.DEFAULT_CONFIG)

def _refresh_config_name_prefill(window: QMainWindow) -> None:
    """进入校准时把「条目名称」按当前标样文件名重填。

    只覆盖"还挂着上一次预填值"的框（或空框）——用户自己改过的名字不动。
    """
    box = getattr(window, "calib_name_edit", None)
    if box is None:
        return
    if box.text().strip() not in ("", getattr(window, "_calib_name_auto", None)):
        return   # 用户改过：不动
    box.setText(_default_config_name(window))
    window._calib_name_auto = box.text()

def _refresh_calib_file_combo(window: QMainWindow) -> None:
    """把文件栏里的原始数据搬进校准页「标样文件」下拉。

    保留当前 `calib_path`；不在文件栏的（[打开…] 临时选的）也补一条，
    避免下拉和实际标样对不上。文件栏里双击原始图也能换标样，
    这个下拉是同一件事的镜像/兜底。
    """
    combo = getattr(window, "calib_file_combo", None)
    if combo is None:
        return
    keep = str(getattr(window, "calib_path", None) or "")
    combo.blockSignals(True)
    combo.clear()
    for src in gui_sources.all_sources(window):
        if src.kind == gui_sources.RAW:
            combo.addItem(src.display or Path(src.path).name, src.path)
    if keep and combo.findData(keep) < 0:
        combo.addItem(f"{Path(keep).name}（不在文件栏）", keep)
    idx = combo.findData(keep) if keep else -1
    if idx < 0 and combo.count():
        idx = 0
    combo.setCurrentIndex(idx)
    combo.blockSignals(False)
    if combo.count() == 0:
        combo.addItem("（还没有原始数据：点 [打开…] 选标样）", "")


def _select_calib_file(window: QMainWindow, path) -> None:
    """选一张标样：开/换校准面板、预填条目名、同步下拉。

    下拉换标样与文件栏双击（2026-11-11：双击 = 对这一个操作）共用。
    """
    path = Path(path)
    if (getattr(window, "calib_dock", None) is not None
            and getattr(window, "calib_key", None) == f"校准|{path}"):
        return
    _open_calib_panel(window, path)
    if getattr(window, "calib_dock", None) is not None:
        _refresh_config_name_prefill(window)
        _refresh_calib_file_combo(window)
        _log(window, f"标样文件：{path.name}")


def _on_calib_file_changed(window: QMainWindow, index: int) -> None:
    """下拉换标样：立即开/换校准面板（不再依赖文件栏勾选）。"""
    combo = getattr(window, "calib_file_combo", None)
    path = combo.itemData(index) if combo is not None else None
    if path:
        _select_calib_file(window, path)


def _choose_calib_file(window: QMainWindow) -> None:
    """[打开…]：选一张不在文件栏里的标样，直接开校准面板。"""
    start = getattr(window, "calib_path", None) or Path.home()
    path, _ = QFileDialog.getOpenFileName(
        window, "选择标样文件", str(start), FILE_FILTER)
    if not path:
        return
    _select_calib_file(window, Path(path))


def _enter_calib(window: QMainWindow) -> None:
    """进入校准模式（[校准] 按下）：露出参数坞 + 开校准面板。

    没选标样只记日志提示（不崩）——校准页顶部有「标样文件」下拉和
    [打开…]，选了就开面板。2026-11-12 起不再按内容拉宽参数坞（用户：
    "开功能参数栏会拉伸"）；校准页自己的控件允许压窄/省略，普通坞宽
    就能放下，绘图区不会忽宽忽窄。
    """
    # 开局参数坞可能是收起的（_clear_entrance）：进校准先露出来。
    # 2026-11-12 起不再按内容拉宽坞（用户："开功能参数栏会拉伸"）——
    # 校准页的下拉允许压窄、长文件名省略，页面最小宽度落在普通坞宽内。
    window.param_dock.setVisible(True)
    # 校准也是工作台：底部坐标/横排/缩放/全部关闭对校准页没有意义，
    # 整条隐藏（2026-11-05，与四个中央工作台同一口径）。放在"没勾文件"
    # 提前返回之前——没文件也要能退出，退出时统一恢复。
    _set_bottom_strip_visible(window, False)
    # 文件栏保持可见（2026-11-11 晚：双击 = 对这一个操作，校准也不例外）；
    # 页顶「标样文件」下拉仍作镜像/兜底。退出时按进入前的状态恢复。
    if hasattr(window, "_dock_intent"):
        if getattr(window, "_calib_dock_saved", None) is None:
            window._calib_dock_saved = {
                "文件": window._dock_intent.get("文件", True)}
        window._dock_intent["文件"] = True
        # 进校准露坞同理记意图（见 app._switch_entrance 的同款记账）：不记
        # 的话意图还停在开局的 False，窗口状态一变（全屏/最小化恢复）就会
        # 被 _resync_docks 收回去。和文件栏一起 resync，避免先把参数坞
        # 按旧意图收起来。
        window._dock_intent["参数"] = True
        from xrd_toolkit.gui.app import _resync_docks   # 破循环
        _resync_docks(window)
    # 出口按钮（坞顶那一行）：本模式的显式退出口，进来就亮出来。
    # 放在"没勾文件"的提前返回之前——没文件也要能退出去
    window.calib_exit_btn.setVisible(True)
    _refresh_calib_file_combo(window)
    checked = gui_sources.first_checked_source(
        window, (gui_sources.RAW,))
    path = _calib_standard_path(window)
    if path is None:
        _log(window, "请在上方「标样文件」选择一张原始图，或点 [打开…]")
        return
    # 首次画 2048² 校正图要几百毫秒：亮等待光标，别让用户以为点空了
    QApplication.setOverrideCursor(Qt.WaitCursor)
    try:
        _open_calib_panel(window, path)
        if (checked is not None
                and getattr(window, "calib_dock", None) is not None):
            _log(window,
                 f"校准：用文件栏勾选的第 1 张原始图 {checked.display}")
        _refresh_calib_file_combo(window)   # 下拉镜像到实际打开的标样
        _refresh_config_name_prefill(window)   # 「条目名称」跟着标样重填
    finally:
        QApplication.restoreOverrideCursor()

def _exit_calib(window: QMainWindow) -> None:
    """退出校准模式：关校准面板（关闭即遗忘）。"""
    window.calib_exit_btn.setVisible(False)
    _close_calib_panel(window)
    _set_bottom_strip_visible(window, True)
    saved = getattr(window, "_calib_dock_saved", None)
    if saved:
        window._dock_intent.update(saved)
        window._calib_dock_saved = None
        from xrd_toolkit.gui.app import _resync_docks   # 破循环
        _resync_docks(window)
