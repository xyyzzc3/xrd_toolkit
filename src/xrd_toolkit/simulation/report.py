"""拟合结果的一页式摘要（2026-10-26）。

用户先看结论，开发者再往下看细节——两者写在同一个 `.txt` 里
（用户 2026-10-26 拍板方案 A：摘要在前、详情在后）。

摘要只用“人话”+ 关键数字：样品、主相、晶胞、尺寸、应变、拟合质量、
提示。完整参数表仍由 `FitResult.report()` 提供，接在摘要后面。
"""
import math


def format_pm(value, esd=None, *, unit="", max_decimals=5):
    """“数值 ± 不确定度”；esd 缺失/无效时只给数值。

    小数位跟着 esd 的首位有效数字走：a=10.3522、esd=0.0013 →
    “10.3522 ± 0.0013”，不堆无意义的尾零。
    """
    if esd is None or not math.isfinite(esd) or esd <= 0:
        return f"{value:.4f} {unit}".strip()
    decimals = 1 - int(math.floor(math.log10(abs(esd))))
    decimals = max(1, min(max_decimals, decimals))
    return f"{value:.{decimals}f} ± {esd:.{decimals}f} {unit}".strip()


def format_size_um(value_um):
    """µm 与 nm 同时给，用户不必自己换算。"""
    return f"{value_um * 1000:.0f} nm（{value_um:.3f} µm）"


def format_strain(mustrain):
    """mustrain 是引擎内部单位；换算成 ε（与脚本里的 ×1e3/4e6 同式）。"""
    return f"{mustrain / 4.0e6 * 1e3:.2f} × 10⁻³"


def fixed_cell_text(structure):
    """晶胞没被精修时，摘要里明说“用的是 CIF 值”。"""
    a, b, c = (float(v) for v in structure.lattice.abc)
    if max(a, b, c) - min(a, b, c) < 1e-4:
        return f"固定为 CIF 值（a = {a:.4f} Å）"
    return (f"固定为 CIF 值（a = {a:.4f}, b = {b:.4f}, "
            f"c = {c:.4f} Å）")


def fit_entries(sample, phase, result, *, lebail=None, al_scale=None,
                cell_fixed=None):
    """全谱拟合的摘要条目 + 提示（fit_lmfp / analyze_pattern /
    simulate_study 共用）。"""
    entries = [("样品", sample), ("主相", phase)]
    params = result.params
    stderr = result.stderr
    if all(k in params for k in ("a", "b", "c")):
        entries.append((
            "晶胞",
            f"a = {format_pm(params['a'], stderr.get('a'), unit='Å')}, "
            f"b = {format_pm(params['b'], stderr.get('b'), unit='Å')}, "
            f"c = {format_pm(params['c'], stderr.get('c'), unit='Å')}"))
    elif cell_fixed:
        entries.append(("晶胞", cell_fixed))

    notes = []
    if "size_um" in params:
        entries.append(("晶粒尺寸", format_size_um(params["size_um"])))
    if "mustrain" in params:
        mustrain = params["mustrain"]
        esd = stderr.get("mustrain")
        if mustrain <= 0.0 or (esd is not None and math.isfinite(esd)
                               and esd > 10.0 * abs(mustrain)):
            entries.append(("微应变", "不可辨识（停在下限 0）"))
            notes.append("微应变不可辨识：数据对峰宽随角度的变化不敏感")
        else:
            entries.append(("微应变", format_strain(mustrain)))
    else:
        entries.append(("微应变", "未精修（固定 0）"))

    if "b_iso" in params:
        b_iso = params["b_iso"]
        b_esd = stderr.get("b_iso")
        if (b_esd is not None and math.isfinite(b_esd)
                and b_esd > abs(b_iso)):
            entries.append(("温度因子 ΔB",
                            f"基本未定（{b_iso:.2f} ± {b_esd:.2f} Å²）"))
        else:
            entries.append(("温度因子 ΔB",
                            format_pm(b_iso, b_esd, unit="Å²")))
        if b_iso > 2.0:
            notes.append(f"B = {b_iso:.2f} Å² 偏大，"
                         "峰强度以 Le Bail 结果为准")

    quality = f"Rwp {result.rwp:.2f}%"
    if lebail is not None:
        quality = (f"结构 Rwp {result.rwp:.2f}% / "
                   f"Le Bail {lebail.rwp:.2f}%")
    entries.append(("拟合质量", quality))

    if al_scale is not None:
        entries.append(("第二相", f"Al 基片（相对强度 {al_scale:.2f}）"))
    if not result.success:
        notes.append("优化器未收敛，结果仅供参考")
    return entries, notes


def summary_block(entries, notes=(), *, title="结果摘要"):
    """结论页 + 一条“详情从这里开始”的分隔线。

    返回的字符串末尾已经带空行，调用方直接接详情正文即可。
    """
    lines = [f"===== {title} ====="]
    for label, text in entries:
        lines.append(f"{label}: {text}")
    if notes:
        lines.append("提示: " + "；".join(notes))
    lines += ["", "===== 以下为复现与排错详情 =====", ""]
    return "\n".join(lines)
