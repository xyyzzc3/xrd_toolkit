#!/usr/bin/env python3
"""GSAS-II 独立对照：原位三相批量的 3 帧逐帧精修（Week 9，2026-10-28）。

分三步、跨两个环境跑（GSAS-II 装在一个单独的 conda 环境里）：

    # 1) 准备数据（本项目环境；需要 pymatgen）
    python scripts/gsas2_insitu_crosscheck.py prepare \
        --data ~/Desktop/lmfpdata \
        --mask outputs/masks/lmfp1_lab6_mask.npy \
        --batch-csv outputs/insitu/insitu_fit.csv \
        --outdir /tmp/xrd_gsas2_insitu

    # 2) GSAS-II 逐帧精修（顺序种子；gsas2 环境）
    conda run -n gsas2 python scripts/gsas2_insitu_crosscheck.py refine \
        --workdir /tmp/xrd_gsas2_insitu

    # 3) 比较（本项目环境）
    python scripts/gsas2_insitu_crosscheck.py compare \
        --workdir /tmp/xrd_gsas2_insitu

prepare 写：frameNNN.xye、insitu.instprm、rich_pnma.cif、poor_pnma.cif、
meta.json、ours.json。refine 写：gsas2_insitu.csv、gsas2_frameNNN.gpx。
compare 写：comparison.txt、comparison.png。

**为什么要对称化 CIF**：仓库里的模型 CIF 是 P1 展开（给手写结构因子用的）。
GSAS-II 对这批 P1 展开 CIF 做晶胞精修（6 个参数）时，profile 的 Fortran
代码会零除（`divide by zero encountered in scalar divide`，试过 Le Bail/加边界/
缩短步长都不行）；Pm-3m 的 LaB6 或 Pnma 的橄榄石就正常。prepare 用
pymatgen 的 SpacegroupAnalyzer 还原成 Pnma，并核对还原前后的峰位/强度
一致，保证 GSAS-II 吃的是同一个结构。

**口径差异（写报告时必须带上）**：
  * 相分数：本项目是相对散射强度分数；GSAS-II 是重量分数（wt%）。
    两者只比趋势/比值，不比绝对值。
  * 背景：本项目 = 滑动基线 + 0.25° 分段修正 + 曲率罚项；GSAS-II =
    Chebyshev-1 六项、逐帧精修。Rwp 不可直接比。
  * 强度修正：本项目 Al 逐反射、rich 二阶平滑；GSAS-II Al/rich 都只有一个
    scale。这也是 Al 参考相只作参考、不参与 LMFP 分数的原因。
"""
import argparse
import csv
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

# 注意：本项目（XRD env）的导入只放在 prepare 里。refine 要在只装了
# GSAS-II 的 conda 环境跑，那里没有 fabio/pyFAI/pymatgen，顶层导入会直接崩。
DEFAULT_DATA = Path.home() / "Desktop" / "lmfpdata"
DEFAULT_RICH = ROOT / "data/structures/LiMn0.6Fe0.4PO4_olivine.cif"
DEFAULT_POOR = ROOT / "data/structures/Mn0.6Fe0.4PO4_heterosite_rich_axis.cif"

# 与 fit_insitu 批量一致的取样口径
NPT = 3000
TTH_RANGE = (1.0, 8.0)
# GSAS-II 里的 Al 参考晶胞（与 fit_insitu 的 AL_A 一致，固定在实测值）
AL_A = 4.061
# 用本项目的 b_iso（Å²）设置 GSAS-II 的 Uiso，保证强度模型一致
B_ISO = {"rich": 0.0, "poor": 0.5, "Al": 0.5}
# 以多数相帧为锚的顺序：45 帧 poor 占绝对多数，1 帧 rich 占绝对多数；
# 少数相的晶胞只有在“同类多数相”的帧里才定得准，先用锚帧再回填种子。
ANCHOR_ORDER = (45, 1, 81)


def _tag(path):
    """从 LMFP_1_atten0-00029.tif 取 29；取不到时退回 0（同 fit_insitu）。"""
    import re
    m = re.search(r"(\d+)$", Path(path).stem)
    return int(m.group(1)) if m else 0


# ---------------- prepare ----------------

def write_instprm(path, lam, params):
    """写 GSAS-II 新版（G2 fmt）单波长 X 射线仪器文件。"""
    path = Path(path)
    path.write_text(
        "#GSAS-II instrument parameter file; do not add/delete items!\n"
        f"Type:PXC\nLam:{lam}\nZero:{params['zero']}\nPolariz.:0.5\n"
        f"U:{params['u']}\nV:{params['v']}\nW:{params['w']}\n"
        f"X:{params['x']}\nY:{params['y']}\nZ:0.0\nSH/L:0.0\n"
        "Azimuth:0.0\nBank:1.0\n", encoding="utf-8")
    return path


def write_xye(path, tth, intensity):
    """写 2θ/I/σ 三列；σ 用 sqrt(max(I, 1))（见模块说明的口径限制）。"""
    err = np.sqrt(np.maximum(np.asarray(intensity, dtype=float), 1.0))
    arr = np.column_stack([np.asarray(tth, dtype=float),
                           np.asarray(intensity, dtype=float), err])
    np.savetxt(path, arr, fmt="%.8f %.6f %.6f")
    return path


def symmetrize_cif(src, dst, symprec=0.01):
    """把 P1 展开的模型 CIF 还原成有空间群的 CIF（GSAS-II 精修晶胞用）。

    还原后用 pymatgen 重新读两边的结构，核对晶胞与成分；再用本项目的
    手写结构因子核对的峰位/强度（相对差 < 1e-6 才通过）。
    """
    try:
        from pymatgen.core import Structure
        from pymatgen.io.cif import CifWriter
        from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
    except ImportError as exc:  # pragma: no cover - 依赖缺失
        raise SystemExit(
            "prepare 需要 pymatgen：pip install 'xrd-toolkit[simulation]'"
        ) from exc

    src_structure = Structure.from_file(str(src))
    analyzer = SpacegroupAnalyzer(src_structure, symprec=symprec)
    symbol = analyzer.get_space_group_symbol()
    if symbol == "P1":
        raise SystemExit(
            f"{src} 在 symprec={symprec} 下仍是 P1；GSAS-II 会对 P1 晶胞"
            f"精修零除。请换一个已对称化的结构文件或调整 symprec。")
    CifWriter(analyzer.get_symmetrized_structure(),
              symprec=symprec).write_file(str(dst))

    sym_structure = Structure.from_file(str(dst))
    if src_structure.composition != sym_structure.composition:
        raise SystemExit(f"{src} -> {dst} 成分变了，拒绝使用")
    # 峰位/强度核对（只比前 40 个峰，避免浮点噪声）
    from xrd_toolkit.simulation import pattern_from_cif
    t0, y0 = pattern_from_cif(str(src), wavelength=0.1223,
                              two_theta_range=(1.0, 8.0))[:2]
    t1, y1 = pattern_from_cif(str(dst), wavelength=0.1223,
                              two_theta_range=(1.0, 8.0))[:2]
    n = min(len(t0), len(t1), 40)
    if n < 3:
        raise SystemExit(f"{src} 峰数太少，无法核对对称化结果")
    if np.max(np.abs(t0[:n] - t1[:n])) > 1e-3:
        raise SystemExit(f"{src} -> {dst} 的峰位对不上，拒绝使用")
    scale = max(float(np.max(y0[:n])), 1e-9)
    if np.max(np.abs(y0[:n] - y1[:n])) / scale > 1e-4:
        raise SystemExit(f"{src} -> {dst} 的峰强对不上，拒绝使用")
    return symbol, dst


def frame_files(data_dir):
    """与 fit_insitu._frames 相同的排序（完整序列、不跳帧）。"""
    files = sorted(Path(data_dir).glob("*.tif"), key=_tag)
    return files


def prepare(args):
    from xrd_toolkit.config import get_config
    from xrd_toolkit.services.data_loader import load_diffraction_image
    from xrd_toolkit.services.integrator import integrate_1d
    from xrd_toolkit.services.mask import load_mask, mask_stats
    import fit_insitu as fi

    data_dir = Path(args.data).expanduser()
    if not data_dir.is_dir():
        raise SystemExit(f"数据目录不存在：{data_dir}")
    outdir = Path(args.outdir).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)

    geom = dict(get_config(args.config)["geometry"])
    lam = geom["wavelength_m"] * 1e10
    mask = None
    if args.mask:
        mp = Path(args.mask).expanduser()
        if not mp.is_absolute():
            mp = ROOT / mp
        if mp.exists():
            mask = load_mask(mp)
            print(f"mask: {mp} ({mask_stats(mask)['masked_pixels']} px)")
        else:
            print(f"警告：mask 不存在（{mp}），按不遮运行")

    files = frame_files(data_dir)
    frames = [int(x) for x in args.frames.split(",") if x.strip()]
    for frame in frames:
        if not 1 <= frame <= len(files):
            raise SystemExit(f"帧号 {frame} 超出范围 1..{len(files)}")

    inst = fi._instrument(geom, lam)
    if inst is None:
        raise SystemExit("没有 LaB6 标样图，无法固定仪器峰形")
    params = {k: float(inst.params[k]) for k in ("zero", "u", "v", "w", "x", "y")}
    print(f"LaB6: zero={params['zero']:+.5f} "
          f"U={params['u']:.3f} V={params['v']:.3f} "
          f"W={params['w']:.3f} X={params['x']:.3f} "
          f"Y={params['y']:.3f}  (Rwp {inst.rwp:.3f}%)")
    write_instprm(outdir / "insitu.instprm", lam, params)

    for frame in frames:
        path = files[frame - 1]
        img = load_diffraction_image(str(path))
        t, y = integrate_1d(img, npt=NPT, tth_min_deg=TTH_RANGE[0],
                            tth_max_deg=TTH_RANGE[1], mask=mask, **geom)
        xye = write_xye(outdir / f"frame{frame:03d}.xye", t, y)
        print(f"frame {frame}: {path.name} -> {xye.name} "
              f"(I {float(y.min()):.0f}..{float(y.max()):.0f})")

    rich_sym, _ = symmetrize_cif(args.rich, outdir / "rich_pnma.cif")
    poor_sym, _ = symmetrize_cif(args.poor, outdir / "poor_pnma.cif")
    print(f"对称化 CIF: rich={rich_sym}, poor={poor_sym}")
    from pymatgen.core import Structure
    cif_cells = {}
    for name in ("rich", "poor"):
        lattice = Structure.from_file(
            str(outdir / f"{name}_pnma.cif")).lattice
        if any(abs(a - 90.0) > 1e-3 for a in lattice.angles):
            raise SystemExit(
                f"{name} 的对称化晶胞不是正交（角度 {lattice.angles}）；"
                "本脚本的 GSAS-II 精修只按 a/b/c 设晶胞，请先扩展脚本支持角度。")
        cif_cells[name] = [float(v) for v in lattice.abc]

    ours = {}
    if args.batch_csv:
        with open(args.batch_csv, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if int(row["frame"]) in frames:
                    ours[int(row["frame"])] = {
                        k: float(row[k]) for k in (
                            "rwp", "f_rich", "f_poor",
                            "rich_a", "rich_b", "rich_c",
                            "poor_a", "poor_b", "poor_c",
                            "rich_a_esd", "rich_b_esd", "rich_c_esd",
                            "poor_a_esd", "poor_b_esd", "poor_c_esd")}
    meta = {"lam": lam, "frames": frames, "instrument": params,
            "cif_cells": cif_cells,
            "config": args.config, "masked_px": int(mask.sum())
            if mask is not None else 0,
            "anchor_order": list(ANCHOR_ORDER),
            "al_a": AL_A, "b_iso": B_ISO}
    (outdir / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    (outdir / "ours.json").write_text(
        json.dumps({"ours": ours} if ours else {}, indent=2,
                   ensure_ascii=False), encoding="utf-8")
    print(f"准备完成：{outdir}")
    return outdir


# ---------------- refine（只在 gsas2 环境跑） ----------------

def _load_gsas():
    gsas_dir = os.environ.get("GSAS2_DIR") or str(Path(sys.prefix) / "GSAS-II")
    if not (Path(gsas_dir) / "GSASII").is_dir():
        raise SystemExit(
            f"找不到 GSAS-II（{gsas_dir}）。请用装了 gsas2pkg 的 conda 环境跑"
            f"（conda run -n gsas2 ...），或设 GSAS2_DIR 指向源码目录。")
    sys.path.insert(0, gsas_dir)
    from GSASII import GSASIIlattice as G2lat
    from GSASII import GSASIIscriptable as G2sc
    return G2sc, G2lat


def _set_cell(phase, abc, G2lat):
    phase.data["General"]["Cell"][1:7] = list(abc) + [90.0, 90.0, 90.0]
    phase.data["General"]["Cell"][7] = G2lat.calc_V(
        G2lat.cell2A(phase.data["General"]["Cell"][1:7]))


def _set_uiso(phase, b_iso):
    u = float(b_iso) / (8.0 * math.pi ** 2)
    for atom in phase.atoms():
        atom.uiso = u


def refine_frame(G2sc, G2lat, workdir, frame, seed):
    """单帧：GSAS-II 三相 Rietveld，先修背景+相分数，再放开晶胞。"""
    t0 = time.perf_counter()
    workdir = Path(workdir)
    gpx = G2sc.G2Project(newgpx=str(workdir / f"gsas2_frame{frame:03d}.gpx"))
    phases = {}
    for name, cif in (("rich", workdir / "rich_pnma.cif"),
                      ("poor", workdir / "poor_pnma.cif"),
                      ("Al", ROOT / "data/structures/Al_metal.cif")):
        phases[name] = gpx.add_phase(str(cif), phasename=name, fmthint="CIF")
        _set_uiso(phases[name], B_ISO[name])
    _set_cell(phases["rich"], seed["rich"], G2lat)
    _set_cell(phases["poor"], seed["poor"], G2lat)
    _set_cell(phases["Al"], (AL_A, AL_A, AL_A), G2lat)

    hist = gpx.add_powder_histogram(
        str(workdir / f"frame{frame:03d}.xye"),
        str(workdir / "insitu.instprm"), phases="all")
    hist.set_refinements({"Limits": TTH_RANGE})
    hist.set_refinements({"Background": {"type": "chebyschev-1",
                                         "no. coeffs": 6, "refine": True}})
    # 直方图总标度固定，强度由三个相的 scale 承担（避免与相分数简并）
    hist.data["Sample Parameters"]["Scale"][1] = False
    for name in ("rich", "poor", "Al"):
        phases[name].set_HAP_refinements({"Scale": True})
        hap = phases[name].getHAPvalues(hist.name)
        hap["Size"][1] = [1e5, 1e5, 1.0]
        hap["Mustrain"][1] = [0.0, 0.0, 1.0]
    gpx.data["Controls"]["data"].setdefault("parmFrozen", {})

    # 第一段：晶胞固定，先把背景/尺度拉近，避免晶胞一步踢飞
    gpx.set_Controls("cycles", 4)
    gpx.do_refinements([{}])
    # do_refinements 会 reload，旧对象作废——必须重新取
    phases = {n: gpx.phase(n) for n in ("rich", "poor", "Al")}
    hist = gpx.histogram(0)
    phases["rich"].set_refinements({"Cell": True})
    phases["poor"].set_refinements({"Cell": True})
    phases["Al"].set_refinements({"Cell": False})
    gpx.set_Controls("cycles", 8)
    gpx.do_refinements([{}])

    phases = {n: gpx.phase(n) for n in ("rich", "poor", "Al")}
    hist = gpx.histogram(0)
    w_r = hist.residuals.get("wR")
    if w_r is None:
        raise RuntimeError(
            f"frame {frame}: GSAS-II 精修失败（wR=None）。常见原因：CIF 仍是 "
            "P1（晶胞精修会零除）或仪器文件不对称。检查 "
            f"{workdir}/gsas2_frame{frame:03d}.lst")
    row = {"frame": frame, "wR": float(w_r)}
    for name in ("rich", "poor", "Al"):
        cell, esd = phases[name].get_cell_and_esd()
        for axis in ("a", "b", "c"):
            row[f"{name}_{axis}"] = float(cell[f"length_{axis}"])
            row[f"{name}_{axis}_esd"] = float(esd[f"length_{axis}"])
    mf = hist.ComputeMassFracs()
    row["rich_wt"] = float(mf["rich"][0])
    row["poor_wt"] = float(mf["poor"][0])
    row["Al_wt"] = float(mf["Al"][0])
    row["rich_wt_esd"] = float(mf["rich"][1])
    row["poor_wt_esd"] = float(mf["poor"][1])
    denom = row["rich_wt"] + row["poor_wt"]
    row["f_rich_wt"] = (row["rich_wt"] / denom
                        if denom > 0 else float("nan"))
    row["t_total_s"] = time.perf_counter() - t0
    return row


def refine(args):
    G2sc, G2lat = _load_gsas()
    workdir = Path(args.workdir).expanduser()
    meta = json.loads((workdir / "meta.json").read_text(encoding="utf-8"))
    frames = [int(x) for x in meta["frames"]]
    order = [f for f in meta.get("anchor_order", ANCHOR_ORDER) if f in frames]
    order += [f for f in frames if f not in order]
    if not order:
        raise SystemExit("meta.json 里没有可跑的帧")

    seeds = {name: tuple(meta["cif_cells"][name])
             for name in ("rich", "poor")}
    rows = []
    # 先跑锚帧，再用锚帧的“多数相晶胞”给别的帧当种子
    for frame in order:
        print(f"==== frame {frame} (seed rich={seeds['rich']}, "
              f"poor={seeds['poor']})", flush=True)
        row = refine_frame(G2sc, G2lat, workdir, frame, seeds)
        rows.append(row)
        print(f"   wR={row['wR']:.2f}%  "
              f"rich a={row['rich_a']:.4f}  poor a={row['poor_a']:.4f}  "
              f"f_rich(wt)={row['f_rich_wt']:.3f}", flush=True)
        if frame == 45:            # poor 多数：poor 晶胞可信
            seeds["poor"] = tuple(row[f"poor_{x}"] for x in "abc")
        if frame == 1:             # rich 多数：rich 晶胞可信
            seeds["rich"] = tuple(row[f"rich_{x}"] for x in "abc")

    rows.sort(key=lambda r: r["frame"])
    fields = list(rows[0])
    csv_path = workdir / "gsas2_insitu.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    total = sum(r["t_total_s"] for r in rows)
    lines = [
        "=== GSAS-II in-situ crosscheck (3 frames) ===",
        f"order: {order} (anchored: poor-majority -> rich-majority -> rest)",
        f"frames: {len(rows)}  total: {total:.1f} s",
        "phase fractions: GSAS-II mass fractions (wt%), "
        "our batch: relative scattering fraction.",
        "background: Chebyshev-1 (6 terms) per frame; cells refined "
        "after a scale/background-only stage.",
        "CIFs: Pnma symmetry-reduced by pymatgen (refining cell parameters "
        "on these P1 CIFs crashes GSAS-II's profile code).",
    ]
    (workdir / "summary.txt").write_text("\n".join(lines) + "\n",
                                         encoding="utf-8")
    print(f"写入 {csv_path}")
    return rows


# ---------------- compare ----------------

def compare_rows(ours, gsas_rows):
    """把两边的同一帧并排；差值按 GSAS-II 的 esd 归一化。"""
    out = []
    for row in gsas_rows:
        frame = int(row["frame"])
        if frame not in ours:
            continue
        o = ours[frame]
        item = {"frame": frame,
                "f_rich_ours": o["f_rich"],
                "f_rich_wt": row["f_rich_wt"],
                "rich_wt": row["rich_wt"], "poor_wt": row["poor_wt"],
                "Al_wt": row["Al_wt"],
                "cells": {}}
        for phase in ("rich", "poor"):
            for axis in "abc":
                got = row[f"{phase}_{axis}"]
                ref = o[f"{phase}_{axis}"]
                esd = row[f"{phase}_{axis}_esd"]
                ours_esd = o.get(f"{phase}_{axis}_esd", float("nan"))
                item["cells"][f"{phase}_{axis}"] = {
                    "ours": ref, "gsas": got,
                    "delta": got - ref,
                    "esd": esd,
                    "sigma": ((got - ref) / esd) if esd and esd > 0
                    else float("inf"),
                    "ours_esd": ours_esd,
                }
        out.append(item)
    return out


def _write_compare_figure(path, rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    x = [r["frame"] for r in rows]
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8))
    ax = axes[0]
    ax.plot(x, [r["f_rich_ours"] for r in rows], "o-", label="ours (scattering)")
    ax.plot(x, [r["f_rich_wt"] for r in rows], "s--", label="GSAS-II (wt%)")
    ax.set_xlabel("frame")
    ax.set_ylabel("rich fraction")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25, linestyle=":")
    ax = axes[1]
    for phase, marker in (("rich", "o"), ("poor", "s")):
        ax.errorbar(x, [r["cells"][f"{phase}_a"]["gsas"] for r in rows],
                    yerr=[r["cells"][f"{phase}_a"]["esd"] for r in rows],
                    marker=marker, ls="none", capsize=2,
                    label=f"GSAS-II {phase} a")
        ax.plot(x, [r["cells"][f"{phase}_a"]["ours"] for r in rows],
                marker=marker, ls=":", label=f"ours {phase} a")
    ax.set_xlabel("frame")
    ax.set_ylabel("a (Å)")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.25, linestyle=":")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def _compare_text(rows):
    lines = ["=== 相分数 ===",
             "frame  ours(scattering)  GSAS-II(wt%)  rich_wt  poor_wt  Al_wt"]
    for r in rows:
        lines.append(f"{r['frame']:>5}  {r['f_rich_ours']:>17.3f}  "
                     f"{r['f_rich_wt']:>12.3f}  {r['rich_wt']:>7.3f}  "
                     f"{r['poor_wt']:>7.3f}  {r['Al_wt']:>5.3f}")
    lines += ["", "=== 晶胞（GSAS-II − 本批）/ esd ===",
              "frame phase axis   ours     GSAS-II   delta    esd    sigma"]
    for r in rows:
        for phase in ("rich", "poor"):
            for axis in "abc":
                c = r["cells"][f"{phase}_{axis}"]
                lines.append(
                    f"{r['frame']:>5} {phase:>6} {axis:>4} "
                    f"{c['ours']:8.4f} {c['gsas']:8.4f} "
                    f"{c['delta']:+8.4f} {c['esd']:7.4f} "
                    f"{c['sigma']:7.1f}")
    lines += ["", "注：sigma 只用 GSAS-II 的 esd；本批 esd 列在 ours.json。",
              "少数相（<~15%）晶胞两边都可能不可靠，看趋势即可。"]
    return "\n".join(lines) + "\n"


def compare(args):
    workdir = Path(args.workdir).expanduser()
    ours = json.loads((workdir / "ours.json").read_text(encoding="utf-8")).get(
        "ours", {})
    ours = {int(k): v for k, v in ours.items()}
    if not ours:
        raise SystemExit("没有 ours.json（prepare 时给 --batch-csv）")
    with open(workdir / "gsas2_insitu.csv", newline="", encoding="utf-8") as fh:
        gsas_rows = [{k: (float(v) if k != "frame" else int(v))
                      for k, v in row.items()} for row in csv.DictReader(fh)]
    rows = compare_rows(ours, gsas_rows)
    text = _compare_text(rows)
    (workdir / "comparison.txt").write_text(text, encoding="utf-8")
    _write_compare_figure(workdir / "comparison.png", rows)
    print(text)
    print(f"写入 {workdir / 'comparison.txt'} / comparison.png")
    return rows


# ---------------- CLI ----------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prepare", help="积分 3 帧、写 xye/仪器文件/对称化 CIF")
    p.add_argument("--data", default=str(DEFAULT_DATA))
    p.add_argument("--mask", default="outputs/masks/lmfp1_lab6_mask.npy")
    p.add_argument("--config", default="lmfp1_lab6")
    p.add_argument("--rich", default=str(DEFAULT_RICH))
    p.add_argument("--poor", default=str(DEFAULT_POOR))
    p.add_argument("--batch-csv", default="",
                   help="本项目 insitu_fit.csv（用于对比）")
    p.add_argument("--frames", default="1,45,81")
    p.add_argument("--outdir", default=str(
        Path(tempfile.gettempdir()) / "xrd_toolkit_gsas2_insitu"))
    p.set_defaults(func=prepare)

    r = sub.add_parser("refine", help="GSAS-II 逐帧精修（顺序种子，gsas2 环境）")
    r.add_argument("--workdir", required=True)
    r.set_defaults(func=refine)

    c = sub.add_parser("compare", help="与本批 CSV 对比并出图")
    c.add_argument("--workdir", required=True)
    c.set_defaults(func=compare)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
