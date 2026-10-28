"""文件栏右键菜单：条目/组的菜单动作、批量打开子菜单。

2026-10-21 从 file_dock.py 原样搬出（只改结构，不动逻辑）。
file_dock 再导出，tests/scripts 照旧经 gui_file_dock 取用。
"""
from pathlib import Path

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import (
    QInputDialog, QMainWindow, QMenu, QMessageBox)

from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui.files.actions import (
    _open_checked_views, _open_entry_view, _open_group_views,
    _remove_from_list, ask_clear_cache, export_sources)
from xrd_toolkit.gui.files.groups import refresh_product_groups
from xrd_toolkit.gui.files.model import (
    GROUP_BATCH_ROLE, GROUP_MASKED2D_ROLE, _group_title, _raise_panel,
    is_group, panel_key_of)
from xrd_toolkit.gui.panel_state import _log
from xrd_toolkit.services import masked2d, stage_cache


# 右键批量打开可选的视图（2026-10-09）：菜单顺序就是这里的顺序。产物条目
# 只有 1D 能出，选了别的视图由 _open_checked_views 挑出原始条目并记日志。
OPEN_BULK_VIEWS = ("1D", "2D", "剖面", "瀑布")

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

def _entry_menu_actions(menu: QMenu, window: QMainWindow, item) -> dict:
    """往 menu 上挂右键菜单项，返回 {QAction: 动作} 映射（不 exec）。

    与 _entry_menu 分开（2026-10-09）：离屏测试点不了模态菜单（_entry_menu
    在窗口没显示时直接 return），但菜单**内容**值得钉住——批量打开两条从
    写死 1D 扩成按视图的子菜单，回归测试直接检查这个映射。动作值两种形态：
    字符串（不带参数）或 (名字, 参数) 元组（如 ("open_checked", "2D")）。
    """
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
        # 超过防爆线（MAX_PANELS_PER_BATCH）一张都不画，所以这里给一条没有
        # 上限的入口。**2026-10-09 起按视图选**（子菜单，此前写死 1D）：
        # 原图三类超限后此前连手动全开的入口都没有——用户："原图功能无法
        # 批量出超过防爆图数字，应该怎么优化"。
        n_checked = len(gui_sources.checked_sources(window))
        if n_checked:
            sub = menu.addMenu(f"打开勾选的 {n_checked} 张图")
            for name in OPEN_BULK_VIEWS:
                actions[sub.addAction(name)] = ("open_checked", name)
        if item is window.file_list.raw_group and item.childCount():
            sub = menu.addMenu(f"打开整组图（{item.childCount()} 张）")
            for name in OPEN_BULK_VIEWS:
                actions[sub.addAction(name)] = ("open_group", name)
    elif item.data(0, GROUP_MASKED2D_ROLE):
        # Mask 后的 2D 组：和原始数据一样能出 2D/1D/剖面/瀑布
        if item.childCount():
            sub = menu.addMenu(f"打开整组图（{item.childCount()} 张）")
            for name in OPEN_BULK_VIEWS:
                actions[sub.addAction(name)] = ("open_group", name)
            menu.addSeparator()
            actions[menu.addAction(
                f"删除这一组 Mask 2D（{item.childCount()} 条）")] = \
                "drop_masked2d_group"
    elif is_group(item):
        # 产物组：产物是 1D 曲线，只有 1D 能出——不给视图子菜单
        if item.childCount():
            actions[menu.addAction(
                f"打开整组 1D 图（{item.childCount()} 张）")] = "open_group"
            menu.addSeparator()
            actions[menu.addAction(
                f"删除这一组产物（{item.childCount()} 条）")] = "drop_group"
            actions[menu.addAction("导出这一组（txt / chi / CSV）")] = \
                "export_group"
    elif src is not None and src.kind == gui_sources.MASKED2D:
        actions[menu.addAction(f"打开 1D 图（{src.display}）")] = "open_item"
        menu.addSeparator()
        actions[menu.addAction(
            f"删除这条 Mask 2D（{src.display}；原图与 mask 不动）")] = \
            "drop_masked2d"
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
        # 单条导出不会生成总表（单个数据集就光一个文件）——菜单别写 CSV
        actions[menu.addAction("导出这一条（txt / chi）")] = "export_item"
    if actions:
        menu.addSeparator()
    actions[menu.addAction("删除所有缓存…")] = "clear_cache"
    return actions

def _drop_masked2d(window, paths) -> tuple:
    """删 Mask 2D manifest，并清掉它名下的 1D / 处理产物缓存。

    Mask 2D 是源，1D/处理产物都按 manifest 路径取数；manifest 没了，
    这些产物就再也挂不回文件栏，留着只占磁盘。返回 (manifest 数,
    相关产物数)。
    """
    wanted = {str(Path(p).resolve()) for p in paths if p}
    keys_1d = [
        key for key, meta, _lo, _hi in stage_cache.list_products("1d")
        if str(Path(meta.get("path") or "").resolve()) in wanted]
    keys_bg = []
    for node in stage_cache.list_batches("bg"):
        for path, meta in (node.get("items") or {}).items():
            if str(Path(path).resolve()) in wanted:
                keys_bg.append(meta.get("key"))
    related = stage_cache.drop_keys("1d", keys_1d) + \
        stage_cache.drop_keys("bg", keys_bg)
    masks = sum(1 for p in paths if masked2d.delete(p))
    return masks, related


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
    菜单项在 _entry_menu_actions 里建（与 exec 分开，便于离屏测试）。
    """
    if not window.isVisible():
        return   # 窗口没显示（测试/无头）不弹模态菜单：会永远等不到人点
    from xrd_toolkit.gui.files.dragdrop import drop_product_group, drop_product_item  # 延迟导入：dragdrop 依赖本模块的 _keys_of
    # 派发时要用的两样东西（菜单项在 helper 里建，它自己也算这两样）
    src = None if is_group(item) else gui_sources.source_of(item)
    panel_key = panel_key_of(item)
    menu = QMenu(window)
    actions = _entry_menu_actions(menu, window, item)
    pos = window.file_list.viewport().mapToGlobal(QPoint(0, 0))
    picked = menu.exec(pos)
    entry = actions.get(picked)
    if entry is None:
        return
    # 动作两种形态：字符串（无参数）或 (名字, 参数) 元组（视图子菜单那几条）
    what, arg = entry if isinstance(entry, tuple) else (entry, None)
    if what == "open_checked":
        _open_checked_views(window, arg or "1D")
    elif what == "open_item":
        _open_entry_view(window, item, explicit=True)   # 右键：原始条目也照开
    elif what == "open_group":
        _open_group_views(window, item, arg or "1D")
    elif what == "drop_group":
        drop_product_group(window, item)
    elif what in ("drop_masked2d", "drop_masked2d_group"):
        items = ([item.child(i) for i in range(item.childCount())]
                 if what.endswith("_group") else [item])
        paths = []
        for leaf in items:
            src = gui_sources.source_of(leaf)
            if src is not None and src.kind == gui_sources.MASKED2D:
                paths.append(src.path)
        removed, related = _drop_masked2d(window, paths)
        refresh_product_groups(window)
        _log(window, f"已删除 {removed} 条 Mask 后的 2D + {related} 条相关 "
                     f"1D/处理产物（原始图与 mask 文件不动）")
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

