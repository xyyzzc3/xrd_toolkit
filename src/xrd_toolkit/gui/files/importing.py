"""文件栏导入：扫描文件夹、拖放导入、最近打开、重名处理。

2026-10-21 从 file_dock.py 原样搬出（只改结构，不动逻辑）。
file_dock 再导出，tests/scripts 照旧经 gui_file_dock 取用。
"""
import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit import paths as xrd_paths
from xrd_toolkit.cli import SUPPORTED_EXTS
from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui.files.groups import refresh_product_groups
from xrd_toolkit.gui.files.model import FileItem
from xrd_toolkit.gui.files.selection import (
    _refresh_file_label, _sync_current_to_checks, _sync_group_states,
    _sync_select_label)
from xrd_toolkit.gui.files.sorting import (
    SORT_IMPORT, _apply_sort, _set_current_keeping_scroll, _sort_mode,
    _stamp_import_order)
from xrd_toolkit.gui.panel_state import _log


SUPPORTED_SUFFIXES = tuple(sorted(SUPPORTED_EXTS))

_RECENT_KEEP = 8     # 存这么多条
_RECENT_SHOW = 5     # 菜单里最多露这么多

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

def _unique_display_name(window: QMainWindow, name: str) -> str:
    """给重复文件起不重名的显示名："xxx.tif" → "xxx (1).tif" → "xxx (2).tif"…"""
    taken = {window.file_list.item(i).text()
             for i in range(window.file_list.count())}
    p = Path(name)
    n = 1
    while f"{p.stem} ({n}){p.suffix}" in taken:
        n += 1
    return f"{p.stem} ({n}){p.suffix}"

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
    from xrd_toolkit.gui.file_dock import _ask_duplicate, _ask_rename  # 破循环
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

