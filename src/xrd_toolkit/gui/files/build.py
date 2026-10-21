"""文件坞构建：整棵树 + 打开/保存/删除/导出按钮 + 选择工具 + 事件接线。

2026-10-21 从 file_dock.py 原样搬出（只改结构，不动逻辑）。
file_dock 再导出，app.py 照旧经 gui_file_dock 取 _build_file_dock。
"""
import itertools

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import (
    QDockWidget, QFileDialog, QGridLayout, QMainWindow, QMenu, QPushButton,
    QTreeWidget, QVBoxLayout, QWidget)

from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui.files.actions import _open_entry_view, _remove_from_list
from xrd_toolkit.gui.files.groups import refresh_product_groups
from xrd_toolkit.gui.files.importing import (
    _note_recent, _rebuild_recent_menu, _scan_folder)
from xrd_toolkit.gui.files.menus import _entry_menu
from xrd_toolkit.gui.files.model import (
    FILE_FILTER, FileTree, _group_state, _group_title, _is_checkable,
    _raise_panel, _refresh_group_badges, is_group, panel_key_of)
from xrd_toolkit.gui.files.selection import (
    _refresh_file_label, _selection_dialog_spec, _set_checks,
    _sync_current_to_checks, _sync_select_label, apply_selection,
    checked_raw_items)
from xrd_toolkit.gui.files.sorting import (
    SORT_IMPORT, SORT_LABELS, SORT_MTIME, SORT_NAME, _set_sort_mode,
    _sync_sort_menu)
from xrd_toolkit.gui.panel_state import _log
from xrd_toolkit.gui.plot_export import _run_export, _save_figures
from xrd_toolkit.services import stage_cache


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
    btn_export.setToolTip("把勾选条目的 1D 产物批量存成两列 txt/chi；"
                          "两个及以上还能顺带生成全部数据总表")
    open_menu = QMenu(btn_open)
    act_files = open_menu.addAction("打开文件…")
    act_files.setToolTip("选择一个或多个衍射图像加入文件栏")
    act_folder = open_menu.addAction("打开文件夹…")
    act_folder.setToolTip("选一个文件夹，自动遍历其中的衍射图像并加入文件栏"
                          "（拖文件夹进窗口同样生效）")
    # 「最近打开」子菜单（2026-10-08 用户："最近打开——做"）：内容每次
    # 弹出前重建（见 _rebuild_recent_menu），空态给一条禁用占位
    recent_menu = open_menu.addMenu("最近打开")
    recent_menu.setObjectName("recent_menu")
    recent_menu.setToolTipsVisible(True)   # 让 action 的完整路径提示可见
    window.recent_menu = recent_menu
    recent_menu.aboutToShow.connect(lambda: _rebuild_recent_menu(window))
    btn_open.setMenu(open_menu)
    window.open_files_action = act_files
    window.open_folder_action = act_folder
    # 三行按钮排成**一张三列栅格**（2026-10-09 用户："文件栏的按钮排布
    # 有点乱"）：此前三行各自是独立横排、分栏各不相同（2/1、1/1/1、1/1），
    # 竖缝一行一个位置（166+80 / 80×3 / 123×2），右边缘看着参差。同一张
    # 栅格 + 每列等拉伸后，三行的缝落在同样的两列上。按钮、顺序、职责都不动。
    grid = QGridLayout()
    grid.setHorizontalSpacing(6)
    grid.setVerticalSpacing(4)
    for col in range(3):
        grid.setColumnStretch(col, 1)
    grid.addWidget(btn_open, 0, 0, 1, 2)
    # [排序▾]（2026-10-08 用户："文件栏添加排序功能"）：挂在第一行右侧。
    # 为什么不进第三行（全选/按条件选 旁）：那行添第三个按钮会把文件列顶
    # 过 ≈320 px 的窄排版红线（第二行三个按钮已把下限锁在 ≈270）。
    btn_sort = QPushButton("排序▾")
    btn_sort.setObjectName("sort_btn")
    btn_sort.setToolTip("「原始数据」的排列方式：导入顺序 / 名称（数字按大小）/ "
                        "修改时间（新在前）；产物分组的行序跟着走，勾选与高亮不动")
    sort_menu = QMenu(btn_sort)
    sort_menu.setObjectName("sort_menu")
    sort_menu.setToolTipsVisible(True)
    sort_group = QActionGroup(btn_sort)
    sort_group.setExclusive(True)
    for mode, tip in (
            (SORT_IMPORT, "按加入文件栏的先后排（默认；每次打开软件都回到它）"),
            (SORT_NAME, "自然顺序：名字里的数字按数值比（29 小于 31 小于 100）"),
            (SORT_MTIME, "文件在磁盘上的修改时间，最新的在最上面")):
        act = sort_menu.addAction(SORT_LABELS[mode])
        act.setCheckable(True)
        act.setData(mode)
        act.setToolTip(tip)
        sort_group.addAction(act)
        act.triggered.connect(
            lambda _checked=False, m=mode: _set_sort_mode(window, m))
    btn_sort.setMenu(sort_menu)
    window.sort_btn = btn_sort
    window.sort_menu = sort_menu
    grid.addWidget(btn_sort, 0, 2)
    _sync_sort_menu(window)
    grid.addWidget(btn_save, 1, 0)
    grid.addWidget(btn_delete, 1, 1)
    grid.addWidget(btn_export, 1, 2)

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
    grid.addWidget(btn_all, 2, 0, 1, 2)
    grid.addWidget(btn_pick, 2, 2)
    lay.addLayout(grid)

    window.file_list = FileTree()   # 覆盖了 minimumSizeHint，可以收窄
    window._check_syncing = False   # 组↔子项联动期间别再记账（防递归）
    # 排序状态（2026-10-08）：模式只活在本次会话（不落盘，用户拍板）；
    # _import_seq 给条目盖"第几个进来的"序号（选回「导入顺序」要用）
    window._sort_mode = SORT_IMPORT
    window._import_seq = itertools.count(1)
    window._sort_mtimes = {}
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
    # 双击条目 = 打开这一张的图：产物/右键那条 = 1D；原始条目 = 它本身的
    # 2D 图（2026-10-08 改，见 _open_entry_view 的说明）。第一次单击会顺手
    # 勾上这一条，无害；不想动勾选就用右键
    window.file_list.itemDoubleClicked.connect(
        lambda item, _col=0: _open_entry_view(window, item))
    window.file_list.setContextMenuPolicy(Qt.CustomContextMenu)
    window.file_list.customContextMenuRequested.connect(
        lambda pos: _entry_menu(window, window.file_list.itemAt(pos)))
    # 记录鼠标按下时命中的条目与对号状态（区分方块点击/行体点击）
    window.file_list.viewport().installEventFilter(
        _PressRecorder(window, window.file_list))

    def open_dialog():
        chosen, _ = QFileDialog.getOpenFileNames(
            window, "选择数据文件", "data", FILE_FILTER)
        if chosen:
            window.add_files(chosen)
            _note_recent(window, chosen)   # 记进「最近打开」

    def open_folder():
        """文件夹导入 = 弹目录选择框 → _scan_folder 扫描加入
        （与拖文件夹进窗口同一套逻辑）。"""
        folder = QFileDialog.getExistingDirectory(
            window, "选择数据文件夹", "data")
        if folder:
            _scan_folder(window, folder)
            _note_recent(window, [folder])   # 记进「最近打开」

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

