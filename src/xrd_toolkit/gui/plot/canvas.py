"""画布辅助层：快照、文本保护、线样式恢复、blit、轴定位。

2026-10-21 从 plot_panels.py 原样搬出（只改结构，不动逻辑）。plot_panels
再导出，plot_views 与测试照旧经 gui_plot_panels 取用。
"""
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.gui.customize import _default_texts
from xrd_toolkit.gui.panel_state import _AUX_GID_PREFIX, _content


def _is_aux_line(line) -> bool:
    """这条线是不是背景扣除的辅助线（不是数据曲线）。"""
    gid = line.get_gid()
    return isinstance(gid, str) and gid.startswith(_AUX_GID_PREFIX)

def _data_lines(ax):
    """坐标轴上真正的数据曲线（排除背景扣除的辅助线与悬停点）。"""
    return [ln for ln in ax.lines if not _is_aux_line(ln)]

def _snapshot_canvas(ax):
    """重画前把会被 ax.clear() 抹掉的状态拍下来（Customize 保护用）。

    返回 (标题, x 标签, y 标签, 纵轴刻度, [(图例名,颜色,线型,线宽,
    标记), ...])。曲线只收有数据的（悬停圆点是空数据假线，不算），
    背景扣除的辅助线也不收——快照与回填都按线号索引，收进来会错位。
    """
    lines = [(line.get_label(), line.get_color(), line.get_linestyle(),
              line.get_linewidth(), line.get_marker())
             for line in _data_lines(ax) if len(line.get_xdata()) > 0]
    return (ax.get_title(), ax.get_xlabel(), ax.get_ylabel(),
            ax.get_yscale(), lines)

def _settle_scale(dock, cur_scale, param_log):
    """纵轴刻度记账：返回本次应使用的刻度名（与用户讨论定稿）。

    cur_scale = 重画前画布上的刻度（ax.clear() 会重置成 linear，须
    提前拍下）。规则：用户在 Customize 对话框改过刻度 → 以用户为准；
    除非参数里的 [对数纵轴] 又改过了（上次生效时记下的 _yscale_param
    与现在不同）→ 参数为准。首画无条件用参数值。
    """
    want = "log" if param_log else "linear"
    ours = getattr(dock, "_yscale_ours", None)
    ours_param = getattr(dock, "_yscale_param", None)
    if ours is None or ours_param is None or ours_param != param_log:
        final = want          # 首画 / 参数又改过：参数为准
    elif cur_scale != ours:
        final = cur_scale     # 用户在 Customize 改过：用户为准
    else:
        final = ours          # 维持上次（参数值或用户值）
    dock._yscale_ours = final
    dock._yscale_param = param_log
    return final

def _apply_text_guards(dock, ax, keep_title, keep_xlabel, keep_ylabel,
                       texts=None):
    """标题/轴标签保护：用户在 Customize 里改过的保留，其余照默认。

    texts = (默认标题, 默认 x 标签, 默认 y 标签)；None = 按面板的
    视图名查 _default_texts（1D/对比 = 积分图默认，2D/剖面/瀑布 =
    各自默认）。标题的"参数源"= 显示名（panel_display）：显示名没
    变 → 用户手改的标题以用户为准；显示名变过 → 参数为准（默认
    标题跟新名字）。轴标签没有参数源 → 用户改过一次就永远以用户
    为准。
    """
    if texts is None:
        view = dock.panel_key.split("|", 1)[0]
        texts = _default_texts(view, dock.panel_display)
    default_title, default_xlabel, default_ylabel = texts
    ours = getattr(dock, "_title_ours", None)
    ours_display = getattr(dock, "_title_display", None)
    if (ours is not None and keep_title != ours
            and ours_display == dock.panel_display):
        ax.set_title(keep_title)   # 用户为准（不更新 ours：它仍是默认基准）
    else:
        ax.set_title(default_title)
        dock._title_ours = default_title
        dock._title_display = dock.panel_display
    ours = getattr(dock, "_xlabel_ours", None)
    if ours is not None and keep_xlabel != ours:
        ax.set_xlabel(keep_xlabel)
    else:
        ax.set_xlabel(default_xlabel)
        dock._xlabel_ours = default_xlabel
    ours = getattr(dock, "_ylabel_ours", None)
    if ours is not None and keep_ylabel != ours:
        ax.set_ylabel(keep_ylabel)
    else:
        ax.set_ylabel(default_ylabel)
        dock._ylabel_ours = default_ylabel
    # 版式：只在"内容变了"时重排一次（标题/轴标签刚改完），随后把布局
    # 引擎卸掉。matplotlib 的 tight 引擎**每次绘制**都会重跑，单块面板
    # 实测 24 ms（一次重绘 54 ms 里的 45%）——缩放/平移根本不改版式，
    # 那笔钱是白付的（用户反馈"放大非常卡"）。下次内容重绘（[应用]/
    # 重算/新开图）会再进来重排一次 ✓
    fig = ax.figure
    if fig.get_layout_engine() is not None:
        fig.tight_layout()
        fig.set_layout_engine("none")

def _restore_line_styles(ax, old_lines, restore_color=True):
    """把重画前拍下的曲线样式原样套回新画的曲线（Customize 保护：
    用户改过的线型/线宽/标记/图例名不被重画盖掉）。数量变了
    就按顺序对前面几条（新多的曲线用默认样式）。

    restore_color=False = 1D/剖面/对比视图：颜色由「渐变色系」参数
    管（参数本身就是用户改色的入口，重画时按新参数着色；旧快照的
    颜色套回会把换色系的 [应用] 打回原形。2026-10-13 起逐条自定义
    色退休，保护对象只剩线型/线宽/标记；瀑布的 χ 渐变色是程序自定、
    与色系参数无关，仍走 restore_color=True）。
    """
    for i, line in enumerate(_data_lines(ax)):
        if i >= len(old_lines):
            break
        label, color, ls, lw, marker = old_lines[i]
        line.set_label(label)
        if restore_color:
            line.set_color(color)
        line.set_linestyle(ls)
        line.set_linewidth(lw)
        line.set_marker(marker)

def _blit_take(dock, ax) -> dict:
    """起一次"低成本重画"会话：把当前帧缓存下来，拖动期只贴变化的部分。

    只给**框选**用（拖动期视图不变，只多一个选框 → 贴图与真画法视觉
    等价）；平移不走这条路，原因见 _pan_press。

    为什么需要（2026-09-24 用户"按照最快来优化"）：一次完整重绘 28 ms
    （剖析：文字排版 22% + Line2D 13% + 光栅化 4%，其余是 mpl 每个
    artist 的属性开销——没有单点能砍），一次拖动的每一个鼠标移动都付
    这份钱就是"卡"。拖动期真正变的只有两样：选框（框选）或整块画面
    的平移（平移），所以缓存一帧、每帧只贴这部分，松手再画一次完整
    的（那时刻度/文字才需要重算）。

    返回会话 dict 或空 dict（后端不支持 → 调用方退回整帧重绘）。
    会话在**任何一次完整绘制后自动作废**（挂在 draw_event 上，见
    _build_canvas_panel）：拖动期间后台算完一张别的图会触发重绘，
    那时缓存的背景已经过期，拿它贴会把画面贴回旧样子。
    """
    canvas = ax.figure.canvas
    if not all(hasattr(canvas, name)
               for name in ("copy_from_bbox", "restore_region", "blit")):
        return {}
    canvas.draw()                      # 背景必须和屏幕上的当前帧一致
    return {"canvas": canvas, "bg": canvas.copy_from_bbox(ax.figure.bbox),
            "ax": canvas.copy_from_bbox(ax.bbox)}

def _blit_drop(window: QMainWindow, key: str) -> None:
    """作废拖动的位图会话（整帧重绘后缓存背景就过期了）。"""
    dock = window.plot_docks.get(key)
    if dock is not None:
        dock._blit = None

def _blit_box(dock, ax, patch) -> None:
    """框选拖动期的一帧：贴回背景 → 只画选框 → 上屏（几毫秒）。"""
    session = getattr(dock, "_blit", None)
    if not session:
        ax.figure.canvas.draw_idle()   # 没有会话（被重绘打断）：老实整帧画
        return
    canvas = session["canvas"]
    canvas.restore_region(session["bg"])
    ax.draw_artist(patch)
    canvas.blit(ax.figure.bbox)

def _panel_axes(dock):
    """面板的主坐标轴（各视图挂在画布上的名字不同，依次试）。

    2D/热图面板还有颜色条的轴，所以不能用 figure.axes[0] 蒙。
    """
    content = _content(dock)
    canvas = getattr(content, "canvas", None)
    for name in ("axes_1d", "axes_2d", "axes_profile", "axes_waterfall",
                 "axes_heat"):
        for holder in (canvas, content):
            ax = getattr(holder, name, None) if holder is not None else None
            if ax is not None:
                return ax
    return None

def _note_hover(window: QMainWindow, key: str, _event=None) -> None:
    """记下"鼠标最后停留过的面板"——Home 键认它。

    为什么不认"高亮面板"：用户的眼睛在哪张图上，手指就在哪张图上滚轮，
    但高亮面板是**点选**定的（点一下标题栏才换），常常不是他正在看的那张。
    """
    window._hover_panel_key = key

