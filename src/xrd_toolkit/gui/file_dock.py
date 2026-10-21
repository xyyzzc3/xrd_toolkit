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
import itertools
import json
import re
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QActionGroup, QBrush, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QDialog, QDockWidget,
    QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMainWindow, QMdiSubWindow, QMenu, QMessageBox, QPushButton,
    QRadioButton, QSpinBox, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.services import stage_cache

from xrd_toolkit.cli import SUPPORTED_EXTS
from xrd_toolkit.gui.panel_state import _collect_geometry, _log, _set_focus
from xrd_toolkit.gui.plot_export import _run_export, _save_figures
from xrd_toolkit.services.data_loader import load_diffraction_image
from xrd_toolkit import paths as xrd_paths
# 文件栏数据模型（2026-10-21 从本文件原样搬出；再导出，tests/scripts 照旧
# 经 gui_file_dock 取用角色常量）
from xrd_toolkit.gui.files.model import (
    GROUP_BATCH_ROLE, GROUP_ROLE, GROUP_TITLE_ROLE, PANEL_KEY_ROLE,
    FileItem, _RowPaintDelegate, _group_counts, _group_state, _group_title,
    _is_checkable, _make_group, _raise_panel, _refresh_group_badges,
    _set_group_title, is_group, panel_key_of)
# 文件栏选择/勾选（2026-10-21 从本文件原样搬出；再导出，tests/scripts 照旧）
from xrd_toolkit.gui.files.selection import (
    _check_summary, _duplicate_raw_paths, _refresh_check_labels,
    _refresh_file_label, _selection_dialog_spec, _selection_matches,
    _set_checks, _sync_current_to_checks, _sync_group_states,
    _sync_select_label, apply_selection, checked_items, checked_raw_items)
# 文件栏排序（2026-10-21 从本文件原样搬出；再导出，tests/scripts 照旧）
from xrd_toolkit.gui.files.sorting import (
    SORT_IMPORT, SORT_LABELS, SORT_MODES, SORT_MTIME, SORT_NAME,
    SORT_SEQ_ROLE, _NATURAL_RE, _apply_sort, _file_mtime, _natural_key,
    _raw_sort_key, _set_current_keeping_scroll, _set_sort_mode, _sort_mode,
    _stamp_import_order, _sync_sort_menu)
# 文件栏分组/打开的图（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.files.groups import (
    OPEN_FIGURES_TITLE, _OPEN_ROW_BG, _fmt_deg, _group_key, _oned_group_title,
    _oned_group_tip, _oned_sig, _oned_sig_is_current, _open_dot,
    _open_dot_icon, _product_keys, _product_keys_on_disk, _restore_tree_state,
    _sync_open_figures, _top_groups, _tree_state, _uncheck_everything,
    refresh_open_marks, refresh_product_groups)
# 文件栏动作（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.files.actions import (
    _confirm_open_many, _open_checked_views, _open_entry_view,
    _open_group_views, _remove_from_list, ask_clear_cache, export_sources)
# 文件栏右键菜单（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.files.menus import (
    OPEN_BULK_VIEWS, _entry_menu, _entry_menu_actions, _keys_of)

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

# ── 「最近打开」（2026-10-08 用户："最近打开——做"） ──────────────
# 记录存 outputs/recent.json：那是用户自己的数据区（不进包、不上传，
# 与"数据不出本机"的既有口径一致）。菜单每次弹出前重建（aboutToShow），
# 已经不存在的路径不进菜单；点击走与主按钮同一条导入口。
_RECENT_KEEP = 8     # 存这么多条
_RECENT_SHOW = 5     # 菜单里最多露这么多

def _recent_path():
    return xrd_paths.OUTPUTS_DIR / "recent.json"

def _load_recent() -> list:
    """读记录；没有/坏了都当空——这是便利功能，不打扰用户。"""
    try:
        data = json.loads(_recent_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [str(p) for p in data if isinstance(p, str)]

def _note_recent(window: QMainWindow, chosen) -> None:
    """把刚打开的文件/文件夹记进「最近打开」（去重、最新的在前）。"""
    items = [str(Path(p)) for p in chosen if p]
    if not items:
        return
    merged = (items + [p for p in _load_recent() if p not in items]
              )[:_RECENT_KEEP]
    try:
        xrd_paths.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
        _recent_path().write_text(
            json.dumps(merged, ensure_ascii=False, indent=1),
            encoding="utf-8")
    except OSError as err:       # 写不进去只是少个便利入口，如实说一声
        _log(window, f"「最近打开」写入失败（不影响导入）：{err}")

def _open_recent(window: QMainWindow, path_str: str) -> None:
    """点「最近打开」里的一条：按类型走与主按钮同一条导入口。"""
    path = Path(path_str)
    if not path.exists():
        return                   # 刚被删/挪走：什么都不做（下次弹出即消失）
    _note_recent(window, [path_str])   # 用过一次顶到最前
    if path.is_dir():
        _scan_folder(window, path)
    else:
        window.add_files([path_str])

def _rebuild_recent_menu(window: QMainWindow) -> None:
    """「最近打开」子菜单每次弹出前重建（失效路径剔除）。"""
    menu = getattr(window, "recent_menu", None)
    if menu is None:
        return
    menu.clear()
    items = [p for p in _load_recent() if Path(p).exists()][:_RECENT_SHOW]
    if not items:
        act = menu.addAction("（空）")
        act.setEnabled(False)
        return
    for p in items:
        path = Path(p)
        act = menu.addAction(path.name or str(path))
        act.setToolTip(p)
        act.triggered.connect(
            lambda _checked=False, s=p: _open_recent(window, s))

# ── 文件栏排序（2026-10-08 用户："文件栏添加排序功能"） ─────────
# 三个模式：导入顺序（默认）/ 名称（自然序）/ 修改时间（新在前）。
# 只重排「原始数据」的子项（**移动条目对象、不重建**——勾选/高亮/开图
# 记号都留住）；各产物分组的组内行序本来就按"文件栏行序"排，自动跟随。
# 不落盘记忆：每次打开软件回到「导入顺序」（用户 2026-10-08 拍板）。

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
                _stamp_import_order(window, item)
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
        _stamp_import_order(window, item)
        item.setCheckState(0, Qt.Checked if select else Qt.Unchecked)
        existing[p.resolve()] = item
        added.append(item)
    window.file_list.blockSignals(False)
    if added and _sort_mode(window) != SORT_IMPORT:
        # 排序开着时新条目落到排好的位置；默认模式保持"纯追加"老路径
        # （TestFolderImport / TestDragDrop 等按导入序断言的用例不受影响）
        _apply_sort(window)
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
