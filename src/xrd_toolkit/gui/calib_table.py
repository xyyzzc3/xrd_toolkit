"""校准页的「三列表」：当前配置 / A / B + Δ 行 + 结论。

从 calib.py 拆出来（纯搬迁）：只做展示与选择（槽下拉=选结果、
以 A/B 为准=采纳），判断都在 calib_model.py。Δ 行与结论的参照系
固定是「当前配置」（「对比基准」下拉框 2026-09-30 已删）。
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
    """按三个槽的当前内容刷新数值表 + Δ 行 + 结论。

    参照系固定 = **当前配置**（用户 2026-09-30："直接把对比基准删了，直接出
    结论当前配置和 a、b 对比分别怎么样，随着用户选择 ab 当前进行变化"）。
    「对比基准」下拉框已经删掉——Δ 行与结论讲的是同一对人（每列 vs 当前配置），
    A/B 槽换一条结果，Δ 与结论立刻跟着变。
    """
    state = _calib_state(window)
    res = {slot: _slot_result(state, slot) for slot in ("current", "A", "B")}
    base_slot = "current"
    base_res = res.get(base_slot)
    for key_, _name, _scale, _fmt, has_delta in COMPARE_ROWS:
        vals = {slot: _row_values(res[slot])[key_] for slot in ("current", "A", "B")}
        for slot in ("current", "A", "B"):
            window.calib_vals[slot][key_].setText(_fmt_row(key_, vals[slot]))
        if has_delta:
            for slot in ("current", "A", "B"):
                window.calib_vals["delta"][key_][slot].setText(
                    # 基准列自身写"基准"而不是"—"：那个破折号看起来像
                    # "这格没数据"（用户 2026-09-27："表述不清"）
                    "基准" if slot == base_slot
                    else _delta_text(base_res, res[slot], key_))
    # 结论逐个候选报"它 vs 当前配置"：A、B 都写全（用户 2026-09-30 把参照系
    # 钉死在当前配置——不再由用户选基准，也就不会出现"基准选着 A、结论在讲
    # 别的"这种要多想一步的表述）。Δ 行与结论看的是同一对人。
    others = [(SLOT_LABELS[slot], res[slot])
              for slot in ("current", "A", "B")
              if slot != base_slot and res[slot] is not None]
    window.calib_verdict.setText(
        _verdict(base_res, SLOT_LABELS.get(base_slot, "基准"), others))
