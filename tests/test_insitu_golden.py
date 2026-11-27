"""LMFP 金标准：通用引擎必须逐帧复现旧 insitu.py（2026-11-17）。

默认跳过：需要本机 ~/Data/XRD/lmfpdata 的真实帧。首/中/末三帧抓常规
回归；另按顺序跑 27–29 帧，专门覆盖 2026-11-24 的 Rwp 守卫（帧 28
需要前一帧的种子才会触发第二起点）。完整 81 帧由
`scripts/check_insitu_reference.py` 做发布门禁。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

DATA = Path.home() / "Data" / "XRD" / "lmfpdata"
NUMERIC_KEYS = ("rwp", "f_rich", "rich_a", "poor_a", "rich_c", "poor_c",
                "f_rich_esd", "wt_rich", "wt_poor",
                "rwp_seed", "rwp_cif", "f_rich_seed", "f_rich_cif")
FLAG_KEYS = ("dual_start", "success_seed", "success_cif")


@unittest.skipUnless(DATA.is_dir(), "没有本机 LMFP 原位帧")
class TestInsituGolden(unittest.TestCase):
    def _batches(self):
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
        return old, new

    def _assert_same(self, a, b, name):
        for key in ("status", "start_used"):
            self.assertEqual(str(a.get(key, "")), str(b.get(key, "")),
                             f"{name} {key}: old={a.get(key)} "
                             f"new={b.get(key)}")
        for key in NUMERIC_KEYS:
            va, vb = float(a[key]), float(b[key])
            if va != va and vb != vb:
                continue
            self.assertAlmostEqual(
                va, vb, places=8,
                msg=f"{name} {key}: old={va} new={vb}")
        for key in FLAG_KEYS:
            self.assertEqual(int(a.get(key, -1)), int(b.get(key, -1)),
                             f"{name} {key}")

    def test_generic_matches_old_engine_on_three_frames(self):
        old, new = self._batches()
        frames = sorted(DATA.glob("*.tif"))
        if len(frames) < 3:
            self.skipTest("帧不足 3 张")
        picks = [frames[0], frames[len(frames) // 2], frames[-1]]
        for path in picks:
            self._assert_same(old.fit_frame(path), new.fit_frame(path),
                              path.name)

    def test_generic_matches_old_engine_across_guard_frames(self):
        """顺序跑 27–29 帧：帧 28 带非 CIF 种子，守卫必须两边一致。"""
        old, new = self._batches()
        frames = sorted(DATA.glob("*.tif"))
        if len(frames) < 30:
            self.skipTest("帧不足 30 张")
        for path in frames[26:30]:
            self._assert_same(old.fit_frame(path), new.fit_frame(path),
                              path.name)


if __name__ == "__main__":
    unittest.main()
