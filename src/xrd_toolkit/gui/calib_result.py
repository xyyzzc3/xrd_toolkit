"""校准结果落地：一条结果进列表、判是否采纳、填 A/B 槽。

2026-10-22 从 calib.py 原样搬出（只改结构，不动逻辑）。
calib 再导出。
"""
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.gui.calib_calibrant import _adopt_result
from xrd_toolkit.gui.calib_form import _calib_sync
from xrd_toolkit.gui.calib_metrics import _metrics_note
from xrd_toolkit.gui.calib_model import (
    KIND_LABELS, _add_result, _adopt_decision, _calib_state, _fill_slot)
from xrd_toolkit.gui.panel_state import _log
from xrd_toolkit.services.calibrants import (
    DEFAULT_CALIBRANT, calibrant_short_label)


# ══ 结果 / Δ / 保存为配置 ════════════════════════════════════
def _on_calib_result(window: QMainWindow, kind: str, result: dict) -> None:
    """一次校准结果落地（主线程）：进累积列表 → 判要不要换当前配置 → 填槽。

    顺序有讲究：先把结果挂进列表（这样日志与槽都有名字可用），再判
    "拟合得好不好"（_adopt_decision），采纳就换当前配置并重画青线，最后
    按 A→B 轮换填没被钉住的槽。
    """
    state = _calib_state(window)
    got_cal = result.get("calibrant")
    if got_cal and got_cal != state.get("calibrant"):
        # 任务在飞时换过标样：这是一份旧标样的结果，进列表只会误导
        _log(window, f"{KIND_LABELS.get(kind, kind)}结果作废：标样已从 "
                     f"{calibrant_short_label(got_cal)} 切到 "
                     f"{calibrant_short_label(state.get('calibrant'))}")
        return
    name = _add_result(state, kind, result)
    label = KIND_LABELS.get(kind, kind)
    if result.get("reindex_note"):
        # 判环换过一套（尺度扫描赢的）：先写这句，下面的结果才读得懂
        _log(window, result["reindex_note"])
    cal_txt = calibrant_short_label(got_cal or state.get("calibrant")
                                    or DEFAULT_CALIBRANT)
    rings_txt = (f"{result['n_rings']} 环"
                 if result.get("n_rings") else "")
    _log(window, f"{label}完成（{name}，{cal_txt}{'，' + rings_txt if rings_txt else ''}）："
                 f"距离 {result['dist_m'] * 1000:.2f} mm，"
                 f"PONI ({result['poni1_px']:.2f}, {result['poni2_px']:.2f}) px，"
                 f"残差 {result['residual_deg']:.4f}°"
                 f"{_metrics_note(result)}")
    take, note = _adopt_decision(state, name)
    if take:
        # 一律采纳（用户 2026-09-30："只要是用户操作的……都填入当前，
        # 让用户看到变化"）——青环立刻跟着动，说明里写明好了/差了多少
        _adopt_result(window, name, why="自动采纳")
    _log(window, note)
    _log(window, _fill_slot(state, name))
    _calib_sync(window)
    if kind == "manual" and state.get("manual_n", 99) < 6:
        # 点数少时最小二乘对单点点击误差敏感，残差小也不代表可信
        _log(window, "提示：点数较少（<6）时手动结果可能不稳，建议多点"
                     "几个环上的点再跑一次，以环位偏差判断可信度")
