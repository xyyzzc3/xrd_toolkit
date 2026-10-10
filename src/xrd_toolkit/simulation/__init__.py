"""XRD 模拟：从晶体结构文件（CIF）计算理论粉末 XRD 图谱。

由 RY04 第六周任务并入（2026-10-10）。计算与绘图分开：core 出数字，
plots 出图。pymatgen 属可选依赖（见 pyproject 的
[project.optional-dependencies].simulation）。
"""
from xrd_toolkit.simulation.core import (
    d_spacing,
    distinct_rotations,
    format_hkls,
    load_structure,
    lp_factor,
    multiplicity,
    pattern_from_cif,
    pattern_from_cif_pymatgen,
    pattern_from_structure,
    pattern_from_structure_pymatgen,
    scattering_factor,
    structure_factor,
    two_theta_from_d,
)
from xrd_toolkit.simulation.plots import plot_annotated, plot_pattern

__all__ = [
    "d_spacing", "two_theta_from_d", "scattering_factor", "structure_factor",
    "distinct_rotations", "multiplicity", "lp_factor", "format_hkls",
    "pattern_from_structure", "pattern_from_cif",
    "pattern_from_structure_pymatgen", "pattern_from_cif_pymatgen",
    "load_structure", "plot_pattern", "plot_annotated",
]
