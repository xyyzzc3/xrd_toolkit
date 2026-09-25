"""左侧文件坞：文件栏（树）+ 打开文件/文件夹 + 选择工具 + 拖放 + 去重。

从 app.py 拆出来（纯搬迁）：这一块只关心"有哪些文件/产物、哪些勾着"，
与参数面板、出图、导出都无关。勾选是全局唯一的"选择表达"——出图/校准/
导出/对比/热图都读它（统一走 sources.checked_sources）。

文件栏是一棵树（用户 2026-09-24 提"每次完成一个大功能后在文件栏有一个
新的子文件夹"）：顶上「原始数据」组，下面是各阶段产物分组（「1D 产物」=
当前设置算好的；「扣背景 …」= 每次 [批量扣背景] 一组）。勾组 = 整组全选，
半勾 = 只勾了一部分。**原始数据那部分的接口沿用老列表的写法**（item(i)/
count()/addItem，见 FileTree），免得几十处读写全改一遍。

导入**不再自动打勾**（用户 2026-09-25 定）：200 张数据要自己说了算，
勾选走 [全选] / [按条件选…]（区间·间隔·名字）或点对号方块。
"""
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QDialog, QDockWidget,
    QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QMainWindow, QMenu, QMessageBox, QPushButton, QRadioButton, QSpinBox,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.services import stage_cache

from xrd_toolkit.cli import SUPPORTED_EXTS
from xrd_toolkit.gui.panel_state import _collect_geometry, _log
from xrd_toolkit.gui.plot_export import _run_export, _save_figures
from xrd_toolkit.services.data_loader import load_diffraction_image

FILE_FILTER = "衍射图像 (*.tif *.tiff *.edf *.cbf);;所有文件 (*)"

# 支持的后缀（拖放/文件夹扫描/CLI 菜单共用一张表；endswith 要 tuple）
SUPPORTED_SUFFIXES = tuple(sorted(SUPPORTED_EXTS))


# ══ 日志 / 状态行 ═══════════════════════════════════════════
def _dropped_items(event) -> list:
    """从拖放事件提取可处理的本地路径：支持类型的文件 + 目录。

    目录拖入 = 扫描其中的数据文件（与 [打开文件夹] 同一套逻辑，
    drop 时经 drop_folder 转发）。文件/目录的分流放在落点处理，
    这里只负责"光标该不该显示可放"的判定与提取——拖文件夹进来
    时光标同样要变 +，不然"显示可放、松手没反应"最糟。
    """
    items = []
    for url in event.mimeData().urls():
        if url.isLocalFile():
            p = Path(url.toLocalFile())
            if p.is_dir() or p.suffix.lower() in SUPPORTED_SUFFIXES:
                items.append(p)
    return items


# ══ 左侧：文件栏（树：原始数据 + 各阶段产物分组）════════════
GROUP_ROLE = Qt.UserRole + 3        # 组节点标记（条目没有这个槽）


class FileItem(QTreeWidgetItem):
    """文件栏条目：列号默认 0，老写法（单列列表那套）原样能用。

    树条目的 checkState()/setCheckState()/text()/data() 在 PySide6 里
    **必须**带列号，而文件栏（以及各视图、探针、测试）几十处都按
    "单列列表"的写法调它们（item.checkState() / item.text() /
    item.data(Qt.UserRole)）。这里按**参数个数**补列号：少的那个写法
    补上 0，两种都吃——老的不用全改一遍，新的想写列号也照样能写。

    为什么按个数而不是"看值是不是 None"（2026-09-25 踩过）：产物条目的
    来源三件套里"产物键"对原始文件就是 None，`setData(0, 键槽, None)`
    会被"没给列号"的判据误判成 `setData(键槽, None)` → 列号变成槽位、
    角色变成 0 = **把角色号 258 写成了显示文本**（界面上文件名变成
    "258"）。个数是无歧义的。
    """

    def checkState(self, *args):
        return super().checkState(*(args or (0,)))

    def setCheckState(self, *args):
        return super().setCheckState(*((0,) + args if len(args) == 1 else args))

    def text(self, *args):
        return super().text(*(args or (0,)))

    def setText(self, *args):
        return super().setText(*((0,) + args if len(args) == 1 else args))

    def data(self, *args):
        return super().data(*((0,) + args if len(args) == 1 else args))

    def setData(self, *args):
        return super().setData(*((0,) + args if len(args) == 2 else args))


def _make_group(text: str, tip: str = "") -> FileItem:
    """建一个组节点（阶段文件夹）：可勾（勾 = 整组全选）、加粗、带提示。"""
    node = FileItem([text])
    node.setData(0, GROUP_ROLE, True)
    node.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable
                  | Qt.ItemIsUserCheckable)
    node.setCheckState(0, Qt.Unchecked)
    node.setToolTip(0, tip or text)
    font = node.font(0)
    font.setBold(True)
    node.setFont(0, font)
    return node


def is_group(item) -> bool:
    """组节点（不是条目：组不带来源三件套）。"""
    return item is not None and bool(item.data(0, GROUP_ROLE))


def _group_state(states) -> Qt.CheckState:
    """一组子项的对号 → 组该显示的态（全勾 / 全不勾 / 半勾）。"""
    states = set(states)
    if not states or states == {Qt.Unchecked}:
        return Qt.Unchecked
    if states == {Qt.Checked}:
        return Qt.Checked
    return Qt.PartiallyChecked


class FileTree(QTreeWidget):
    """文件栏：顶上"原始数据"组，往下是各阶段的产物分组。

    为什么改成树（用户 2026-09-25）："每次完成一个大功能后在文件栏有一个
    新的子文件夹进行区分"——扣完背景的图整组勾上就能去 [对比]/[热图]；
    勾组 = 勾组里全部（半勾表示只勾了一部分）。

    **保留三个老接口** item(i) / count() / addItem()，它们都指"原始数据"
    组：文件栏的几十处读写（各视图、批量扣背景、导出、探针、测试）都是按
    "原始文件"想的，换树之后让它们全改一遍是无谓的风险。要跨组读的地方
    （出图/批量扣背景/对比/热图/导出/校准取标样）统一走 sources 模块。
    """

    def __init__(self):
        super().__init__()
        self.setHeaderHidden(True)
        self.setUniformRowHeights(True)
        self.setTextElideMode(Qt.ElideMiddle)   # 窄坞里长名字中间省略
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        # 覆盖 minimumSizeHint：QListWidget/QTreeWidget 内部写死约 270px
        # （按"能显示条目"设计），而 dock 布局只认这个 hint、不认
        # setMinimumWidth。覆盖后文件栏才能收到按钮行决定的真实最窄宽度
        self.raw_group = _make_group("原始数据", "导入的原始衍射图像")
        self.addTopLevelItem(self.raw_group)

    def minimumSizeHint(self):
        return QSize(100, 120)

    # ── 老接口：都指"原始数据"组 ──
    def item(self, i):
        """原始数据组里的第 i 个条目（老 QListWidget 的口径）。"""
        return self.raw_group.child(i)

    def count(self):
        """原始数据组里的条目数（不含各组产物）。"""
        return self.raw_group.childCount()

    def addItem(self, item):
        """加进"原始数据"组（老 QListWidget 的口径）。"""
        self.raw_group.addChild(item)

    # ── 分组 ──
    def groups(self):
        """除"原始数据"以外的全部组节点（各阶段产物）。"""
        return [self.topLevelItem(i) for i in range(self.topLevelItemCount())
                if self.topLevelItem(i) is not self.raw_group]

    def clear_groups(self):
        """摘掉全部产物组（重建前先清；原始数据组和它的条目不动）。"""
        for node in self.groups():
            self.takeTopLevelItem(self.indexOfTopLevelItem(node))


def _scan_folder(window: QMainWindow, folder) -> None:
    """扫描一个文件夹并把支持的数据文件加进列表（不递归、排序稳定）。

    [打开文件夹] 按钮与"拖文件夹进窗口"共用：整目录重加时重复
    文件只记日志不弹窗（skip_duplicates）——一摞"文件已存在"
    弹窗没有意义。
    """
    found = sorted(p for p in Path(folder).iterdir()
                   if p.suffix.lower() in SUPPORTED_EXTS)
    if not found:
        _log(window, "文件夹里没有支持的数据文件"
                     "（.tif/.tiff/.edf/.cbf）")
        return
    _log(window, f"文件夹扫描：{folder} 找到 {len(found)} 个数据文件")
    window.add_files([str(p) for p in found], skip_duplicates=True)


def _build_file_dock(window: QMainWindow) -> QDockWidget:
    """文件坞：打开（多选）/ 保存 / 删除 / 导出 + 选择工具 + 文件列表。

    选择 = 对号，三种手势：
      - 点行 = 加选：勾上这一行，其他对号不动；已勾的行再点没
        反应（不取消）；
      - 点对号方块 = 勾上/取消这一行；
      - [全选] / [全不选] / [按条件选…] = 批量（区间·间隔·名字包含）。
    背景高亮跟随最后点的那行（且必须是对号行，没对号就不高亮），
    作图按钮对所有对号文件各开一张图，删除删所有对号文件。
    itemChanged 统一刷新标签和日志；itemClicked 靠 _PressRecorder
    记着的按下状态区分方块/行体手势；批量改对号走 _set_checks（屏蔽
    信号、只刷新一次）。
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
    # 按钮两排：[打开文件][打开文件夹] 各占一半；[保存][删除]
    # [导出数据] 各占 1/3。第二排三个按钮把文件列最小宽度锁在
    # ≈ 3×80+间距（270），与参数列一致——排布改了，这个锁宽
    # 约束不变（别把第二排砍成两个按钮，文件列会收得比参数列窄）
    btn_open = QPushButton("打开文件")
    btn_save = QPushButton("保存")
    btn_delete = QPushButton("删除")
    btn_folder = QPushButton("打开文件夹")
    btn_export = QPushButton("导出数据")
    btn_save.setObjectName("save_btn")
    btn_delete.setObjectName("delete_btn")
    btn_folder.setObjectName("folder_btn")
    btn_export.setObjectName("export_btn")
    btn_open.setToolTip("选择一个或多个衍射图像加入列表")
    btn_folder.setToolTip("选一个文件夹，自动遍历其中的衍射图像并加入列表"
                          "（拖文件夹进窗口同样生效）")
    btn_export.setToolTip("把勾选文件的 1D 结果批量存成两列 txt/chi，"
                          "可顺带生成 CSV 总表")
    row1 = QHBoxLayout()
    row1.addWidget(btn_open, 1)
    row1.addWidget(btn_folder, 1)
    lay.addLayout(row1)
    row2 = QHBoxLayout()
    row2.addWidget(btn_save, 1)
    row2.addWidget(btn_delete, 1)
    row2.addWidget(btn_export, 1)
    lay.addLayout(row2)

    # 选择工具（挨着文件列，管的就是它）：导入默认不勾选，所以"选哪些"
    # 得有一组顺手的按钮——全选 / 全不选 / 按条件选（区间·间隔·名字）。
    # 三条件走弹窗而不是常驻一行控件：文件列本来就窄（≈270 px 的下限
    # 由第二排三个按钮锁住），常驻一行会把列撑宽。
    btn_all = QPushButton("全选")
    btn_none = QPushButton("全不选")
    btn_pick = QPushButton("按条件选…")
    btn_all.setObjectName("select_all_btn")
    btn_none.setObjectName("select_none_btn")
    btn_pick.setObjectName("select_pick_btn")
    btn_all.setToolTip("勾上「原始数据」里的全部文件"
                       "（各产物分组请点组名自己勾）")
    btn_none.setToolTip("取消全部对号（含各产物分组）")
    btn_pick.setToolTip("按区间（第几个到第几个）、间隔（每 N 个选 1 个）"
                        "或名字包含来勾选，可叠加，可追加；"
                        "只作用在「原始数据」上")
    row3 = QHBoxLayout()
    row3.addWidget(btn_all, 1)
    row3.addWidget(btn_none, 1)
    row3.addWidget(btn_pick, 1)
    lay.addLayout(row3)

    window.file_list = FileTree()   # 覆盖了 minimumSizeHint，可以收窄
    window._check_syncing = False   # 组↔子项联动期间别再记账（防递归）
    lay.addWidget(window.file_list)

    def on_item_changed(item, column=0):
        """对号状态变了 → 组↔子项联动 + 状态行标签 + 背景高亮；勾上记日志。

        组节点不是条目而是"整组开关"：勾组 = 勾组里全部子项；子项全勾
        组自动全勾、勾一部分组显示半勾（三态）。联动期间（_check_syncing）
        只同步不记账——勾一个 81 张的组只记一行日志，不是 81 行。
        """
        if window._check_syncing:
            return
        window._check_syncing = True
        try:
            if is_group(item):
                for i in range(item.childCount()):
                    item.child(i).setCheckState(0, item.checkState(0))
                if item.checkState(0) == Qt.Checked:
                    _log(window, f"已选中整组 {item.text(0)}"
                                 f"（{item.childCount()} 个；"
                                 "点击视图按钮开始计算）")
            else:
                parent = item.parent()
                if parent is not None and is_group(parent):
                    parent.setCheckState(0, _group_state(
                        parent.child(i).checkState(0)
                        for i in range(parent.childCount())))
                if item.checkState(0) == Qt.Checked:
                    _log(window, f"已选中 {item.text(0)}"
                                 "（点击视图按钮开始计算）")
        finally:
            window._check_syncing = False
        _sync_current_to_checks(window)
        _refresh_file_label(window)

    def on_item_clicked(item, column=0):
        """手势区分：
          - 点对号方块：Qt 已自动切换（勾上/取消），无需再动；
          - 点行：只勾上这一条（加选），其他对号不动；已勾的再点没
            反应。取消对号只能用对号方块。组节点点行 = 勾上整组。
        """
        square = (window._press_item is item
                  and item.checkState(0) != window._press_state)
        if square:
            return   # Qt 已切换，itemChanged 已同步
        if is_group(item):
            if item.checkState(0) != Qt.Checked:
                item.setCheckState(0, Qt.Checked)   # 整组勾上（点行不取消）
            return
        if item.checkState(0) == Qt.Unchecked:
            item.setCheckState(0, Qt.Checked)   # → itemChanged 同步高亮/标签/日志

    window.file_list.itemChanged.connect(on_item_changed)
    window.file_list.itemClicked.connect(on_item_clicked)
    window.file_list.setContextMenuPolicy(Qt.CustomContextMenu)
    window.file_list.customContextMenuRequested.connect(
        lambda pos: _product_menu(window, window.file_list.itemAt(pos)))
    # 记录鼠标按下时命中的条目与对号状态（区分方块点击/行体点击）
    window.file_list.viewport().installEventFilter(
        _PressRecorder(window, window.file_list))

    def open_dialog():
        paths, _ = QFileDialog.getOpenFileNames(
            window, "选择数据文件", "data", FILE_FILTER)
        if paths:
            window.add_files(paths)

    def open_folder():
        """文件夹导入 = 弹目录选择框 → _scan_folder 扫描加入
        （与拖文件夹进窗口同一套逻辑）。"""
        folder = QFileDialog.getExistingDirectory(
            window, "选择数据文件夹", "data")
        if folder:
            _scan_folder(window, folder)

    btn_folder.clicked.connect(open_folder)
    btn_export.clicked.connect(lambda: _run_export(window))

    def select_all():
        # [全选]：勾上「原始数据」整组（产物分组不自动勾——那要自己挑）
        total = window.file_list.count()
        if not total:
            _log(window, "文件列表是空的")
            return
        _set_checks(window, [(window.file_list.item(i), True)
                             for i in range(total)])
        _log(window, f"全选：{total} 个文件（「原始数据」整组）")

    def select_none():
        # [全不选]：取消全部对号（含各产物分组）
        leaves = [s.item for s in gui_sources.all_sources(window)]
        n = len(gui_sources.checked_sources(window))
        if not n:
            _log(window, "当前没有勾选的条目")
            return
        _set_checks(window, [(it, False) for it in leaves])
        _log(window, f"全不选：{n} 个条目的对号已取消")

    def select_by_condition():
        total = window.file_list.count()
        if not total:
            _log(window, "文件列表是空的")
            return
        spec = _selection_dialog_spec(window, total)
        if spec is not None:
            apply_selection(window, spec)

    def delete_selected():
        """[删除]：移除勾选的**原始数据**条目。

        产物条目不走这个按钮：它们**右键就能删**（单条或整组，见
        _product_menu）——[删除] 的口径是"勾选 = 要处理的对象"，产物
        不是"要处理的对象"而是"处理的结果"，两件事分开更好理解。
        """
        checked = checked_raw_items(window)
        if not checked:
            _log(window, "没有选中要删除的文件"
                         "（产物条目请右键删除）")
            return
        skipped = len([s for s in gui_sources.checked_sources(window)
                       if s.kind != gui_sources.RAW])
        # 屏蔽信号：移除过程中 Qt 会把当前项挪到相邻条目，别让
        # 中间状态触发登记/日志
        window.file_list.blockSignals(True)
        for it in checked:
            window.file_list.raw_group.removeChild(it)
        window.file_list.blockSignals(False)
        _sync_group_states(window)   # 批量删也是屏蔽信号做的：补组态
        _sync_current_to_checks(window)   # 高亮跟随剩余对号集合
        _refresh_file_label(window)   # 状态行跟随剩余对号集合
        # 条目没了，面板绑定的列表条目随之失效：解除引用（面板照常
        # 工作，靠 panel_file 记住自己的文件；重新加回时由 _plot_view
        # 把面板归位到新条目）。对比面板没有 panel_item，跳过。
        for dock in window.plot_docks.values():
            if getattr(dock, "panel_item", None) in checked:
                dock.panel_item = None
        tail = f"，另有 {skipped} 个产物条目没动（要删请右键）" if skipped else ""
        _log(window, f"已删除 {len(checked)} 个文件{tail}")
        refresh_product_groups(window)    # 文件没了，它的产物分组也跟着收

    btn_open.clicked.connect(open_dialog)
    btn_save.clicked.connect(lambda: _save_figures(window))
    btn_delete.clicked.connect(delete_selected)
    btn_all.clicked.connect(select_all)
    btn_none.clicked.connect(select_none)
    btn_pick.clicked.connect(select_by_condition)

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


def add_files(window: QMainWindow, paths, skip_duplicates: bool = False,
              select: bool = False) -> None:
    """把文件加进左侧列表；**默认不勾选**（用户 2026-09-25 定）。

    对号是唯一的选择表达。导入不再自动勾上——200 张数据要自己说了算，
    勾选用 [全选] / [按条件选…] / 点对号方块（select=True 保留"加进来
    就全勾"的老行为，脚本与测试用）。
    重复文件（同一路径再次加入）弹窗询问：
      - 覆盖 = 保留原条目（新条目跳过；select=True 时顺手勾上，否则
        原条目的对号原样不动——那是用户自己勾的）；
      - 改名 = 弹输入框起新名字（预填 "xxx (1).tif"，可自己改；
        输入的名字已被占用会要求换一个）再开一条（同文件两条条目）；
      - 取消 = 这次不加。
    skip_duplicates=True（文件夹导入用）时重复文件直接跳过不弹窗：
    整目录重加弹一摞"文件已存在"没有意义。
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
            if skip_duplicates:   # 文件夹导入：不弹窗，直接跳过
                _log(window, f"已跳过重复文件 {p.name}")
                continue
            choice = _ask_duplicate(window, p.name)
            if choice == "overwrite":
                if select:
                    old.setCheckState(Qt.Checked)   # 老行为：保留并勾上
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
                item = FileItem([new_name])
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                gui_sources.set_item_source(item, p)
                window.file_list.addItem(item)
                item.setCheckState(0, Qt.Checked if select else Qt.Unchecked)
                added.append(item)
                existing[p.resolve()] = item   # 同批再出现同路径时走本条目
                _log(window, f"{p.name} 已在列表中（改名加入：{new_name}）")
            else:
                _log(window, f"已跳过重复文件 {p.name}")
            continue
        item = FileItem([p.name])
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setToolTip(0, str(p))              # 全路径在悬停提示里
        gui_sources.set_item_source(item, p)    # 路径 + 来源三件套
        window.file_list.addItem(item)
        item.setCheckState(0, Qt.Checked if select else Qt.Unchecked)
        existing[p.resolve()] = item
        added.append(item)
    window.file_list.blockSignals(False)
    if added:
        window.file_list.setCurrentItem(added[-1])   # 高亮最后新条目
        tail = ("（已全选）" if select
                else "（未选中：点 [全选] 或 [按条件选…]，再点视图按钮出图）")
        _log(window, f"已添加 {len(added)} 个文件{tail}")
    _sync_group_states(window)   # 批量加是屏蔽信号做的：组态在这里补
    _sync_current_to_checks(window)
    _refresh_file_label(window)
    # 新来的文件可能有产物（同一次实验重开会话）→ 刷新各产物分组
    refresh_product_groups(window)


def _sync_current_to_checks(window: QMainWindow) -> None:
    """高亮跟随对号：当前项必须是对号条目；没对号就不高亮。

    防"看起来选中了其实没勾"的假象——比如点对号方块取消勾选时，
    Qt 会先把那行设为当前项，不纠正就会留下一行无对号的高亮。
    组节点不参与高亮（它不是一个"能出图的对象"）。
    """
    current = window.file_list.currentItem()
    if (current is not None and not is_group(current)
            and current.checkState(0) == Qt.Checked):
        return
    for src in gui_sources.checked_sources(window):
        window.file_list.setCurrentItem(src.item)
        return
    window.file_list.setCurrentItem(None)


def _refresh_file_label(window: QMainWindow) -> None:
    """状态行文件标签跟随对号集合：0 个 = 未打开 / 1 个 = 名字 /
    N 个 = 已选 N 个（其中有产物条目就点明几个，免得"文件数"对不上）。"""
    checked = gui_sources.checked_sources(window)
    odd = [s for s in checked if s.kind != gui_sources.RAW]
    if not checked:
        window.file_label.setText("未打开文件")
    elif len(checked) == 1:
        window.file_label.setText(checked[0].display)
    else:
        tail = f"（含 {len(odd)} 个产物）" if odd else ""
        window.file_label.setText(f"已选 {len(checked)} 个文件{tail}")


def _keys_of(item, kind: str = None) -> list:
    """组/条目身上挂着的产物键（组 = 组里全部子项的键）。"""
    if is_group(item):
        out = []
        for i in range(item.childCount()):
            src = gui_sources.source_of(item.child(i))
            if src is not None and src.key and (kind is None or src.kind == kind):
                out.append((src.kind, src.key))
        return out
    src = gui_sources.source_of(item)
    if src is None or not src.key:
        return []
    return [(src.kind, src.key)]


def drop_product_group(window: QMainWindow, item) -> int:
    """删掉**一组**产物（盘上的产物 + 对应的台账条目），返回删掉的份数。

    两种组：扣背景批次（一整批，`drop_batch`）与「1D 产物」（当前设置下算好
    的那些，逐键删）。菜单确认之后调；脚本/测试也可以直接调（QMenu.exec 在
    PySide6 里打不了补丁，弹菜单那一步没法在无头环境里走——所以把"删"这一
    步单独摘出来，能测的就是它）。
    """
    if item is None or not is_group(item) or item is window.file_list.raw_group:
        return 0
    batch = item.data(0, GROUP_ROLE + 1)
    if batch:
        n = stage_cache.drop_batch("bg", batch)
        tail = "（台账一并清掉）"
    else:
        n = 0
        for kind, keys in _group_keys_by_kind(item).items():
            n += stage_cache.drop_keys(kind, keys)
        tail = ""
    _log(window, f"已删除产物分组「{item.text(0)}」：{n} 份产物{tail}")
    refresh_product_groups(window)
    return n


def _group_keys_by_kind(item) -> dict:
    """组里子项的产物键，按阶段归类：{阶段: [键, ...]}。"""
    out = {}
    for kind, key in _keys_of(item):
        out.setdefault(kind, []).append(key)
    return out


def drop_product_item(window: QMainWindow, item) -> int:
    """删掉**一条**产物（单个条目，用户 2026-09-25 定：所有产物都能删）。"""
    if item is None or is_group(item):
        return 0
    src = gui_sources.source_of(item)
    if src is None or not src.key:
        return 0    # 原始数据条目：那走 [删除] 按钮，不走这里
    n = stage_cache.drop_keys(src.kind, [src.key])
    _log(window, f"已删除产物：{src.display}（{n} 份文件）")
    refresh_product_groups(window)
    return n


def _product_menu(window: QMainWindow, item) -> None:
    """右键产物分组 / 产物条目 → 弹菜单删掉它（组 = 整组，条目 = 这一条）。

    原始数据组与原始数据条目不进这个菜单：它们的删除入口是 [删除] 按钮
    （勾选 = 要处理的对象，语义不同，别混在一起）。
    """
    if item is None or item is window.file_list.raw_group:
        return
    if is_group(item):
        if not item.childCount():
            return
        what = f"删除这一组产物（{item.childCount()} 个）"
    else:
        src = gui_sources.source_of(item)
        if src is None or src.kind == gui_sources.RAW or not src.key:
            return
        what = f"删除这一条产物（{src.display}）"
    if not window.isVisible():
        return   # 窗口没显示（测试/无头）不弹模态菜单：会永远等不到人点
    menu = QMenu(window)
    act = menu.addAction(what)
    pos = window.file_list.viewport().mapToGlobal(QPoint(0, 0))
    if menu.exec(pos) is not act:
        return
    if is_group(item):
        drop_product_group(window, item)
    else:
        drop_product_item(window, item)


def refresh_product_groups(window: QMainWindow) -> None:
    """重建文件栏里的产物分组：「1D 产物」+ 各组「扣背景 …」。

    数据来源两处，各有各的道理：
      - 1D 产物**正向查**：按当前设置算键 → 看文件在不在（与"点 [1D]
        会不会命中缓存"完全同源，设置一变分组自然跟着变）；
      - 扣背景产物**读台账**：产物键里含设置哈希，反查不出来，只能靠
        [批量扣背景] 当时记的那一笔（见 services/stage_cache 的台账一节）。

    只在"有事发生"时调（导入/删除、批量扣背景、清空缓存）：每次要给列表
    里每个文件算一次指纹（读 64 KiB），200 个文件 ≈ 100 ms，定时刷新是
    白烧 CPU。台账里"产物已经没了"的条目顺手落盘清掉。
    """
    tree = window.file_list

    def add_leaf(group, raw_item, kind, key, tail):
        """往组里加一条产物条目（默认不勾）。"""
        leaf = FileItem([f"{raw_item.text(0)} · {tail}"])
        leaf.setFlags(leaf.flags() | Qt.ItemIsUserCheckable)
        leaf.setCheckState(0, Qt.Unchecked)
        gui_sources.set_item_source(leaf, raw_item.data(0, Qt.UserRole),
                                    kind, key)
        leaf.setToolTip(0, f"{raw_item.toolTip(0)}\n{group.text(0)}")
        group.addChild(leaf)
        return leaf

    # 重建期间屏蔽信号：新条目是 Unchecked，逐条会触发联动/刷新（每个
    # 条目都要重算一次组态 + 刷状态行，200 个就是 200 次白跑）
    tree.blockSignals(True)
    try:
        tree.clear_groups()
        tree.raw_group.setText(0, f"原始数据 ({tree.count()})")
        by_path = {}
        for i in range(tree.count()):
            it = tree.item(i)
            by_path[str(Path(it.data(0, Qt.UserRole)).resolve())] = it

        # ① 1D 产物：当前设置下已经算好的那些
        geom = _collect_geometry(window)
        npt = int(window.params["输出点数"].value())
        kw = dict(config=window.config_name, npt=npt,
                  tth_min=geom.get("tth_min_deg"),
                  tth_max=geom.get("tth_max_deg"))
        have = []
        for i in range(tree.count()):
            it = tree.item(i)
            key = stage_cache.key_of_1d(it.data(0, Qt.UserRole), **kw)
            if key and stage_cache.has_key("1d", key):
                have.append((it, key))
        if have:
            group = _make_group(
                f"1D 产物 ({len(have)})",
                f"当前设置下算好的 1D 曲线（几何 {window.config_name}、"
                f"{npt} 点、2θ {kw['tth_min']:g}–{kw['tth_max']:g}°）。\n"
                "整组勾上可去 [对比]/[热图]；改设置后这里会跟着变。")
            for it, key in have:
                add_leaf(group, it, gui_sources.ONED, key, "1D")
            tree.addTopLevelItem(group)

        # ② 扣背景：一次 [批量扣背景] = 一组（台账，新的在上）
        raw_batches = stage_cache.list_batches("bg")
        batches = stage_cache.list_batches("bg", prune=True)
        if sum(len(n["items"]) for n in raw_batches) != \
                sum(len(n["items"]) for n in batches):
            stage_cache.write_batches("bg", batches)   # 产物没了的条目落盘清掉
        for node in batches:
            kids = []
            for path, meta in sorted(node["items"].items()):
                raw_item = by_path.get(str(Path(path).resolve()))
                if raw_item is None or not stage_cache.has_key(
                        "bg", meta.get("key")):
                    continue    # 不在当前文件栏里 / 产物没了 → 不显示
                kids.append((raw_item, meta))
            if not kids:
                continue
            group = _make_group(
                f"{node['label']} ({len(kids)})",
                f"几何 {node.get('config')}、{node.get('npt')} 点、"
                f"2θ {node.get('tth_min'):g}–{node.get('tth_max'):g}°\n"
                "整组勾上可去 [对比]/[热图]；右键删掉这一组。")
            group.setData(0, GROUP_ROLE + 1, node["id"])   # 右键删这一组用
            for raw_item, meta in kids:
                add_leaf(group, raw_item, gui_sources.BG,
                         meta.get("key"), "扣背景")
            tree.addTopLevelItem(group)
        tree.expandAll()
    finally:
        tree.blockSignals(False)



def checked_items(window: QMainWindow) -> list:
    """当前打对号的**叶子**条目（原始数据 + 各组产物；组节点不算）。"""
    return [src.item for src in gui_sources.checked_sources(window)]


def checked_raw_items(window: QMainWindow) -> list:
    """当前打对号的原始数据条目（[删除] 与 [全选] 的口径）。"""
    return [src.item for src in gui_sources.checked_sources(window)
            if src.kind == gui_sources.RAW]


def _sync_group_states(window: QMainWindow) -> None:
    """按子项把每个组的三态重算一遍（组 = 全勾 / 半勾 / 全不勾）。

    批量改对号（导入、[全选]、[按条件选]、删除）都是**屏蔽信号**做的，
    itemChanged 的联动不会跑，所以这些地方收尾要显式补一次——否则界面上
    会出现"组上没勾、组里全是勾"这种自相矛盾的样子（2026-09-25 踩过：
    导入默认全勾时组态还停在 Unchecked，勾"整组取消"就没反应）。
    """
    tree = window.file_list
    for node in [tree.raw_group] + tree.groups():
        node.setCheckState(0, _group_state(
            node.child(i).checkState(0) for i in range(node.childCount())))


def _set_checks(window: QMainWindow, wanted: list) -> int:
    """一次性改一批对号，返回改动条数。

    wanted = [(条目, True/False), ...]——用列表而不是字典：PySide6 的
    条目类定义了 __eq__ 却没定义 __hash__（不可作字典键，2026-09-25
    踩过：报 TypeError，而槽函数里的异常只会打到后台，界面上表现为
    "点了没反应"）。要按身份去重就一律用 id()（`in` 走 __eq__，而
    __eq__ 比的是什么得看绑定实现，别赌）。屏蔽信号、改完统一刷新：
    200 个条目逐条触发 itemChanged 会记 200 行日志 + 刷 200 次状态行，
    界面直接卡住（与 add_files 批量加一个道理）。
    """
    changed = 0
    window.file_list.blockSignals(True)
    for item, want in wanted:
        state = Qt.Checked if want else Qt.Unchecked
        if item.checkState() != state:
            item.setCheckState(state)
            changed += 1
    _sync_group_states(window)   # 组态跟着子项走（屏蔽信号时不会自动跑）
    window.file_list.blockSignals(False)
    if changed:
        _sync_current_to_checks(window)
        _refresh_file_label(window)
    return changed


def _selection_matches(window: QMainWindow, spec: dict) -> list:
    """按条件挑条目（纯读；弹窗预览与真正执行共用同一套判定）。

    序号一律按**列表顺序从 1 数**（界面上看到第几个就是第几个）：
      - 区间：第 start 到第 stop 个；
      - 间隔：从第 offset 个起，每 every 个选 1 个；
      - 名称包含（可选）：不区分大小写的子串，与上面两条叠加。
    """
    text = (spec.get("text") or "").strip().lower()
    picked = []
    for i in range(window.file_list.count()):
        item = window.file_list.item(i)
        n = i + 1
        if spec.get("mode") == "stride":
            every = max(1, int(spec["every"]))
            offset = int(spec["offset"])
            if n < offset or (n - offset) % every:
                continue
        elif not (int(spec["start"]) <= n <= int(spec["stop"])):
            continue
        if text and text not in item.text().lower():
            continue
        picked.append(item)
    return picked


def apply_selection(window: QMainWindow, spec: dict) -> list:
    """执行一次"按条件选"：勾上命中的条目，返回它们。

    默认"只选这些"——没命中的一律取消对号；spec["append"]=True 则只加
    不减（跨几个区间攒一批用）。整批改完只记一行日志。
    """
    picked = _selection_matches(window, spec)
    hit = {id(it) for it in picked}
    append = bool(spec.get("append"))
    wanted = []
    for i in range(window.file_list.count()):
        item = window.file_list.item(i)
        wanted.append((item, id(item) in hit
                       or (append and item.checkState() == Qt.Checked)))
    _set_checks(window, wanted)
    if spec.get("mode") == "stride":
        what = (f"每 {int(spec['every'])} 个选 1"
                f"（从第 {int(spec['offset'])} 个起）")
    else:
        what = f"第 {int(spec['start'])}–{int(spec['stop'])} 个"
    text = (spec.get("text") or "").strip()
    if text:
        what += f" 且名字含“{text}”"
    if picked:
        _log(window, f"按条件选中 {len(picked)} 个文件（{what}"
                     + ("，追加到已有对号）" if append else "）"))
    else:
        _log(window, f"没有命中任何文件（{what}）")
    return picked


def _selection_dialog_spec(window: QMainWindow, total: int):
    """「按条件选…」弹窗：区间 / 间隔 + 名称筛选 + 追加开关，带实时预览。

    返回 spec（结构见 _selection_matches / apply_selection）或 None
    （取消）。窗口没显示（测试环境）不弹模态框、直接返回 None——与
    _ask_duplicate 同理防挂死；要测选择逻辑直接调 apply_selection。
    """
    if not window.isVisible():
        return None
    dlg = QDialog(window)
    dlg.setWindowTitle("按条件选择文件")
    form = QFormLayout(dlg)

    rb_range = QRadioButton("区间：第")
    rb_range.setObjectName("sel_range")
    sp_start = QSpinBox()
    sp_start.setObjectName("sel_start")
    sp_start.setRange(1, total)
    sp_start.setValue(1)
    sp_stop = QSpinBox()
    sp_stop.setObjectName("sel_stop")
    sp_stop.setRange(1, total)
    sp_stop.setValue(total)
    row_range = QHBoxLayout()
    row_range.addWidget(rb_range)
    row_range.addWidget(sp_start)
    row_range.addWidget(QLabel("到第"))
    row_range.addWidget(sp_stop)
    row_range.addWidget(QLabel("个"))
    row_range.addStretch(1)
    box_range = QWidget()
    box_range.setLayout(row_range)
    form.addRow(box_range)

    rb_stride = QRadioButton("间隔：每")
    rb_stride.setObjectName("sel_stride")
    sp_every = QSpinBox()
    sp_every.setObjectName("sel_every")
    sp_every.setRange(1, total)
    sp_every.setValue(2)
    sp_off = QSpinBox()
    sp_off.setObjectName("sel_offset")
    sp_off.setRange(1, total)
    sp_off.setValue(1)
    row_stride = QHBoxLayout()
    row_stride.addWidget(rb_stride)
    row_stride.addWidget(sp_every)
    row_stride.addWidget(QLabel("个选 1 个，从第"))
    row_stride.addWidget(sp_off)
    row_stride.addWidget(QLabel("个起"))
    row_stride.addStretch(1)
    box_stride = QWidget()
    box_stride.setLayout(row_stride)
    form.addRow(box_stride)

    # 互斥靠 QButtonGroup（两个单选钮各套了一层容器，不是兄弟控件，
    # Qt 默认的"同父互斥"不成立；组挂在弹窗上随它销毁）
    group = QButtonGroup(dlg)
    group.addButton(rb_range)
    group.addButton(rb_stride)
    rb_range.setChecked(True)

    edit_text = QLineEdit()
    edit_text.setObjectName("sel_text")
    edit_text.setPlaceholderText("留空 = 不筛（不区分大小写）")
    form.addRow("名称包含", edit_text)
    chk_append = QCheckBox("追加到当前选择（不取消已勾的）")
    chk_append.setObjectName("sel_append")
    form.addRow(chk_append)
    preview = QLabel()
    preview.setObjectName("sel_preview")
    form.addRow(preview)

    def current_spec():
        return {"mode": "range" if rb_range.isChecked() else "stride",
                "start": sp_start.value(), "stop": sp_stop.value(),
                "every": sp_every.value(), "offset": sp_off.value(),
                "text": edit_text.text(),
                "append": chk_append.isChecked()}

    def refresh_preview(*_):
        n = len(_selection_matches(window, current_spec()))
        preview.setText(f"预览：将选中 {n} 个文件（列表共 {total} 个）")

    for radio in (rb_range, rb_stride):
        radio.toggled.connect(refresh_preview)
    for spin in (sp_start, sp_stop, sp_every, sp_off):
        spin.valueChanged.connect(refresh_preview)
    edit_text.textChanged.connect(refresh_preview)
    refresh_preview()

    ok = QPushButton("确定")
    cancel = QPushButton("取消")
    row_btn = QHBoxLayout()
    row_btn.addWidget(ok)
    row_btn.addWidget(cancel)
    form.addRow(row_btn)
    ok.clicked.connect(dlg.accept)
    cancel.clicked.connect(dlg.reject)
    if dlg.exec() != QDialog.Accepted:
        return None
    return current_spec()


def _on_file_selected(window: QMainWindow, current, previous) -> None:
    """文件列表选中变化 → 只刷新状态行标签，不计算。

    算哪个视图由工具栏的作图按钮决定（点击 = 对每个对号文件开
    面板并计算），文件选择本身保持轻快——这也是"反应迟钝"问题
    的根源：以前每次点选都触发一次完整积分。
    """
    _refresh_file_label(window)


class _PressRecorder(QObject):
    """记录鼠标按下时命中的文件项与对号状态（装在列表视口上）。

    用途：区分"点对号方块"（Qt 在弹起时自动切换对号，itemChanged
    先到）与"点行其他位置"（对号不动）——itemClicked 据此决定手势：
    点行 = 只勾不取消（加选），方块 = 勾上/取消。
    """

    def __init__(self, window: QMainWindow, tree: QTreeWidget):
        super().__init__(window)   # 挂在窗口上，随窗口销毁
        self._tree = tree
        window._press_item = None
        window._press_state = None

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonPress:
            item = self._tree.itemAt(event.position().toPoint())
            window = self.parent()   # 不存 window 引用（同 _PanelClickTracker：防引用环）
            window._press_item = item
            window._press_state = item.checkState(0) if item else None
        return False   # 不消费事件
