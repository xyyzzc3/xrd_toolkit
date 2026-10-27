"""结果摘要测试（2026-10-26）。

用户拍板方案 A：同一个 `.txt` 里先给结论、再给详情。这里钉住摘要的
格式约定（± 不确定度、µm/nm 双单位、不可辨识的措辞、分隔线位置）。
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xrd_toolkit.simulation.report import (  # noqa: E402
    fit_entries,
    fixed_cell_text,
    format_pm,
    format_size_um,
    summary_block,
)


def _result(**params):
    defaults = {
        "a": 10.3522, "b": 6.0232, "c": 4.7035,
        "size_um": 0.0931, "mustrain": 1e-20, "b_iso": 4.11,
    }
    defaults.update(params)
    stderr = {key: 0.001 for key in defaults}
    stderr["mustrain"] = 1e3
    stderr["b_iso"] = 0.63
    return SimpleNamespace(params=defaults, stderr=stderr, rwp=3.02,
                           success=True)


class TestSummaryFormat(unittest.TestCase):
    def test_format_pm_follows_esd_precision(self):
        self.assertEqual(format_pm(10.3522, 0.0013, unit="Å"),
                         "10.3522 ± 0.0013 Å")
        self.assertEqual(format_pm(4.1568, None, unit="Å"), "4.1568 Å")

    def test_format_size_gives_both_units(self):
        self.assertEqual(format_size_um(0.0931), "93 nm（0.093 µm）")

    def test_fixed_cell_says_it_comes_from_cif(self):
        cubic = SimpleNamespace(lattice=SimpleNamespace(abc=(4.1568,) * 3))
        self.assertEqual(fixed_cell_text(cubic),
                         "固定为 CIF 值（a = 4.1568 Å）")
        ortho = SimpleNamespace(lattice=SimpleNamespace(abc=(10.35, 6.02, 4.70)))
        self.assertIn("a = 10.3500", fixed_cell_text(ortho))


class TestFitEntries(unittest.TestCase):
    def test_unidentifiable_strain_and_large_b_are_flagged(self):
        entries, notes = fit_entries("x.tif", "LMFP", _result())
        by_label = dict(entries)
        self.assertIn("不可辨识", by_label["微应变"])
        self.assertIn("93 nm", by_label["晶粒尺寸"])
        self.assertTrue(any("偏大" in note for note in notes))

    def test_le_bail_quality_and_al_scale(self):
        result = _result()
        lebail = SimpleNamespace(rwp=1.73)
        entries, _notes = fit_entries(
            "x.tif", "LMFP", result, lebail=lebail, al_scale=0.626)
        by_label = dict(entries)
        self.assertEqual(by_label["拟合质量"],
                         "结构 Rwp 3.02% / Le Bail 1.73%")
        self.assertIn("0.63", by_label["第二相"])

    def test_al_per_reflection_summary(self):
        result = _result()
        result.extra_lebail = {"Al": {"tth": [3.0]}}
        entries, _notes = fit_entries(
            "x.tif", "LMFP", result, al_scale=0.5)
        self.assertIn("逐峰强度", dict(entries)["第二相"])

    def test_zero_b_at_lower_bound_is_flagged(self):
        _entries, notes = fit_entries("x.tif", "LMFP", _result(b_iso=0.0))
        self.assertTrue(any("停在下限 0" in note for note in notes))

    def test_fixed_cell_and_unrefined_strain(self):
        result = SimpleNamespace(
            params={"size_um": 1.0437, "b_iso": 0.0943},
            stderr={"size_um": 0.001, "b_iso": 1.06},
            rwp=4.48, success=True)
        entries, _notes = fit_entries(
            "lab6.tif", "LaB6", result, cell_fixed="固定为 CIF 值")
        by_label = dict(entries)
        self.assertEqual(by_label["晶胞"], "固定为 CIF 值")
        self.assertEqual(by_label["微应变"], "未精修（固定 0）")
        # esd 比数值还大 → 摘要里明说“基本未定”，不假装有结果
        self.assertIn("基本未定", by_label["温度因子 ΔB"])


class TestSummaryBlock(unittest.TestCase):
    def test_conclusion_comes_first(self):
        text = summary_block([("样品", "x.tif")], ["一条提示"])
        self.assertTrue(text.startswith("===== 结果摘要 ====="))
        self.assertIn("样品: x.tif", text)
        self.assertIn("提示: 一条提示", text)
        self.assertLess(text.index("提示:"),
                        text.index("===== 以下为复现与排错详情 ====="))


if __name__ == "__main__":
    unittest.main()
