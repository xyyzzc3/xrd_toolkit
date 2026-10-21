"""文件栏拖放：把文件拖到产物分组/条目上（改归属、加进组）。

2026-10-21 从 file_dock.py 原样搬出（只改结构，不动逻辑）。
file_dock 再导出；menus 在 _entry_menu 里延迟导入这两个动作。
"""
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui.files.groups import refresh_product_groups
from xrd_toolkit.gui.files.menus import _keys_of
from xrd_toolkit.gui.files.model import (
    GROUP_BATCH_ROLE, _group_title, is_group)
from xrd_toolkit.gui.panel_state import _log
from xrd_toolkit.services import stage_cache


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

