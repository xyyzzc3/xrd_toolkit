"""单文件视图绘制：1D/2D/剖面/瀑布、背景叠加、通用视图复位。

2026-10-21 从 plot_views.py 原样搬出（只改结构，不动逻辑）。
plot_views 再导出，测试与探针照旧经 gui_plot_views 取用。
"""
import numpy as np
from matplotlib import cm
from matplotlib.colors import LogNorm
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.gui.image_utils import downsample_max
from xrd_toolkit.gui.panel_state import (
    _AUX_GID_PREFIX, _auto_contrast_values, _auto_y_range, _content,
    _gradient_color, _panel_param, _param_box_set, _proc_curve, _proc_params,
    _proc_settings)
from xrd_toolkit.gui.peak_view import draw_peak_markers, fill_peak_table
from xrd_toolkit.gui.fit_view import draw_fit_overlays
from xrd_toolkit.gui.plot.canvas import (
    _apply_text_guards, _data_lines, _restore_line_styles, _settle_scale,
    _snapshot_canvas)
from xrd_toolkit.gui.plot.interaction import _connect_axis_sync, _refresh_home
from xrd_toolkit.services import process
from xrd_toolkit.services.stacking import row_step


def _draw_bg_overlay(window: QMainWindow, dock, ax, tth, intensity, base,
                     path) -> None:
    """画背景扣除的辅助线：基线（点线）+ 原始曲线（虚线灰）+ 锚点标记。

    全部带 bg: 前缀的 gid——它们不是"曲线"，_snapshot_canvas /
    _restore_line_styles / _hover_motion 都按线号或最近距离处理线条，
    辅助线混进去会让样式回填错位、悬停点乱跳。
    锚点直接从 window.bg_anchors 重建（不存 dock 属性），所以面板
    弹出/收回、参数重画都不会丢。

    锚点圆圈在**手动锚点**与**自动基线（自动 + 锚点校正）**两种模式下
    都画：后者的锚点同样参与计算（改的是基线的电平，见
    background._correct_with_anchors），点上去却没有圆圈反馈——用户
    2026-09-27 报的"小圆圈没了"就是这个门槛漏了 auto 模式（335db7b
    放宽了拾取与计算，忘了放宽画图）。
    """
    ax.plot(tth, base, linestyle=":", lw=1.0, color="#1baf7a",
            gid=_AUX_GID_PREFIX + "baseline", label="Baseline")
    if _panel_param(window, dock, "背景显示原始", True):
        ax.plot(tth, intensity, linestyle="--", lw=0.6, color="#999999",
                gid=_AUX_GID_PREFIX + "raw", label="Raw")
    anchors = getattr(window, "bg_anchors", {}).get(str(path), [])
    if anchors and _panel_param(window, dock, "背景扣除模式",
                                "off") in ("anchor", "auto"):
        xs = [p[0] for p in anchors]
        ys = [p[1] for p in anchors]
        ln = ax.plot(xs, ys, "o", ms=6, mfc="none", mec="#e34948", mew=1.4,
                     gid=_AUX_GID_PREFIX + "anchor", label="Anchors")[0]
        ln.set_zorder(6)

def _bg_path_of(dock):
    """面板对应的文件路径（锚点按路径存；路径是唯一的，显示名可能重名）。"""
    return getattr(dock, "panel_file", None)

def _update_smooth_points(window: QMainWindow, dock) -> None:
    """把"平滑窗口 ▲° ≈ 几个点"的提示填进参数坞（教学用，不参与计算）。

    窗口按度给（换点数不失效），但真正做平均的是点——两个数字摆在一起，
    "窗口取到峰宽量级会明显削峰"才有可操作的手感。面板还没算过就留空。
    """
    lbl = getattr(window, "smooth_points_lbl", None)
    if lbl is None:
        return
    deg = float(window.params["平滑窗口 (°)"].value() or 0.0)
    tth = getattr(dock, "last_tth", None) if dock is not None else None
    if tth is None or deg <= 0:
        lbl.setText("")
        return
    from xrd_toolkit.services.background import _window_to_points
    n = _window_to_points(tth, deg)
    lbl.setText(f"≈ {2 * n + 1} 个采样点")

def _draw_1d(window: QMainWindow, dock, tth, intensity) -> None:
    """在指定的 1D 面板画出积分曲线（只允许主线程调用）。

    x 轴范围：优先该面板快照里的"视图 2θ 范围"（缩放/平移实时
    写回的显示窗口，只看图不参与计算）；没被用户动过（None）就
    跟随数据组的积分 2θ 上下限。显示样式跟随该面板自己的 1D 显示
    参数（对数纵轴 / 纵轴范围）——每张图各记各的，画哪张就用哪张
    的设置（_panel_param），不看参数坞控件当前值：控件此刻可能正
    显示别的面板的设置。积分完成与图像参数 [应用] 都会走这里——
    后者只改显示、不重新积分。

    全程举着 _setting_limits 旗标：画图里 set_xlim/set_ylim/clear
    引发的范围变化是程序自己设的，不算用户改动，不触发同步写回
    （否则 ax.clear() 会先把范围重置成 (0,1)，同步会写回错值）。
    """
    ax = _content(dock).axes_1d
    # Customize 保护：clear 会把标题/标签/刻度/曲线全抹掉，先拍下现状
    keep_title, keep_xlabel, keep_ylabel, keep_scale, old_lines = \
        _snapshot_canvas(ax)
    # 背景扣除：在**绘制时**扣，缓存里的原始曲线不动。重绑 intensity，
    # 后面的纵轴自动范围（_auto_y_range）跟着用扣除后的数据——图与范围
    # 必须同一个口径
    path = _bg_path_of(dock)
    raw = intensity          # 辅助线里的"原始曲线"要的是未扣的那份
    tth, intensity, base = _proc_curve(window, dock, path, tth, intensity)
    # 裁剪挖空**也作用于两条辅助线**（2026-10-08 用户："剪完……扣完
    # 背景的峰看不清。两条线都在一张图，保证都能看清"）：被剪的区间三条
    # 线一起空着——否则未剪的"原始"仍顶着大峰的量程，纵轴自动范围掉不
    # 下来、结果线被压扁在底部。平滑不套（虚线本来就是"扣背景前"的对照）。
    cuts = (_proc_settings(window, dock, path) or {}).get("cut_ranges") or []
    if cuts:
        hide = np.zeros(np.shape(tth), dtype=bool)
        for lo, hi in cuts:
            hide |= (tth >= lo) & (tth <= hi)
        raw = np.where(hide, np.nan, np.asarray(raw, dtype=float))
        if base is not None:
            base = np.where(hide, np.nan, np.asarray(base, dtype=float))
    # 旗标 save/restore：重入本函数时内层恢复外层取值，不许踩灭外层保护
    # （2026-10-08"裁剪后纵轴自动被关掉"的链条里就有这一环）
    prev_limits = getattr(window, "_setting_limits", False)
    window._setting_limits = True
    try:
        ax.clear()
        # 单曲线颜色 = 渐变色系的基色（默认蓝，与对比第 1 条同色）
        family = _panel_param(window, dock, "渐变色系", "蓝")
        ax.plot(tth, intensity, color=_gradient_color(family, 0, 1),
                lw=0.8)
        if base is not None:
            _draw_bg_overlay(window, dock, ax, tth, raw, base, path)
        lo = _panel_param(window, dock, "视图 2θ 下限 (°)", None)
        hi = _panel_param(window, dock, "视图 2θ 上限 (°)", None)
        # 这个窗口是用户缩放的（缩放/平移写回快照）还是按参数算的？
        # 前者意味着"这次重画只是把用户缩放过的窗口再套一遍"——那就不许
        # 把它当成"这张图最初的样子"（见 _refresh_home；2026-09-27 的
        # Home 失灵就是这个：缩放后每点一次锚点重画一次，第二次就把
        # 缩放窗口写成了"家"）
        zoomed = lo is not None and hi is not None and lo < hi
        if not zoomed:
            lo = _panel_param(window, dock, "2θ 下限 (°)", 1.0)
            hi = _panel_param(window, dock, "2θ 上限 (°)", 8.0)
        if lo < hi:
            ax.set_xlim(lo, hi)
        # 视图范围框显示"正在看的窗口"——只有画的正是焦点面板才填
        if window.plot_docks.get(window.focus_panel) is dock:
            _param_box_set(window, "视图 2θ 下限 (°)", lo)
            _param_box_set(window, "视图 2θ 上限 (°)", hi)
        # 对数纵轴：弱峰"抬起来"（XRD 行规，主峰与弱峰强度差几个数量级）。
        # 刻度以 Customize 用户改动为准时（_settle_scale），纵轴范围也
        # 按生效的刻度算（eff_log），避免对数轴拿到线性分位画不出来
        log_y = _panel_param(window, dock, "对数纵轴", False)
        scale = _settle_scale(dock, keep_scale, log_y)
        eff_log = (scale == "log")
        if scale != "linear":
            ax.set_yscale(scale)
        # 纵轴范围：自动 = 按曲线 1%/99.9% 分位；手动 = 手填上下限。
        # 对数轴画不出 ≤0 的范围，手动值也兜底抬高。自动模式把算出的
        # 区间填进置灰输入框（只读展示"程序正在用的区间"）——只有画的
        # 正是焦点面板才填：否则会覆盖用户正在看的别面板参数
        if _panel_param(window, dock, "纵轴自动", True):
            # 纵轴范围按**未扣背景的原始曲线**定（扣后曲线只补上界），
            # 但**裁剪区间要从里面挖掉**——于是：调背景（锚点/窗口）时框
            # 不动、只有曲线在框里往下走；裁掉巨峰时框跟着缩（裁剪的本意
            # 就是"剪掉它，剩下的才看得见"）。
            # 按扣后曲线算下界的话，每加一个锚点整张图都重定一次比例，
            # 看着像"图在动"（用户 2026-09-26："主要是图会动来动去"；
            # 实测锚点 1 个时 297~3050 一跳跳到 -1252~1500）。
            # 下界兜到 0 附近（留 2% 框高）：扣完的曲线落在 0 上下，
            # 得看得见它，轻微过扣（负值）也露得出来；对数轴画不出 ≤0，
            # 不兜
            ref = np.asarray(raw, dtype=float)
            cut = _proc_params(window, dock, path).get("cut_ranges")
            if cut:
                ref = process.cut_ranges(tth, ref, cut)
            ylo, yhi = _auto_y_range(ref, eff_log)
            if base is not None:
                _, hi_proc = _auto_y_range(np.asarray(intensity, dtype=float),
                                           eff_log)
                yhi = max(yhi, hi_proc)
                if not eff_log:
                    ylo = min(ylo, -0.02 * abs(yhi))
            if _panel_param(window, dock, "显示峰位", False):
                # 给峰位标签留头顶空间（2026-10-24）：自动范围按 99.9%
                # 分位算，窄而高的峰可能整个在框外（实测 LMFP 2.98° 峰
                # 2407 counts、99.9% 只到 ~2020）——先把最高点抬进来，
                # 再加 18% 给标签。否则最高的那根永远没有标签。
                finite = np.asarray(ref, dtype=float)
                finite = finite[np.isfinite(finite)]
                if finite.size:
                    top = float(finite.max())
                    if eff_log:
                        yhi = max(yhi, top) * 1.6
                    else:
                        yhi = max(yhi, top)
                        span = max(yhi - ylo, 1e-9)
                        head = 0.18 * span
                        # 小面板里 18% 可能不够一个竖排标签（实测 172 px
                        # 高的框只让出 31 px，标签要 ~33 px）——按像素兜底
                        if ax.bbox.height > 20:
                            head = max(head,
                                       40.0 * span / float(ax.bbox.height))
                        yhi += head
            if window.plot_docks.get(window.focus_panel) is dock:
                # 程序回填必须挂旗标（同 10-08 对比度那对）：裸 setValue 会触发
                # 即改即画钩子（_refresh_1d_display）、重入一次本函数——重入的
                # finally 把 _setting_limits 踩灭后，本函数稍后的 set_ylim 就被
                # _on_ylim_changed 当成"用户动过纵轴"、把「纵轴自动」悄悄关掉
                # （用户 2026-10-08 报"裁剪后扣背景的峰看不清"的真凶）
                _param_box_set(window, "纵轴下限", ylo)
                _param_box_set(window, "纵轴上限", yhi)
        else:
            ylo = _panel_param(window, dock, "纵轴下限", 1.0)
            yhi = _panel_param(window, dock, "纵轴上限", 100000.0)
            if eff_log:
                ylo = max(ylo, 1e-6)
        if ylo < yhi:
            ax.set_ylim(ylo, yhi)
        _apply_text_guards(dock, ax, keep_title, keep_xlabel, keep_ylabel)
        # 颜色跟"渐变色系"参数走（参数本身就是用户改色的入口），
        # 线型/线宽/标记照旧保护
        _restore_line_styles(ax, old_lines, restore_color=False)
        ax.grid(alpha=0.3)
        peaks = draw_peak_markers(window, dock, ax, tth, intensity)
        # 拟合曲线叠在数据曲线之上（只画选中那根；「叠加全部」才全画）
        draw_fit_overlays(window, dock, ax, tth, intensity)
        if window.plot_docks.get(window.focus_panel) is dock:
            fill_peak_table(window, dock, peaks)
        _content(dock).draw()
    finally:
        window._setting_limits = prev_limits
    _connect_axis_sync(window, dock.panel_key)   # ax.clear() 清掉了回调（见 helper 注释）
    if zoomed:
        # 这次画的是"用户缩放过的窗口"，不是这张图本来的样子 → 别更新家
        # （Home 要能回到最初的比例；见上面 zoomed 的说明）
        dock._view_from_gesture = True
    _refresh_home(dock, ax)   # 程序重画 = 新"家"（见 helper 注释）
    dock.figure_saved = False   # 重画 = 新内容还没存盘

def _apply_plain_view(window: QMainWindow, dock, ax) -> bool:
    """像素轴面板（2D / 剖面 / 瀑布）按快照摆回视图窗口；返回"是不是缩放窗口"。

    1D / 对比 / 热图 走的是**语义键**（视图 2θ 范围 + 纵轴自动/上下限）；
    这三张图的轴是像素或行号，没有那样的参数，所以用一对通用键
    （`视图 x 范围` / `视图 y 范围`，由 _on_xlim_changed/_on_ylim_changed 写）。

    用户 2026-09-28："别的图的放大检查一下"——它们原本**从不记**缩放，
    于是任何一次重画（[应用]、换色图/对比度）都被自动缩放顶掉。
    """
    zoomed = False
    for name, setter in (("视图 x 范围", ax.set_xlim),
                         ("视图 y 范围", ax.set_ylim)):
        rng = _panel_param(window, dock, name, None)
        try:
            lo, hi = float(rng[0]), float(rng[1])
        except (TypeError, IndexError, ValueError):
            continue        # 没有 / 坏了：这一维按数据自动
        if lo < hi:
            setter(lo, hi)
            zoomed = True
    return zoomed

def _draw_2d(window: QMainWindow, dock, image) -> None:
    """在指定的 2D 面板画出衍射图：对数色标 + 颜色条 + 对比度参数
    + 束心十字（勾上"显示束心"才画，**默认不画**）。

    样式对齐 CLI view_diffraction / 校准图：magma + LogNorm、自动
    对比度 1%/99.9% 分位（下限兜底 1.0）、origin="lower"（数组行
    序与 beam_center 的行序一致）。束心 = 当前几何配置的
    beam_center（(行, 列)，画图取 (x=列, y=行)）。对比度参数读该
    面板自己的快照：自动模式把算出的区间填进置灰输入框（同 1D
    纵轴的只读展示语义，只填焦点面板）；手动模式手填上下限。
    """
    ax = _content(dock).axes_2d
    # masked 2D 产物：数据是 MaskedArray（原始像素 + 它自己的 mask）。
    # 显示原始像素 + 红色叠加；mask 不参与自动对比度。
    panel_mask = (np.ma.getmaskarray(image)
                  if np.ma.isMaskedArray(image) else None)
    data = np.ma.getdata(image) if np.ma.isMaskedArray(image) else image
    keep_title, keep_xlabel, keep_ylabel, keep_scale, old_lines = \
        _snapshot_canvas(ax)
    # 旗标 save/restore：重入本函数时内层恢复外层取值，不许踩灭外层保护
    # （2026-10-08"裁剪后纵轴自动被关掉"的链条里就有这一环）
    prev_limits = getattr(window, "_setting_limits", False)
    window._setting_limits = True
    try:
        ax.clear()
        auto = _panel_param(window, dock, "自动对比度", True)
        if auto:
            contrast = (np.where(panel_mask, np.nan, data)
                        if panel_mask is not None else data)
            lo, hi = _auto_contrast_values(contrast)
            vmin = max(1.0, lo)
            vmax = max(hi, vmin * 10.0)
            if window.plot_docks.get(window.focus_panel) is dock:
                # 这对框 2026-10-08 起接了即改即画：程序回填必须挂旗标
                # （_param_box_set），否则每画一次 2D 都反触发一轮重画
                _param_box_set(window, "对比度下限", vmin)
                _param_box_set(window, "对比度上限", vmax)
        else:
            vmin = _panel_param(window, dock, "对比度下限", 1.0)
            vmax = _panel_param(window, dock, "对比度上限", 100000.0)
            vmin = max(vmin, 1e-12)
            if vmax <= vmin:
                vmax = vmin * 10.0   # 手填区间不合法时兜底（防 LogNorm 报错）
        # 色图按面板快照（Customize 里改；缺省 magma——与 CLI 一致）
        im = ax.imshow(data, cmap=_panel_param(window, dock, "2D 色图", "magma"),
                       norm=LogNorm(vmin=vmin, vmax=vmax),
                       origin="lower")
        # Mask 2D 产物自带的 mask：半透明红叠在原图上（2026-10-27）。
        # 大图先做「块内有任一被遮」降采样——隔点取样会漏掉奇坐标上的
        # 孤立坏点；extent 要乘回 ds，块心才对得上原像素中心。
        mask = panel_mask
        if mask is not None and np.shape(mask) == data.shape:
            ds = 1 if mask.size <= 1_500_000 else 2
            small = downsample_max(mask, ds)
            rgba = np.zeros((*small.shape, 4), dtype=np.uint8)
            rgba[..., 0] = 255
            rgba[..., 3] = 89 * small          # 0.35 × 255
            hh, ww = small.shape
            ax.imshow(rgba, extent=(-0.5, ww * ds - 0.5,
                                    -0.5, hh * ds - 0.5),
                      origin="lower", interpolation="nearest", zorder=5)
        ax.set_aspect("equal")
        # 颜色条（作业规格"带颜色条"）：ax.clear() 不清 colorbar（它是
        # 图上的另一个坐标系）。只建一次、之后 update_normal 复用
        # ——remove+重建每做一次，fig.colorbar 就把主坐标轴再让出
        # 20% 宽度且 remove 不退还（累积缩小，教训 13）；复用时
        # 几何只算一次，数据/norm/色图跟着新 im 走。挂在 dock 上：
        # 关面板随 figure 一起销毁，不用清理
        cb = getattr(dock, "_colorbar_2d", None)
        if cb is None:
            dock._colorbar_2d = ax.figure.colorbar(im, ax=ax)
        else:
            cb.update_normal(im)
        dock._colorbar_2d.ax.tick_params(labelsize=7)
        # 束心十字（当前几何配置的直射束落点）：**默认不画**（用户
        # 2026-10-09："实现默认 2D 图是不显示束心的"），要核对束心位置
        # 就在图像参数里勾上"显示束心"（2026-10-01 起有这一个开关，
        # 当时默认画）。读面板快照（每张图各记各的），与色图/对比度同一套
        if _panel_param(window, dock, "显示束心", False):
            cy, cx = window.config["beam_center"]
            ax.plot([cx], [cy], "+", color="white", ms=10, mew=1.2)
        zoomed = _apply_plain_view(window, dock, ax)   # 摆回缩放的窗口
        _apply_text_guards(dock, ax, keep_title, keep_xlabel, keep_ylabel)
        _content(dock).draw()
    finally:
        window._setting_limits = prev_limits
    _connect_axis_sync(window, dock.panel_key, ax)   # ax.clear() 清掉了回调
    if zoomed:
        dock._view_from_gesture = True   # 缩放的窗口不是"家"（同 _draw_1d）
    _refresh_home(dock, ax)   # 程序重画 = 新"家"（见 helper 注释）
    dock.figure_saved = False   # 重画 = 新内容还没存盘

def _draw_profile(window: QMainWindow, dock, t, intensity) -> None:
    """在指定的剖面面板画出强度剖面（x = 到束心的带符号距离）。

    与 _draw_1d 同套路但更简：x 轴是像素距离不是 2θ，没有视图
    2θ 范围的概念（范围写回只连纵轴）；纵轴显示参数（对数纵轴 /
    纵轴自动 / 上下限）读该面板自己的快照，缩放/平移写回纵轴窗口。
    """
    ax = _content(dock).axes_profile
    keep_title, keep_xlabel, keep_ylabel, keep_scale, old_lines = \
        _snapshot_canvas(ax)
    # 旗标 save/restore：重入本函数时内层恢复外层取值，不许踩灭外层保护
    # （2026-10-08"裁剪后纵轴自动被关掉"的链条里就有这一环）
    prev_limits = getattr(window, "_setting_limits", False)
    window._setting_limits = True
    try:
        ax.clear()
        family = _panel_param(window, dock, "渐变色系", "蓝")
        ax.plot(t, intensity, color=_gradient_color(family, 0, 1),
                lw=0.8)
        log_y = _panel_param(window, dock, "对数纵轴", False)
        scale = _settle_scale(dock, keep_scale, log_y)
        eff_log = (scale == "log")
        if scale != "linear":
            ax.set_yscale(scale)
        if _panel_param(window, dock, "纵轴自动", True):
            ylo, yhi = _auto_y_range(intensity, eff_log)
            if window.plot_docks.get(window.focus_panel) is dock:
                # 同 _draw_1d：程序回填挂旗标，否则触发即改即画钩子重入本函数
                _param_box_set(window, "纵轴下限", ylo)
                _param_box_set(window, "纵轴上限", yhi)
        else:
            ylo = _panel_param(window, dock, "纵轴下限", 1.0)
            yhi = _panel_param(window, dock, "纵轴上限", 100000.0)
            if eff_log:
                ylo = max(ylo, 1e-6)
        if ylo < yhi:
            ax.set_ylim(ylo, yhi)
        # x 是像素距离、没有强度/2θ 那样的语义键 → 只取通用键那一半
        rng = _panel_param(window, dock, "视图 x 范围", None)
        try:
            xlo, xhi = float(rng[0]), float(rng[1])
        except (TypeError, IndexError, ValueError):
            xlo = xhi = None
        zoomed = xlo is not None and xlo < xhi
        if zoomed:
            ax.set_xlim(xlo, xhi)
        _apply_text_guards(dock, ax, keep_title, keep_xlabel, keep_ylabel)
        # 颜色跟"渐变色系"参数走（参数本身就是用户改色的入口），
        # 线型/线宽/标记照旧保护
        _restore_line_styles(ax, old_lines, restore_color=False)
        ax.grid(alpha=0.3)
        _content(dock).draw()
    finally:
        window._setting_limits = prev_limits
    # x 也要接（写通用键"视图 x 范围"）：不接的话剖面横向的缩放白做
    # （用户 2026-09-28："别的图的放大检查一下"；y 一直是接的）
    _connect_axis_sync(window, dock.panel_key, ax=ax, sync_x=True)
    if zoomed:
        dock._view_from_gesture = True   # 缩放的窗口不是"家"
    _refresh_home(dock, ax)
    dock.figure_saved = False

def _chi_tick_labels(ax, chi) -> list:
    """瀑布图的 y 刻度标签：36 个 χ 值挤在矮面板上会叠成一团，按高度抽稀。

    面板高度按 英寸 × dpi 估（不用渲染器：画的时候窗口还没上屏，取不到
    真实像素高），每个标签留 ~9 px；放得下就全标（CLI 那张 12×8 英寸的
    36 个全在），放不下就每隔 k 个标一个——**抽掉的标签留空串**，刻度线
    还在，读数靠悬停（曲线名仍是各自的 χ）。

    **首尾锚定**（2026-10-12，用户把 χ 刻度打开后问"155-175 为什么是
    空的"）：抽稀原先从底部起每 k 行标一个，最上面那行常落在没标的一档
    ——看起来像"轴到 155° 就没了、上面是空的"（其实行行都有曲线）。现在
    最下一行与最上一行**永远有数字**，抽稀只发生在中间；空着的行 = 没标
    数字，行与行固定差 10°（every=2 时没标的那行 = 相邻已标数字 ±10°）。
    """
    n = len(chi)
    if not n:
        return []
    h_px = float(ax.figure.get_size_inches()[1]) * float(ax.figure.dpi)
    every = max(1, int(np.ceil(n * 9.0 / max(h_px * 0.8, 1.0))))
    return [f"{float(c):.0f}°" if (k % every == 0 or k == n - 1) else ""
            for k, c in enumerate(chi)]

def _draw_waterfall(window: QMainWindow, dock, tth, i2d, chi) -> None:
    """在指定的瀑布面板画出 36 扇区堆叠瀑布。

    画法（2026-09-26 晚按用户口径定了两处）：
      - **行距统一**：所有行同一个行高（= 第二高的行峰 × 0.7，见
        services/stacking），不再按各扇区自己的峰值定行高——那等于把每行
        都缩到各自的高度，强弱没法横向比（用户："不要按照各自的最高峰
        归一化，所有的图"）；也不按全场最大——一个特别强的扇区会把其余行
        压成平线（用户 2026-10-02："行间距小一点，让峰明显一点"）。
      - **处理链照跑**：背景用**扇区均值**估一条共同基线、所有扇区减同
        一条（不逐扇区各估各的——空气散射在方位角上均匀，逐扇区各扣会
        把"哪个扇区强"这个瀑布图存在的理由抹平）；**平滑与裁剪逐扇区
        跑**（[处理] 页那两项），于是被裁掉的巨峰在所有行上一起消失、
        剩下的弱扇区才看得清（用户："瀑布图也是在处理后画，去掉无效的
        峰就看得清了"）。

    每条曲线画到自身第一个 0（被探测器切掉的位置）——右端阶梯即截断
    几何；y 刻度 = 各扇区 χ 基线（**默认就画**——2026-10-12 用户拍板
    "甲"：那是一轴的刻度不是"名字"，36 行唯一的分辨办法，抽稀见
    _chi_tick_labels），曲线名 = 扇区 χ（悬停读数用，无图例）。
    纵轴显示参数不适用（行偏移由数据决定），范围写回不连（同 2D）。
    """
    ax = _content(dock).axes_waterfall
    keep_title, keep_xlabel, keep_ylabel, keep_scale, old_lines = \
        _snapshot_canvas(ax)
    # 旗标 save/restore：重入本函数时内层恢复外层取值，不许踩灭外层保护
    # （2026-10-08"裁剪后纵轴自动被关掉"的链条里就有这一环）
    prev_limits = getattr(window, "_setting_limits", False)
    window._setting_limits = True
    try:
        ax.clear()
        raw2d = np.asarray(i2d, dtype=float)
        # 背景：共同基线，理由见 docstring。
        # 扇区均值自己算而不用 np.nanmean：坏扇区整行 NaN 时 nanmean 返回
        # NaN 并往 stderr 打 RuntimeWarning（本模块的坏扇区 NaN 是预期输入）。
        # 这里逐 2θ 在有效扇区上取均值，全坏的位置取 0 = 该处不扣
        path = _bg_path_of(dock)
        finite = np.isfinite(raw2d)
        counts = finite.sum(axis=1)
        sums = np.where(finite, raw2d, 0.0).sum(axis=1)
        mean_curve = np.divide(sums, counts, out=np.zeros_like(sums),
                               where=counts > 0)
        params = _proc_params(window, dock, path)
        _, _, base = _proc_curve(window, dock, path, tth, mean_curve)
        i2d = raw2d - np.asarray(base, dtype=float)[:, None] \
            if base is not None else raw2d
        # 平滑 + 裁剪：逐扇区跑链的后半段（背景位关掉：上面已经按共同
        # 基线扣过，不能 36 个扇区各估各的）。两项都没开就整段跳过
        sc = dict(params, mode="off")
        if (float(sc.get("smooth_deg") or 0.0) > 0
                or sc.get("cut_ranges")):
            i2d = np.column_stack([
                process.apply_chain(tth, i2d[:, k], sc)[0]
                for k in range(i2d.shape[1])])
        n = i2d.shape[1]
        colors = cm.viridis(np.linspace(0, 1, n))
        # 每条曲线画到自身第一个 0（截断几何）；未截断的画到末尾。
        # 截断位置要看**原始**数据：扣背景后原来的 0 会变成 -基线（非零），
        # 拿扣除后的值判 0 会把所有曲线都画到末尾。
        # 2026-10-12（用户："155-175 为什么是空的／只有 33 条"）：**开头的
        # 0 不是截断**——束心附近头几格是亚像素半径，空箱 dummy = 0（实测
        # 这批数据 155/165/175 三个扇区的前 1~3 箱恒为 0、其余箱正常），
        # 按"第一个 0"截就把这三条整条截空，36 条只剩 33 条。现在从第一个
        # 非零箱起画，再从那里往后找真正的截断点；整列没数据的仍是空行。
        curves = []
        for k in range(n):
            v = i2d[:, k]
            alive = np.isfinite(raw2d[:, k]) & (raw2d[:, k] != 0)
            if not alive.any():
                curves.append((tth[:0], v[:0], k))   # 整列死区：空行
                continue
            start = int(np.argmax(alive))            # 跳过束心空箱的 0
            dead = ~np.isfinite(raw2d[start:, k]) | (raw2d[start:, k] == 0)
            end = start + int(np.argmax(dead)) if dead.any() else len(v)
            curves.append((tth[start:end], v[start:end], k))
        # 行距统一：所有行同一个行高（等行距 → 各行的强弱能横向比，
        # y 刻度也均匀分布；裁剪过的巨峰不再撑高自己那一行）。
        # 行距怎么定见 services/stacking：**按第二高的行峰**，不按全场
        # 最大——一个特别强的扇区（实测这批 LMFP：χ=165° 的主峰是第二名的
        # 3 倍）会把其余 35 行压成平线（用户 2026-10-02："行间距小一点，
        # 让峰明显一点"）。
        i_pos = np.clip(i2d, 0.0, None)
        i_pos = np.where(np.isfinite(i_pos), i_pos, 0.0)
        step = row_step(i_pos.max(axis=0))
        if _panel_param(window, dock, "瀑布手动行距", False):
            # 用户自己定行距（2026-10-05："对比的堆叠添加让用户自己改
            # y off 还有瀑布图也是"）：按倍数缩放自动行距——只动显示，
            # 1.00 = 与自动相同（对比堆叠那对是同一口径，键各管各的）
            step *= float(_panel_param(window, dock, "瀑布行距倍数", 1.0))
        offsets = np.arange(n, dtype=float) * step
        for t_cut, v_cut, k in curves:
            ax.plot(t_cut, np.clip(v_cut, 0.0, None) + offsets[k],
                    color=colors[k], lw=0.5)
        # χ 刻度 = 纵轴的刻度，**默认就画**（2026-10-12 用户拍板"甲"：
        # 瀑布是一个文件 36 个扇区，行与行的区别就是方位角——刻度一藏
        # 就"不知道每条线代表什么"；轴不是"数据名字"，那把 [显示数据名]
        # 开关从此退出瀑布，只管对比的两处名字）。抽稀照旧
        # （_chi_tick_labels：矮面板每 k 行标一个、抽掉的留空；曲线名
        # 仍贴在线上供悬停读数，与刻度无关）
        ax.set_yticks(offsets)
        ax.set_yticklabels(_chi_tick_labels(ax, chi), fontsize=7)
        # 行距换台阶 → 缩放时钉下的 y 窗口一起换算（× 新 step/旧 step）：
        # 保留**同一批行**留在视野里。用户 2026-10-13 报"瀑布图在先缩放后
        # 调整行距时会 yoff 失效"——偏移每次重画都按新行距重算（那一环没
        # 毛病），坏的是钉住的窗口按旧台阶的坐标数值原样套回，视野里换成
        # 了另一段角度的行。处理链改行高（第二高峰变了 → 自动行距跟着变）
        # 走同一条路，一起修。
        # 与"家"重合的窗口（缩放后又按过 Home ≈ 天然量程）不换算、直接
        # 丢掉：那是自动量程的窗口，让新行距自己重新量程，才不会被按比例
        # 放大/缩小（行距相对数据很小时差得出来）。
        snap = getattr(dock, "params_snapshot", None)
        prev_step = getattr(dock, "_wf_step", None)
        if (snap is not None and prev_step is not None
                and np.isfinite(prev_step) and float(prev_step) > 0
                and np.isfinite(step) and step > 0):
            rng = snap.get("视图 y 范围")
            lo = hi = None
            try:
                lo, hi = float(rng[0]), float(rng[1])
            except (TypeError, IndexError, ValueError):
                pass
            if lo is not None and lo < hi:
                home = getattr(dock, "view_home", None)
                if (home is not None and len(home) >= 2
                        and np.isclose(lo, home[1][0], rtol=1e-6)
                        and np.isclose(hi, home[1][1], rtol=1e-6)):
                    snap.pop("视图 y 范围", None)   # ≈ 天然窗口：丢给自动量程
                else:
                    ratio = float(step) / float(prev_step)
                    if abs(ratio - 1.0) > 1e-12:
                        snap["视图 y 范围"] = [lo * ratio, hi * ratio]
        dock._wf_step = float(step)
        natural_ylim = ax.get_ylim()   # 钉住缩放窗口**之前**的量程：家的 y 用它
        zoomed = _apply_plain_view(window, dock, ax)   # 摆回缩放的窗口
        _apply_text_guards(dock, ax, keep_title, keep_xlabel, keep_ylabel)
        _restore_line_styles(ax, old_lines)
        for line, c in zip(_data_lines(ax), chi):
            line.set_label(f"{c:.0f}°")   # 重画后重贴扇区名（悬停读数）
        ax.grid(alpha=0.2)
        _content(dock).draw()
    finally:
        window._setting_limits = prev_limits
    _connect_axis_sync(window, dock.panel_key, ax)   # ax.clear() 清掉了回调
    if zoomed:
        dock._view_from_gesture = True   # 缩放的窗口不是"家"
    # 瀑布的 y 是**模式算出来的**（36 行的错开行距，[手动行距]/倍数一改
    # 就换高矮），与对比·堆叠同款：家的 y 跟着换（2026-10-09，原因见
    # _refresh_home 的 mode_y 说明）。传 natural_ylim：家的 y 取**钉住
    # 缩放窗口之前**的量程，不然缩放后调行距会把缩放窗口封成"家"
    # （2026-10-13，"先缩放后调行距"修复的另一半）
    _refresh_home(dock, ax, mode_y=True, mode_ylim=natural_ylim)
    dock.figure_saved = False

