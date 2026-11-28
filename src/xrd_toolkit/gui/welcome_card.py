"""空工作区的欢迎卡（2026-11-28 视觉第五轮）。

只在绘图区**没有任何子窗口**时显示：静态卡片，不新增按钮或交互。
第一印象从「一块空的浅灰画布」变成「三步上手」。

实现上它是 MDI viewport 的子控件，不是 MDI 子窗口——否则会进
subWindowList、被平铺/弹出/关闭影响，也会被测试当成一张图。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget)

from xrd_toolkit import __version__
from xrd_toolkit.gui.theme import (
    ACCENT, ACCENT_SOFT, BORDER_STRONG, MUTED, SURFACE, TEXT)


class WelcomeCard(QWidget):
    """空态欢迎卡：软件名、版本、三步和一句数据去向。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("welcomeCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(470)
        self.setStyleSheet(
            f"QWidget#welcomeCard {{"
            f" background: {SURFACE};"
            f" border: 1px solid {BORDER_STRONG};"
            f" border-radius: 10px; }}"
            f"QLabel {{ background: transparent; border: none; }}")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        title = QLabel("XRD Toolkit")
        title.setStyleSheet(
            f"color: {TEXT}; font-size: 18pt; font-weight: 600;")
        head.addWidget(title)
        head.addStretch(1)
        version = QLabel(f"v{__version__}")
        version.setStyleSheet(
            f"color: {MUTED}; font-size: 9pt; padding-top: 7px;")
        head.addWidget(version, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)

        subtitle = QLabel("粉末衍射数据处理与 Rietveld 分析")
        subtitle.setStyleSheet(f"color: {MUTED}; font-size: 10pt;")
        lay.addWidget(subtitle)

        lay.addSpacing(14)
        rule = QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet(
            f"background: {BORDER_STRONG}; border: none;")
        lay.addWidget(rule)
        lay.addSpacing(14)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        steps = (
            ("打开数据", "文件栏 [打开…]，或把文件拖进来"),
            ("校准几何", "[校准] 用 LaB₆ 标样定束心与距离"),
            ("生成 1D", "勾选原始数据，点 [1D] 出图"),
        )
        for index, (step_title, step_hint) in enumerate(steps):
            badge = QLabel(str(index + 1))
            badge.setFixedSize(22, 22)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet(
                f"background: {ACCENT_SOFT}; color: {ACCENT};"
                f" border-radius: 11px; font-weight: 600;"
                f" font-size: 9pt;")
            grid.addWidget(badge, index, 0,
                           Qt.AlignmentFlag.AlignTop)
            text = QVBoxLayout()
            text.setContentsMargins(0, 0, 0, 0)
            text.setSpacing(1)
            name = QLabel(step_title)
            name.setStyleSheet(f"color: {TEXT}; font-weight: 600;")
            text.addWidget(name)
            hint = QLabel(step_hint)
            hint.setWordWrap(True)
            hint.setStyleSheet(f"color: {MUTED}; font-size: 9pt;")
            text.addWidget(hint)
            grid.addLayout(text, index, 1)
        lay.addLayout(grid)

        lay.addSpacing(16)
        note = QLabel("所有数据只在本机处理 · 按 [帮助] 查看完整说明")
        note.setStyleSheet(f"color: {MUTED}; font-size: 8.5pt;")
        lay.addWidget(note)

        self.adjustSize()


def sync_welcome(window) -> None:
    """按 MDI 子窗口数量显示/定位欢迎卡。"""
    card = getattr(window, "welcome_card", None)
    mdi = getattr(window, "mdi", None)
    if card is None or mdi is None:
        return
    if mdi.subWindowList():
        card.hide()
        return
    card.adjustSize()
    area = mdi.viewport().rect()
    x = area.x() + max(0, (area.width() - card.width()) // 2)
    y = area.y() + max(0, (area.height() - card.height()) // 2)
    card.move(x, y)
    card.show()
    card.raise_()


class WelcomeCardFilter(QObject):
    """监听 viewport 的尺寸和子窗口增删，同步欢迎卡。"""

    def __init__(self, window):
        super().__init__(window)
        self.window = window

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.ChildAdded,
                            QEvent.Type.ChildRemoved):
            # 子窗口增删事件先于 subWindowList 更新送达；等事件循环
            # 走完再查列表，否则会按旧状态显示/隐藏（2026-11-28 实测）。
            QTimer.singleShot(0, self._sync_later)
        elif event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            sync_welcome(self.window)
        return False

    def _sync_later(self):
        try:
            sync_welcome(self.window)
        except RuntimeError:
            pass          # 窗口在定时器排队期间被销毁
