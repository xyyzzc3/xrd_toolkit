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
    FILE_FILTER, GROUP_BATCH_ROLE, GROUP_ROLE, GROUP_TITLE_ROLE,
    PANEL_KEY_ROLE, FileItem, FileTree, _RowPaintDelegate, _group_counts,
    _group_state, _group_title, _is_checkable, _make_group, _raise_panel,
    _refresh_group_badges, _set_group_title, is_group, panel_key_of)
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
# 文件栏导入（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.files.importing import (
    SUPPORTED_SUFFIXES, _RECENT_KEEP, _RECENT_SHOW, _dropped_items,
    _load_recent, _note_recent, _open_recent, _recent_path,
    _rebuild_recent_menu, _scan_folder, _unique_display_name, add_files)
# 文件栏拖放（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.files.dragdrop import (
    _group_keys_by_kind, drop_product_group, drop_product_item)
# 文件栏重名对话框（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.files.dialogs import _ask_duplicate, _ask_rename
# 文件坞构建（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.files.build import (
    _PressRecorder, _build_file_dock, _on_file_selected)

# 支持的后缀（拖放/文件夹扫描/CLI 菜单共用一张表；endswith 要 tuple）

# ══ 左侧：文件栏（树：原始数据 + 各阶段产物分组）════════════

# ── 「最近打开」（2026-10-08 用户："最近打开——做"） ──────────────
# 记录存 outputs/recent.json：那是用户自己的数据区（不进包、不上传，
# 与"数据不出本机"的既有口径一致）。菜单每次弹出前重建（aboutToShow），
# 已经不存在的路径不进菜单；点击走与主按钮同一条导入口。

# ── 文件栏排序（2026-10-08 用户："文件栏添加排序功能"） ─────────
# 三个模式：导入顺序（默认）/ 名称（自然序）/ 修改时间（新在前）。
# 只重排「原始数据」的子项（**移动条目对象、不重建**——勾选/高亮/开图
# 记号都留住）；各产物分组的组内行序本来就按"文件栏行序"排，自动跟随。
# 不落盘记忆：每次打开软件回到「导入顺序」（用户 2026-10-08 拍板）。

