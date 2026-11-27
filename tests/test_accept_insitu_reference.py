"""Tests for accepting a new in-situ reference (2026-11-27)."""
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import accept_insitu_reference as accept  # noqa: E402


def _write(path, frames):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["frame", "rwp"])
        writer.writeheader()
        for frame in frames:
            writer.writerow({"frame": frame, "rwp": 2.2})


class TestAcceptReference(unittest.TestCase):
    def test_copies_csv_and_writes_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            candidate = Path(td) / "cand.csv"
            reference = Path(td) / "ref.csv"
            _write(candidate, [1, 2, 3])
            code = accept.main([
                "--candidate", str(candidate),
                "--reference", str(reference),
                "--version", "0.2.2",
                "--engine", "generic",
                "--allow-frames", "2"])
            self.assertEqual(code, 0)
            self.assertTrue(reference.is_file())
            manifest = json.loads(
                reference.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "0.2.2")
        self.assertEqual(manifest["engine"], "generic")
        self.assertEqual(manifest["frames"], 3)
        self.assertEqual(manifest["allow_frames"], [2])

    def test_refuses_to_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as td:
            candidate = Path(td) / "cand.csv"
            reference = Path(td) / "ref.csv"
            _write(candidate, [1])
            reference.write_text("old", encoding="utf-8")
            with self.assertRaises(SystemExit):
                accept.main([
                    "--candidate", str(candidate),
                    "--reference", str(reference),
                    "--version", "0.2.2"])
            code = accept.main([
                "--candidate", str(candidate),
                "--reference", str(reference),
                "--version", "0.2.2", "--force"])
        self.assertEqual(code, 0)

    def test_rejects_csv_without_frame_column(self):
        with tempfile.TemporaryDirectory() as td:
            candidate = Path(td) / "cand.csv"
            candidate.write_text("rwp\n2.2\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                accept.main([
                    "--candidate", str(candidate),
                    "--reference", str(Path(td) / "ref.csv"),
                    "--version", "0.2.2"])


if __name__ == "__main__":
    unittest.main()
