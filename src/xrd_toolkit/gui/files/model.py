"""文件栏数据模型：FileItem、组节点、角色槽、行画笔委托。

2026-10-21 从 file_dock.py 原样搬出（只改结构，不动逻辑）。file_dock
再导出，tests/scripts 照旧经 gui_file_dock 取用角色常量。
"""
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QMainWindow, QMdiSubWindow, QStyle,
    QStyleOptionViewItem, QStyledItemDelegate, QTreeWidget, QTreeWidgetItem)

from xrd_toolkit.gui.panel_state import _set_focus


GROUP_ROLE = Qt.UserRole + 3        # 组节点标记（条目没有这个槽）
GROUP_BATCH_ROLE = GROUP_ROLE + 1   # 处理组的台账批次号（右键"删除这一组"用）
GROUP_TITLE_ROLE = GROUP_ROLE + 2   # 组的**纯名字**（不含折叠时才显示的
                                    # "（n/m 选中）"后缀，见 _group_title）
GROUP_MASKED2D_ROLE = GROUP_ROLE + 4   # 「Mask 后的 2D」组标记
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

# ── 文件栏树（2026-10-21 从 file_dock.py 搬入） ──
FILE_FILTER = "衍射图像 (*.tif *.tiff *.edf *.cbf);;所有文件 (*)"

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

