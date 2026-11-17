"""LMFP 金标准：通用引擎必须逐帧复现旧 insitu.py（2026-11-17）。

默认跳过：需要本机 ~/Data/XRD/lmfpdata 的真实帧。取首帧、中间帧、末帧
三帧即可抓住绝大多数回归（完整 81 帧由 `scripts/check_insitu_golden.py`
或手工基准脚本跑）。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

DATA = Path.home() / "Data" / "XRD" / "lmfpdata"
KEYS = ("status", "rwp", "f_rich", "rich_a", "poor_a",
        "rich_c", "poor_c", "f_rich_esd", "wt_rich", "wt_poor")


@unittest.skipUnless(DATA.is_dir(), "没有本机 LMFP 原位帧")
class TestInsituGolden(unittest.TestCase):
    def test_generic_matches_old_engine_on_three_frames(self):
        from xrd_toolkit import paths
        from xrd_toolkit.simulation.insitu import prepare_setup
        from xrd_toolkit.simulation.insitu_generic import LMFPBatch
        from xrd_toolkit.simulation.phase_set import lmfp_phase_set
        mask = paths.default_outputs_dir() / "masks" / "lmfp1_lab6_mask.npy"
        try:
            old = prepare_setup(
                "lmfp1_lab6",
                mask_path=str(mask) if mask.is_file() else None)
        except Exception as err:                    # noqa: BLE001
            self.skipTest(f"准备失败（缺 LaB6/结构？）：{err}")
        new = LMFPBatch(
            [], old.geom, old.lam,
            instrument=dict(getattr(old.instrument, "params", {}) or {}),
            mask=old.mask, phase_set=lmfp_phase_set(),
            first_nfev=120, frame_nfev=120)
        frames = sorted(DATA.glob("*.tif"))
        if len(frames) < 3:
            self.skipTest("帧不足 3 张")
        picks = [frames[0], frames[len(frames) // 2], frames[-1]]
        for path in picks:
            a = old.fit_frame(path)
            b = new.fit_frame(path)
            for key in KEYS:
                if key == "status":
                    self.assertEqual(a[key], b[key], f"{path.name} status")
                    continue
                va, vb = float(a[key]), float(b[key])
                if va != va and vb != vb:
                    continue
                self.assertAlmostEqual(
                    va, vb, places=8,
                    msg=f"{path.name} {key}: old={va} new={vb}")


if __name__ == "__main__":
    unittest.main()
