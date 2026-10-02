"""左侧文件坞：文件栏（树）+ 打开文件/文件夹 + 选择工具 + 拖放 + 去重。

从 app.py 拆出来（纯搬迁）：这一块只关心"有哪些文件/产物、哪些勾着"，
与参数面板、出图、导出都无关。勾选是全局唯一的"选择表达"——出图/校准/
导出/对比/热图都读它（统一走 sources.checked_sources）。

文件栏是一棵树（用户 2026-09-24 提"每次完成一个大功能后在文件栏有一个
新的子文件夹"）：顶上「原始数据」组，下面是各阶段产物分组（「1D 产物 …」=
**一套积分设置一组**，组名写着是哪一套；「处理后 …」= 每次 [批量处理] 一组），
再往下是「打开的图」（**屏幕上开着的图**逐个列出来，双击提到最前面；校准图
不在内，那些行不给勾——用户 2026-10-01 第 5 条，见 _sync_open_figures）。
勾组 = 整组全选，两态：全勾 / 不勾（勾一部分 = 不亮，没有半勾）。**整行颜色
只表示"这个条目有图正开着"**（淡色 + 左侧小点，见 refresh_open_marks），
勾没勾只看框里的小勾——用户 2026-09-28 第 5 条定的。**原始数据那部分的接口
沿用老列表的写法**（item(i)/count()/addItem，见 FileTree），免得几十处读写
全改一遍。

导入**不再自动打勾**（用户 2026-09-25 定）：200 张数据要自己说了算，
勾选走 [全选] / [按条件选…]（区间·间隔·名字）或点对号方块。
"""
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QDialog, QDockWidget,
    QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QMainWindow, QMdiSubWindow, QMenu, QMessageBox, QPushButton, QRadioButton,
    QSpinBox, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget)

from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.services import stage_cache

from xrd_toolkit.cli import SUPPORTED_EXTS
from xrd_toolkit.gui.panel_state import _collect_geometry, _log, _set_focus
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
GROUP_BATCH_ROLE = GROUP_ROLE + 1   # 处理组的台账批次号（右键"删除这一组"用）
GROUP_TITLE_ROLE = GROUP_ROLE + 2   # 组的**纯名字**（不含折叠时才显示的
                                    # "（n/m 选中）"后缀，见 _group_title）
PANEL_KEY_ROLE = GROUP_ROLE + 3     # 「打开的图」那一组的行：存面板键
                                    # （"<视图>|<身份>"）。这些行**不给勾**
                                    # （它们不是数据，是"屏幕上正开着的东西"），
                                    # 也不带来源三件套——sources.all_sources
                                    # 因此看不见它们，出图/批量/导出/校准取样
                                    # 都不会把一张图片当成输入
# 槽号一律走上面的名字，别再写 GROUP_ROLE + n 的字面量：2026-09-28 给徽标
# 加槽时就撞过一次——新槽正好落在"批次号"那一格上，于是处理组的名字被批次
# id 顶掉（文件栏上显示成 "probe-batch"），而组态、勾选、右键删除全都正常，
# 只有名字错。这种撞车不报错，只是"名字看起来怪"，最难查。


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


def _set_group_title(node, base: str) -> None:
    """写组的**纯名字**（折叠徽标由 _refresh_group_badges 统一渲染）。"""
    node.setData(0, GROUP_TITLE_ROLE, base)
    node.setText(0, base)


def _group_title(node) -> str:
    """组的纯名字 = 不带"（n/m 选中）"徽标的那个。

    日志、悬停提示、右键菜单一律读它——`text(0)` 是**显示文本**，组折叠
    时后面挂着徽标，念出来会变成"已选中整组 1D 产物（81/81 选中）（81 个…）"。
    """
    return node.data(0, GROUP_TITLE_ROLE) or node.text(0)


def _group_counts(node) -> tuple:
    """组里 (勾了几条, 共几条)。递归数——组里再套组也数得对。

    只数**子项**的对号，不数组自身的：组态是子项算出来的派生量（见
    _group_state），信它就会把"组上暂时是空格"误报成"里面一条没勾"。
    """
    n = m = 0
    for i in range(node.childCount()):
        child = node.child(i)
        if is_group(child):
            cn, cm = _group_counts(child)
            n += cn
            m += cm
        else:
            m += 1
            if child.checkState(0) == Qt.Checked:
                n += 1
    return n, m


def _refresh_group_badges(window: QMainWindow) -> None:
    """组名后缀"（n/m 选中）"，**只在组折叠时显示**（用户 2026-09-28）。

    起因是"勾选集比想的大"那类事故（162 条 = 81 原始 + 81 处理后）：组
    折叠着看不见里面的对号，而组上又是不亮的——0 条和勾了 81 条在列表里
    长得一模一样（半勾那一态 2026-09-26 已按用户要求去掉，见 _group_state），
    所以"我想不到里面还留着一整批勾"这件事没有任何线索。徽标补上的正是
    这条线索。展开时条目的对号就在眼前，不重复写数字。

    **改文字会发 itemChanged**，而 on_item_changed 把组上的 itemChanged
    当成"组被勾了"、会把组的状态**铺给全部子项**——组此刻通常是空格，
    于是写一个徽标就会把里面的对号全清掉（静默的那种）。所以整个过程用
    _check_syncing 罩住（它正是那个槽的重入闸），旧值存下来再还原：本函数
    会被已经罩着的调用方（_set_checks）叫到，不能一把掀开。

    幂等：文本没变就不 setText，反复调没有副作用——所以"勾选变了 / 组
    重建了 / 展开折叠了"三处都放心地叫它（_sync_group_states、
    refresh_product_groups、文件坞的两条信号）。
    """
    if getattr(window, "file_list", None) is None:
        return
    prev = window._check_syncing
    window._check_syncing = True
    try:
        tree = window.file_list
        for node in [tree.raw_group] + tree.groups():
            if not _is_checkable(node):
                continue    # 「打开的图」没有"选中几条"这回事，别加徽标
            base = _group_title(node)
            if node.isExpanded():
                text = base
            else:
                n, m = _group_counts(node)
                text = f"{base}（{n}/{m} 选中）"
            if node.text(0) != text:
                node.setText(0, text)
    finally:
        window._check_syncing = prev


def _make_group(text: str, tip: str = "", checkable: bool = True) -> FileItem:
    """建一个组节点（阶段文件夹）：可勾（勾 = 整组全选）、加粗、带提示。

    名字进 GROUP_TITLE_ROLE（纯名字）；显示文本由 _refresh_group_badges
    按展开/折叠渲染（折叠时后缀"（n/m 选中）"）。**组名里不再带总数**
    （原来是"1D 产物 (81)"）：那个数字跟徽标里的分母重复，同一行两个数字
    反而分不清谁是谁。

    `checkable=False`：「打开的图」那一组用——它装的是屏幕上开着的图，
    不是数据，没有"全选"这回事（也**绝不设对号槽**：设了 Qt 就会画一个
    复选框出来）。全树的勾选逻辑（_set_checks / _sync_group_states /
    _refresh_group_badges / on_item_changed）都按这个开关跳过它。
    """
    node = FileItem([text])
    node.setData(0, GROUP_ROLE, True)
    node.setData(0, GROUP_TITLE_ROLE, text)
    flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
    if checkable:
        flags |= Qt.ItemIsUserCheckable
    node.setFlags(flags)
    if checkable:
        node.setCheckState(0, Qt.Unchecked)
    node.setToolTip(0, tip or text)
    font = node.font(0)
    font.setBold(True)
    node.setFont(0, font)
    return node


def is_group(item) -> bool:
    """组节点（不是条目：组不带来源三件套）。"""
    return item is not None and bool(item.data(0, GROUP_ROLE))


def _is_checkable(item) -> bool:
    """这个节点带不带对号槽（能不能勾）。

    「打开的图」那些行**不给勾**（它们不是数据，是屏幕上正开着的东西）：
    全树的勾选逻辑（on_item_changed / on_item_clicked / _set_checks /
    _sync_group_states / _refresh_group_badges）一律先问这一句——Qt 对
    没有 ItemIsUserCheckable 的条目**照样接受** setCheckState，画出来
    就是一个勾选框，而"打开着的图"根本不是可选的数据。
    """
    return item is not None and bool(item.flags() & Qt.ItemIsUserCheckable)


def panel_key_of(item):
    """「打开的图」那一行指向的面板键（"<视图>|<身份>"）；别的条目 None。"""
    return item.data(0, PANEL_KEY_ROLE) if item is not None else None


def _raise_panel(window: QMainWindow, key: str) -> None:
    """把「打开的图」里那一行对应的图提到最前面（子窗口 / 弹出去的独立窗口）。

    顺带把它设成**编辑对象**（`_set_focus`）：点谁就是谁——与直接点面板
    本身同一个口径（参数坞跟着显示那张图的参数）。
    """
    dock = getattr(window, "plot_docks", {}).get(key)
    if dock is None:
        return
    if isinstance(dock, QMdiSubWindow):
        window.mdi.setActiveSubWindow(dock)
        dock.raise_()
    else:                      # 弹出去的独立窗口：归系统管，先抬到最前
        dock.raise_()
        dock.activateWindow()
    _set_focus(window, key, dock.windowTitle())


OPEN_FIGURES_TITLE = "打开的图"


def _sync_open_figures(window: QMainWindow) -> None:
    """把「打开的图」那一组对齐到屏幕上真正开着的图（用户 2026-10-01 第 5 条）。

    用户原话："画图出的也放进文件区……对应的主要是现在在左边文件区看不到的
    内容，比如对比、热图、瀑布之类"。进这一组的是**开着的窗口**本身，不是
    存过盘的图片文件（图片没有台账、也重算不出来，列它们得另造一本账）：
    所以它跟着面板开关实时增减，关掉一张图那一行就没了。

    两条规矩（用户 2026-10-01）：
      - **校准图不放入**——它是校准模式的一部分，出口在坞顶那一行；
      - 这些行**不给勾**（"看的东西"不是数据，见 _is_checkable）。

    位置：紧跟「原始数据」组（index 1）。它是"我现在在看什么"的导航，
    该在最上面；产物分组照旧排在后面（`groups()[-1]` 仍是产物组）。
    行的顺序 = 开图顺序（plot_docks 是插入有序的字典）。

    调用点：refresh_open_marks（面板开/关、文件栏重建都经它）。
    位置变化只在"键的次序真的变了"时重建子行，否则就地改文字——
    重建会把用户当前选中的行、折叠状态弄丢（2026-09-27 那类投诉）。
    """
    tree = getattr(window, "file_list", None)
    if tree is None:
        return
    docks = getattr(window, "plot_docks", None) or {}
    wanted = [(key, dock.windowTitle()) for key, dock in docks.items()]
    group = next((n for n in _top_groups(tree)
                  if _group_title(n) == OPEN_FIGURES_TITLE), None)
    if not wanted:
        if group is not None:          # 一张图都不剩：整个组收走
            tree.blockSignals(True)
            try:
                tree.takeTopLevelItem(tree.indexOfTopLevelItem(group))
            finally:
                tree.blockSignals(False)
        return
    scroll = tree.verticalScrollBar().value()
    prev_syncing = window._check_syncing
    window._check_syncing = True
    tree.blockSignals(True)
    try:
        if group is None:
            group = _make_group(
                OPEN_FIGURES_TITLE,
                "屏幕上开着的图（校准图不在内）。双击可提到最前面，"
                "右键可以关掉这一张。", checkable=False)
            tree.insertTopLevelItem(1, group)
            group.setExpanded(True)
        rows = [group.child(i) for i in range(group.childCount())]
        if [row.data(0, PANEL_KEY_ROLE) for row in rows] != \
                [key for key, _title in wanted]:
            for row in rows:           # 次序/成员变了：整组重排（行数很少）
                group.removeChild(row)
            rows = []
        for i, (key, title) in enumerate(wanted):
            row = rows[i] if i < len(rows) else None
            if row is None:
                row = FileItem([title])
                row.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                row.setData(0, PANEL_KEY_ROLE, key)
                row.setIcon(0, _open_dot_icon())   # 与"正在打开"同一个记号
                group.addChild(row)
            elif row.text(0) != title:
                row.setText(0, title)
            row.setToolTip(0, f"{key}\n双击可把这张图提到最前面；"
                              f"右键可以关掉这一张（不动数据与产物）")
            row.setBackground(0, QBrush(QColor(_OPEN_ROW_BG)))
        group.setToolTip(0, f"{OPEN_FIGURES_TITLE}（{len(wanted)} 张）："
                            "屏幕上开着的图，双击提到最前面")
    finally:
        tree.blockSignals(False)
        window._check_syncing = prev_syncing
    tree.verticalScrollBar().setValue(scroll)


def _group_state(states) -> Qt.CheckState:
    """一组子项的对号 → 组该显示的态。**只有两态**：全勾 = 对号，其余
    （含只勾了一部分）= 空格。

    用户 2026-09-26 定："现在文件栏有三种状态，对号、横线、空格。横线和
    空重复了，留空格"——半勾那根横线在列表里跟空格几乎分不出来，索性去掉：
    勾了一部分 = 组不亮，选了哪几条看条目本身的对号（状态行/日志里也有
    条数）。"""
    states = set(states)
    return Qt.Checked if states == {Qt.Checked} else Qt.Unchecked


class _RowPaintDelegate(QStyledItemDelegate):
    """条目底色的画笔：只画"正在打开"，不画 Qt 的选中底色。

    用户 2026-09-28 第 5 条："文件栏背景加深代表这个图正在打开，选中未选中
    仅用框内的标志表示"。当时是靠 FileTree 上的一条样式表
    （`QTreeView::item:selected { background: transparent; }`）把 Qt 的选中
    底色压成透明的——可样式表是"对所有条目一条口径"，它连条目**自己**的
    背景刷子（refresh_open_marks 设的那层淡色）也一起盖掉了：当前那一行
    （刚点过的、或刚双击打开的那行）反而是纯白，只剩左边小点——"整行淡色"
    这个记号在最该看见它的那行偏偏没有（2026-09-30 复核逮到）。

    改到绘制这一层，两步：
      ① 交基类之前把 option 里的"选中"状态位摘掉 → Qt 的选中底色不画。
         只是"怎么画"变了：选中逻辑本身在 selection model 里，勾选、右键、
         删除、键盘操作一律照旧；
      ② 条目带背景刷子（= "正在打开"）时自己先铺一遍 → 淡色不再依赖样式表
         放行，也用不着跟任何风格博弈。
    """

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        option.state &= ~QStyle.StateFlag.State_Selected

    def paint(self, painter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)   # 背景刷子 = 从模型读的那一份
        if opt.backgroundBrush.style() != Qt.BrushStyle.NoBrush:
            painter.fillRect(opt.rect, opt.backgroundBrush)
        super().paint(painter, opt, index)  # 文字/对号/小点照常


class FileTree(QTreeWidget):
    """文件栏：顶上"原始数据"组，往下是各阶段的产物分组。

    为什么改成树（用户 2026-09-25）："每次完成一个大功能后在文件栏有一个
    新的子文件夹进行区分"——扣完背景的图整组勾上就能去 [对比]/[热图]；
    勾组 = 勾组里全部。

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
        # **选中底色取消**（用户 2026-09-28 第 5 条："文件栏背景加深代表这个
        # 图正在打开，选中未选中仅用框内的标志表示"）：改之前整行深色是 Qt 的
        # "当前行"高亮，含义只能靠猜；现在整行颜色专供"正在打开"（见
        # refresh_open_marks），勾没勾只看框里那个小勾。做法见 _RowPaintDelegate
        # （原先挂的是样式表，它会把条目自己的淡色也一起盖掉）
        self.setItemDelegate(_RowPaintDelegate(self))
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
    # 按钮：[打开…]（文件 / 文件夹收进一个下拉——用户 2026-09-27：
    # "打开文件文件夹合一"）+ [保存][删除][导出数据] 一排。后三个按钮把
    # 文件列最小宽度锁在 ≈ 3×80+间距（270），与参数列一致——别把它们
    # 砍成两个，文件列会收得比参数列窄
    btn_open = QPushButton("打开…")
    btn_save = QPushButton("保存图片…")
    btn_delete = QPushButton("删除")
    btn_export = QPushButton("导出数据")
    btn_open.setObjectName("open_btn")
    btn_save.setObjectName("save_btn")
    btn_delete.setObjectName("delete_btn")
    btn_export.setObjectName("export_btn")
    btn_open.setToolTip("打开文件（可多选）或整个文件夹")
    btn_save.setToolTip("把打开的图存成图片（先勾选要存哪几张，"
                        "再选格式与分辨率）")
    btn_export.setToolTip("把勾选条目的 1D 产物批量存成两列 txt/chi，"
                          "可顺带生成 CSV 总表")
    open_menu = QMenu(btn_open)
    act_files = open_menu.addAction("打开文件…")
    act_files.setToolTip("选择一个或多个衍射图像加入文件栏")
    act_folder = open_menu.addAction("打开文件夹…")
    act_folder.setToolTip("选一个文件夹，自动遍历其中的衍射图像并加入文件栏"
                          "（拖文件夹进窗口同样生效）")
    btn_open.setMenu(open_menu)
    window.open_files_action = act_files
    window.open_folder_action = act_folder
    row1 = QHBoxLayout()
    row1.addWidget(btn_open, 1)
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
    # 全选 / 全不选合成一个（用户 2026-09-27："全选全部不选合一"）：
    # 标签跟着状态走——全勾上时按它就是"全不选"，否则是"全选"
    btn_all = QPushButton("全选（原始数据）")
    btn_pick = QPushButton("按条件选…")
    btn_all.setObjectName("select_all_btn")   # 老名字沿用（测试/别名）
    btn_pick.setObjectName("select_pick_btn")
    # 两个方向的作用范围不同（勾只勾原始数据、清清全部），标签已经写出来；
    # tooltip 再说一遍"为什么"（用户 2026-09-28 第 3 条问过）
    btn_all.setToolTip("按下去做哪件事、作用于谁，看按钮上的字：\n"
                       "[全选（原始数据）]：勾上原始数据整组"
                       "（产物分组请点组名自己勾——勾选集是出图/批量处理的"
                       "输入，顺手全勾会让输入集合翻倍）；\n"
                       "[全不选（全部条目）]：清掉全树的勾（含各产物分组）")
    window.select_all_btn = btn_all
    btn_pick.setToolTip("按区间（第几个到第几个）、间隔（每 N 个选 1 个）"
                        "或名称包含来勾选，可叠加，可追加；"
                        "只作用在「原始数据」上")
    row3 = QHBoxLayout()
    row3.addWidget(btn_all, 1)
    row3.addWidget(btn_pick, 1)
    lay.addLayout(row3)

    window.file_list = FileTree()   # 覆盖了 minimumSizeHint，可以收窄
    window._check_syncing = False   # 组↔子项联动期间别再记账（防递归）
    lay.addWidget(window.file_list)

    def on_item_changed(item, column=0):
        """对号状态变了 → 组↔子项联动 + 状态行标签 + 背景高亮；勾上记日志。

        组节点不是条目而是"整组开关"：勾组 = 勾组里全部子项；子项全勾
        子项全勾组自动亮，否则组不亮（两态，见 _group_state）。联动期间（_check_syncing）
        只同步不记账——勾一个 81 张的组只记一行日志，不是 81 行。
        """
        if window._check_syncing:
            return
        if not _is_checkable(item):
            return      # 「打开的图」那些行/组没有对号：没有联动这回事
        window._check_syncing = True
        try:
            if is_group(item):
                for i in range(item.childCount()):
                    item.child(i).setCheckState(0, item.checkState(0))
                if item.checkState(0) == Qt.Checked:
                    _log(window, f"已勾选整组 {_group_title(item)}"
                                 f"（{item.childCount()} 项；"
                                 "点视图按钮开始计算）")
            else:
                parent = item.parent()
                if parent is not None and is_group(parent) \
                        and _is_checkable(parent):
                    parent.setCheckState(0, _group_state(
                        parent.child(i).checkState(0)
                        for i in range(parent.childCount())))
                if item.checkState(0) == Qt.Checked:
                    _log(window, f"已勾选 {item.text(0)}"
                                 "（点视图按钮开始计算）")
        finally:
            window._check_syncing = False
        _sync_current_to_checks(window)
        _refresh_file_label(window)
        _sync_select_label(window)
        _refresh_group_badges(window)

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
        # 「打开的图」那一行：点它 = 把那张图提到最前面（没有对号可点，
        # 所以"点行 = 勾上这一条"那套手势在这里没有意义）
        key = panel_key_of(item)
        if key is not None:
            _raise_panel(window, key)
            return
        if not _is_checkable(item):
            return
        if is_group(item):
            if item.checkState(0) != Qt.Checked:
                item.setCheckState(0, Qt.Checked)   # 整组勾上（点行不取消）
            return
        if item.checkState(0) == Qt.Unchecked:
            item.setCheckState(0, Qt.Checked)   # → itemChanged 同步高亮/标签/日志

    window.file_list.itemChanged.connect(on_item_changed)
    window.file_list.itemClicked.connect(on_item_clicked)
    # 展开/折叠 = 徽标消失/出现（"（n/m 选中）"只在折叠时显示）。
    # 两个信号都连：Qt 在"本来就展开/收起"时不发信号，真发不发不值得赌，
    # 刷新函数是幂等的（读 isExpanded 自己判断），多调一次没副作用
    window.file_list.itemExpanded.connect(
        lambda *_a: _refresh_group_badges(window))
    window.file_list.itemCollapsed.connect(
        lambda *_a: _refresh_group_badges(window))
    # 双击条目 = 打开这一张的 1D 图（"点开看一张"最顺手的手势；右键菜单
    # 里也有同一条，两个都留着——双击的第一次单击会顺手勾上这一条，
    # 无害；不想动勾选就用右键）
    window.file_list.itemDoubleClicked.connect(
        lambda item, _col=0: _open_entry_view(window, item))
    window.file_list.setContextMenuPolicy(Qt.CustomContextMenu)
    window.file_list.customContextMenuRequested.connect(
        lambda pos: _entry_menu(window, window.file_list.itemAt(pos)))
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

    act_files.triggered.connect(lambda _checked=False: open_dialog())
    act_folder.triggered.connect(lambda _checked=False: open_folder())
    btn_export.clicked.connect(lambda: _run_export(window))

    def select_all():
        """[全选（原始数据）] / [全不选（全部条目）]：一个按钮、两件事。

        **两个方向的范围本来就不一样**（用户 2026-09-28 第 3 条报"全选只全选
        原始数据，全不选却会包含产物"）：勾**只勾「原始数据」整组**（产物
        分组要自己点组名勾——勾选集是出图/批量处理的输入，顺手把 81 个产物
        也勾上会让输入集合悄悄翻倍，用户 2026-09-27 踩过"选中 81 出 162"）；
        清**清掉全树的对号**（清是"归零"，归零就该彻底）。
        范围的不同现在写在按钮标签上（见 _sync_select_label），不再是暗规矩。
        """
        total = window.file_list.count()
        if not total:
            _log(window, "文件栏是空的")
            return
        n = len(gui_sources.checked_sources(window))
        if _all_raw_checked() and n:
            # 已经全勾上 → 取消全部对号（含各产物分组）
            leaves = [s.item for s in gui_sources.all_sources(window)]
            _set_checks(window, [(it, False) for it in leaves])
            _log(window, f"全不选：清掉全部 {n} 项的对号"
                         "（原始数据 + 各产物分组）")
            return
        # [全选]：勾上「原始数据」整组（产物分组不自动勾——那要自己挑）
        _set_checks(window, [(window.file_list.item(i), True)
                             for i in range(total)])
        _log(window, f"全选：勾上「原始数据」整组 {total} 个文件"
                     "（各产物分组要自己点组名勾）")

    def _all_raw_checked() -> bool:
        """「原始数据」里是不是每一条都勾上了（切换按钮据此决定做哪件事）。"""
        raw = window.file_list.raw_group
        n = raw.childCount()
        return bool(n) and all(
            raw.child(i).checkState(0) == Qt.Checked for i in range(n))

    def select_by_condition():
        total = window.file_list.count()
        if not total:
            _log(window, "文件栏是空的")
            return
        spec = _selection_dialog_spec(window, total)
        if spec is not None:
            apply_selection(window, spec)

    def delete_selected():
        """[删除]：勾选的**原始数据**从列表移除，勾选的**产物**真删掉。

        与右键同一套口径（用户 2026-09-25 定："不要区分原始和处理后"）。
        唯一不可动摇的差别：原始数据**只从列表移除**，硬盘上的 tif 一个
        字节都不碰；产物是程序自己生成的东西，删就是真删（连带台账）。
        """
        raw = checked_raw_items(window)
        products = [s for s in gui_sources.checked_sources(window)
                    if s.kind != gui_sources.RAW]
        if not raw and not products:
            _log(window, "没有勾选要删除的条目")
            return
        if raw:
            _remove_from_list(window, raw)
        if products:
            n = 0
            for src in products:
                n += stage_cache.drop_keys(src.kind, [src.key])
            _log(window, f"已删除 {len(products)} 条产物（{n} 个文件）")
            refresh_product_groups(window)

    # [打开…] 的点击由下拉菜单的两个动作负责（btn_open 自己只弹菜单）
    btn_save.clicked.connect(lambda: _save_figures(window))
    btn_delete.clicked.connect(delete_selected)
    btn_all.clicked.connect(select_all)      # 全选/全不选同一个入口
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
                _log(window, f"{p.name} 已在文件栏中（保留原条目）")
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
                _log(window, f"{p.name} 已在文件栏中（改名加入：{new_name}）")
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
        # 高亮最后新条目，但**不许把列表滚过去**：一次导入几十个文件时
        # Qt 会把当前项滚进视野，列表就停在最后一批（用户 2026-09-27：
        # "导入数据后，会一下跳到数据中间位置，应该是还在顶端"）
        _set_current_keeping_scroll(window.file_list, added[-1])
        tail = ("（原始数据已勾选）" if select
                else "（未勾选：点 [全选] 或 [按条件选…]，再点视图按钮出图）")
        _log(window, f"已添加 {len(added)} 个文件{tail}")
    _sync_group_states(window)   # 批量加是屏蔽信号做的：组态在这里补
    _sync_current_to_checks(window)
    _refresh_file_label(window)
    _sync_select_label(window)    # 新条目默认不勾 → 标签可能要从"全不选"翻回"全选"
    # 新来的文件可能有产物（同一次实验重开会话）→ 刷新各产物分组
    refresh_product_groups(window)


def _set_current_keeping_scroll(tree, item) -> None:
    """设当前项，但**不许它带着列表滚**。

    Qt 的 `setCurrentItem` 会把目标行滚进视野；文件栏里的"当前项"只是一行
    高亮，没有理由让列表跟着跑（用户 2026-09-26/27 反复报"一操作就跳"、
    "导入后跳到数据中间"）。同步放回一次，再延后一帧补一次——Qt 的滚动
    有时落在下一个事件循环，只同步放回会**偶发**失手（"有概率跳"）。
    """
    bar = tree.verticalScrollBar()
    keep = bar.value()
    tree.setCurrentItem(item)
    bar.setValue(keep)
    QTimer.singleShot(0, lambda: bar.setValue(keep))


def _sync_current_to_checks(window: QMainWindow) -> None:
    """Qt 的"当前项"不许停在没勾选的条目上；停在别处就清掉当前项。

    防的是"点一行就留下一块底部/键盘焦点的痕迹"——点对号方块取消勾选时
    Qt 会先把那行设为当前项，不纠正它就留在一个"没勾的行"上。

    旧版这里叫"高亮"，因为那会儿整行深色是 Qt 的当前项底色；2026-09-28
    起**整行颜色专供"正在打开"**（见 refresh_open_marks，选中底色已在
    FileTree 的样式里取消），所以本函数现在只管 Qt 的当前项本身（键盘
    导航 / 滚动锚点还认它），不再负责任何颜色。

    旧版这里是"跳到第一个勾选条目"：那个条目通常就是列表最顶上那条文件，
    于是取消勾选、点组节点、[全选] 都会把列表拽回顶端（用户 2026-09-26
    起的反复投诉）。改成**直接清掉当前项**（`setCurrentItem(None)`）之后
    没有可跳的目标，也就没有了"同步放回/延后放回"的时序竞态——用户
    2026-09-27："没打字就跳了"，正是那种偶发。
    """
    current = window.file_list.currentItem()
    if current is None or is_group(current) \
            or current.checkState(0) == Qt.Checked:
        return      # 没高亮 / 高亮在组节点上 / 就是勾选着的条目：都不动
    window.file_list.setCurrentItem(None)


def _refresh_file_label(window: QMainWindow) -> None:
    """状态行文件标签跟随对号集合：0 个 = 未打开 / 1 个 = 名字 /
    N 个 = 已选 N 个（其中有产物条目就点明几个，免得"文件数"对不上）。"""
    checked = gui_sources.checked_sources(window)
    odd = [s for s in checked if s.kind != gui_sources.RAW]
    if not checked:
        window.file_label.setText("未打开任何文件")
    elif len(checked) == 1:
        window.file_label.setText(checked[0].display)
    else:
        tail = f"（含 {len(odd)} 条产物）" if odd else ""
        window.file_label.setText(f"已勾选 {len(checked)} 项{tail}")


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

    两种组：处理批次（一整批，`drop_batch`）与「1D 产物 …」（那**一组设置**
    下算好的那些，逐键删——按设置分组之后，删一组 = 删一套设置的结果）。
    菜单确认之后调；脚本/测试也可以直接调（QMenu.exec 在
    PySide6 里打不了补丁，弹菜单那一步没法在无头环境里走——所以把"删"这一
    步单独摘出来，能测的就是它）。
    """
    if item is None or not is_group(item) or item is window.file_list.raw_group:
        return 0
    batch = item.data(0, GROUP_BATCH_ROLE)
    if batch:
        n = stage_cache.drop_batch("bg", batch)
        tail = "（记录一并清掉）"
    else:
        n = 0
        for kind, keys in _group_keys_by_kind(item).items():
            n += stage_cache.drop_keys(kind, keys)
        tail = ""
    _log(window, f"已删除产物分组「{_group_title(item)}」：{n} 条产物{tail}")
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
    _log(window, f"已删除产物：{src.display}（{n} 个文件）")
    refresh_product_groups(window)
    return n


def _entry_menu(window: QMainWindow, item) -> None:
    """右键文件栏：**删除与导出**，一套手势、不区分原始与处理后。

    用户 2026-09-25 定："把删除和保存统一做成右键以及按键，不要区分原始和
    处理后"。映射如下：

        原始数据条目  → 从列表移除（**硬盘上的 tif 永远不动**）
        产物条目      → 删除这一条产物（真删文件 + 台账）
        产物分组      → 删除这一组产物 / 导出这一组（数据）
        空白处 / 原始数据组 → 删除所有缓存…

    两个词分工：**删除**管"不要了"，**导出**管"存出去"（图另存走面板上的
    [保存]，那是图片不是数据）。差别只在日志里说明——菜单项本身长得一样。
    """
    if not window.isVisible():
        return   # 窗口没显示（测试/无头）不弹模态菜单：会永远等不到人点
    menu = QMenu(window)
    actions = {}
    src = None if is_group(item) else gui_sources.source_of(item)
    panel_key = panel_key_of(item)
    if panel_key is not None:
        # 「打开的图」那一行：管的是**窗口**，不是数据——所以这里没有
        # 删除/导出（那些是 data 的事），只有"看"和"关"
        actions[menu.addAction("提到最前面")] = "raise_panel"
        menu.addSeparator()
        actions[menu.addAction("关闭这一张图（数据与产物都不动）")] = \
            "close_panel"
    elif item is None or item is window.file_list.raw_group:
        # 空白处 / 原始数据组：批量打开勾选的那批 + （整组）+ 清缓存。
        # 用户 2026-09-27："没有办法批量打开图，只能一个一个选"——视图按钮
        # 在 1D 上超过 24 张一张都不画，所以这里给一条没有上限的入口
        n_checked = len(gui_sources.checked_sources(window))
        if n_checked:
            actions[menu.addAction(
                f"打开勾选的 {n_checked} 张 1D 图")] = "open_checked"
        if item is window.file_list.raw_group and item.childCount():
            actions[menu.addAction(
                f"打开整组 1D 图（{item.childCount()} 张）")] = "open_group"
    elif is_group(item):
        if item.childCount():
            actions[menu.addAction(
                f"打开整组 1D 图（{item.childCount()} 张）")] = "open_group"
            menu.addSeparator()
            actions[menu.addAction(
                f"删除这一组产物（{item.childCount()} 条）")] = "drop_group"
            actions[menu.addAction("导出这一组（txt / chi / CSV）")] = \
                "export_group"
    elif src is None or src.kind == gui_sources.RAW:
        shown = f"（{src.display}）" if src is not None else ""
        tag = f" {src.display}" if src is not None else ""
        actions[menu.addAction(f"打开 1D 图{shown}")] = "open_item"
        menu.addSeparator()
        actions[menu.addAction(f"从文件栏移除{tag}"
                               f"（硬盘上的文件不动）")] = "remove_raw"
    else:
        actions[menu.addAction(f"打开 1D 图（{src.display}）")] = "open_item"
        menu.addSeparator()
        actions[menu.addAction(f"删除这一条产物（{src.display}）")] = "drop_item"
        actions[menu.addAction("导出这一条（txt / chi / CSV）")] = "export_item"
    if actions:
        menu.addSeparator()
    actions[menu.addAction("删除所有缓存…")] = "clear_cache"
    pos = window.file_list.viewport().mapToGlobal(QPoint(0, 0))
    picked = menu.exec(pos)
    what = actions.get(picked)
    if what is None:
        return
    if what == "open_checked":
        _open_checked_views(window, "1D")
    elif what == "open_item":
        _open_entry_view(window, item, explicit=True)   # 右键：原始条目也照开
    elif what == "open_group":
        _open_group_views(window, item)
    elif what == "drop_group":
        drop_product_group(window, item)
    elif what == "drop_item":
        drop_product_item(window, item)
    elif what == "remove_raw":
        _remove_from_list(window, [item])
    elif what == "export_group":
        sources = [gui_sources.source_of(item.child(i))
                   for i in range(item.childCount())]
        export_sources(window, sources)
    elif what == "export_item":
        export_sources(window, [src])
    elif what == "clear_cache":
        ask_clear_cache(window)
    elif what == "raise_panel":
        _raise_panel(window, panel_key)
    elif what == "close_panel":
        from xrd_toolkit.gui.panels import _close_panel   # 延迟：见模块说明
        _close_panel(window, panel_key)   # 与面板自己的 × 同一条路


def _open_entry_view(window: QMainWindow, item, explicit: bool = False) -> None:
    """双击条目 / 右键 [打开 1D 图]：按条目打开一张面板（两处共用）。

    走 window.open_view_source 回调（app 建窗时挂上 plot_views._open_source_view），
    避免本模块反向 import plot_views。双击时第一次单击已经按老手势处理过
    （可能顺手把这条勾上了）——不去撤销：勾上无害，撤销反而打乱用户的选择。

    **原始条目双击不出图**（用户 2026-09-27："原始数据应该双击打不开，因为
    原始数据可以出各种图"）：双击只给一句指路——原始数据要哪种图由视图按钮
    定。右键那条 `explicit=True` 照旧打开（菜单上明写着 1D，不会误解）。
    产物条目两种手势都直接打开：它们天生只有 1D 这一种。
    """
    key = panel_key_of(item)
    if key is not None:
        _raise_panel(window, key)   # 「打开的图」那些行：双击可提到最前面
        return
    opener = getattr(window, "open_view_source", None)
    if opener is None or item is None or is_group(item):
        return
    src = gui_sources.source_of(item)
    if src is None:
        return
    if src.kind == gui_sources.RAW and not explicit:
        _log(window, f"{src.display}：原始数据可以出多种图——先勾上它，再点 "
                     f"[2D] / [剖面] / [1D] / [瀑布]；只看 1D 用右键 →"
                     f"[打开 1D 图]")
        return
    opener(src, "1D")


def _open_group_views(window: QMainWindow, item) -> None:
    """右键 [打开整组 1D 图]：整组逐条打开；张数多时先问一声。

    每张 ≈15 MB（81 张 ≈1.4 GB），超过批量开图的上限（24）先弹确认——
    整组打开是显式动作，确认一下比默默吃内存好。
    """
    from xrd_toolkit.gui.plot_views import MAX_PANELS_PER_BATCH
    sources = [gui_sources.source_of(item.child(i))
               for i in range(item.childCount())]
    sources = [s for s in sources if s is not None]
    if not sources:
        _log(window, "这一组里没有可打开的条目")
        return
    if len(sources) > MAX_PANELS_PER_BATCH and window.isVisible():
        if not _confirm_open_many(
                window, "打开整组 1D 图",
                f"要打开 {len(sources)} 张 1D 图吗？每张约占 15 MB 内存"
                f"（合计约 {len(sources) * 15} MB），开完会占满面板区。"):
            return
    opener = getattr(window, "open_view_group", None)
    if opener is not None:
        opener(sources, "1D")


def _confirm_open_many(window: QMainWindow, title: str, text: str) -> bool:
    """>24 张批量开图前的确认框：[打开] / [取消]（回车默认 [取消]）。

    单独抽成一个函数是为了让测试能拦住它：这里直接 exec() 一个模态框，
    离屏测试里没人去点它就会把测试挂死（2026-10-02 踩到：测试 mock 的
    是旧的 QMessageBox.question，拦不住新写法）。
    """
    box = QMessageBox(window)
    box.setWindowTitle(title)
    box.setText(text)
    btn_open = box.addButton("打开", QMessageBox.AcceptRole)
    btn_cancel = box.addButton("取消", QMessageBox.RejectRole)
    box.setDefaultButton(btn_cancel)
    box.exec()
    return box.clickedButton() is btn_open


def _open_checked_views(window: QMainWindow, name: str = "1D") -> None:
    """右键 [打开勾选的 N 张 1D 图]：把勾选的条目逐条打开。

    与视图按钮的唯一区别：**没有 24 张上限**，超过先弹确认（每张 ≈15 MB）。
    用户 2026-09-27："没有办法批量打开图，只能一个一个选"——视图按钮在 1D
    上超过 24 张是**一张都不画**的（防爆图），所以那批人没有别的入口；这条
    右键给他们一条明确的、带确认的路。
    """
    sources = gui_sources.checked_sources(window)
    if not sources:
        _log(window, "还没有勾选任何条目")
        return
    from xrd_toolkit.gui.plot_views import MAX_PANELS_PER_BATCH
    if len(sources) > MAX_PANELS_PER_BATCH and window.isVisible():
        if not _confirm_open_many(
                window, f"打开勾选的 {len(sources)} 张图",
                f"要打开 {len(sources)} 张 {name} 图吗？每张约占 15 MB 内存"
                f"（合计约 {len(sources) * 15} MB），开完会占满面板区。"):
            return
    opener = getattr(window, "open_view_group", None)
    if opener is not None:
        opener(sources, name)


def export_sources(window: QMainWindow, sources) -> None:
    """右键"导出这一条/这一组"：只导出给定来源（不动勾选状态）。

    延迟导入 plot_export：它 import 本模块（打开文件/保存/导出按钮），
    模块级互相 import 会成环。
    """
    from xrd_toolkit.gui.plot_export import _run_export
    _run_export(window, sources=sources)


def _remove_from_list(window: QMainWindow, items) -> None:
    """把条目从文件栏移除（只动列表，硬盘上的文件一个字节都不碰）。"""
    items = [it for it in items if it is not None and not is_group(it)]
    if not items:
        return
    window.file_list.blockSignals(True)
    for it in items:
        window.file_list.raw_group.removeChild(it)
    window.file_list.blockSignals(False)
    for dock in window.plot_docks.values():
        if getattr(dock, "panel_item", None) in items:
            dock.panel_item = None
    _sync_group_states(window)
    _sync_current_to_checks(window)
    _refresh_file_label(window)
    refresh_product_groups(window)
    _log(window, f"已从文件栏移除 {len(items)} 个文件（硬盘上的文件未改动）")


def ask_clear_cache(window: QMainWindow) -> None:
    """「删除所有缓存…」：二次确认后再清（菜单里点错比按钮上点错更容易）。

    没显示的窗口（测试/无头）不弹模态框、直接清——与 _ask_duplicate 同理。
    """
    from xrd_toolkit.services import stage_cache
    info = stage_cache.describe()
    if not info["files"]:
        _log(window, "缓存本来就是空的（还没有生成过产物）")
        return
    n_prod = int(info["files"])
    if window.isVisible():
        box = QMessageBox(window)
        box.setWindowTitle("删除所有缓存")
        box.setText(f"要删掉全部 {n_prod} 个缓存文件吗"
                    f"（约 {info['bytes'] / 1e6:.1f} MB）？")
        box.setInformativeText("删掉的是处理产物与 1D 产物（缓存目录 "
                               "outputs/_stage）。"
                               "下次出图会重新积分——只慢一点，不会算错。")
        ok = box.addButton("删除", QMessageBox.DestructiveRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not ok:
            _log(window, "已取消删除缓存")
            return
    cleared = stage_cache.clear()
    window.refresh_groups()
    _log(window, f"已删除所有缓存：{cleared} 个缓存文件")


def _oned_sig(meta, lo, hi) -> tuple:
    """一套积分设置的指纹 = 1D 产物分组的身份。

    取的就是 1D 缓存键里那些**人看得见**的因子（几何条目 / 点数 / 2θ 范围）
    加上积分实现版本——同指纹的产物是"同一套设置下算出来的同一批"，正是
    用户 2026-09-28 第 4 条要的那种分组（"同一批原始数据出两组 theta 不同的
    1d 图会掺在一起"）。文件指纹不在其中：它是"哪张图"，不是"哪套设置"。
    """
    return (str(meta.get("config") or ""), int(meta.get("npt") or 0),
            round(float(lo), 6), round(float(hi), 6),
            int(meta.get("engine") or 0))


def _oned_sig_is_current(sig, cur) -> bool:
    """这套设置是不是坞顶**当前**那一套（组名标「当前设置」+ 排最前）。"""
    return (sig[0] == str(cur["config"]) and sig[1] == int(cur["npt"])
            and abs(sig[2] - cur["tth_min"]) <= 0.01
            and abs(sig[3] - cur["tth_max"]) <= 0.01)


def _fmt_deg(x) -> str:
    """2θ 端点给人看的写法：「1.001」「7.999」「12.5」（三位小数、去尾零）。

    为什么要它：范围是从**曲线端点**读的（元数据里老产物没记范围，曲线永远
    知道自己在哪儿），直接 `:g` 会打出「1.00117–7.99883°」这种全精度噪声
    ——真数据探针里一眼就看见它了。三位小数足够区分实际会出现的范围差；
    万一两组因此重名，文件栏那边还有"· 第 N 组"尾注兜底。
    """
    return f"{float(x):.3f}".rstrip("0").rstrip(".")


def _oned_group_title(sig, cur) -> str:
    """1D 产物组的组名：把"这一组是按什么算的"写在名字里。

    例子：「1D 产物 2θ 1.001–7.999°（当前设置）」「1D 产物 2θ 3–12° ·
    1000 点 · 几何 lmfp2_lab6」。与当前设置一致的部分不重复写（省宽度），
    不一致的才补出来——一眼就能分清哪一组是刚才那两下算出来的。

    "1D 产物"这个家族名留在最前面（徽标、右键菜单、日志都按它认这一族）。
    """
    config, npt_, lo, hi, engine = sig
    bits = [f"2θ {_fmt_deg(lo)}–{_fmt_deg(hi)}°"]
    if npt_ != int(cur["npt"]):
        bits.append(f"{npt_} 点")
    if config and config != str(cur["config"]):
        bits.append(f"几何 {config}")
    if engine != stage_cache.INTEGRATION_VERSION:
        bits.append("旧积分版本")
    title = "1D 产物 " + " · ".join(bits)
    if _oned_sig_is_current(sig, cur):
        title += "（当前设置）"
    return title


def _oned_group_tip(sig, cur) -> str:
    """1D 产物组的悬停提示：这一组是什么、与当前设置差在哪儿。"""
    config, npt_, lo, hi, _engine = sig
    txt = (f"按这套设置算好的 1D 产物：几何 {config or '—'}、{npt_} 点、"
           f"2θ {_fmt_deg(lo)}–{_fmt_deg(hi)}°。\n整组勾上可去 [对比] / [热图]；"
           "点开读的就是这一份，不重算。")
    if not _oned_sig_is_current(sig, cur):
        txt += (f"\n（当前设置：几何 {cur['config']}、{cur['npt']} 点、"
                f"2θ {_fmt_deg(cur['tth_min'])}–{_fmt_deg(cur['tth_max'])}°"
                f"——要按当前设置再算一批，勾上文件点 [1D]）")
    return txt


def _group_key(item, tree):
    """组节点的稳定身份（重建前后认人用）。

    重建前后组节点是**新对象**，只能靠数据槽认人：处理组用台账批次号
    （GROUP_BATCH_ROLE，右键"删除这一组"用的也是它）；原始数据组与 1D
    产物组没有批次号，各给一个固定名——**不能都退回同一个名字**，否则
    "收起原始数据"会被误判成"收起 1D 产物"，把好端端展开着的组收起来。
    """
    if item is tree.raw_group:
        return ("raw",)
    batch = item.data(0, GROUP_BATCH_ROLE)
    return ("bg", str(batch)) if batch else ("1d",)


def _top_groups(tree) -> list:
    """全部顶层组节点（含"原始数据"组；tree.groups() 是不含它的）。"""
    return [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]


def _tree_state(window: QMainWindow) -> dict:
    """重建前记下"用户在哪儿"：滚动位置 / 收起的组 / 勾着的产物条目。

    为什么非要记：`refresh_product_groups` 是"clear_groups → 重新 add →
    expandAll"三步，这三样全都会丢，列表被弹回最顶端——用户 2026-09-27：
    "文件里面选取文字，或者不选时，只要是操作，包括画图现在会自动跳到最
    顶端"。产物条目的勾选也一起丢（重建的叶子硬编码成不勾），而勾选正是
    "拿哪几条去 [对比]/[热图]"的表达，中途按一次 [1D] 就没了。

    原始数据组不参与重建（clear_groups 不碰它），所以它的勾选天然还在，
    这里只管产物条目。
    """
    tree = window.file_list
    out = {
        "scroll": tree.verticalScrollBar().value(),
        "collapsed": {_group_key(it, tree) for it in _top_groups(tree)
                      if not it.isExpanded()},
        "checked": {(s.kind, s.key) for s in gui_sources.all_sources(window)
                    if s.kind != gui_sources.RAW
                    and s.item.checkState(0) == Qt.Checked},
        "current": None,
    }
    cur = tree.currentItem()
    if cur is not None and not is_group(cur):
        src = gui_sources.source_of(cur)
        out["current"] = (src.kind, src.key if src.kind != gui_sources.RAW
                          else src.path)
    return out


def _restore_tree_state(window: QMainWindow, keep: dict,
                        restore_checks: bool = True) -> None:
    """把 `_tree_state` 记下的东西放回去（重建之后调）。

    顺序有讲究：先恢复展开/勾选（都在信号屏蔽期间做，免得每条都触发一次
    联动 + 一行日志），再恢复当前项与滚动位置——`setCurrentItem` 自己会
    滚动，所以它必须在最后。

    `restore_checks=False`：**只不还勾选**，滚动/收起/当前项照还。
    什么时候要这样：重建时冒出了新产物 → 上一轮的勾选已经按规矩清掉了
    （见 _uncheck_everything），不能又叫 `keep` 把它们放回来。用户在
    哪儿那几样照旧要还——那是 2026-09-27"一操作就跳回顶端"投诉的另一半。
    """
    tree = window.file_list
    for node in _top_groups(tree):
        if _group_key(node, tree) in keep["collapsed"]:
            node.setExpanded(False)
    if restore_checks and keep["checked"]:
        for src in gui_sources.all_sources(window):
            if src.kind == gui_sources.RAW:
                continue        # 原始数据组的勾选从来没被动过
            if (src.kind, src.key) in keep["checked"]:
                src.item.setCheckState(0, Qt.Checked)
        _sync_group_states(window)   # 组态跟着子项走（屏蔽信号时不会自动跑）
    # 先放滚动位置，再放高亮：放高亮那一步自己会保滚动（见 _set_current_*）
    tree.verticalScrollBar().setValue(keep["scroll"])
    if keep["current"] is not None:
        for src in gui_sources.all_sources(window):
            same = ((src.kind, src.key) if src.kind != gui_sources.RAW
                    else (src.kind, src.path))
            if same == keep["current"]:
                _set_current_keeping_scroll(tree, src.item)
                break
    if not restore_checks:
        # 勾选刚被清过：当前项不许停在没勾的行上（同 _sync_current_to_checks）
        _sync_current_to_checks(window)


def _product_keys(window: QMainWindow) -> set:
    """文件栏里全部产物条目的身份 `(kind, key)`（原始数据不算）。"""
    return {(s.kind, s.key) for s in gui_sources.all_sources(window)
            if s.kind != gui_sources.RAW}


def _product_keys_on_disk() -> set:
    """**盘上**现有的全部产物身份 `(kind, key)`——不管它在不在文件栏里。

    判"冒出新产物"要拿它当地基，不能拿"文件栏里现在有什么"：导入一个
    以前算过的文件时，它的产物本来就在盘上、只是**刚被列出来**，那不是
    新产物——拿文件栏当地基会把这种情况误判成新产物，把用户导入后就勾好
    的那批对号当场清掉（2026-10-01 探针抓到：点 [1D] 只等到"没有选中的
    文件"）。

    单次"扫一遍"也不够：产物是**先落盘、后重建**的（算完 → store_1d →
    refresh），所以"重建时现扫"必然把刚写的键也算进去。真正的基线是
    **上一次重建时看到的盘面**（见 refresh_product_groups 里的
    `_products_seen`）。
    """
    keys = {("1d", key)
            for key, _meta, _lo, _hi in stage_cache.list_products("1d")}
    for node in stage_cache.list_batches("bg"):
        for _path, meta in node["items"].items():
            # 台账里"产物已经没了"的条目也在这里：它照样占着这把键，
            # 免得到时候"重新出现"被当成新产物（prune 在下面照常做）
            keys.add(("bg", meta.get("key")))
    return keys


def _uncheck_everything(window: QMainWindow) -> int:
    """清掉全树的勾（原始数据组 + 各产物组），返回清了几条。

    用户 2026-10-01 第 8 条（方案"甲"）：**文件栏出现新产物 = 上一轮的活
    干完了**，对号该清零重来，而不是继续挂着冒充"这一轮的选择"。

    调用方负责信号屏蔽（重建期间本来就在屏蔽中，见 refresh_product_groups）
    ——这里只改状态，标签/按钮文字由调用方收尾时统一刷。
    """
    changed = 0
    for src in gui_sources.all_sources(window):
        if src.item.checkState(0) != Qt.Unchecked:
            src.item.setCheckState(0, Qt.Unchecked)
            changed += 1
    _sync_group_states(window)   # 组态跟着子项走（屏蔽信号时不会自动跑）
    return changed


def refresh_product_groups(window: QMainWindow) -> None:
    """重建文件栏里的产物分组：各组「1D 产物 …」+ 各组「处理后 …」。

    数据来源两处，各有各的道理：
      - 1D 产物**扫盘 + 按积分设置分组**：磁盘上有什么就显示什么（不许
        因为改了设置就凭空消失），同指纹的归一组、组名写着是哪一套设置；
      - 处理产物**读台账**：产物键里含设置哈希，反查不出来，只能靠
        [批量处理] 当时记的那一笔（见 services/stage_cache 的台账一节）。

    只在"有事发生"时调（导入/删除、批量处理、清空缓存）：每次要给列表
    里每个文件算一次指纹（读 64 KiB），200 个文件 ≈ 100 ms，定时刷新是
    白烧 CPU。台账里"产物已经没了"的条目顺手落盘清掉。

    **重建不许动用户在哪儿**：滚动位置、收起的组、勾着的产物条目、当前
    高亮，重建前后必须一样（见 _tree_state / _restore_tree_state）。
    """
    tree = window.file_list
    keep = _tree_state(window)     # 重建不许改"用户在哪儿"（见其 docstring）
    # "冒出新产物"的基线 = **上一次重建时看到的盘面**（不是这次的盘面：
    # 产物先落盘、后重建，现扫必然把刚写的也算进去；也不是文件栏里已有的：
    # 那会把"导入以前算过的文件"误判成新产物。见 _product_keys_on_disk）
    now_on_disk = _product_keys_on_disk()
    seen_before = getattr(window, "_products_seen", None)
    newborn = set()
    if seen_before is not None:
        newborn = now_on_disk - seen_before
    window._products_seen = now_on_disk

    def add_leaf(group, raw_item, kind, key, tail):
        """往组里加一条产物条目（默认不勾）。"""
        leaf = FileItem([f"{raw_item.text(0)} · {tail}"])
        leaf.setFlags(leaf.flags() | Qt.ItemIsUserCheckable)
        leaf.setCheckState(0, Qt.Unchecked)
        gui_sources.set_item_source(leaf, raw_item.data(0, Qt.UserRole),
                                    kind, key)
        leaf.setToolTip(0, f"{raw_item.toolTip(0)}\n{_group_title(group)}")
        group.addChild(leaf)
        return leaf

    # 重建期间屏蔽信号：新条目是 Unchecked，逐条会触发联动/刷新（每个
    # 条目都要重算一次组态 + 刷状态行，200 个就是 200 次白跑）
    tree.blockSignals(True)
    try:
        tree.clear_groups()
        _set_group_title(tree.raw_group, "原始数据")
        by_path = {}
        for i in range(tree.count()):
            it = tree.item(i)
            by_path[str(Path(it.data(0, Qt.UserRole)).resolve())] = it

        # ① 1D 产物：**扫盘**列出（磁盘上有什么就显示什么），**按积分设置分组**
        #
        # 旧版按"当前设置"正查键，于是**改了 2θ 范围之后那一组会整个空掉**
        # （键里含范围）——用户 2026-09-28 报的"处理后的把 1D 的替换了"
        # 就是这个：其实一份都没少，只是不显示。规矩定成"缓存里有什么就
        # 显示什么，删不删由我自己决定"，与设置不一致的**在名字里标出来**。
        #
        # **分组 = 一套积分设置**（2026-09-28 用户第 4 条："同一批原始数据出
        # 两组 theta 不同的 1d 图会掺在一起，应该有分组区分"）：改之前全库
        # 只有一个"1D 产物"组，靠行尾 `1D（2θ 1–8°）` 区分，而且那截尾巴是
        # **相对当前设置**算的——把坞顶的 2θ 一改，尾巴会从 A 行跳到 B 行，
        # 看着像两组数据换了身份。现在与处理产物（一批一组）同一个形状：
        # 一组一套设置，组名就写着是哪一套。
        geom = _collect_geometry(window)
        npt = int(window.params["输出点数"].value())
        cur = {"tth_min": float(geom.get("tth_min_deg") or 0.0),
               "tth_max": float(geom.get("tth_max_deg") or 0.0),
               "config": window.config_name, "npt": npt}
        # 认人：**全路径优先**（元数据里有 path），老产物只有文件名 →
        # 退回按名字；两个目录里同名的文件因此不会挂错（2026-09-28 踩到）
        by_path_name, by_name = {}, {}
        for i in range(tree.count()):
            it = tree.item(i)
            p = Path(it.data(0, Qt.UserRole))
            by_path_name.setdefault(str(p), it)
            by_name.setdefault(p.name, it)
        groups_1d = {}          # 设置指纹 → [(条目, 键, 元数据, lo, hi), ...]
        for key, meta, lo, hi in stage_cache.list_products("1d"):
            it = by_path_name.get(str(meta.get("path") or ""))
            if it is None:
                it = by_name.get(str(meta.get("source") or ""))
            if it is None:
                continue        # 不在当前文件栏里 → 不显示
            groups_1d.setdefault(_oned_sig(meta, lo, hi), []).append(
                (it, key, meta, lo, hi))
        for sig in sorted(groups_1d, key=lambda s: (_oned_sig_is_current(s, cur),
                                                    s[2], s[3])):
            rows = groups_1d[sig]
            # 组内按**文件栏的行序**排（同一文件的两份产物都在时也能对上眼）
            order = {id(tree.item(i)): i for i in range(tree.count())}
            rows.sort(key=lambda r: order.get(id(r[0]), 10 ** 9))
            group = _make_group(_oned_group_title(sig, cur),
                                _oned_group_tip(sig, cur))
            for it, key, meta, lo, hi in rows:
                add_leaf(group, it, gui_sources.ONED, key, "1D")
            tree.addTopLevelItem(group)

        # ② 处理：一次 [批量处理] = 一组（台账，新的在上）
        raw_batches = stage_cache.list_batches("bg")
        batches = stage_cache.list_batches("bg", prune=True)
        if sum(len(n["items"]) for n in raw_batches) != \
                sum(len(n["items"]) for n in batches):
            stage_cache.write_batches("bg", batches)   # 产物没了的条目落盘清掉
        stale = 0
        seen_labels = {}      # 组名 → 已出现几次（重名时加尾注，见下）
        for node in batches:
            kids = []
            for path, meta in sorted(node["items"].items()):
                raw_item = by_path.get(str(Path(path).resolve()))
                if raw_item is None or not stage_cache.has_key(
                        "bg", meta.get("key")):
                    continue    # 不在当前文件栏里 / 产物没了 → 不显示
                # 旧背景算法算的那批**照样列出来**，只在名字里标"不可信"
                # ——用户 2026-09-28："缓存里有什么就显示什么，删不删由我
                # 自己决定"。上一版是直接不显示，与这条规矩冲突（他刚被
                # "东西怎么会不见"咬过一次）。
                is_stale = stage_cache.bg_product_stale(meta.get("key"))
                if is_stale:
                    stale += 1
                kids.append((raw_item, meta, is_stale))
            if not kids:
                continue
            # 2θ 范围可能没记（老台账、或批处理时没设范围）→ 别让
            # f-string 拿 None 去格式化：那会**在重建时抛异常**，整个文件栏
            # 建不起来（测试用最小台账记一批就复现了）
            span = ("2θ 范围未记录"
                    if node.get("tth_min") is None
                    or node.get("tth_max") is None
                    else f"2θ {_fmt_deg(node['tth_min'])}–"
                         f"{_fmt_deg(node['tth_max'])}°")
            # 台账那套设置与**当前**设置不同时，把当前值也写进悬停提示
            # （跟 1D 产物一样的道理：一眼看得出这批是按什么算的）
            now = f"2θ {_fmt_deg(cur['tth_min'])}–{_fmt_deg(cur['tth_max'])}°"
            differs = (node.get("config") != cur["config"]
                       or (node.get("tth_min") is not None
                           and abs(float(node["tth_min"]) - cur["tth_min"]) > 0.01)
                       or (node.get("tth_max") is not None
                           and abs(float(node["tth_max"]) - cur["tth_max"]) > 0.01))
            more = f"\n（当前设置：几何 {cur['config']}、{cur['npt']} 点、{now}）" \
                if differs else ""
            # 组名重了要能分开（2026-09-28 探针逮到）：批标签只到"分钟"，
            # 同一分钟里跑了两批**可见参数相同、锚点强度不同**的（哈希因此
            # 不同 = 两批），名字就一模一样——两个长得一样的组里各有一条同一
            # 文件的条目，删了哪一个都看不出来。重名的第二组起加尾注。
            label = str(node["label"])
            # 旧缓存里存的是「处理后 …」（2026-10-02 改名前的批次）：
            # 显示时归一到现名——同一批数据的组名不该新旧两套并存
            if label.startswith("处理后"):
                label = "处理产物" + label[len("处理后"):]
            seen_labels[label] = seen_labels.get(label, 0) + 1
            if seen_labels[label] > 1:
                label = f"{label} · 第 {seen_labels[label]} 组"
            group = _make_group(
                label,
                f"几何 {node.get('config')}、{node.get('npt')} 点、{span}\n"
                "整组勾上可去 [对比] / [热图]；右键删掉这一组。" + more)
            group.setData(0, GROUP_BATCH_ROLE, node["id"])   # 右键删这一组用
            for raw_item, meta, is_stale in kids:
                tail = "处理产物（旧算法，不可信）" if is_stale else "处理产物"
                leaf = add_leaf(group, raw_item, gui_sources.BG,
                                meta.get("key"), tail)
                if is_stale and leaf is not None:
                    leaf.setToolTip(
                        0, f"{leaf.toolTip(0)}\n这一份是按<b>旧的背景算法</b>"
                           "算的（曲线不可信）；要清掉就右键删这一条/这一组，"
                           "或[删除所有缓存…]")
            tree.addTopLevelItem(group)
        tree.expandAll()
        if stale:
            # 列出来、但标"不可信"：产物文件是用户的数据，删不删他说了算
            _log(window, f"文件栏里有 {stale} 条旧背景算法算的处理产物"
                         "（名字里标着“旧算法，不可信”）：扣背景的算法已"
                         "升级，那批曲线不再可信——要清掉就右键删那一组，"
                         "或[删除所有缓存…]，然后重新批量处理")
        # 冒出新产物（基线见上面那段）= 上一轮的工作结束了：勾选
        # （出图/批量处理的输入）清零重来（用户 2026-10-01 第 8 条，
        # 方案"甲"）。清掉之后不能再叫 keep 把旧对号放回来——滚动/
        # 收起/当前项照旧要还（那是 2026-09-27 那半条）。
        if newborn:
            _uncheck_everything(window)
        # 恢复展开/勾选/当前项/滚动位置：在信号屏蔽期间做（否则每个被恢复
        # 勾选的条目都要触发一次联动 + 一行日志，200 个条目就是 200 次）
        _restore_tree_state(window, keep, restore_checks=not newborn)
    finally:
        tree.blockSignals(False)
    if newborn:
        # 勾选"自己没了"必须说清楚去哪了，否则就是又一个"东西怎么会不见"
        _log(window, f"文件栏出现 {len(newborn)} 条新产物：上一轮的勾选已"
                     "清空（勾选集是出图/批量处理的输入，请重新勾）")
    # 恢复出来的勾选集合可能和进来时不一样（有产物的条目没了）→ 按钮上的
    # 数字与状态行跟着刷新（只改文字，不碰树）
    _refresh_file_label(window)
    _sync_select_label(window)
    _refresh_group_badges(window)   # 重建出新组了：折叠着的那些要带上数字
    refresh_open_marks(window)      # 重建出来的是新对象：标记要重新打一遍


# 「正在打开」的两种记号（用户 2026-09-28 第 5 条）：整行淡色 + 左侧小点
_OPEN_ROW_BG = "#e8f0fb"
_open_dot = None        # 懒建（建 QPixmap 要先有 QApplication）


def _open_dot_icon() -> QIcon:
    """左侧那个小实心点：深蓝色 8 px 圆，居中画在 10×10 的透明底上。

    为什么要它：整行淡色在浅色主题下很淡（远看像隔行着色），旁边再给一个
    "实心点 = 有面板活着"的硬记号，扫一眼就能数出开着几张。
    """
    global _open_dot
    if _open_dot is None:
        pm = QPixmap(10, 10)
        pm.fill(QColor(0, 0, 0, 0))          # 透明
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(QBrush(QColor("#1a6fd4")))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(1, 1, 8, 8)
        painter.end()
        _open_dot = QIcon(pm)
    return _open_dot


def refresh_open_marks(window: QMainWindow) -> None:
    """把"这个条目有活着的面板"画出来：整行淡色 + 左侧小点。

    用户 2026-09-28 第 5 条："文件栏背景加深代表这个图正在打开，选中未选中
    仅用框内的标志表示"。改之前整行深色是 Qt 的"当前行"高亮（而且被限制成
    "只能在勾选着的行上"，见 _sync_current_to_checks），而**哪张图开着**在
    文件栏里根本看不出来——`dock.panel_item` 只被用来做删除时的清理。

    现在反过来：整行颜色专供"正在打开"，勾没勾只看框里的小勾（Qt 的选中
    底色已在 FileTree.__init__ 的样式里取消）。

    认哪一条开着：面板键是 `"<视图>|<条目身份>"`（1D/2D/剖面/瀑布都按条目
    开），身份 = sources.source_id（原始文件是路径、产物是 "阶段#键"）——
    所以直接拿键的后半段跟每个条目的身份对。对比/热图是**单槽多文件**视图
    （键里没有 "|"，见 plot_compare._plot_compare），不对应单个条目，不参与。

    什么时候调：面板开（plot_panels._open_plot_panel 末尾）/ 关
    （panels._close_panel 里）/ 文件栏重建（refresh_product_groups 末尾），
    都经 window.mark_open_rows 回调，避免那两个模块反向 import 文件坞。

    「打开的图」那一组也在这里同步（同一件事的两半：谁是开着的）：行是
    整行淡色 + 左侧小点的**另一处**表达，而且对"单槽多文件"的对比/热图
    也成立——那两种视图没有对应条目，只在那一组里看得见（用户 2026-10-01
    第 5 条："现在在左边文件区看不到的内容，比如对比、热图、瀑布之类"）。
    """
    _sync_open_figures(window)     # 「打开的图」组：开着的窗口逐个列出来
    open_ids = {key.split("|", 1)[1] for key in getattr(window, "plot_docks", {})
                if "|" in key}
    brush = QBrush(QColor(_OPEN_ROW_BG))
    icon = _open_dot_icon()
    tree = window.file_list
    # 屏蔽信号：setBackground/setIcon 会经 FileItem.setData 走一遍
    # QTreeWidgetItem::setData → **发 itemChanged**，而 on_item_changed 把
    # 它当成"勾选变了"——每次重建都给每条已勾的行白记一行"已选中 X"
    # （2026-10-01 查第 8 条时顺出来的）。这里改的是行颜色/小点，与勾选
    # 无关，本来就不该触发那套联动。blockSignals 没有嵌套计数，所以存下
    # 原状态再还原（refresh_product_groups 末尾调来时外层可能是开着的）。
    prev_blocked = tree.blockSignals(True)
    try:
        for src in gui_sources.all_sources(window):
            item = src.item
            if item is None:
                continue
            if gui_sources.source_id(src) in open_ids:
                item.setBackground(0, brush)
                item.setIcon(0, icon)      # 同一张图重复设同一个 icon 无害
            else:
                item.setBackground(0, QBrush())   # 空画刷 = 恢复默认（无底）
                item.setIcon(0, QIcon())
    finally:
        tree.blockSignals(prev_blocked)


def checked_items(window: QMainWindow) -> list:
    """当前打对号的**叶子**条目（原始数据 + 各组产物；组节点不算）。"""
    return [src.item for src in gui_sources.checked_sources(window)]


def checked_raw_items(window: QMainWindow) -> list:
    """当前打对号的原始数据条目（[删除] 与 [全选] 的口径）。"""
    return [src.item for src in gui_sources.checked_sources(window)
            if src.kind == gui_sources.RAW]


def _sync_group_states(window: QMainWindow) -> None:
    """按子项把每个组的状态重算一遍（组 = 全勾 / 不勾，两态）。

    批量改对号（导入、[全选]、[按条件选]、删除）都是**屏蔽信号**做的，
    itemChanged 的联动不会跑，所以这些地方收尾要显式补一次——否则界面上
    会出现"组上没勾、组里全是勾"这种自相矛盾的样子（2026-09-25 踩过：
    导入默认全勾时组态还停在 Unchecked，勾"整组取消"就没反应）。
    """
    tree = window.file_list
    for node in [tree.raw_group] + tree.groups():
        if not _is_checkable(node):
            continue    # 「打开的图」（不给勾）：没有组态这回事
        node.setCheckState(0, _group_state(
            node.child(i).checkState(0) for i in range(node.childCount())))
    _refresh_group_badges(window)   # 批量改完，折叠着的组上的数字也要跟上


def _sync_select_label(window: QMainWindow) -> None:
    """出图按钮与全选/全不选切换按钮的**文字**跟着勾选集合走。

    全选按钮（2026-09-27 用户："全选全部不选合一"）做哪件事由当前状态决定，
    标签必须跟着变，否则按下去会发生什么全靠猜。

    出图按钮上的数字（2026-09-27 用户："选中 81 个图出对比图，结果出了 162
    个文件的对比图"）：对比没错，是**勾选集比他想的大**——为了出 1D 图勾过
    81 个原始文件，之后又点了「1D 产物」组行，81+81=162；这些数字原先只写在
    状态行角落。把它写在按钮上、并在绘图页给一句构成说明，就再也藏不住。
    """
    btn = getattr(window, "select_all_btn", None)
    if btn is not None:
        raw = window.file_list.raw_group
        n = raw.childCount()
        checked = sum(1 for i in range(n)
                      if raw.child(i).checkState(0) == Qt.Checked)
        # 标签把**作用范围**写出来（用户 2026-09-28 第 3 条）：这个按钮本来就
        # 是"勾只勾原始数据、清却清全部"（见 select_all 的说明），两个方向
        # 的范围不同——不写出来，用户按下去发生什么全靠猜
        btn.setText("全不选（全部条目）" if n and checked == n
                    else "全选（原始数据）")
    _refresh_check_labels(window)


def _check_summary(window: QMainWindow) -> str:
    """勾选集合的一句话说明：条数 + 构成（+ "同一条曲线画两遍"提醒）。"""
    checked = gui_sources.checked_sources(window)
    if not checked:
        return ("已勾选 0 项：点条目行：勾这一条，点组那一行：整组一起勾"
                "（对号方块可以取消）")
    kinds = {}
    for src in checked:
        kinds[src.kind] = kinds.get(src.kind, 0) + 1
    parts = [f"{text} {kinds[kind]}" for kind, text in
             ((gui_sources.RAW, "原始数据"), (gui_sources.ONED, "1D 产物"),
              (gui_sources.BG, "处理产物")) if kinds.get(kind)]
    out = f"已勾选 {len(checked)} 项：" + " ｜ ".join(parts)
    dup = _duplicate_raw_paths(checked)
    if dup:
        # 同一文件的原始条目与它的 1D 产物是同一条曲线，两个都勾 = 画两遍
        out += (f"；⚠ 其中 {len(dup)} 个文件同时勾了原始数据条目和它的 "
                "1D 产物（同一条曲线，会画两遍）")
    return out


def _duplicate_raw_paths(checked) -> set:
    """同一文件既勾了原始条目、又勾了它的 1D 产物 → 那些源文件路径。"""
    raw = {str(s.path) for s in checked if s.kind == gui_sources.RAW}
    prod = {str(s.path) for s in checked if s.kind == gui_sources.ONED}
    return raw & prod


def _refresh_check_labels(window: QMainWindow) -> None:
    """把"勾了多少条"写到出图按钮上，并在绘图页刷新那句构成说明。"""
    n = len(gui_sources.checked_sources(window))
    text = f"出图（已勾选 {n} 项）" if n else "出图（尚未勾选）"
    for attr in ("plot_1d_btn", "plot_now_btn"):
        btn = getattr(window, attr, None)
        if btn is not None:
            btn.setText(text)
    lbl = getattr(window, "check_summary_lbl", None)
    if lbl is not None:
        lbl.setText(_check_summary(window))


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
        if not _is_checkable(item):
            continue    # 「打开的图」那些行不给勾：别给它凭空长出一个复选框
        state = Qt.Checked if want else Qt.Unchecked
        if item.checkState() != state:
            item.setCheckState(state)
            changed += 1
    _sync_group_states(window)   # 组态跟着子项走（屏蔽信号时不会自动跑）
    window.file_list.blockSignals(False)
    if changed:
        _sync_current_to_checks(window)
        _refresh_file_label(window)
    _sync_select_label(window)
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
        what += f" 且名称包含“{text}”"
    if picked:
        _log(window, f"按条件勾选 {len(picked)} 项（{what}"
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
    edit_text.setPlaceholderText("留空则不筛（不区分大小写）")
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
        preview.setText(f"预览：将勾选 {n} 项（共 {total} 项）")

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
