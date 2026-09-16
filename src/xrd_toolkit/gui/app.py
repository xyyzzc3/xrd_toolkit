"""GUI 主程序：主框架（可停靠面板式工作区）与事件循环（PySide6）。

入口：python -m xrd_toolkit.gui（__main__.py 转发到本文件的 main()）。
create_window() 与 main() 分离：测试里可以只建窗口、不进事件循环。

架构说明：
  - 外窗口（QMainWindow）管三块固定坞：文件（左）/ 参数（右）/
    日志（底）+ 顶部工具栏 + 状态行；
  - Qt 的中央区不接受停靠，所以中央再嵌一个内层 QMainWindow 专门
    管理图面板坞——嵌套是标准做法（PyCharm / Spyder 同款）；
  - 框架阶段的占位动作统一写日志区 + 状态行提示"尚未实现"；
    实现功能时只替换动作背后的处理，框架结构不变。

窗口上的公共接口（供后续页面接线与测试使用）：
  window.log(text)        写日志区 + 状态行
  window.add_files(paths) 把文件加进左侧列表
  window.plot_docks       {视图名: QDockWidget}
  window.params           参数面板控件字典
"""
import sys

from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGridLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QPlainTextEdit, QPushButton,
    QSpinBox, QToolBar, QVBoxLayout, QWidget, QDockWidget, QApplication)

FILE_FILTER = "衍射图像 (*.tif *.edf *.cbf);;所有文件 (*)"
VIEW_NAMES = ("2D", "剖面", "1D", "瀑布")   # 四个图面板（视图开关的顺序）


# ══ 日志 / 状态行 ═══════════════════════════════════════════
def _log(window: QMainWindow, text: str) -> None:
    """统一反馈出口：写日志区（累积）+ 状态行（常驻"就绪"，消息
    替换它 5 秒后自动恢复——状态栏永远有内容，不会隐形）。"""
    window.log_text.appendPlainText(text)
    window.status_text.setText(text)
    if window._status_timer is None:
        window._status_timer = QTimer(window)
        window._status_timer.setSingleShot(True)
        window._status_timer.timeout.connect(
            lambda: window.status_text.setText("就绪"))
    window._status_timer.start(5000)   # 重新计时


# ══ 左侧：文件列 ═══════════════════════════════════════════
class NarrowList(QListWidget):
    """最小宽度可收窄的列表。

    QListWidget 的 minimumSizeHint 内部写死约 270px（按"能显示
    条目"设计），且 dock 布局只认这个 hint、不认 setMinimumWidth。
    覆盖 hint 后文件列才能收到按钮行决定的真实最窄宽度。
    """

    def minimumSizeHint(self):
        return QSize(100, 120)


def _build_file_dock(window: QMainWindow) -> QDockWidget:
    """文件坞：打开（多选）/ 保存 + 勾选式文件列表。"""
    dock = QDockWidget("文件", window)
    dock.setObjectName("file_dock")
    dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)

    content = QWidget()
    lay = QVBoxLayout(content)
    # 按钮排 2 列网格：[打开] 占满第一行（长按钮），[保存][删除] 第二行。
    # 一行三个按钮的最小宽度 ≈ 3×80+间距，把文件列锁在 270；
    # 网格最宽的一行只有两个按钮 ≈ 184，文件列才能和参数列一样收窄
    btns = QGridLayout()
    btn_open = QPushButton("打开")
    btn_save = QPushButton("保存")
    btn_delete = QPushButton("删除")
    btns.addWidget(btn_open, 0, 0, 1, 2)   # 打开占满第一行
    btns.addWidget(btn_save, 1, 0)
    btns.addWidget(btn_delete, 1, 1)
    lay.addLayout(btns)

    window.file_list = NarrowList()   # 覆盖了 minimumSizeHint，可以收窄
    # 支持按住 Shift/Ctrl 选多行，配合"删除"批量移除
    window.file_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
    lay.addWidget(window.file_list)

    def open_dialog():
        paths, _ = QFileDialog.getOpenFileNames(
            window, "选择数据文件", "data", FILE_FILTER)
        if paths:
            window.add_files(paths)

    def delete_selected():
        items = window.file_list.selectedItems()
        if not items:
            _log(window, "未选中要删除的文件")
            return
        for it in items:
            window.file_list.takeItem(window.file_list.row(it))
        _log(window, f"已删除 {len(items)} 个文件")

    btn_open.clicked.connect(open_dialog)
    btn_save.clicked.connect(lambda: _log(window, "保存 — 尚未实现"))
    btn_delete.clicked.connect(delete_selected)

    dock.setWidget(content)
    window.addDockWidget(Qt.LeftDockWidgetArea, dock)
    return dock


def add_files(window: QMainWindow, paths) -> None:
    """把文件加进左侧列表（勾选式；单选看单张、多选批量）。"""
    from pathlib import Path
    for p in paths:
        p = Path(p)
        item = QListWidgetItem(p.name)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked)          # 默认勾上
        item.setData(Qt.UserRole, str(p))       # 全路径藏在 UserRole
        window.file_list.addItem(item)
    _log(window, f"已添加 {len(paths)} 个文件")


# ══ 右侧：参数面板 ═════════════════════════════════════════
def _build_param_dock(window: QMainWindow) -> QDockWidget:
    """参数坞：占位表单 + [应用]。以后随选中图切换成对应参数组。"""
    dock = QDockWidget("参数", window)
    dock.setObjectName("param_dock")
    dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)

    content = QWidget()
    form = QFormLayout(content)

    window.params = {}
    def add_float(name, lo, hi, value, decimals=2):
        box = QDoubleSpinBox()
        box.setRange(lo, hi)
        box.setValue(value)
        box.setDecimals(decimals)
        window.params[name] = box
        form.addRow(name, box)
        return box

    add_float("像素尺寸 (µm)", 0.0, 10000.0, 200.0, decimals=1)
    add_float("波长 (Å)", 0.0, 10.0, 0.1223, decimals=4)
    add_float("初始距离 (mm)", 0.0, 10000.0, 1600.0, decimals=1)
    add_float("2θ 下限 (°)", 0.0, 90.0, 1.0)
    add_float("2θ 上限 (°)", 0.0, 90.0, 8.0)

    npt = QSpinBox()
    npt.setRange(100, 100000)
    npt.setValue(3000)
    window.params["输出点数"] = npt
    form.addRow("输出点数", npt)

    btn_apply = QPushButton("应用")
    btn_apply.clicked.connect(lambda: _log(window, "应用参数 — 尚未实现"))
    form.addRow(btn_apply)

    dock.setWidget(content)
    window.addDockWidget(Qt.RightDockWidgetArea, dock)
    return dock


# ══ 底部：日志区（两层之一，状态行在 _build_status 里）════════
def _build_log_dock(window: QMainWindow) -> QDockWidget:
    """日志坞：累积输出（校准结果 / 积分统计都滚在这里），可收起。"""
    dock = QDockWidget("日志", window)
    dock.setObjectName("log_dock")
    window.log_text = QPlainTextEdit()
    window.log_text.setReadOnly(True)
    window.log_text.setMaximumBlockCount(5000)   # 只留最近 5000 行，防涨爆
    dock.setWidget(window.log_text)
    window.addDockWidget(Qt.BottomDockWidgetArea, dock)
    return dock


def _build_status(window: QMainWindow) -> None:
    """状态行（两层之二）：左侧常驻文字（"就绪"/瞬时消息）+
    右侧常驻当前文件。用常驻 QLabel 而不是 showMessage——
    后者超时清空后状态栏会变成看不见的细条。"""
    window.status_text = QLabel("就绪")
    window.statusBar().addWidget(window.status_text)
    window.file_label = QLabel("未打开文件")
    window.statusBar().addPermanentWidget(window.file_label)


# ══ 顶部：工具栏 ═══════════════════════════════════════════
def _build_toolbar(window: QMainWindow) -> None:
    """工具栏 = [校准] 模式开关 + 视图开关 + 日志开关。"""
    tb = QToolBar("主工具栏", window)
    tb.setMovable(False)
    window.addToolBar(tb)

    # 模式开关：[校准] 可勾选。按下 = 校准工作台（几何参数 +
    # 点图微调束心），弹起 = 默认的分析工作台。原来的 [图像] 按钮
    # 已删：outputs 摊成四个视图开关后，它只剩空壳
    btn_calib = QPushButton("校准")
    btn_calib.setCheckable(True)   # 默认弹起 = 分析工作台
    tb.addWidget(btn_calib)
    btn_calib.toggled.connect(lambda on: _on_mode(window, on))

    tb.addSeparator()

    # 视图开关：[2D][剖面][1D][瀑布]——勾选 = 对应图面板的开关
    window.view_buttons = {}   # 登记按钮，供面板可见性同步使用
    for name in VIEW_NAMES:
        btn = QPushButton(name)
        btn.setCheckable(True)
        tb.addWidget(btn)
        window.view_buttons[name] = btn
        btn.toggled.connect(lambda on, n=name: _toggle_plot(window, n, on))

    tb.addSeparator()

    # 日志开关：日志坞可被关闭，用这个勾选按钮重新打开
    btn_log = QPushButton("日志")
    btn_log.setCheckable(True)
    btn_log.setChecked(True)
    tb.addWidget(btn_log)
    window.log_dock.visibilityChanged.connect(btn_log.setChecked)
    btn_log.toggled.connect(window.log_dock.setVisible)


def _on_mode(window: QMainWindow, calibrating: bool) -> None:
    """模式开关：勾选 = 校准工作台，弹起 = 分析工作台（框架阶段只换占位提示）。"""
    if calibrating:
        window.mode_label.setText("校准模式 — 待实现")
        _log(window, "进入校准模式")
    else:
        window.mode_label.setText("分析模式 — 待实现")
        _log(window, "回到分析模式")


# ══ 中央：可停靠图面板区（内层 QMainWindow）═════════════════
def _build_center(window: QMainWindow) -> None:
    """内层 QMainWindow 托管图面板坞；外层窗口管文件/参数/日志坞。"""
    inner = QMainWindow(window)
    # PySide6 怪癖：QMainWindow(parent) 构造后仍带 Qt::Window 标志，
    # 不会被当成子部件嵌入（isWindow() 为真、永不随主窗口显示）。
    # 显式改成 Qt.Widget 后才是真正的嵌入式内层窗口。
    inner.setWindowFlags(Qt.Widget)
    inner.setObjectName("plot_area")
    inner.setDockOptions(QMainWindow.AnimatedDocks
                         | QMainWindow.AllowNestedDocks
                         | QMainWindow.AllowTabbedDocks)
    window.inner = inner
    window.plot_docks = {}

    window.mode_label = QLabel("分析模式 — 待实现")   # 默认：分析工作台
    window.mode_label.setAlignment(Qt.AlignCenter)
    inner.setCentralWidget(window.mode_label)
    window.setCentralWidget(inner)

    # 绘图区右下角：横排/竖排按钮（像 Excel 底部右角的视图按钮）。
    # 内层窗口自带状态栏，正贴绘图区下边缘；addPermanentWidget
    # 靠右排列，按钮组就落在右下角。
    inner.statusBar().setSizeGripEnabled(False)   # 去掉拖角手柄，更整洁
    arrange_box = QWidget()
    row = QHBoxLayout(arrange_box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(2)
    for name in ("横排", "竖排"):
        btn = QPushButton(name)
        btn.setFlat(True)      # 扁平样式，更像 Excel 角上的小按钮
        row.addWidget(btn)
        btn.clicked.connect(
            lambda checked=False, n=name: _arrange(window, n))
    inner.statusBar().addPermanentWidget(arrange_box)


def _toggle_plot(window: QMainWindow, name: str, on: bool) -> None:
    """视图开关：开 = 建面板（或显示），关 = 隐藏面板。

    面板是 QDockWidget：拖标题栏换位、拖出窗口浮动、× 关闭。
    用户用 × 关掉面板时 visibilityChanged 会把按钮勾掉，两者同步。
    """
    dock = window.plot_docks.get(name)
    if dock is None:
        dock = QDockWidget(name, window.inner)
        dock.setObjectName(f"plot_{name}")
        placeholder = QLabel(f"{name} — 面板占位")
        placeholder.setAlignment(Qt.AlignCenter)
        dock.setWidget(placeholder)
        window.plot_docks[name] = dock
        window.inner.addDockWidget(Qt.RightDockWidgetArea, dock)
        dock.show()   # 显式显示：不依赖停靠系统自动显示
        dock.visibilityChanged.connect(
            lambda v: _sync_toggle(window, name, v))
        _log(window, f"打开{name}面板")
    else:
        dock.setVisible(on)


def _sync_toggle(window: QMainWindow, name: str, visible: bool) -> None:
    """面板可见性与工具栏勾选按钮保持同步（用户点 × 关面板时）。"""
    window.view_buttons[name].setChecked(visible)


def _arrange(window: QMainWindow, mode: str) -> None:
    """一键重排所有已打开的面板：横排成一行 / 竖排成一列。

    用 splitDockWidget 依次把每个面板排到前一个旁边（横）或下面
    （竖），再用 resizeDocks 均分空间。每次点击都会重新确立布局，
    之前怎么摆的都归位。
    """
    docks = [d for d in window.plot_docks.values() if d.isVisible()]
    if not docks:
        _log(window, "没有打开的面板")
        return
    for d in docks:
        window.inner.addDockWidget(Qt.RightDockWidgetArea, d)
        d.show()
    orient = Qt.Vertical if mode == "竖排" else Qt.Horizontal
    for prev, d in zip(docks, docks[1:]):
        window.inner.splitDockWidget(prev, d, orient)
    size = (window.inner.height() if mode == "竖排"
            else window.inner.width()) // len(docks)
    window.inner.resizeDocks(docks, [max(size, 120)] * len(docks), orient)
    _log(window, f"已{mode} {len(docks)} 个面板")


# ══ 主窗口组装 ════════════════════════════════════════════
def create_window() -> QMainWindow:
    """组装主框架：文件坞 + 参数坞 + 日志坞 + 工具栏 + 中央面板区。"""
    window = QMainWindow()
    window.setWindowTitle("XRD Toolkit")
    window.resize(1200, 800)

    window._status_timer = None   # _log 里的状态栏恢复计时器（懒创建）
    window.log = lambda text: _log(window, text)
    window.add_files = lambda paths: add_files(window, paths)

    _build_center(window)
    file_dock = _build_file_dock(window)
    param_dock = _build_param_dock(window)
    window.log_dock = _build_log_dock(window)
    _build_status(window)
    _build_toolbar(window)

    # 初始尺寸：文件列/参数列默认收到最窄（各自内容的最小宽度），
    # 需要时用户自己拖宽；日志高 140（都可拖动）
    for d in (file_dock, param_dock):
        window.resizeDocks([d], [d.minimumSizeHint().width()], Qt.Horizontal)
    window.resizeDocks([window.log_dock], [140], Qt.Vertical)

    window.log("主框架已就绪 — 功能尚未实现")
    return window


def main() -> int:
    """程序入口：创建 QApplication → 建窗口 → 进入事件循环。

    事件循环（app.exec()）是 GUI 程序的"心跳"：程序停在这里，
    不断接收鼠标/键盘/重绘事件并分发出去，直到窗口被关闭。
    """
    app = QApplication(sys.argv)
    window = create_window()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
