"""校准图绘制：整幅重画、理论环、选点标记贴图、视野三档。

2026-10-22 从 calib_panel.py 原样搬出（只改结构，不动逻辑）。
calib_panel 再导出；_redraw_calib 对 _calib_image / _calib_draw_geometry
用函数内延迟导入破环（这两个留在 calib_panel，保住 load_diffraction_image
的 56 处测试 patch 目标）。
"""
import numpy as np
from matplotlib.colors import LogNorm
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.gui.calib_model import (
    CP_COLOR, RING_COLOR, SELECT_COLOR, _calib_state, _metrics_dev,
    _result_by_name, _slot_label)
from xrd_toolkit.gui.panel_state import _auto_contrast_values, _log
from xrd_toolkit.services.integrator import theoretical_ring_paths


def _draw_calib_image(window: QMainWindow, key: str, image, geometry,
                      control_points=None, ring_marks=None) -> None:
    """整幅重画校准图：图像 + 理论环路径 + 可选绿点/用户点标记。

    对齐 scripts/view_diffraction.py 的显示：magma + LogNorm、自动
    对比度 1%/99.9% 分位、vmin 下限 1.0、origin="lower"。理论环用
    theoretical_ring_paths 精确反解，不是"圆心 + 半径"的正圆：探测
    器有倾斜时环是椭圆、公共圆心是直射束落点而非 PONI，写正圆会整体
    偏 8~23 px（实测本数据）。用户点 = 青圈 + 环号（点图找环的依据），
    控制点 = 绿点（pyFAI 实际取点，验证精修效果）。

    几何把环全推出图像时（距离/像素/波长填错、校准跑出离谱解）不静默
    画一堆看不见的线，而是红字 + 日志说清楚原因（_warn_rings_off_image）
    ——**视野不动**：图像是这张图上唯一不动的参照系（要放大到看得见环：
    勾面板上的 [看环全貌]，或点亮 [放大镜] 后框选放大，见 _set_fit_rings
    / _fit_view_to_rings / _on_calib_press）。
    """
    ax = window.calib_ax
    ax.clear()
    h, w = image.shape
    lo, hi = _auto_contrast_values(image)
    vmin = max(1.0, lo)
    vmax = max(hi, vmin * 10.0)
    ax.imshow(image, cmap="magma", norm=LogNorm(vmin=vmin, vmax=vmax),
              origin="lower")
    ax.set_aspect("equal")
    state = _calib_state(window)
    paths = theoretical_ring_paths(
        pixel_size_m=geometry["pixel_size_m"],
        wavelength_m=geometry["wavelength_m"],
        dist_m=geometry["dist_m"], poni1_px=geometry["poni1_px"],
        poni2_px=geometry["poni2_px"], rot1_deg=geometry["rot1_deg"],
        rot2_deg=geometry["rot2_deg"], image_shape=image.shape,
        calibrant=state["calibrant"], n_rings=state["n_rings"])
    for ring, xy in paths["rings"]:
        ax.plot(xy[:, 0], xy[:, 1], color=RING_COLOR, lw=0.7, alpha=0.65)
        # 环号标在"路径上、落在图像内、最靠右"的点（标到图外看不见）
        inside = ((xy[:, 0] >= 0) & (xy[:, 0] < w)
                  & (xy[:, 1] >= 0) & (xy[:, 1] < h))
        cand = np.flatnonzero(inside)
        if cand.size:
            j = cand[int(np.argmax(xy[cand, 0]))]
            ax.annotate(str(ring), (xy[j, 0], xy[j, 1]), color=RING_COLOR,
                        fontsize=7, va="center", ha="left")
    if control_points is not None and len(control_points):
        # 控制点可达数千个：抽稀到 1000 以内（绿点只是视觉验证）
        stride = max(1, len(control_points) // 1000)
        pts = np.asarray(control_points)[::stride]
        ax.plot(pts[:, 0], pts[:, 1], ".", color=CP_COLOR, ms=2.5)
    # 选点标记：整幅重画时按 state 重建，artist 记进 window.calib_marker_artists。
    # 它们是**不可见**的（见 _new_marker_artists）：整幅画里不出现，底图才是
    # 干净的；贴图（_refresh_calib_markers，由 draw_event 触发）时单独画上去
    window.calib_marker_artists = []
    if ring_marks:
        for x, y, ring in ring_marks:
            window.calib_marker_artists += _new_marker_artists(ax, x, y, ring)
    span = ("Ring radius %.0f–%.0f px" % (paths["r_min_px"], paths["r_max_px"])
            if np.isfinite(paths["r_min_px"]) else "Ring radius: no solution")
    ax.set_xlabel("Horizontal (px)")
    ax.set_ylabel("Vertical (px)")
    # 标题里写明"青环是谁画的"（用户 2026-09-30："加一个图上的[说明]……让用户
    # 知道结果就是当前用的数据"）：青环 = 当前配置（哪条结果 + 它现在的环位
    # 偏差）——图上看到的东西和"现在用的几何"从此对得上号
    state = _calib_state(window)
    dev = _metrics_dev(state.get("current_metrics"))
    dev_txt = f" · ring deviation {dev:.2f} px" if dev is not None else ""
    # 标题两行、字号收小（2026-10-10 用户："标题过长"——原先一行塞四段
    # （文件名 · 青环是谁 · 环位偏差 · 环半径覆盖），面板一窄两头裁）。
    # 第一行只留"青环=哪条结果 + 环位偏差"（看图的人真正要对的信息）；
    # 文件名删掉（窗口标题栏已经写着），环半径覆盖挪到第二行。
    ax.set_title(f"Cyan rings = current config: "
                 f"{_slot_label(state, 'current')}{dev_txt}\n{span}",
                 fontsize=10)
    if not paths["n_inside"]:
        _warn_rings_off_image(window, ax, image, paths, geometry)
    # 视野（必须在所有画线之后设），三档优先：
    #   ① 用户自己缩过/框过（window.calib_view，放大镜手势写的）——回放它，
    #      重画不弹回（2026-10-13 加放大功能后的新一档）；
    #   ② [看环全貌] 勾着 → 放大到包住环；
    #   ③ 默认锁死 = 图像那一框。
    # 为什么不默认跟着环走——环画到图像外时 matplotlib 的自动缩放会把
    # 坐标范围撑大（实测像素填 20 µm：视野 2048 → 21258 px，图像在画布上
    # 只剩 10% 宽），看着像"图动了/图被迫变小"（2026-09-26 晚用户报）。
    # 图像是这张图上唯一不动的参照系：环围着它动，跑到框外就老实被裁掉，
    # 由红字说明。范围取 imshow（origin="lower"、无 extent）给的那一框。
    view = getattr(window, "calib_view", None)
    if view is not None:
        try:
            ax.set_xlim(*view[0])
            ax.set_ylim(*view[1])
        except (TypeError, ValueError):   # 坏窗口（防御）：退回下面几档
            window.calib_view = None
            view = None
    if view is None and getattr(window, "calib_fit_rings", False):
        _fit_view_to_rings(ax, image, paths)
    elif view is None:
        ax.set_xlim(-0.5, w - 0.5)
        ax.set_ylim(-0.5, h - 0.5)
    window.calib_canvas.draw_idle()


def _set_fit_rings(window: QMainWindow, on: bool) -> None:
    """[看环全貌] 开关：翻状态 + 清掉手势缩放 + 重画。

    状态记在 window.calib_fit_rings（布尔）而不是读控件：面板一关，
    控件就是悬空对象，读它 RuntimeError（同 calib_ax 那个坑）。

    翻它 = 用户显式要"看环全貌/锁回图像"这一种视野——先把放大镜手势
    留下的 calib_view 清掉（2026-10-13），否则新开的视野会被旧缩放窗口
    盖住，按钮看着"没反应"。
    """
    window.calib_fit_rings = bool(on)
    window.calib_view = None
    _log(window, "视野：看环全貌" if on else "视野：锁定图像（看环全貌已关）")
    _redraw_calib_if_open(window)


def reset_calib_view(window: QMainWindow) -> None:
    """Home 键的落点：校准图回到"最初的视野"（用户 2026-10-14）。

    用户原话："校准图添加 home 键，还是回到最初的视图"。[看环全貌] 那个
    开关管的是"看环 / 锁图像"这一种**模式**，Home 不翻它——只清掉放大镜
    手势留下的缩放窗口（window.calib_view），回到进这张图时那个视野
    （开关开着 = 包住环的那一框，关着 = 图像那一框）。别的图面板的 Home
    也是这个分工："最初的样子"由视图自己那条线定义，键盘只负责回去。
    视图本来就是最初的（没缩放过）就不动、不记日志——同普通面板的 Home。
    """
    if getattr(window, "calib_view", None) is None:
        return
    window.calib_view = None
    _log(window, "视野：已复位到最初（[看环全貌] 开关不动）")
    _redraw_calib_if_open(window)


def _calib_zoom_on(window: QMainWindow) -> bool:
    """校准图上的放大镜是否点亮（状态位，不读控件——控件随面板销毁）。"""
    return bool(getattr(window, "calib_zoom_on", False))


def _set_calib_zoom(window: QMainWindow, on: bool) -> None:
    """放大镜开关：翻状态 + 写日志（与普通图面板同一句话的校准版）。

    手势不下传到 Qt：状态位由各事件处理函数现读。
    """
    window.calib_zoom_on = bool(on)
    _log(window, "放大镜已" + ("开启" if on else "关闭") + "："
                 + ("左键拖 = 框选放大，滚轮以光标为中心缩放" if on
                    else "左键点图选点（滚轮不缩放）"))


def _fit_view_to_rings(ax, image, paths) -> None:
    """把视野放大到包住全部环路径（[看环全貌] 勾上时才走这里）。

    环路径全推出图像时这是唯一能看到环在哪的办法（视野比图像大几十倍，
    图像会缩成画布中央一小块——那是这个开关的本意，不再是自动行为）。
    视野多留 5% 余量；一个有效点都没有（几何离谱到反解不出解）就不动。
    """
    h, w = image.shape
    xy = np.vstack([p for _, p in paths["rings"]])
    fin = np.isfinite(xy).all(axis=1)
    if not fin.any():
        return
    x0, x1 = float(xy[fin, 0].min()), float(xy[fin, 0].max())
    y0, y1 = float(xy[fin, 1].min()), float(xy[fin, 1].max())
    pad_x = 0.05 * max(x1 - x0, w)
    pad_y = 0.05 * max(y1 - y0, h)
    ax.set_xlim(min(0.0, x0) - pad_x, max(w, x1) + pad_x)
    ax.set_ylim(min(0.0, y0) - pad_y, max(h, y1) + pad_y)


def _warn_rings_off_image(window: QMainWindow, ax, image, paths,
                          geometry) -> None:
    """守卫：几何把理论环全推出图像时，红字 + 日志说清楚（**不动视野**）。

    静默什么都不显示是最坏的失败方式（用户只会觉得"校准没反应"），
    所以要在图上明说；但**不把视野放大到包住环**——2026-09-26 晚用户
    报"青环变得很大时图会被迫变小"：环跑到图像外时视野被撑到 11 倍宽
    （实测像素填 20 µm：视野 2048 → 22249 px），图像在画布上缩成一小块，
    看上去像"图动了/图变小了"，而真正发生变化的是青环。图像是这张图上
    唯一不动的参照系，编辑几何不该把它挪走；环跑到外面这件事，红字里
    连半径范围一起报出来（一眼看得出差几倍）就够了。
    日志按几何指纹去重（撤销/清空选点的重画不重复刷屏）。
    """
    h, w = image.shape
    fit = getattr(window, "calib_fit_rings", False)
    zoomed = getattr(window, "calib_view", None) is not None
    head = (f"当前几何下 {len(paths['rings'])} 条环全部落在图像外"
            f"（环半径 {paths['r_min_px']:.0f}–{paths['r_max_px']:.0f} px）")
    if fit:
        how = "视野已按 [看环全貌] 放大到看得见它们"
    elif zoomed:
        how = ("视野由缩放掌握——环不在当前视野里就框选/滚轮找，"
               "或勾 [看环全貌]")
    else:
        how = ("图上不会出现青线（想看环在哪：勾上面的 [看环全貌]，"
               "或点亮 [放大镜] 后框选放大）")
    tail = f"图像 {w}×{h} —— 请核对像素尺寸 / 波长 / 距离"
    # 图上用英文（图内文字一律英文），日志保留同一句话的中文
    fig_head = (f"{len(paths['rings'])} rings fall outside the image under the "
                f"current geometry (ring radius "
                f"{paths['r_min_px']:.0f}–{paths['r_max_px']:.0f} px)")
    fig_how = ("view zoomed out to include them ([看环全貌] on)" if fit
               else "view is set by zoom: box-zoom or wheel to find them, "
                    "or turn on [看环全貌]" if zoomed
               else "no rings are drawn (turn on [看环全貌] or use the "
                    "[放大镜] box zoom to find them)")
    fig_tail = f"image {w}×{h} — check pixel size / wavelength / distance"
    # 折行写：默认视野锁在图像那一框（不放大），一行写不下会被右边缘裁掉
    ax.text(0.02, 0.98, "\n".join([f"⚠ {fig_head}", fig_how, fig_tail]),
            transform=ax.transAxes, color="#ff6666", fontsize=8,
            va="top", ha="left")
    key = tuple(round(float(geometry[k]), 6) for k in sorted(geometry))
    if getattr(window, "_calib_span_warned", None) != key:
        window._calib_span_warned = key
        _log(window, f"{head}，{how}，{tail}")


def _new_marker_artists(ax, x, y, ring: int, selected: bool = False) -> list:
    """一个选点标记（青圈 + 环号）的 artist，**先设成不可见**。

    不可见是为了把它们**排除在整幅重画之外**：整幅画一次要给 2048² 图重过
    LogNorm（几百毫秒到十几秒），而标记只是几个圈——它们只在贴图时用
    `draw_artist` 单独画（draw_artist 不看可见性，见 _refresh_calib_markers）。
    这样底图永远是"干净的"（不含标记），撤销/清空只要贴回底图再画剩下的标记。

    selected=True 再加一圈金黄高亮——右键选中的那个点要一眼看得出来
    （2026-10-03：环号改成"选中 + 面板里改"，选中的是谁必须可见）。
    """
    arts = []
    if selected:
        arts.append(ax.plot([x], [y], "o", mfc="none", mec=SELECT_COLOR,
                            ms=15, mew=2.0)[0])
    ln = ax.plot([x], [y], "o", mfc="none", mec=RING_COLOR, ms=9, mew=1.5)[0]
    txt = ax.annotate(str(ring), (x, y), color=RING_COLOR, fontsize=8,
                      va="bottom", ha="left")
    arts += [ln, txt]
    for art in arts:
        art.set_visible(False)
    return arts


def _refresh_calib_markers(window: QMainWindow) -> None:
    """把选点标记贴到画布上：贴回底图 + 画所有标记 + 上屏（几毫秒）。

    没有底图（面板刚建、后端不支持贴图）就老实整幅画一次——draw_event 里
    会补拍底图，之后又回到贴图这条路。
    """
    ax = window.calib_ax
    canvas = window.calib_canvas
    arts = getattr(window, "calib_marker_artists", None) or []
    bg = getattr(window, "calib_bg", None)
    if bg is None or not hasattr(canvas, "restore_region") \
            or not hasattr(canvas, "blit"):
        canvas.draw_idle()
        return
    canvas.restore_region(bg)
    for art in arts:
        art.set_visible(True)       # draw_artist 不看可见性，但摆明了更省心
        ax.draw_artist(art)
        art.set_visible(False)
    canvas.blit(ax.figure.bbox)


def _rebuild_calib_markers(window: QMainWindow) -> None:
    """按 state["points"] 重建全部标记并贴一次
    （撤销 / 清空 / 选中 / 改环号后调）。"""
    ax = getattr(window, "calib_ax", None)
    if ax is None:
        return
    state = _calib_state(window)
    sel = state.get("selected")
    for art in getattr(window, "calib_marker_artists", None) or []:
        try:
            art.remove()
        except Exception:                                  # noqa: BLE001
            pass
    window.calib_marker_artists = []
    for i, (x, y, ring) in enumerate(state["points"]):
        window.calib_marker_artists += _new_marker_artists(
            ax, x, y, ring, selected=(i == sel))
    _refresh_calib_markers(window)


def _cache_calib_bg(window: QMainWindow, _event=None) -> None:
    """整幅画完：拍一张底图（干净的，不含标记）+ 把标记贴回去。

    与 plot_panels 的 `_blit_take` 同一个道理：整幅重绘一次要 ~0.6 s
    （2048² 图像的 LogNorm 上色 + 重采样，探针实测，2026-10-01），而选点、
    撤销、清空、改环号都只需要动那几个圈。挂在 draw_event 上——**任何一次
    整幅重绘之后都会重拍**（换几何、改视野都在其中），所以底图永不过期；
    窗口一缩放也会重画 → 重拍。
    """
    canvas = getattr(window, "calib_canvas", None)
    ax = getattr(window, "calib_ax", None)
    if canvas is None or ax is None:
        return
    try:
        window.calib_bg = canvas.copy_from_bbox(ax.figure.bbox)
    except Exception:                                  # noqa: BLE001
        window.calib_bg = None      # 后端不支持贴图：退回整幅重画
        return
    _refresh_calib_markers(window)  # 整幅画里没有标记（不可见），这里贴上去


def _add_calib_marker(window: QMainWindow, x, y, ring: int) -> None:
    """点击成功后加一个青圈 + 环号——**贴图**（几毫秒），不整幅重画。

    用户 2026-10-01："校准功能卡死两次，都是在选点时"：原先这里调
    `draw_idle`，等于每点一下都把整幅 2048² 图像重绘一遍（探针实测 610 ms
    每次，真窗口更慢），事件循环整个被占住。现在只贴标记（见
    _refresh_calib_markers）。
    """
    arts = getattr(window, "calib_marker_artists", None)
    if arts is None:
        arts = window.calib_marker_artists = []
    arts += _new_marker_artists(window.calib_ax, x, y, ring)
    _refresh_calib_markers(window)


def _redraw_calib_if_open(window: QMainWindow) -> None:
    """几何变了就重画青线——但只有面板开着才画。

    为什么要有这个守卫：[编辑当前配置] 只能在校准页点到，可 [加载参数]
    （导入 .poni → 借成当前配置）分析模式下就能用，那时没有画布。

    面板开着的判据是 calib_dock **不是** calib_ax：关面板时只把
    calib_dock 置 None（calib_ax / calib_canvas 是悬空的旧对象，画布
    C++ 侧随子窗口一起删了），拿它判断会在"用过校准、退回分析模式、
    再导入 .poni"时画到已删除的画布上。
    """
    if (getattr(window, "calib_dock", None) is not None
            and getattr(window, "calib_ax", None) is not None):
        _redraw_calib(window)


def _redraw_calib(window: QMainWindow) -> None:
    """按当前状态整幅重画：理论环（当前配置的几何）+ 用户点 + 自动校准
    控制点（若有）。撤销/清空/校准完成/几何被改后调用。"""
    from xrd_toolkit.gui.calib import (_calib_sync, _ensure_current, _refresh_current_metrics,
        _reset_calib_form)   # 破循环：见模块说明
    from xrd_toolkit.gui.calib_panel import (  # 破循环
        _calib_draw_geometry, _calib_image)
    state = _calib_state(window)
    # 控制点来自 pyFAI extract_cp（自动取点产出；手动选点不带）：取
    # "当前配置"对应的那份，没有就退回最近一条带控制点的结果
    cps = None
    cur_name = state["slots"].get("current")
    item = _result_by_name(state, cur_name) if cur_name else None
    if item is not None and item["result"].get("control_points"):
        cps = item["result"]["control_points"]
    else:
        for it in reversed(state["results"]):
            if it["result"].get("control_points"):
                cps = it["result"]["control_points"]
                break
    _draw_calib_image(window, window.calib_key,
                      _calib_image(window, window.calib_path),
                      _calib_draw_geometry(window), control_points=cps,
                      ring_marks=state["points"])
