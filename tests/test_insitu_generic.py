"""通用多相原位模式的纯函数、批次行与入口（2026-11-11 用户第 14 条）。"""
import math
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from PySide6.QtWidgets import QApplication  # noqa: E402

try:
    from tests import _gui_integration_support as _support
    from tests._gui_integration_support import *  # noqa: F401,F403
except ModuleNotFoundError:  # 直接运行本文件
    from tests import _gui_integration_support as _support
    from tests._gui_integration_support import *  # noqa: F401,F403

from xrd_toolkit.simulation import insitu_generic as gen  # noqa: E402

RICH = ROOT / "data" / "structures" / "LiFePO4_olivine.cif"
POOR = ROOT / "data" / "structures" / "Mn0.6Fe0.4PO4_heterosite.cif"


def setUpModule():
    _support.setUpModule()


def _duck(*, rwp=2.0, success=True, rich_a=10.33,
          poor_cell=(9.80, 5.83, 4.77),
          labels=("LiFePO4_olivine", "Mn0_6Fe0_4PO4_heterosite")):
    poor = labels[1]
    return SimpleNamespace(
        rwp=rwp, rp=1.0, success=success, scale=2.0,
        peak_table_intensities=np.array([10.0, 10.0]),
        peak_table_norm=250.0,
        params={"a": rich_a, "b": 6.00, "c": 4.69,
                f"{poor}:a": poor_cell[0], f"{poor}:b": poor_cell[1],
                f"{poor}:c": poor_cell[2],
                f"{poor}:scale": 1.0, "scale": 1.0},
        extra_lebail={},
        extra_phase_table_sum={poor: 20.0},
        extra_phase_norm={poor: 250.0})


class TestGenericEngine(unittest.TestCase):
    def test_cell_free_by_symmetry(self):
        from xrd_toolkit.simulation import load_structure
        self.assertEqual(
            gen.cell_free_params(load_structure(str(RICH))),
            ("a", "b", "c"))
        al = load_structure(str(ROOT / "data" / "structures" / "Al_metal.cif"))
        self.assertEqual(gen.cell_free_params(al), ("a",))

    def test_phases_labels_unique(self):
        phases = gen.make_phases([RICH, POOR])
        self.assertEqual(len({p.label for p in phases}), 2)
        self.assertFalse(phases[0].lebail)
        self.assertFalse(phases[1].lebail)

    def test_fractions_use_absolute_amplitudes(self):
        phases = gen.make_phases([RICH, POOR])
        fracs = gen.fractions(_duck(), phases)
        self.assertAlmostEqual(fracs[phases[0].label], 100.0 / 150.0)
        self.assertAlmostEqual(fracs[phases[1].label], 50.0 / 150.0)

    def test_batch_rows_and_csv(self):
        batch = gen.GenericBatch([RICH, POOR], {}, 1.0)
        labels = [p.label for p in batch.phases]
        poor_cell = tuple(float(x) for x in
                          batch.phases[1].structure.lattice.abc)
        with mock.patch.object(
                gen, "load_frame",
                return_value=(np.array([1.0, 2.0]),
                              np.array([1.0, 2.0]), np.zeros(2),
                              {"fit": 0.1})), \
             mock.patch.object(gen.GenericBatch, "_fit_once",
                               return_value=_duck(labels=labels,
                                                  poor_cell=poor_cell)):
            row = batch.fit_frame("frame.tif")
        self.assertEqual(row["status"], "ok")
        self.assertAlmostEqual(row["f_LiFePO4_olivine"], 100.0 / 150.0,
                               places=6)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "generic.csv"
            batch.write_csv(out)
            header = out.read_text(encoding="utf-8").splitlines()[0]
        self.assertIn("f_LiFePO4_olivine", header)
        self.assertIn("Mn0_6Fe0_4PO4_heterosite_a", header)

    def test_phase_set_roles_and_fraction_denominator(self):
        """2026-11-17：rich/poor 样品相 + Al 参考相；Al 不进样品分数。"""
        from xrd_toolkit.simulation.phase_set import lmfp_phase_set
        pset = lmfp_phase_set()
        batch = gen.GenericBatch([], {}, 1.0, phase_set=pset)
        self.assertEqual([p.role for p in batch.phases],
                         ["sample", "sample", "reference"])
        self.assertEqual(batch._main_phase().label, "rich")
        labels = ["rich", "poor", "Al"]
        duck = _duck(labels=("rich", "poor"))
        duck.extra_phase_table_sum["Al"] = 10.0
        duck.extra_phase_norm["Al"] = 100.0
        duck.params["Al:scale"] = 1.0
        fracs = gen.fractions(duck, batch.phases)
        self.assertAlmostEqual(fracs["rich"], 100.0 / 150.0, places=6)
        self.assertAlmostEqual(fracs["poor"], 50.0 / 150.0, places=6)
        self.assertFalse(math.isfinite(fracs["Al"]), "参考相不进分母")

    def test_prepare_batch_from_scheme(self):
        scheme = {
            "name": "demo", "adapter": "", "policy": {}, "views": {},
            "instrument": {"mode": "none"},
            "phases": [{"path": str(RICH), "label": "demo",
                        "role": "sample", "width_free": False,
                        "size_um": 0.2}],
        }
        batch = gen.prepare_batch("lmfp1_lab6", scheme)
        self.assertIsInstance(batch, gen.GenericBatch)
        self.assertNotIsInstance(batch, gen.LMFPBatch)
        self.assertEqual(batch.instrument, {}, "mode=none 不固定仪器")
        self.assertAlmostEqual(batch.phases[0].size_um, 0.2)
        self.assertFalse(batch.phases[0].width_free,
                         "方案里的 PhaseSpec 字段要原样进批处理")

    def test_prepare_batch_uses_lmfp_adapter(self):
        scheme = {"name": "LMFP", "adapter": "lmfp", "policy": {},
                  "views": {}, "instrument": {"mode": "none"},
                  "phases": [{"path": str(RICH), "label": "rich"}]}
        batch = gen.prepare_batch("lmfp1_lab6", scheme)
        self.assertIsInstance(batch, gen.LMFPBatch)

    def test_resolve_instrument_modes(self):
        from xrd_toolkit.simulation.phase_set import InstrumentSpec
        record = {"wavelength": 1.0, "params": {"zero": 0.1}}
        self.assertEqual(
            gen.resolve_instrument(
                InstrumentSpec(mode="baseline"), {}, 1.0, record),
            {"zero": 0.1})
        self.assertIsNone(gen.resolve_instrument(
            InstrumentSpec(mode="baseline"), {}, 2.0, record),
            "λ 不符不能用旧基线")
        self.assertIsNone(gen.resolve_instrument(
            InstrumentSpec(mode="none"), {}, 1.0, record))

    # ── 背景模型/窗口（2026-11-19 B4） ─────────────────────
    def _bg_batch(self, **policy_kwargs):
        from xrd_toolkit.simulation.phase_set import (
            BatchPolicy, generic_phase_set)
        pset = generic_phase_set([RICH], name="demo")
        pset.policy = BatchPolicy(**policy_kwargs)
        return gen.GenericBatch([], {}, 1.5406, phase_set=pset)

    def test_background_models_reach_fit_pattern(self):
        cases = [
            ("estimated", {"background_window": 0.3},
             {"knots": 0.25, "smooth": gen.BG_SMOOTH, "bg_is_array": True}),
            ("chebyshev", {"background_order": 7},
             {"background": 7, "smooth": 0.0, "bg_is_array": False}),
            ("none", {}, {"background": None, "smooth": 0.0,
                          "bg_is_array": False}),
        ]
        for model, extra, expect in cases:
            with self.subTest(model=model):
                batch = self._bg_batch(background_model=model, **extra)
                t = np.linspace(1.0, 8.0, 40)
                y = np.ones(40)
                bg = np.zeros(40)
                seen = {}

                def fake_fit(*_args, **kwargs):
                    seen.update(kwargs)
                    return _duck()

                with mock.patch.object(gen, "fit_pattern",
                                       side_effect=fake_fit):
                    batch._fit_once(t, y, bg, 10)
                if expect["bg_is_array"]:
                    self.assertIs(seen["background"], bg)
                    self.assertEqual(seen["background_knots"],
                                     expect["knots"])
                else:
                    self.assertEqual(seen["background"],
                                     expect["background"])
                    self.assertNotIn("background_knots", seen)
                self.assertEqual(seen["background_smooth"], expect["smooth"])

    def test_background_window_reaches_load_frame(self):
        batch = self._bg_batch(background_window=0.45)
        captured = {}

        def fake_load(_path, _geom, _mask, **kwargs):
            captured.update(kwargs)
            return (np.linspace(1.0, 8.0, 20), np.ones(20),
                    np.zeros(20), {})

        with mock.patch.object(gen, "load_frame", side_effect=fake_load), \
             mock.patch.object(gen.GenericBatch, "_fit_once",
                               return_value=_duck()):
            batch.fit_frame("frame.tif")
        self.assertAlmostEqual(captured["bg_window"], 0.45)

    def test_bad_background_policy_fails_early(self):
        with self.assertRaises(ValueError):
            self._bg_batch(background_model="magic")
        with self.assertRaises(ValueError):
            self._bg_batch(background_window=0.0)

    def test_scheme_roundtrip_keeps_background_settings(self):
        from xrd_toolkit.simulation.phase_set import (
            phase_set_from_dict, phase_set_to_dict)
        batch = self._bg_batch(background_model="chebyshev",
                               background_order=8,
                               background_window=0.35)
        data = phase_set_to_dict(batch.phase_set)
        self.assertEqual(data["policy"]["background_model"], "chebyshev")
        back = phase_set_from_dict(data)
        self.assertEqual(back.policy.background_model, "chebyshev")
        self.assertEqual(back.policy.background_order, 8)
        self.assertAlmostEqual(back.policy.background_window, 0.35)

    def test_cell_jump_keeps_seed(self):
        batch = gen.GenericBatch([RICH, POOR], {}, 1.0)
        batch.frame_no = 1        # 第一帧不判晶胞跳变（与 LMFP 引擎同口径）
        before = dict(batch.seed)
        labels = [p.label for p in batch.phases]
        poor_cell = tuple(float(x) for x in
                          batch.phases[1].structure.lattice.abc)
        with mock.patch.object(
                gen, "load_frame",
                return_value=(np.array([1.0, 2.0]),
                              np.array([1.0, 2.0]), np.zeros(2), {})), \
             mock.patch.object(gen.GenericBatch, "_fit_once",
                               return_value=_duck(rich_a=10.80,
                                                  poor_cell=poor_cell,
                                                  labels=labels)):
            row = batch.fit_frame("frame.tif")
        self.assertEqual(row["status"], "cell_jump")
        self.assertEqual(batch.seed, before)


class TestGenericDualStart(unittest.TestCase):
    """2026-11-27：通用引擎接上共享双起点规则和溯源列。"""

    def _batch(self, *, guard=2.45, mode="auto"):
        from xrd_toolkit.simulation.phase_set import (
            BatchPolicy, generic_phase_set)
        pset = generic_phase_set([RICH, POOR])
        pset.policy = BatchPolicy(dual_start=mode, auto_rwp_guard=guard,
                                  pending_frames=2)
        batch = gen.GenericBatch([], {}, 1.0, phase_set=pset)
        # 种子必须不是 CIF 端元，否则 _is_cif_seed 会直接跳过第二起点。
        batch.seed[batch.phases[0].label] = (10.20, 6.00, 4.70)
        return batch

    def _run(self, results, **policy):
        batch = self._batch(**policy)
        with mock.patch.object(
                gen, "load_frame",
                return_value=(np.linspace(1.0, 8.0, 20), np.ones(20),
                              np.zeros(20), {})), \
             mock.patch.object(gen.GenericBatch, "_fit_once",
                               side_effect=results) as fit:
            row = batch.fit_frame("frame.tif")
        return row, fit

    def test_high_rwp_runs_second_start_and_records_provenance(self):
        row, fit = self._run([_duck(rwp=2.473), _duck(rwp=2.416)])
        self.assertEqual(fit.call_count, 2)
        self.assertEqual(row["dual_start"], 1)
        self.assertEqual(row["start_used"], "cif")
        self.assertAlmostEqual(row["rwp_seed"], 2.473)
        self.assertAlmostEqual(row["rwp_cif"], 2.416)
        self.assertEqual(row["success_seed"], 1)
        self.assertEqual(row["success_cif"], 1)
        self.assertAlmostEqual(row["f_LiFePO4_olivine_seed"], 2.0 / 3.0,
                               places=6)
        self.assertEqual(row["status"][:2], "ok")

    def test_low_rwp_converged_seed_skips_second_start(self):
        row, fit = self._run([_duck(rwp=2.10)])
        self.assertEqual(fit.call_count, 1)
        self.assertEqual(row["dual_start"], 0)
        self.assertEqual(row["start_used"], "seed")
        self.assertAlmostEqual(row["rwp_seed"], 2.10)
        self.assertTrue(math.isnan(row["rwp_cif"]))

    def test_non_strict_seed_runs_second_start(self):
        row, fit = self._run([_duck(success=False, rwp=2.60),
                              _duck(rwp=2.30)])
        self.assertEqual(fit.call_count, 2)
        self.assertEqual(row["start_used"], "cif")
        self.assertEqual(row["success_seed"], 0)

    def test_no_guard_keeps_old_generic_behavior(self):
        row, fit = self._run([_duck(rwp=9.0)], guard=None)
        self.assertEqual(fit.call_count, 1)
        self.assertEqual(row["start_used"], "seed")

    def test_columns_include_provenance(self):
        batch = self._batch()
        cols = batch.columns()
        for key in ("dual_start", "start_used", "rwp_seed", "rwp_cif",
                    "success_seed", "success_cif",
                    "f_LiFePO4_olivine_seed", "f_LiFePO4_olivine_cif"):
            self.assertIn(key, cols)

    def test_scheme_roundtrip_keeps_guard(self):
        from xrd_toolkit.simulation.phase_set import (
            phase_set_from_dict, phase_set_to_dict)
        batch = self._batch()
        data = phase_set_to_dict(batch.phase_set)
        self.assertEqual(data["policy"]["auto_rwp_guard"], 2.45)
        back = phase_set_from_dict(data)
        self.assertEqual(back.policy.auto_rwp_guard, 2.45)

    def test_lmfp_preset_has_guard_generic_does_not(self):
        from xrd_toolkit.simulation.phase_set import (
            BatchPolicy, lmfp_phase_set)
        self.assertEqual(lmfp_phase_set().policy.auto_rwp_guard, 2.45)
        self.assertIsNone(BatchPolicy.generic().auto_rwp_guard)

    def test_old_lmfp_scheme_gets_guard_default(self):
        from xrd_toolkit.simulation.phase_set import (
            lmfp_phase_set, phase_set_from_dict, phase_set_to_dict)
        data = phase_set_to_dict(lmfp_phase_set())
        data["policy"].pop("auto_rwp_guard", None)
        back = phase_set_from_dict(data)
        self.assertEqual(back.policy.auto_rwp_guard, 2.45)

    def test_lmfp_columns_include_legacy_aliases(self):
        import tempfile
        from xrd_toolkit.simulation.phase_set import lmfp_phase_set
        batch = gen.LMFPBatch([], {}, 1.0, phase_set=lmfp_phase_set())
        cols = batch.columns()
        for key in ("wt_al", "al_a", "al_ref"):
            self.assertIn(key, cols)
        row = {key: 0 for key in cols}
        row.update({"frame": 1, "file": "f.tif", "status": "ok",
                    "usable": True, "start_used": "seed"})
        batch.rows = [row]
        with tempfile.TemporaryDirectory() as td:
            path = batch.write_csv(Path(td) / "lmfp.csv")
            header = path.read_text(encoding="utf-8").splitlines()[0]
        self.assertIn("wt_al", header)
        self.assertIn("start_used", header)


class TestGenericEntry(unittest.TestCase):
    def setUp(self):
        self.w = create_window()
        self.w.show()
        QApplication.processEvents()
        self.w.insitu_btn.click()
        QApplication.processEvents()
        self.editor = self.w.insitu_editor

    def tearDown(self):
        self.w.hide()
        self.w.close()
        QApplication.processEvents()

    def _pick(self, data):
        combo = self.editor.preset_combo
        combo.setCurrentIndex(combo.findData(data))
        combo.activated.emit(combo.currentIndex())
        QApplication.processEvents()

    def test_default_page_is_generic_and_empty(self):
        """默认是通用空相表（2026-11-18）。"""
        self.assertEqual(self.editor.preset_combo.currentData(), "generic")
        self.assertEqual(self.editor.phase_table.rowCount(), 0)
        self.assertEqual(self.editor._table_keys[:5],
                         ["frame", "file", "status", "rwp", "rp"])

    def test_lmfp_preset_fills_the_phase_table_in_place(self):
        self._pick("lmfp")
        table = self.editor.phase_table
        self.assertEqual(table.rowCount(), 3)
        roles = [table.cellWidget(r, 0).currentData() for r in range(3)]
        self.assertEqual(roles, ["sample", "sample", "reference"])
        self.assertEqual(table.cellWidget(2, 3).currentData(), "lebail")
        self.assertFalse(table.cellWidget(2, 4).isChecked())
        self.assertFalse(table.cellWidget(1, 5).isChecked())
        table.cellWidget(1, 5).setChecked(True)
        self.assertEqual(
            self.editor.phase_table.phases()[1]["free_extra"],
            ("b_iso", "size_um", "mustrain"))
        # 结果表列按方案（LMFP 紧凑 12 列）
        self.assertEqual(len(self.editor._table_keys), 12)
        self.assertIn("f_rich", self.editor._table_keys)
        self.assertEqual(self.editor._scheme_instrument.mode, "lab6")
        self.assertEqual(self.editor.instrument_mode.currentData(), "lab6")

    def test_instrument_source_can_be_changed(self):
        """通用页也能选仪器来源（2026-11-19 手测）。"""
        self.assertEqual(self.editor.instrument_mode.currentData(), "baseline")
        mode = self.editor.instrument_mode
        mode.setCurrentIndex(mode.findData("none"))
        QApplication.processEvents()
        self.assertEqual(self.editor._scheme_instrument.mode, "none")
        self.assertIn("不固定", self.editor.instrument_label.text())
        mode.setCurrentIndex(mode.findData("lab6"))
        QApplication.processEvents()
        self.assertEqual(self.editor._scheme_instrument.mode, "lab6")
        self.assertFalse(self.editor.lab6_btn.isHidden())
        self._pick("generic")
        self.assertEqual(self.editor.instrument_mode.currentData(),
                         "baseline")

    def test_compact_columns_and_show_all(self):
        """通用方案默认紧凑列，右键可切全部列（2026-11-19 手测）。"""
        from types import SimpleNamespace
        all_keys = ["frame", "file", "status", "rwp", "rp",
                    "f_demo", "f_demo_esd", "a_demo", "wt_demo",
                    "demo_a", "demo_a_esd", "demo_b", "demo_b_esd",
                    "demo_c", "demo_c_esd", "f_Al", "wt_Al", "Al_a",
                    "t_fit_s", "t_total_s"]
        phase = SimpleNamespace(label="demo", is_sample=lambda: True)
        ref = SimpleNamespace(label="Al", is_sample=lambda: False)
        from xrd_toolkit.gui.insitu_workbench import compact_table_columns
        keys = compact_table_columns(all_keys, [phase, ref])
        self.assertEqual(keys, ["frame", "file", "status", "rwp",
                                "f_demo", "wt_demo", "demo_a", "demo_c",
                                "t_fit_s"])
        self.assertNotIn("Al_a", keys)
        self.assertNotIn("f_demo_esd", keys)
        self.assertNotIn("rp", keys)
        # 右键切全部列/切回
        self.editor._all_keys = list(all_keys)
        self.editor._compact_keys = list(keys)
        self.editor.batch = SimpleNamespace(rows=[])
        self.editor._show_all_columns = True
        self.editor._rebuild_table_columns()
        self.assertEqual(self.editor._table_keys, all_keys)
        self.editor._show_all_columns = False
        self.editor._rebuild_table_columns()
        self.assertEqual(self.editor._table_keys, keys)

    def test_prepared_batch_uses_compact_columns(self):
        """准备完成后通用方案自动用紧凑列（2026-11-19 手测）。"""
        from types import SimpleNamespace
        all_keys = ["frame", "file", "status", "usable", "rwp", "rp",
                    "f_demo", "a_demo", "wt_demo", "demo_a", "demo_b",
                    "demo_c", "t_fit_s", "t_total_s"]
        phase = SimpleNamespace(label="demo", display_name="Demo phase",
                                is_sample=lambda: True)
        ref = SimpleNamespace(label="Al", display_name="Al foil",
                              is_sample=lambda: False)
        fake = SimpleNamespace(
            rows=[],
            mask=None,
            columns=lambda: list(all_keys),
            phase_set=SimpleNamespace(
                phases=[phase, ref],
                views=SimpleNamespace(table_columns=())),
            instrument=None)
        editor = self.editor
        editor.batch = fake
        editor.frames = []
        editor._on_prepared()
        self.assertEqual(editor._all_keys, all_keys)
        self.assertEqual(editor._table_keys,
                         ["frame", "file", "status", "rwp",
                          "f_demo", "wt_demo", "demo_a", "demo_c",
                          "t_fit_s"])
        # 短表头 + 图例（2026-11-19 手测：13 列时机器标签太长）
        self.assertEqual(editor.table.horizontalHeaderItem(4).text(), "f1")
        self.assertEqual(editor.table.horizontalHeaderItem(5).text(), "wt1%")
        self.assertEqual(editor.table.horizontalHeaderItem(6).text(), "a1")
        self.assertEqual(editor.table.horizontalHeaderItem(7).text(), "c1")
        self.assertIn("P1 = Demo phase", editor.table_legend.text())
        self.assertNotIn("Al foil", editor.table_legend.text())
        self.assertFalse(editor.table_legend.isHidden())
        # 诊断列：完整表头、图例收起（全列里 f_demo 在第 7 列）
        editor._show_all_columns = True
        editor._rebuild_table_columns()
        self.assertEqual(editor.table.horizontalHeaderItem(6).text(),
                         "f demo")
        self.assertTrue(editor.table_legend.isHidden())
        # 切回紧凑列
        editor._show_all_columns = False
        editor._rebuild_table_columns()
        self.assertEqual(editor.table.horizontalHeaderItem(4).text(), "f1")
        self.assertFalse(editor.table_legend.isHidden())

    def test_details_dialog_edits_initial_cell(self):
        """相细节能填初始晶胞（2026-11-19 手测：Al 4.061 进不去 UI）。"""
        from xrd_toolkit.gui.insitu_phase_table import (
            _DetailsDialog, default_phase)
        spec = default_phase(RICH, set())
        dlg = _DetailsDialog(self.editor, spec)
        try:
            self.assertFalse(dlg.use_cell0.isChecked())
            self.assertIsNone(dlg.values()["cell0"])
            dlg.use_cell0.setChecked(True)
            for axis in ("a", "b", "c"):
                dlg.cell0_spins[axis].setValue(4.061)
            cell0 = dlg.values()["cell0"]
            self.assertEqual(len(cell0), 3)
            for value in cell0:
                self.assertAlmostEqual(value, 4.061)
        finally:
            dlg.close()
            dlg.deleteLater()
        spec2 = dict(spec)
        spec2["cell0"] = (9.8, 5.8, 4.77)
        dlg2 = _DetailsDialog(self.editor, spec2)
        try:
            self.assertTrue(dlg2.use_cell0.isChecked())
            self.assertAlmostEqual(dlg2.values()["cell0"][0], 9.8)
        finally:
            dlg2.close()
            dlg2.deleteLater()
        QApplication.processEvents()

    def test_mask_hint_for_known_geometry(self):
        """有现成掩膜却没选时提醒（2026-11-19：忘选掩膜 Rwp 8%）。"""
        from xrd_toolkit.gui import insitu_workbench as wb
        with tempfile.TemporaryDirectory() as tmp:
            masks = Path(tmp) / "masks"
            masks.mkdir()
            (masks / "demo_geom_mask.npy").write_bytes(b"")
            editor = self.editor
            old_mask = editor.mask_path
            editor.mask_path = None
            try:
                with mock.patch.object(editor.main, "config_name",
                                       "demo_geom"), \
                     mock.patch.object(wb.paths, "default_outputs_dir",
                                       return_value=Path(tmp)):
                    self.assertEqual(editor._suggested_mask_path().name,
                                     "demo_geom_mask.npy")
                    editor._refresh_mask_label()
                    self.assertIn("建议", editor.mask_label.text())
            finally:
                editor.mask_path = old_mask
                editor._refresh_mask_label()

    def test_new_phase_defaults(self):
        """新相默认 0.1 µm、通用策略 pending=2（2026-11-19 手测）。"""
        from xrd_toolkit.gui.insitu_phase_table import default_phase
        from xrd_toolkit.simulation.phase_set import (
            BatchPolicy, spec_from_path)
        self.assertEqual(default_phase(RICH, set())["size_um"], 0.1)
        self.assertEqual(spec_from_path(RICH).size_um, 0.1)
        self.assertEqual(BatchPolicy.generic().pending_frames, 2)

    def test_role_dropdown_applies_reference_defaults(self):
        """直接在下拉里改角色也要套默认（2026-11-19 手测）。"""
        table = self.editor.phase_table
        table.add_paths(
            [str(ROOT / "data" / "structures" / "Al_metal.cif")])
        role = table.cellWidget(0, 0)
        role.setCurrentIndex(role.findData("reference"))
        QApplication.processEvents()
        self.assertFalse(table.cellWidget(0, 2).isChecked())
        self.assertEqual(table.cellWidget(0, 3).currentData(), "lebail")
        self.assertFalse(table.cellWidget(0, 4).isChecked())
        spec = table.phases()[0]
        self.assertEqual(spec["role"], "reference")
        self.assertFalse(spec["cell_free"])
        self.assertEqual(spec["intensity"], "lebail")
        self.assertFalse(spec["include_in_fraction"])
        self.assertTrue(table.dirty)
        # 切回样品相也恢复默认
        role.setCurrentIndex(role.findData("sample"))
        QApplication.processEvents()
        self.assertTrue(table.cellWidget(0, 2).isChecked())
        self.assertEqual(table.cellWidget(0, 3).currentData(), "structure")
        self.assertTrue(table.cellWidget(0, 4).isChecked())

    def test_switching_to_generic_clears_the_table(self):
        self._pick("lmfp")
        self._pick("generic")
        self.assertEqual(self.editor.phase_table.rowCount(), 0)
        self.assertEqual(self.editor._scheme_adapter, "")

    def test_background_controls_follow_scheme(self):
        """背景模型/窗口/阶数（2026-11-19 B4）：界面、策略、存载一致。"""
        from xrd_toolkit.gui import insitu_workbench as wb
        self._pick("lmfp")
        self.assertEqual(self.editor.bg_model.currentData(), "estimated")
        self.assertTrue(self.editor.bg_window.isEnabled())
        self.assertFalse(self.editor.bg_order.isEnabled())
        self.editor.bg_model.setCurrentIndex(
            self.editor.bg_model.findData("chebyshev"))
        QApplication.processEvents()
        self.assertFalse(self.editor.bg_window.isEnabled())
        self.assertTrue(self.editor.bg_order.isEnabled())
        self.editor.bg_order.setValue(7)
        self.editor.bg_window.setValue(0.35)
        policy = self.editor._current_policy()
        self.assertEqual(policy.background_model, "chebyshev")
        self.assertEqual(policy.background_order, 7)
        self.assertAlmostEqual(policy.background_window, 0.35)

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bg.json"
            with mock.patch.object(
                    wb.QFileDialog, "getSaveFileName",
                    return_value=(str(out), "")):
                self.editor._save_scheme()
            self._pick("generic")
            self.assertEqual(self.editor.bg_model.currentData(), "estimated",
                             "切回通用方案应回到默认背景设置")
            with mock.patch.object(
                    wb.QFileDialog, "getOpenFileName",
                    return_value=(str(out), "")):
                self.editor._load_scheme()
        self.assertEqual(self.editor.bg_model.currentData(), "chebyshev")
        self.assertEqual(self.editor.bg_order.value(), 7)
        self.assertAlmostEqual(self.editor.bg_window.value(), 0.35)
        self.assertFalse(self.editor.bg_window.isEnabled())
        self.assertTrue(self.editor.bg_order.isEnabled())

    def test_rename_updates_plot_name_and_bragg_labels(self):
        """改名同时改图内名称（2026-11-18 用户："Li rich 这些怎么改"）。"""
        from xrd_toolkit.gui import insitu_phase_table as pt
        self._pick("lmfp")
        table = self.editor.phase_table
        table.setCurrentCell(0, 1)

        class _Dlg:
            def __init__(self, *args, **kwargs):
                pass

            def exec(self):
                return pt.QDialog.Accepted

            def values(self):
                return ("富锂相（新）", "Li-rich custom")

        with mock.patch.object(pt, "_NamesDialog", _Dlg):
            self.editor._rename_phase()
        self.assertEqual(table.item(0, 1).text(), "富锂相（新）")
        spec = table.phases()[0]
        self.assertEqual(spec["display_name"], "富锂相（新）")
        self.assertEqual(spec["plot_name"], "Li-rich custom")
        self.assertEqual(
            self.editor._preview_phase_set.phases[0].plot_name,
            "Li-rich custom")
        self.assertEqual(
            self.editor._bragg_labels()["rich"], "Li-rich custom")

    def test_phase_details_update_spec_and_color(self):
        """每相细节（2026-11-18）：B/尺寸/晶胞范围等写回相表，颜色立即生效。"""
        from xrd_toolkit.gui import insitu_phase_table as pt
        self._pick("lmfp")
        table = self.editor.phase_table
        table.setCurrentCell(0, 1)

        class _Dlg:
            def __init__(self, parent, spec):
                self.spec = dict(spec)

            def exec(self):
                return pt.QDialog.Accepted

            def values(self):
                self.spec["size_um"] = 0.5
                self.spec["cell_bounds"] = {"a": (10.3, 10.4)}
                self.spec["color"] = "#ff0000"
                return self.spec

        with mock.patch.object(pt, "_DetailsDialog", _Dlg):
            self.editor._edit_phase_details()
        spec = table.phases()[0]
        self.assertAlmostEqual(spec["size_um"], 0.5)
        self.assertEqual(spec["cell_bounds"], {"a": (10.3, 10.4)})
        self.assertEqual(spec["color"], "#ff0000")
        self.assertEqual(
            self.editor._preview_phase_set.phases[0].color, "#ff0000")

    def test_scheme_save_load_roundtrip(self):
        from xrd_toolkit.gui import insitu_workbench as wb
        self._pick("lmfp")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "demo.json"
            with mock.patch.object(
                    wb.QFileDialog, "getSaveFileName",
                    return_value=(str(out), "")):
                self.editor._save_scheme()
            self.assertTrue(out.is_file())
            self._pick("generic")
            self.assertEqual(self.editor.phase_table.rowCount(), 0)
            with mock.patch.object(
                    wb.QFileDialog, "getOpenFileName",
                    return_value=(str(out), "")):
                self.editor._load_scheme()
            self.assertEqual(self.editor.phase_table.rowCount(), 3)
            self.assertEqual(
                [p["label"] for p in self.editor.phase_table.phases()],
                ["rich", "poor", "Al"])
            self.assertEqual(self.editor._scheme_views.shade_phase, "rich")
            self.assertEqual(
                self.editor._scheme_views.peak_windows[0].tth_min, 1.18)
            self.assertEqual(self.editor._scheme_instrument.mode, "lab6")
            self.assertEqual(self.editor._scheme_adapter, "lmfp")
            self.assertEqual(
                tuple(self.editor.phase_table.phases()[2]["cell0"]),
                (4.061, 4.061, 4.061), "Al 的初始晶胞要随方案保存")
