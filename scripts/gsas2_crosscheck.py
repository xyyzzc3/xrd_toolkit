#!/usr/bin/env python3
"""GSAS-II 模拟（交叉验证用）：**必须用装了 GSAS-II 的 conda 环境跑**。

用法（一次性建环境）：
    conda create -y -n gsas2 -c briantoby -c conda-forge gsas2pkg python=3.13
用法（跑一次模拟，把曲线存成 npz）：
    conda run -n gsas2 python scripts/gsas2_crosscheck.py \
        --cif data/structures/LaB6_1000055.cif --out /tmp/gsas2_sim.npz \
        --w 18.033 --shl 0.0          # W=18.033 centideg² ↔ 0.1° FWHM

参数与 `xrd_toolkit.simulation.gsas_profile.simulate_pattern_gsas`
**一一对应**（同名同单位）：

    零点 Zero(°) / 波长 Lam(Å) / U,V,W(centideg²) / X,Y,Z(centideg)
    Size(µm) / Mustrain(4ε·10⁶) / Polariz. / SH/L / 2θ起止步长

GSAS-II 的位置从**当前环境**推出来（sys.prefix/GSAS-II，gsas2pkg 装法固定；
也可用环境变量 GSAS2_DIR 覆盖）——不写死绝对路径，换机器照跑。
输出：npz 含 x（2θ 网格）与 ycalc（计算谱、无噪声）。
"""
import argparse
import os
import sys
from pathlib import Path

import numpy as np

GSAS2_DIR = os.environ.get("GSAS2_DIR") or str(Path(sys.prefix) / "GSAS-II")
if not (Path(GSAS2_DIR) / "GSASII").is_dir():
    sys.exit(f"找不到 GSAS-II（{GSAS2_DIR}）——请先用装了 gsas2pkg 的环境跑，"
             f"或设 GSAS2_DIR 指向 GSAS-II 源码目录")
sys.path.insert(0, GSAS2_DIR)

from GSASII import GSASIIscriptable as G2sc  # noqa: E402


def write_instprm(path, *, lam, zero, u, v, w, x, y, z=0.0, shl,
                  polariz=0.5):
    """写一份新版（G2 fmt）单波长 X 射线仪器文件。"""
    Path(path).write_text(
        "#GSAS-II instrument parameter file; do not add/delete items!\n"
        f"Type:PXC\nLam:{lam}\nZero:{zero}\nPolariz.:{polariz}\n"
        f"U:{u}\nV:{v}\nW:{w}\nX:{x}\nY:{y}\nZ:{z}\nSH/L:{shl}\n"
        f"Azimuth:0.0\nBank:1.0\n", encoding="utf-8")


def simulate(cif, out, *, lam=1.5406, zero=0.0, u=0.0, v=0.0, w=0.01,
             x=0.0, y=0.0, z=0.0, shl=0.0, polariz=0.5,
             size=1e5, mustrain=0.0, tmin=10.0, tmax=80.0, step=0.02):
    """跑一次 GSAS-II 模拟，返回 (2θ 数组, ycalc 数组) 并存 npz。"""
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="gsas2_"))
    prm = tmp / "inst.instprm"
    write_instprm(prm, lam=lam, zero=zero, u=u, v=v, w=w, x=x, y=y,
                  z=z, shl=shl, polariz=polariz)

    gpx = G2sc.G2Project(newgpx=str(tmp / "sim.gpx"))
    phase = gpx.add_phase(str(cif), phasename=Path(cif).stem, fmthint="CIF")
    hist = gpx.add_simulated_powder_histogram(
        "sim", str(prm), tmin, tmax, Tstep=step, phases=[phase])
    # 微结构中性化（2026-10-17 发现；2026-10-22 修）：GSAS-II 新物相
    # **自带** Size=1 µm + Mustrain=1000，不设掉会在"纯仪器"模拟里混进
    # 假展宽。注意 Size/Mustrain 的列表是 [i, a, **mx**]，第三位是
    # LG 混合系数：以前误写成 [size,size,size]，把 mx 设成了尺寸，
    # 反而让"中性化"多出 ~6% 的假展宽（W=18.033 时 0.100°→0.106°）。
    hap = phase.getHAPvalues(hist.name)
    hap["Size"][1] = [float(size), float(size), 1.0]
    hap["Mustrain"][1] = [float(mustrain), float(mustrain), 1.0]
    # 0 轮精修 = 只做一遍计算把 ycalc 填出来（建 hist 时还不算）
    gpx.do_refinements([{}])
    tth = np.asarray(hist.getdata("x"), dtype=float)
    ycalc = np.asarray(hist.getdata("ycalc"), dtype=float)
    np.savez(out, x=tth, ycalc=ycalc, lam=lam, zero=zero, u=u, v=v, w=w,
             x_inst=x, y_inst=y, z=z, shl=shl, polariz=polariz,
             size=size, mustrain=mustrain)
    return tth, ycalc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cif", required=True)
    ap.add_argument("--out", required=True, help="npz 输出路径")
    ap.add_argument("--lam", type=float, default=1.5406)
    ap.add_argument("--zero", type=float, default=0.0)
    ap.add_argument("--u", type=float, default=0.0)
    ap.add_argument("--v", type=float, default=0.0)
    ap.add_argument("--w", type=float, default=0.01)
    ap.add_argument("--x", type=float, default=0.0)
    ap.add_argument("--y", type=float, default=0.0)
    ap.add_argument("--z", type=float, default=0.0)
    ap.add_argument("--shl", type=float, default=0.0)
    ap.add_argument("--polariz", type=float, default=0.5)
    ap.add_argument("--size", type=float, default=1e5,
                    help="晶粒尺寸 µm（默认 1e5 = 无限大，中性化）")
    ap.add_argument("--mustrain", type=float, default=0.0,
                    help="GSAS Mustrain（4ε·10⁶；默认 0）")
    ap.add_argument("--tmin", type=float, default=10.0)
    ap.add_argument("--tmax", type=float, default=80.0)
    ap.add_argument("--step", type=float, default=0.02)
    a = ap.parse_args()
    tth, yc = simulate(a.cif, a.out, lam=a.lam, zero=a.zero, u=a.u, v=a.v,
                       w=a.w, x=a.x, y=a.y, z=a.z, shl=a.shl,
                       polariz=a.polariz, size=a.size, mustrain=a.mustrain,
                       tmin=a.tmin, tmax=a.tmax, step=a.step)
    print(f"GSAS-II: {len(tth)} 点，{tth.min():.2f}–{tth.max():.2f}°，"
          f"ycalc 峰顶 {yc.max():.1f} → {a.out}")


if __name__ == "__main__":
    main()
