"""面板容器：子窗口壳、标题栏过滤、浮动窗口、关闭/记忆流程。

2026-10-21 从 panels.py 原样搬出（只改结构，不动逻辑）。
panels 再导出，plot_panels 与 app 照旧经 gui_panels 取用。
"""
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QMainWindow, QMdiSubWindow, QWidget

from xrd_toolkit.gui.panel_state import (
    FOCUS_EMPTY_TEXT, _content, _log, _set_focus)


# 面板状态属性的白名单：弹出/收回时整体搬家的"行李清单"。
# 不能用 vars() 整体拷：PySide6 包装对象 vars() 里混着信号实例
# （windowStateChanged/destroyed…），整体拷会把新容器的信号盖成
# 旧容器的绑定信号（探针验证过）。
_PANEL_ATTRS = (
    "panel_file", "panel_item", "panel_display", "figure_saved",
    "params_snapshot", "compare_files", "compare_gen", "compare_pending",
    "compare_data", "_dragged", "_canvas_pref", "hover_marker",
    "_pan_start", "_pan_limits", "last_tth", "last_intensity",
    "_last_canvas", "_settling", "panel_key",
    # 面板 [Home] 的"家"视图与手势标记（弹出/收回要跟着搬，见
    # plot_panels._refresh_home）；对比堆叠还有自己一份"家"
    # （view_home_stack，取消堆叠后回平铺那份，2026-10-15）
    "view_home", "view_home_stack", "_view_from_gesture",
    # Customize 对话框保护记账（重画不覆盖用户改动，见 _draw_1d）
    "_title_ours", "_title_display", "_xlabel_ours", "_ylabel_ours",
    "_yscale_ours", "_yscale_param",
)

def _copy_panel_attrs(src, dst) -> None:
    """按白名单把面板状态从旧容器搬到新容器（弹出/收回用）。"""
    for name in _PANEL_ATTRS:
        if hasattr(src, name):
            setattr(dst, name, getattr(src, name))

# ══ 中央：图面板区（QMdiArea 子窗口，互不牵连，可弹出）══════
class _PlotSubWindow(QMdiSubWindow):
    """绘图子窗口：× 关闭 = 面板从 plot_docks 移除（关闭即遗忘）。

    Qt 默认行为是"关闭 = 隐藏"（还留在 subWindowList 里），与
    "关闭即遗忘"的规则不符 → 重写 closeEvent 走统一关闭入口
    _close_panel（从登记表移除 + 焦点移交 + 销毁容器）。

    无原生标题栏（frameless）：壳改成内容自己的一行自绘标题栏
    （plot_panels._build_slim_bar，实测省 57 px/面板）。原生标题栏
    被拿掉的三件事在这里补回来：拖动 = _PanelBarFilter 挂在自绘
    栏上、双击最大化 = 同一个过滤器、× 关闭 = 自绘栏的关闭按钮
    （调用与原生 × 同一个 _close_panel）。弹出的独立窗口不套这个
    壳——它要系统的窗口管理（见 _FloatedWindow）。

    frameless 由 _apply_panel_chrome 按内容决定（不是构造函数里
    一刀切）：占位面板（未注册视图）没有自绘栏，得留着原生标题栏，
    否则既拖不动也关不掉。
    """

    def __init__(self, window: QMainWindow, key: str):
        super().__init__()
        self._window = window
        self.panel_key = key
        # 原生壳的 flags（占位面板要还原成这个，见 _apply_panel_chrome）
        self._native_flags = self.windowFlags()
        # 标题变了（改名/显示名变化）自绘栏的悬停提示要跟着变。用
        # windowTitleChanged 信号而不是 WindowTitleChange 事件：
        # 后者只发给**顶层窗口**（QWidget::setWindowTitle 里
        # topextra 为空就不发），MDI 子窗口是子部件，收不到（踩过：
        # 标题栏文案一直是空字符串）
        self.windowTitleChanged.connect(
            lambda title: _sync_bar_tooltip(self, title))
        # 点窗口任何地方都选中该面板：由 _PanelClickTracker（应用级
        # 过滤器，见 create_window）统一处理——QWidget 的父过滤器
        # 收不到子部件事件，容器级过滤器盖不住内容区

    def closeEvent(self, event):
        _close_panel(self._window, self.panel_key)
        super().closeEvent(event)

class _PanelBarFilter(QObject):
    """自绘标题栏上的"窗口管理器"：按住拖 = 移动面板、双击 = 最大化。

    原生标题栏被拿掉后，这两件事本来由系统做。事件过滤器装在自绘
    栏本体上（不装按钮）：按在按钮上的事件不会到过滤器，点按钮
    照旧触发动作——只有标题文字/空白处才是"窗口把儿"。

    容器现查（window.plot_docks[key]），不捕获对象：弹出/收回会换
    容器（子窗口 ↔ 独立窗口），捕获旧对象就拖不动了。拖动按"全局
    坐标位移差"算，滚动条/缩放都不影响。
    """

    def __init__(self, window: QMainWindow, key: str, bar):
        super().__init__(bar)      # 父 = 自绘栏：防 Python GC 静默失效
        self._window = window
        self._key = key
        self._drag = None          # (按下时的全局坐标, 容器当时的位置)

    def eventFilter(self, obj, event):
        etype = event.type()
        if etype == QEvent.MouseButtonDblClick:
            self._drag = None   # 双击前那一下 press 记的拖动作废
            dock = self._window.plot_docks.get(self._key)
            if isinstance(dock, QMdiSubWindow):
                # 双击 = 占满绘图区 / 还原（子窗口才有这个语义；弹出
                # 窗口归系统管）
                if dock.isMaximized():
                    dock.showNormal()
                else:
                    dock.showMaximized()
            return True
        if etype == QEvent.MouseButtonPress:
            dock = self._window.plot_docks.get(self._key)
            if dock is not None and event.button() == Qt.LeftButton:
                self._drag = (event.globalPosition().toPoint(), dock.pos())
            return False
        if etype == QEvent.MouseMove and self._drag is not None:
            dock = self._window.plot_docks.get(self._key)
            if dock is None:
                self._drag = None
                return False
            start, origin = self._drag
            delta = event.globalPosition().toPoint() - start
            dock.move(origin + delta)
            return True
        if etype == QEvent.MouseButtonRelease:
            self._drag = None
        return super().eventFilter(obj, event)

class _FloatedWindow(QWidget):
    """弹出的独立窗口：面板内容整个搬进来，× 关闭同样"关闭即遗忘"。

    无父 = 顶层 OS 窗口，不随主窗口移动/关闭。收回主窗口时不走
    close()（那会触发 _close_panel 把面板登记抹掉），由调用方直接
    deleteLater() 销毁壳。
    """

    def __init__(self, window: QMainWindow, key: str):
        super().__init__()   # 无父 = 顶层独立窗口
        self._window = window
        self.panel_key = key
        self.content = None   # 面板内容（_content(dock) 从这里取）
        self.setAttribute(Qt.WA_DeleteOnClose)
        # 弹出状态下的改名也要同步自绘栏（同 _PlotSubWindow）
        self.windowTitleChanged.connect(
            lambda title: _sync_bar_tooltip(self, title))
        # 点窗口任何地方都选中该面板：同 _PlotSubWindow，
        # 由 _PanelClickTracker 统一处理

    def closeEvent(self, event):
        _close_panel(self._window, self.panel_key)
        super().closeEvent(event)

def _sync_bar_tooltip(dock, title: str) -> None:
    """自绘标题栏的悬停提示跟着容器标题走（开局、改名、换显示名都算）。

    2026-09-24 用户："名字在三处太多" → 标题栏不再显示名字（只留
    按钮），改成悬停提示：鼠标放在这行的空白处就知道是哪块面板，
    屏幕上则靠图上的标题辨认。容器可能是子窗口或弹出窗口，两边都
    连到本函数；内容没建好（占位面板）时静默跳过。
    """
    content = _content(dock) if isinstance(dock, QMdiSubWindow) \
        else getattr(dock, "content", None)
    bar = getattr(content, "slim_bar", None)
    if bar is not None and bar.toolTip() != title:
        bar.setToolTip(title)

def _apply_panel_chrome(dock, content) -> None:
    """按内容决定容器的壳：有自绘标题栏 → frameless 子窗口。

    画布面板的内容自带一行自绘标题栏（plot_panels._build_slim_bar，
    标题 + 四个按钮 + [弹出][关闭]）→ 原生标题栏就不要了，省下
    36 px；占位面板（未注册视图）没有那一行 → 保留原生标题栏（否则
    没法拖动、也没关闭入口）。弹出窗口不在这里管：它要系统的窗口
    管理（见 _FloatedWindow 说明）。

    注意 setWindowFlags 会把窗口藏起来，调用方随后必须 show()。
    """
    if not isinstance(dock, QMdiSubWindow):
        return
    flags = Qt.FramelessWindowHint if hasattr(content, "slim_bar") \
        else getattr(dock, "_native_flags", Qt.Window)
    dock.setWindowFlags(flags)

def _close_all_panels(window: QMainWindow) -> None:
    """[全关]：把所有图面板一次关掉（弹出去的独立窗口也一样）。

    用户 2026-09-26："加一个一键关闭所有图像，就是打开的子窗口全部关闭"。
    与单个 × 同一条 _close_panel 路径（关闭即遗忘、焦点逐个移交）；不弹
    确认框——单个 × 也不弹（只有关窗才问存不存盘），批量关不该比单个更
    啰嗦，但日志里报一句有几张没存过盘，免得关完才想起来。
    """
    keys = list(window.plot_docks)
    unsaved = [k for k, d in window.plot_docks.items()
               if getattr(_content(d), "figure", None) is not None
               and not getattr(d, "figure_saved", False)]
    for key in keys:
        _close_panel(window, key, quiet=True)
    if keys:
        tail = f"（其中 {len(unsaved)} 张没存过盘）" if unsaved else ""
        _log(window, f"已关闭全部 {len(keys)} 张图{tail}")

def _close_panel(window: QMainWindow, key: str, quiet: bool = False) -> None:
    """统一关闭面板：从登记表移除（关闭即遗忘）+ 焦点移交 + 销毁容器。

    quiet=True 不写"已关闭面板"那行（[全关] 一次关几十张时不刷屏，
    汇总行由调用方写）。

    幂等：面板已不在登记表里（如收回主窗口后旧壳被删除）直接返回。
    子窗口先从 MDI 摘下再 deleteLater（探针验证 closeEvent 里这组
    操作安全）；弹出窗口靠 WA_DeleteOnClose 自行销毁。焦点面板被
    关 → 编辑对象移给下一张还开着的图；一张不剩则清空。
    """
    dock = window.plot_docks.pop(key, None)
    if dock is None:
        return
    # 关掉一个面板 → 文件栏里那一条的"正在打开"记号要收回去（见
    # file_dock.refresh_open_marks；经窗级回调，不反向 import 文件坞）
    mark = getattr(window, "mark_open_rows", None)
    if mark is not None:
        mark()
    # 覆盖光标可能还挂在抓手过滤器名下（关面板时鼠标可能正停在
    # 抓取区上，来不及收 Leave）：主动撤销，别把方向光标留下
    grip_filter = getattr(_content(dock), "_grip_filter", None)
    if grip_filter is not None:
        grip_filter._release_cursor()
    if isinstance(dock, QMdiSubWindow):
        if dock in window.mdi.subWindowList():
            window.mdi.removeSubWindow(dock)
        dock.deleteLater()
    if not quiet:
        _log(window, f"已关闭面板：{dock.windowTitle()}")
    if window.focus_panel == key:
        window.focus_panel = None   # 先清空，绕过 _set_focus 的同键早退
        for next_key, next_dock in window.plot_docks.items():
            _set_focus(window, next_key, next_dock.windowTitle())
            break
        if window.focus_panel is None:
            window.focus_label.setText(FOCUS_EMPTY_TEXT)

