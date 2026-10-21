"""校准模式进出：开/关校准面板 + 参数坞宽度拉宽/还原 + 条目名预填。

2026-10-22 从 calib.py 原样搬出（只改结构，不动逻辑）。
calib 再导出。
"""
import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit import config
from xrd_toolkit.gui.calib_model import CALIB_PANEL_RESERVE_PX
from xrd_toolkit.gui.calib_panel import (
    _calib_standard_path, _close_calib_panel, _open_calib_panel)
from xrd_toolkit.gui.config_ops import _suggest_config_key
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

def _enter_calib(window: QMainWindow) -> None:
    """进入校准模式（[校准] 按下）：开校准面板 + 按校准页内容拉宽参数坞。

    没勾文件只记日志提示（不崩）——用户勾好文件后点 [定位束心并精修]
    也能开面板。宽度只在够得着时拉：给绘图区留 CALIB_PANEL_RESERVE_PX
    （点环选点是在图上做的，坞太宽就没法点了）。
    """
    # 先让参数坞露出来再量宽：开局它是收起的（_clear_entrance），
    # 隐藏时量到的是旧值/默认值 → 拉宽和"退出还原"都会拿假数字
    # （[校准] 的 toggled 先于 clicked 触发，所以不能指望入口那边先
    # 露坞——2026-09-26 实测：以前量出来 327 px，远没到该有的宽度）
    window.param_dock.setVisible(True)
    # 进校准露坞同理记意图（见 app._switch_entrance 的同款记账）：不记的话
    # 意图还停在开局的 False，窗口状态一变（全屏/最小化恢复）就会被
    # _resync_docks 收回去
    if hasattr(window, "_dock_intent"):
        window._dock_intent["参数"] = True
    # 出口按钮（坞顶那一行）：本模式的显式退出口，进来就亮出来。
    # 放在"没勾文件"的提前返回之前——没文件也要能退出去
    window.calib_exit_btn.setVisible(True)
    path = _calib_standard_path(window)
    if path is None:
        _log(window, "请先在文件列表勾选标样文件")
        return
    _open_calib_panel(window, path)
    _refresh_config_name_prefill(window)   # 「条目名称」跟着这个标样重填
    _widen_dock_for_calib(window)

def _exit_calib(window: QMainWindow) -> None:
    """退出校准模式：关校准面板（关闭即遗忘）+ 参数坞宽度还原。"""
    window.calib_exit_btn.setVisible(False)
    _close_calib_panel(window)
    _restore_dock_width(window)

def _widen_dock_for_calib(window: QMainWindow) -> None:
    """校准模式下把参数坞拉宽到"放下校准页所有内容"（不超过窗口上限）。"""
    dock = getattr(window, "param_dock", None)
    page = getattr(window, "calib_scroll", None)
    if dock is None or page is None:
        return
    if getattr(window, "_dock_w_before", None) is None:
        # 记住分析模式的宽度。参数坞开局是收起的（app._clear_entrance，
        # 用户 2026-09-25 定），点 [校准] 时才刚由隐藏转可见、还没走
        # 布局，此刻 width() 是旧值/默认值 → 先手动走一遍布局再量，
        # 否则"退出校准还原宽度"会还原成一个假数字
        lay = window.layout()
        if lay is not None:
            lay.activate()
        window._dock_w_before = dock.width()
    need = page.widget().sizeHint().width() + 24  # 滚动区边框/条余量
    limit = max(360, window.width() - CALIB_PANEL_RESERVE_PX)
    target = int(min(need, limit))
    if dock.isFloating():
        dock.resize(target, dock.height())
    else:
        window.resizeDocks([dock], [target], Qt.Horizontal)
    if need > limit:
        _log(window, f"校准页需要 {need} px，但为保住绘图区（点环用）只给到 "
                     f"{target} px：窄窗口下校准页里可以横向滚动")

def _restore_dock_width(window: QMainWindow) -> None:
    """出校准模式：参数坞宽度还原成进之前那一条。"""
    was = getattr(window, "_dock_w_before", None)
    dock = getattr(window, "param_dock", None)
    if was is None or dock is None:
        return
    if dock.isFloating():
        dock.resize(was, dock.height())
    else:
        window.resizeDocks([dock], [was], Qt.Horizontal)
    window._dock_w_before = None
