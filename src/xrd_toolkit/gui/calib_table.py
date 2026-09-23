"""校准页的「三列表」：当前配置 / A / B + Δ 行 + 基准。

从 calib.py 拆出来（纯搬迁）：只做展示与选择（槽下拉=选结果、
基准下拉=选参考列、以 A/B 为准=采纳），判断都在 calib_model.py。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QMainWindow,
                               QVBoxLayout)

from xrd_toolkit.gui.calib_model import (COMPARE_HINT, COMPARE_ROWS,
                                         _calib_state,
                                         SLOT_LABELS, _delta_text,
                                         _fmt_row, _result_by_name,
                                         _row_values, _slot_label,
                                         _slot_result, _verdict)
from xrd_toolkit.gui.panel_state import _log


def _sync_slot_combos(window: QMainWindow) -> None:
    """把累积结果填进三个槽的下拉框（状态是唯一真相，重填时保留选择）。"""
    state = _calib_state(window)
    names = [item["name"] for item in state["results"]]
    for slot, combo in window.calib_slot_combo.items():
        combo.blockSignals(True)
        combo.clear()
        if slot == "current":
            combo.addItem(_slot_label(state, "current"), None)
        else:
            combo.addItem("—（空）", None)
        for name in names:
            combo.addItem(name, name)
        idx = combo.findData(state["slots"].get(slot))
        combo.setCurrentIndex(max(0, idx))
        combo.blockSignals(False)


def _on_slot_changed(window: QMainWindow, slot: str) -> None:
    """某个槽的下拉框选了一条结果（或清空）。"""
    from xrd_toolkit.gui.calib import (_adopt_result, _calib_sync)   # 破循环：见本模块说明
    state = _calib_state(window)
    name = window.calib_slot_combo[slot].currentData()
    if name is None:
        if slot != "current" and state["slots"][slot] is not None:
            state["slots"][slot] = None
            state["pinned"][slot] = False
            _log(window, f"{slot} 槽清空（取消钉住）")
            _calib_sync(window)
        return
    if slot == "current":
        if state["slots"]["current"] != name:
            _adopt_result(window, name, why="手动选择")
    else:
        state["slots"][slot] = name
        state["pinned"][slot] = True
        _log(window, f"{slot} 槽 → {name}（钉住：新结果不再覆盖它）")
    _calib_sync(window)


def _refresh_table(window: QMainWindow) -> None:
    """按三个槽的当前内容刷新数值表 + Δ 行 + 结论。"""
    state = _calib_state(window)
    res = {slot: _slot_result(state, slot) for slot in ("current", "A", "B")}
    base_slot = window.calib_base_combo.currentData()
    base_res = res.get(base_slot)
    for key_, _name, _scale, _fmt, has_delta in COMPARE_ROWS:
        vals = {slot: _row_values(res[slot])[key_] for slot in ("current", "A", "B")}
        for slot in ("current", "A", "B"):
            window.calib_vals[slot][key_].setText(_fmt_row(key_, vals[slot]))
        if has_delta:
            for slot in ("current", "A", "B"):
                window.calib_vals["delta"][key_][slot].setText(
                    "—" if slot == base_slot
                    else _delta_text(base_res, res[slot], key_))
    other = "A" if base_slot != "A" else "B"
    window.calib_verdict.setText(
        _verdict(base_res, res[other], SLOT_LABELS[base_slot],
                 SLOT_LABELS[other]))
