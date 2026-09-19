"""GUI 主程序：窗口组装（文件坞 / 参数坞 / 日志坞 / 工具栏 / 保存关窗流程）。

入口：python -m xrd_toolkit.gui（__main__.py 转发到 main()）。
create_window() 与 main() 分离：测试里可以只建窗口、不进事件循环。

模块图（2026-09-18 从 3622 行单文件拆分，调用方向永远从上往下，
循环导入无路可走）：
    app.py（本模块：窗口组装与保存/关窗流程）
      → plot_views.py   视图注册表 + 出图调度 + 1D/对比绘图 +
                         悬停取点 + 手势（扩展点：2D/剖面/瀑布接线
                         = 往 _VIEW_BUILDERS/_VIEW_RUNNERS 加条目）
      → calib.py        校准工作台：参数坞第 2 页表单 + 中央校准图
                         面板 + 自动/手动校准后台任务
      → panels.py        面板容器生命周期：MDI 子窗口/弹出窗口、
                         关闭即遗忘、抓手、平铺、总缩放
      → customize.py     Customize 自绘轴属性对话框
      → panel_state.py   共享层：_log/_content/参数快照/焦点回放/
                         几何收集/自动显示区间
      → tasks.py         后台任务运行器（耗时计算挪出界面线程）

窗口上的公共接口（供后续页面接线与测试使用）：
  window.log(text)        写日志区 + 状态行
  window.add_files(paths) 把文件加进左侧列表
  window.mdi              QMdiArea（绘图区，所有图子窗口的父场地）
  window.plot_docks       {面板键: QMdiSubWindow 或 _FloatedWindow}，
                          键 = f"{视图}|{路径}"，重复文件改名条目再补
                          |显示名 区分；已关闭的面板不在登记表里
  window.focus_panel      参数面板编辑对象 = 面板键（点窗口任意处/
                          计算完成时设定）
  window.params           参数面板控件字典
  window.config_name      当前选中的配置条目 key（如 lmfp1_lab6）
  window.config           完整条目 dict（label / geometry / beam_center）
  1D 面板的画布/坐标轴在面板内容上：_content(dock).axes_1d

兼容再导出：拆分前全部函数都住在 app 模块里，测试等外部代码继续
经 gui_app 访问它们（下方 import 即再导出）。mock.patch 的目标请
指到实现所在的新模块（tests 里已改为 gui_views / gui_customize）。
"""
import sys
from pathlib import Path

import matplotlib

matplotlib.use("qtagg")   # 必须在导入 FigureCanvasQTAgg 之前选定 Qt 后端
# 图标题取自文件显示名（重复文件改名可输入中文）→ 字体回退链补上
# macOS 中文字体，缺字形时逐字体回退，标题不会渲染成方框。
# 注意要设 font.family 直接给列表：实测 qtagg 后端下 font.sans-serif
# 列表不触发回退（Agg 可以），中文仍会变方框
matplotlib.rcParams["font.family"] = [
    "DejaVu Sans", "PingFang SC", "Hiragino Sans GB", "Arial Unicode MS"]
from PySide6.QtCore import QEvent, QObject, Qt, QSize
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QInputDialog, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QMdiSubWindow,  # 兼容再导出：测试 isinstance 用
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QSplitter, QStackedWidget, QToolBar, QVBoxLayout, QWidget,
    QDockWidget, QApplication)

from xrd_toolkit.config import CONFIGS, DEFAULT_CONFIG
# ── 兼容再导出（见模块 docstring）：测试与后续接线继续经本模块访问 ──
from xrd_toolkit.gui.calib import (
    _build_calib_form, _CalibSubWindow, _close_calib_panel,
    _draw_calib_image, _enter_calib, _exit_calib,
    _on_calib_click, _open_calib_panel,
    _start_auto_calib, _start_manual_calib, _undo_calib_point,
    _clear_calib_points)
from xrd_toolkit.gui.customize import (
    _apply_customize, _build_customize_dialog, _open_customize_dialog)
from xrd_toolkit.gui.panels import (
    _apply_area_zoom, _build_center, _close_panel, _FloatedWindow,
    _PlotSubWindow, _toggle_pop_out)
from xrd_toolkit.gui.panel_state import (
    _apply_auto_contrast, _apply_auto_ylim, _apply_config,
    _collect_geometry, _content, _log, _set_focus)
from xrd_toolkit.gui.plot_views import (
    _apply_image_params, _apply_params, _compute_integration, _draw_1d,
    _hover_leave, _hover_motion, _magnifier_on, _open_plot_panel,
    _pan_motion, _pan_press, _pan_release, _plot_compare, _plot_view,
    _wheel_zoom)

FILE_FILTER = "衍射图像 (*.tif *.edf *.cbf);;所有文件 (*)"
VIEW_NAMES = ("2D", "剖面", "1D", "瀑布")   # 四个图面板（作图按钮的顺序）
SUPPORTED_SUFFIXES = (".tif", ".edf", ".cbf")   # 拖放只认这三种

# ══ 日志 / 状态行 ═══════════════════════════════════════════
def _dropped_paths(event) -> list:
    """从拖放事件提取本地文件路径（只留支持的类型 tif/edf/cbf）。"""
    paths = []
    for url in event.mimeData().urls():
        if url.isLocalFile():
            p = url.toLocalFile()
            if p.lower().endswith(SUPPORTED_SUFFIXES):
                paths.append(p)
    return paths

class _MainWindow(QMainWindow):
    """顶层窗口：接受拖入文件（拖到窗口任意位置 = 加进文件列表）。

    至少拖进一个支持类型的文件才"接住"（光标变 +）；子控件默认
    不接拖放（日志区显式关掉了文本拖放），事件会冒泡到顶层窗口
    ——一个入口覆盖整个窗口面。drop_callback 由 create_window 接
    到 add_files 上。
    """

    def __init__(self):
        super().__init__()
        self.drop_callback = None
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if self.drop_callback and _dropped_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = _dropped_paths(event)
        if paths and self.drop_callback:
            self.drop_callback(paths)
            event.acceptProposedAction()
        else:
            event.ignore()


class _PanelClickTracker(QObject):
    """应用级事件过滤器：点任何面板窗口内任何位置都选中该面板。

    与用户讨论定稿：选中子窗口 = 选中参数，不必非点图本体（标题
    栏、边框、图、工具栏都算）。为什么必须是应用级：探针实证
    QWidget 的父过滤器收不到子部件事件（子部件 accept 后事件不
    向上传播，matplotlib 画布恰恰会 accept 鼠标按下）——只有挂
    在 QApplication 上的过滤器能看到一切。收到鼠标按下后从落点
    逐级向上找面板容器（_PlotSubWindow / _FloatedWindow），且该
    容器必须登记在本窗口 plot_docks 里（测试会同时开多个窗口，
    各窗口的过滤器只认自己登记的面板，防止串窗）。只"监听"不
    "拦截"：返回 False，事件照常传递。
    """

    def __init__(self, window: QMainWindow):
        super().__init__(window)   # 挂在窗口上，随窗口销毁
        # 刻意不存 window 引用：QApplication 的过滤器表 → 过滤器 →
        # window 会形成引用环，关掉的窗口永不回收（探针实测全套件
        # 30 个窗口全存活）。窗口就是父对象，用时 self.parent() 取。

    def eventFilter(self, obj, event):
        if event.type() != QEvent.MouseButtonPress:
            return False
        window = self.parent()
        if not isinstance(window, QMainWindow):
            return False   # 窗口已销毁：过滤器随父摘除前的兜底
        w = obj if isinstance(obj, QWidget) else None
        while w is not None:
            if isinstance(w, (_PlotSubWindow, _FloatedWindow)):
                key = w.panel_key
                if window.plot_docks.get(key) is w:
                    _set_focus(window, key, w.windowTitle())
                break
            w = w.parentWidget()
        return False   # 不消费事件

class _ElideLabel(QLabel):
    """宽度受限的单行标签：文字超宽时打省略号，绝不撑宽父布局。

    用在参数坞的长名称行（编辑对象标题 / 几何配置说明）：窄坞下
    缩略显示，悬停有完整提示；把坞拖宽自动多显示几个字。text()
    仍返回完整文字（缩略只是显示层面，程序与测试读 text() 不受
    影响）。横向尺寸策略设 Ignored = "布局给多宽就显示多宽，
    不按文字宽度反过来要地方"——正是它治住"名字太长撑大参数区"。
    """

    def __init__(self, text: str = "", mode=Qt.ElideMiddle, parent=None):
        super().__init__(text, parent)
        self._full = text
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setToolTip(text)

    def setText(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._refresh()

    def text(self) -> str:
        return self._full

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        if self.width() <= 0:
            super().setText(self._full)   # 布局还没定宽：先显示全量
            return
        super().setText(self.fontMetrics().elidedText(
            self._full, self._mode, self.width()))

class _PressRecorder(QObject):
    """记录鼠标按下时命中的文件项与对号状态（装在列表视口上）。

    用途：区分"点对号方块"（Qt 在弹起时自动切换对号，itemChanged
    先到）与"点行其他位置"（对号不动）——itemClicked 据此决定手势：
    点行 = 只勾不取消（加选），方块 = 勾上/取消。
    """

    def __init__(self, window: QMainWindow, lst: QListWidget):
        super().__init__(window)   # 挂在窗口上，随窗口销毁
        self._list = lst
        window._press_item = None
        window._press_state = None

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonPress:
            item = self._list.itemAt(event.position().toPoint())
            window = self.parent()   # 不存 window 引用（同 _PanelClickTracker：防引用环）
            window._press_item = item
            window._press_state = item.checkState() if item else None
        return False   # 不消费事件

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
    """文件坞：打开（多选）/ 保存 / 删除 + 以对号为选择的文件列表。

    选择 = 对号，两种手势：
      - 点行 = 加选：勾上这一行，其他对号不动；已勾的行再点没
        反应（不取消）；
      - 点对号方块 = 勾上/取消这一行（取消对号的唯一途径）。
    背景高亮跟随最后点的那行（且必须是对号行，没对号就不高亮），
    作图按钮对所有对号文件各开一张图，删除删所有对号文件。
    itemChanged 统一刷新标签和日志；itemClicked 靠 _PressRecorder
    记着的按下状态区分方块/行体手势。
    """
    dock = QDockWidget("文件", window)
    dock.setObjectName("file_dock")
    # 可移动/可浮动/可关闭：收起文件列给图让地方（工具栏 [文件] 开关
    # 或标题栏 × 都能收，visibilityChanged 双向同步按钮状态）
    dock.setFeatures(QDockWidget.DockWidgetMovable
                     | QDockWidget.DockWidgetFloatable
                     | QDockWidget.DockWidgetClosable)

    content = QWidget()
    lay = QVBoxLayout(content)
    # 按钮排 2 列网格：[打开] 占满第一行（长按钮），[保存][删除] 第二行。
    # 一行三个按钮的最小宽度 ≈ 3×80+间距，把文件列锁在 270；
    # 网格最宽的一行只有两个按钮 ≈ 184，文件列才能和参数列一样收窄
    btns = QGridLayout()
    btn_open = QPushButton("打开")
    btn_save = QPushButton("保存")
    btn_delete = QPushButton("删除")
    btn_save.setObjectName("save_btn")
    btn_delete.setObjectName("delete_btn")
    btns.addWidget(btn_open, 0, 0, 1, 2)   # 打开占满第一行
    btns.addWidget(btn_save, 1, 0)
    btns.addWidget(btn_delete, 1, 1)
    lay.addLayout(btns)

    window.file_list = NarrowList()   # 覆盖了 minimumSizeHint，可以收窄
    # 对号 = 选中（可多选）。用系统默认的选中样式：背景高亮 + 白字
    # （可读）。之前压掉背景导致"白字配白底"看不见字——背景高亮
    # 必须保留，且始终跟随对号集合（对号是唯一的选择表达）
    window.file_list.setSelectionMode(QAbstractItemView.SingleSelection)
    lay.addWidget(window.file_list)

    def on_item_changed(item):
        """对号状态变了 → 状态行标签 + 背景高亮同步；勾上时记日志。"""
        if item.checkState() == Qt.Checked:
            _log(window, f"已选中 {item.text()}（点击视图按钮开始计算）")
        _sync_current_to_checks(window)
        _refresh_file_label(window)

    def on_item_clicked(item):
        """手势区分：
          - 点对号方块：Qt 已自动切换（勾上/取消），无需再动；
          - 点行：只勾上这一行（加选），其他对号不动；已勾的行再
            点没反应。取消对号只能用对号方块。
        """
        square = (window._press_item is item
                  and item.checkState() != window._press_state)
        if square:
            return   # Qt 已切换，itemChanged 已同步
        if item.checkState() == Qt.Unchecked:
            item.setCheckState(Qt.Checked)   # → itemChanged 同步高亮/标签/日志

    window.file_list.itemChanged.connect(on_item_changed)
    window.file_list.itemClicked.connect(on_item_clicked)
    # 记录鼠标按下时命中的条目与对号状态（区分方块点击/行体点击）
    window.file_list.viewport().installEventFilter(
        _PressRecorder(window, window.file_list))

    def open_dialog():
        paths, _ = QFileDialog.getOpenFileNames(
            window, "选择数据文件", "data", FILE_FILTER)
        if paths:
            window.add_files(paths)

    def delete_selected():
        checked = [window.file_list.item(i)
                   for i in range(window.file_list.count())
                   if window.file_list.item(i).checkState() == Qt.Checked]
        if not checked:
            _log(window, "没有选中要删除的文件")
            return
        # 屏蔽信号：移除过程中 Qt 会把当前项挪到相邻行，别让
        # 中间状态触发登记/日志
        window.file_list.blockSignals(True)
        for it in checked:
            window.file_list.takeItem(window.file_list.row(it))
        window.file_list.blockSignals(False)
        _sync_current_to_checks(window)   # 高亮跟随剩余对号集合
        _refresh_file_label(window)   # 状态行跟随剩余对号集合
        # 条目没了，面板绑定的列表条目随之失效：解除引用（面板照常
        # 工作，靠 panel_file 记住自己的文件；重新加回时由 _plot_view
        # 把面板归位到新条目）。对比面板没有 panel_item，跳过。
        for dock in window.plot_docks.values():
            if getattr(dock, "panel_item", None) in checked:
                dock.panel_item = None
        _log(window, f"已删除 {len(checked)} 个文件")

    btn_open.clicked.connect(open_dialog)
    btn_save.clicked.connect(lambda: _save_figures(window))
    btn_delete.clicked.connect(delete_selected)

    dock.setWidget(content)
    window.addDockWidget(Qt.LeftDockWidgetArea, dock)
    return dock

def _unique_display_name(window: QMainWindow, name: str) -> str:
    """给重复文件起不重名的显示名："xxx.tif" → "xxx (1).tif" → "xxx (2).tif"…"""
    taken = {window.file_list.item(i).text()
             for i in range(window.file_list.count())}
    p = Path(name)
    n = 1
    while f"{p.stem} ({n}){p.suffix}" in taken:
        n += 1
    return f"{p.stem} ({n}){p.suffix}"

def _ask_duplicate(window: QMainWindow, name: str) -> str:
    """同一文件再次加入时的询问弹窗。返回 "overwrite" / "rename" / "cancel"。

    窗口没显示（测试环境）时不弹模态框，默认 "overwrite"（保留原
    条目）——与 _confirm_close 的可见性护栏同理，防测试挂死。
    """
    if not window.isVisible():
        return "overwrite"
    box = QMessageBox(window)
    box.setWindowTitle("文件已存在")
    box.setText(f"{name} 已经在文件列表里了。")
    box.setInformativeText(
        "覆盖 = 保留原条目；改名 = 弹输入框起个新名字（预填编号名，"
        "可自己改）；取消 = 这次不加。")
    btn_overwrite = box.addButton("覆盖", QMessageBox.AcceptRole)
    btn_rename = box.addButton("改名", QMessageBox.ActionRole)
    btn_cancel = box.addButton("取消", QMessageBox.RejectRole)
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

def add_files(window: QMainWindow, paths) -> None:
    """把文件加进左侧列表；一批新加的文件全部打对号（选中）。

    对号是唯一的选择表达：一起选入/拖入的文件默认全部勾上，点一次
    作图按钮就批量出图。重复文件（同一路径再次加入）弹窗询问：
      - 覆盖 = 保留原条目（新条目跳过，原条目勾上）；
      - 改名 = 弹输入框起新名字（预填 "xxx (1).tif"，可自己改；
        输入的名字已被占用会要求换一个）再开一条（同文件两条条目）；
      - 取消 = 这次不加。
    列表里永远不出现重名。
    """
    existing = {}   # 绝对路径 → 已有条目（重复检测按真实路径）
    for i in range(window.file_list.count()):
        item = window.file_list.item(i)
        existing[Path(item.data(Qt.UserRole)).resolve()] = item

    added = []   # 本批真正新加的条目
    window.file_list.blockSignals(True)   # 批量加：结束后统一同步/记日志
    for p in paths:
        p = Path(p)
        old = existing.get(p.resolve())
        if old is not None:   # 重复文件
            choice = _ask_duplicate(window, p.name)
            if choice == "overwrite":
                old.setCheckState(Qt.Checked)   # 保留原条目并勾上
                _log(window, f"{p.name} 已在列表中（覆盖：保留原条目）")
            elif choice == "rename":
                new_name = None
                default_name = _unique_display_name(window, p.name)
                while True:
                    answer = _ask_rename(window, default_name)
                    if answer is None:
                        break   # 取消 → 这次不加
                    if any(window.file_list.item(i).text() == answer
                           for i in range(window.file_list.count())):
                        _log(window, f"显示名 {answer} 已被占用，请换一个")
                        default_name = answer   # 重开输入框保留用户输入
                        continue
                    new_name = answer
                    break
                if new_name is None:
                    _log(window, f"已跳过重复文件 {p.name}")
                    continue
                item = QListWidgetItem(new_name)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setData(Qt.UserRole, str(p))
                window.file_list.addItem(item)
                item.setCheckState(Qt.Checked)
                added.append(item)
                existing[p.resolve()] = item   # 同批再出现同路径时走本条目
                _log(window, f"{p.name} 已在列表中（改名加入：{new_name}）")
            else:
                _log(window, f"已跳过重复文件 {p.name}")
            continue
        item = QListWidgetItem(p.name)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setData(Qt.UserRole, str(p))       # 全路径藏在 UserRole
        window.file_list.addItem(item)
        item.setCheckState(Qt.Checked)          # 新文件默认选中
        existing[p.resolve()] = item
        added.append(item)
    window.file_list.blockSignals(False)
    if added:
        window.file_list.setCurrentItem(added[-1])   # 高亮最后新条目
        _log(window, f"已添加 {len(added)} 个文件")
    _sync_current_to_checks(window)
    _refresh_file_label(window)

def _sync_current_to_checks(window: QMainWindow) -> None:
    """高亮跟随对号：当前项必须是对号行；没对号就不高亮。

    防"看起来选中了其实没勾"的假象——比如点对号方块取消勾选时，
    Qt 会先把那行设为当前项，不纠正就会留下一行无对号的高亮。
    """
    current = window.file_list.currentItem()
    if current is not None and current.checkState() == Qt.Checked:
        return
    for i in range(window.file_list.count()):
        item = window.file_list.item(i)
        if item.checkState() == Qt.Checked:
            window.file_list.setCurrentItem(item)
            return
    window.file_list.setCurrentRow(-1)

def _refresh_file_label(window: QMainWindow) -> None:
    """状态行文件标签跟随对号集合：0 个 = 未打开 / 1 个 = 文件名 /
    N 个 = 已选 N 个文件。"""
    checked = [window.file_list.item(i)
               for i in range(window.file_list.count())
               if window.file_list.item(i).checkState() == Qt.Checked]
    if not checked:
        window.file_label.setText("未打开文件")
    elif len(checked) == 1:
        window.file_label.setText(checked[0].text())
    else:
        window.file_label.setText(f"已选 {len(checked)} 个文件")

def _on_file_selected(window: QMainWindow, current, previous) -> None:
    """文件列表选中变化 → 只刷新状态行标签，不计算。

    算哪个视图由工具栏的作图按钮决定（点击 = 对每个对号文件开
    面板并计算），文件选择本身保持轻快——这也是"反应迟钝"问题
    的根源：以前每次点选都触发一次完整积分。
    """
    _refresh_file_label(window)

# ══ 右侧：参数面板 ═════════════════════════════════════════

def _build_param_dock(window: QMainWindow) -> QDockWidget:
    """参数坞：顶部固定"编辑对象"名，下面上下对半分两块（QGroupBox）
    ——数据参数（上）/ 图像参数（下），中间分隔条可拖。

    数据参数 = 参与计算的（几何配置 + 积分区间 + 点数），改它们
    会改变积分/校准的结果；图像参数 = 只看图不参与计算的，组内
    分两个区：2D/剖面视图（对比度 + 剖面线角度，接线后生效）+
    "1D 显示" 小节（对数纵轴 + 纵轴范围，随 [应用] 重画曲线）。
    两块各自独立滚动（内容放不下时自动出滚动条），[恢复默认]
    在左、[应用] 在右并排（通栏宽一分为二），固定在各区最下方，
    不随滚动走。
    排版约定（为窄排版）：单位放在输入框后缀里（标签不带括号单
    位）；成对的上下限并排一行（中间 ~ 连接，转盘限宽到数值能
    完整显示的底限）；小节用全宽灰色小标题（"1D 显示"不套子分组
    框，省嵌套边距）；每项悬停有人话提示。坞有尺寸下限（拖动边
    框时不会把控件裁掉）：左右 = 最宽一行完整显示的宽度，上下 =
    固定件不被遮没的高度。
    对照 CLI：数据参数来自 integrate/calibrate 脚本，图像参数来自
    view_diffraction 的 --vmin/--vmax/--angle。
    """
    dock = QDockWidget("参数", window)
    dock.setObjectName("param_dock")
    # 同文件坞：可移动/可浮动/可关闭，收起参数列给图让地方
    dock.setFeatures(QDockWidget.DockWidgetMovable
                     | QDockWidget.DockWidgetFloatable
                     | QDockWidget.DockWidgetClosable)

    content = QWidget()
    lay = QVBoxLayout(content)
    lay.setContentsMargins(0, 0, 0, 0)

    # 参数坞 = QStackedWidget 两页翻面：页 0 = 分析工作台（编辑对象
    # + 数据/图像参数对半），页 1 = 校准工作台（校准表单）。[校准]
    # 按钮按下/弹起 = 翻页（见 _on_mode），用户参数不丢（页 0 原样
    # 保留，退出模式还原）。
    window.param_stack = QStackedWidget()
    lay.addWidget(window.param_stack)

    # ── 页 0：分析工作台 ──
    analysis_page = QWidget()
    analysis_lay = QVBoxLayout(analysis_page)
    analysis_lay.setContentsMargins(0, 0, 0, 0)
    analysis_lay.setSpacing(0)

    # 编辑对象：参数坞当前作用在哪个图面板上。点图面板（_FocusMarker）
    # 或某视图计算完成（_on_integration_done）时更新；[应用] 重算它。
    # 编辑对象标题 = 文件名直出，可能很长：_ElideLabel 单行缩略，
    # 中间打省略号保留首尾（重名条目的区分后缀在尾部），悬停看
    # 全名，不撑宽参数坞
    window.focus_label = _ElideLabel("编辑对象：未选中图面板",
                                     Qt.ElideMiddle)
    window.focus_label.setStyleSheet("color: gray;")
    analysis_lay.addWidget(window.focus_label)   # 固定最上方，不随下面滚动

    # 下半区上下对半分：两块各自独立滚动的 QScrollArea + 底部固定
    # 按钮行。分隔条可拖（初始 1:1）；两块不允许拖到完全收起
    splitter = QSplitter(Qt.Vertical)
    splitter.setChildrenCollapsible(False)
    analysis_lay.addWidget(splitter, 1)
    window.param_stack.addWidget(analysis_page)

    # ── 页 1：校准工作台（calib.py；见其模块 docstring）──
    window.param_stack.addWidget(_build_calib_form(window))

    # ── 数据参数组（上半）──
    data_box = QGroupBox("数据参数")
    data_v = QVBoxLayout(data_box)
    data_v.setContentsMargins(4, 2, 4, 4)

    data_scroll = QScrollArea()
    data_scroll.setWidgetResizable(True)   # 条目随滚动区宽度自动重排
    data_scroll.setFrameShape(QFrame.NoFrame)   # 分组框已有边框，不再套一层
    data_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    data_fields = QWidget()
    form = QFormLayout(data_fields)
    form.setContentsMargins(2, 1, 2, 1)
    data_scroll.setWidget(data_fields)
    data_v.addWidget(data_scroll, 1)

    # 几何配置选择器：下拉框只显示短 key（如 lmfp1_lab6），完整
    # 批次备注走悬停提示（鼠标长放显示，不单独占一行——与用户
    # 讨论定稿）。key 藏在 itemData 里给程序用。
    # 选中即把该条目的标定几何填进下方三个输入框；完整条目（含
    # beam_center）挂在 window.config，后续 2D/剖面接线时直接取用。
    # 注意顺序：先填条目、设默认，再连接信号——建坞阶段日志区还没
    # 建好，信号此刻触发会去写一个还不存在的控件；默认值改由
    # create_window 收尾时显式调用 _apply_config 应用。
    window.config_combo = QComboBox()
    for name, entry in CONFIGS.items():
        window.config_combo.addItem(name, name)
        # 悬停提示：完整批次备注（下拉框里只放短 key）
        window.config_combo.setItemData(
            window.config_combo.count() - 1, entry["label"], Qt.ToolTipRole)

    window.config_combo.setCurrentIndex(
        window.config_combo.findData(DEFAULT_CONFIG))
    window.config_combo.currentIndexChanged.connect(
        lambda i: _apply_config(window, i))
    form.addRow("几何配置", window.config_combo)

    window.params = {}
    def add_caption(form, text):
        """全宽灰色小节标题（布局行横跨标签/字段两列）。"""
        cap = QLabel(text)
        cap.setStyleSheet("color: gray;")
        row = QHBoxLayout()
        row.addWidget(cap)
        form.addRow(row)
        return cap

    def add_float(form, name, lo, hi, value, decimals=2,
                  label=None, suffix="", tooltip=""):
        box = QDoubleSpinBox()
        box.setRange(lo, hi)
        box.setValue(value)
        box.setDecimals(decimals)
        if suffix:
            box.setSuffix(suffix)   # 单位跟在数字后（标签不再带括号单位）
        if tooltip:
            box.setToolTip(tooltip)
        window.params[name] = box
        form.addRow(label if label is not None else name, box)
        return box

    def add_range(form, lo_name, hi_name, lo, hi, lo_value, hi_value,
                  label, suffix="", decimals=1, tooltip="", max_width=84):
        """成对的上下限并排一行：左框 ~ 右框，共用一个行标签。

        window.params 的键保持原来的 lo_name/hi_name 不动（快照回放、
        测试都按这些键找控件），只改显示排版。max_width = 单框限宽：
        mac 原生转盘内边距很肥（sizeHint 117px 且缩不小），两框并排
        会把坞撑太宽；84 = "100000.0" 在编辑区完整显示的实测底限。
        """
        lo_box = QDoubleSpinBox()
        lo_box.setRange(lo, hi)
        lo_box.setValue(lo_value)
        lo_box.setDecimals(decimals)
        hi_box = QDoubleSpinBox()
        hi_box.setRange(lo, hi)
        hi_box.setValue(hi_value)
        hi_box.setDecimals(decimals)
        for box in (lo_box, hi_box):
            if suffix:
                box.setSuffix(suffix)
            if tooltip:
                box.setToolTip(tooltip)
            box.setMaximumWidth(max_width)
        window.params[lo_name] = lo_box
        window.params[hi_name] = hi_box
        field = QWidget()
        row = QHBoxLayout(field)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        row.addWidget(lo_box, 1)   # 两框平分行宽
        row.addWidget(QLabel("~"))
        row.addWidget(hi_box, 1)
        form.addRow(label, field)
        return lo_box, hi_box

    add_caption(form, "标定几何")
    add_float(form, "像素尺寸 (µm)", 0.0, 10000.0, 200.0, decimals=1,
              label="像素尺寸", suffix=" µm",
              tooltip="探测器单个像素的边长；随几何配置自动填入，也可手改")
    add_float(form, "波长 (Å)", 0.0, 10.0, 0.1223, decimals=4,
              label="波长", suffix=" Å",
              tooltip="X 射线波长；随几何配置自动填入，也可手改")
    add_float(form, "初始距离 (mm)", 0.0, 10000.0, 1600.0, decimals=1,
              label="初始距离", suffix=" mm",
              tooltip="样品到探测器的距离；随几何配置自动填入，也可手改")

    add_caption(form, "积分设置")
    add_range(form, "2θ 下限 (°)", "2θ 上限 (°)", 0.0, 90.0, 1.0, 8.0,
              label="2θ 范围", suffix=" °", max_width=88,
              tooltip="参与积分的衍射角区间")

    npt = QSpinBox()
    npt.setRange(100, 100000)
    npt.setValue(3000)
    npt.setToolTip("2θ 区间内取多少个采样点，越大曲线越细、计算越慢")
    window.params["输出点数"] = npt
    form.addRow("输出点数", npt)

    # [恢复默认] + [应用] 并排：[恢复默认] 只把参数复位（几何回到
    # 当前配置条目、区间/点数回到初值），不计算；[应用] 才重算焦点视图
    data_defaults = {
        "2θ 下限 (°)": 1.0,
        "2θ 上限 (°)": 8.0,
        "输出点数": 3000,
    }
    btn_reset_data = QPushButton("恢复默认")
    btn_reset_data.setObjectName("reset_data_btn")
    btn_apply = QPushButton("应用")
    btn_apply.setObjectName("apply_btn")
    btn_apply.clicked.connect(lambda: _apply_params(window))

    def reset_data():
        _apply_config(window, window.config_combo.currentIndex())
        for name, value in data_defaults.items():
            window.params[name].setValue(value)
        _log(window, "数据参数已恢复默认（未计算，点 [应用] 生效）")

    btn_reset_data.clicked.connect(reset_data)

    btn_col = QHBoxLayout()
    btn_col.setSpacing(4)
    # 通栏宽一分为二（与用户讨论定稿）：[恢复默认] 在左、[应用] 在右
    btn_col.addWidget(btn_reset_data, 1)
    btn_col.addWidget(btn_apply, 1)
    data_v.addLayout(btn_col)   # 按钮列固定在数据区最下方（滚动区之外）

    splitter.addWidget(data_box)

    # ── 图像参数组（下半）──
    img_box = QGroupBox("图像参数")
    img_v = QVBoxLayout(img_box)
    img_v.setContentsMargins(4, 2, 4, 4)

    img_scroll = QScrollArea()
    img_scroll.setWidgetResizable(True)   # 条目随滚动区宽度自动重排
    img_scroll.setFrameShape(QFrame.NoFrame)
    img_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    img_fields = QWidget()
    form2 = QFormLayout(img_fields)
    form2.setContentsMargins(2, 1, 2, 1)
    img_scroll.setWidget(img_fields)
    img_v.addWidget(img_scroll, 1)

    # 组内分区：上面的对比度/剖面角只对二维视图有意义（接线后
    # 生效）；下面的 "1D 显示" 子分组管曲线图自己的显示参数
    add_caption(form2, "2D/剖面视图（接线后生效）")

    # 自动对比度（默认开）：显示区间按编辑对象（焦点图）数据的
    # 1%/99.9% 分位自定，与 view_diffraction 的默认行为一致；取消勾
    # 选后手填两个输入框（对应 --vmin/--vmax 的"指定时覆盖自动值"
    # 语义）。自动模式下输入框置灰 = 只读展示程序正在用的区间；
    # 勾回自动 = 立刻按焦点图重算并填回（恢复默认对比度）。
    auto = QCheckBox("自动对比度")
    auto.setChecked(True)
    auto.setToolTip("显示区间按图像 1%~99.9% 分位自动确定")
    window.params["自动对比度"] = auto
    form2.addRow(auto)

    add_range(form2, "对比度下限", "对比度上限", 0.0, 1e9, 1.0, 100000.0,
              label="显示范围", decimals=1,
              tooltip="取消自动对比度后手填的显示区间（下限 ~ 上限）")

    def sync_contrast(checked, silent=False):
        window.params["对比度下限"].setEnabled(not checked)
        window.params["对比度上限"].setEnabled(not checked)
        if checked:
            _apply_auto_contrast(window, silent=silent)

    auto.toggled.connect(sync_contrast)
    sync_contrast(True, silent=True)   # 初始状态：自动开 → 输入框置灰（不刷日志）

    angle = add_float(form2, "剖面角度 (°)", -180.0, 180.0, 0.0, decimals=1,
                      label="剖面角度", suffix=" °",
                      tooltip="剖面线相对参考方向的角度")
    angle.setSingleStep(5.0)   # 步进 5°，对应 view_diffraction 的 --angle

    # 看图参数：不参与计算，只影响图怎么显示；点 [应用] 落到编辑
    # 对象（快照跟着更新）
    hint = QLabel("只看图不参与计算，点 [应用] 生效")
    hint.setStyleSheet("color: gray;")
    hint.setWordWrap(True)
    hint_row = QHBoxLayout()
    hint_row.addWidget(hint)
    form2.addRow(hint_row)   # 全宽一行（不再挤在标签列里竖排）

    # ── 1D 显示（小节）：曲线图自己的显示参数 ──
    # 不套子分组框（嵌套框自带一套标签列 + 边框，白白多占 ~30px
    # 宽）：灰色小节标题的层次感够用。对数纵轴：主峰与弱峰强度
    # 差几个数量级，对数刻度把弱峰"抬起来"（XRD 软件行规）。纵轴
    # 范围与对比度同套路：自动 = 按曲线 1%/99.9% 分位，取消勾选
    # 手填；自动模式输入框置灰 = 只读展示正在用的区间
    add_caption(form2, "1D 显示")

    # 视图 2θ 范围：只看图不参与计算的显示窗口。初始跟随数据组的
    # 积分 2θ 范围；在图里缩放/平移（滚轮/拖拽/Home/自定义对话框）
    # 会实时写回这里，[应用] 再用这里重画。与数据组的 2θ 范围完全
    # 分开——改这里不会影响积分的区间
    add_range(form2, "视图 2θ 下限 (°)", "视图 2θ 上限 (°)",
              0.0, 90.0, 1.0, 8.0,
              label="视图 2θ 范围", suffix=" °", max_width=88,
              tooltip="看图的窗口：缩放/平移实时写回；[应用] 用这里重画。"
                      "恢复默认 = 回到跟随积分范围")

    log_y = QCheckBox("对数纵轴")
    log_y.setToolTip("对数刻度：强弱峰差几个数量级时弱峰也看得清")
    window.params["对数纵轴"] = log_y
    form2.addRow(log_y)

    auto_y = QCheckBox("纵轴自动")
    auto_y.setChecked(True)
    auto_y.setToolTip("按曲线 1%~99.9% 分位自动确定纵轴区间")
    window.params["纵轴自动"] = auto_y
    form2.addRow(auto_y)

    add_range(form2, "纵轴下限", "纵轴上限", 0.0, 1e9, 1.0, 100000.0,
              label="纵轴范围", decimals=1,
              tooltip="取消自动后手填的纵轴区间（下限 ~ 上限）")

    # 对比归一化（与用户讨论定稿：下拉框四选一）——叠图时强度差
    # 很大的文件不归一会被强者压扁。四种模式：
    #   each   各自最强峰：每条曲线除以自己的最强峰
    #   global 全图最强峰：所有曲线除以全部曲线里最高的峰
    #   file   指定数据：所有曲线除以旁边下拉框选的文件的最强峰
    #   off    不归一化（默认：原样画原始强度）
    # 归一化只动显示层，原始结果原样保留在 compare_data。
    cmp_norm = QComboBox()
    for text, data in (("各自最强峰", "each"), ("全图最强峰", "global"),
                       ("指定数据…", "file"), ("不归一化", "off")):
        cmp_norm.addItem(text, data)
    cmp_norm.setCurrentIndex(cmp_norm.findData("off"))   # 默认 = 不归一化
    cmp_norm.setToolTip("叠图归一化：各自最强峰 / 全图最强峰 / "
                        "指定数据的最强峰 / 不归一化")
    window.params["对比归一化"] = cmp_norm
    norm_target = QComboBox()
    norm_target.setToolTip("以哪个文件的最强峰归一化（列表 = 对比面板的文件）")
    window.params["归一化目标"] = norm_target
    norm_row = QWidget()
    norm_lay = QHBoxLayout(norm_row)
    norm_lay.setContentsMargins(0, 0, 0, 0)
    norm_lay.setSpacing(2)
    norm_lay.addWidget(cmp_norm, 1)     # 同一行：模式在左、目标文件在右
    norm_lay.addWidget(norm_target, 1)
    form2.addRow(norm_row)

    def sync_norm_target(*_):
        norm_target.setEnabled(cmp_norm.currentData() == "file")

    cmp_norm.currentIndexChanged.connect(sync_norm_target)
    sync_norm_target()   # 初始 = 不归一化 → 目标下拉框置灰

    def sync_ylim(checked):
        window.params["纵轴下限"].setEnabled(not checked)
        window.params["纵轴上限"].setEnabled(not checked)
        if checked:
            _apply_auto_ylim(window)   # 勾回自动：立刻按焦点图算并填回（同对比度套路）

    auto_y.toggled.connect(sync_ylim)
    sync_ylim(True)   # 初始状态：自动开 → 输入框置灰

    img_defaults = {
        "对比度下限": 1.0,
        "对比度上限": 100000.0,
        "剖面角度 (°)": 0.0,
        "对数纵轴": False,
        "纵轴自动": True,
        "对比归一化": "off",
        "归一化目标": "",
    }
    btn_reset_img = QPushButton("恢复默认")
    btn_reset_img.setObjectName("reset_image_btn")

    def reset_image():
        if not auto.isChecked():
            auto.setChecked(True)   # 触发 sync_contrast → 按焦点图重算自动值
        else:
            _apply_auto_contrast(window)   # 已勾着 toggled 不响，手动重算
        window.params["剖面角度 (°)"].setValue(img_defaults["剖面角度 (°)"])
        window.params["对数纵轴"].setChecked(img_defaults["对数纵轴"])
        window.params["纵轴自动"].setChecked(img_defaults["纵轴自动"])
        window.params["对比归一化"].setCurrentIndex(
            window.params["对比归一化"].findData(img_defaults["对比归一化"]))
        window.params["归一化目标"].setCurrentIndex(0)
        if window.params["纵轴自动"].isChecked():
            _apply_auto_ylim(window)   # 已勾着 toggled 不响，手动重算填回
        # 视图 2θ 范围回到"跟随积分范围"：从焦点面板快照里删掉
        # 显式视图值（None = 跟随），输入框显示回积分范围
        dock = window.plot_docks.get(window.focus_panel)
        if dock is not None and getattr(dock, "params_snapshot", None):
            for name in ("视图 2θ 下限 (°)", "视图 2θ 上限 (°)"):
                dock.params_snapshot.pop(name, None)
        for name, base in (("视图 2θ 下限 (°)", "2θ 下限 (°)"),
                           ("视图 2θ 上限 (°)", "2θ 上限 (°)")):
            window.params[name].setValue(window.params[base].value())
        _log(window, "图像参数已恢复默认")

    btn_reset_img.clicked.connect(reset_image)

    # [恢复默认] 左 [应用] 右并排：与数据参数组同款布局。[应用]
    # 把当前图像参数应用到编辑对象（见 _apply_image_params）
    btn_apply_img = QPushButton("应用")
    btn_apply_img.setObjectName("apply_image_btn")
    btn_apply_img.clicked.connect(lambda: _apply_image_params(window))

    btn_col2 = QHBoxLayout()
    btn_col2.setSpacing(4)
    # 同数据参数组：通栏宽一分为二，[恢复默认] 在左、[应用] 在右
    btn_col2.addWidget(btn_reset_img, 1)
    btn_col2.addWidget(btn_apply_img, 1)
    img_v.addLayout(btn_col2)   # 按钮列固定在图像区最下方（滚动区之外）

    splitter.addWidget(img_box)
    splitter.setSizes([1, 1])          # 初始上下各一半
    splitter.setStretchFactor(0, 1)    # 随坞变高/变矮两块等比例伸缩
    splitter.setStretchFactor(1, 1)
    # 每半的最低高度：至少露出标题 + 按钮行 + 几行内容（内容靠滚动看）
    data_scroll.setMinimumHeight(110)
    img_scroll.setMinimumHeight(110)

    # 拖动坞边框的尺寸下限（上下左右都设）：左右 = 最宽一张表单的
    # 最小宽度 + "壳"（滚动条 + 分组/表单边距，另加少量余量），拖
    # 再窄也到这就停，不会裁掉控件；上下 = 固定件（编辑对象名 +
    # 两组标题/按钮行 + 各几行内容）不被遮没的高度，再矮由各半
    # 自己的滚动条接管。用表单 minimumSizeHint 而不是 content 的
    # sizeHint：转盘限宽后 sizeHint 低估了成对行的最小宽度（实测
    # 会横向裁掉近 20px）
    form_min = max(data_fields.minimumSizeHint().width(),
                   img_fields.minimumSizeHint().width())
    chrome = (data_scroll.verticalScrollBar().sizeHint().width()
              + data_v.contentsMargins().left() + data_v.contentsMargins().right()
              + form.contentsMargins().left() + form.contentsMargins().right())
    dock.setMinimumWidth(form_min + chrome + 2)
    content.setMinimumWidth(form_min + chrome + 2)   # 双保险：坞本身也算上
    # 坞的最小高度按分析页固定件显式算（编辑对象名 + 分隔条最小
    # 提示）：翻页栈的最小尺寸只报当前页，且嵌套后布局 minimumSize
    # 不再含子件 minimumSizeHint（探针验证），靠布局算会把下限
    # 塌成一行标签的高度
    dock.setMinimumHeight(
        splitter.minimumSizeHint().height()
        + window.focus_label.minimumSizeHint().height() + 4)

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
    # 文本编辑器默认接受拖放：不关掉的话，文件拖到日志区会被它
    # 吞掉（塞成文字），到不了顶层窗口的"拖入文件"入口
    window.log_text.setAcceptDrops(False)
    dock.setWidget(window.log_text)
    window.addDockWidget(Qt.BottomDockWidgetArea, dock)
    return dock

def _build_status(window: QMainWindow) -> None:
    """状态行（两层之二）：左侧常驻文字（"就绪"/瞬时消息）+ 右侧
    两块分明：坐标标签（等宽字体 + 凹槽框，一眼就是"数据读数"）
    | 竖分隔线 | 当前文件。用常驻 QLabel 而不是 showMessage——
    后者超时清空后状态栏会变成看不见的细条。坐标标签常驻（鼠标
    没悬停在曲线上时是空文字，悬停时才显示，见 _hover_motion），
    这样出字时状态栏不会整体跳动。"""
    window.status_text = QLabel("就绪")
    window.statusBar().addWidget(window.status_text)
    # 坐标标签：凹槽框 + 等宽字体——坐标是机器读出来的数，用等宽
    # 字体数字不会左右跳；凹槽框把"坐标信息"和右侧文件信息切开
    window.coord_label = QLabel("")   # 鼠标悬停时实时显示曲线坐标
    window.coord_label.setFrameShape(QFrame.Shape.StyledPanel)
    window.coord_label.setStyleSheet(
        "font-family: Menlo, Consolas, monospace; padding: 1px 4px;")
    window.statusBar().addPermanentWidget(window.coord_label)
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.VLine)
    sep.setFrameShadow(QFrame.Shadow.Sunken)
    window.statusBar().addPermanentWidget(sep)
    window.file_label = QLabel("未打开文件")
    window.statusBar().addPermanentWidget(window.file_label)

# ══ 顶部：工具栏 ═══════════════════════════════════════════
def _build_toolbar(window: QMainWindow) -> None:
    """工具栏 = [校准] 模式开关 + 作图按钮（2D/剖面/1D/瀑布 + 对比）+
    面板开关（文件/参数/日志）。"""
    tb = QToolBar("主工具栏", window)
    tb.setMovable(False)
    window.addToolBar(tb)

    # 模式开关：[校准] 可勾选。按下 = 校准工作台（几何参数 +
    # 点图微调束心），弹起 = 默认的分析工作台。原来的 [图像] 按钮
    # 已删：outputs 摊成四个作图按钮后，它只剩空壳
    btn_calib = QPushButton("校准")
    btn_calib.setCheckable(True)   # 默认弹起 = 分析工作台
    tb.addWidget(btn_calib)
    window.calib_btn = btn_calib   # 登记按钮（测试与后续接线用）
    btn_calib.toggled.connect(lambda on: _on_mode(window, on))

    tb.addSeparator()

    # 作图按钮：[2D][剖面][1D][瀑布]——点一下 = 打开面板 + 计算
    # 该视图并出图。纯动作不是开关：点几下算几下，重复点击安全；
    # 面板的开/关只由 × 和拖动管理（勾选式的第二次点击会关面板，
    # 让人误以为"画不了"）
    window.view_buttons = {}   # 登记按钮（测试与后续接线用）
    for name in VIEW_NAMES:
        btn = QPushButton(name)
        tb.addWidget(btn)
        window.view_buttons[name] = btn
        btn.clicked.connect(
            lambda checked=False, n=name: _plot_view(window, n))

    # [对比]：把勾选文件的 1D 曲线叠到一张图（见 _plot_compare）。
    # 同为纯动作：重复点击 = 刷新那张对比面板
    btn_compare = QPushButton("对比")
    tb.addWidget(btn_compare)
    window.compare_btn = btn_compare   # 登记按钮（测试用）
    btn_compare.clicked.connect(lambda: _plot_compare(window))

    tb.addSeparator()

    # 面板开关：[文件][参数][日志] 三个勾选按钮，收起/展开对应坞。
    # 全收起来 = 中央只剩绘图区，看图视野最大。双向同步：按钮点
    # 击 → 坞显隐；坞被标题栏 × 关掉 → 按钮自动弹起（visibilityChanged
    # 信号），下次点按钮还能再展开。
    window.panel_toggles = {}   # 登记按钮（测试与后续接线用）
    for name, dock in (("文件", window.file_dock),
                       ("参数", window.param_dock),
                       ("日志", window.log_dock)):
        btn = QPushButton(name)
        btn.setCheckable(True)
        btn.setChecked(True)
        tb.addWidget(btn)
        window.panel_toggles[name] = btn
        dock.visibilityChanged.connect(btn.setChecked)
        btn.toggled.connect(dock.setVisible)

def _on_mode(window: QMainWindow, calibrating: bool) -> None:
    """模式开关：勾选 = 校准工作台，弹起 = 分析工作台。

    进入：参数坞翻到校准页（页 0 的分析参数原样保留），勾选的第一个
    文件开校准面板；没勾文件只记日志提示（不崩）。退出：翻回分析页
    + 关校准面板（校准状态清零，关闭即遗忘）。
    """
    if calibrating:
        window.mode_label.setText("校准模式")
        _log(window, "进入校准模式")
        window.param_stack.setCurrentIndex(1)
        _enter_calib(window)
    else:
        window.mode_label.setText("分析模式")
        window.param_stack.setCurrentIndex(0)
        _exit_calib(window)
        _log(window, "回到分析模式")

# ══ 保存：勾选已输出的图 → 逐个选文件名存 PNG ═══════════════
def _save_figures(window: QMainWindow) -> bool:
    """[保存] 按钮与关窗询问共用：弹窗勾选要保存的图 → 逐个选文件名存 PNG。

    返回 False = 流程被取消（关窗时应留在程序里），True = 完成。
    目前只有 1D 面板有真图（figure）；占位面板不参与。
    """
    panels = [d for d in window.plot_docks.values()
              if getattr(_content(d), "figure", None) is not None]
    if not panels:
        _log(window, "没有已输出的图可保存")
        return True
    chosen = _choose_panels(window, panels)
    if chosen is None:
        _log(window, "已取消保存")
        return False
    if not chosen:
        _log(window, "没有勾选要保存的图")
        return False
    saved, skipped = 0, 0
    for dock in chosen:
        default = str(Path("outputs") / f"{dock.windowTitle()}.png")
        name, _ = QFileDialog.getSaveFileName(
            window, f"保存 {dock.windowTitle()}", default, "PNG 图片 (*.png)")
        if not name:
            skipped += 1   # 这张图用户没存：不算"保存完成"
            _log(window, f"已跳过保存 {dock.windowTitle()}")
            continue
        if not name.lower().endswith(".png"):
            name += ".png"
        try:
            Path(name).parent.mkdir(parents=True, exist_ok=True)
            _content(dock).figure.savefig(name)
        except OSError as err:
            _log(window, f"保存失败 {dock.windowTitle()} → {name}（{err}）")
            skipped += 1
            continue
        dock.figure_saved = True
        saved += 1
        _log(window, f"已保存 {dock.windowTitle()} → {name}")
    if saved:
        _log(window, f"保存完成：{saved} 张图")
    return skipped == 0

def _choose_panels(window: QMainWindow, panels) -> list:
    """弹窗勾选要保存的面板（默认全勾）；确定 = 勾选列表（可为空），
    取消 = None（与"确定但一张没勾"区分开）。"""
    dlg = QDialog(window)
    dlg.setWindowTitle("保存哪些图")
    lay = QVBoxLayout(dlg)
    lay.addWidget(QLabel("勾选要保存的图："))
    lst = QListWidget()
    for d in panels:
        it = QListWidgetItem(d.windowTitle())
        it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
        it.setCheckState(Qt.Checked)   # 默认全勾
        lst.addItem(it)
    lay.addWidget(lst)
    row = QHBoxLayout()
    ok = QPushButton("确定")
    cancel = QPushButton("取消")
    row.addWidget(ok)
    row.addWidget(cancel)
    lay.addLayout(row)
    ok.clicked.connect(dlg.accept)
    cancel.clicked.connect(dlg.reject)
    if dlg.exec() != QDialog.Accepted:
        return None
    return [panels[i] for i in range(lst.count())
            if lst.item(i).checkState() == Qt.Checked]

def _confirm_close(window: QMainWindow, n_unsaved: int) -> str:
    """关窗时询问未保存的图怎么处理；返回 "save" / "discard" / "cancel"。

    窗口从未显示过（测试等非交互场景）不弹框，直接按"不保存"关，
    免得模态对话框把测试挂死；真实使用中窗口显示过，正常弹框。
    """
    if not window.isVisible():
        return "discard"
    ans = QMessageBox.question(
        window, "关闭 XRD Toolkit",
        f"有 {n_unsaved} 张图尚未保存，要保存后再关闭吗？",
        QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
        QMessageBox.Save)
    return {QMessageBox.Save: "save", QMessageBox.Discard: "discard"}.get(
        ans, "cancel")

# ══ 主窗口组装 ════════════════════════════════════════════
def create_window() -> QMainWindow:
    """组装主框架：文件坞 + 参数坞 + 日志坞 + 工具栏 + 中央面板区。"""
    window = _MainWindow()
    window.setWindowTitle("XRD Toolkit")
    window.resize(1200, 800)
    # 关窗即销毁 C++ 对象：窗口自己的 dict 里挂满了捕获自己的闭包
    # （点选/作图/保存等回调），Python 引用计数环永远归不了零；而
    # shiboken 的 C++→Python 绑定映射在 C++ 对象存活期间持有包装
    # 器，gc 视之为可达也收不掉（探针实证：裸窗口能回收、带闭包环
    # 的窗口不能）。close 时先销毁 C++ 对象 → 绑定映射解除 → 闭包
    # 环变成无根环，gc 即可收走。取消/保存失败走 event.ignore()，
    # 窗口留在程序里不受影响。
    window.setAttribute(Qt.WA_DeleteOnClose)
    # 拖文件进窗口任意位置 = 加进文件列表（拖放事件冒泡到顶层窗口）
    window.drop_callback = lambda paths: add_files(window, paths)

    window._status_timer = None   # _log 里的状态栏恢复计时器（懒创建）
    window.log = lambda text: _log(window, text)
    window.add_files = lambda paths: add_files(window, paths)
    # 布局/同步旗标（各自用途见 _on_canvas_resized / _tile_panels /
    # _draw_1d / _on_limits_changed 的注释）
    window._layouting = False          # 程序自己在平铺/布局（不算用户拖动）
    window._setting_limits = False     # 程序自己在画图设范围（不算用户改动）

    # 后台任务簿：进行中的积分任务挂在这里防垃圾回收（结束回调里
    # 移除）；关窗口时逐一 discard（等待后台函数返回，防线程悬空）
    window._tasks = []
    # 每面板的最新任务：同面板连点两次时，先开的晚到会被丢弃
    window._latest_task = {}
    # 自动对比度的图像缓存（路径 → 图像数组，上限 3 张）：同一文件
    # 反复点焦点不重复解码大 tif
    window._image_cache = {}
    # 焦点面板：参数面板"编辑对象"指向的图面板（点图/计算完成时更新）
    window.focus_panel = None

    _build_center(window)
    # 点任何面板窗口内任何位置都选中该面板（应用级过滤器，原因见
    # _PanelClickTracker）。每个窗口装一个；窗口销毁时过滤器随父
    # 对象销毁，Qt 自动把它从应用事件分发里摘掉
    QApplication.instance().installEventFilter(_PanelClickTracker(window))
    window.file_dock = _build_file_dock(window)
    window.param_dock = _build_param_dock(window)
    window.log_dock = _build_log_dock(window)
    _build_status(window)
    _build_toolbar(window)

    # 点选文件 → 只登记当前文件（此时各坞已就绪，可安全接信号）
    window.file_list.currentItemChanged.connect(
        lambda cur, prev: _on_file_selected(window, cur, prev))

    # 关窗口：有未保存的图先询问（保存 / 不保存 / 取消），再等后台
    # 任务收尾——直接销毁运行中的线程 Qt 会 abort
    def on_close(event):
        unsaved = [d for d in window.plot_docks.values()
                   if getattr(_content(d), "figure", None) is not None
                   and not getattr(d, "figure_saved", False)]
        if unsaved:
            choice = _confirm_close(window, len(unsaved))
            if choice == "cancel":
                event.ignore()   # 取消 → 留在程序里
                return
            if choice == "save" and not _save_figures(window):
                event.ignore()   # 保存流程被取消 → 留在程序里
                return
        for task in window._tasks:
            task.discard()
        # 弹出的顶层窗口不随主窗口关，逐一关掉（close → 遗忘登记；
        # 遍历拷贝：close 会把面板从 plot_docks 里摘掉）
        for d in list(window.plot_docks.values()):
            if isinstance(d, _FloatedWindow):
                d.close()
        event.accept()

    window.closeEvent = on_close

    # 初始尺寸：文件列/参数列默认收到最窄（各自内容的最小宽度），
    # 需要时用户自己拖宽；日志高 140（都可拖动）
    for d in (window.file_dock, window.param_dock):
        window.resizeDocks([d], [d.minimumSizeHint().width()], Qt.Horizontal)
    window.resizeDocks([window.log_dock], [140], Qt.Vertical)

    # 启动即应用默认配置条目：参数坞初值来自注册表（如初始距离
    # 1595.80 mm），而不是写死的占位默认值
    _apply_config(window, window.config_combo.currentIndex())

    window.log("主框架已就绪")
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
