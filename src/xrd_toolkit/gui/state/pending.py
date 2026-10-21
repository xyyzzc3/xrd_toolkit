"""未应用参数账本：哪些控件要按 [应用] 才生效、灰字刷新、快照回放。

2026-10-21 从 panel_state.py 原样搬出（只改结构，不动逻辑）。
panel_state 再导出，builder/refresh 照旧经 gui_panel_state 取用。
"""
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.gui.state.base import _set_widget_value, _widget_value


# ── 「未应用」：改了要按一下才生效的控件（用户 2026-09-30 的"丙"方案） ──
# 用户原话："参数页显示本图的参数，就是上方图片名称的本图参数。如果用户改了，
# 添加一个灰字未应用来区分，切图再切回来保持"。三件事：
#   ① 灰字：当前编辑对象有"改了还没生效"的改动时，坞顶那个灰字亮着；
#   ② 记住：改动**按图**记（`dock.pending_params`），切走再切回来原样还在
#      （改之前的行为是：切图时回放直接把用户填的值冲掉，什么提示都没有）；
#   ③ 生效即清：快照一旦追上控件（[重算这张图] 会写快照），差集自然为空、
#      灰字自己灭——不需要谁专门去"清 pending"。
# 表里只放**要按一下才生效**的控件。即改即生效的不放——显示参数
# 2026-10-08 起**全部**即改即画（对比四参数 10-02、热图三项与 1D 显示
# 10-07、2D 三项 / 剖面角度 / 热图范围 10-08，见 app 与 plot_views 的
# _refresh_* 接线），本表只剩「数据」一组：2θ 范围 / 点数要等**下一次
# 计算**才被用掉，"改了还没算"是真实且必须提示的状态。
_PENDING_GROUPS = {
    "数据": ("2θ 下限 (°)", "2θ 上限 (°)", "输出点数"),
}

def _pending_names() -> tuple:
    """表里所有控件的名字并成一条（按表里的顺序）。"""
    return tuple(name for names in _PENDING_GROUPS.values() for name in names)

def _group_of(name: str):
    """这个键属于哪一组（数据 / 图像；不在名单里返回 None）。"""
    for group, names in _PENDING_GROUPS.items():
        if name in names:
            return group
    return None

def _note_user_edit(window: QMainWindow, name: str) -> None:
    """用户动了某个"要按一下才生效"的控件 → 这组的值又变回"没被用掉"。

    配套 _note_params_consumed：两个标记一起决定"这次改动算不算未应用"
    （见 _capture_pending 的说明——"填数 → 出图"必须不算）。
    """
    consumed = dict(getattr(window, "_pending_consumed", None) or {})
    group = _group_of(name)
    if group is not None:
        consumed[group] = False
    window._pending_consumed = consumed
    _refresh_pending_labels(window)

def _note_params_consumed(window: QMainWindow, group: str) -> None:
    """这一组控件的当前值刚被**用掉**（算了/重算了一张图、应用了显示设置）。

    用掉之后它们就不再是"未应用"：用户填 2.5 出一张新图，那是**指令**，
    不是"上一张图的未生效改动"——不区分的话，切回上一张图时它会把刚填的值
    当成那张图的待办冒出来（TestParamSnapshot 那两条就是这么挂的）。
    """
    consumed = dict(getattr(window, "_pending_consumed", None) or {})
    consumed[group] = True
    window._pending_consumed = consumed

def _pending_diff(window: QMainWindow, dock) -> dict:
    """当前控件值 vs 该面板**已生效**快照的差（只算上面那张表里的键）。

    "程序自己填的值不算用户改动"的例外（探针实测踩到的，不排掉的话"未
    应用"会永远亮着）：带空占位项的下拉（`currentData()` 是 None）与快照
    里的 ""（默认表的写法）是同一个意思——直接比会永远不等。快照里没有
    这个键（老快照）同样不算改动：宁可少报，也别误报。
    （"自动开关算完填进上下限"那条例外随「图像」组 2026-10-08 退休。）
    """
    snap = getattr(dock, "params_snapshot", None) or {}
    out = {}
    for name in _pending_names():
        if name not in snap:
            continue
        w = window.params.get(name)
        if w is None:
            continue
        value = _widget_value(w)
        same = (value == snap[name]) or (value is None and snap[name] == "")
        if not same:
            out[name] = value
    return out

def _refresh_pending_labels(window: QMainWindow) -> None:
    """按"当前编辑对象有没有未应用的改动"开关那个灰字（没控件就跳过）。"""
    labels = getattr(window, "pending_labels", None)
    if not labels:
        return
    dock = window.plot_docks.get(window.focus_panel)
    diff = _pending_diff(window, dock) if dock is not None else {}
    for group, label in labels.items():
        label.setVisible(any(name in diff for name in _PENDING_GROUPS[group]))

def _capture_pending(window: QMainWindow, key) -> None:
    """切走之前：把"改了还没生效"的控件值记给**正要离开**的那张图。

    用户 2026-09-30："切图再切回来保持"——不记的话回放会把它们冲掉。
    记在面板对象上（不是全局）：面板关了它就跟着一块儿忘掉，与
    "关闭即遗忘"的既有口径一致。

    **刚被用掉的那组不记**（`_pending_consumed`）：用户填 2θ=2.5 按 [出图]
    出一张新图，2.5 是**指令**、不是"上一张图的未生效改动"——记下来的话，
    切回上一张图会凭空冒出一个"未应用"（TestParamSnapshot 两条实测）。
    """
    dock = window.plot_docks.get(key) if key else None
    if dock is None:
        return
    consumed = getattr(window, "_pending_consumed", None) or {}
    dock.pending_params = {
        name: value
        for name, value in _pending_diff(window, dock).items()
        if not consumed.get(_group_of(name))
    } or None

def _overlay_pending(window: QMainWindow, dock) -> None:
    """切进来之后：把该面板自己的未应用改动铺回控件（在快照回放**之后**调）。

    挂 _param_replaying 旗标：写控件会触发那些"改了即重画"的信号，
    不挡的话这一铺会被当成用户改动。
    """
    pend = getattr(dock, "pending_params", None)
    if not pend:
        return
    prev = getattr(window, "_param_replaying", False)
    window._param_replaying = True
    try:
        for name, value in pend.items():
            w = window.params.get(name)
            if w is not None:
                _set_widget_value(w, value)
    finally:
        window._param_replaying = prev

