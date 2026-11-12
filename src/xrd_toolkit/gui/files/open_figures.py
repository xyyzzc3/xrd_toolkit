"""文件栏「打开的图」组：同步屏幕上开着的面板、左侧小点标记。

2026-10-21 从 files/groups.py 原样搬出（只改结构，不动逻辑）。
groups 再导出，file_dock 照旧经 gui_file_dock 取用。
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui.files.model import (
    PANEL_KEY_ROLE, FileItem, _group_title, _make_group)


OPEN_FIGURES_TITLE = "打开的图"

# 「正在打开」的唯一记号 = 左侧实心蓝点（2026-11-12 用户：整行淡色
# 容易和"选中/勾选"混，去掉底色，只留点）。
_open_dot = None        # 懒建（建 QPixmap 要先有 QApplication）

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
        group.setToolTip(0, f"{OPEN_FIGURES_TITLE}（{len(wanted)} 张）："
                            "屏幕上开着的图，双击提到最前面")
    finally:
        tree.blockSignals(False)
        window._check_syncing = prev_syncing
    tree.verticalScrollBar().setValue(scroll)

def _top_groups(tree) -> list:
    """全部顶层组节点（含"原始数据"组；tree.groups() 是不含它的）。"""
    return [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]

def _open_dot_icon() -> QIcon:
    """左侧那个小实心点：深蓝色 8 px 圆，居中画在 10×10 的透明底上。

    为什么要它：整行底色容易和"选中/勾选"混（2026-11-12 用户：背景加深
    很乱）。现在蓝点是唯一的"有面板活着"记号，扫一眼就能数出开着几张。
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

def set_workbench_open(window: QMainWindow, source_id) -> None:
    """当前工作台的中央图对应的条目也算"开着"（2026-11-12 用户拍板）。

    校准 2D 图、掩膜画布、物相/精修当前曲线都显示在屏幕中央，但不在
    plot_docks 里；这里把它的 source id 记到 window._workbench_open_id，
    refresh_open_marks 会和面板键的 id 取并集，用同一个蓝点表达。
    退出工作台 / 没有当前图时传 None 清掉。
    """
    sid = str(source_id) if source_id else None
    if getattr(window, "_workbench_open_id", None) == sid:
        return
    window._workbench_open_id = sid
    refresh_open_marks(window)


def refresh_open_marks(window: QMainWindow) -> None:
    """把"屏幕上现在有这张图"画出来：左侧实心蓝点。

    用户 2026-09-28 定「文件栏记号 = 正在打开」；2026-11-12 进一步
    简化："背景加深很乱"——去掉整行淡色，只留蓝点；勾没勾只看框里的
    小勾（Qt 的选中底色已在 FileTree.__init__ 的样式里取消）。
    2026-11-12 再扩：**当前工作台的中央图也算开着**（校准 2D / 掩膜
    画布 / 物相精修当前曲线），与独立面板共用同一个蓝点。

    认哪一条开着：面板键是 `"<视图>|<条目身份>"`（1D/2D/剖面/瀑布都按条目
    开），身份 = sources.source_id（原始文件是路径、产物是 "阶段#键"）——
    所以直接拿键的后半段跟每个条目的身份对；再加上工作台的
    window._workbench_open_id。对比/热图是**单槽多文件**视图（键里没有
    "|"，见 plot_compare._plot_compare），不对应单个条目，不参与。

    什么时候调：面板开（plot_panels._open_plot_panel 末尾）/ 关
    （panels._close_panel 里）/ 文件栏重建（refresh_product_groups 末尾），
    都经 window.mark_open_rows 回调，避免那两个模块反向 import 文件坞；
    工作台换对象/退出由 set_workbench_open 调。

    「打开的图」那一组也在这里同步（同一件事的两半：谁是开着的）：行是
    左侧小点的**另一处**表达，而且对"单槽多文件"的对比/热图也成立——
    那两种视图没有对应条目，只在那一组里看得见（用户 2026-10-01 第 5 条：
    "现在在左边文件区看不到的内容，比如对比、热图、瀑布之类"）。工作台
    的中央图**不进这一组**（它是工具，不是结果图；2026-11-12 用户确认）。
    """
    _sync_open_figures(window)     # 「打开的图」组：开着的窗口逐个列出来
    open_ids = {key.split("|", 1)[1] for key in getattr(window, "plot_docks", {})
                if "|" in key}
    workbench_id = getattr(window, "_workbench_open_id", None)
    if workbench_id:
        open_ids.add(str(workbench_id))
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
            item = getattr(src, "item", None)
            if item is None:
                continue   # 测试/脚本手工造的 Source 可能没有文件栏行
            if gui_sources.source_id(src) in open_ids:
                item.setIcon(0, icon)      # 同一张图重复设同一个 icon 无害
            else:
                item.setIcon(0, QIcon())
            # 旧版的"整行淡色"残留：统一清掉（空画刷 = 恢复默认）
            item.setBackground(0, QBrush())
    finally:
        tree.blockSignals(prev_blocked)

