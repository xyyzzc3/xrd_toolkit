"""GUI 轻量主题（2026-11-27）：统一浅色外观、字号和控件层级。

目标不是换肤，而是把默认 Qt / 系统外观收拢成一套：
  * Fusion + 浅色 QPalette：Windows 深色工具栏、macOS 原生深色模式
    都不会再把局部控件染深；
  * 一处定义的主色/灰阶/圆角/间距，供工具栏、按钮层级和面板使用；
  * 少量全局 QSS 只覆盖工具栏、主按钮、表头、标签页和 MDI 区，
    其余控件保持 Fusion 原生样式，避免样式表把对话框/输入框弄坏。

深色模式是另一个工程（所有图、表、对话框都要适配），这里明确不做：
系统是深色时 GUI 仍保持浅色。2026-11-27 与用户讨论后确定。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QMdiSubWindow, QPushButton

# ── 设计令牌（与官网/说明书同一套冷色调）────────────────────
BG = "#f4f6f9"            # 窗口底色
SURFACE = "#ffffff"       # 内容/输入底色
SURFACE_ALT = "#eef2f7"   # 表头、交替行、MDI 工作区
BORDER = "#dfe5ec"        # 分隔线
TEXT = "#1c2733"
MUTED = "#5b6773"
ACCENT = "#1d5496"        # 品牌蓝（主按钮）
ACCENT_HOVER = "#17447c"
ACCENT_SOFT = "#dce8f7"   # 工具栏当前页
SELECT = "#cfe0f5"        # 选中底（表格/菜单/MDI 标题栏）
SELECT_TEXT = "#12365f"
DANGER = "#a93226"
DANGER_SOFT = "#fdf0ee"
DISABLED = "#9aa4ae"
MDI_BG = "#eef2f7"        # 图面板工作区（比内容暗一档）

# 字号：只在系统默认偏小时抬一档，避免把 macOS/Windows 的布局全改掉
_MIN_POINT_SIZE = 9.5


def build_palette() -> QPalette:
    """浅色 QPalette；Fusion 下 MDI 标题栏/选中色都从这里取。"""
    palette = QPalette()
    role = QPalette.ColorRole
    group = QPalette.ColorGroup

    palette.setColor(role.Window, QColor(BG))
    palette.setColor(role.WindowText, QColor(TEXT))
    palette.setColor(role.Base, QColor(SURFACE))
    palette.setColor(role.AlternateBase, QColor(SURFACE_ALT))
    palette.setColor(role.Text, QColor(TEXT))
    palette.setColor(role.Button, QColor("#f7f9fc"))
    palette.setColor(role.ButtonText, QColor(TEXT))
    palette.setColor(role.BrightText, QColor("#ffffff"))
    palette.setColor(role.Highlight, QColor(SELECT))
    palette.setColor(role.HighlightedText, QColor(SELECT_TEXT))
    palette.setColor(role.ToolTipBase, QColor(SURFACE))
    palette.setColor(role.ToolTipText, QColor(TEXT))
    palette.setColor(role.PlaceholderText, QColor(MUTED))
    palette.setColor(role.Link, QColor(ACCENT))
    # MDI 标题栏在非活动状态会取 Inactive/Disabled 组；不显式写的话
    # 浅色标题栏上会出现浅灰字（2026-11-28 截图确认）。
    for state in (group.Active, group.Inactive):
        for key in (role.Text, role.WindowText, role.ButtonText):
            palette.setColor(state, key, QColor(TEXT))
    palette.setColor(group.Disabled, role.WindowText, QColor(MUTED))
    palette.setColor(group.Disabled, role.Text, QColor(MUTED))
    palette.setColor(group.Disabled, role.ButtonText, QColor(MUTED))
    return palette


_STYLE_SHEET = f"""
QToolBar {{
    background: {BG};
    border: 0;
    border-bottom: 1px solid {BORDER};
    padding: 5px 8px;
    spacing: 4px;
}}
QToolBar QPushButton {{
    padding: 5px 11px;
    border-radius: 6px;
    color: {TEXT};
}}
QToolBar QPushButton:hover {{ background: #e6edf6; }}
QToolBar QPushButton:checked {{
    background: {ACCENT_SOFT};
    color: {ACCENT_HOVER};
    font-weight: bold;
}}
QToolBar QPushButton:pressed {{ background: #cfdff2; }}
QToolBar::separator {{
    background: {BORDER};
    width: 1px;
    margin: 5px 6px;
}}

QPushButton[role="primary"] {{
    background: {ACCENT};
    color: white;
    border: 1px solid {ACCENT};
    border-radius: 6px;
    padding: 6px 14px;
    font-weight: bold;
}}
QPushButton[role="primary"]:hover {{
    background: {ACCENT_HOVER};
    border-color: {ACCENT_HOVER};
}}
QPushButton[role="primary"]:pressed {{ background: #12365f; }}
QPushButton[role="primary"]:disabled {{
    background: #b8c6d6;
    border-color: #b8c6d6;
    color: #f2f5f8;
}}
QPushButton[role="danger"] {{
    color: {DANGER};
    background: {SURFACE};
    border: 1px solid #e3b7b1;
    border-radius: 6px;
    padding: 5px 12px;
}}
QPushButton[role="danger"]:hover {{
    background: {DANGER_SOFT};
    border-color: {DANGER};
}}

QHeaderView::section {{
    background: {SURFACE_ALT};
    border: 0;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    padding: 4px 6px;
    font-weight: bold;
}}
QTableCornerButton::section {{ background: {SURFACE_ALT}; border: 0; }}

QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    padding: 6px 12px;
    margin-right: 2px;
    border: 1px solid transparent;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}}
QTabBar::tab:hover {{ background: #e6edf6; }}
QTabBar::tab:selected {{
    background: {SURFACE};
    border-color: {BORDER};
    color: {ACCENT_HOVER};
    font-weight: bold;
}}
QMdiArea {{ background: {MDI_BG}; }}
"""


def apply_app_theme(app: QApplication | None = None) -> QApplication | None:
    """在 QApplication 建好、窗口创建之前调用一次。"""
    app = app or QApplication.instance()
    if app is None:
        return None
    app.setStyle("Fusion")
    app.setPalette(build_palette())
    font = QFont(app.font())
    if font.pointSizeF() < _MIN_POINT_SIZE:
        font.setPointSizeF(_MIN_POINT_SIZE)
    app.setFont(font)
    app.setStyleSheet(_STYLE_SHEET)
    from xrd_toolkit.simulation.plot_style import apply_plot_style
    apply_plot_style()
    return app


def set_role(button: QPushButton, role: str) -> None:
    """按钮层级：primary（主操作）/ danger（删除类）。

    用动态属性而不是 objectName，避免影响现有测试/样式钩子。
    """
    button.setProperty("role", str(role))
    style = button.style()
    if style is not None:
        style.unpolish(button)
        style.polish(button)


def brand_subwindow(sub: QMdiSubWindow) -> None:
    """MDI 子窗口标题栏保持纯文字（不画小图标）。

    2026-11-28 用户反馈：标题栏左侧的小图标会被认成 Qt 标志。空
    QIcon 会被 Fusion 回退成默认 Qt 图标，所以给一个 1×1 全透明
    pixmap；应用图标仍用于任务栏/主窗口。
    """
    blank = QPixmap(1, 1)
    blank.fill(Qt.GlobalColor.transparent)
    sub.setWindowIcon(QIcon(blank))
