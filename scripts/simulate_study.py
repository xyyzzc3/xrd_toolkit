#!/usr/bin/env python3
"""XRD 模拟研究脚本（RY04 第七周起，2026-10-17）。

按任务逐步出图 + 打印数字，一张图对应任务清单里的一项产出：

    basic          任务一：基础模拟谱（理想晶体 + 常数展宽，带 hkl 标注）
    instrument     任务二：逐仪器参数（波长 / 零点 / 展宽 / 范围步长）   [待加]
    microstructure 任务三：逐样品参数（尺寸 / 应变 / B 因子 / 取向）    [待加]
    compare        任务四：模拟 vs 实验（lab6 实测一维谱）              [待加]

用法：
    python scripts/simulate_study.py basic
    python scripts/simulate_study.py basic --cif data/structures/Si_9008565.cif
    python scripts/simulate_study.py basic --outdir /tmp/out

主 CIF 默认 LaB₆（与第三周的实验数据同材料，任务四要对得上）。
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xrd_toolkit.simulation import (  # noqa: E402
    load_structure, pattern_from_cif, plot_profile, simulate_pattern)

DEFAULT_CIF = ROOT / "data" / "structures" / "LaB6_1000055.cif"


def _material_name(cif: Path) -> str:
    """从文件名取材料短名（LaB6_1000055.cif → LaB6）。"""
    return cif.stem.split("_")[0]


def study_basic(cif: Path, outdir: Path) -> None:
    """任务一：基础模拟谱。理想晶体 + 常数展宽（0.1° Gaussian），带 hkl 标注。"""
    material = _material_name(cif)
    structure = load_structure(str(cif))
    tth, curve, peaks, is_hex = simulate_pattern(
        structure, two_theta_range=(10.0, 80.0), step_deg=0.02)
    sticks_tt, sticks_ii, _, _ = pattern_from_cif(
        str(cif), two_theta_range=(10.0, 80.0))
    out = outdir / f"sim_xrd_basic_{material}.png"
    strongest = max(peaks, key=lambda p: p[1])
    plot_profile(
        tth, curve, peaks, is_hex,
        f"{material} (from CIF) — basic simulated pattern\n"
        f"Cu Kα 1.5406 Å · ideal crystal · 0.1° Gaussian broadening",
        str(out), sticks=(sticks_tt, sticks_ii),
        zoom=(strongest[0], 0.9))          # 放大最强峰：0.1° 的峰形看得见
    print(f"{material}：10°–80° 内 {len(peaks)} 个反射（≥1% 最强峰），"
          f"最强峰在 {strongest[0]:.2f}°")
    print(f"  → {out}")


STUDIES = {
    "basic": study_basic,
    # "instrument": study_instrument,        # 任务二
    # "microstructure": study_microstructure,  # 任务三
    # "compare": study_compare,              # 任务四
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("study", choices=sorted(STUDIES), help="要跑的课题")
    ap.add_argument("--cif", default=str(DEFAULT_CIF),
                    help=f"输入 CIF（默认 {DEFAULT_CIF.name}）")
    ap.add_argument("--outdir", default=str(ROOT / "showcase" / "simulation"),
                    help="输出目录（默认 showcase/simulation）")
    args = ap.parse_args()
    cif = Path(args.cif)
    if not cif.exists():
        ap.error(f"CIF 不存在：{cif}")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    STUDIES[args.study](cif, outdir)


if __name__ == "__main__":
    main()
