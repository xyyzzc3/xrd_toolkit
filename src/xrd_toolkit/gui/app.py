"""GUI 主程序：主框架（可停靠面板式工作区）与事件循环（PySide6）。

入口：python -m xrd_toolkit.gui（__main__.py 转发到本文件的 main()）。
create_window() 与 main() 分离：测试里可以只建窗口、不进事件循环。

架构说明：
  - 外窗口（QMainWindow）管三块固定坞：文件（左）/ 参数（右）/
    日志（底）+ 顶部工具栏 + 状态行；工具栏尾部有 [文件][参数]
    [日志] 三个收起开关（也可用各坞标题栏的 ×），全收起来就只剩
    绘图区，看图视野最大；
  - Qt 的中央区不接受停靠，所以中央再嵌一个内层 QMainWindow 专门
    管理图面板坞——嵌套是标准做法（PyCharm / Spyder 同款）；
  - 框架阶段的占位动作统一写日志区 + 状态行提示"尚未实现"；
    实现功能时只替换动作背后的处理，框架结构不变；
  - 文件进列表两种方式：拖文件（tif/edf/cbf）到窗口任意位置，或
    点 [打开] 选文件；
  - 已接线（1D 闭环）：交互模型 = 文件列表以对号选择（点行 = 只
    加选不取消，取消只能用对号方块；选入/拖入默认全勾；重复文件
    弹窗覆盖/改名）→ 点击视图按钮对每个对号文件各开面板并算该
    视图（多选 = 批量，一次出多张图；按钮是纯动作不是开关，重复
    点击安全；面板按「视图 + 文件」成对创建，同一视图可同时开多
    张不同文件的图）→ 点图面板设"编辑对象"，参数坞回放该面板
    的参数快照（点哪张图就显示哪张图作图时的参数）→ 数据 [应用]
    用当前参数重算焦点面板；图像 [应用] 只改显示不重算——显示
    参数（对数纵轴/纵轴范围/对比归一化）是全局风格，点一次 =
    所有已打开的 1D/对比图用已有数据一起重画，切面板不回放；
    计算走 gui/tasks.py 后台线程，界面不卡。
  - [对比]（工具栏，仅 1D）：把勾选的多个文件叠进同一张图（勾选
    数 >= 2），每文件一个后台任务、全部算完再一起画；面板键 =
    f"对比|{排序后的路径们}"，重复点击同一选择 = 复用同一面板并
    重算；颜色自动循环（C0/C1/...），图例 = 显示名；"对比归一化
    到最强峰"（默认开，1D 显示组）= 每条曲线除以自己的最强峰，
    曝光差很多的文件也看得清彼此峰形；1D 显示参数（对数/纵轴
    范围）对对比面板同样生效。
  - [保存] 是主动操作：弹窗勾选要保存的已出图面板 → 逐个选文件
    名存 PNG；另外关闭窗口时若有尚未保存的图会弹窗询问
    （保存后关闭 / 不保存直接关 / 取消留在程序里）。

窗口上的公共接口（供后续页面接线与测试使用）：
  window.log(text)        写日志区 + 状态行
  window.add_files(paths) 把文件加进左侧列表
  window.plot_docks       {面板键: QDockWidget}，键 = f"{视图}|{路径}"，
                          重复文件改名条目再补 |显示名 区分；标题 =
                          f"{视图}_{显示名}"（如 1D_lab6-00024.tif）
  window.focus_panel      参数面板编辑对象 = 面板键（点图/计算完成时设定）
  window.params           参数面板控件字典
  window.config_name      当前选中的配置条目 key（如 lmfp1_lab6）
  window.config           完整条目 dict（label / geometry / beam_center）
  1D 面板的画布/坐标轴在面板控件上：dock.widget().axes_1d
"""
import sys
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("qtagg")   # 必须在导入 FigureCanvasQTAgg 之前选定 Qt 后端
# 图标题取自文件显示名（重复文件改名可输入中文）→ 字体回退链补上
# macOS 中文字体，缺字形时逐字体回退，标题不会渲染成方框。
# 注意要设 font.family 直接给列表：实测 qtagg 后端下 font.sans-serif
# 列表不触发回退（Agg 可以），中文仍会变方框
matplotlib.rcParams["font.family"] = [
    "DejaVu Sans", "PingFang SC", "Hiragino Sans GB", "Arial Unicode MS"]
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QEvent, QObject, Qt, QSize, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QInputDialog, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QSizePolicy, QSpinBox, QToolBar,
    QVBoxLayout, QWidget, QDockWidget, QApplication)

from xrd_toolkit.config import CONFIGS, DEFAULT_CONFIG  # 几何配置注册表（下拉框数据源）
from xrd_toolkit.gui.tasks import BackgroundTask  # 后台线程任务（积分等耗时计算）
from xrd_toolkit.services.data_loader import load_diffraction_image
from xrd_toolkit.services.integrator import integrate_1d

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


class _FocusMarker(QObject):
    """事件过滤器：点击图面板时把参数面板的编辑对象切到该面板。

    装在图面板控件上；只"监听"不"拦截"——eventFilter 返回 False，
    鼠标事件照常传给画布/标签。参数坞顶部的"编辑对象"标签随点击
    更新（显示"视图_文件名"，多张图一眼分清在编辑哪张），[应用]
    就作用在这个焦点面板上；同时参数坞回放该面板的参数快照——
    点哪张图，旁边的参数就显示哪张图作图时的参数（能看也能改）。
    """

    def __init__(self, window: QMainWindow, key: str, title: str):
        super().__init__(window)   # 挂在窗口上，随窗口销毁
        self._window = window
        self._key = key
        self._title = title

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonPress:
            _set_focus(self._window, self._key, self._title)
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


def _snapshot_params(window: QMainWindow) -> dict:
    """拍下参数面板当前状态：配置条目 + 各控件值 + 自动对比度开关。

    每张面板在开图/重算时拍一张快照挂在 dock.params_snapshot 上 =
    "这张图是用什么参数画的"；点选面板时参数坞回放快照（既能看也
    能改，改完 [应用] 重算并刷新快照）。
    """
    snap = {"config": window.config_name}
    for name, w in window.params.items():
        if isinstance(w, QCheckBox):
            snap[name] = w.isChecked()
        else:
            snap[name] = w.value()
    return snap


_DISPLAY_PARAMS = {"对数纵轴", "纵轴自动", "纵轴下限", "纵轴上限",
                   "对比归一化"}   # 1D 显示组：全局风格，不随焦点回放


def _load_params_snapshot(window: QMainWindow, snap: dict) -> None:
    """把参数快照填回参数面板（只展示不计算）。

    顺序讲究：
      - 先切几何配置：触发 _apply_config，同步 window.config（PONI/
        倾斜角取该条目注册表值，[应用] 依赖它）；
      - 再填各输入框：用"当时真用的"值覆盖注册表几何值；
      - 最后处理自动对比度开关（blockSignals 防日志刷屏）：勾着 →
        静默按焦点图重算自动值展示；手动模式 → 回放快照里的值。

    显示参数（_DISPLAY_PARAMS）跳过回放：它们是全局风格，[应用] 时
    所有图一起重画；切面板若回放旧值会把用户刚改的显示设置冲掉。
    """
    idx = window.config_combo.findData(snap.get("config"))
    if idx >= 0:
        window.config_combo.setCurrentIndex(idx)
    auto = window.params.get("自动对比度")
    for name, value in snap.items():
        if name == "config" or name == "自动对比度" or name in _DISPLAY_PARAMS:
            continue
        w = window.params.get(name)
        if w is None:
            continue
        if isinstance(w, QCheckBox):
            w.setChecked(value)
        else:
            w.setValue(value)
    if auto is not None:
        auto.blockSignals(True)
        auto.setChecked(snap.get("自动对比度", True))
        auto.blockSignals(False)
        window.params["对比度下限"].setEnabled(not auto.isChecked())
        window.params["对比度上限"].setEnabled(not auto.isChecked())
        if auto.isChecked():
            _apply_auto_contrast(window, silent=True)   # 焦点图的自动值
        else:
            window.params["对比度下限"].setValue(snap.get("对比度下限", 1.0))
            window.params["对比度上限"].setValue(
                snap.get("对比度上限", 100000.0))


def _set_focus(window: QMainWindow, key: str, title: str) -> None:
    """把参数面板的编辑对象切到某面板（点面板 / 计算完成时调用）。

    切过去的同时回放该面板的参数快照——点哪张图，旁边就显示哪张
    图的参数。重复点同一面板直接返回：不冲掉用户正在改的值。
    """
    if window.focus_panel == key:
        return
    window.focus_panel = key
    window.focus_label.setText(f"编辑对象：{title}")
    dock = window.plot_docks.get(key)
    snap = getattr(dock, "params_snapshot", None)
    if snap is not None:
        _load_params_snapshot(window, snap)


class _PressRecorder(QObject):
    """记录鼠标按下时命中的文件项与对号状态（装在列表视口上）。

    用途：区分"点对号方块"（Qt 在弹起时自动切换对号，itemChanged
    先到）与"点行其他位置"（对号不动）——itemClicked 据此决定手势：
    点行 = 只勾不取消（加选），方块 = 勾上/取消。
    """

    def __init__(self, window: QMainWindow, lst: QListWidget):
        super().__init__(window)   # 挂在窗口上，随窗口销毁
        self._window = window
        self._list = lst
        window._press_item = None
        window._press_state = None

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonPress:
            item = self._list.itemAt(event.position().toPoint())
            self._window._press_item = item
            self._window._press_state = item.checkState() if item else None
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
        window.file_list.setCurrentRow(-1)
        _refresh_file_label(window)   # 状态行跟随剩余对号集合
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


# ══ 文件 → 1D 闭环：选文件 → 后台积分 → 1D 面板出图 ══════════
def _collect_geometry(window: QMainWindow) -> dict:
    """从参数面板收集积分几何。

    PONI/倾斜角取当前配置条目的标定值（面板暂不暴露），像素/波长/
    距离取面板输入框——对应 integrate_pattern 里 --pixel/--wavelength/
    --dist 覆盖配置值的语义。
    """
    geom = window.config["geometry"]
    return dict(
        pixel_size_m=window.params["像素尺寸 (µm)"].value() * 1e-6,
        wavelength_m=window.params["波长 (Å)"].value() * 1e-10,
        dist_m=window.params["初始距离 (mm)"].value() * 1e-3,
        poni1_m=geom["poni1_m"],
        poni2_m=geom["poni2_m"],
        rot1_deg=geom["rot1_deg"],
        rot2_deg=geom["rot2_deg"],
    )


def _compute_integration(path_str: str, geom: dict, npt: int) -> tuple:
    """后台线程里运行的纯计算：读图 → 全角度积分。

    不碰任何界面控件；异常由 tasks.BackgroundTask 转成 error 信号
    送回主线程。
    """
    image = load_diffraction_image(path_str)
    tth, intensity = integrate_1d(image, npt=npt, **geom)
    return tth, intensity


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


def _run_view(window: QMainWindow, name: str, path: Path, key: str) -> None:
    """对 (视图, 文件) 面板跑对应的计算（后台线程）。

    1D = 全角度积分；其余视图尚未接线（面板保持占位）。
    """
    if name != "1D":
        _log(window, f"{name} 视图尚未接线（面板占位）")
        return
    # 开工前先刷新参数快照：这张图 = 这组参数画的，点选面板时回放
    dock = window.plot_docks.get(key)
    if dock is not None:
        dock.params_snapshot = _snapshot_params(window)
    geom = _collect_geometry(window)
    npt = int(window.params["输出点数"].value())
    window.status_text.setText(f"正在积分 {path.name}…")
    _log(window, f"开始积分 {path.name}（后台线程）")
    _spawn(window, path, geom, npt, key)


def _apply_params(window: QMainWindow) -> None:
    """[应用] 按钮：用当前参数重算焦点面板。

    焦点面板 = 参数坞顶部"编辑对象"（点图面板或计算完成时设定）；
    没选焦点时提示用户先点图。
    """
    key = window.focus_panel
    if key is None:
        _log(window, "先点击要更新的图面板（如 1D），再点 [应用]")
        return
    dock = window.plot_docks.get(key)
    if dock is None:
        _log(window, "编辑对象的面板已不存在")
        window.focus_panel = None
        window.focus_label.setText("编辑对象：未选中图面板")
        return
    view = key.split("|", 1)[0]
    _log(window, f"[应用] 重算编辑对象：{dock.windowTitle()}")
    if view == "对比":
        _run_compare(window, key)   # 一组文件全部重算
        return
    _run_view(window, view, dock.panel_file, key)


def _apply_image_params(window: QMainWindow) -> None:
    """[应用] 按钮（图像参数组）：把当前显示参数应用到所有已打开的图。

    显示参数（对数纵轴/纵轴范围/对比归一化）是全局风格：改完点一次
    [应用] = 所有 1D 面板 + 对比面板用已有数据一起重画（不重新积分），
    不会出现"改了只动一张、别的图不动"的分裂状态。没算完的面板跳过。
    2D/剖面尚未接线，其占位面板不参与；数据参数仍按焦点面板快照
    （见 _apply_params）。显示参数切面板不回放（见 _DISPLAY_PARAMS）。
    """
    key = window.focus_panel
    if key is None:
        _log(window, "先点击要更新的图面板（如 1D），再点 [应用]")
        return
    dock = window.plot_docks.get(key)
    if dock is None:
        _log(window, "编辑对象的面板已不存在")
        window.focus_panel = None
        window.focus_label.setText("编辑对象：未选中图面板")
        return
    dock.params_snapshot = _snapshot_params(window)   # 数据参数快照照常刷新
    redrawn = 0
    for k, d in window.plot_docks.items():
        view = k.split("|", 1)[0]
        if view == "1D":
            if getattr(d, "last_tth", None) is None:
                continue   # 还没算完的面板：跳过，不误报
            _draw_1d(window, d, d.last_tth, d.last_intensity)
            redrawn += 1
        elif view == "对比":
            if not getattr(d, "compare_data", None):
                continue
            _redraw_compare(window, k)
            redrawn += 1
    if redrawn:
        _log(window, f"[应用] 图像参数已重画 {redrawn} 张图")
    view = key.split("|", 1)[0]
    if view not in ("1D", "对比"):
        _log(window, f"[应用] 图像参数已更新编辑对象：{dock.windowTitle()}"
                     f"（{view} 视图尚未接线）")
    elif redrawn == 0:
        _log(window, f"[应用] 图像参数：{dock.windowTitle()} 还没有"
                     f"计算结果（积分完成后再试）")


def _spawn(window: QMainWindow, path: Path, geom: dict, npt: int,
           key: str, on_done=None, on_error=None) -> None:
    """启动后台积分任务；引用挂在 window._tasks 防垃圾回收，结束移除。

    task 变量在闭包外定义、闭包内只引用：done/error 回调在任务结束
    时才被调用，那时 task 早已完成赋值。

    单文件面板走默认回调（_on_integration_done 画一张图）；对比
    面板传入 on_done/on_error——一个面板有多个任务，各自把结果
    画到同一张图上、出错时各自计数。
    """
    task = None

    def done(result):
        window._tasks.remove(task)
        (on_done or _on_integration_done)(window, key, task, result)

    def error(msg):
        window._tasks.remove(task)
        if on_error is not None:
            on_error(msg)
        else:
            _on_integration_error(window, path, msg)

    task = BackgroundTask(_compute_integration, str(path), geom, npt,
                          on_done=done, on_error=error)
    window._latest_task[key] = task   # 每面板只认最新任务（防旧结果覆盖）
    window._tasks.append(task)
    task.start()


def _on_integration_done(window: QMainWindow, key: str, task, result) -> None:
    """面板的计算完成（主线程）：画进它自己的面板。

    每个面板绑定自己的文件，结果永远画回自己的面板；唯一的过期
    情况是同一面板连点两次开了两个任务——先开的晚到会被丢弃
    （每面板只认最新任务，旧结果不得覆盖新图）。
    """
    if window._latest_task.get(key) is not task:
        dock = window.plot_docks[key]
        _log(window, f"已忽略 {dock.panel_display} 的过期结果"
                     f"（同一面板已有更新的计算）")
        return
    dock = window.plot_docks[key]
    tth, intensity = result
    # 结果留在面板上：图像参数 [应用] 只改显示时，用已有数据重画，
    # 不用重新积分
    dock.last_tth = tth
    dock.last_intensity = intensity
    _set_focus(window, key, dock.windowTitle())   # 最新出的图成为编辑对象
    _draw_1d(window, dock, tth, intensity)
    _log(window, f"积分完成：{dock.panel_display}（{len(tth)} 点，"
                 f"2θ {tth.min():.3f}~{tth.max():.3f}°）")


def _on_integration_error(window: QMainWindow, path: Path, msg: str) -> None:
    """积分失败（主线程）：报错进日志区，不崩溃。"""
    _log(window, f"积分失败：{path.name} — {msg}")


def _draw_1d(window: QMainWindow, dock: QDockWidget, tth, intensity) -> None:
    """在指定的 1D 面板画出积分曲线（只允许主线程调用）。

    x 轴范围跟随参数坞的 2θ 上下限（看图范围，不参与计算）；
    显示样式跟随 1D 显示参数（对数纵轴 / 纵轴范围）。积分完成与
    图像参数 [应用] 都会走这里——后者只改显示、不重新积分。
    """
    ax = dock.widget().axes_1d
    ax.clear()
    ax.plot(tth, intensity, "b-", lw=0.8)
    lo = window.params["2θ 下限 (°)"].value()
    hi = window.params["2θ 上限 (°)"].value()
    if lo < hi:
        ax.set_xlim(lo, hi)
    # 对数纵轴：弱峰"抬起来"（XRD 行规，主峰与弱峰强度差几个数量级）
    log_y = window.params["对数纵轴"].isChecked()
    if log_y:
        ax.set_yscale("log")
    # 纵轴范围：自动 = 按曲线 1%/99.9% 分位；手动 = 手填上下限。
    # 对数轴画不出 ≤0 的范围，手动值也兜底抬高
    if window.params["纵轴自动"].isChecked():
        ylo, yhi = _auto_y_range(intensity, log_y)
    else:
        ylo = window.params["纵轴下限"].value()
        yhi = window.params["纵轴上限"].value()
        if log_y:
            ylo = max(ylo, 1e-6)
    if ylo < yhi:
        ax.set_ylim(ylo, yhi)
    ax.set_xlabel("2θ (deg)")
    ax.set_ylabel("Intensity (a.u.)")
    ax.set_title(f"{dock.panel_display}: full azimuthal integration")
    ax.grid(alpha=0.3)
    dock.widget().draw()
    dock.figure_saved = False   # 重画 = 新内容还没存盘


# ══ 右侧：参数面板 ═════════════════════════════════════════
_CONTRAST_FALLBACK = (1.0, 100000.0)   # 没有焦点图时的自动对比度占位默认


def _auto_y_range(intensity, log_scale: bool) -> tuple:
    """1D 曲线自动纵轴范围：取 1%/99.9% 分位，掐掉极端强/弱值。

    与自动对比度同一套路（见 _auto_contrast_values），只是对象从
    图像像素换成曲线强度。对数纵轴时只取正值参与分位（对数轴画
    不出 ≤0 的范围）；没有有效数值时回退占位默认。
    """
    arr = np.asarray(intensity, dtype=float)
    finite = arr[np.isfinite(arr)]
    if log_scale:
        finite = finite[finite > 0]
    if finite.size == 0:
        return _CONTRAST_FALLBACK
    return float(np.percentile(finite, 1.0)), float(np.percentile(finite, 99.9))


def _auto_contrast_values(image) -> tuple:
    """自动对比度：取全体像素的 1%/99.9% 分位，掐掉极端亮暗值。

    与 CLI view_diffraction 的默认 vmin/vmax 语义一致。返回 (lo, hi)；
    图像没有有效数值时报 ValueError。
    """
    arr = np.asarray(image, dtype=float)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        raise ValueError("图像没有有效数值")
    return float(np.percentile(finite, 1.0)), float(np.percentile(finite, 99.9))


def _apply_auto_contrast(window: QMainWindow, silent: bool = False) -> None:
    """按编辑对象（焦点图）重算自动对比度，填进置灰的输入框。

    输入框在自动模式下只是"程序正在用的区间"的展示（只读）。没有
    焦点图或读取失败时填占位默认。2D 面板真正画图时用
    _auto_contrast_values 从内存里的图像直接算，与这里的展示一致。
    """
    lo, hi = _CONTRAST_FALLBACK
    path = None
    if window.focus_panel is not None:
        dock = window.plot_docks.get(window.focus_panel)
        if dock is not None:
            # 对比面板没有 panel_file（一组文件）：占位默认即可
            path = getattr(dock, "panel_file", None)
    loaded = False
    if path is not None:
        try:
            image = load_diffraction_image(str(path))
            lo, hi = _auto_contrast_values(image)
            loaded = True
        except Exception as err:
            _log(window, f"自动对比度：读取 {path.name} 失败（{err}），"
                         f"恢复占位默认值")
    window.params["对比度下限"].setValue(lo)
    window.params["对比度上限"].setValue(hi)
    if not silent:
        if loaded:
            _log(window, f"自动对比度：按 {path.name} 算得 {lo:.1f} ~ {hi:.1f}")
        elif path is None:
            _log(window, "自动对比度：没有编辑对象，恢复占位默认值")


def _build_param_dock(window: QMainWindow) -> QDockWidget:
    """参数坞：两个分组（QGroupBox）——数据参数 / 图像参数。

    数据参数 = 参与计算的（几何配置 + 积分区间 + 点数），改它们
    会改变积分/校准的结果；图像参数 = 只看图不参与计算的，组内
    分两个区：2D/剖面视图（对比度 + 剖面线角度，接线后生效）+
    "1D 显示" 子分组（对数纵轴 + 纵轴范围，随 [应用] 重画曲线）。
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

    # 编辑对象：参数坞当前作用在哪个图面板上。点图面板（_FocusMarker）
    # 或某视图计算完成（_on_integration_done）时更新；[应用] 重算它。
    # 编辑对象标题 = 文件名直出，可能很长：_ElideLabel 单行缩略，
    # 中间打省略号保留首尾（重名条目的区分后缀在尾部），悬停看
    # 全名，不撑宽参数坞
    window.focus_label = _ElideLabel("编辑对象：未选中图面板",
                                     Qt.ElideMiddle)
    window.focus_label.setStyleSheet("color: gray;")
    lay.addWidget(window.focus_label)

    # ── 数据参数组 ──
    data_box = QGroupBox("数据参数")
    form = QFormLayout(data_box)

    # 几何配置选择器：下拉框只显示短 key（如 lmfp1_lab6）——长备注
    # 挤进下拉框会撑宽参数坞；完整批次备注放在下方灰色说明行
    # （随选择更新）+ 悬停提示。key 藏在 itemData 里给程序用。
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

    # 说明行也换 _ElideLabel：自动换行模式下第二行会被 QFormLayout
    # 按一行高布局、压在下一行控件底下（"被遮挡"）。改单行缩略 +
    # 悬停全名：多长的备注都只占一行，不撑宽也不被遮
    window.config_label = _ElideLabel(
        CONFIGS[DEFAULT_CONFIG]["label"], Qt.ElideRight)
    window.config_label.setStyleSheet("color: gray;")

    field = QWidget()   # 下拉框 + 说明行装进一个字段（表格行内竖排）
    box = QVBoxLayout(field)
    box.setContentsMargins(0, 0, 0, 0)
    box.setSpacing(2)
    box.addWidget(window.config_combo)
    box.addWidget(window.config_label)

    window.config_combo.setCurrentIndex(
        window.config_combo.findData(DEFAULT_CONFIG))
    window.config_combo.currentIndexChanged.connect(
        lambda i: _apply_config(window, i))
    form.addRow("几何配置", field)

    window.params = {}
    def add_float(form, name, lo, hi, value, decimals=2):
        box = QDoubleSpinBox()
        box.setRange(lo, hi)
        box.setValue(value)
        box.setDecimals(decimals)
        window.params[name] = box
        form.addRow(name, box)
        return box

    add_float(form, "像素尺寸 (µm)", 0.0, 10000.0, 200.0, decimals=1)
    add_float(form, "波长 (Å)", 0.0, 10.0, 0.1223, decimals=4)
    add_float(form, "初始距离 (mm)", 0.0, 10000.0, 1600.0, decimals=1)
    add_float(form, "2θ 下限 (°)", 0.0, 90.0, 1.0)
    add_float(form, "2θ 上限 (°)", 0.0, 90.0, 8.0)

    npt = QSpinBox()
    npt.setRange(100, 100000)
    npt.setValue(3000)
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

    btn_row = QHBoxLayout()
    btn_row.addWidget(btn_reset_data)
    btn_row.addWidget(btn_apply)
    form.addRow(btn_row)

    lay.addWidget(data_box)

    # ── 图像参数组 ──
    img_box = QGroupBox("图像参数")
    form2 = QFormLayout(img_box)

    # 组内分区：上面的对比度/剖面角只对二维视图有意义（接线后
    # 生效）；下面的 "1D 显示" 子分组管曲线图自己的显示参数
    cap_2d = QLabel("2D/剖面视图（接线后生效）")
    cap_2d.setStyleSheet("color: gray;")
    form2.addRow(cap_2d)

    # 自动对比度（默认开）：显示区间按编辑对象（焦点图）数据的
    # 1%/99.9% 分位自定，与 view_diffraction 的默认行为一致；取消勾
    # 选后手填两个输入框（对应 --vmin/--vmax 的"指定时覆盖自动值"
    # 语义）。自动模式下输入框置灰 = 只读展示程序正在用的区间；
    # 勾回自动 = 立刻按焦点图重算并填回（恢复默认对比度）。
    auto = QCheckBox("自动对比度")
    auto.setChecked(True)
    window.params["自动对比度"] = auto
    form2.addRow(auto)

    add_float(form2, "对比度下限", 0.0, 1e9, 1.0, decimals=1)
    add_float(form2, "对比度上限", 0.0, 1e9, 100000.0, decimals=1)

    def sync_contrast(checked, silent=False):
        window.params["对比度下限"].setEnabled(not checked)
        window.params["对比度上限"].setEnabled(not checked)
        if checked:
            _apply_auto_contrast(window, silent=silent)

    auto.toggled.connect(sync_contrast)
    sync_contrast(True, silent=True)   # 初始状态：自动开 → 输入框置灰（不刷日志）

    angle = add_float(form2, "剖面角度 (°)", -180.0, 180.0, 0.0, decimals=1)
    angle.setSingleStep(5.0)   # 步进 5°，对应 view_diffraction 的 --angle

    # 看图参数：不参与计算，只影响图怎么显示；点 [应用] 落到编辑
    # 对象（快照跟着更新）
    hint = QLabel("只看图不参与计算，点 [应用] 生效")
    hint.setStyleSheet("color: gray;")
    hint.setWordWrap(True)
    form2.addRow(hint)

    # ── 1D 显示（子分组）：曲线图自己的显示参数 ──
    # 对数纵轴：主峰与弱峰强度差几个数量级，对数刻度把弱峰"抬
    # 起来"（XRD 软件行规）。纵轴范围与对比度同套路：自动 = 按
    # 曲线 1%/99.9% 分位，取消勾选手填；自动模式输入框置灰 =
    # 只读展示正在用的区间
    sub_1d = QGroupBox("1D 显示")
    form3 = QFormLayout(sub_1d)

    log_y = QCheckBox("对数纵轴")
    window.params["对数纵轴"] = log_y
    form3.addRow(log_y)

    auto_y = QCheckBox("纵轴自动")
    auto_y.setChecked(True)
    window.params["纵轴自动"] = auto_y
    form3.addRow(auto_y)

    add_float(form3, "纵轴下限", 0.0, 1e9, 1.0, decimals=1)
    add_float(form3, "纵轴上限", 0.0, 1e9, 100000.0, decimals=1)

    # 对比归一化：叠图时每条曲线除以自己的最强峰——曝光时间/衰减
    # 不同的文件强度差很多，不归一会被强者压扁（默认开）
    cmp_norm = QCheckBox("对比归一化到最强峰")
    cmp_norm.setChecked(True)
    window.params["对比归一化"] = cmp_norm
    form3.addRow(cmp_norm)

    def sync_ylim(checked):
        window.params["纵轴下限"].setEnabled(not checked)
        window.params["纵轴上限"].setEnabled(not checked)

    auto_y.toggled.connect(sync_ylim)
    sync_ylim(True)   # 初始状态：自动开 → 输入框置灰

    form2.addRow(sub_1d)

    img_defaults = {
        "对比度下限": 1.0,
        "对比度上限": 100000.0,
        "剖面角度 (°)": 0.0,
        "对数纵轴": False,
        "纵轴自动": True,
        "对比归一化": True,
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
        window.params["对比归一化"].setChecked(img_defaults["对比归一化"])
        _log(window, "图像参数已恢复默认")

    btn_reset_img.clicked.connect(reset_image)

    # [恢复默认] + [应用] 并排：与数据参数组同款布局。
    # [应用] 把当前图像参数应用到编辑对象（见 _apply_image_params）
    btn_apply_img = QPushButton("应用")
    btn_apply_img.setObjectName("apply_image_btn")
    btn_apply_img.clicked.connect(lambda: _apply_image_params(window))

    btn_row2 = QHBoxLayout()
    btn_row2.addWidget(btn_reset_img)
    btn_row2.addWidget(btn_apply_img)
    form2.addRow(btn_row2)

    lay.addWidget(img_box)
    # 底部弹簧：坞比内容高时的多余空间全留白在底部——否则会被
    # QBoxLayout 分给顶部标题行，把它竖向拉成一条粗灰带
    lay.addStretch(1)

    dock.setWidget(content)
    window.addDockWidget(Qt.RightDockWidgetArea, dock)
    return dock


def _apply_config(window: QMainWindow, index: int) -> None:
    """把下拉框选中的配置条目应用到参数面板。

    几何值来自 config.py 注册表（标定值，不是占位默认值）。只同步
    参数坞里已有的三个输入框（像素/波长/距离）；PONI、倾斜角等其余
    几何键随完整条目一起挂在 window.config 上，留给后续图面板接线。
    """
    key = window.config_combo.itemData(index)
    cfg = CONFIGS[key]
    geom = cfg["geometry"]
    window.config_name = key
    window.config = cfg   # 完整条目（label / geometry / beam_center）
    window.config_label.setText(cfg["label"])   # 说明行随选择更新
    window.params["像素尺寸 (µm)"].setValue(geom["pixel_size_m"] * 1e6)
    window.params["波长 (Å)"].setValue(geom["wavelength_m"] * 1e10)
    window.params["初始距离 (mm)"].setValue(geom["dist_m"] * 1e3)
    _log(window, f"已加载几何配置 {key}（{cfg['label']}）")


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
    """状态行（两层之二）：左侧常驻文字（"就绪"/瞬时消息）+
    右侧常驻当前文件。用常驻 QLabel 而不是 showMessage——
    后者超时清空后状态栏会变成看不见的细条。"""
    window.status_text = QLabel("就绪")
    window.statusBar().addWidget(window.status_text)
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


def _build_view_widget(window: QMainWindow, name: str, key: str,
                       title: str) -> QWidget:
    """按视图名创建面板内容：1D = matplotlib 画布，其余暂为占位标签。

    每张面板内容独立（自己的画布/坐标轴，挂在控件上供 _draw_1d
    使用）；以后 2D/剖面/瀑布接线时在此函数里加分支。每个面板都
    装 _FocusMarker：点它即成为参数面板的编辑对象。
    """
    if name == "1D":
        fig = Figure(figsize=(5, 3), tight_layout=True)
        canvas = FigureCanvasQTAgg(fig)
        canvas.axes_1d = fig.add_subplot(111)
        widget = canvas
    else:
        placeholder = QLabel(f"{title} — 尚未接线")
        placeholder.setAlignment(Qt.AlignCenter)
        widget = placeholder
    widget.installEventFilter(_FocusMarker(window, key, title))
    return widget


def _plot_view(window: QMainWindow, name: str) -> None:
    """工具栏作图按钮的动作：对每个对号文件开面板（或复用）并计算。

    支持批量：勾选几个文件（选入/拖入即全勾），点一下视图按钮 =
    一次开出多张图。面板按「视图 + 文件条目」成对创建：同一文件
    重复点 = 刷新那张图；面板标题 = 视图_显示名（如 1D_lab6-
    00024.tif），多张图一眼分清。重复文件改名加入的条目显示名不同
    （xxx (1).tif），面板键补显示名区分，各自成图、互不当过期。
    按钮是纯动作不是开关——点一下算一下，重复点击安全；面板的
    开/关只由 × 和拖动管理。
    """
    checked = [window.file_list.item(i)
               for i in range(window.file_list.count())
               if window.file_list.item(i).checkState() == Qt.Checked]
    if not checked:
        _log(window, "没有选中的文件")
        return
    for item in checked:
        path = Path(item.data(Qt.UserRole))
        display = item.text()
        key = f"{name}|{path}"
        dock = window.plot_docks.get(key)
        if dock is not None and getattr(dock, "panel_item", None) is not item:
            # 同路径的另一条目（重复文件改名加入）→ 键补显示名区分
            key = f"{name}|{path}|{display}"
            dock = window.plot_docks.get(key)
        if dock is None:
            title = f"{name}_{display}"
            dock = QDockWidget(title, window.inner)
            dock.setObjectName(f"plot_{name}")
            dock.panel_file = path   # 面板绑定自己的文件（删文件不影响已开的面板）
            dock.panel_item = item   # 面板绑定自己的列表条目（重名条目各自成图）
            dock.panel_display = display   # 显示名（标题/日志/默认存盘名用）
            dock.figure_saved = False   # 有没有存过盘（关窗询问用）
            dock.params_snapshot = _snapshot_params(window)   # 开图时的参数快照
            dock.setWidget(_build_view_widget(window, name, key, title))
            window.plot_docks[key] = dock
            window.inner.addDockWidget(Qt.RightDockWidgetArea, dock)
            _log(window, f"打开{name}面板：{display}")
        dock.setVisible(True)
        _run_view(window, name, path, key)


# ══ 对比面板（1D 多文件叠图）════════════════════════════════
def _compare_title(displays) -> str:
    """对比面板标题：两个文件 = A vs B；更多 = A 等 N 个文件。"""
    if len(displays) == 2:
        return f"对比_{displays[0]}_vs_{displays[1]}"
    return f"对比_{displays[0]} 等 {len(displays)} 个文件"


def _redraw_compare(window: QMainWindow, key: str) -> None:
    """用面板已有的曲线数据按当前 1D 显示参数重画整张对比图。

    与 _draw_1d 同套路：坐标范围随参数（2θ 上下限 / 对数纵轴 /
    纵轴范围），只是画多条曲线 + 图例。归一化到最强峰只动显示
    数据（原始结果原样保留在 compare_data）。
    """
    dock = window.plot_docks[key]
    ax = dock.widget().axes_1d
    ax.clear()
    normalize = window.params["对比归一化"].isChecked()
    shown_curves = []   # 实际画上去的曲线（归一化是显示层处理）
    # 画图顺序 = 文件列表顺序（不随各文件算完的先后变）→ 图例顺序、
    # 颜色序号稳定
    for i, (path, display) in enumerate(dock.compare_files):
        if display not in dock.compare_data:
            continue   # 这条还没算成（本函数只在全部到齐后调用）
        tth, raw = dock.compare_data[display]
        shown = np.asarray(raw, dtype=float)
        if normalize:
            peak = float(np.nanmax(shown)) if shown.size else 0.0
            if peak > 0:
                shown = shown / peak
        shown_curves.append(shown)
        ax.plot(tth, shown, f"C{i}", lw=0.8, label=display)
    xlo = window.params["2θ 下限 (°)"].value()
    xhi = window.params["2θ 上限 (°)"].value()
    if xlo < xhi:
        ax.set_xlim(xlo, xhi)
    log_y = window.params["对数纵轴"].isChecked()
    if log_y:
        ax.set_yscale("log")
    if window.params["纵轴自动"].isChecked():
        if not shown_curves:
            pass   # 一条曲线都没算成：空图，纵轴交给 matplotlib 默认
        else:
            ylo, yhi = _auto_y_range(np.concatenate(shown_curves), log_y)
            if ylo < yhi:
                ax.set_ylim(ylo, yhi)
    else:
        ylo = window.params["纵轴下限"].value()
        yhi = window.params["纵轴上限"].value()
        if log_y:
            ylo = max(ylo, 1e-6)
        if ylo < yhi:
            ax.set_ylim(ylo, yhi)
    ax.set_xlabel("2θ (deg)")
    ax.set_ylabel("Intensity (a.u.)")
    ax.set_title(f"{dock.panel_display}: full azimuthal integration")
    if dock.compare_data:
        ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    dock.widget().draw()
    dock.figure_saved = False   # 重画 = 新内容还没存盘


def _finish_compare(window: QMainWindow, key: str) -> None:
    """对比面板全部曲线到齐：整图重画 → 成为编辑对象 → 记日志。"""
    dock = window.plot_docks[key]
    _redraw_compare(window, key)
    _set_focus(window, key, dock.panel_display)
    _log(window, f"对比完成：{len(dock.compare_data)} 条曲线")


def _run_compare(window: QMainWindow, key: str) -> None:
    """对对比面板的每个文件各起一个后台积分，结果画到同一张图。

    代次（gen）防过期：重复点 [对比] 或数据 [应用] 时旧代任务
    全部作废。快照开工前拍下（与单文件面板一致）。
    """
    dock = window.plot_docks[key]
    dock.params_snapshot = _snapshot_params(window)
    dock.compare_gen += 1
    gen = dock.compare_gen
    dock.compare_pending = len(dock.compare_files)
    dock.compare_data = {}
    dock.curve_count = 0   # 第几条曲线（颜色序号 = 图例顺序）
    geom = _collect_geometry(window)
    npt = int(window.params["输出点数"].value())
    dock.widget().axes_1d.clear()

    def finish_one(path, display, result):
        """单文件结果到位：存原始数据、画一条曲线；全齐后收尾。"""
        if dock.compare_gen != gen:
            return   # 旧代次结果，静默丢弃（整图已由新代次重画）
        tth, intensity = result
        dock.compare_data[display] = (tth, intensity)
        dock.curve_count += 1
        dock.compare_pending -= 1
        if dock.compare_pending == 0:
            _finish_compare(window, key)

    def fail_one(path, display, msg):
        if dock.compare_gen != gen:
            return
        dock.compare_pending -= 1
        _log(window, f"对比：{display} 积分失败 — {msg}")
        if dock.compare_pending == 0:
            _finish_compare(window, key)

    for path, display in dock.compare_files:
        # 默认参数绑定防闭包晚绑定（循环变量到回调执行时已走到末尾）
        def spawn_one(path=path, display=display):
            def done(window_, key_, task, result):
                finish_one(path, display, result)

            def error(msg):
                fail_one(path, display, msg)

            window.status_text.setText(f"正在积分 {path.name}…")
            _spawn(window, path, geom, npt, key,
                   on_done=done, on_error=error)

        spawn_one()
    _log(window, f"开始对比 {len(dock.compare_files)} 个文件（后台线程）")


def _plot_compare(window: QMainWindow) -> None:
    """[对比] 按钮：把勾选文件的 1D 曲线叠到同一张面板上。

    只做 1D。勾选 ≥2 个文件，每个文件后台各算各的积分，结果陆续
    画进同一张图（一个文件一条曲线，自动分色 + 图例显示名），
    全部完成后该面板成为编辑对象。同一勾选集合重复点 = 复用同一
    张面板刷新；换集合 = 新开一张。面板键 = "对比|排序后的路径串"
    （与单文件面板 "1D|路径" 并存，互不干扰）。
    """
    checked = [window.file_list.item(i)
               for i in range(window.file_list.count())
               if window.file_list.item(i).checkState() == Qt.Checked]
    if len(checked) < 2:
        _log(window, "对比至少勾选两个文件（勾上的文件叠到一张图）")
        return
    files = [(Path(item.data(Qt.UserRole)), item.text()) for item in checked]
    key = "对比|" + ",".join(sorted(str(p) for p, _ in files))
    dock = window.plot_docks.get(key)
    if dock is None:
        title = _compare_title([d for _, d in files])
        dock = QDockWidget(title, window.inner)
        dock.setObjectName("plot_compare")
        dock.panel_display = title   # 标题/日志/默认存盘名用
        dock.figure_saved = False
        dock.compare_files = files   # 面板绑定这组文件（重算用）
        dock.compare_gen = 0
        dock.params_snapshot = _snapshot_params(window)
        dock.setWidget(_build_view_widget(window, "1D", key, title))
        window.plot_docks[key] = dock
        window.inner.addDockWidget(Qt.RightDockWidgetArea, dock)
        _log(window, f"打开对比面板：{len(files)} 个文件叠一张图")
    dock.setVisible(True)
    _run_compare(window, key)


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


# ══ 保存：勾选已输出的图 → 逐个选文件名存 PNG ═══════════════
def _save_figures(window: QMainWindow) -> bool:
    """[保存] 按钮与关窗询问共用：弹窗勾选要保存的图 → 逐个选文件名存 PNG。

    返回 False = 流程被取消（关窗时应留在程序里），True = 完成。
    目前只有 1D 面板有真图（figure）；占位面板不参与。
    """
    panels = [d for d in window.plot_docks.values()
              if getattr(d.widget(), "figure", None) is not None]
    if not panels:
        _log(window, "没有已输出的图可保存")
        return True
    chosen = _choose_panels(window, panels)
    if not chosen:
        _log(window, "已取消保存")
        return False
    for dock in chosen:
        default = str(Path("outputs") / f"{dock.windowTitle()}.png")
        name, _ = QFileDialog.getSaveFileName(
            window, f"保存 {dock.windowTitle()}", default, "PNG 图片 (*.png)")
        if not name:
            continue
        if not name.lower().endswith(".png"):
            name += ".png"
        dock.widget().figure.savefig(name)
        dock.figure_saved = True
        _log(window, f"已保存 {dock.windowTitle()} → {name}")
    return True


def _choose_panels(window: QMainWindow, panels) -> list:
    """弹窗勾选要保存的面板（默认全勾）；确定 = 勾选列表，取消 = 空列表。"""
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
        return []
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
    # 拖文件进窗口任意位置 = 加进文件列表（拖放事件冒泡到顶层窗口）
    window.drop_callback = lambda paths: add_files(window, paths)

    window._status_timer = None   # _log 里的状态栏恢复计时器（懒创建）
    window.log = lambda text: _log(window, text)
    window.add_files = lambda paths: add_files(window, paths)

    # 后台任务簿：进行中的积分任务挂在这里防垃圾回收（结束回调里
    # 移除）；关窗口时逐一 discard（等待后台函数返回，防线程悬空）
    window._tasks = []
    # 每面板的最新任务：同面板连点两次时，先开的晚到会被丢弃
    window._latest_task = {}
    # 焦点面板：参数面板"编辑对象"指向的图面板（点图/计算完成时更新）
    window.focus_panel = None

    _build_center(window)
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
                   if getattr(d.widget(), "figure", None) is not None
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
