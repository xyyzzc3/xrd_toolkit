"""原位双起点策略（2026-11-27）：两个引擎共用一套判断。

2026-11-24 在旧 LMFP 引擎里加了"种子 Rwp 高位也补 CIF 起点"的守卫；
2026-11-27 抽到这里，通用引擎也调用同一份，避免两边再分叉。

判断只依赖标量（success / rwp / 主相分数 / 是否已从 CIF 起步 / 模式 /
守卫阈值），不依赖具体拟合结果类型，所以两个引擎都能直接用。
"""
from __future__ import annotations

import math

# 默认守卫：种子解的 Rwp 达到这个值（百分点）时，即使 success=True
# 也补跑 CIF 端元起点。2.45 来自 81 帧 LMFP 批次：Rwp ≥ 2.45 的只有
# 帧 27–30，实测帧 28–30 的 CIF 起点更好，帧 27 仍是种子胜出。
DEFAULT_AUTO_RWP_GUARD = 2.45


def needs_second_start(success, rwp, fraction, cif_is_seed, mode,
                       guard=DEFAULT_AUTO_RWP_GUARD):
    """自动双起点是否跑 CIF 端元起点。

    * ``off``    —— 不跑；
    * ``always`` —— 只要当前种子不是 CIF 端元就跑；
    * 其它值（含 ``auto`` 和空值）—— 自动：
        - 种子解没有严格收敛 → 跑；
        - 主相分数读不出来（NaN/异常）→ 跑（保守）；
        - 种子 Rwp 达到 ``guard`` → 跑；
        - ``guard`` 为 None / 非正数 → 只看 success（旧通用引擎行为）。
    """
    if cif_is_seed:
        return False
    mode = str(mode or "auto").strip().lower()
    if mode == "always":
        return True
    if mode == "off":
        return False
    if not bool(success):
        return True
    if guard is None:
        return False
    try:
        guard_value = float(guard)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(guard_value) or guard_value <= 0:
        return False
    try:
        fraction_value = float(fraction)
    except (TypeError, ValueError):
        return True
    if not math.isfinite(fraction_value):
        return True
    try:
        rwp_value = float(rwp)
    except (TypeError, ValueError):
        return True
    return math.isfinite(rwp_value) and rwp_value >= guard_value
