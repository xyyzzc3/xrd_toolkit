"""LaB₆ 仪器基线落盘的单元测试（2026-11-05）。"""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from xrd_toolkit.services import instrument_baseline as ib


class TestInstrumentBaseline(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "nested" / "baseline.json"

    def test_save_load_round_trip(self):
        saved = ib.save_baseline({
            "params": {"zero": 0.01, "u": 0.02, "v": -0.003,
                       "w": 0.004, "x": 0.005, "y": 0.006},
            "wavelength": 0.1223,
            "source": "LaB6_NIST660c.cif",
        }, path=self.path)
        self.assertEqual(saved, self.path)
        self.assertTrue(self.path.is_file())
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(raw["version"], 1)
        self.assertTrue(raw["saved_at"])
        self.assertEqual(raw["source"], "LaB6_NIST660c.cif")

        loaded = ib.load_baseline(self.path)
        self.assertIsNotNone(loaded)
        self.assertAlmostEqual(loaded["wavelength"], 0.1223)
        self.assertEqual(loaded["source"], "LaB6_NIST660c.cif")
        for name, value in raw["params"].items():
            self.assertAlmostEqual(loaded["params"][name], value)

    def test_missing_file_is_none(self):
        self.assertIsNone(ib.load_baseline(self.path))

    def test_corrupt_file_is_none(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("not json", encoding="utf-8")
        with redirect_stderr(io.StringIO()):
            self.assertIsNone(ib.load_baseline(self.path))

    def test_binary_file_is_none(self):
        """非 UTF-8 文件也不能让启动崩（UnicodeDecodeError 也要吞）。"""
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"\xff\xfe\x00\x01")
        with redirect_stderr(io.StringIO()):
            self.assertIsNone(ib.load_baseline(self.path))

    def test_bad_params_are_dropped(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({
            "params": {"zero": 0.0, "u": "bad", "v": float("nan"),
                       "w": 1.5},
            "wavelength": 0.12,
        }), encoding="utf-8")
        loaded = ib.load_baseline(self.path)
        self.assertIsNotNone(loaded)
        self.assertEqual(set(loaded["params"]), {"zero", "w"})
        self.assertAlmostEqual(loaded["params"]["w"], 1.5)

    def test_no_valid_params_returns_none(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"params": {"nope": 1}}),
                             encoding="utf-8")
        self.assertIsNone(ib.load_baseline(self.path))
        with self.assertRaises(ValueError):
            ib.save_baseline({"params": {"nope": 1}}, path=self.path)

    def test_overwrite_is_atomic_and_latest_wins(self):
        ib.save_baseline({"params": {"w": 1.0}, "wavelength": 0.1},
                         path=self.path)
        ib.save_baseline({"params": {"w": 2.0}, "wavelength": 0.2},
                         path=self.path)
        loaded = ib.load_baseline(self.path)
        self.assertAlmostEqual(loaded["params"]["w"], 2.0)
        self.assertAlmostEqual(loaded["wavelength"], 0.2)
        self.assertFalse(self.path.with_suffix(".json.tmp").exists(),
                         "原子写不该留下 .tmp")

    def test_environment_override(self):
        with mock.patch.dict(
                os.environ, {"XRD_INSTRUMENT_BASELINE": str(self.path)}):
            self.assertEqual(ib.baseline_path(), self.path)


if __name__ == "__main__":
    unittest.main()
