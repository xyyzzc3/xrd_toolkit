"""热图、2D 色标、背景扣除

2026-10-30 从 tests/test_gui_integration.py 拆出；导入与公共辅助见
tests/_gui_integration_support.py。
"""
try:
    from tests._gui_integration_support import *  # noqa: F401,F403
    from tests import _gui_integration_support as _support
except ModuleNotFoundError:  # 直接运行本文件：把仓库根加进 sys.path
    import sys
    from pathlib import Path as _Path
    sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    from tests._gui_integration_support import *  # noqa: F401,F403
    from tests import _gui_integration_support as _support


def setUpModule():
    _support.setUpModule()


class TestHeatmap(unittest.TestCase):
    """[热图]：勾选文件的 1D 曲线拼成 2θ×样品 强度热图。

    只拼已经算好的曲线（2026-10-16 栅栏：没出过 1D 的原始数据整批
    拦下）；显示参数（色图/归一化/对数/范围）走面板快照，图像 [应用]
    只重画、数据 [应用] 重读已经算好的曲线再画（不现场积分）。
    """

    def test_heatmap_labels_shorten_and_thin_out(self):
        """行名 = 号段短名 + 按高度抽稀（81 个长名字会糊成一条黑带）。

        用户 2026-09-26 的热图截图：81 个 "LMFP_1_atten0-000NN" 挤成
        黑带，还把整张图挤到右边。矮面板抽稀、高面板全标。
        """
        from matplotlib.figure import Figure
        names = [f"LMFP_1_atten0-{i:05d}.tif" for i in range(40)]
        short = Figure(figsize=(4, 2), dpi=100).add_subplot(111)
        labels = gui_plot_compare._short_labels(names, short)
        self.assertEqual(len(labels), 40)
        shown = [x for x in labels if x]
        self.assertLess(len(shown), 40, "矮面板上该抽稀")
        self.assertTrue(shown and all(x.isdigit() for x in shown),
                        f"该只剩号段：{shown[:3]}")
        tall = Figure(figsize=(6, 9), dpi=100).add_subplot(111)
        self.assertEqual(
            len([x for x in gui_plot_compare._short_labels(names, tall) if x]),
            40, "9 英寸高的面板放得下 40 个")

    def _heat_dock(self, w):
        """找到热图面板（键 = "热图"——单槽）。"""
        return next(d for k, d in w.plot_docks.items()
                    if k.startswith("热图"))

    def _wait_heat(self, w):
        return _wait_until(
            lambda: any(k.startswith("热图")
                        and getattr(d, "heat_data", None) is not None
                        for k, d in w.plot_docks.items()))

    def test_assemble_heatmap_same_grid_no_interp(self):
        r = [("a", np.array([1.0, 2.0, 3.0]), np.array([1.0, 2.0, 3.0])),
             ("b", np.array([1.0, 2.0, 3.0]), np.array([4.0, 5.0, 6.0]))]
        tth, matrix, stems, interp = gui_plot_compare._assemble_heatmap(r)
        self.assertFalse(interp)
        self.assertEqual(stems, ["a", "b"])
        np.testing.assert_array_equal(matrix, [[1, 2, 3], [4, 5, 6]])

    def test_assemble_heatmap_mismatched_grids_interp(self):
        """网格不一致（点数/区间不同）→ 重插值到第一个文件的网格。"""
        r = [("a", np.array([1.0, 2.0, 3.0]), np.array([1.0, 2.0, 3.0])),
             ("b", np.array([1.0, 3.0]), np.array([10.0, 30.0]))]
        tth, matrix, stems, interp = gui_plot_compare._assemble_heatmap(r)
        self.assertTrue(interp)
        np.testing.assert_allclose(matrix[1], [10.0, 20.0, 30.0])

    def test_assemble_heatmap_empty_returns_none(self):
        self.assertIsNone(gui_plot_compare._assemble_heatmap([]))
        self.assertIsNone(gui_plot_compare._assemble_heatmap(
            [("a", np.array([]), np.array([]))]))

    def test_heat_shown_modes(self):
        m = np.array([[1.0, 2.0], [3.0, 4.0]])
        np.testing.assert_array_equal(gui_state._heat_shown(m, "off"), m)
        np.testing.assert_allclose(gui_state._heat_shown(m, "global"),
                                   m / 4.0)
        # "每行各自最强峰"（each）已删：认不出的模式一律当 off（原样）
        for stale in ("each", True, None, "nonsense"):
            np.testing.assert_array_equal(gui_state._heat_shown(m, stale), m)

    def test_heatmap_display_params_redraw_live(self):
        """热图色图/对数/归一化改了就重画（原先这些控件谁都没接）。

        用户 2026-09-27："热图一单画出来就改不了颜色什么的，没法调整"。
        """
        w = create_window()
        try:
            _prime_1d(w, ["data/fake_a.tif", "data/fake_b.tif"],
                      stub=_fake_compute)      # 栅栏前置：先各出过 1D
            w.heat_btn.click()
            self.assertTrue(self._wait_heat(w))
            ax = gui_panel_state._content(self._heat_dock(w)).axes_heat
            self.assertEqual(ax.images[0].get_cmap().name, "magma")
            combo = w.params["热图色图"]
            combo.setCurrentIndex(combo.findData("viridis"))
            QApplication.processEvents()
            self.assertEqual(ax.images[0].get_cmap().name, "viridis",
                             "换色图该立刻重画")
            w.params["热图对数"].setChecked(True)
            QApplication.processEvents()
            self.assertIsInstance(ax.images[0].norm, LogNorm,
                                  "开对数强度该立刻重画")
            norm = w.params["热图归一化"]
            norm.setCurrentIndex(norm.findData("global"))
            QApplication.processEvents()
            self.assertAlmostEqual(float(ax.images[0].get_array().max()), 1.0,
                                   places=6, msg="全图归一化该立刻重画")
        finally:
            w.close()

    def test_heatmap_view_boxes_crop_without_recompute(self):
        """「视图 2θ」框（热图那对，2026-10-08 晚拆出的专用键）：填数 →
        立刻裁剪重画（重看，不是重算）。

        用户 2026-10-05："热图添加回 2theta 改变视图的功能，就是重看，
        不是重算"。框绑缩放/平移写回的那对键，双向：填数 → 裁剪重画 +
        写回快照；图上缩放 → 实时写回框。
        """
        w = create_window()
        try:
            _prime_1d(w, ["data/fake_a.tif", "data/fake_b.tif"],
                      stub=_fake_compute)      # 栅栏前置：先各出过 1D
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute) as c:
                w.heat_btn.click()
                self.assertTrue(self._wait_heat(w))
                calls = c.call_count
            ax = gui_panel_state._content(self._heat_dock(w)).axes_heat
            self.assertAlmostEqual(ax.get_xlim()[0], 0.5, places=2)  # 初始 = 数据范围
            w.params["热图视图 2θ 下限 (°)"].setValue(2.0)
            w.params["热图视图 2θ 上限 (°)"].setValue(4.0)
            QApplication.processEvents()
            self.assertAlmostEqual(ax.get_xlim()[0], 2.0, places=2)
            self.assertAlmostEqual(ax.get_xlim()[1], 4.0, places=2)
            self.assertEqual(c.call_count, calls, "只换窗口，不许重新积分")
            snap = self._heat_dock(w).params_snapshot
            self.assertAlmostEqual(snap["热图视图 2θ 下限 (°)"], 2.0, places=1)
            self.assertAlmostEqual(snap["热图视图 2θ 上限 (°)"], 4.0, places=1)
            # 反方向：图上的缩放实时写回这两个框（热图是焦点面板）
            ax.set_xlim(1.5, 3.5)
            QApplication.processEvents()
            self.assertAlmostEqual(
                w.params["热图视图 2θ 下限 (°)"].value(), 1.5, places=1)
            self.assertAlmostEqual(
                w.params["热图视图 2θ 上限 (°)"].value(), 3.5, places=1)
        finally:
            w.close()

    def test_view_boxes_split_between_compare_and_heatmap(self):
        """拆键（2026-10-08 晚用户："对比与热图分开管理"）：两对框各管各的。

        「对比」小节那对（老键）只裁对比面板；「热图显示」那对（新键
        "热图视图 2θ …"）只裁热图面板——改一边不再动两边。
        """
        w = create_window()
        try:
            _prime_1d(w, ["data/fake_a.tif", "data/fake_b.tif"],
                      stub=_fake_compute)      # 栅栏前置：先各出过 1D
            w.compare_btn.click()
            keys = [k for k in w.plot_docks if k.startswith("对比")]
            self.assertEqual(len(keys), 1)
            cmp_ax = gui_panel_state._content(
                w.plot_docks[keys[0]]).axes_1d
            self.assertTrue(_wait_until(lambda: len(cmp_ax.lines) >= 2))
            w.heat_btn.click()
            self.assertTrue(self._wait_heat(w))
            heat_dock = self._heat_dock(w)
            heat_ax = gui_panel_state._content(heat_dock).axes_heat
            x0_heat = tuple(heat_ax.get_xlim())
            # ① 老键那对（对比）：只裁对比，热图纹丝不动
            w.params["视图 2θ 下限 (°)"].setValue(2.0)
            w.params["视图 2θ 上限 (°)"].setValue(4.0)
            QApplication.processEvents()
            self.assertAlmostEqual(cmp_ax.get_xlim()[0], 2.0, places=2)
            self.assertAlmostEqual(cmp_ax.get_xlim()[1], 4.0, places=2)
            self.assertAlmostEqual(heat_ax.get_xlim()[0], x0_heat[0], places=4,
                                   msg="对比那对框不许动热图")
            # ② 新键那对（热图）：只裁热图，对比保持 ① 的窗口
            w.params["热图视图 2θ 下限 (°)"].setValue(3.0)
            w.params["热图视图 2θ 上限 (°)"].setValue(5.0)
            QApplication.processEvents()
            self.assertAlmostEqual(heat_ax.get_xlim()[0], 3.0, places=2)
            self.assertAlmostEqual(heat_ax.get_xlim()[1], 5.0, places=2)
            self.assertAlmostEqual(cmp_ax.get_xlim()[0], 2.0, places=2,
                                   msg="热图那对框不许动对比")
            # 快照各写各的键：热图不沾老键、对比不沾新键
            hsnap = heat_dock.params_snapshot
            self.assertAlmostEqual(hsnap["热图视图 2θ 下限 (°)"], 3.0, places=1)
            self.assertIsNone(hsnap.get("视图 2θ 下限 (°)"),
                              "热图的窗口不该再写进老键")
            csnap = w.plot_docks[keys[0]].params_snapshot
            self.assertAlmostEqual(csnap["视图 2θ 下限 (°)"], 2.0, places=1)
            self.assertIsNone(csnap.get("热图视图 2θ 下限 (°)"),
                              "对比的窗口不该沾新键")
        finally:
            w.close()

    def test_row_names_default_off_and_toggle_live(self):
        """热图行名默认关、由「热图显示」小节自己的 [显示数据名] 即改即画。

        2026-10-12 用户拍板"甲，拆开"（原话："对比热图现在共用一个显示
        数据名，但是是在对比的板块"）：热图行名不再挂「对比显示」小节
        那把——两把互不影响（那把 = 平铺图例/堆叠纵轴名 + 瀑布 χ）。
        """
        w = create_window()
        try:
            _prime_1d(w, ["data/fake_a.tif", "data/fake_b.tif"],
                      stub=_fake_compute)      # 栅栏前置：先各出过 1D
            w.heat_btn.click()
            self.assertTrue(self._wait_heat(w))
            ax = gui_panel_state._content(self._heat_dock(w)).axes_heat
            self.assertEqual([t.get_text() for t in ax.get_yticklabels()], [],
                             "行名默认关")
            w.params["显示数据名"].setChecked(True)   # 对比那把：管不到热图
            QApplication.processEvents()
            self.assertEqual([t.get_text() for t in ax.get_yticklabels()], [],
                             "「对比显示」那把不参与热图行名（甲：拆开）")
            w.params["热图显示数据名"].setChecked(True)   # 即改即画
            QApplication.processEvents()
            self.assertEqual([t.get_text() for t in ax.get_yticklabels()],
                             ["fake_a", "fake_b"], "勾上 → 短名出现")
            w.params["热图显示数据名"].setChecked(False)
            QApplication.processEvents()
            self.assertEqual([t.get_text() for t in ax.get_yticklabels()], [],
                             "取消 → 名字收回")
            w.params["显示数据名"].setChecked(False)   # 还原对比那把
        finally:
            w.close()

    def test_heatmap_uses_cached_1d_and_draws(self):
        """1D 已算好 → [热图] 零后台任务直接出图（复用面板缓存）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute) as c:
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: all(
                        getattr(_dock(w, "1D", p), "last_tth", None)
                        is not None
                        for p in ("data/fake_a.tif", "data/fake_b.tif"))))
                calls = c.call_count
                w.heat_btn.click()
                self.assertTrue(self._wait_heat(w))
            self.assertEqual(c.call_count, calls)   # 复用缓存：零新任务
            log = w.log_text.toPlainText()
            self.assertIn("开始热图：2 个文件（只拼已经算好的曲线）", log)
            self.assertIn("热图完成：2 个文件 × 3 点", log)
            # 图真的画上去了：imshow + 颜色条 + 行标签 = 文件名
            # （行名默认关、由「热图显示」小节自己的 [显示数据名] 管——
            # 2026-10-12 从对比那把拆出；这里勾上才读得到短名，"默认关"
            # 本身由专门测试钉）
            dock = self._heat_dock(w)
            ax = gui_panel_state._content(dock).axes_heat
            self.assertEqual(len(ax.images), 1)
            self.assertIsNotNone(dock._heat_colorbar)
            w.params["热图显示数据名"].setChecked(True)
            QApplication.processEvents()
            self.assertEqual([t.get_text() for t in ax.get_yticklabels()],
                             ["fake_a", "fake_b"])
            self.assertTrue(w.focus_panel.startswith("热图"))
        finally:
            w.close()

    def test_heatmap_refuses_raw_without_1d(self):
        """栅栏（用户 2026-10-16："原始数据不允许直接进行对比，必须先去
        1d 积分，日志里记得写"）：没出过 1D 的原始数据 → 热图整批不出图、
        不现场积分、不开面板；日志点名 + 指路。"""
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration") as compute:
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                w.heat_btn.click()
                QApplication.processEvents()
                log = w.log_text.toPlainText()
                self.assertIn("还没有 1D 曲线", log)
                self.assertIn("先点 [1D] 出图，再来出热图", log)
                compute.assert_not_called()
            self.assertFalse([k for k in w.plot_docks if k.startswith("热图")],
                             "拒绝时不开面板")
        finally:
            w.close()

    def test_heatmap_apply_skips_a_curve_that_went_missing(self):
        """直进 runner 的兜底（数据 [应用]，同对比那条）：曲线取不到了
        → 逐条跳过并写明，其余照样成图。"""
        w = create_window()
        try:
            _prime_1d(w, ["data/fake_a.tif", "data/fake_b.tif"],
                      stub=_fake_compute)
            w.heat_btn.click()
            self.assertTrue(self._wait_heat(w))
            gui_panels._close_panel(w, "1D|data/fake_a.tif")   # 唯一来源关掉
            w.findChild(QPushButton, "apply_btn").click()
            QApplication.processEvents()
            log = w.log_text.toPlainText()
            self.assertIn("热图：fake_a.tif 还没有 1D 曲线（先点 [1D] 出图）"
                          "——跳过", log)
            self.assertIn("热图完成：1 个文件 × 3 点", log)
        finally:
            w.close()

    def test_heatmap_single_file_hint(self):
        w = create_window()
        try:
            add_checked(w, ["data/fake_a.tif"])
            w.heat_btn.click()
            self.assertIn("热图至少勾选两个文件", w.log_text.toPlainText())
        finally:
            w.close()

    def test_heatmap_error_still_draws_others(self):
        """一个来源读不到（那一份产物被删了）：逐条跳过，其余照样成图。

        栅栏（2026-10-16）之后失败只剩这一类：勾的是产物条目、而那一份
        已经不在盘上（老路"某个文件积分炸了"随现场积分一起退休）。
        """
        w = create_window()
        try:
            files = _tmp_files(2)
            keys = [_store_product(w, p) for p in files]
            add_checked(w, [str(p) for p in files])
            w.refresh_groups()
            w.file_list.raw_group.setCheckState(0, Qt.Unchecked)
            grp = _group_by_text(w, "1D 产物")
            for i in range(grp.childCount()):
                grp.child(i).setCheckState(0, Qt.Checked)
            QApplication.processEvents()
            stage_cache.drop_keys("1d", [keys[0]])   # 第一份被删
            w.heat_btn.click()
            self.assertTrue(self._wait_heat(w))
            log = w.log_text.toPlainText()
            self.assertIn("产物不存在", log)
            self.assertIn("跳过", log)
            self.assertIn("热图完成：1 个文件 × 3 点", log)
        finally:
            w.close()

    def test_heatmap_mismatched_grids_logs_interp_note(self):
        """各文件网格不一致 → 出图 + 日志提示重插值。"""
        def grids(path_str, geom, npt):
            if path_str.endswith("fake_a.tif"):
                return np.array([0.5, 1.0, 8.5]), np.array([1.0, 2.0, 3.0])
            return np.array([0.5, 8.5]), np.array([1.0, 3.0])

        w = create_window()
        try:
            _prime_1d(w, ["data/fake_a.tif", "data/fake_b.tif"],
                      stub=grids)              # 栅栏前置：先各出过 1D
            w.heat_btn.click()
            self.assertTrue(self._wait_heat(w))
            log = w.log_text.toPlainText()
            self.assertIn("各文件 2θ 网格不一致，已统一到"
                          "第一个文件的 2θ 网格", log)
            dock = self._heat_dock(w)
            self.assertEqual(dock.heat_data[1].shape, (2, 3))
        finally:
            w.close()

    def test_heatmap_image_apply_redraws_with_new_colormap(self):
        """改色图 → 即改即画重画（不重新积分；2026-10-08 起无 [应用] 按钮）。"""
        w = create_window()
        try:
            _prime_1d(w, ["data/fake_a.tif", "data/fake_b.tif"],
                      stub=_fake_compute)      # 栅栏前置：先各出过 1D
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute) as c:
                w.heat_btn.click()
                self.assertTrue(self._wait_heat(w))
                calls = c.call_count
            dock = self._heat_dock(w)
            ax = gui_panel_state._content(dock).axes_heat
            self.assertEqual(ax.images[0].get_cmap().name, "magma")
            w.params["热图色图"].setCurrentIndex(
                w.params["热图色图"].findData("viridis"))
            QApplication.processEvents()
            self.assertEqual(ax.images[0].get_cmap().name, "viridis")
            self.assertEqual(c.call_count, calls)   # 只重画不重算
        finally:
            w.close()

    def test_heatmap_repeated_apply_does_not_shrink_axes(self):
        """反复即改即画重画：主坐标轴宽度不缩（教训 13——
        remove+重建颜色条每次让 20% 宽度且不退还；颜色条改为
        update_normal 复用后几何只算一次）。"""
        w = create_window()
        try:
            _prime_1d(w, ["data/fake_a.tif", "data/fake_b.tif"],
                      stub=_fake_compute)      # 栅栏前置：先各出过 1D
            w.heat_btn.click()
            self.assertTrue(self._wait_heat(w))
            dock = self._heat_dock(w)
            ax = gui_panel_state._content(dock).axes_heat
            widths = []
            for _ in range(3):
                gui_plot_compare._refresh_heat(w)   # 即改即画同款重画
                widths.append(float(ax.get_position().width))
            self.assertLess(max(widths) - min(widths), 1e-3,
                            f"热图宽度在反复重画后缩小：{widths}")
        finally:
            w.close()

    def test_heatmap_data_apply_rereads_from_cache(self):
        """数据 [应用]：不重积分（栅栏 2026-10-16）——按已经算好的曲线
        重读重画一遍（老语义"改数据参数 → 全部重新积分"随现场积分退休）。"""
        w = create_window()
        try:
            _prime_1d(w, ["data/fake_a.tif", "data/fake_b.tif"],
                      stub=_fake_compute)      # 栅栏前置：先各出过 1D
            w.heat_btn.click()
            self.assertTrue(self._wait_heat(w))
            with mock.patch.object(gui_plot_spawn, "_compute_integration") as c:
                w.findChild(QPushButton, "apply_btn").click()
                QApplication.processEvents()
                c.assert_not_called()
            self.assertGreaterEqual(
                w.log_text.toPlainText().count("热图完成：2 个文件 × 3 点"), 2)
        finally:
            w.close()


class Test2DColorbar(unittest.TestCase):
    """2D 视图带颜色条（作业规格"带颜色条"）：每画一次恰好一条，
    颜色条只建一次、重画 update_normal 复用（教训 13）。"""

    def test_2d_has_single_colorbar_and_redraw_does_not_stack(self):
        w = create_window()
        try:
            with mock.patch.object(gui_plot_workers, "load_diffraction_image",
                                   return_value=np.ones((64, 64)) * 5.0):
                add_checked(w, ["data/fake_a.tif"])
                _open_view(w, "2D")
                self.assertTrue(_wait_until(
                    lambda: getattr(_dock(w, "2D", "data/fake_a.tif"),
                                    "last_image", None) is not None))
            dock = _dock(w, "2D", "data/fake_a.tif")
            fig = gui_panel_state._content(dock).figure
            self.assertEqual(len(fig.axes), 2)   # 图像轴 + 颜色条轴
            # 即改即画重画 → 颜色条仍恰好一条
            gui_views._refresh_image_display(w)
            self.assertEqual(len(fig.axes), 2)
            self.assertIsNotNone(dock._colorbar_2d)
        finally:
            w.close()

    def test_2d_repeated_apply_does_not_shrink_axes(self):
        """反复即改即画重画：主坐标轴宽度不缩（与热图同病，
        教训 13——remove+重建颜色条每次让 20% 宽度且不退还）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_plot_workers, "load_diffraction_image",
                                   return_value=np.ones((64, 64)) * 5.0):
                add_checked(w, ["data/fake_a.tif"])
                _open_view(w, "2D")
                self.assertTrue(_wait_until(
                    lambda: getattr(_dock(w, "2D", "data/fake_a.tif"),
                                    "last_image", None) is not None))
            dock = _dock(w, "2D", "data/fake_a.tif")
            ax = gui_panel_state._content(dock).axes_2d
            widths = []
            for _ in range(3):
                gui_views._refresh_image_display(w)   # 即改即画同款重画
                widths.append(float(ax.get_position().width))
            self.assertLess(max(widths) - min(widths), 1e-3,
                            f"2D 宽度在反复重画后缩小：{widths}")
        finally:
            w.close()


class TestBackgroundSubtraction(unittest.TestCase):
    """任务六·背景扣除：三种模式 + 实时预览 + 辅助线隔离。

    用 _fake_bg_compute（200 点陡升背景 + 尖峰）——3 点的玩具曲线下
    基线算法会退化，窗口参数也失去分辨力。
    """

    PATH = "data/fake_bg.tif"
    KEY = "1D|data/fake_bg.tif"

    def _open_1d(self, w):
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_bg_compute):
            add_checked(w, [self.PATH])
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, "1D", self.PATH).lines) > 0))
        return _dock(w, "1D", self.PATH)

    def _set_mode(self, w, mode):
        cb = w.params["背景扣除模式"]
        cb.setCurrentIndex(cb.findData(mode))

    def _anchors_of(self, w, dock):
        """当前作用文件的锚点列表（键 = 面板文件的路径字符串）。"""
        return w.bg_anchors.get(str(gui_views._bg_path_of(dock)), [])

    def _click_anchor(self, w, ax, x, y=None):
        """在一个锚点位置按一下再松手（像素不位移 = 点击）。

        y 省略 = 点在屏幕上那条曲线（原始数据）上；要删已有关键点时得
        点在**标记**上——扣完背景后曲线已经落到 0 附近，而锚点标记画在
        原始尺度上，两者不是一个位置。
        """
        press, release = _bg_click(ax, x, y=y)
        gui_plot_compare._anchor_press(w, self.KEY, press)
        gui_plot_compare._anchor_release(w, self.KEY, release)

    def test_off_by_default_draws_single_line(self):
        """默认关闭 = 零行为变化：1D 仍只画一条曲线，没有辅助线。

        这是整个功能的回归护栏——不扣背景时画布必须和从前一模一样。
        """
        w = create_window()
        try:
            self._open_1d(w)
            ax = _axes(w, "1D", self.PATH)
            data, aux = _bg_lines(ax)
            self.assertEqual(len(data), 1)
            self.assertEqual(aux, [], "关闭模式不该画任何辅助线")
            self.assertEqual(w.params["背景扣除模式"].currentData(), "off")
        finally:
            w.close()

    def test_auto_mode_draws_curve_baseline_and_raw(self):
        """自动模式：扣除后曲线 + 基线（点线）+ 原始曲线（虚线），
        三条线都带 bg: gid（辅助线才不会被快照/悬停当成曲线）。"""
        w = create_window()
        try:
            self._open_1d(w)
            self._set_mode(w, "auto")
            ax = _axes(w, "1D", self.PATH)
            data, aux = _bg_lines(ax)
            self.assertEqual(len(data), 1, "数据曲线仍只有一条")
            self.assertEqual(len(aux), 2, "基线 + 原始曲线")
            self.assertEqual(sorted(ln.get_gid() for ln in aux),
                             ["bg:baseline", "bg:raw"])
            # 基线落在原始数据的量级内（既不是 0，也不高到追上峰顶）
            base = next(ln for ln in aux if ln.get_gid() == "bg:baseline")
            raw = next(ln for ln in aux if ln.get_gid() == "bg:raw")
            yb = np.asarray(base.get_ydata(), dtype=float)
            y_raw = np.asarray(raw.get_ydata(), dtype=float)
            self.assertGreater(float(yb.max()), 0.2 * float(y_raw.max()))
            self.assertLess(float(yb.max()), 0.9 * float(y_raw.max()))
            # 陡升段不该被削掉：基线在低角端应贴近背景量级（不该塌向 0）
            self.assertGreater(float(yb[0]), 0.5 * float(y_raw[0]))
            self.assertIn("背景扣除：自动基线", w.log_text.toPlainText())
        finally:
            w.close()

    def test_show_raw_off_hides_original_curve(self):
        """取消"显示原始曲线对比"只剩扣除后曲线 + 基线。"""
        w = create_window()
        try:
            self._open_1d(w)
            self._set_mode(w, "auto")
            w.params["背景显示原始"].setChecked(False)
            ax = _axes(w, "1D", self.PATH)
            _, aux = _bg_lines(ax)
            self.assertEqual([ln.get_gid() for ln in aux], ["bg:baseline"])
        finally:
            w.close()

    def test_live_preview_redraws_without_reintegrating(self):
        """实时预览：改窗口宽度立刻重画，但**不重新积分**（调用次数不变）。

        这是本功能唯一的"改控件即重画"通路，靠的是扣除发生在绘制层
        （缓存里的原始曲线不动）。
        """
        w = create_window()
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_bg_compute) as compute:
            try:
                dock = self._open_1d(w)
                self._set_mode(w, "auto")
                ax = _axes(w, "1D", self.PATH)
                before = np.array(_bg_lines(ax)[0][0].get_ydata())
                n_calls = compute.call_count
                w.params["背景窗口 (°)"].setValue(3.0)
                after = np.array(_bg_lines(ax)[0][0].get_ydata())
                self.assertFalse(np.allclose(before, after),
                                 "窗口一变，扣除后曲线应跟着变")
                self.assertEqual(compute.call_count, n_calls,
                                 "实时预览不该触发重新积分")
                self.assertIsNotNone(dock.last_tth)
            finally:
                w.close()

    def test_anchor_click_adds_and_removes(self):
        """锚点点选：点一下加一个（≤5 px 算点击），再点同处删掉。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            self._set_mode(w, "anchor")
            w.bg_pick_btn.setChecked(True)
            ax = _axes(w, "1D", self.PATH)
            self._click_anchor(w, ax, 1.0)
            anchors = self._anchors_of(w, dock)
            self.assertEqual(len(anchors), 1)
            # 吸附到最近的真实数据点（网格步长 0.04°，不是鼠标原始位置）
            self.assertLess(abs(anchors[0][0] - 1.0), 0.05)
            self.assertIn("加锚点", w.log_text.toPlainText())
            # 点回同一个锚点标记上 = 删除（按标记的像素位置点，而不是
            # 屏幕上那条已经扣到 0 附近的曲线）
            ax_, ay_ = anchors[0]
            self._click_anchor(w, ax, ax_, y=ay_)
            self.assertEqual(self._anchors_of(w, dock), [])
            self.assertIn("删除锚点", w.log_text.toPlainText())
        finally:
            w.close()

    def test_right_click_deletes_one_anchor(self):
        """右键点锚点 = 删掉那一个，**不需要** [拾取锚点]（用户 2026-10-02 第 2 条）。

        原先删单个锚点只有一条窄路：点亮 [拾取锚点] + 左键点圆圈（8 px 内）。
        右键在 1D 图上是空的，拿它当"删除"最顺；只删不加，所以不必再要求
        拾取开关——但要**看得见**才认（锚点圆圈画着，见 _anchors_visible）。
        """
        w = create_window()
        try:
            dock = self._open_1d(w)
            self._set_mode(w, "anchor")
            w.bg_pick_btn.setChecked(True)
            ax = _axes(w, "1D", self.PATH)
            self._click_anchor(w, ax, 1.0)
            self._click_anchor(w, ax, 5.0)
            anchors = self._anchors_of(w, dock)
            self.assertEqual(len(anchors), 2)
            w.bg_pick_btn.setChecked(False)      # 拾取关掉：右键照样能删
            # 注意先取值再删：`anchors` 就是 w.bg_anchors 里那个 list 本身，
            # 删完它自己也短了（第一版直接在删除后读 anchors[1] → IndexError）
            ax_, ay_ = anchors[0]
            kept_x = anchors[1][0]
            press, _ = _bg_click(ax, ax_, y=ay_)
            press.button = 3                     # 右键
            gui_plot_compare._anchor_right_click(w, self.KEY, press)
            left = self._anchors_of(w, dock)
            self.assertEqual(len(left), 1, "只删点到的那一个")
            self.assertAlmostEqual(left[0][0], kept_x, places=6)
            self.assertIn("删除锚点", w.log_text.toPlainText())
        finally:
            w.close()

    def test_right_click_ignores_an_invisible_anchor(self):
        """模式 = 关闭时圆圈没画着 → 右键不删：看不见的东西不该被点掉。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            self._set_mode(w, "anchor")
            w.bg_pick_btn.setChecked(True)
            ax = _axes(w, "1D", self.PATH)
            self._click_anchor(w, ax, 1.0)
            anchors = self._anchors_of(w, dock)
            self.assertEqual(len(anchors), 1)
            self._set_mode(w, "off")             # 圆圈不再画
            ax_, ay_ = anchors[0]
            press, _ = _bg_click(ax, ax_, y=ay_)
            press.button = 3
            gui_plot_compare._anchor_right_click(w, self.KEY, press)
            self.assertEqual(len(self._anchors_of(w, dock)), 1)
        finally:
            w.close()

    def test_anchor_click_through_canvas_wiring(self):
        """画布上真连的两根线要能解析到锚点处理（拆模块的回归护栏）。

        上面几条锚点测试直接调 _anchor_press/_anchor_release，绕过了
        _build_1d_widget 里 mpl_connect 的那两行 lambda。拆模块时 lambda
        引用的名字搬去了 plot_compare、面板壳这边忘了导入：直接调处理的
        测试全绿，用户真点画布却什么也不发生（回调里的 NameError 被
        matplotlib 打印到 stderr，不弹窗——探针实证）。这条从
        canvas.callbacks 发真 MouseEvent，走用户真实路径；两根线只要
        有一根断在 NameError 上，锚点就加不出来，本测试失败。
        """
        w = create_window()
        try:
            dock = self._open_1d(w)
            self._set_mode(w, "anchor")
            w.bg_pick_btn.setChecked(True)
            ax = _axes(w, "1D", self.PATH)
            canvas = gui_panel_state._content(dock).canvas
            # 真 MouseEvent 的 (x, y) = 显示像素；用 _bg_click 算好位置，
            # 按下/松手同一点 = 点击（不位移就不算平移）
            press, _ = _bg_click(ax, 1.0)
            for name in ("button_press_event", "button_release_event"):
                canvas.callbacks.process(
                    name, MouseEvent(name, canvas, press.x, press.y,
                                     button=1))
            self.assertEqual(len(self._anchors_of(w, dock)), 1)
            self.assertIn("加锚点", w.log_text.toPlainText())
        finally:
            w.close()

    def test_anchor_drag_is_not_a_click(self):
        """按下后拖走（>5 px）→ 平移手势，不加锚点。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            self._set_mode(w, "anchor")
            w.bg_pick_btn.setChecked(True)
            ax = _axes(w, "1D", self.PATH)
            press, release = _bg_click(ax, 1.0, drag=(150, 150))
            gui_plot_compare._anchor_press(w, self.KEY, press)
            gui_plot_compare._anchor_release(w, self.KEY, release)
            self.assertEqual(self._anchors_of(w, dock), [])
        finally:
            w.close()

    def test_anchor_needs_pick_toggle(self):
        """模式是锚点但没开 [拾取锚点] → 点图不出锚点（不误点）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            self._set_mode(w, "anchor")
            w.bg_pick_btn.setChecked(False)
            ax = _axes(w, "1D", self.PATH)
            self._click_anchor(w, ax, 1.0)
            self.assertEqual(self._anchors_of(w, dock), [])
        finally:
            w.close()

    def test_anchor_baseline_follows_points(self):
        """锚点基线过点：在两点上各打一锚，基线在这两点处**恰好等于**
        当时点到的曲线值（这是手动锚点的核心承诺：你点哪它走哪）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            self._set_mode(w, "anchor")
            w.bg_pick_btn.setChecked(True)
            ax = _axes(w, "1D", self.PATH)
            for x in (1.0, 8.0):
                self._click_anchor(w, ax, x)
            anchors = self._anchors_of(w, dock)
            self.assertEqual(len(anchors), 2)
            _, aux = _bg_lines(ax)
            base = next(ln for ln in aux if ln.get_gid() == "bg:baseline")
            self.assertEqual(len(base.get_xdata()), 200)
            for ax_, ay_ in anchors:
                self.assertAlmostEqual(
                    float(np.interp(ax_, base.get_xdata(), base.get_ydata())),
                    ay_, places=6, msg=f"基线应过锚点 2θ={ax_:.3f}°")
        finally:
            w.close()

    def test_auto_mode_also_draws_anchor_markers(self):
        """自动基线下点锚点 → 也要画小圆圈（用户 2026-09-27："圆圈没了"）。

        335db7b 让自动模式（"自动基线（推荐）"）也能拾取锚点、锚点也真的
        改基线电平（自动 + 锚点校正），画图那道门槛却还写着"只有手动锚点
        模式才画"——点下去界面毫无反馈。判据：auto 模式 + 有锚点 →
        辅助线里必须有 bg:anchor，且圆心正好落在锚点上。
        """
        w = create_window()
        try:
            dock = self._open_1d(w)
            self._set_mode(w, "auto")
            w.bg_pick_btn.setChecked(True)
            ax = _axes(w, "1D", self.PATH)
            for x in (2.0, 5.0):
                self._click_anchor(w, ax, x)
            anchors = self._anchors_of(w, dock)
            self.assertEqual(len(anchors), 2, "自动模式下锚点也该拾取")
            self.assertEqual(w.params["背景扣除模式"].currentData(), "auto")
            _, aux = _bg_lines(ax)
            marker = next(ln for ln in aux if ln.get_gid() == "bg:anchor")
            np.testing.assert_allclose(sorted(marker.get_xdata()),
                                       sorted(a[0] for a in anchors))
            np.testing.assert_allclose(sorted(marker.get_ydata()),
                                       sorted(a[1] for a in anchors))
            # 计数标签要带**覆盖范围**：锚点之外是推出来的，不让用户
            # 以为整条曲线都点过了
            self.assertIn("2 个锚点", w.bg_count_lbl.text())
            self.assertIn("覆盖", w.bg_count_lbl.text())
        finally:
            w.close()

    def test_anchor_mode_without_anchors_draws_nothing_extra(self):
        """锚点模式但一个锚点都没点 → 不扣也不画辅助线（不是画一条 0 基线）。"""
        w = create_window()
        try:
            self._open_1d(w)
            self._set_mode(w, "anchor")
            ax = _axes(w, "1D", self.PATH)
            data, aux = _bg_lines(ax)
            self.assertEqual(aux, [])
            np.testing.assert_allclose(
                np.asarray(data[0].get_ydata(), dtype=float),
                _fake_bg_compute("", {}, 0)[1])
        finally:
            w.close()

    def test_clear_anchors_button(self):
        """[清空锚点] 清掉当前 1D 面板对应文件的锚点。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            self._set_mode(w, "anchor")
            w.bg_pick_btn.setChecked(True)
            ax = _axes(w, "1D", self.PATH)
            self._click_anchor(w, ax, 1.0)
            self.assertEqual(len(self._anchors_of(w, dock)), 1)
            w.bg_clear_btn.click()
            self.assertEqual(self._anchors_of(w, dock), [])
            self.assertIn("已清空", w.log_text.toPlainText())
        finally:
            w.close()

    def test_anchors_are_per_file(self):
        """锚点按文件各记各的：另一个文件不会套用这份锚点。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            self._set_mode(w, "anchor")
            w.bg_pick_btn.setChecked(True)
            ax = _axes(w, "1D", self.PATH)
            self._click_anchor(w, ax, 1.0)
            self.assertEqual(len(self._anchors_of(w, dock)), 1)
            other = gui_state._bg_params(w, dock, "data/other.tif")
            self.assertEqual(other["anchors"], [])
        finally:
            w.close()

    def test_blank_without_image_warns_and_does_not_subtract(self):
        """选了空扫模式但还没挑空扫图 → 提示 + 不扣（不画辅助线）。"""
        w = create_window()
        try:
            self._open_1d(w)
            self._set_mode(w, "blank")
            ax = _axes(w, "1D", self.PATH)
            data, aux = _bg_lines(ax)
            self.assertEqual(aux, [])
            self.assertIn("还没选空扫图", w.log_text.toPlainText())
        finally:
            w.close()

    def test_blank_subtracts_after_pick(self):
        """挑好空扫图（缓存里放好）→ 切到空扫模式即按系数扣除。"""
        w = create_window()
        try:
            self._open_1d(w)
            tth = np.linspace(0.5, 8.5, 200)
            blank = 0.5 * (100.0 + 900.0 * np.exp(-tth / 2.0))
            w.bg_blank = {"path": "data/blank.tif", "tth": tth,
                          "intensity": blank, "geom": ""}
            self._set_mode(w, "blank")
            ax = _axes(w, "1D", self.PATH)
            data, aux = _bg_lines(ax)
            expect = _fake_bg_compute("", {}, 0)[1] - blank
            np.testing.assert_allclose(
                np.asarray(data[0].get_ydata(), dtype=float), expect)
            self.assertEqual(len(aux), 2, "基线 + 原始")
            # 归一化系数作为倍率：×2 之后扣掉的是两倍
            w.params["空扫归一化"].setValue(2.0)
            data, _ = _bg_lines(ax)
            np.testing.assert_allclose(
                np.asarray(data[0].get_ydata(), dtype=float),
                _fake_bg_compute("", {}, 0)[1] - 2.0 * blank)
        finally:
            w.close()

    def test_negative_values_kept_by_default_and_clipped_on_demand(self):
        """扣完的负值默认保留（噪声地板露出），勾上"负值截断为 0"才切零。"""
        w = create_window()
        try:
            self._open_1d(w)
            tth = np.linspace(0.5, 8.5, 200)
            # 空扫给得比样品还高 → 扣完处处为负
            w.bg_blank = {"path": "b.tif", "tth": tth,
                          "intensity": np.full(200, 5000.0), "geom": ""}
            self._set_mode(w, "blank")
            ax = _axes(w, "1D", self.PATH)
            data, _ = _bg_lines(ax)
            self.assertLess(float(np.asarray(data[0].get_ydata()).min()), -1.0)
            w.params["负值截断为 0"].setChecked(True)
            data, _ = _bg_lines(ax)
            self.assertGreaterEqual(
                float(np.asarray(data[0].get_ydata()).min()), 0.0)
        finally:
            w.close()

    def test_aux_lines_excluded_from_snapshot_and_hover(self):
        """辅助线不进快照、悬停不选它们（否则样式回填错位、悬停乱跳）。"""
        w = create_window()
        try:
            self._open_1d(w)
            self._set_mode(w, "auto")
            ax = _axes(w, "1D", self.PATH)
            _, _, _, _, old_lines = gui_plot_panels._snapshot_canvas(ax)
            self.assertEqual(len(old_lines), 1, "快照只该收数据曲线")
            # 悬停选线只认数据曲线；取点标记自己也是辅助线（不参与选线）
            gui_plot_panels._hover_motion(w, self.KEY, _hover_event(ax, 1.0))
            marker = w.plot_docks[self.KEY].hover_marker
            self.assertTrue(gui_plot_panels._is_aux_line(marker))
            self.assertTrue(marker.get_visible())
        finally:
            w.close()

    def test_style_restore_stays_aligned_with_aux_lines(self):
        """重画时旧样式按序套回数据曲线（辅助线不占序号，不错位）。"""
        w = create_window()
        try:
            self._open_1d(w)
            self._set_mode(w, "auto")
            ax = _axes(w, "1D", self.PATH)
            data_line = _bg_lines(ax)[0][0]
            data_line.set_linestyle("--")   # 假装用户在 Customize 里改过
            data_line.set_linewidth(2.5)
            gui_views._draw_1d(w, w.plot_docks[self.KEY],
                               w.plot_docks[self.KEY].last_tth,
                               w.plot_docks[self.KEY].last_intensity)
            data_line = _bg_lines(ax)[0][0]
            self.assertEqual(data_line.get_linestyle(), "--")
            self.assertEqual(data_line.get_linewidth(), 2.5)
        finally:
            w.close()

    def test_compare_curves_are_background_subtracted(self):
        """对比面板的每条曲线也扣背景（口径一致：同一份数据在不同
        面板里长得一样）。"""
        w = create_window()
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_bg_compute):
            try:
                _prime_1d(w, ["data/fake_a.tif", "data/fake_b.tif"],
                          stub=_fake_bg_compute)   # 栅栏前置（2026-10-16）
                gui_plot_compare._plot_compare(w)
                keys = [k for k in w.plot_docks if k.startswith("对比")]
                self.assertTrue(_wait_until(lambda: len(keys) == 1))
                key = keys[0]
                dock = w.plot_docks[key]
                self.assertTrue(_wait_until(
                    lambda: len(getattr(dock, "compare_data", {})) == 2))
                ax = gui_panel_state._content(dock).axes_1d
                before = np.asarray(_bg_lines(ax)[0][0].get_ydata(),
                                    dtype=float).copy()
                self._set_mode(w, "auto")
                # ax.clear() 会换掉线对象——重画后必须重新取线，否则拿到
                # 的是已从轴上摘下来的旧对象（数据永远是旧的）
                after = np.asarray(_bg_lines(ax)[0][0].get_ydata(),
                                   dtype=float)
                self.assertFalse(np.allclose(before, after),
                                 "对比曲线应跟着扣背景")
                self.assertLess(float(after.max()), float(before.max()))
            finally:
                w.close()

    def test_waterfall_uses_common_baseline(self):
        """瀑布用扇区均值的**共同**基线（不逐扇区各扣各的，否则抹平
        扇区之间的真实强度差——那是瀑布图存在的意义）。

        判据：扣完之后相邻扇区首点的**间距**必须和原始数据里的间距一致
        （各扇区减同一条基线 → 扇区间距不变）。若逐扇区各扣各的，间距会
        被各自的基线吃掉、变得不一致。
        """
        w = create_window()
        try:
            with mock.patch.object(gui_plot_runners, "_compute_waterfall",
                                   side_effect=_fake_waterfall_compute):
                add_checked(w, ["data/fake_w.tif"])
                _open_view(w, "瀑布")
                dock = _dock(w, "瀑布", "data/fake_w.tif")
                self.assertTrue(_wait_until(
                    lambda: getattr(dock, "last_waterfall", None) is not None))
                self._set_mode(w, "auto")
                _, i2d, _ = dock.last_waterfall
                # 直接验设计决定：_proc_curve 只被调用一次，且喂进去的是
                # **扇区均值**（共同基线）。逐扇区各扣各的会调用 4 次、
                # 每次喂一条扇区曲线——扇区之间的真实强度差就被抹平了
                with mock.patch.object(gui_plot_drawing, "_proc_curve",
                                       wraps=gui_panel_state._proc_curve) as spy:
                    gui_views._draw_waterfall(w, dock, *dock.last_waterfall)
                self.assertEqual(spy.call_count, 1, "应只估一条共同基线")
                fed = spy.call_args[0][4]
                np.testing.assert_allclose(
                    np.asarray(fed, dtype=float),
                    np.nanmean(np.asarray(i2d, dtype=float), axis=1),
                    err_msg="喂给基线估计的应是扇区均值")
                wax = gui_panel_state._content(dock).axes_waterfall
                self.assertEqual(len(_bg_lines(wax)[0]), 4, "四条扇区曲线")
        finally:
            w.close()

    def test_heat_matrix_subtracted_per_file(self):
        """热图逐行按各文件自己的参数扣背景。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            tth = np.linspace(0.5, 8.5, 200)
            dock.heat_files = [gui_sources.make_source(
                gui_views._bg_path_of(dock), "fake_bg.tif")]
            dock.heat_results = [("fake_bg", tth,
                                  _fake_bg_compute("", {}, 0)[1])]
            blank = 0.5 * (100.0 + 900.0 * np.exp(-tth / 2.0))
            w.bg_blank = {"path": "b.tif", "tth": tth,
                          "intensity": blank, "geom": ""}
            self._set_mode(w, "blank")
            data = gui_plot_compare._heat_data(w, dock)
            self.assertIsNotNone(data)
            np.testing.assert_allclose(
                data[1][0], _fake_bg_compute("", {}, 0)[1] - blank)
        finally:
            w.close()

    def test_anchor_takes_raw_value_even_with_raw_overlay_off(self):
        """回归：关掉"显示原始曲线对比"后点锚点，锚点 y 仍须取自**原始**
        曲线。

        那时轴上没有 bg:raw 辅助线，若照屏幕上那条扣过的曲线取值，在已有
        关键点处会记下负值/0（探针实测中间锚点记成 −476.9）→ 该处背景等于
        没扣，而画面上看不出错；锚点还会持久化，重新勾上原始叠加也不自愈。
        """
        w = create_window()
        try:
            dock = self._open_1d(w)
            self._set_mode(w, "anchor")
            w.bg_pick_btn.setChecked(True)
            w.params["背景显示原始"].setChecked(False)   # 关键：关掉原始叠加
            ax = _axes(w, "1D", self.PATH)
            for x in (1.0, 5.0, 8.0):
                self._click_anchor(w, ax, x)
            anchors = self._anchors_of(w, dock)
            self.assertEqual(len(anchors), 3)
            raw = np.asarray(dock.last_intensity, dtype=float)
            tth = dock.last_tth
            for x, y in anchors:
                self.assertAlmostEqual(y, float(np.interp(x, tth, raw)),
                                       places=6,
                                       msg=f"锚点 {x:.3f}° 的 y 应是原始曲线值")
            self.assertTrue(all(y > 0 for _, y in anchors),
                            "锚点不该被记成 0/负值")
        finally:
            w.close()

    def test_switching_focus_does_not_mix_panel_snapshots(self):
        """回归：反复切焦点后，两块面板的显示参数快照仍各是各的。

        背景扣除控件连着 _refresh_proc（实时预览），而 _set_focus 会回放面板
        快照进控件 → 不挂回放旗标的话，回放途中的 setValue 会触发
        _refresh_proc 把"回放了一半的控件值"写进本面板快照，把上一块面板的
        显示参数（实测是 热图色图 等注册在背景组之后的几项）串过来。
        """
        w = create_window()
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_bg_compute):
            try:
                add_checked(w, ["data/fg_a.tif", "data/fg_b.tif"])
                _open_view(w, "1D")
                keys = [k for k in w.plot_docks if k.startswith("1D|")]
                self.assertTrue(_wait_until(
                    lambda: all(getattr(w.plot_docks[k], "last_tth", None)
                                is not None for k in keys), 20000))
                ka = next(k for k in keys if "fg_a" in k)
                kb = next(k for k in keys if "fg_b" in k)
                da, db = w.plot_docks[ka], w.plot_docks[kb]
                gui_state._set_focus(w, ka, da.panel_display)
                hc = w.params["热图色图"]
                hc.setCurrentIndex(hc.findData("viridis"))
                da.params_snapshot["热图色图"] = "viridis"   # A 自己的设置
                self._set_mode(w, "auto")                    # 背景组也动一下
                db.params_snapshot["热图色图"] = "magma"     # B 自己的设置
                gui_state._set_focus(w, kb, db.panel_display)
                gui_state._set_focus(w, ka, da.panel_display)
                self.assertEqual(da.params_snapshot.get("热图色图"), "viridis")
                self.assertEqual(db.params_snapshot.get("热图色图"), "magma")
            finally:
                w.close()

    def test_dragging_on_another_panel_keeps_pick_armed(self):
        """回归：在别的 1D 面板上做一次**拖拽平移**，不该把"拾取锚点"弄丢。

        按下时若就切焦点，会回放那块面板的快照（模式多半是"关闭"）→
        _sync_bg_rows 顺手把拾取开关取消，用户只是在别的图上拖了一下就
        失去了拾取状态。切焦点挪到确认是点击之后，拖拽就不影响。
        """
        w = create_window()
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_bg_compute):
            try:
                add_checked(w, ["data/fg_a.tif", "data/fg_b.tif"])
                _open_view(w, "1D")
                keys = [k for k in w.plot_docks if k.startswith("1D|")]
                self.assertTrue(_wait_until(
                    lambda: all(getattr(w.plot_docks[k], "last_tth", None)
                                is not None for k in keys), 20000))
                ka = next(k for k in keys if "fg_a" in k)
                kb = next(k for k in keys if "fg_b" in k)
                gui_state._set_focus(w, ka, w.plot_docks[ka].panel_display)
                self._set_mode(w, "anchor")
                w.bg_pick_btn.setChecked(True)
                # 在 B 面板上按下并拖走（>5 px = 平移手势，不是点击）
                axb = _axes(w, "1D", "data/fg_b.tif")
                press, release = _bg_click(axb, 1.0, drag=(150, 150))
                gui_plot_compare._anchor_press(w, kb, press)
                gui_plot_compare._anchor_release(w, kb, release)
                self.assertTrue(w.bg_pick_btn.isChecked(),
                                "拖拽平移不该取消拾取状态")
            finally:
                w.close()

    def test_blank_partial_coverage_warns(self):
        """空扫只覆盖部分 2θ 区间时提示（未覆盖段不扣，静默会让人以为坏了）。"""
        w = create_window()
        try:
            self._open_1d(w)
            tth = np.linspace(0.5, 8.5, 200)
            part = np.linspace(2.0, 6.0, 80)
            w.bg_blank = {"path": "b.tif", "tth": part,
                          "intensity": np.full(80, 5.0), "geom_sig": None}
            self._set_mode(w, "blank")
            self.assertIn("空扫图只覆盖", w.log_text.toPlainText())
            # 覆盖齐全时不提示
            w2 = create_window()
            try:
                with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                       side_effect=_fake_bg_compute):
                    add_checked(w2, [self.PATH])
                    _open_view(w2, "1D")
                    self.assertTrue(_wait_until(
                        lambda: getattr(w2.plot_docks.get(self.KEY), "last_tth",
                                        None) is not None))
                d2 = w2.plot_docks[self.KEY]
                gui_state._set_focus(w2, self.KEY, d2.panel_display)
                tth2 = d2.last_tth
                w2.bg_blank = {"path": "b.tif", "tth": tth2,
                               "intensity": np.full(len(tth2), 5.0),
                               "geom_sig": None}
                self._set_mode(w2, "blank")
                self.assertNotIn("空扫图只覆盖", w2.log_text.toPlainText())
            finally:
                w2.close()
        finally:
            w.close()

    def test_blank_geometry_mismatch_warns_once(self):
        """空扫图与当前几何不一致时提示（线性前提被破坏），且只提示一次
        ——本函数每次重画都会跑，每次记日志会刷屏。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            tth = np.linspace(0.5, 8.5, 200)
            blank = 0.5 * (100.0 + 900.0 * np.exp(-tth / 2.0))
            geom = gui_panel_state._collect_geometry(w)
            w.bg_blank = {"path": "b.tif", "tth": tth, "intensity": blank,
                          "geom_sig": gui_panel_state._bg_geom_sig(geom)}
            self._set_mode(w, "blank")
            self.assertNotIn("空扫图与当前几何不一致", w.log_text.toPlainText())
            # 改一个几何量 → 再扣就该提示。几何只认配置条目（分析页
            # 的字段是只读显示），所以改配置、不是改面板控件。
            w.config = dict(w.config, geometry=dict(
                w.config["geometry"],
                dist_m=w.config["geometry"]["dist_m"] + 0.05))
            self._set_mode(w, "auto")
            self._set_mode(w, "blank")
            text = w.log_text.toPlainText()
            self.assertIn("空扫图与当前几何不一致", text)
            n_once = text.count("空扫图与当前几何不一致")
            self._set_mode(w, "auto")
            self._set_mode(w, "blank")
            self.assertEqual(w.log_text.toPlainText().count(
                "空扫图与当前几何不一致"), n_once, "同一状态下只该提示一次")
        finally:
            w.close()

    def test_anchors_survive_panel_pop_out(self):
        """锚点/空扫存在 window 上（不是 dock），面板弹出/收回不丢——
        panels.py 的 _PANEL_ATTRS 白名单只搬少量 dock 属性，新加的会丢。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            self._set_mode(w, "anchor")
            w.bg_pick_btn.setChecked(True)
            ax = _axes(w, "1D", self.PATH)
            self._click_anchor(w, ax, 1.0)
            self.assertEqual(len(self._anchors_of(w, dock)), 1)
            gui_panels._toggle_pop_out(w, self.KEY)
            QApplication.processEvents()
            new_dock = _dock(w, "1D", self.PATH)
            self.assertEqual(len(self._anchors_of(w, new_dock)), 1,
                             "弹出后锚点还在")
            gui_panels._toggle_pop_out(w, self.KEY)
            QApplication.processEvents()
            self.assertEqual(len(self._anchors_of(
                w, _dock(w, "1D", self.PATH))), 1, "收回后锚点还在")
        finally:
            w.close()

    def test_mode_switch_keeps_dock_narrow(self):
        """每种模式只放出一行专用控件，且坞最小宽始终 ≤ 320。"""
        w = create_window()
        try:
            for mode in ("off", "blank", "auto", "anchor"):
                self._set_mode(w, mode)
                vis = [n for n, r in w.bg_rows.items() if not r.isHidden()]
                # auto 放两行：窗口 + 锚点（自动 + 锚点校正要用锚点那行）
                want = 0 if mode == "off" else (2 if mode == "auto" else 1)
                self.assertEqual(len(vis), want,
                                 f"{mode} 放出的行数不对，实际 {vis}")
                self.assertLess(w.param_dock.minimumWidth(), 320)
        finally:
            w.close()

    def test_reset_defaults_turns_background_off(self):
        """[恢复默认] 把背景扣除复位（锚点/空扫属于用户挑的数据，不代删）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            self._set_mode(w, "auto")
            w.params["背景窗口 (°)"].setValue(4.0)
            w.findChild(QPushButton, "reset_image_btn").click()
            self.assertEqual(w.params["背景扣除模式"].currentData(), "off")
            # 窗口默认跟着引擎默认走（2026-09-26 从 1.0° → 0.5°、09-27 → 0.3°、
            # 09-28 用户："默认值 0.3 改成 0.2"）。这里断的是**同一个常量**：
            # 恢复默认、面板默认、控件初始值三处都读它，别再各写一个数
            from xrd_toolkit.services.background import AUTO_WINDOW_DEG
            self.assertEqual(w.params["背景窗口 (°)"].value(),
                             AUTO_WINDOW_DEG)
            self.assertEqual(AUTO_WINDOW_DEG, 0.2)
            ax = _axes(w, "1D", self.PATH)
            self.assertEqual(_bg_lines(ax)[1], [])
            self.assertIsNotNone(getattr(dock, "last_tth", None))
        finally:
            w.close()

    def test_export_bg_option_subtracts(self):
        """[导出数据] 勾"导出处理后的曲线"→ 取到的是扣完的值。"""
        w = create_window()
        try:
            self._open_1d(w)
            self._set_mode(w, "auto")
            plain = gui_export._checked_1d_results(w, want_bg=False)
            with_bg = gui_export._checked_1d_results(w, want_bg=True)
            self.assertEqual(len(plain), 1)
            raw = np.asarray(plain[0][2], dtype=float)
            sub = np.asarray(with_bg[0][2], dtype=float)
            np.testing.assert_allclose(raw, _fake_bg_compute("", {}, 0)[1])
            self.assertFalse(np.allclose(sub, raw),
                             "勾了扣背景，导出的不该还是原始值")
            self.assertLess(float(sub.min()), 0.0)   # 负值保留
        finally:
            w.close()

    def test_export_dialog_has_bg_checkbox(self):
        """导出弹窗里有"导出处理后的曲线"复选项（默认不勾；名字写全三项）。"""
        w = create_window()
        try:
            captured = {}

            def fake_exec(self):
                captured["bg"] = self.findChild(
                    QCheckBox, "export_bg_check").isChecked()
                captured["check"] = self.findChild(QCheckBox, "export_bg_check")
                return QDialog.Rejected

            with mock.patch.object(QDialog, "exec", new=fake_exec):
                self.assertIsNone(gui_export._build_export_dialog(w, 1))
            self.assertIsNotNone(captured.get("check"))
            self.assertFalse(captured["bg"], "默认不勾 = 导出原始曲线")
            text = captured["check"].text()
            # 链跑的是背景 + 平滑 + 裁剪，名字不许只写"扣背景"——开着
            # 平滑的人会以为拿到的是没平滑过的曲线（2026-10-03 定）
            self.assertIn("背景", text)
            self.assertIn("平滑", text)
            self.assertIn("裁剪", text)
        finally:
            w.close()

    def test_export_bg_requested_but_no_anchors_stays_raw(self):
        """「手动锚点」没点锚点时勾"导出处理后的曲线"：没动过的照实写 Raw。

        回归护栏（2026-10-03）：判"处理过没有"按链**实际做了的事**——
        锚点 0 个时 apply_chain 原样返回，但设置里模式还写着 anchor；
        拿设置当判据会把原始数据标成 Processed（谎报）。
        """
        w = create_window()
        try:
            self._open_1d(w)
            self._set_mode(w, "anchor")
            got = gui_export._checked_1d_results(w, want_bg=True)
            self.assertEqual(len(got), 1)
            stem, _, intensity, chain, category, _, _mask = got[0]
            np.testing.assert_allclose(np.asarray(intensity, dtype=float),
                                       _fake_bg_compute("", {}, 0)[1])
            self.assertEqual(chain, "none", "没做事就不许写 bg=")
            self.assertEqual(category, "Raw")
            self.assertNotIn("_处理产物", stem)
            self.assertIn("没扣背景", w.log_text.toPlainText())
        finally:
            w.close()

    def test_export_all_off_stays_raw_and_says_so(self):
        """三项全关时勾"导出处理后的曲线"：照实写 Raw，日志里说一声。"""
        w = create_window()
        try:
            self._open_1d(w)
            got = gui_export._checked_1d_results(w, want_bg=True)
            stem, _, _, chain, category, _, _mask = got[0]
            self.assertEqual(chain, "none")
            self.assertEqual(category, "Raw")
            self.assertNotIn("_处理产物", stem)
            self.assertIn("三项都关着", w.log_text.toPlainText())
        finally:
            w.close()

    def test_export_bg_missing_with_smoothing_keeps_smoothing_honest(self):
        """没扣成背景但开着平滑：平滑照做，链描述只写真的做了的平滑。"""
        w = create_window()
        try:
            self._open_1d(w)
            self._set_mode(w, "anchor")
            w.params["平滑曲线"].setChecked(True)
            w.params["平滑窗口 (°)"].setValue(0.5)
            got = gui_export._checked_1d_results(w, want_bg=True)
            stem, _, intensity, chain, category, _, _mask = got[0]
            self.assertIn("smooth=", chain)
            self.assertNotIn("bg=", chain, "背景没发生，不许写进链描述")
            self.assertEqual(category, "Processed")
            self.assertIn("_处理产物", stem)
            raw = np.asarray(_fake_bg_compute("", {}, 0)[1], dtype=float)
            self.assertFalse(np.allclose(np.asarray(intensity, dtype=float),
                                         raw), "平滑要真的生效")
        finally:
            w.close()

    def test_export_dialog_has_name_field_and_preview(self):
        """名称输入框 + 默认四段名预览（2026-10-23）。"""
        w = create_window()
        try:
            captured = {}
            results = [("LMFP_1_1D", np.array([1.0]), np.array([1.0]),
                        "none", data_export.CATEGORY_ONED, "lmfp1_lab6")]

            def fake_exec(self):
                captured["edit"] = self.findChild(
                    QLineEdit, "export_name_edit")
                captured["preview"] = self.findChild(
                    QLabel, "export_name_preview").text()
                return QDialog.Rejected

            with mock.patch.object(QDialog, "exec", new=fake_exec):
                gui_export._build_export_dialog(w, 1, results)
            self.assertIsNotNone(captured["edit"])
            self.assertEqual(captured["edit"].text(), "")
            self.assertIn("1D产物-LMFP_1-", captured["preview"])
        finally:
            w.close()

    def test_custom_name_is_sanitized(self):
        w = create_window()
        try:
            results = [("a", np.array([1.0]), np.array([1.0]),
                        "none", data_export.CATEGORY_RAW, "lmfp1_lab6")]
            base = gui_export._export_base_name(
                w, results, {"name": "我的/数据: v2"})
            self.assertEqual(base, "我的-数据- v2")
            self.assertIn("非法字符已替换", w.log_text.toPlainText())
        finally:
            w.close()

    def test_export_dialog_defaults_and_csv_gate(self):
        """目录默认 = 完整绝对路径；单个数据集禁用总表复选框（2026-10-02）。"""
        w = create_window()
        try:
            captured = {}

            def fake_exec(self):
                captured["dir"] = self.findChild(
                    QLineEdit, "export_dir_edit").text()
                captured["csv"] = self.findChild(QCheckBox,
                                                 "export_csv_check")
                return QDialog.Rejected

            with mock.patch.object(QDialog, "exec", new=fake_exec):
                gui_export._build_export_dialog(w, 1)
            self.assertEqual(captured["dir"], str(xrd_paths.OUTPUTS_DIR),
                             "默认目录是完整绝对路径，不再是字面 outputs")
            self.assertFalse(captured["csv"].isEnabled(),
                             "单条导出没有总表可生成")
            self.assertFalse(captured["csv"].isChecked())
            with mock.patch.object(QDialog, "exec", new=fake_exec):
                gui_export._build_export_dialog(w, 2)
            self.assertTrue(captured["csv"].isEnabled())
            self.assertTrue(captured["csv"].isChecked(),
                            "两个及以上默认顺手出一张大集合")
        finally:
            w.close()
