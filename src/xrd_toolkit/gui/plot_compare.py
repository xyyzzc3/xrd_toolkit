"""多文件视图：对比面板（多条 1D 叠图）+ 热图 + 锚点拾取。

从 plot_views.py 拆出来（纯搬迁）。共同点：**要选多个文件才成立**（用户
定的归类规则），将来整体归「对比」页。
依赖方向：本模块 → plot_views（背景叠加/重画）与 plot_panels（面板壳），
反向调用一律函数内延迟导入。
"""
from pathlib import Path

import numpy as np
from matplotlib.colors import LogNorm, Normalize
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.gui.panel_state import (
    _auto_y_range, _AUX_GID_PREFIX, _bg_curve, _collect_geometry,
    _compare_shown_curves, _content, _curve_color, _data_snapshot,
    _heat_shown, _log, _panel_param, _set_focus)
from xrd_toolkit.gui.plot_panels import (
    _apply_text_guards, _connect_axis_sync, _data_lines, _open_plot_panel,
    _refresh_home, _restore_line_styles, _settle_scale, _snapshot_canvas)
from xrd_toolkit.gui.plot_views import (
    _batch_step, _bg_path_of, _refresh_bg, _spawn)


def _compare_title(displays) -> str:
    """对比面板标题：两个文件 = A vs B；更多 = A 等 N 个文件。"""
    if len(displays) == 2:
        return f"对比_{displays[0]}_vs_{displays[1]}"
    return f"对比_{displays[0]} 等 {len(displays)} 个文件"


def _redraw_compare(window: QMainWindow, key: str) -> None:
    """用面板已有的曲线数据按该面板自己的 1D 显示参数重画整张对比图。

    与 _draw_1d 同套路：坐标范围随参数（2θ 上下限 / 对数纵轴 /
    纵轴范围），只是画多条曲线 + 图例。显示参数同样只读本面板快照
    （_panel_param），不读参数坞控件——各算完到齐收尾时焦点可能还
    在别的面板上，读控件会把别的图的设置画到这张图上。对比归一化
    只动显示数据（原始结果原样保留在 compare_data，见
    _compare_shown_curves 的四模式）。
    """
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已关：静默丢弃
    ax = _content(dock).axes_1d
    # Customize 保护：同 _draw_1d，先拍下现状再 clear
    keep_title, keep_xlabel, keep_ylabel, keep_scale, old_lines = \
        _snapshot_canvas(ax)
    window._setting_limits = True   # 同 _draw_1d：程序设范围不算用户改动
    try:
        ax.clear()
        curves = _compare_shown_curves(window, dock)
        # 画图顺序 = 文件列表顺序（不随各文件算完的先后变）→ 图例顺序、
        # 颜色序号稳定（i 由 _compare_shown_curves 携带）。颜色 =
        # 逐条自定义色（dock.curve_colors，Customize 对话框改）优先，
        # 否则按面板快照里的配色参数取槽色（颜色跟着文件走，见
        # _curve_color）
        palette = _panel_param(window, dock, "曲线配色", "高对比")
        overrides = getattr(dock, "curve_colors", None) or {}
        stack = _panel_param(window, dock, "对比堆叠", False)
        if stack:
            # 瀑布式错开叠放：行高 = 该行峰值 × 0.7（对齐 CLI
            # waterfall 画法），y 刻度 = 各条基线（显示名）。归一化
            # 先做（每条显示数据再叠），堆叠下纵轴范围/对数不适用
            # （行偏移由数据决定，同瀑布）
            peaks = [float(np.nanmax(s)) if len(s) and np.isfinite(s).any()
                     else 0.0 for _, s, _, _ in curves]
            offsets = [0.0]
            for p in peaks[:-1]:
                offsets.append(offsets[-1] + p * 0.7)
            for (tth, shown, display, i), off in zip(curves, offsets):
                color = overrides.get(display) or _curve_color(palette, i)
                ax.plot(tth, shown + off, color=color, lw=0.8,
                        label=display)
            ax.set_yticks(offsets)
            ax.set_yticklabels([d for _, _, d, _ in curves], fontsize=6)
        else:
            for tth, shown, display, i in curves:
                color = overrides.get(display) or _curve_color(palette, i)
                ax.plot(tth, shown, color=color, lw=0.8, label=display)
        # 颜色不套旧快照（配色参数/自定义色在画图时已定），其余样式
        # 照旧保护（图例在下面读标签，先套回样式）
        _restore_line_styles(ax, old_lines, restore_color=False)
        xlo = _panel_param(window, dock, "视图 2θ 下限 (°)", None)
        xhi = _panel_param(window, dock, "视图 2θ 上限 (°)", None)
        if xlo is None or xhi is None or not xlo < xhi:
            xlo = _panel_param(window, dock, "2θ 下限 (°)", 1.0)
            xhi = _panel_param(window, dock, "2θ 上限 (°)", 8.0)
        if xlo < xhi:
            ax.set_xlim(xlo, xhi)
        if window.plot_docks.get(window.focus_panel) is dock:
            window.params["视图 2θ 下限 (°)"].setValue(xlo)
            window.params["视图 2θ 上限 (°)"].setValue(xhi)
        if not stack:
            # 堆叠模式下纵轴由行偏移决定，对数/范围参数不适用（同瀑布）
            log_y = _panel_param(window, dock, "对数纵轴", False)
            scale = _settle_scale(dock, keep_scale, log_y)
            eff_log = (scale == "log")
            if scale != "linear":
                ax.set_yscale(scale)
            auto_y = _panel_param(window, dock, "纵轴自动", True)
            ylo = yhi = None
            if auto_y:
                if curves:
                    ylo, yhi = _auto_y_range(
                        np.concatenate([s for _, s, _, _ in curves]), eff_log)
                    if ylo < yhi:
                        ax.set_ylim(ylo, yhi)
                else:
                    pass   # 一条曲线都没算成：空图，纵轴交给 matplotlib 默认
            else:
                ylo = _panel_param(window, dock, "纵轴下限", 1.0)
                yhi = _panel_param(window, dock, "纵轴上限", 100000.0)
                if eff_log:
                    ylo = max(ylo, 1e-6)
                if ylo < yhi:
                    ax.set_ylim(ylo, yhi)
            # 自动模式把实际用的区间填进置灰输入框（同 _draw_1d，只填焦点）
            if auto_y and ylo is not None and window.plot_docks.get(window.focus_panel) is dock:
                window.params["纵轴下限"].setValue(ylo)
                window.params["纵轴上限"].setValue(yhi)
        _apply_text_guards(dock, ax, keep_title, keep_xlabel, keep_ylabel)
        if dock.compare_data and not stack:
            # 堆叠下 y 刻度 = 样品名（曲线就躺在自己名字那行上），
            # 图例冗余（同瀑布）
            ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
        _content(dock).draw()
    finally:
        window._setting_limits = False
    _connect_axis_sync(window, dock.panel_key)   # ax.clear() 清掉了回调（见 helper 注释）
    _refresh_home(dock, ax)   # 程序重画 = 新"家"（见 helper 注释）
    dock.figure_saved = False   # 重画 = 新内容还没存盘


def _finish_compare(window: QMainWindow, key: str) -> None:
    """对比面板全部曲线到齐（含失败）：整图重画 → 成为编辑对象 → 记日志。"""
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已关：静默丢弃
    _redraw_compare(window, key)
    _set_focus(window, key, dock.panel_display)
    if dock.compare_data:
        _log(window, f"对比完成：{len(dock.compare_data)} 条曲线")
    else:
        _log(window, "对比失败：所有文件的积分都失败了，面板留空")


def _run_compare(window: QMainWindow, key: str) -> None:
    """对对比面板的每个文件各起一个后台积分，结果画到同一张图。

    代次（gen）防过期：重复点 [对比] 或数据 [应用] 时旧代任务
    全部作废。快照开工前拍下（与单文件面板一致）。
    """
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已关：迟到点击/重算不落地
    # 数据参数 = 控件当前值（计算就用它），显示参数沿用面板自己的
    # 旧快照（重按 [对比] 刷新不改这张图的长相）
    dock.params_snapshot = _data_snapshot(window, dock.params_snapshot)
    dock.compare_gen += 1
    gen = dock.compare_gen
    epoch = window._panel_epoch.get(key, 0)   # 面板代数：关过重开旧代全作废
    dock.compare_pending = len(dock.compare_files)
    dock.compare_data = {}
    geom = _collect_geometry(window)
    npt = int(window.params["输出点数"].value())
    window._setting_limits = True   # 程序自己清轴：不触发范围同步写回
    try:
        _content(dock).axes_1d.clear()
    finally:
        window._setting_limits = False

    def finish_one(path, display, result):
        """单文件结果到位：存原始数据、画一条曲线；全齐后收尾。"""
        # 现查面板（不捕获对象：面板可能已关、已换容器）：
        # 面板没了 / 代数变了（关过重开）/ 代次旧了 → 丢弃
        panel = window.plot_docks.get(key)
        if (panel is None
                or window._panel_epoch.get(key, 0) != epoch
                or panel.compare_gen != gen):
            return   # 旧结果静默丢弃（整图已由新代次重画）
        tth, intensity = result
        panel.compare_data[display] = (tth, intensity)
        panel.compare_pending -= 1
        if panel.compare_pending == 0:
            _finish_compare(window, key)

    def fail_one(path, display, msg):
        panel = window.plot_docks.get(key)
        if (panel is None
                or window._panel_epoch.get(key, 0) != epoch
                or panel.compare_gen != gen):
            return
        panel.compare_pending -= 1
        _log(window, f"对比：{display} 积分失败 — {msg}")
        if panel.compare_pending == 0:
            _finish_compare(window, key)

    for path, display in dock.compare_files:
        # 默认参数绑定防闭包晚绑定（循环变量到回调执行时已走到末尾）
        def spawn_one(path=path, display=display):
            def done(window_, key_, task, result):
                finish_one(path, display, result)

            def error(msg):
                fail_one(path, display, msg)

            window.status_text.setText(f"正在积分 {path.name}…")
            _spawn(window, path, geom, npt, key,
                   on_done=done, on_error=error)

        spawn_one()
    _log(window, f"开始对比 {len(dock.compare_files)} 个文件（后台线程）")


def _plot_compare(window: QMainWindow) -> None:
    """[对比] 按钮：把勾选文件的 1D 曲线叠到同一张面板上。

    只做 1D。勾选 ≥2 个文件，每个文件后台各算各的积分，结果陆续
    画进同一张图（一个文件一条曲线，自动分色 + 图例显示名），
    全部完成后该面板成为编辑对象。同一勾选集合重复点 = 复用同一
    张面板刷新；换集合 = 新开一张。面板键 = "对比|排序后的路径串"
    （与单文件面板 "1D|路径" 并存，互不干扰）。
    """
    checked = [window.file_list.item(i)
               for i in range(window.file_list.count())
               if window.file_list.item(i).checkState() == Qt.Checked]
    if len(checked) < 2:
        _log(window, "对比至少勾选两个文件（勾上的文件叠到一张图）")
        return
    files = [(Path(item.data(Qt.UserRole)), item.text()) for item in checked]
    key = "对比|" + ",".join(sorted(str(p) for p, _ in files))
    dock = window.plot_docks.get(key)
    if dock is None:
        title = _compare_title([d for _, d in files])
        # 新面板级联摆放，现有面板原地不动（同 _plot_view）
        dock = _open_plot_panel(window, "1D", key, title)
        dock.panel_display = title   # 标题/日志/默认存盘名用
        dock.figure_saved = False
        dock.compare_files = files   # 面板绑定这组文件（重算用）
        dock.compare_gen = 0
        dock.params_snapshot = _data_snapshot(window)   # 新面板：显示参数从默认起步
        _log(window, f"打开对比面板：{len(files)} 个文件叠一张图")
    else:
        # 复用面板：文件显示名可能变过（删除重加/改名）→ 绑定刷新，
        # 标题/图例跟着新名字走
        dock.compare_files = files
        title = _compare_title([d for _, d in files])
        if title != dock.panel_display:
            dock.setWindowTitle(title)
            dock.panel_display = title
    dock.setVisible(True)
    _run_compare(window, key)


def _assemble_heatmap(results):
    """把 [(样品名, tth, intensity), ...] 对齐成强度矩阵（纯函数）。

    横轴 = 2θ、纵轴 = 样品（文件列表顺序）、颜色 = 强度。2θ 网格
    以第一个文件的网格为准；别的文件网格不一致（点数/区间不同）
    就 np.interp 重插值到第一网格（视图只求对齐，误差可忽略）——
    是否重插值由返回的 interp 标志报告，调用方记日志。结果空 / 网
    格空（0 点）返回 None。
    """
    if not results:
        return None
    x = np.asarray(results[0][1], dtype=float)
    if x.size < 2:
        return None   # 0/1 个点画不出 extent（imshow 至少要一段区间）
    rows = []
    interp = False
    for name, t, intensity in results:
        ti = np.asarray(t, dtype=float)
        vi = np.asarray(intensity, dtype=float)
        if ti.shape != x.shape or not np.allclose(ti, x, atol=1e-12):
            interp = True
            vi = np.interp(x, ti, vi)   # t 由引擎保证升序
        rows.append(vi)
    return x, np.vstack(rows), [r[0] for r in results], interp


def _anchor_enabled(window: QMainWindow, key: str) -> bool:
    """这个 1D 面板现在该不该响应锚点点选。

    两个条件都要满足：参数坞的模式是"手动锚点"、[拾取锚点] 开着。
    两个开关分开是故意的——模式决定"用什么算背景"（画图时生效），
    拾取开关决定"鼠标现在是在画图还是在选点"，不让人在只想看图时
    误点出锚点。
    """
    if key.split("|", 1)[0] != "1D":
        return False
    btn = getattr(window, "bg_pick_btn", None)
    return bool(btn is not None and btn.isChecked())


def _anchor_press(window: QMainWindow, key: str, event) -> None:
    """1D 面板按下：记账候选锚点（没拖动才算"点击"，松手再决定）。

    与热图行点击同套路：左键拖 = 平移（通用手势），点按 = 加/删锚点。
    """
    dock = window.plot_docks.get(key)
    if dock is None or event.inaxes is None or event.button != 1:
        return
    if not _anchor_enabled(window, key):
        return
    if event.xdata is None or event.ydata is None:
        return
    # 这里**不切焦点**：按下时还不知道是点击还是拖拽平移，而切焦点会回放
    # 该面板的快照（模式多半是"关闭"）→ 拾取开关被 _sync_bg_rows 收掉，
    # 用户只是想在别的图上拖一下就把"拾取锚点"弄丢了（实测日志末行变成
    # "背景扣除：关闭"）。切焦点挪到 _anchor_release、确认是点击之后
    dock._anchor_press = (event.x, event.y)


def _anchor_release(window: QMainWindow, key: str, event) -> None:
    """1D 面板松手：按下点没怎么挪（阈值 5 px）→ 在最近的曲线上取一个
    锚点；点到已有关键点附近（阈值 8 px）→ 删掉它。挪多了 = 平移，不动。

    取点复用悬停那套"最近曲线 + 吸附最近真实数据点"（_hover_motion）：
    报的是真算出来的值，不是鼠标的原始位置。
    """
    dock = window.plot_docks.get(key)
    if dock is None:
        return
    info = getattr(dock, "_anchor_press", None)
    dock._anchor_press = None
    if info is None:
        return
    x0, y0 = info
    if (event.x - x0) ** 2 + (event.y - y0) ** 2 > 5 ** 2:
        return   # 拖过了 = 平移手势，不是点击
    if not _anchor_enabled(window, key):
        return
    if event.inaxes is None or event.xdata is None or event.ydata is None:
        return
    path = _bg_path_of(dock)
    if path is None:
        return
    # 点哪张图就编辑哪张图（与点面板选中编辑对象一致）：_refresh_bg 把参数
    # 坞控件值推进的正是编辑对象的快照，不切焦点会出现"点了没反应"。放在
    # 这里而不是按下时——此刻已确认是点击，不会误伤平移手势（见 _anchor_press）
    _set_focus(window, key, dock.panel_display)
    # 注意不能写 `(... or {}).setdefault(...)`：空 dict 是 falsy，`or`
    # 会换成临时新字典，锚点全加进临时对象里去（探针实测：日志报"加了
    # 1 个"而 window.bg_anchors 还是空的）
    if getattr(window, "bg_anchors", None) is None:
        window.bg_anchors = {}
    anchors = window.bg_anchors.setdefault(str(path), [])
    # 点到已有关键点附近 → 删除（再点恢复的开关语义）
    ax = event.inaxes
    for i, (ax_, ay_) in enumerate(anchors):
        px, py = ax.transData.transform((ax_, ay_))
        if (px - event.x) ** 2 + (py - event.y) ** 2 <= 8 ** 2:
            anchors.pop(i)
            _anchor_changed(window, f"删除锚点：2θ = {ax_:.3f}°")
            return
    # 取最近曲线上的最近真实数据点
    lines = [ln for ln in _data_lines(ax) if len(ln.get_xdata()) > 1]
    if not lines:
        return
    best = None
    for ln in lines:
        xd = ln.get_xdata()
        if xd.size < 2 or not (xd[0] <= event.xdata <= xd[-1]):
            continue
        yline = float(np.interp(event.xdata, xd, ln.get_ydata()))
        px, py = ax.transData.transform((event.xdata, yline))
        d2 = (px - event.x) ** 2 + (py - event.y) ** 2
        if best is None or d2 < best[0]:
            best = (d2, xd, ln)
    if best is None:
        return
    _, xd, ln = best
    i = int(np.argmin(np.abs(xd - event.xdata)))
    x = float(xd[i])
    y = _anchor_raw_y(window, dock, ax, xd, i, ln)
    anchors.append((x, y))
    _anchor_changed(window, f"加锚点：2θ = {x:.3f}°（{len(anchors)} 个）")


def _anchor_raw_y(window: QMainWindow, dock, ax, xd, i, line) -> float:
    """锚点的 y：**原始**曲线的值，不是屏幕上那条扣过的曲线。

    锚点描述的是原始曲线的背景形状。屏幕上的实线是扣完的（在已有关键点
    处已经接近 0），照它取值会把新锚点记成 0/负值 → 该处背景等于没扣，
    而画面上看不出错；锚点还会持久化，把"显示原始曲线对比"重新勾上也不
    会自愈。

    取法按可靠性排队：
      ① 画出来的原始叠加线（bg:raw）—— 它是"这张图当时用的原始数据"的
         权威副本；
      ② 面板缓存的 last_intensity —— 用户关掉原始叠加时走这条（此时
         轴上没有 bg:raw，而缓存里的原始曲线仍在，且绘制层从不改它）；
      ③ 最后的兜底：就用屏幕上那条线的值（只有前两者都拿不到才走到，
         例如测试直接拿自定义数组调 _draw_1d）。
    """
    raw = next((ln_ for ln_ in ax.lines
                if ln_.get_gid() == _AUX_GID_PREFIX + "raw"), None)
    if raw is not None and len(raw.get_ydata()) == len(xd):
        return float(raw.get_ydata()[i])
    cached_y = getattr(dock, "last_intensity", None)
    cached_x = getattr(dock, "last_tth", None)
    if cached_y is not None and cached_x is not None \
            and len(cached_y) == len(xd) and np.allclose(cached_x, xd):
        return float(np.asarray(cached_y, dtype=float)[i])
    return float(line.get_ydata()[i])


def _anchor_changed(window: QMainWindow, msg: str) -> None:
    """锚点变动后的统一收尾：刷新计数 + 立刻重画 + 记日志。"""
    updater = getattr(window, "_bg_count_refresh", None)
    if updater is not None:
        updater(window)
    _refresh_bg(window)
    _log(window, msg)


def _heat_row_press(window: QMainWindow, key: str, event) -> None:
    """热图按下：记候选行（没拖动才算"点击"，松手再决定）。

    左键拖 = 平移（通用手势），点按 = 行联动——按下先记账，松手
    时挪动超过阈值就算平移，不算点击。
    """
    if event.inaxes is None or event.button != 1:
        return
    dock = window.plot_docks.get(key)
    if dock is None:
        return
    y = event.ydata
    n = len(getattr(dock, "heat_files", ()))
    row = int(round(y))
    if not (0 <= row < n) or abs(y - row) > 0.5:
        return   # 点在行缝/图外：不算
    dock._heat_press = (event.x, event.y, row)


def _heat_row_release(window: QMainWindow, key: str, event) -> None:
    """热图松手：按下点没怎么挪（阈值 5 px）→ 行联动——该行样品在
    所有含它的对比面板里隐藏/显示切换（再点恢复）；挪多了 = 平移，
    不动。

    隐藏集合挂在对比面板 dock.compare_hidden 上（显示名），画图/
    图例/自动纵轴同口径少掉这条曲线（见 _compare_shown_curves）；
    颜色序号不重排（颜色跟着文件走）。
    """
    dock = window.plot_docks.get(key)
    if dock is None:
        return
    info = getattr(dock, "_heat_press", None)
    dock._heat_press = None
    if info is None:
        return
    x0, y0, row = info
    if (event.x - x0) ** 2 + (event.y - y0) ** 2 > 5 ** 2:
        return   # 拖过了 = 平移手势，不是点击
    _path, display = dock.heat_files[row]
    targets = []
    for ckey, cdock in window.plot_docks.items():
        if not ckey.startswith("对比|"):
            continue
        if any(d == display for _p, d in
               getattr(cdock, "compare_files", ())):
            targets.append((ckey, cdock))
    if not targets:
        _log(window, f"热图点击 {display}：没有含该文件的对比面板")
        return
    for ckey, cdock in targets:
        hidden = set(getattr(cdock, "compare_hidden", None) or ())
        if display in hidden:
            hidden.discard(display)
            _log(window, f"热图点击 {display}：对比面板重新显示该曲线")
        else:
            hidden.add(display)
            _log(window, f"热图点击 {display}：对比面板隐藏该曲线"
                         f"（再点该行恢复）")
        cdock.compare_hidden = hidden
        _redraw_compare(window, ckey)


def _draw_heatmap(window: QMainWindow, dock, tth, matrix, stems) -> None:
    """在热图面板画出强度热图（只允许主线程调用）。

    样式：imshow 行 = 样品（origin=lower，列表第一个文件在最下）、
    列 = 2θ，颜色 = 强度，右侧颜色条。显示参数读该面板自己的快照
    （_panel_param）：热图色图 / 热图归一化（_heat_shown 三模式，
    只动显示数据）/ 热图对数（LogNorm，弱峰抬起来）/ 热图自动范围
    （自动 = 显示矩阵 1%/99.9% 分位，置灰框只读展示，同 2D 对比度
    套路；手动 = 手填上下限）。颜色条同 2D：重画先拆旧的（ax.clear
    不清 colorbar）。
    """
    ax = _content(dock).axes_heat
    keep_title, keep_xlabel, keep_ylabel, keep_scale, old_lines = \
        _snapshot_canvas(ax)
    window._setting_limits = True
    try:
        ax.clear()
        cmap = _panel_param(window, dock, "热图色图", "magma")
        mode = _panel_param(window, dock, "热图归一化", "off")
        log_c = _panel_param(window, dock, "热图对数", False)
        shown = _heat_shown(matrix, mode)
        auto = _panel_param(window, dock, "热图自动范围", True)
        if auto:
            vmin, vmax = _auto_y_range(shown, log_c)
            if window.plot_docks.get(window.focus_panel) is dock:
                window.params["热图下限"].setValue(vmin)
                window.params["热图上限"].setValue(vmax)
        else:
            vmin = _panel_param(window, dock, "热图下限", 1.0)
            vmax = _panel_param(window, dock, "热图上限", 100000.0)
            if vmax <= vmin:
                vmax = vmin * 10.0   # 手填区间不合法时兜底
        if log_c:
            vmin = max(vmin, 1e-12)   # LogNorm 画不出 ≤0
            vmax = max(vmax, vmin * 10.0)
        norm = LogNorm(vmin=vmin, vmax=vmax) if log_c \
            else Normalize(vmin=vmin, vmax=vmax)
        n = matrix.shape[0]
        im = ax.imshow(shown, aspect="auto", origin="lower", cmap=cmap,
                       norm=norm, extent=[float(tth[0]), float(tth[-1]),
                                          -0.5, n - 0.5])
        ax.set_yticks(range(n))
        ax.set_yticklabels(stems, fontsize=7)
        # 颜色条同 2D：只建一次、之后 update_normal 复用（remove+
        # 重建会让坐标轴每次再让 20% 宽度，教训 13）
        cb = getattr(dock, "_heat_colorbar", None)
        if cb is None:
            dock._heat_colorbar = ax.figure.colorbar(im, ax=ax)
        else:
            cb.update_normal(im)
        dock._heat_colorbar.ax.tick_params(labelsize=7)
        _apply_text_guards(dock, ax, keep_title, keep_xlabel, keep_ylabel)
        _content(dock).draw()
    finally:
        window._setting_limits = False
    _refresh_home(dock, ax)
    dock.figure_saved = False


def _heat_data(window: QMainWindow, dock):
    """热图面板的 (tth, matrix, stems, interp)——逐行按各文件自己的参数
    扣背景后再对齐成矩阵。

    顺序对齐：dock.heat_results 与 dock.heat_files 同序（失败项是 None
    占位），所以用**原始下标**取路径，压缩掉 None 之后再取会错位。
    空扫/锚点都按文件走，所以同一张热图里每个样品用的是它自己的基线。
    """
    rows = []
    files = getattr(dock, "heat_files", ()) or ()
    for i, r in enumerate(getattr(dock, "heat_results", ()) or ()):
        if r is None:
            continue
        stem, tth, intensity = r
        path = files[i][0] if i < len(files) else None
        _, sub, _ = _bg_curve(window, dock, path, tth, intensity)
        rows.append((stem, tth, sub))
    return _assemble_heatmap(rows)


def _finish_heatmap(window: QMainWindow, key: str) -> None:
    """热图全部数据到齐（含失败）：对齐成矩阵 → 整图画出 → 成为编辑
    对象 → 记日志。全部失败 = 面板留空。"""
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已关：静默丢弃
    data = _heat_data(window, dock)
    dock.heat_data = data
    if data is None:
        _log(window, "热图失败：所有文件的积分都失败了，面板留空")
        return
    tth, matrix, stems, interp = data
    if interp:
        _log(window, "热图提示：各文件 2θ 网格不一致，已重插值到"
                     "第一个文件的网格")
    _draw_heatmap(window, dock, tth, matrix, stems)
    _set_focus(window, key, dock.panel_display)
    _log(window, f"热图完成：{len(stems)} 个样品 × {len(tth)} 点")


def _run_heatmap(window: QMainWindow, key: str, force: bool = False) -> None:
    """跑热图计算：已算好的 1D 面板缓存直接用，缺的后台补积分。

    缓存复用 = 用户点 [热图] 时把已经算过 1D 的文件直接拿结果，
    只对没算过的文件起后台任务（进度计数走 _batch_step，批名
    "热图"）；force = 数据 [应用] 重算：用户改了数据参数，全部文件
    重新积分（缓存里的旧参数结果不可信）。代次（heat_gen）防过期：
    重复点 [热图] 旧代任务全部作废；面板代数（_panel_epoch）防关过
    重开。结果按文件列表顺序收进 dock.heat_results，全部到齐
    （含失败）收尾。
    """
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已关：迟到点击/重算不落地
    dock.params_snapshot = _data_snapshot(window, dock.params_snapshot)
    dock.heat_gen += 1
    gen = dock.heat_gen
    epoch = window._panel_epoch.get(key, 0)
    results = [None] * len(dock.heat_files)   # 文件顺序占位
    geom = _collect_geometry(window)
    npt = int(window.params["输出点数"].value())
    missing = []

    def cache_of(path, display):
        """该文件已有 1D 面板的积分缓存吗？（键同 _checked_1d_results）"""
        for k in (f"1D|{path}", f"1D|{path}|{display}"):
            d = window.plot_docks.get(k)
            if d is not None and getattr(d, "last_tth", None) is not None:
                return d.last_tth, d.last_intensity
        return None

    for i, (path, display) in enumerate(dock.heat_files):
        if not force:
            cached = cache_of(str(path), display)
            if cached is not None:
                results[i] = (Path(path).stem, cached[0], cached[1])
                continue
        missing.append((i, path, display))
    dock.heat_results = results
    dock.heat_pending = len(missing)
    # 总结行两种路径都打（全缓存 = 后台积分 0 个）：日志一眼看出
    # 这次热图用了多少新算的结果
    _log(window, f"开始热图：{len(dock.heat_files)} 个文件"
                 f"（复用已有 1D 结果，后台积分 {len(missing)} 个）")
    if missing:
        if len(missing) > 1:
            # 批量进度记账（同 _plot_view）：完成任务/失败各计一次，
            # 批走完自动清账
            window._batch = {"view": "热图", "total": len(missing),
                             "done": 0}
        for i, path, display in missing:
            # 默认参数绑定防闭包晚绑定（循环变量到回调执行时已走到末尾）
            def spawn_one(i=i, path=path, display=display):
                def done(window_, key_, task, result):
                    suffix = _batch_step(window, key_)
                    panel = window.plot_docks.get(key)
                    if (panel is None
                            or window._panel_epoch.get(key, 0) != epoch
                            or panel.heat_gen != gen):
                        return   # 旧结果静默丢弃（整图已由新代次接管）
                    tth, intensity = result
                    panel.heat_results[i] = (Path(path).stem, tth, intensity)
                    panel.heat_pending -= 1
                    _log(window, f"热图：{display} 积分完成（{len(tth)} 点）"
                                 f"{suffix}")
                    if panel.heat_pending == 0:
                        _finish_heatmap(window, key)

                def error(msg):
                    suffix = _batch_step(window, key)   # error 包装器只有
                    # msg（key 取闭包外层；done 才有 key_ 形参）
                    panel = window.plot_docks.get(key)
                    if (panel is None
                            or window._panel_epoch.get(key, 0) != epoch
                            or panel.heat_gen != gen):
                        return
                    panel.heat_pending -= 1
                    _log(window, f"热图：{display} 积分失败 — {msg}{suffix}")
                    if panel.heat_pending == 0:
                        _finish_heatmap(window, key)

                window.status_text.setText(f"正在积分 {path.name}…")
                _spawn(window, path, geom, npt, key,
                       on_done=done, on_error=error)

            spawn_one()
    else:
        _finish_heatmap(window, key)


def _plot_heatmap(window: QMainWindow) -> None:
    """[热图] 按钮：把勾选文件的 1D 曲线拼成一张 2θ×样品 强度热图。

    多文件 → 一张面板（同 [对比] 的流程形态）：横轴 2θ、纵轴样品
    （文件列表顺序，行标签 = 文件名）、颜色 = 强度——原位实验看
    峰位/强度/峰形随样品（时间/充电状态）的变化。勾选 ≥2 个文件；
    已算好的 1D 结果直接复用，缺的后台补积分（见 _run_heatmap）。
    同一勾选集合重复点 = 复用同一张面板刷新；换集合 = 新开一张。
    面板键 = "热图|排序后的路径串"（与单文件面板并存，互不干扰）。
    """
    checked = [window.file_list.item(i)
               for i in range(window.file_list.count())
               if window.file_list.item(i).checkState() == Qt.Checked]
    if len(checked) < 2:
        _log(window, "热图至少勾选两个文件（多条 1D 曲线拼成一张强度图）")
        return
    files = [(Path(item.data(Qt.UserRole)), item.text()) for item in checked]
    key = "热图|" + ",".join(sorted(str(p) for p, _ in files))
    dock = window.plot_docks.get(key)
    if dock is None:
        title = f"热图_{len(files)} 个样品"
        dock = _open_plot_panel(window, "热图", key, title)
        dock.panel_display = title   # 标题/日志/默认存盘名用
        dock.figure_saved = False
        dock.heat_files = files   # 面板绑定这组文件（重算用）
        dock.heat_gen = 0
        dock.heat_results = []
        dock.heat_pending = 0
        dock.heat_data = None
        dock.params_snapshot = _data_snapshot(window)   # 新面板：显示参数从默认起步
        _log(window, f"打开热图面板：{len(files)} 个文件拼一张强度图")
    else:
        dock.heat_files = files   # 复用面板：绑定刷新（删除重加/改名）
    dock.setVisible(True)
    _run_heatmap(window, key)
