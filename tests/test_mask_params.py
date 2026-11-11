"""掩膜检测参数的按几何记忆（2026-11-11）：存取、夹紧、旧格式兼容。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xrd_toolkit.services import mask_params  # noqa: E402


class TestMaskParams(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self._old = mask_params.STORE
        mask_params.STORE = Path(self._td.name) / "mask_params.json"

    def tearDown(self):
        mask_params.STORE = self._old
        self._td.cleanup()

    def test_defaults_when_missing(self):
        params = mask_params.load("lmfp1_lab6")
        self.assertEqual(params, mask_params.DEFAULTS)

    def test_roundtrip_per_geometry(self):
        mask_params.save("lmfp1_lab6", {"ratio": 12.0, "grow": 5})
        mask_params.save("other", {"ratio": 4.0})
        self.assertEqual(mask_params.load("lmfp1_lab6")["ratio"], 12.0)
        self.assertEqual(mask_params.load("lmfp1_lab6")["grow"], 5)
        self.assertEqual(mask_params.load("other")["ratio"], 4.0)
        raw = json.loads(mask_params.STORE.read_text(encoding="utf-8"))
        self.assertEqual(raw["version"], 1)
        self.assertIn("lmfp1_lab6", raw["configs"])

    def test_values_are_clamped(self):
        mask_params.save("x", {"window": 4, "ratio": 999, "max_area": 1,
                               "min_peak": -5, "grow": 99,
                               "n_sigma": 0})
        params = mask_params.load("x")
        self.assertEqual(params["window"], 5)     # 夹紧且修正为奇数
        self.assertEqual(params["ratio"], 50.0)
        self.assertEqual(params["max_area"], 10)
        self.assertEqual(params["min_peak"], 0.0)
        self.assertEqual(params["grow"], 10)
        self.assertEqual(params["n_sigma"], 1.0)

    def test_corrupt_file_and_legacy_top_level(self):
        mask_params.STORE.write_text("{bad", encoding="utf-8")
        self.assertEqual(mask_params.load("x"), mask_params.DEFAULTS)
        mask_params.STORE.write_text(json.dumps({
            "lmfp1_lab6": {"ratio": 6.0}}), encoding="utf-8")
        self.assertEqual(mask_params.load("lmfp1_lab6")["ratio"], 6.0)


if __name__ == "__main__":
    unittest.main()
