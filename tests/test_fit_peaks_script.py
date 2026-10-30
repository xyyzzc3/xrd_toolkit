"""`scripts/fit_peaks.py` 的报告口径测试（2026-10-31）。

不读真图：替换 load_1d / fit_single_peak，只钉报告里有没有 Scherrer
这一节、D 列和结论行，避免脚本改动后报告悄悄退回“只有 W-H”。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import fit_peaks as fp  # noqa: E402


def _fit(center, fwhm=0.05):
    return SimpleNamespace(
        center=center, fwhm=fwhm, fwhm_left=fwhm, fwhm_right=fwhm,
        m=1.0, eta=0.5, area=100.0, rwp=1.0, height=500.0,
        params={"center": center, "fwhm": fwhm})


class TestFitPeaksReport(unittest.TestCase):
    def test_report_has_scherrer_section_and_column(self):
        with tempfile.TemporaryDirectory() as td:
            image = Path(td) / "fake.tif"
            image.write_bytes(b"")
            argv = ["fit_peaks.py", "--image", str(image),
                    "--peaks", "20,40,60", "--outdir", td]
            with mock.patch.object(fp, "get_config",
                                   return_value={"geometry": {
                                       "wavelength_m": 1.5406e-10}}), \
                    mock.patch.object(
                        fp, "load_1d",
                        return_value=(np.linspace(10, 70, 600),
                                      np.full(600, 100.0),
                                      np.full(600, 100.0))), \
                    mock.patch.object(
                        fp, "fit_single_peak",
                        side_effect=lambda t, y, c, **kw: _fit(c)), \
                    mock.patch.object(sys, "argv", argv):
                fp.main()
            report = (Path(td) / "peakfit_fake.txt").read_text(encoding="utf-8")
        self.assertIn("=== Scherrer ===", report)
        self.assertIn("D_nm", report)
        self.assertIn("size median", report)
        self.assertIn("Scherrer 尺寸", report)
        self.assertIn("未扣仪器宽度", report)


if __name__ == "__main__":
    unittest.main()
