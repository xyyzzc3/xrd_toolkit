"""自动显示区间：1D 纵轴、2D/剖面对比度、热图强度范围。

2026-10-21 从 panel_state.py 原样搬出（只改结构，不动逻辑）。
panel_state 再导出，app/drawing 照旧经 gui_panel_state 取用。
"""
import numpy as np
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.gui.state.base import _log, _panel_param, _param_box_set
from xrd_toolkit.gui.state.processing import (
    _compare_shown_curves, _heat_shown, _proc_curve)
from xrd_toolkit.services.data_loader import load_diffraction_image


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
        raise ValueError("图没有有效数值")
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
                    _log(window, f"自动对比度：读取 {path.name} 失败，"
                                 f"已恢复默认值（原因：{err}）")
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
    _param_box_set(window, "对比度下限", lo)
    _param_box_set(window, "对比度上限", hi)
    if not silent:
        if loaded:
            _log(window, f"自动对比度：按 {path.name} 算得 {lo:.1f}–{hi:.1f}")
        elif window.focus_panel is None:
            _log(window, "自动对比度：没有编辑对象，已恢复默认值")
        else:
            _log(window, "自动对比度：编辑对象不是 2D/剖面 视图，"
                         f"已恢复默认值")

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
                # 用**画出来的那条**算范围（背景/平滑/裁剪都算进去）：
                # 与 _draw_1d 同一口径——否则裁剪掉的巨峰还会把范围撑回去，
                # 置灰框里显示的数字也跟图对不上
                _, shown, _ = _proc_curve(window, dock,
                                          getattr(dock, "panel_file", None),
                                          dock.last_tth, dock.last_intensity)
                ylo, yhi = _auto_y_range(shown, log_y)
                loaded = True
            elif view == "对比" and getattr(dock, "compare_data", None):
                # 用 ref（不跑背景/平滑的那份）定范围：调背景时框不动
                refs = [c[4] for c in _compare_shown_curves(window, dock)]
                if refs:
                    ylo, yhi = _auto_y_range(np.concatenate(refs), log_y)
                    loaded = True
            elif view == "剖面" and getattr(dock, "last_profile_t",
                                            None) is not None:
                ylo, yhi = _auto_y_range(dock.last_profile_intensity, log_y)
                loaded = True
    # 回填挂旗标（同 _apply_auto_contrast/_apply_auto_heatlim，见本文件
    # 2026-10-08 的旗标说明）：裸 setValue 会触发即改即画钩子、抢在
    # 调用方的重画之前重入一次画图（同 _draw_1d 那批 2026-10-08 的病）
    _param_box_set(window, "纵轴下限", ylo)
    _param_box_set(window, "纵轴上限", yhi)
    if not silent and loaded:
        _log(window, f"自动纵轴：编辑对象算得 {ylo:.4g}–{yhi:.4g}")

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
    _param_box_set(window, "热图下限", lo)
    _param_box_set(window, "热图上限", hi)
    if not silent and loaded:
        _log(window, f"热图范围：编辑对象算得 {lo:.4g}–{hi:.4g}")

