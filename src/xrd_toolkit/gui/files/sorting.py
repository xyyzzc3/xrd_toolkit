"""文件栏排序：导入顺序/名称/修改时间、自然序、排序菜单。

2026-10-21 从 file_dock.py 原样搬出（只改结构，不动逻辑）。
file_dock 再导出，tests/scripts 照旧经 gui_file_dock 取用。
"""
import re
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.gui.panel_state import _log


# ── 文件栏排序（2026-10-08）的常量 ──
SORT_SEQ_ROLE = Qt.UserRole + 7   # 条目进文件栏的先后序号——「导入顺序」的
                                  # 依据：重排会打乱位置，序号得单独记着
SORT_IMPORT = "import"
SORT_NAME = "name"
SORT_MTIME = "mtime"
SORT_MODES = (SORT_IMPORT, SORT_NAME, SORT_MTIME)
SORT_LABELS = {SORT_IMPORT: "导入顺序", SORT_NAME: "名称",
               SORT_MTIME: "修改时间"}
_NATURAL_RE = re.compile(r"(\d+)")

def _natural_key(text) -> list:
    """自然排序键：数字段按数值比（29 < 31 < 100），其余段按小写文本比。

    re.split 带捕获组 ⇒ 键的偶数位必是文本段、奇数位必是数字段，类型逐位
    一致，列表比较不会出现 int/str 混比（不用再包元组）。
    """
    return [int(part) if part.isdigit() else part.lower()
            for part in _NATURAL_RE.split(str(text or ""))]

def _sort_mode(window) -> str:
    return getattr(window, "_sort_mode", SORT_IMPORT)

def _stamp_import_order(window, item) -> None:
    """给新条目盖"第几个进来的"序号——回头选「导入顺序」要靠它排回去。"""
    item.setData(0, SORT_SEQ_ROLE, next(window._import_seq))

def _file_mtime(window, path_str):
    """按需 stat；读不到就沿用上次看到的（从没读到过 → None = 排最后）。"""
    cache = window._sort_mtimes
    try:
        mt = Path(path_str).stat().st_mtime
    except OSError:
        return cache.get(path_str)
    cache[path_str] = mt
    return mt

def _raw_sort_key(window, item) -> tuple:
    mode = _sort_mode(window)
    name = _natural_key(item.text(0))
    path_str = item.data(0, Qt.UserRole) or ""
    if mode == SORT_NAME:
        return (name, path_str)
    if mode == SORT_MTIME:
        mt = _file_mtime(window, path_str)
        return (0.0 if mt is None else -mt, name, path_str)   # 新在前
    seq = item.data(0, SORT_SEQ_ROLE)
    return (10 ** 9 if seq is None else int(seq),)

def _apply_sort(window: QMainWindow) -> None:
    """按当前模式重排「原始数据」子项（移动、不重建，勾选与高亮都留住）。

    滚动条原地不动（同"不许跳"的既有规矩）；显式换排序时由
    _set_sort_mode 在收尾把滚动条归零。
    """
    tree = window.file_list
    raw = tree.raw_group
    cur = tree.currentItem()
    tree.blockSignals(True)
    try:
        items = [raw.takeChild(0) for _ in range(raw.childCount())]
        for it in sorted(items, key=lambda it: _raw_sort_key(window, it)):
            raw.addChild(it)
    finally:
        tree.blockSignals(False)
    if cur is not None:
        _set_current_keeping_scroll(tree, cur)

def _sync_sort_menu(window) -> None:
    """菜单勾点归位（setChecked 不会发 triggered，无递归）。"""
    menu = getattr(window, "sort_menu", None)
    if menu is None:
        return
    cur = _sort_mode(window)
    for act in menu.actions():
        act.setChecked(act.data() == cur)

def _set_sort_mode(window: QMainWindow, mode: str) -> None:
    """换排序：重排「原始数据」→ 产物分组行序跟上 → 勾点归位 → 回顶端。

    不落盘：关掉软件再打开回到「导入顺序」。
    """
    if mode == _sort_mode(window):
        _sync_sort_menu(window)
        return   # 原地不动：不重排、不滚顶（点的是已选中的那条）
    window._sort_mode = mode
    _apply_sort(window)
    _log(window, f"文件栏排序改为「{SORT_LABELS[mode]}」：重排「原始数据」"
                 "（产物分组的行序跟着走，勾选与高亮不动）")
    from xrd_toolkit.gui.file_dock import refresh_product_groups  # 破循环：只取这一个
    refresh_product_groups(window, quiet=True)   # 产物行序立刻跟上
    _sync_sort_menu(window)
    bar = window.file_list.verticalScrollBar()
    bar.setValue(0)
    # 延后一帧再归零一次：与 _set_current_keeping_scroll 的延迟滚动同一招，
    # 不让"回顶端"输给 Qt 排在下一个事件循环里的滚动
    QTimer.singleShot(0, lambda: bar.setValue(0))

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

