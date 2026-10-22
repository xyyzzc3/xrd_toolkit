"""XRD 模拟：从晶体结构文件（CIF）计算理论粉末 XRD 图谱。

由 RY04 第六周任务并入（2026-10-10）。计算与绘图分开：core 出数字，
plots 出图。pymatgen 属可选依赖（见 pyproject 的
[project.optional-dependencies].simulation）。
"""
from xrd_toolkit.simulation.core import (
    b_iso_from_cif,
    d_spacing,
    distinct_rotations,
    format_hkls,
    load_structure,
    lp_factor,
    march_dollase,
    multiplicity,
    pattern_from_cif,
    pattern_from_cif_pymatgen,
    pattern_from_structure,
    pattern_from_structure_pymatgen,
    scattering_factor,
    structure_factor,
    two_theta_from_d,
)
from xrd_toolkit.simulation.gsas_profile import (
    W_CONV,
    GAM_CONV,
    instrument_sig_gam,
    legacy_to_gsas,
    lp_gsas,
    polarization,
    pv_area_normalized,
    reflection_sig_gam,
    sample_sig_gam,
    sig_gam_to_fwhm_eta,
    simulate_pattern_gsas,
)
from xrd_toolkit.simulation.fit import FitResult, fit_pattern, rwp
from xrd_toolkit.simulation.plots import (
    plot_annotated,
    plot_fit,
    plot_fit_bottom,
    plot_pattern,
    plot_profile,
)
from xrd_toolkit.simulation.profile import (
    caglioti_fwhm_deg,
    lorentz_fwhm_deg,
    profile_fwhm_eta,
    pseudo_voigt,
    scherrer_fwhm_deg,
    simulate_pattern,
    strain_fwhm_deg,
    tch_fwhm_eta,
)

__all__ = [
    "b_iso_from_cif",
    "d_spacing", "two_theta_from_d", "scattering_factor", "structure_factor",
    "march_dollase",
    "distinct_rotations", "multiplicity", "lp_factor", "format_hkls",
    "pattern_from_structure", "pattern_from_cif",
    "pattern_from_structure_pymatgen", "pattern_from_cif_pymatgen",
    "load_structure", "plot_pattern", "plot_annotated", "plot_profile",
    "plot_fit", "plot_fit_bottom",
    "caglioti_fwhm_deg", "lorentz_fwhm_deg", "scherrer_fwhm_deg",
    "strain_fwhm_deg", "tch_fwhm_eta", "pseudo_voigt", "profile_fwhm_eta",
    "simulate_pattern",
    "simulate_pattern_gsas", "sample_sig_gam", "instrument_sig_gam",
    "reflection_sig_gam", "sig_gam_to_fwhm_eta", "pv_area_normalized",
    "polarization", "lp_gsas", "legacy_to_gsas", "W_CONV", "GAM_CONV",
    "fit_pattern", "FitResult", "rwp",
]
