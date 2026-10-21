"""文件栏动作：打开条目/整组/勾选、导出、从列表移除、清缓存。

2026-10-21 从 file_dock.py 原样搬出（只改结构，不动逻辑）。
file_dock 再导出，tests/scripts 照旧经 gui_file_dock 取用。
"""
from PySide6.QtWidgets import QMainWindow, QMessageBox

from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui.files.groups import refresh_product_groups
from xrd_toolkit.gui.files.model import _raise_panel, is_group, panel_key_of
from xrd_toolkit.gui.files.selection import (
    _refresh_file_label, _sync_current_to_checks, _sync_group_states)
from xrd_toolkit.gui.panel_state import _log


def _open_entry_view(window: QMainWindow, item, explicit: bool = False) -> None:
    """双击条目 / 右键 [打开 1D 图]：按条目打开一张面板（两处共用）。

    走 window.open_view_source 回调（app 建窗时挂上 plot_views._open_source_view），
    避免本模块反向 import plot_views。双击时第一次单击已经按老手势处理过
    （可能顺手把这条勾上了）——不去撤销：勾上无害，撤销反而打乱用户的选择。

    **原始条目双击 = 打开它本身的 2D 图**（2026-10-08 用户拍板改的）：
    旧行为（2026-09-27"双击打不开、只给指路"）的理由是"原始数据能出多种
    图"——但"双击必有反应"是更硬的习惯，而原始数据的本体就是那张 2D 图
    （零计算、秒开）；1D / 剖面 / 瀑布仍走顶部类型按钮——那些才是要计算
    的动作。右键 [打开 1D 图] 的 `explicit=True` 路照旧打开 1D（菜单上
    明写着，不会误解）。产物条目两种手势都直接打开：它们天生只有 1D 一种。
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
        opener(src, "2D")
        return
    opener(src, "1D")

def _open_group_views(window: QMainWindow, item, name: str = "1D") -> None:
    """右键 [打开整组图] ▸ 视图：整组逐条打开；张数多时先问一声。

    每张 ≈15 MB（81 张 ≈1.4 GB），超过批量开图的上限（MAX_PANELS_PER_BATCH）
    先弹确认——整组打开是显式动作，确认一下比默默吃内存好。
    **name 可换视图**（2026-10-09）：原始数据组自带视图子菜单（整组开
    2D/剖面/瀑布）；产物组只有 1D（那条菜单项不传 name，走默认）。
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
                window, f"打开整组 {name} 图",
                f"要打开 {len(sources)} 张 {name} 图吗？每张约占 15 MB 内存"
                f"（合计约 {len(sources) * 15} MB），开完会占满面板区。"):
            return
    opener = getattr(window, "open_view_group", None)
    if opener is not None:
        opener(sources, name)

def _confirm_open_many(window: QMainWindow, title: str, text: str) -> bool:
    """> 防爆线批量开图前的确认框：[打开] / [取消]（回车默认 [取消]）。

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
    """右键 [打开勾选的 N 张图] ▸ 视图：把勾选的条目逐条打开。

    与视图按钮的唯一区别：**没有防爆线**，超过先弹确认（每张 ≈15 MB）。
    用户 2026-09-27："没有办法批量打开图，只能一个一个选"——视图按钮超
    过防爆线是**一张都不画**的（防爆图），所以那批人没有别的入口；这条
    右键给他们一条明确的、带确认的路。
    **2026-10-09**：入口从写死 1D 扩成按视图选（1D / 2D / 剖面 / 瀑布），
    因为原图三类超限后此前没有任何手动全开的出口（用户："原图功能无法
    批量出超过防爆图数字，应该怎么优化"）。非 1D 视图要的是原始图像：
    勾选里的产物条目跳过、日志说清（产物是 1D 曲线，只能出 1D 图 /
    对比 / 热图——与视图按钮 _plot_selected 同一套说法）。
    """
    sources = gui_sources.checked_sources(window)
    if not sources:
        _log(window, "还没有勾选任何条目")
        return
    if name != "1D":
        raws = [s for s in sources if s.kind == gui_sources.RAW]
        if len(raws) != len(sources):
            _log(window, f"跳过 {len(sources) - len(raws)} 个产物条目："
                         f"{name} 要从原始图像算（产物是 1D 曲线，只能出 "
                         "1D 图 / 对比 / 热图）")
        sources = raws
        if not sources:
            _log(window, f"勾选里没有原始数据条目——{name} 要从原始图像算")
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
    """「删除所有缓存…」：**点一下直接删**（2026-10-14 起）。

    两代保护的历史：2026-10-13 之前弹确认框——这台机器上（真 cocoa 实测）
    QMessageBox 从 exec() 到真正画上屏要好几秒，"取消"因此吃过三回冤枉
    （用户："gui 现在无法删除所有缓存"，日志连记三条「已取消删除缓存」）；
    2026-10-13 改成"日志提醒 + 再点一次才真删"；**2026-10-14** 用户拍板
    （"删除全部缓存，改成就一次"）干脆一击即删。删得起的原因：缓存只是重算得
    回来的产物（下次出图重新积分，不会算错），而原始数据、导出、配置
    都不受影响——日志照旧写清删了几个，不弹任何框。
    """
    from xrd_toolkit.services import stage_cache
    info = stage_cache.describe()
    if not info["files"]:
        _log(window, "缓存本来就是空的（还没有生成过产物）")
        return
    cleared = stage_cache.clear()
    window.refresh_groups()
    _log(window, f"已删除所有缓存：{cleared} 个缓存文件"
                 f"（约 {info['bytes'] / 1e6:.1f} MB；只删处理产物与 1D 产物，"
                 "下次出图重新积分）")

