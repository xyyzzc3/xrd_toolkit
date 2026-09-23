"""面板状态共享层：面板登记访问、参数快照、焦点回放、自动显示区间。

模块图（2026-09-18 从 app.py 拆分，调用方向永远从上往下）：
    app.py（窗口组装）→ plot_views.py（视图注册表与绘图）
      → panels.py（面板容器生命周期）→ customize.py（Customize 对话框）
      → panel_state.py（本模块：最底层共享层，不 import 任何兄弟模块）

内容：
  - _log / _content：日志与"面板容器 → 面板内容"的统一入口；
  - 参数快照：_snapshot_params（拍控件）/ _data_snapshot（数据参数
    用当前值、显示参数沿用旧快照）/ _display_snapshot（反过来）/
    _panel_param（读某面板自己的参数值，画图一律走它）——每张图
    各记各的，新开面板从默认起步、重算保留自己的旧设置；
  - 焦点与回放：_set_focus（点面板/计算完成时切编辑对象）/
    _load_params_snapshot（把快照填回参数坞，只展示不计算）；
  - _apply_config：几何配置条目应用到参数坞（PONI/倾斜角等完整
    条目挂在 window.config 供后续接线）；
  - _reload_config_combo：配置下拉框与 CONFIGS 注册表同步
    （校准工作台保存新条目后调用，保存即选中生效）；
  - _collect_geometry：从参数坞收集积分几何（像素/波长/距离 +
    配置条目的 PONI/倾斜角）；
  - 自动显示区间：_auto_y_range / _auto_contrast_values（1%/99.9%
    分位）/ _apply_auto_contrast / _apply_auto_ylim（按焦点面板
    重算填回置灰输入框）/ _compare_shown_curves（对比面板实际画
    上去的归一化后曲线——画图与自动纵轴共用同一份口径）。
"""
import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QMainWindow, QMdiSubWindow, QWidget)

from xrd_toolkit.config import CONFIGS, DEFAULT_CONFIG
from xrd_toolkit.services.background import (
    compute_baseline, subtract_background)
from xrd_toolkit.services.data_loader import load_diffraction_image


def _log(window: QMainWindow, text: str) -> None:
    """统一反馈出口：写日志区（累积）+ 状态行（常驻"就绪"，消息
    替换它 5 秒后自动恢复——状态栏永远有内容，不会隐形）。"""
    window.log_text.appendPlainText(text)
    window.status_text.setText(text)
    if window._status_timer is None:
        window._status_timer = QTimer(window)
        window._status_timer.setSingleShot(True)
        window._status_timer.timeout.connect(
            lambda: window.status_text.setText("就绪"))
    window._status_timer.start(5000)   # 重新计时


def _snapshot_params(window: QMainWindow) -> dict:
    """拍下参数面板当前状态：配置条目 + 各控件值 + 自动对比度开关。

    每张面板在开图/重算时拍一张快照挂在 dock.params_snapshot 上 =
    "这张图是用什么参数画的"；点选面板时参数坞回放快照（既能看也
    能改，改完 [应用] 重算并刷新快照）。显示参数（对数纵轴/纵轴
    范围/对比归一化）也收在快照里：每张图各记各的，画图时只读
    自己面板的快照（见 _panel_param）。
    """
    snap = {"config": window.config_name}
    for name, w in window.params.items():
        if isinstance(w, QCheckBox):
            snap[name] = w.isChecked()
        elif isinstance(w, QComboBox):
            snap[name] = w.currentData()   # 下拉框记 data（文本只是显示名）
        else:
            snap[name] = w.value()
    return snap


# 图像参数组的全部条目（"只看图不参与计算"的那一组）。快照里这两类
# 参数按职责分开更新（见 _data_snapshot / _display_snapshot）：两个
# [应用] 各管各的，互不串改。
_DISPLAY_DEFAULTS = {
    "自动对比度": True,
    "对比度下限": 1.0,
    "对比度上限": 100000.0,
    "剖面角度 (°)": 0.0,
    "对数纵轴": False,
    "纵轴自动": True,
    "纵轴下限": 1.0,
    "纵轴上限": 100000.0,
    # 对比归一化 = 模式（each 各自最强峰 / global 全图最强峰 /
    # file 指定数据 / off 不归一化，默认 off = 原样画原始强度），
    # "归一化目标" = file 模式用哪个文件
    "对比归一化": "off",
    "归一化目标": "",
    # 曲线配色 = 多曲线（对比/瀑布）的分类色调色板："高对比"（默认，
    # 固定顺序 8 槽、色盲友好）或 "默认"（matplotlib 自带循环）
    "曲线配色": "高对比",
    # 对比堆叠 = 瀑布式错开叠放（每条曲线抬到自己的行上，y 刻度 =
    # 样品名；堆叠下纵轴范围/对数不适用，同瀑布）
    "对比堆叠": False,
    "视图 2θ 下限 (°)": None,   # None = 跟随积分 2θ 范围；缩放/平移后写回显式值
    "视图 2θ 上限 (°)": None,
    # 热图显示：色图 / 归一化（与对比同款 each/global/off 三模式）/
    # 对数强度 / 强度范围（自动 = 1%/99.9% 分位）
    "热图色图": "magma",
    "热图归一化": "off",
    "热图对数": False,
    "热图自动范围": True,
    "热图下限": 1.0,
    "热图上限": 100000.0,
    # 背景扣除（1D/对比/瀑布/热图四条曲线路径共用；2D 是原始图、剖面是
    # 沿线的像素强度，都不参与）。模式 off 关闭 / blank 空扫相减 /
    # auto 自动基线 / anchor 手动锚点；锚点列表与空扫曲线不是参数，
    # 放窗级属性（window.bg_anchors / window.bg_blank）
    "背景扣除模式": "off",
    "背景窗口 (°)": 1.0,
    "空扫归一化": 1.0,
    "锚点拟合方式": "linear",
    # 显示原始曲线对比（实时预览"扣前 vs 扣后"）与负值截断
    "背景显示原始": True,
    "负值截断为 0": False,
}
_DISPLAY_PARAMS = frozenset(_DISPLAY_DEFAULTS)   # 显示参数 = 以上全部

# 曲线配色表：分类色固定顺序、颜色跟着文件走不跟排序走（第一个
# 文件永远是蓝，过滤/增删文件不会把幸存者重涂）。"高对比" 8 槽按
# OKLab 校验色盲安全（相邻对 CVD ΔE ≥ 9.1），浅色底上可用；槽用尽
# （第 9 条曲线起）回到槽 0 复用——曲线再多靠图例文字分辨，不生成
# 近似第 9 色。"默认" = matplotlib 传统（单曲线蓝 b、多曲线 C 循环）。
_CURVE_PALETTES = {
    "高对比": ("#2a78d6", "#eb6834", "#1baf7a", "#eda100",
               "#e87ba4", "#008300", "#4a3aa7", "#e34948"),
    "默认": None,
}


def _curve_color(palette: str, i: int, single: bool = False) -> str:
    """第 i 条曲线的颜色（palette = 面板快照里的"曲线配色"）。

    single=True = 单曲线面板（1D/剖面）。未知色板名兜底成高对比。
    """
    if palette == "默认":
        return "b" if single else f"C{i % 10}"   # C 循环只有 C0~C9
    slots = _CURVE_PALETTES.get(palette) or _CURVE_PALETTES["高对比"]
    return slots[i % len(slots)]


def _data_snapshot(window: QMainWindow, base: dict = None) -> dict:
    """数据参数快照：数据组取控件当前值，显示参数沿用 base（无 base
    用默认值）。

    base = 面板自己的旧快照（重算时传入）：显示参数每张图各记各的，
    重算不改它的长相；新面板（base=None）显示参数从默认起步——不
    受上一张焦点图的设置影响（开新图永远是"默认长相"）。
    """
    snap = _snapshot_params(window)
    for name in _DISPLAY_PARAMS:
        if base is not None and name in base:
            snap[name] = base[name]
        else:
            snap[name] = _DISPLAY_DEFAULTS[name]
    return snap


def _display_snapshot(window: QMainWindow, base: dict = None) -> dict:
    """显示参数快照：显示组取控件当前值，数据参数沿用 base。

    base = 面板自己的旧快照（图像 [应用] 时传入）：图像 [应用] 只改
    显示不重算，数据参数保持"这张图当时是用什么算的"，顺手改了
    数据控件也不会冒充成这张图的计算参数。
    """
    base = base or {}
    snap = {"config": window.config_name}
    for name, w in window.params.items():
        if isinstance(w, QCheckBox):
            value = w.isChecked()
        elif isinstance(w, QComboBox):
            value = w.currentData()   # 下拉框记 data（文本只是显示名）
        else:
            value = w.value()
        if name in _DISPLAY_PARAMS:
            snap[name] = value
        elif name in base:
            snap[name] = base[name]
        else:
            snap[name] = value
    return snap


def _panel_param(window: QMainWindow, dock, name: str,
                 default):
    """面板自己的参数值：优先快照（这张图作图时用的值）。

    没有快照（或快照缺这个键）时退回参数坞控件当前值——防御后路：
    以后新增画图调用点若忘了拍快照，也不至于崩溃。画图一律走这里
    读参数，而不是直接读控件：控件是"编辑对象"的编辑界面，画别的
    面板时控件可能正显示另一张图的设置。
    """
    snap = getattr(dock, "params_snapshot", None)
    if snap and name in snap:
        return snap[name]
    w = window.params.get(name)
    if w is None:
        return default
    if isinstance(w, QCheckBox):
        return w.isChecked()
    if isinstance(w, QComboBox):
        return w.currentData()
    return w.value()


def _load_params_snapshot(window: QMainWindow, snap: dict) -> None:
    """把一份面板快照回放进参数坞控件，期间挂 _param_replaying 旗标。

    旗标的作用：回放是**程序**在写控件，而每个背景扣除控件都连着
    _refresh_bg（实时预览）。不挂旗标的话回放途中的 setValue 会一路触发
    _refresh_bg → _display_snapshot，把"正回放到一半的控件状态"当成用户
    的设置写进当前面板的快照——切一次焦点就把别的面板的显示参数（实测是
    注册在背景组之后的 热图色图/热图归一化/热图对数/热图范围 这几项）
    串到本面板上，而且不可逆。详见 _load_params_snapshot_body。
    """
    window._param_replaying = True
    try:
        _load_params_snapshot_body(window, snap)
    finally:
        window._param_replaying = False


def _load_params_snapshot_body(window: QMainWindow, snap: dict) -> None:
    """把参数快照填回参数面板（只展示不计算）。

    顺序讲究：
      - 先切几何配置：触发 _apply_config，同步 window.config（PONI/
        倾斜角取该条目注册表值，[应用] 依赖它）；
      - 再填各输入框：用"当时真用的"值覆盖注册表几何值；
      - 最后处理自动对比度开关（blockSignals 防日志刷屏）：勾着 →
        静默刷新展示值（焦点是 2D/剖面 才读图，其余占位）；手动
        模式 → 回放快照里的值。

    显示参数（对数纵轴/纵轴范围/对比归一化）也一起回放：每张图的
    显示设置各记各的，点哪张图参数坞就显示哪张图作图时的设置。
    纵轴自动勾着时，最后按焦点图的实际数据重算并填回置灰输入框
    （同自动对比度的套路，见 _apply_auto_ylim）。
    """
    idx = window.config_combo.findData(snap.get("config"))
    if idx >= 0 and idx != window.config_combo.currentIndex():
        # 静默切换：拦下 currentIndexChanged，手动应用（不记日志）
        window.config_combo.blockSignals(True)
        window.config_combo.setCurrentIndex(idx)
        window.config_combo.blockSignals(False)
        _apply_config(window, idx, silent=True)
    auto = window.params.get("自动对比度")
    for name, value in snap.items():
        if name == "config" or name == "自动对比度":
            continue
        if value is None:
            continue   # 视图 2θ 范围没被用户动过 = 跟随积分范围（下方补填）
        w = window.params.get(name)
        if w is None:
            continue
        if isinstance(w, QCheckBox):
            w.setChecked(value)
        elif isinstance(w, QComboBox):
            idx = w.findData(value)
            if idx >= 0:
                w.setCurrentIndex(idx)   # data 不在列表里就保持原样（下方兜底）
        else:
            w.setValue(value)
    # 视图 2θ 范围跟随积分范围时：输入框显示"正在用的视图" =
    # 该面板快照里的积分范围（无快照值退回控件当前值，防御后路）
    for name, fallback in (("视图 2θ 下限 (°)", "2θ 下限 (°)"),
                           ("视图 2θ 上限 (°)", "2θ 上限 (°)")):
        box = window.params.get(name)
        if box is None or snap.get(name) is not None:
            continue
        other = window.params.get(fallback)
        if other is not None:
            box.setValue(snap.get(fallback, other.value()))
    if auto is not None:
        auto.blockSignals(True)
        auto.setChecked(snap.get("自动对比度", True))
        auto.blockSignals(False)
        window.params["对比度下限"].setEnabled(not auto.isChecked())
        window.params["对比度上限"].setEnabled(not auto.isChecked())
        if auto.isChecked():
            _apply_auto_contrast(window, silent=True)   # 焦点图的自动值
        else:
            window.params["对比度下限"].setValue(snap.get("对比度下限", 1.0))
            window.params["对比度上限"].setValue(
                snap.get("对比度上限", 100000.0))
    # 纵轴自动开关的联动状态同步：自动开 → 上下限输入框置灰（只读
    # 展示）。回放 setChecked 时 toggled 信号一般已联动，这里再兜底
    # 一次（同值 setChecked 不触发信号，联动状态可能停在旧面板的）；
    # 勾着自动 → 按焦点图数据重算填回（toggled 时填过一次，但随后
    # 的回放循环可能又覆盖了上下限框的值，收尾再算一次才是准的）
    auto_y = window.params.get("纵轴自动")
    if auto_y is not None:
        window.params["纵轴下限"].setEnabled(not auto_y.isChecked())
        window.params["纵轴上限"].setEnabled(not auto_y.isChecked())
        if auto_y.isChecked():
            _apply_auto_ylim(window, silent=True)
    # 热图自动范围联动（同纵轴自动套路）：自动开 → 上下限框置灰
    # 只读展示，并按焦点热图重算填回
    auto_heat = window.params.get("热图自动范围")
    if auto_heat is not None:
        window.params["热图下限"].setEnabled(not auto_heat.isChecked())
        window.params["热图上限"].setEnabled(not auto_heat.isChecked())
        if auto_heat.isChecked():
            _apply_auto_heatlim(window, silent=True)
    # 归一化目标下拉框按焦点对比面板的文件列表重建（面板里有几个
    # 文件列表就是什么样；焦点不是对比面板 = 保持原样），建完再按
    # 快照值回选——列表重建会丢掉旧选中。只在"指定数据"模式可用
    norm_target = window.params.get("归一化目标")
    if norm_target is not None:
        dock = window.plot_docks.get(window.focus_panel)
        files = getattr(dock, "compare_files", None)
        if files:
            norm_target.blockSignals(True)
            norm_target.clear()
            for path, display in files:
                # data 存字符串路径：findData 对 Path 不按 Python 相等
                # 比较（Path 不是 Qt 认识的类型），字符串才找得回
                norm_target.addItem(display, str(path))
            idx = norm_target.findData(str(snap.get("归一化目标", "")))
            norm_target.setCurrentIndex(idx if idx >= 0 else 0)
            norm_target.blockSignals(False)
        mode = window.params.get("对比归一化")
        if mode is not None:
            norm_target.setEnabled(mode.currentData() == "file")
    # 背景扣除的专用行按回放后的模式收起/放出（模式是每张图各记各的：
    # 切面板时控件值跟着变，行也得跟着变）。app 侧的函数经窗级回调
    # 调用，避免 panel_state 反向 import app
    bg_sync = getattr(window, "_bg_rows_sync", None)
    if bg_sync is not None:
        bg_sync(window)


def _set_focus(window: QMainWindow, key: str, title: str) -> None:
    """把参数面板的编辑对象切到某面板（点面板 / 计算完成时调用）。

    切过去的同时回放该面板的参数快照——点哪张图，旁边就显示哪张
    图的参数。重复点同一面板直接返回：不冲掉用户正在改的值。
    """
    if window.focus_panel == key:
        return
    window.focus_panel = key
    window.focus_label.setText(f"编辑对象：{title}")
    dock = window.plot_docks.get(key)
    snap = getattr(dock, "params_snapshot", None)
    if snap is not None:
        _load_params_snapshot(window, snap)


def _collect_geometry(window: QMainWindow) -> dict:
    """收集当前**几何配置条目**的积分几何（面板上的几何字段是只读显示）。

    分析页只读后这里没有"面板覆盖配置"的隐藏通路了：像素/波长/距离/
    PONI/倾斜角一律取 window.config["geometry"]（下拉框选中哪条就是
    哪条）；要改几何去校准页。2θ 上下限是积分设置（参与计算，不是
    显示窗口），仍从面板取：_compute_integration 原样 **geom 传给
    integrate_1d，改了范围点 [应用] 即按新区间重积分。

    （几何字段的控件仍留在 window.params 里：_apply_config 往它们填
    显示值、快照回放与测试也按这些键找控件——改的是"谁说了算"，
    不是控件本身。）
    """
    geom = window.config["geometry"]
    return dict(
        pixel_size_m=geom["pixel_size_m"],
        wavelength_m=geom["wavelength_m"],
        dist_m=geom["dist_m"],
        poni1_m=geom["poni1_m"],
        poni2_m=geom["poni2_m"],
        rot1_deg=geom["rot1_deg"],
        rot2_deg=geom["rot2_deg"],
        tth_min_deg=window.params["2θ 下限 (°)"].value(),
        tth_max_deg=window.params["2θ 上限 (°)"].value(),
    )


_CONTRAST_FALLBACK = (1.0, 100000.0)   # 没有焦点图时的自动对比度占位默认
_YLIM_FALLBACK = (1.0, 100000.0)   # 曲线无有效数值时的纵轴占位（任意但安全）


def _auto_y_range(intensity, log_scale: bool) -> tuple:
    """1D 曲线自动纵轴范围：取 1%/99.9% 分位，掐掉极端强/弱值。

    与自动对比度同一套路（见 _auto_contrast_values），只是对象从
    图像像素换成曲线强度。对数纵轴时只取正值参与分位（对数轴画
    不出 ≤0 的范围）；没有有效数值时回退占位默认。
    """
    arr = np.asarray(intensity, dtype=float)
    finite = arr[np.isfinite(arr)]
    if log_scale:
        finite = finite[finite > 0]
    if finite.size == 0:
        return _YLIM_FALLBACK
    return float(np.percentile(finite, 1.0)), float(np.percentile(finite, 99.9))


def _auto_contrast_values(image) -> tuple:
    """自动对比度：取全体像素的 1%/99.9% 分位，掐掉极端亮暗值。

    与 CLI view_diffraction 的默认 vmin/vmax 语义一致。返回 (lo, hi)；
    图像没有有效数值时报 ValueError。
    """
    arr = np.asarray(image, dtype=float)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        raise ValueError("图像没有有效数值")
    return float(np.percentile(finite, 1.0)), float(np.percentile(finite, 99.9))


def _apply_auto_contrast(window: QMainWindow, silent: bool = False) -> None:
    """按编辑对象（焦点图）重算自动对比度，填进置灰的输入框。

    输入框在自动模式下只是"程序正在用的区间"的展示（只读）。
    对比度只对 2D/剖面视图有意义：焦点是 1D/对比面板时不读文件
    （读一张大 tif 要解码整幅图，主线程上会卡），直接占位默认。
    2D/剖面 焦点 → 按该面板的文件算（图像缓存按路径存 3 张，
    同一文件反复点不再重复解码）。2D 面板真正画图时用
    _auto_contrast_values 从内存里的图像直接算，与这里的展示一致。
    """
    lo, hi = _CONTRAST_FALLBACK
    path = None
    if window.focus_panel is not None:
        dock = window.plot_docks.get(window.focus_panel)
        if dock is not None:
            view = window.focus_panel.split("|", 1)[0]
            # 对比面板没有 panel_file；1D/对比焦点不读文件（见 docstring）
            if view in ("2D", "剖面"):
                path = getattr(dock, "panel_file", None)
    loaded = False
    if path is not None:
        cache = window._image_cache
        image = cache.get(str(path))
        if image is None:
            try:
                image = load_diffraction_image(str(path))
            except Exception as err:
                if not silent:
                    _log(window, f"自动对比度：读取 {path.name} 失败（{err}），"
                                 f"恢复占位默认值")
            else:
                cache[str(path)] = image   # 路径键，改显示名不影响缓存
                while len(cache) > 3:   # 上限 3 张：弹出最早的一张
                    cache.pop(next(iter(cache)))
        if image is not None:
            try:
                lo, hi = _auto_contrast_values(image)
                loaded = True
            except ValueError:
                pass   # 图像没有有效数值：占位默认
    window.params["对比度下限"].setValue(lo)
    window.params["对比度上限"].setValue(hi)
    if not silent:
        if loaded:
            _log(window, f"自动对比度：按 {path.name} 算得 {lo:.1f} ~ {hi:.1f}")
        elif window.focus_panel is None:
            _log(window, "自动对比度：没有编辑对象，恢复占位默认值")
        else:
            _log(window, "自动对比度：编辑对象不是 2D/剖面 视图，"
                         f"恢复占位默认值")


# 决定"先扣图再积分 ≡ 先积分再扣"能否成立的几何量（2θ 范围与点数不在
# 其中：网格不一致由 interp_onto_grid 重插值处理，几何不一致没法补）
_BG_GEOM_KEYS = ("pixel_size_m", "wavelength_m", "dist_m", "poni1_m",
                 "poni2_m", "rot1_deg", "rot2_deg")


def _bg_geom_sig(geom) -> tuple:
    """几何指纹：只取真正影响积分的几何量，浮点取整避免表示误差误报。"""
    return tuple(round(float(geom.get(k, 0.0)), 12) for k in _BG_GEOM_KEYS)


def _bg_params(window: QMainWindow, dock, path) -> dict:
    """该面板当前的背景扣除参数（显示参数 + 窗级数据 → 纯函数层字典）。

    锚点按文件存（window.bg_anchors[str(path)]）——一套锚点套到不同曲线
    上是误导：每条曲线的背景形状不一样。空扫是整批实验的属性，所以放
    window.bg_blank（窗级），天然免疫面板弹出/收回（panels.py 的
    _PANEL_ATTRS 弹出白名单只搬少量属性，dock 上的新属性会丢）。
    """
    return {
        "mode": _panel_param(window, dock, "背景扣除模式", "off"),
        "window_deg": _panel_param(window, dock, "背景窗口 (°)", 1.0),
        "blank_scale": _panel_param(window, dock, "空扫归一化", 1.0),
        "anchors": (getattr(window, "bg_anchors", None) or {}).get(str(path),
                                                                  []),
        "anchor_method": _panel_param(window, dock, "锚点拟合方式", "linear"),
    }


def _bg_curve(window: QMainWindow, dock, path, tth, intensity):
    """按面板显示参数扣背景，返回 (tth, 扣后强度, 基线或 None)。

    模式关闭（或选了空扫但还没积分）时原样返回、基线为 None——调用方
    据此决定要不要画"原始/基线"辅助线。**缓存里的曲线永远不动**：扣除
    只发生在绘制时，所以参数一变重画即可，不需要重新积分（实时预览的
    前提，见 services/background.py 里关于积分线性的说明）。
    path 可以是 None（拿不到路径的场景）→ 该曲线按无锚点处理。
    """
    params = _bg_params(window, dock, path)
    blank = getattr(window, "bg_blank", None)
    blank_curve = None
    if params["mode"] == "blank" and blank is not None:
        blank_curve = (blank["tth"], blank["intensity"])
        # 几何不一致时"两图各自积分再相减"不再等价于"两图相减再积分"
        # （前提是同一个 2θ 环带）。只在状态翻转时提示一次——本函数在
        # 每次重画都会跑，每次记日志会刷屏
        sig = blank.get("geom_sig")
        bad_geom = (sig is not None
                    and sig != _bg_geom_sig(_collect_geometry(window)))
        if bad_geom != getattr(window, "_bg_geom_warned", False):
            window._bg_geom_warned = bad_geom
            if bad_geom:
                _log(window, "背景扣除提示：空扫图与当前几何不一致"
                             "（像素/波长/距离/束心/倾斜有变化），"
                             "空扫应重新按相同几何积分")
        # 空扫没覆盖到的 2θ 区间不扣（interp_onto_grid 在那里返回 0）——
        # 用户在覆盖范围外看到"没扣"却没有任何提示，会以为功能坏了
        b_tth = np.asarray(blank["tth"], dtype=float)
        uncovered = (b_tth.size > 0
                     and (float(b_tth[0]) > float(tth[0]) + 1e-9
                          or float(b_tth[-1]) < float(tth[-1]) - 1e-9))
        if uncovered != getattr(window, "_bg_cover_warned", False):
            window._bg_cover_warned = uncovered
            if uncovered and b_tth.size:
                _log(window, f"背景扣除提示：空扫只覆盖 "
                             f"{b_tth[0]:.3f}~{b_tth[-1]:.3f}°，"
                             f"该区间以外的数据未扣背景")
    base = compute_baseline(tth, intensity, params, blank_curve=blank_curve)
    if base is None:
        return tth, intensity, None
    clip = _panel_param(window, dock, "负值截断为 0", False)
    return tth, subtract_background(intensity, base, clip_negative=clip), base


def _compare_shown_curves(window: QMainWindow, dock) -> list:
    """对比面板实际画上去的曲线：[(tth, shown, display, i), ...]。

    shown = 按该面板自己的"对比归一化"设置处理后的显示数据（归一化
    是显示层，原始结果原样保留在 compare_data）；i = 在 compare_files
    里的序号（决定颜色/图例顺序）。画图（_redraw_compare）与自动
    纵轴（_apply_auto_ylim）共用这一份数据——两边口径一致，置灰框
    显示的区间才跟图对得上。热图联动隐藏的样品（dock.compare_hidden
    里的显示名）不参与：画图、图例、自动纵轴同时少掉这条曲线。

    归一化四模式（与用户讨论定稿）：
      each   各自最强峰：每条曲线除以自己的最强峰
      global 全图最强峰：所有曲线除以全部曲线里最高的峰
      file   指定数据：所有曲线除以"归一化目标"那个文件的最强峰
      off    不归一化（默认：原样画原始强度）
    旧快照里的 True/False 兼容（True = each、False = off）。
    """
    mode = _panel_param(window, dock, "对比归一化", "off")
    if isinstance(mode, bool):   # 旧快照兼容：True = 各自最强峰
        mode = "each" if mode else "off"
    target_path = _panel_param(window, dock, "归一化目标", "") \
        if mode == "file" else ""
    # 先把原始数据全收起来：global/file 的除数要等所有曲线到齐才算。
    # 热图联动隐藏的样品（dock.compare_hidden）直接跳过：不算除数、
    # 不进图例、不占颜色槽——其他曲线的颜色序号不变（颜色跟着文件
    # 走的承诺在隐藏/恢复来回切时也不破）
    hidden = set(getattr(dock, "compare_hidden", None) or ())
    raw_curves = []
    for i, (path, display) in enumerate(dock.compare_files):
        if display in hidden:
            continue
        if display not in dock.compare_data:
            continue   # 这条还没算成（本函数只在全部到齐后调用）
        tth, raw = dock.compare_data[display]
        # 背景扣除在归一化**之前**：先扣掉不含结构信息的加性背景，
        # 再谈"相对强度"才有意义（归一化会把这个尺度信息抹掉）
        _, raw, _ = _bg_curve(window, dock, path, tth,
                              np.asarray(raw, dtype=float))
        raw_curves.append((tth, np.asarray(raw, dtype=float), display,
                           i, path))
    divisor = 1.0
    if mode == "global":
        divisor = max((float(np.nanmax(r)) if r.size else 0.0
                       for _, r, _, _, _ in raw_curves), default=0.0)
    elif mode == "file":
        # 按路径找目标文件的最强峰（快照记字符串路径）；找不到
        # （快照过期防御）= 除数保持 1.0 = 不归一化
        for _, r, _, _, p in raw_curves:
            if str(p) == str(target_path):
                divisor = float(np.nanmax(r)) if r.size else 0.0
                break
    if divisor <= 0:
        divisor = 1.0
    curves = []
    for tth, raw, display, i, _ in raw_curves:
        shown = raw
        if mode == "each":
            peak = float(np.nanmax(raw)) if raw.size else 0.0
            if peak > 0:
                shown = raw / peak
        elif mode != "off":
            shown = raw / divisor   # global/file 共享同一个除数
        curves.append((tth, shown, display, i))
    return curves


def _heat_shown(matrix, mode):
    """热图实际画上去的强度矩阵（归一化是显示层，原始结果原样保留）。

    mode 三选（与对比归一化同款语义，热图没有"指定文件"）：
      each   每行最强峰：每个样品除以自己的最强峰（观察峰形/峰位
             随样品的变化，绝对强度差异抹平——原位实验最常用）
      global 全图最强峰：全体除以最强样品的最强峰
      off    不归一化（默认：原样画原始强度）
    画图（_draw_heatmap）与自动强度范围（_apply_auto_heatlim）共用
    这一份口径，置灰框显示的区间才跟图对得上。
    """
    shown = np.asarray(matrix, dtype=float)
    if mode == "each":
        peaks = np.nanmax(shown, axis=1)
        peaks = np.where(np.isfinite(peaks) & (peaks > 0), peaks, 1.0)
        shown = shown / peaks[:, None]
    elif mode == "global":
        peak = float(np.nanmax(shown)) if np.isfinite(shown).any() else 0.0
        if peak > 0:
            shown = shown / peak
    return shown


def _apply_auto_ylim(window: QMainWindow, silent: bool = False) -> None:
    """按编辑对象（焦点图）重算自动纵轴范围，填进置灰的输入框。

    与 _apply_auto_contrast 同款：自动模式下输入框只是"程序正在用
    的区间"的只读展示。1D 焦点 → 按该面板曲线数据算；对比焦点 →
    按叠图显示数据（含归一化）算；剖面焦点 → 按剖面强度曲线算；
    2D 没有纵轴概念、或还没算完 → 占位默认。画图时（_draw_1d/
    _redraw_compare/_draw_profile）也会填一次，所以焦点图刚算完/
    刚 [应用] 后输入框一定是准的。
    """
    ylo, yhi = _YLIM_FALLBACK
    loaded = False
    if window.focus_panel is not None:
        dock = window.plot_docks.get(window.focus_panel)
        if dock is not None:
            view = window.focus_panel.split("|", 1)[0]
            log_y = _panel_param(window, dock, "对数纵轴", False)
            if view == "1D" and getattr(dock, "last_tth", None) is not None:
                ylo, yhi = _auto_y_range(dock.last_intensity, log_y)
                loaded = True
            elif view == "对比" and getattr(dock, "compare_data", None):
                shown = [s for _, s, _, _ in _compare_shown_curves(window, dock)]
                if shown:
                    ylo, yhi = _auto_y_range(np.concatenate(shown), log_y)
                    loaded = True
            elif view == "剖面" and getattr(dock, "last_profile_t",
                                            None) is not None:
                ylo, yhi = _auto_y_range(dock.last_profile_intensity, log_y)
                loaded = True
    window.params["纵轴下限"].setValue(ylo)
    window.params["纵轴上限"].setValue(yhi)
    if not silent and loaded:
        _log(window, f"自动纵轴：编辑对象算得 {ylo:.4g} ~ {yhi:.4g}")


def _apply_auto_heatlim(window: QMainWindow, silent: bool = False) -> None:
    """按编辑对象（焦点热图）重算热图强度范围，填进置灰的输入框。

    与 _apply_auto_ylim 同款：自动模式下输入框只是"程序正在用的
    区间"的只读展示。焦点不是热图面板（或还没算完）= 占位默认。
    范围按显示数据（含归一化/对数）算，与 _draw_heatmap 画图口径
    一致（见 _heat_shown）。
    """
    lo, hi = _YLIM_FALLBACK
    loaded = False
    if window.focus_panel is not None:
        dock = window.plot_docks.get(window.focus_panel)
        if (dock is not None
                and window.focus_panel.split("|", 1)[0] == "热图"):
            data = getattr(dock, "heat_data", None)
            if data is not None:
                _, matrix, _, _ = data
                shown = _heat_shown(matrix, _panel_param(
                    window, dock, "热图归一化", "off"))
                lo, hi = _auto_y_range(shown, _panel_param(
                    window, dock, "热图对数", False))
                loaded = True
    window.params["热图下限"].setValue(lo)
    window.params["热图上限"].setValue(hi)
    if not silent and loaded:
        _log(window, f"热图范围：编辑对象算得 {lo:.4g} ~ {hi:.4g}")


def _apply_config(window: QMainWindow, index: int, silent: bool = False) -> None:
    """把下拉框选中的配置条目应用到参数面板。

    几何值来自 config.py 注册表（标定值，不是占位默认值）。只同步
    参数坞里已有的三个输入框（像素/波长/距离）；PONI、倾斜角等其余
    几何键随完整条目一起挂在 window.config 上，留给后续图面板接线。
    silent = 快照回放时的静默切换（用户没动手，不记日志）。
    """
    key = window.config_combo.itemData(index)
    cfg = CONFIGS[key]
    geom = cfg["geometry"]
    window.config_name = key
    window.config = cfg   # 完整条目（label / geometry / beam_center）
    # （几何配置完整备注现在只走下拉框的悬停提示，不再单独写说明行）
    window.params["像素尺寸 (µm)"].setValue(geom["pixel_size_m"] * 1e6)
    window.params["波长 (Å)"].setValue(geom["wavelength_m"] * 1e10)
    window.params["初始距离 (mm)"].setValue(geom["dist_m"] * 1e3)
    if not silent:
        _log(window, f"已加载几何配置 {key}（{cfg['label']}）")


def _reload_config_combo(window: QMainWindow,
                         select_key: str | None = None) -> None:
    """配置下拉框与 CONFIGS 注册表同步（GUI 保存新条目后调用）。

    重建全部条目（内置在前、用户条目在后，按 CONFIGS 顺序），悬停
    提示保留（用户条目附加残差），最后选中 select_key：索引变化触发
    _apply_config 把几何填进参数坞（保存即生效）。select_key 为 None
    时保持当前选择不变。
    """
    combo = window.config_combo
    if select_key is None:
        select_key = combo.itemData(combo.currentIndex())
    combo.blockSignals(True)
    combo.clear()
    for name, entry in CONFIGS.items():
        combo.addItem(name, name)
        tip = entry["label"]
        if "residual_deg" in entry:
            tip += f"（残差 {entry['residual_deg']:.4f}°）"
        combo.setItemData(combo.count() - 1, tip, Qt.ToolTipRole)
    combo.blockSignals(False)
    idx = combo.findData(select_key)
    if idx < 0:
        idx = 0
    if idx != combo.currentIndex():
        combo.setCurrentIndex(idx)   # 索引变化 → currentIndexChanged → _apply_config
    else:
        _apply_config(window, idx)   # 索引没变信号不触发：手动应用


def _content(dock) -> QWidget:
    """面板容器 → 面板内容：子窗口用 widget()，弹出窗口用 content。"""
    if isinstance(dock, QMdiSubWindow):
        return dock.widget()
    return dock.content
