"""对比/堆叠/热图联动、文件栏、产物分组

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


class TestCompareStackAndHeatLink(unittest.TestCase):
    """任务六·对比增强：瀑布式堆叠显示 + 热图行点击联动对比面板。"""

    def _plot_compare(self, w):
        """勾 fake_a + fake_b：先各出过 1D（栅栏前置）再点 [对比]，
        等两条曲线到齐 → 返回（面板键, 坐标轴）。"""
        _prime_1d(w, ["data/fake_a.tif", "data/fake_b.tif"])
        w.compare_btn.click()
        keys = [k for k in w.plot_docks if k.startswith("对比")]
        self.assertEqual(len(keys), 1)
        ax = gui_panel_state._content(w.plot_docks[keys[0]]).axes_1d
        self.assertTrue(_wait_until(lambda: len(ax.lines) >= 2))
        return keys[0], ax

    def _synthetic_event(self, ax, ydata, x=50, y=50, button=1):
        ev = mock.Mock()
        ev.inaxes = ax
        ev.button = button
        ev.xdata = 1.0
        ev.ydata = ydata
        ev.x = x
        ev.y = y
        return ev

    def test_stack_offsets_curves_like_waterfall(self):
        """堆叠：行距统一 = **第二高**的行峰 ×0.7（不按各条自己的峰值，
        也不按全场最大），y 刻度为样品名（[显示数据名] 勾上时；默认关，
        见 test_data_names_default_off_and_toggle_live），无图例；取消堆叠
        回到平铺 + 图例回来。

        用户 2026-09-26："不要按照各自的最高峰归一化，所有的图"——
        假数据 fake_a 峰值 3、fake_b 峰值 30：两条按**同一个**行距抬行
        （旧规则按各自的 3 / 30 抬，等于把每条都缩到自己的高度）。
        2026-10-02 起基准由"全场最大"换成"第二高的那一行"（同
        services/stacking）：两条曲线时第二高 = 弱的那条 3 → 行距 2.1，
        强的那条照它的真实强度探出去——它确实强 10 倍，不该被压平。
        """
        w = create_window()
        try:
            key, ax = self._plot_compare(w)
            dock = w.plot_docks[key]
            y_flat = [np.asarray(l.get_ydata()).copy() for l in ax.lines]
            dock.params_snapshot["对比堆叠"] = True
            dock.params_snapshot["显示数据名"] = True   # 名字默认关（2026-10-08）
            gui_plot_compare._redraw_compare(w, key)
            y_stack = [np.asarray(l.get_ydata()).copy() for l in ax.lines]
            peaks = [float(np.nanmax(y)) for y in y_flat]        # [3, 30]
            step = sorted(peaks)[-2] * 0.7                       # 第二高 × 0.7
            self.assertAlmostEqual(step, 3.0 * 0.7, places=6,
                                   msg="基准是第二高的那条（3），不是最大（30）")
            np.testing.assert_allclose(y_stack[0], y_flat[0])   # 第一条不动
            np.testing.assert_allclose(y_stack[1], y_flat[1] + step)
            # 两条行基线 = 0 与 行距（同一个行距，不按各自的峰）
            self.assertAlmostEqual(float(y_stack[0][0]),
                                   float(y_flat[0][0]))
            self.assertAlmostEqual(float(y_stack[1][0]) - float(y_flat[1][0]),
                                   step)
            self.assertEqual([t.get_text() for t in ax.get_yticklabels()],
                             ["fake_a", "fake_b"], "短名（剥公共后缀）")
            self.assertIsNone(ax.get_legend(), "堆叠下 y 刻度即样品名，无图例")
            dock.params_snapshot["对比堆叠"] = False
            gui_plot_compare._redraw_compare(w, key)
            np.testing.assert_allclose(ax.lines[0].get_ydata(), y_flat[0])
            self.assertIsNotNone(ax.get_legend())
        finally:
            w.close()

    def test_manual_row_step_multiplies_the_offset(self):
        """[手动行距]：勾上后行距 = 自动行距 × 倍数（用户 2026-10-05）。

        用户原话："对比的堆叠添加让用户自己改 y off"。纯显示参数：
        改了立刻重画（与归一化/配色同一套即改即画），不勾 = 原来的
        自动口径（第二高的行峰 × 0.7）不动。
        """
        w = create_window()
        try:
            key, ax = self._plot_compare(w)
            dock = w.plot_docks[key]
            y_flat = [np.asarray(l.get_ydata()).copy() for l in ax.lines]
            w.params["对比堆叠"].setChecked(True)     # 即改即画
            step_auto = float(np.asarray(ax.lines[1].get_ydata())[0]
                              - y_flat[1][0])
            self.assertFalse(w.params["行距倍数"].isEnabled(),
                             "自动行距下倍数框置灰")
            w.params["手动行距"].setChecked(True)
            self.assertTrue(w.params["行距倍数"].isEnabled())
            w.params["行距倍数"].setValue(2.0)
            y2 = float(np.asarray(ax.lines[1].get_ydata())[0] - y_flat[1][0])
            self.assertAlmostEqual(y2, step_auto * 2.0, places=6,
                                   msg="行距该按倍数放大")
            w.params["手动行距"].setChecked(False)    # 回到自动口径
            y3 = float(np.asarray(ax.lines[1].get_ydata())[0] - y_flat[1][0])
            self.assertAlmostEqual(y3, step_auto, places=6)
        finally:
            w.close()

    def test_view_boxes_crop_compare_without_recompute(self):
        """「视图 2θ」框（对比小节那对，老键）作用于对比面板（裁剪窗口、
        不重算，2026-10-07；2026-10-08 晚拆键后它是**对比专用**——热图
        有自己一对，改一边不再动两边，见 TestHeatmap 的拆键测试）。

        填数即把对比的横轴裁到这一段，数据一份不动。
        """
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compare_compute) as c:
                key, ax = self._plot_compare(w)
                calls = c.call_count
            w.params["视图 2θ 下限 (°)"].setValue(2.0)
            w.params["视图 2θ 上限 (°)"].setValue(4.0)
            QApplication.processEvents()
            self.assertAlmostEqual(ax.get_xlim()[0], 2.0, places=2)
            self.assertAlmostEqual(ax.get_xlim()[1], 4.0, places=2)
            self.assertEqual(c.call_count, calls, "只换窗口，不许重算")
        finally:
            w.close()

    def test_stack_ignores_log_and_ylim(self):
        """堆叠下纵轴保持线性、范围由行偏移决定（对数/纵轴参数不适用）。"""
        w = create_window()
        try:
            key, ax = self._plot_compare(w)
            dock = w.plot_docks[key]
            dock.params_snapshot["对数纵轴"] = True
            dock.params_snapshot["纵轴自动"] = False
            dock.params_snapshot["纵轴下限"] = 2.0
            dock.params_snapshot["纵轴上限"] = 3.0
            dock.params_snapshot["对比堆叠"] = True
            gui_plot_compare._redraw_compare(w, key)
            self.assertEqual(ax.get_yscale(), "linear")
            lo, hi = ax.get_ylim()   # 手填 2~3 不生效：行基线决定范围
            self.assertTrue(lo <= 0.0 and hi > 3.0,
                            f"范围应按行偏移算，实际 {lo}~{hi}")
        finally:
            w.close()

    def test_stack_checkbox_applies_live(self):
        """勾 [堆叠显示] 立刻重画，**不需要** [应用显示设置]（用户 2026-10-02 第 1 条）。

        回归的是"够不着"：这个框住在「对比」页，而 [应用显示设置] 与"未应用"
        灰字只在「原图」页——旧行为下在对比页勾上它，那一页没有任何东西能把
        改动用出去，看上去就是"堆叠显示无效"（画图那侧实测一直是好的）。
        """
        w = create_window()
        try:
            key, ax = self._plot_compare(w)
            y_flat = [np.asarray(l.get_ydata()).copy() for l in ax.lines]
            w.params["显示数据名"].setChecked(True)   # 名字默认关（2026-10-08）
            w.params["对比堆叠"].setChecked(True)     # 只勾，不按 [应用]
            QApplication.processEvents()
            self.assertEqual([t.get_text() for t in ax.get_yticklabels()],
                             ["fake_a", "fake_b"], "勾上就该堆叠（短名刻度）")
            self.assertTrue((w.plot_docks[key].params_snapshot or {})
                            .get("对比堆叠"), "快照跟着更新")
            self.assertNotIn("图像", w.pending_labels,
                             "「图像」待办组已随全量即改即画退休（2026-10-08）")
            w.params["对比堆叠"].setChecked(False)
            QApplication.processEvents()
            np.testing.assert_allclose(ax.lines[0].get_ydata(), y_flat[0])
            self.assertIsNotNone(ax.get_legend(), "取消堆叠图例回来")
        finally:
            w.close()

    def test_data_names_default_off_and_toggle_live(self):
        """「显示数据名」默认关：平铺无图例、堆叠无纵轴名；勾上立刻出现。

        2026-10-08（用户："做按钮，默认不显示，点击开启后按条件显示"）
        ——起因是 81 条堆叠时全名糊成一条黑带（实测占图宽 29%）。
        """
        w = create_window()
        try:
            key, ax = self._plot_compare(w)             # 平铺，默认关
            self.assertIsNone(ax.get_legend(), "默认不画图例")
            w.params["对比堆叠"].setChecked(True)
            QApplication.processEvents()
            self.assertEqual([t.get_text() for t in ax.get_yticklabels()], [],
                             "默认不标样品名")
            w.params["显示数据名"].setChecked(True)     # 即改即画
            QApplication.processEvents()
            self.assertEqual([t.get_text() for t in ax.get_yticklabels()],
                             ["fake_a", "fake_b"], "勾上：短名 + 抽稀")
            w.params["显示数据名"].setChecked(False)
            QApplication.processEvents()
            self.assertEqual([t.get_text() for t in ax.get_yticklabels()], [])
            w.params["对比堆叠"].setChecked(False)
            w.params["显示数据名"].setChecked(True)
            QApplication.processEvents()
            self.assertIsNotNone(ax.get_legend(), "平铺 + 勾名字 → 图例")
        finally:
            w.close()

    def test_stack_widget_and_reset(self):
        w = create_window()
        try:
            w.params["对比堆叠"].setChecked(True)
            w.findChild(QPushButton, "reset_image_btn").click()
            self.assertFalse(w.params["对比堆叠"].isChecked())
            self.assertEqual(gui_state._DISPLAY_DEFAULTS["对比堆叠"], False)
        finally:
            w.close()

    def test_heat_row_click_toggles_compare_curve(self):
        """热图行点击 = 该样品在对比面板隐藏/显示切换（颜色序号不乱）。"""
        w = create_window()
        try:
            key, ax = self._plot_compare(w)
            dock = w.plot_docks[key]
            # 给面板挂 heat_files（行→文件），行 1 = fake_b.tif
            dock.heat_files = [gui_sources.make_source(
                "data/fake_a.tif", "fake_a.tif"),
                               gui_sources.make_source(
                                                   "data/fake_b.tif", "fake_b.tif")]
            gui_plot_compare._heat_row_press(w, key, self._synthetic_event(ax, 1))
            gui_plot_compare._heat_row_release(w, key, self._synthetic_event(ax, 1))
            self.assertEqual(dock.compare_hidden, {"fake_b.tif"})
            self.assertEqual([l.get_label() for l in ax.lines],
                             ["fake_a.tif"])
            self.assertIn("对比面板隐藏该曲线", w.log_text.toPlainText())
            # 再点一次 → 恢复，颜色序号不变（fake_b 仍是渐变第 2 条）
            gui_plot_compare._heat_row_press(w, key, self._synthetic_event(ax, 1))
            gui_plot_compare._heat_row_release(w, key, self._synthetic_event(ax, 1))
            self.assertEqual(dock.compare_hidden, set())
            self.assertEqual(len(ax.lines), 2)
            self.assertEqual(ax.lines[1].get_color(), "#9fc2ed")
        finally:
            w.close()

    def test_heat_drag_is_not_a_click(self):
        """按下后拖走（>5 px）→ 平移手势，不切换。"""
        w = create_window()
        try:
            key, ax = self._plot_compare(w)
            dock = w.plot_docks[key]
            dock.heat_files = [gui_sources.make_source(
                "data/fake_a.tif", "fake_a.tif"),
                               gui_sources.make_source(
                                                   "data/fake_b.tif", "fake_b.tif")]
            gui_plot_compare._heat_row_press(w, key,
                                      self._synthetic_event(ax, 1, x=50, y=50))
            gui_plot_compare._heat_row_release(w, key,
                                        self._synthetic_event(ax, 1,
                                                              x=200, y=200))
            self.assertFalse(hasattr(dock, "compare_hidden"))
            self.assertEqual(len(ax.lines), 2)
        finally:
            w.close()

    def test_heat_click_no_compare_panel_only_logs(self):
        """没有含该文件的对比面板 → 只提示，不炸。"""
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0))
            dock1 = _dock(w, "1D", "data/fake_b.tif")
            dock1.heat_files = [gui_sources.make_source(
                "data/fake_b.tif", "fake_b.tif")]
            ax1 = gui_panel_state._content(dock1).axes_1d
            gui_plot_compare._heat_row_press(w, "1D|data/fake_b.tif",
                                      self._synthetic_event(ax1, 0))
            gui_plot_compare._heat_row_release(w, "1D|data/fake_b.tif",
                                        self._synthetic_event(ax1, 0))
            self.assertIn("没有含该文件的对比面板", w.log_text.toPlainText())
        finally:
            w.close()

    def test_compare_shown_curves_filters_hidden(self):
        """_compare_shown_curves 跳过 compare_hidden：图例/颜色同口径。"""
        w = create_window()
        try:
            key, ax = self._plot_compare(w)
            dock = w.plot_docks[key]
            dock.compare_hidden = {"fake_a.tif"}
            curves = gui_state._compare_shown_curves(w, dock)
            # 每条 = (tth, shown, display, i, ref)，ref = 纵轴范围看的那份
            self.assertEqual([c[2] for c in curves], ["fake_b.tif"])
            self.assertEqual([c[3] for c in curves], [1],
                             "颜色序号跟文件走（隐藏第一条，第二条仍是 1 号）")
            self.assertEqual([len(c[4]) for c in curves],
                             [len(c[1]) for c in curves], "ref 与 shown 等长")
        finally:
            w.close()


class TestCompareStackView(unittest.TestCase):
    """堆叠视图与平铺视图分开记（用户 2026-10-15："对比图在堆叠后取消
    堆叠会导致视图不能回来"）。

    缘起（复现实测）：堆叠的 y 是"第几行"（行偏移），不是强度。此前
    堆叠里滚轮/平移写下的窗口冒充「纵轴自动/上下限」（平铺的设置），
    取消堆叠时被原样套用——假数据偏一点、真数据（81 条垫到几千上万
    counts、平铺才 0~310）画面直接空白/贴底；"家"也被换成堆叠量程，
    按 Home 回不来。现在：堆叠的窗口记「堆叠视图 y 范围」（视图键一
    类），家分两份（view_home / view_home_stack）——取消堆叠后视图
    与 Home 都回平铺那份；堆叠里缩放也不再被任何重画丢掉。
    """

    def _plot_compare(self, w):
        """勾 fake_a + fake_b：先各出过 1D（栅栏前置）再点 [对比]，
        等两条曲线到齐 → 返回（面板键, 坐标轴）。"""
        _prime_1d(w, ["data/fake_a.tif", "data/fake_b.tif"])
        w.compare_btn.click()
        keys = [k for k in w.plot_docks if k.startswith("对比")]
        self.assertEqual(len(keys), 1)
        ax = gui_panel_state._content(w.plot_docks[keys[0]]).axes_1d
        self.assertTrue(_wait_until(lambda: len(ax.lines) >= 2))
        return keys[0], ax

    def _wheel(self, w, key, ax, notches=3):
        """滚轮缩放（放大镜点亮）——同时动 x 和 y，与真手势一致。"""
        content = gui_panel_state._content(w.plot_docks[key])
        if not content.toolbar._actions["zoom"].isChecked():
            content.toolbar._actions["zoom"].trigger()
        px, py = ax.transData.transform((4.0, 2.0))
        for _ in range(notches):
            content.canvas.callbacks.process("scroll_event", MouseEvent(
                "scroll_event", content.canvas, px, py, step=1, button="up"))
        QApplication.processEvents()

    def _home(self, w, key):
        content = gui_panel_state._content(w.plot_docks[key])
        content.toolbar._actions["home"].trigger()
        QApplication.processEvents()

    def test_stacked_gesture_writes_its_own_view_key(self):
        """堆叠里滚轮：窗口记「堆叠视图 y 范围」；「纵轴自动/上下限」与
        参数坞控件原封不动（那是平铺的设置，曾被抓成堆叠坐标）。"""
        w = create_window()
        try:
            key, ax = self._plot_compare(w)
            dock = w.plot_docks[key]
            w.params["对比堆叠"].setChecked(True)
            QApplication.processEvents()
            self._wheel(w, key, ax)
            snap = dock.params_snapshot
            self.assertIn("堆叠视图 y 范围", snap)
            lo, hi = snap["堆叠视图 y 范围"]
            self.assertAlmostEqual(lo, ax.get_ylim()[0], places=3)
            self.assertAlmostEqual(hi, ax.get_ylim()[1], places=3)
            self.assertTrue(snap["纵轴自动"], "平铺的自动开关不许被碰")
            self.assertTrue(w.params["纵轴自动"].isChecked(),
                            "参数坞控件也不许被刷成堆叠值")
        finally:
            w.close()

    def test_unstack_returns_to_the_flat_view(self):
        """平铺缩放（窗口 W）→ 堆叠 → 堆叠里缩放 → 取消堆叠：视图回到
        平铺那份 W（堆叠坐标的窗口不许套进来）。"""
        w = create_window()
        try:
            key, ax = self._plot_compare(w)
            self._wheel(w, key, ax)                 # 平铺里缩放
            flat_window = tuple(ax.get_ylim())
            w.params["对比堆叠"].setChecked(True)
            QApplication.processEvents()
            self._wheel(w, key, ax)                 # 堆叠里再缩放
            self.assertNotEqual(tuple(ax.get_ylim()), flat_window,
                                "前置条件：堆叠里确实缩过")
            w.params["对比堆叠"].setChecked(False)
            QApplication.processEvents()
            # 平铺窗口经参数坞控件走了一趟（1 位小数），容差放到 0.1
            self.assertAlmostEqual(ax.get_ylim()[0], flat_window[0], places=1)
            self.assertAlmostEqual(ax.get_ylim()[1], flat_window[1], places=1)
        finally:
            w.close()

    def test_home_follows_the_current_mode(self):
        """堆叠里缩放 → 取消堆叠 → Home = 平铺最初视野；再堆叠 → Home =
        整个堆叠（两份"家"各管各的模式）；堆叠里按过 Home 后窗口账
        作废（免得下次重画又套回旧窗口）。"""
        w = create_window()
        try:
            key, ax = self._plot_compare(w)
            dock = w.plot_docks[key]
            flat_home = tuple(ax.get_ylim())        # 平铺最初视野
            w.params["对比堆叠"].setChecked(True)
            QApplication.processEvents()
            self._wheel(w, key, ax)
            w.params["对比堆叠"].setChecked(False)
            QApplication.processEvents()
            self._home(w, key)
            self.assertEqual(tuple(ax.get_ylim()), flat_home,
                             "平铺的 Home 该回平铺最初视野（不是堆叠量程）")
            w.params["对比堆叠"].setChecked(True)
            QApplication.processEvents()
            self._home(w, key)
            stacked_top = max(float(np.nanmax(ln.get_ydata()))
                              for ln in ax.lines)
            self.assertGreaterEqual(float(ax.get_ylim()[1]),
                                    stacked_top * 0.95,
                                    "堆叠的 Home = 整个堆叠，不裁上半截")
            self.assertNotIn("堆叠视图 y 范围", dock.params_snapshot,
                             "与家重合的窗口丢弃（防下次重画又套回来）")
        finally:
            w.close()

    def test_stacked_zoom_survives_a_redraw(self):
        """堆叠里缩放后切「显示数据名」（重画）：窗口还在——此前重画
        按默认量程、放大白做。"""
        w = create_window()
        try:
            key, ax = self._plot_compare(w)
            w.params["对比堆叠"].setChecked(True)
            QApplication.processEvents()
            self._wheel(w, key, ax)
            window = tuple(ax.get_ylim())
            w.params["显示数据名"].setChecked(True)   # 即改即画 → 重画
            QApplication.processEvents()
            self.assertAlmostEqual(ax.get_ylim()[0], window[0], places=3)
            self.assertAlmostEqual(ax.get_ylim()[1], window[1], places=3)
        finally:
            w.close()


class TestClosePrompt(unittest.TestCase):
    """关窗询问：有未保存的图时弹窗（保存 / 不保存 / 取消）。"""

    def test_close_cancel_stays_open(self):
        w = create_window()
        try:
            _draw_one_1d(w)
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="cancel"):
                self.assertFalse(w.close(), "取消 → 留在程序里")
        finally:
            w.close()

    def test_close_discard_closes(self):
        w = create_window()
        try:
            _draw_one_1d(w)
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard") as confirm:
                self.assertTrue(w.close())
                self.assertTrue(confirm.called)
        finally:
            w.close()

    def test_close_save_runs_save_flow(self):
        w = create_window()
        try:
            _draw_one_1d(w)
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="save"), \
                 mock.patch.object(gui_app, "_save_figures",
                                   return_value=True) as save:
                self.assertTrue(w.close())
                self.assertTrue(save.called)
        finally:
            w.close()

    def test_close_save_cancelled_stays_open(self):
        w = create_window()
        try:
            _draw_one_1d(w)
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="save"), \
                 mock.patch.object(gui_app, "_save_figures",
                                   return_value=False):
                self.assertFalse(w.close(), "保存被取消 → 留在程序里")
        finally:
            # 第一次 close 被 ignore、窗口还开着：收尾这次必须罩住确认框，
            # 否则 offscreen 下真弹窗 → 挂死（同其他关窗测试）
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_close_saved_figures_skips_prompt(self):
        w = create_window()
        try:
            dock = _draw_one_1d(w)
            dock.figure_saved = True   # 已存过盘
            with mock.patch.object(gui_app, "_confirm_close",
                                   side_effect=AssertionError("不应询问")) as confirm:
                self.assertTrue(w.close())
                confirm.assert_not_called()
        finally:
            w.close()


class TestDragDrop(unittest.TestCase):
    """拖文件进窗口 = 加入文件列表（tif/edf/cbf，拖到窗口任意位置）。"""

    def test_drop_adds_files(self):
        w = create_window()
        try:
            abs_a = str(Path("data/fake_a.tif").resolve())
            abs_b = str(Path("data/fake_b.tif").resolve())
            _drop_event(w, [abs_a, abs_b])
            self.assertEqual(w.file_list.count(), 2)
            self.assertEqual(w.file_list.item(0).text(), "fake_a.tif")
            self.assertEqual(w.file_list.item(1).text(), "fake_b.tif")
            # 拖入也不自动勾（导入默认不勾选，用户 2026-09-25 定）
            self.assertEqual(w.file_list.item(0).checkState(), Qt.Unchecked)
            self.assertEqual(w.file_list.item(1).checkState(), Qt.Unchecked)
            self.assertIsNone(w.file_list.currentItem())
            self.assertEqual(w.file_label.text(), "未打开任何文件")
            self.assertIn("已添加 2 个文件", w.log_text.toPlainText())
        finally:
            w.close()

    def test_drag_enter_accepts_supported_rejects_others(self):
        w = create_window()
        try:
            ev = _drop_event(w, ["/tmp/x.tif"], kind="enter")
            self.assertTrue(ev.isAccepted(), "支持的类型应接住")
            ev = _drop_event(w, ["/tmp/x.txt"], kind="enter")
            self.assertFalse(ev.isAccepted(), "不支持的类型应拒绝")
        finally:
            w.close()

    def test_dropped_file_can_be_plotted(self):
        w = create_window()
        try:
            abs_b = str(Path("data/fake_b.tif").resolve())
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                _drop_event(w, [abs_b])
                w.findChild(QPushButton, "select_all_btn").click()   # 勾上
                _open_view(w, "1D")
                drawn = _wait_until(
                    lambda: len(_axes(w, "1D", abs_b).lines) > 0)
                self.assertTrue(drawn, "拖入的文件应能直接作图")
        finally:
            w.close()


class TestFileBarKeepsItsPlace(unittest.TestCase):
    """文件栏重建不许改"用户在哪儿"（用户 2026-09-27）。

    触发重建的路径很多，最常撞上的是"画图"：一批 1D 算完 → refresh_groups
    （plot_views._batch_step）。重建 = clear_groups + 重新 addTopLevelItem +
    expandAll，滚动位置/收起的组/勾着的产物条目/当前高亮本来都会丢，列表
    被弹回最顶端。
    """

    def setUp(self):
        stage_cache.write_batches("bg", [])

    def _long_list(self, w, n=24):
        """造一个长到需要滚动的列表：n 个文件 + 各一份 1D 产物。

        **必须 w.show()**：Qt 的 `setCurrentItem` 只在视图可见时才把目标行
        滚进视野（`QAbstractItemView` 的 shouldScrollToCurrent 判可见性 +
        焦点）。不 show 的话"滚动位置"根本不会变，断言全成了空话——探针
        实测：不显示时 setCurrentItem 后 scroll 纹丝不动，显示时 8 → 1。
        """
        files = _tmp_files(n)
        w.show()
        w.add_files([str(p) for p in files], select=False)
        for p in files:
            _store_product(w, p)
        w.refresh_groups()
        group = _group_by_text(w, "1D 产物")
        self.assertIsNotNone(group, "得先有分组可测")
        return w.file_list, group

    def test_rebuild_keeps_scroll_checks_and_highlight(self):
        """重建前后：滚动位置、产物条目的对号、当前高亮都一样。"""
        w = create_window()
        try:
            tree, group = self._long_list(w)
            bar = tree.verticalScrollBar()
            self.assertGreaterEqual(bar.maximum(), 5, "内容够长才有得测")
            leaf = group.child(0)               # 产物组里最上面那条
            leaf.setCheckState(0, Qt.Checked)
            tree.setCurrentItem(leaf)           # 真实点击会顺手把它设为当前项
            QApplication.processEvents()
            self.assertIs(tree.currentItem(), leaf, "勾选着的条目可以带高亮")
            bar.setValue(0)                     # 用户在最上面
            QApplication.processEvents()
            w.refresh_groups()
            QApplication.processEvents()    # 滚动位置是下一个事件循环才落地的
            self.assertEqual(bar.value(), 0, "重建后滚动位置必须原地不动")
            restored = _group_by_text(w, "1D 产物")
            self.assertEqual(restored.child(0).checkState(0), Qt.Checked,
                             "重建后勾着的产物条目还是勾着的")
            src = gui_sources.source_of(restored.child(0))
            cur = gui_sources.source_of(tree.currentItem())
            self.assertEqual((cur.kind, cur.key), (src.kind, src.key),
                             "当前高亮也该回到那一条")
        finally:
            w.close()

    def test_rebuild_keeps_a_collapsed_group_collapsed(self):
        """用户收起来的组，重建后仍收着（expandAll 不许一刀切）。

        两个没有批次号的组都要管：「原始数据」和「1D 产物」——按身份认人
        时不能都退回同一个名字，否则"收起原始数据"会被当成"收起 1D 产物"。
        """
        w = create_window()
        try:
            tree, group = self._long_list(w)
            group.setExpanded(False)
            tree.raw_group.setExpanded(False)
            QApplication.processEvents()
            w.refresh_groups()
            self.assertFalse(_group_by_text(w, "1D 产物").isExpanded())
            self.assertFalse(tree.raw_group.isExpanded(), "原始数据组也该收着")
        finally:
            w.close()

    def test_import_does_not_scroll_the_list(self):
        """导入一批文件后，列表还在原处（不许被"最后一条新条目"带着滚走）。

        用户 2026-09-27："导入数据后，会一下跳到数据中间位置，应该是还在
        顶端"。原因：导入结尾 `setCurrentItem(added[-1])` 把最后一条新
        条目设为当前项，Qt 顺手把它滚进视野。
        """
        w = create_window()
        try:
            files = _tmp_files(40)
            w.add_files([str(p) for p in files[:5]], select=False)
            w.show()
            QApplication.processEvents()
            tree = w.file_list
            bar = tree.verticalScrollBar()
            w.add_files([str(p) for p in files[5:]], select=False)
            QApplication.processEvents()
            self.assertGreaterEqual(bar.maximum(), 2, "内容够长才有得测")
            self.assertEqual(bar.value(), 0, "导入不该把列表滚到新条目那儿")
            self.assertEqual(tree.count(), 40, "40 条都进来了")
        finally:
            w.hide()
            w.close()

    def test_right_click_open_checked_has_no_panel_cap(self):
        """右键「打开勾选的 N 张图」：勾选的条目全交出去（超防爆线也不砍）。

        用户 2026-09-27："没有办法批量打开图，只能一个一个选"。视图按钮
        超过防爆线是**一张都不画**的（防爆图），所以批量开图必须有另一条
        入口——它带确认框，但不砍张数。2026-10-09 起按视图选，这里把
        1D 与 2D 两条都过一遍。
        """
        w = create_window()
        try:
            files = _tmp_files(30)
            w.add_files([str(p) for p in files], select=False)
            w.file_list.raw_group.setCheckState(0, Qt.Checked)
            QApplication.processEvents()
            got = {}
            w.open_view_group = lambda sources, name: got.update(
                n=len(sources), name=name)
            gui_file_dock._open_checked_views(w, "1D")   # 窗口没显示 → 不弹确认
            self.assertEqual(got.get("n"), 30, "30 张全交出去，不砍到防爆线")
            self.assertEqual(got.get("name"), "1D")
            gui_file_dock._open_checked_views(w, "2D")   # 原图同样没有上限
            self.assertEqual(got.get("n"), 30)
            self.assertEqual(got.get("name"), "2D")
        finally:
            w.close()

    def test_bulk_open_menu_offers_every_view(self):
        """右键批量打开按视图选（2026-10-09）：勾选/整组两条都挂四种视图。

        此前写死 1D——原图三类超限后连手动全开的入口都没有（用户：
        "原图功能无法批量出超过防爆图数字，应该怎么优化"）。菜单内容与
        exec 分开，离屏测试直接查这份映射。
        """
        w = create_window()
        try:
            files = _tmp_files(2)
            w.add_files([str(p) for p in files], select=True)
            actions = gui_file_dock._entry_menu_actions(
                QMenu(w), w, w.file_list.raw_group)
            wanted = {("open_checked", v) for v in gui_file_dock.OPEN_BULK_VIEWS}
            wanted |= {("open_group", v) for v in gui_file_dock.OPEN_BULK_VIEWS}
            self.assertTrue(
                wanted.issubset(set(actions.values())),
                "「打开勾选的 N 张图」与「打开整组图」都该给 "
                f"1D/2D/剖面/瀑布（{gui_file_dock.OPEN_BULK_VIEWS}）")
        finally:
            w.close()

    def test_open_checked_two_d_skips_product_entries(self):
        """批量打开选 2D：产物条目挑出去、日志说清（产物只能出 1D）。

        2026-10-09 扩展入口时定的口径：非 1D 视图要原始图像，勾选里的
        产物条目跳过并记日志——与视图按钮 _plot_selected 同一套说法，
        不静默少开。
        """
        w = create_window()
        try:
            raw = gui_sources.make_source("/tmp/a.tif", display="a.tif",
                                          kind=gui_sources.RAW)
            bg = gui_sources.make_source("/tmp/a.tif", display="a.tif 处理产物",
                                         kind=gui_sources.BG, key="k1")
            got = {}
            w.open_view_group = lambda sources, name: got.update(
                n=len(sources), name=name)
            with mock.patch.object(gui_file_dock.gui_sources,
                                   "checked_sources",
                                   return_value=[raw, bg]):
                gui_file_dock._open_checked_views(w, "2D")
            self.assertEqual(got.get("n"), 1, "只交原始条目")
            self.assertEqual(got.get("name"), "2D")
            self.assertIn("跳过 1 个产物条目", w.log_text.toPlainText())
        finally:
            w.close()

    def test_check_summary_shows_composition_and_flags_duplicates(self):
        """勾选摘要写清"勾了几条、都是什么"，并提醒"同一文件勾了两遍"。

        用户 2026-09-27："选中81个图出对比图，结果出了162个文件的对比图，
        好像是把1d也加进去了，修正，对比图就是选中的对比"——对比**只画勾选
        的**，真相是勾选集比他以为的大（为了出 1D 图勾过 81 个原始文件，
        之后又点了「1D 产物」组行）。所以把数字写到出图按钮上、构成写到
        摘要里。
        """
        w = create_window()
        try:
            files = _tmp_files(3)
            w.add_files([str(p) for p in files], select=False)
            for p in files:
                _store_product(w, p)
            w.refresh_groups()
            tree = w.file_list
            group = _group_by_text(w, "1D 产物")
            self.assertIsNotNone(group)
            tree.raw_group.setCheckState(0, Qt.Checked)     # 勾 3 条原始
            QApplication.processEvents()
            # 原图页底部那个 [出图] 已删（2026-10-08：与页顶类型按钮
            # 同源、纯重复）；"勾选 N 个"只剩 1D 页的产出按钮 + 摘要句
            self.assertFalse(hasattr(w, "plot_now_btn"))
            self.assertEqual(w.plot_1d_btn.text(), "出图（已勾选 3 项）")
            self.assertIn("原始数据 3", w.check_summary_lbl.text())
            self.assertNotIn("⚠", w.check_summary_lbl.text(), "此时还没有重复")
            group.setCheckState(0, Qt.Checked)              # 再勾 3 条 1D 产物
            QApplication.processEvents()
            self.assertEqual(w.plot_1d_btn.text(), "出图（已勾选 6 项）")
            txt = w.check_summary_lbl.text()
            self.assertIn("原始数据 3", txt)
            self.assertIn("1D 产物 3", txt)
            self.assertIn("画两遍", txt, "同一文件的原始条目与 1D 产物都勾了")
        finally:
            w.close()

    def test_unchecking_does_not_scroll_the_list(self):
        """取消勾选 → 列表纹丝不动，高亮也不许跳到别处。

        旧版这里会把高亮跳到"第一条勾选条目"（通常就是最上面那条文件），
        于是取消勾选、点组节点、[全选] 都会把列表拽回顶端；而且"放回滚动
        位置"是同步做的，Qt 的滚动有时下一帧才落地 → **偶发**跳顶
        （用户 2026-09-27："没打字就跳了"）。现在改成取消高亮，没有可跳的
        目标，也就没有竞态。
        """
        w = create_window()
        try:
            tree, group = self._long_list(w)
            bar = tree.verticalScrollBar()
            self.assertGreaterEqual(bar.maximum(), 5, "内容够长才有得测")
            top = tree.raw_group.child(0)        # 列表最上面那条（原始数据）
            top.setCheckState(0, Qt.Checked)
            leaf = group.child(3)                # 靠下的产物条目
            leaf.setCheckState(0, Qt.Checked)
            QApplication.processEvents()
            tree.setCurrentItem(leaf)            # 用户刚点了靠下那条
            QApplication.processEvents()
            bar.setValue(bar.maximum())          # 滚到底
            QApplication.processEvents()
            leaf.setCheckState(0, Qt.Unchecked)  # 取消它
            QApplication.processEvents()
            self.assertEqual(bar.value(), bar.maximum(),
                             "取消勾选不该动滚动条")
            self.assertIsNone(tree.currentItem(),
                              "高亮留在没勾的条目上就是假象 → 直接取消")
        finally:
            w.close()


class TestFileBarSorting(unittest.TestCase):
    """文件栏排序（2026-10-08 用户："文件栏添加排序功能"）。

    三个模式：导入顺序（默认；只活本次会话、不落盘）/ 名称（自然序）/
    修改时间（新在前）。只动「原始数据」的行；产物分组的行序跟着走；
    勾选与高亮始终不动；「按条件选…」的"第 N 个"按**当前显示顺序**数。
    """

    def _named(self, names):
        folder = Path(tempfile.mkdtemp(prefix="xrd_sort_"))
        out = []
        for n in names:
            p = folder / n
            p.write_bytes(b"x")
            out.append(str(p))
        return out

    def _order(self, w):
        return [w.file_list.item(i).text() for i in range(w.file_list.count())]

    def _trigger(self, w, label):
        act = next(a for a in w.sort_menu.actions() if a.text() == label)
        act.trigger()
        QApplication.processEvents()

    def test_natural_key_orders_digit_runs_numerically(self):
        k = gui_file_dock._natural_key
        self.assertLess(k("LMFP_1_atten0-00029.tif"),
                        k("LMFP_1_atten0-00031.tif"))
        self.assertLess(k("LMFP_1_atten0-00031.tif"),
                        k("LMFP_1_atten0-00100.tif"))
        self.assertLess(k("s2.tif"), k("s10.tif"))
        self.assertEqual(k("A1"), k("a1"), "大小写不影响排序")
        self.assertEqual(k(None), k(""), "空值不炸")

    def test_sort_menu_defaults_to_import_order(self):
        w = create_window()
        try:
            btn = w.findChild(QPushButton, "sort_btn")
            self.assertIsNotNone(btn)
            self.assertIsNotNone(btn.menu(), "排序是个下拉菜单")
            acts = w.sort_menu.actions()
            self.assertEqual([a.text() for a in acts],
                             ["导入顺序", "名称", "修改时间"])
            self.assertEqual([a.text() for a in acts if a.isChecked()],
                             ["导入顺序"], "默认勾「导入顺序」")
        finally:
            w.close()

    def test_switch_to_name_reorders_and_keeps_checks_and_current(self):
        w = create_window()
        try:
            paths = self._named(["b.tif", "a.tif", "c.tif"])
            w.add_files(paths, select=False)
            first = w.file_list.item(0)          # b.tif
            first.setCheckState(0, Qt.Checked)
            w.file_list.setCurrentItem(first)
            self._trigger(w, "名称")
            self.assertEqual(self._order(w), ["a.tif", "b.tif", "c.tif"])
            self.assertEqual(first.text(0), "b.tif", "移动的是同一个条目对象")
            self.assertEqual(first.checkState(0), Qt.Checked, "勾选跟着走")
            self.assertIs(w.file_list.currentItem(), first, "高亮跟着走")
            self.assertIn("文件栏排序改为「名称」", w.log_text.toPlainText())
        finally:
            w.close()

    def test_switch_back_to_import_order_restores_the_original_order(self):
        w = create_window()
        try:
            w.add_files(self._named(["b.tif", "a.tif", "c.tif"]),
                        select=False)
            self._trigger(w, "名称")
            self._trigger(w, "导入顺序")
            self.assertEqual(self._order(w), ["b.tif", "a.tif", "c.tif"],
                             "序号戳把原顺序排回来")
        finally:
            w.close()

    def test_sort_by_mtime_puts_the_newest_first(self):
        w = create_window()
        try:
            paths = self._named(["a.tif", "b.tif", "c.tif"])
            base = time.time() - 1000
            for i, p in enumerate(paths):        # a 最旧、c 最新
                os.utime(p, (base + i * 10, base + i * 10))
            os.remove(paths[1])                  # b 从磁盘消失（条目还在栏里）
            w.add_files(paths, select=False)
            self._trigger(w, "修改时间")
            order = self._order(w)
            self.assertEqual(order[0], "c.tif", "最新的在最上面")
            self.assertEqual(order[-1], "b.tif",
                             "读不到修改时间的（文件已不在）排最后")
        finally:
            w.close()

    def test_import_while_sorted_lands_in_sorted_position(self):
        w = create_window()
        try:
            w.add_files(self._named(["s05.tif"]), select=False)
            w.show()
            QApplication.processEvents()
            # 40 条（同 test_import_does_not_scroll_the_list 的舞台）：
            # 默认窗口下列表够长、滚动条真的能滚
            w.add_files(self._named([f"s{i:02d}.tif" for i in
                                     range(20, 60)]), select=False)
            self._trigger(w, "名称")
            bar = w.file_list.verticalScrollBar()
            self.assertGreaterEqual(bar.maximum(), 2, "内容够长才有得测")
            bar.setValue(bar.maximum())
            keep = bar.value()
            self.assertGreater(keep, 0, "前提：真的滚到了下面")
            w.add_files(self._named(["s01.tif"]), select=False)
            QApplication.processEvents()
            self.assertEqual(self._order(w)[0], "s01.tif",
                             "排序开着时新条目落到排好的位置")
            self.assertEqual(bar.value(), keep, "滚动条原地不动（不许跳）")
        finally:
            w.hide()
            w.close()

    def test_product_group_rows_follow_the_sorted_raw_order(self):
        w = create_window()
        try:
            paths = [Path(s) for s in self._named(["b.tif", "a.tif"])]
            w.add_files([str(p) for p in paths], select=False)
            for p in paths:
                _store_product(w, p)
            w.refresh_groups()
            group = _group_by_text(w, "1D 产物")
            self.assertEqual(
                [group.child(i).text(0).split(" ·")[0]
                 for i in range(group.childCount())],
                ["b.tif", "a.tif"], "前提：组内先按文件栏行序（导入序）")
            self._trigger(w, "名称")
            group = _group_by_text(w, "1D 产物")
            self.assertEqual(
                [group.child(i).text(0).split(" ·")[0]
                 for i in range(group.childCount())],
                ["a.tif", "b.tif"], "1D 产物组的行序跟着文件栏走")
        finally:
            w.close()

    def test_processing_group_rows_follow_the_sorted_raw_order(self):
        w = create_window()
        try:
            paths = [Path(s) for s in self._named(["b.tif", "a.tif"])]
            w.add_files([str(p) for p in paths], select=False)
            items = [(p, _store_product(w, p, kind="bg")) for p in paths]
            stage_cache.record_batch(
                "bg", "20260101-000000-c0ffee",
                label="处理产物 01-01 00:00（自动基线）",
                items=items, config=w.config_name, npt=1000)
            w.refresh_groups()
            group = _group_by_text(w, "处理产物 01-01")
            self.assertIsNotNone(group, "前提：该有处理产物组")
            self.assertEqual(
                [group.child(i).text(0).split(" ·")[0]
                 for i in range(group.childCount())],
                ["b.tif", "a.tif"],
                "处理产物组行序 = 文件栏行序（与路径字符串序相反，"
                "2026-10-08 拍板与 1D 组同口径）")
            self._trigger(w, "名称")
            group = _group_by_text(w, "处理产物 01-01")
            self.assertEqual(
                [group.child(i).text(0).split(" ·")[0]
                 for i in range(group.childCount())],
                ["a.tif", "b.tif"], "排序后处理产物组也跟着走")
        finally:
            w.close()

    def test_selection_by_condition_counts_in_display_order(self):
        """「按条件选…」的"第 N 个"按当前显示顺序——排序会改变它数的对象。"""
        w = create_window()
        try:
            w.add_files(self._named(["s10.tif", "s2.tif", "s1.tif"]),
                        select=False)
            spec = {"mode": "range", "start": 1, "stop": 1, "text": "",
                    "append": False}
            gui_file_dock.apply_selection(w, spec)
            self.assertEqual(
                [self._order(w)[i] for i in range(3)
                 if w.file_list.item(i).checkState() == Qt.Checked],
                ["s10.tif"], "排序前：第 1 个 = 导入的第一个")
            self._trigger(w, "名称")
            gui_file_dock.apply_selection(w, spec)
            self.assertEqual(
                [self._order(w)[i] for i in range(3)
                 if w.file_list.item(i).checkState() == Qt.Checked],
                ["s1.tif"], "排序后：第 1 个 = 显示的第一个")
        finally:
            w.close()

    def test_picking_the_current_mode_does_not_move_the_list(self):
        w = create_window()
        try:
            w.add_files(self._named(["b.tif", "a.tif"]), select=False)
            n_before = w.log_text.toPlainText().count("文件栏排序改为")
            self._trigger(w, "导入顺序")     # 已经是导入顺序：点了等于没点
            self.assertEqual(self._order(w), ["b.tif", "a.tif"])
            self.assertEqual(w.log_text.toPlainText().count("文件栏排序改为"),
                             n_before, "不重排也不刷日志")
        finally:
            w.close()

    def test_sort_button_keeps_the_file_dock_narrow(self):
        """第三个按钮不进第三行：文件列不许顶过 ≈320 px 的窄排版红线。"""
        w = create_window()
        try:
            w.show()
            w.entrance_buttons["1D"].click()
            QApplication.processEvents()
            self.assertLess(w.file_dock.minimumSizeHint().width(), 320)
        finally:
            w.close()


class TestRecipeReuse(unittest.TestCase):
    """配方：存下来、给别的文件/批次套用——**锚点位置共用、强度逐文件重取**。

    用户 2026-09-27："我觉得配方可以保存用也挺好，两批数据用同一个锚点可行吗"。
    可行：锚点回答的是"2θ 的哪几个位置是纯背景"，那是装置的性质；背景有多高
    是每张图自己的事（同 [批量处理] 一贯的口径）。
    """

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="xrd_recipe_"))
        self._old_store = recipe_store.STORE
        recipe_store.STORE = self.dir / "recipes.json"

    def tearDown(self):
        recipe_store.STORE = self._old_store

    def test_anchors_outside_the_data_are_dropped(self):
        """锚点落在数据范围外要**丢掉**（`np.interp` 会静默钳到端点值）。

        钳端点 = 拿边缘那一点的强度当锚点，图上完全看不出来；跨批次复用
        配方时这条最常见（用户 2026-09-27 讨论时一起定的口径）。
        """
        tth = np.linspace(1.0, 8.0, 100)
        intensity = np.linspace(1000.0, 100.0, 100)
        keep, dropped = gui_views._anchors_on_curve([0.5, 2.0, 9.9],
                                                    tth, intensity)
        self.assertEqual([x for x, _ in keep], [2.0])
        self.assertEqual(dropped, [0.5, 9.9])
        self.assertAlmostEqual(keep[0][1],
                               float(np.interp(2.0, tth, intensity)), places=9)

    def test_save_then_apply_re_samples_the_anchor_heights(self):
        """存一份配方 → 套到另一个文件：位置照搬，**强度按那张图自己的曲线取**。"""
        w = create_window()
        try:
            files = _tmp_files(2)

            def fake(path_str, geom, npt):
                tth, y = _fake_compute(path_str, geom, npt)
                k = 2.0 if files[1].name in str(path_str) else 1.0
                return tth, np.asarray(y, dtype=float) * k

            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=fake):
                w.add_files([str(p) for p in files], select=False)
                gui_file_dock._open_entry_view(w, w.file_list.raw_group.child(0),
                                               explicit=True)
                keyA = f"1D|{files[0]}"
                self.assertTrue(_wait_until(
                    lambda: keyA in w.plot_docks
                    and len(_axes(w, "1D", str(files[0])).lines) > 0))
                dockA = w.plot_docks[keyA]
                combo = w.params["背景扣除模式"]
                combo.setCurrentIndex(combo.findData("auto"))
                w.bg_anchors[str(files[0])] = [(2.0, 900.0), (6.0, 300.0)]
                QApplication.processEvents()
                recipe_store.save("测试配方", gui_panel_state._proc_settings(
                    w, dockA, files[0]))
                self.assertIn("测试配方", recipe_store.names())

                # B：打开（曲线是原始的），再套用配方
                gui_file_dock._open_entry_view(w, w.file_list.raw_group.child(1),
                                               explicit=True)
                keyB = f"1D|{files[1]}"
                self.assertTrue(_wait_until(
                    lambda: keyB in w.plot_docks
                    and len(_axes(w, "1D", str(files[1])).lines) > 0))
                dockB = w.plot_docks[keyB]
                gui_panel_state._set_focus(w, keyB, dockB.windowTitle())
                QApplication.processEvents()
                w.recipe_combo.setCurrentIndex(
                    w.recipe_combo.findData("saved|测试配方"))
                gui_app._apply_selected_recipe(w)
                QApplication.processEvents()
                anchors_b = w.bg_anchors.get(str(files[1])) or []
                self.assertEqual([x for x, _ in anchors_b], [2.0, 6.0],
                                 "锚点位置照搬（跨批次复用）")
                tth_b = np.asarray(dockB.last_tth, dtype=float)
                y_b = np.asarray(dockB.last_intensity, dtype=float)
                self.assertAlmostEqual(
                    anchors_b[0][1], float(np.interp(2.0, tth_b, y_b)),
                    places=6, msg="强度要在 B 自己的曲线上重取，不是 A 的 900")
                self.assertEqual(
                    (dockB.params_snapshot or {}).get("背景扣除模式"), "auto",
                    "套用后 B 的模式跟着配方走")
        finally:
            w.close()

    def test_save_recipe_button_actually_writes_the_store(self):
        """[保存为配方…] 这个**按钮**要能落盘（2026-09-28 用户报"保存无效"的回归）。

        查出来的根因很朴素：`app._save_recipe_dialog` 里调了 `_proc_settings`，
        而这个名字**在 app 模块里从来没导入过**——每点一次都是 NameError，
        日志区一个字都没有，`outputs/recipes.json` 从来没生成过（用户机器上
        确实不存在这个文件，正是佐证）。旧的配方测试都直接调
        `recipe_store.save(...)`，正好绕过按钮，所以一直全绿。

        这条守在**用户走的那条路**上：点按钮 → 仓库里多一份 → 下拉里立刻
        能选到它。
        """
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files], select=False)
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                gui_file_dock._open_entry_view(
                    w, w.file_list.raw_group.child(0), explicit=True)
                key = f"1D|{files[0]}"
                self.assertTrue(_wait_until(
                    lambda: key in w.plot_docks
                    and getattr(w.plot_docks[key], "last_tth", None) is not None))
            combo = w.params["背景扣除模式"]
            combo.setCurrentIndex(combo.findData("auto"))
            QApplication.processEvents()
            dlg = mock.MagicMock()
            dlg.getText.return_value = ("按钮配方", True)
            with mock.patch.object(gui_param_helpers, "QInputDialog", dlg):
                w.findChild(QPushButton, "recipe_save_btn").click()
            QApplication.processEvents()
            self.assertIn("按钮配方", recipe_store.names(),
                          "点了按钮就该真的存下来")
            self.assertIn("配方已保存：按钮配方", w.log_text.toPlainText())
            # 存完立刻能选（下拉当场刷）
            self.assertGreaterEqual(
                w.recipe_combo.findData("saved|按钮配方"), 0,
                "存下来的配方要能马上在下拉里选到")
        finally:
            w.close()

    def test_recipe_rows_are_fresh_right_after_a_tweak(self):
        """刚调完参数，下拉里就该有「本图配方」（用户报"套用无效"的另一半）。

        原先填下拉的动作跑在 `_sync_bg_rows` 里、而"本图配方"是 `_refresh_proc`
        末尾才写进 window.proc_recipes 的——顺序颠倒，于是**刚处理完那一刻
        下拉里只有占位项**（"（还没有配方…）"），用户按 [套用] 只得到一句
        "先在下拉里选一份配方"。真实手势恰恰是"调好参数马上点 [套用]"
        （2026-09-28 探针复现）。现在 _refresh_proc 收尾回调刷一次。
        """
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files], select=False)
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                gui_file_dock._open_entry_view(
                    w, w.file_list.raw_group.child(0), explicit=True)
                key = f"1D|{files[0]}"
                self.assertTrue(_wait_until(
                    lambda: key in w.plot_docks
                    and getattr(w.plot_docks[key], "last_tth", None) is not None))
            combo = w.params["背景扣除模式"]
            combo.setCurrentIndex(combo.findData("auto"))
            QApplication.processEvents()      # 不再手动调 _sync_bg_rows
            self.assertGreaterEqual(
                w.recipe_combo.findData("local"), 0,
                "调完参数下拉里就该有「本图配方」")
            self.assertIn("本图配方", w.bg_prov_lbl.text(),
                          "来处那行也该跟着新鲜")
        finally:
            w.close()

    def _bg_product_with_source(self, w, path, values=None):
        """造一对真有血缘的产物：1D 曲线 + 从它派生的处理产物，返回产物键。"""
        kw = _kw_of(w)
        tth = np.linspace(kw["tth_min"], kw["tth_max"], 5)
        inten = np.asarray(values if values is not None
                           else [9.0, 6.0, 3.0, 2.0, 1.0], dtype=float)
        stage_cache.store_1d(path, tth, inten, **kw)
        key = _store_product(w, path, kind="bg")   # 拿它自己的键（别扫盘猜）
        return tth, inten, key

    def test_recipe_on_a_bg_product_redoes_from_the_source_curve(self):
        """产物面板上套配方 = **从它底下的原始曲线重做**（不拒绝、不二次相减）。

        用户 2026-09-28 第 3 条："套用别的配方的处理图不能二次处理了，修正"。
        旧版这里一句拒绝（"这一条是处理产物…配方套不上去"）——想换套参数重做
        在界面上无路可走。现在就地换成源曲线再套：面板此后就是"这个文件的一条
        1D 曲线"，配方照常生效，产物也照常能 [采用这份结果]。
        """
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files], select=False)
            tth, inten, key = self._bg_product_with_source(w, files[0])
            src = gui_sources.make_source(files[0], kind=gui_sources.BG,
                                          key=key)
            panel_key = gui_views._open_product_panel(w, src)
            dock = w.plot_docks[panel_key]
            gui_panel_state._set_focus(w, panel_key, dock.windowTitle())
            QApplication.processEvents()
            self.assertIsNotNone(dock.panel_source, "前提：这是产物面板")
            recipe_store.save("R", {"mode": "auto", "window_deg": 0.3,
                                    "anchors": [[2.0, 100.0]]})
            gui_app._recipe_combo_fill(w)     # 下拉要刷一次才看得到"已保存：R"
            w.recipe_combo.setCurrentIndex(w.recipe_combo.findData("saved|R"))
            self.assertEqual(w.recipe_combo.currentData(), "saved|R",
                             "前提：下拉里选中了那份配方")
            gui_app._apply_selected_recipe(w)
            QApplication.processEvents()
            log = w.log_text.toPlainText()
            self.assertIn("已换成它底下的原始 1D 曲线", log)
            self.assertIn("已套用配方", log, "配方真的套上去了")
            self.assertIsNone(dock.panel_source, "面板不再是「产物面板」")
            self.assertTrue(np.allclose(np.asarray(dock.last_intensity,
                                                   dtype=float), inten),
                            "画的是**源曲线**，不是在扣完的曲线上再扣一遍")
            self.assertEqual((dock.params_snapshot or {}).get("背景扣除模式"),
                             "auto", "配方把模式也带过来了")
        finally:
            w.close()

    def test_recipe_is_refused_when_the_source_curve_is_gone(self):
        """找不到源曲线时仍然拒绝，并说清为什么（不静默、不硬套）。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files], select=False)
            key = _store_product(w, files[0], kind="bg")   # 没有 1D、也没台账
            src = gui_sources.make_source(files[0], kind=gui_sources.BG, key=key)
            panel_key = gui_views._open_product_panel(w, src)
            dock = w.plot_docks[panel_key]
            gui_panel_state._set_focus(w, panel_key, dock.windowTitle())
            QApplication.processEvents()
            recipe_store.save("R", {"mode": "auto", "window_deg": 0.3,
                                    "anchors": [[2.0, 100.0]]})
            gui_app._recipe_combo_fill(w)     # 下拉要刷一次才看得到"已保存：R"
            w.recipe_combo.setCurrentIndex(w.recipe_combo.findData("saved|R"))
            self.assertEqual(w.recipe_combo.currentData(), "saved|R",
                             "前提：下拉里选中了那份配方")
            gui_app._apply_selected_recipe(w)
            QApplication.processEvents()
            log = w.log_text.toPlainText()
            self.assertIn("换配方重做不了", log)
            self.assertIn("打开原始条目重出图再处理", log)
            self.assertEqual((dock.params_snapshot or {}).get("背景扣除模式"),
                             "off", "拒绝时产物面板的模式不许被改掉")
        finally:
            w.close()

    def test_placeholder_while_computing(self):
        """数据还没到时面板写着"正在计算…"，不是一块空白。

        用户 2026-09-27："重新打开原始文件…有的是空白的图"——那是还在后台
        排队算；写一句占位，算完 ax.clear() 自然冲掉。
        """
        w = create_window()
        try:
            files = _tmp_files(1)

            def slow(*_a, **_k):
                time.sleep(2.0)          # 让"计算中"这一态能被看到
                return _fake_compute("", {}, 0)

            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=slow):
                w.add_files([str(p) for p in files], select=False)
                gui_file_dock._open_entry_view(w, w.file_list.raw_group.child(0),
                                               explicit=True)
                QApplication.processEvents()
                ax = _axes(w, "1D", str(files[0]))
                self.assertIn("正在计算…", [t.get_text() for t in ax.texts])
        finally:
            w.close()


class TestGroupSelectionBadge(unittest.TestCase):
    """折叠的组名后缀"（n/m 选中）"（用户 2026-09-28："在文件栏加上个当前
    有多少被选中的显示，仅在文件被折叠时显示"）。

    起因是"勾选集比想的大"那类事故（162 条 = 81 原始 + 81 处理后）：组
    折叠着看不见里面的对号，而组上又是不亮的（半勾没有专属状态，见
    _group_state），于是"里面还留着一整批勾"没有任何线索。徽标只在**折叠**
    时出现——展开时对号就在眼前，不重复写数字。
    """

    def test_badge_appears_only_when_collapsed(self):
        w = create_window()
        try:
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
            raw = w.file_list.raw_group
            raw.setExpanded(True)
            QApplication.processEvents()
            self.assertEqual(raw.text(0), "原始数据", "展开时不带数字")
            raw.setExpanded(False)
            QApplication.processEvents()
            self.assertEqual(raw.text(0), "原始数据（2/2 选中）")
            raw.setExpanded(True)
            QApplication.processEvents()
            self.assertEqual(raw.text(0), "原始数据", "再展开，数字退场")
        finally:
            w.close()

    def test_badge_follows_checks_and_keeps_them(self):
        """折叠着改勾选：数字跟上，且**别的对号一个都不许掉**。

        这条用例钉的是一个静默陷阱：改组名文字会发 itemChanged，而文件坞
        把它当成"组被勾了/取消了"、会把组的状态铺给全部子项——组此刻通常
        是空格，于是"写一个徽标"就能把里面的对号全清掉。实现用
        _check_syncing 罩住了那一步（见 _refresh_group_badges）。
        """
        w = create_window()
        try:
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif",
                            "data/s3.tif"])
            raw = w.file_list.raw_group
            raw.setExpanded(False)
            QApplication.processEvents()
            self.assertEqual(raw.text(0), "原始数据（3/3 选中）")
            raw.child(1).setCheckState(0, Qt.Unchecked)
            QApplication.processEvents()
            self.assertEqual(raw.text(0), "原始数据（2/3 选中）")
            self.assertEqual(
                [raw.child(i).checkState(0) for i in range(3)],
                [Qt.Checked, Qt.Unchecked, Qt.Checked],
                "改徽标不许动对号")
            raw.setCheckState(0, Qt.Checked)     # 整组再勾上
            QApplication.processEvents()
            self.assertEqual(raw.text(0), "原始数据（3/3 选中）")
        finally:
            w.close()

    def test_product_group_badge(self):
        """产物组的名字里不再带总数（"1D 产物 (1)"）——那个数字跟徽标里的
        分母重复，同一行两个数字分不清谁是谁。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files], select=True)
            _store_product(w, files[0])
            w.refresh_groups()
            group = _group_by_text(w, "1D 产物")
            base = group.text(0)
            # 组名 = 家族名 + 这一组按什么算的（2026-09-28 第 4 条：按积分
            # 设置分组），本测试只关心**名字里不带总数**、徽标另有其数
            self.assertTrue(base.startswith("1D 产物 2θ "), base)
            self.assertNotIn("(1)", base, "名字里不再带总数")
            group.setExpanded(False)
            QApplication.processEvents()
            self.assertEqual(group.text(0), f"{base}（0/1 选中）",
                             "产物条目默认不勾（导入不勾选那套口径）")
            group.child(0).setCheckState(0, Qt.Checked)
            QApplication.processEvents()
            self.assertEqual(group.text(0), f"{base}（1/1 选中）")
        finally:
            w.close()


class TestOpenRowMarks(unittest.TestCase):
    """文件栏的"正在打开"记号（用户 2026-09-28 第 5 条）。

    用户的原话："文件栏背景加深代表这个图正在打开，选中未选中仅用框内的标志
    表示"。改之前整行深色是 Qt 的"当前行"高亮（还被限制成"只能在勾选着的行
    上"），而哪张图开着在文件栏里**完全看不出来**。现在：

      * 有面板活着的条目 = 整行淡色 + 左侧小点（refresh_open_marks）；
      * 勾没勾只看框里的小勾（Qt 的选中底色已在 FileTree 的样式里取消）；
      * 两个记号互不影响——取消勾选不该把"开着"的记号抹掉，反过来也一样。
    """

    def _marked(self, item):
        return (item.background(0).color().isValid()
                and item.background(0).color().alpha() > 0
                and not item.icon(0).isNull())

    def test_row_is_marked_while_a_panel_is_open(self):
        w = create_window()
        try:
            files = _tmp_files(2)
            w.add_files([str(p) for p in files], select=False)
            first = w.file_list.raw_group.child(0)
            second = w.file_list.raw_group.child(1)
            self.assertFalse(self._marked(first), "还没开图：不该有记号")
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                gui_file_dock._open_entry_view(w, first, explicit=True)
                self.assertTrue(_wait_until(
                    lambda: "1D|" + str(files[0]) in w.plot_docks))
                QApplication.processEvents()
                self.assertTrue(self._marked(first), "开着的那条要有记号")
                self.assertFalse(self._marked(second), "没开的条目不许被带上")
                # 取消勾选不影响"开着"的记号（两种记号各管各的）
                first.setCheckState(0, Qt.Checked)
                QApplication.processEvents()
                self.assertTrue(self._marked(first))
                first.setCheckState(0, Qt.Unchecked)
                QApplication.processEvents()
                self.assertTrue(self._marked(first),
                                "勾选状态与「正在打开」是两回事")
                # 关掉面板 → 记号收回
                gui_app._close_panel(w, "1D|" + str(files[0]))
                QApplication.processEvents()
                self.assertFalse(self._marked(first), "关了就收回记号")
        finally:
            w.close()

    def test_the_current_row_keeps_its_open_tint(self):
        """当前行也要看得见"正在打开"的淡色（2026-09-30 修的尾巴）。

        根因：原来那条样式表（`QTreeView::item:selected { background:
        transparent }`）把条目**自己**的背景刷子也一起盖掉了——刚从别处点
        过来的那一行只剩左侧小点、淡色没了，而它恰恰是最该被看见的一行。
        现在绘制走 _RowPaintDelegate：交基类前摘掉"选中"状态位（Qt 的选中
        深色不画），条目自己的刷子由代理先铺一遍。

        这里**真的渲染一遍抓像素**：数据槽里的刷子查不出这种问题——出事
        的正是"样式表/绘制路径"这一层（旧样式表在时，`_marked()` 照样为真）。
        """
        w = create_window()
        try:
            files = _tmp_files(2)
            w.add_files([str(p) for p in files], select=False)
            tree = w.file_list
            first = tree.raw_group.child(0)
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                gui_file_dock._open_entry_view(w, first, explicit=True)
                self.assertTrue(_wait_until(
                    lambda: "1D|" + str(files[0]) in w.plot_docks))
            QApplication.processEvents()
            w.show()
            tree.setCurrentItem(first)          # 当前行 = 开着的那条
            for _ in range(5):
                QApplication.processEvents()
            img = tree.viewport().grab().toImage()
            rect = tree.visualItemRect(first)
            y = rect.center().y()
            colors = {img.pixelColor(x, y).name()
                      for x in (rect.center().x(), rect.right() - 3)}
            self.assertIn(gui_file_dock._OPEN_ROW_BG, colors,
                          "当前行也要铺「正在打开」的淡色")
        finally:
            # 本用例 show() 过（要真渲染才能抓像素）→ 关窗会弹"未保存的图"
            # 确认框；离屏下没人点，必须按脚本关（同 TestCloseFlow 的做法）
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_product_rows_get_marked_too(self):
        """产物条目（「处理后」）开着面板时同样有记号。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files], select=False)
            key = _store_product(w, files[0], kind="bg")
            # 处理产物在文件栏里靠**台账**列出来（1D 产物才是扫盘）
            stage_cache.record_batch(
                "bg", "20260928-235959-test01", label="处理后 09-28 23:59（测试）",
                items=[(files[0], key)], config=w.config_name,
                npt=int(w.params["输出点数"].value()), tth_min=1.0,
                tth_max=8.0, settings={}, note="1 个文件")
            w.refresh_groups()
            QApplication.processEvents()
            item = None
            for src in gui_sources.all_sources(w):
                if src.kind == gui_sources.BG:
                    item = src.item
            self.assertIsNotNone(item, "前提：文件栏里有处理产物条目")
            self.assertFalse(self._marked(item))
            gui_views._open_product_panel(
                w, gui_sources.make_source(files[0], kind=gui_sources.BG,
                                           key=key))
            QApplication.processEvents()
            self.assertTrue(self._marked(item), "产物条目也要有记号")
        finally:
            w.close()


class TestOpenFiguresGroup(unittest.TestCase):
    """「打开的图」：屏幕上开着的图在文件栏里也有一行（用户 2026-10-01 第 5 条）。

    用户原话："画图出的也放进文件区……对应的主要是现在在左边文件区看不到的
    内容，比如对比、热图、瀑布之类"。两条规矩（用户同日定）：**校准图不放入**、
    这些行**不给勾**（"看的东西"不是数据，不进勾选集）。
    """

    def _open_1d(self, w, name="data/fake_b.tif"):
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, [name])
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, "1D", name).lines) > 0))
        return _dock(w, "1D", name)

    def _rows(self, w):
        group = _group_by_text(w, "打开的图")
        if group is None:
            return []
        return [group.child(i) for i in range(group.childCount())]

    def test_open_figure_gets_a_row_and_leaves_with_the_panel(self):
        w = create_window()
        try:
            self.assertIsNone(_group_by_text(w, "打开的图"), "开局没有这一组")
            dock = self._open_1d(w)
            group = _group_by_text(w, "打开的图")
            self.assertIsNotNone(group, "出图之后该有「打开的图」")
            self.assertEqual([r.text(0) for r in self._rows(w)],
                             [dock.windowTitle()])
            row = self._rows(w)[0]
            self.assertEqual(row.data(0, gui_file_dock.PANEL_KEY_ROLE),
                             "1D|data/fake_b.tif")
            # 位置：紧跟「原始数据」（产物分组照旧排在后面）
            self.assertIs(w.file_list.groups()[0], group)
            # 关掉这张图 → 行没了、组也没了（"开着的图"是它的定义）
            gui_panels._close_panel(w, "1D|data/fake_b.tif")
            QApplication.processEvents()
            self.assertIsNone(_group_by_text(w, "打开的图"))
        finally:
            w.close()

    def test_rows_are_not_checkable_and_stay_out_of_the_selection(self):
        """这一组不参与勾选：行/组都没有对号槽，[全选]/[全不选] 也不碰它。"""
        w = create_window()
        try:
            self._open_1d(w)
            group = _group_by_text(w, "打开的图")
            row = self._rows(w)[0]
            self.assertFalse(gui_file_dock._is_checkable(group))
            self.assertFalse(gui_file_dock._is_checkable(row))
            self.assertEqual(len(gui_sources.checked_sources(w)), 1,
                             "勾选集里只有那条原始数据")
            for item in (group, row):        # 全选/**全不选**都不许给它长对号
                gui_file_dock._set_checks(w, [(item, True)])
                self.assertEqual(item.checkState(0), Qt.Unchecked)
            w.select_all_btn.click()          # [全选（原始数据）]
            QApplication.processEvents()
            self.assertEqual(row.checkState(0), Qt.Unchecked)
            gui_file_dock._sync_group_states(w)   # 组态重算也不许碰它
            self.assertEqual(group.checkState(0), Qt.Unchecked)
        finally:
            w.close()

    def test_aggregate_views_without_a_file_row_are_listed(self):
        """对比/热图这类"单槽多文件"视图没有对应条目——这一组正是它们唯一
        看得见的地方（用户点名："比如对比、热图、瀑布之类"）。"""
        w = create_window()
        try:
            _prime_1d(w, ["data/fake_a.tif", "data/fake_b.tif"],
                      stub=_fake_compute)      # 两张 1D 面板（栅栏前置）
            w.compare_btn.click()              # [对比] = 单槽多文件视图
            self.assertTrue(_wait_until(lambda: any(
                k.startswith("对比") for k in w.plot_docks)))
            keys = [r.data(0, gui_file_dock.PANEL_KEY_ROLE)
                    for r in self._rows(w)]
            self.assertTrue(any(k.startswith("对比") for k in keys),
                            f"对比面板也该列出来：{keys}")
            self.assertEqual(sum(1 for k in keys if "|" in k), 2,
                             f"两张 1D 图也在：{keys}")
        finally:
            w.close()

    def test_double_click_raises_the_panel_and_sets_the_focus(self):
        w = create_window()
        try:
            self._open_1d(w)
            w.focus_panel = None
            gui_file_dock._open_entry_view(w, self._rows(w)[0])
            self.assertEqual(w.focus_panel, "1D|data/fake_b.tif",
                             "双击可提到最前面 + 成为编辑对象")
        finally:
            w.close()

    def test_calibration_panel_is_not_listed(self):
        """校准图不放入（用户 2026-10-01）。"""
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
                QApplication.processEvents()
                self.assertIsNotNone(w.calib_dock)
            self.assertIsNone(_group_by_text(w, "打开的图"),
                              "校准图不进「打开的图」")
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()


class TestProductGroups(unittest.TestCase):
    """文件栏的"阶段文件夹"：① 1D 产物 ② 每次 [批量处理] 一组。

    勾组 = 整组全选（半勾表示只勾了一部分）；产物条目出图/对比直接读
    产物（不重算、不再扣背景）；原始数据的对号语义一点没变。
    """

    def setUp(self):
        # 台账是模块级共享的临时缓存根：每个用例自己清一份，断言才数得准
        stage_cache.write_batches("bg", [])

    def test_group_check_propagates_both_ways(self):
        """勾组 → 组里全勾；取消一个 → 组**不再亮**（不是半勾）；全取消 → 不勾。

        用户 2026-09-26："文件栏有三种状态，对号、横线、空格。横线和空
        重复了，留空格"——半勾已从模型里去掉，勾一部分时组就是空格。
        """
        w = create_window()
        try:
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif",
                            "data/s3.tif"])
            raw = w.file_list.raw_group
            raw.setCheckState(0, Qt.Checked)
            QApplication.processEvents()
            states = [raw.child(i).checkState(0) for i in range(3)]
            self.assertEqual(states, [Qt.Checked] * 3, "勾组 = 组里全勾")
            self.assertIn("已勾选整组 原始数据", w.log_text.toPlainText())
            raw.child(0).setCheckState(0, Qt.Unchecked)
            QApplication.processEvents()
            self.assertEqual(raw.checkState(0), Qt.Unchecked,
                             "只勾一部分 → 组是空格（没有半勾这一态）")
            raw.child(0).setCheckState(0, Qt.Checked)
            QApplication.processEvents()
            self.assertEqual(raw.checkState(0), Qt.Checked, "补齐 → 组亮")
            for i in range(3):
                raw.child(i).setCheckState(0, Qt.Unchecked)
            QApplication.processEvents()
            self.assertEqual(raw.checkState(0), Qt.Unchecked)
        finally:
            w.close()

    def test_oned_products_are_grouped_by_their_settings(self):
        """**按积分设置分组**：两组 theta 不同的 1D 各自一组，不掺在一起。

        用户 2026-09-28 两条一起定的：
          * 第 4 条："同一批原始数据出两组 theta 不同的 1d 图会掺在一起，
            应该有分组区分"——改之前全库只有一个「1D 产物」组，靠行尾那截
            `1D（2θ 1–8°）` 区分，而且那截尾巴是**相对当前设置**算的（改一下
            坞顶的范围，尾巴就从 A 行跳到 B 行，像两组换了身份）；
          * "处理后的图把 1d 图的文件给替换了…删除缓存只能用户自己删"——
            盘上一份没少，是那颗组按**当前设置**正查键、范围一改整组空掉。
        现在：扫盘（磁盘上有什么就显示什么）+ 一组一套设置 + 组名写着是哪
        一套；当前设置那一组排最前、名字后面标「（当前设置）」。
        """
        w = create_window()
        try:
            files = _tmp_files(2)
            w.add_files([str(p) for p in files], select=False)
            kw = _kw_of(w)
            # 按"旧范围"（1–7°）存一份产物——与窗口当前的 1–8° 不同
            keys = [Path(stage_cache.store_1d(
                f, np.linspace(1.0, 7.0, 5), np.array([1.0, 2.0, 3.0, 2.0, 1.0]),
                config=kw["config"], npt=kw["npt"],
                tth_min=1.0, tth_max=7.0)).stem for f in files]
            w.refresh_groups()
            group = _group_by_text(w, "1D 产物")
            self.assertIsNotNone(group, "旧范围的产物也必须列出来")
            self.assertIn("2θ 1–7°", group.text(0), "组名里写着它是按什么算的")
            self.assertNotIn("当前设置", group.text(0),
                             "它不是坞顶当前那一套（1–8°），不该标「当前设置」")
            self.assertEqual(group.childCount(), 2)
            leaf = group.child(0)
            self.assertIn("1D", leaf.text(0), "组内同名同类：范围由组名交代")
            self.assertIn("当前设置：", group.toolTip(0),
                          "悬停提示里写出当前设置，好知道该按什么重算")
            src = gui_sources.source_of(leaf)
            self.assertIn(src.key, keys, "挂着的是那一份产物的键（点开读它）")
            # 再按当前范围（1–8°）算一批 → **第二组**冒出来，两组并存
            for f in files:
                stage_cache.store_1d(
                    f, np.linspace(1.0, 8.0, 5),
                    np.array([1.0, 2.0, 3.0, 2.0, 1.0]),
                    config=kw["config"], npt=kw["npt"],
                    tth_min=1.0, tth_max=8.0)
            w.refresh_groups()
            titles = [g.text(0) for g in w.file_list.groups()
                      if "1D 产物" in g.text(0)]
            self.assertEqual(len(titles), 2, f"该分成两组：{titles}")
            self.assertIn("1D 产物 2θ 1–8°（当前设置）", titles,
                          "当前那一组排最前并标明")
            self.assertEqual(titles[0], "1D 产物 2θ 1–8°（当前设置）",
                             "当前那一组排最前（2026-10-08 修：排序键漏了 not）")
            self.assertIn("1D 产物 2θ 1–7°", titles)
        finally:
            w.close()

    def test_products_from_an_older_algorithm_are_flagged_not_hidden(self):
        """换过背景算法之后，旧产物**照样列出来**，只在名字里标"不可信"。

        2026-09-27：锚点外推失控算了 68 份歪曲线（−3000 量级）——那批不能
        再用来下结论（判据 = 元数据里的 algo 版本）。但**不能因此把它们藏
        起来**：用户 2026-09-28 定的规矩是"缓存里有什么就显示什么，删不删
        由我自己决定"（他刚被"东西怎么会不见"咬过一次）。
        """
        w = create_window()
        try:
            files = _tmp_files(2)
            w.add_files([str(p) for p in files], select=False)
            key = _store_product(w, files[0], kind="bg")
            stage_cache.record_batch(
                "bg", "20260101-000000-deadbe",
                label="处理后 01-01 00:00（自动基线）",
                items=[(files[0], key)], config=w.config_name, npt=1000)
            w.refresh_groups()
            group = _group_by_text(w, "处理产物 01-01")
            self.assertIsNotNone(group, "前提：产物该露面")
            # 模拟"换了背景算法"：版本号 +1 → 已存的产物立刻变成旧的
            with mock.patch.object(stage_cache, "BG_ALGO_VERSION",
                                   stage_cache.BG_ALGO_VERSION + 1):
                w.refresh_groups()
                group = _group_by_text(w, "处理产物 01-01")
                self.assertIsNotNone(group, "旧产物也要照样列着（不藏）")
                leaf = group.child(0)
                self.assertIn("旧算法", leaf.text(0), "名字里要标出来")
                self.assertIn("不可信", leaf.toolTip(0))
            self.assertIn("旧算法", w.log_text.toPlainText(), "日志说清怎么回事")
            self.assertTrue(stage_cache.has_key("bg", key),
                            "产物文件不删（删不删用户说了算）")
        finally:
            w.close()

    def test_one_d_group_appears_for_cached_files(self):
        """算过 1D 的文件进「1D 产物」组；没算过的不进。"""
        w = create_window()
        try:
            files = _tmp_files(2)
            w.add_files([str(p) for p in files], select=True)
            self.assertIsNone(_group_by_text(w, "1D 产物"), "还没有产物")
            _store_product(w, files[0])
            w.refresh_groups()
            group = _group_by_text(w, "1D 产物")
            self.assertIsNotNone(group)
            self.assertEqual(group.childCount(), 1)
            self.assertIn(files[0].name, group.child(0).text(0))
            self.assertIn("· 1D", group.child(0).text(0))
        finally:
            w.close()

    def test_product_item_draws_from_cache_without_integrating(self):
        """产物条目出 1D 图 = 直接读盘画线（积分函数一次都不该被调）。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files], select=True)
            key = _store_product(w, files[0], values=[7.0, 8.0, 9.0])
            w.refresh_groups()
            group = _group_by_text(w, "1D 产物")
            group.child(0).setCheckState(0, Qt.Checked)
            w.file_list.raw_group.setCheckState(0, Qt.Unchecked)
            QApplication.processEvents()
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute) as compute:
                _open_view(w, "1D")
                QApplication.processEvents()
                self.assertEqual(compute.call_count, 0, "产物不该重新积分")
            dock = w.plot_docks[f"1D|1d#{key}"]
            lines = _axes(w, "1D", "1d#" + key).lines
            self.assertEqual(len(lines), 1)
            self.assertEqual(list(lines[0].get_ydata()), [7.0, 8.0, 9.0])
            self.assertIn("直接读取，不重算", w.log_text.toPlainText())
            # 1D 产物 = 那条原始积分曲线（不是"已完成"的东西）：不强制「不扣」，
            # 模式照默认起步，锚点/自动基线和原始文件一样能用
            self.assertNotIn("面板背景扣除已置「不扣」", w.log_text.toPlainText())
            self.assertEqual(dock.params_snapshot["背景扣除模式"], "off",
                             "默认就是不扣（面板快照的出厂值），不是被强制的")
        finally:
            w.close()

    def test_bg_product_panel_forces_background_off(self):
        """处理产物面板：强制「不扣」（已经扣过，再扣就是二次相减）。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files])
            key = _store_product(w, files[0], kind="bg", values=[5.0, 6.0, 7.0])
            stage_cache.record_batch("bg", "force-off",
                                     label="扣背景 09-25 07:00（空扫相减）",
                                     items=[(files[0], key)], **_kw_of(w),
                                     settings={"mode": "blank"})
            w.refresh_groups()
            _group_by_text(w, "扣背景 09-25 07:00").child(0).setCheckState(
                0, Qt.Checked)
            QApplication.processEvents()
            _open_view(w, "1D")
            QApplication.processEvents()
            dock = w.plot_docks[f"1D|bg#{key}"]
            self.assertEqual(dock.params_snapshot["背景扣除模式"], "off")
            self.assertIn("面板背景扣除已置「不扣」", w.log_text.toPlainText())
        finally:
            w.close()

    def test_product_item_on_2d_view_is_skipped_with_log(self):
        """产物条目点了 [2D]：点名跳过（1D 曲线没有 2D 视图）。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files], select=True)
            _store_product(w, files[0])
            w.refresh_groups()
            group = _group_by_text(w, "1D 产物")
            group.child(0).setCheckState(0, Qt.Checked)
            w.file_list.raw_group.setCheckState(0, Qt.Unchecked)
            QApplication.processEvents()
            _open_view(w, "2D")
            QApplication.processEvents()
            self.assertIn("跳过 1 个产物条目", w.log_text.toPlainText())
        finally:
            w.close()

    def test_compare_uses_background_group(self):
        """勾一整组处理产物 → [对比] 直接画出那两条（不重算、不再处理）。"""
        w = create_window()
        try:
            files = _tmp_files(2)
            w.add_files([str(p) for p in files], select=True)
            keys = [_store_product(w, p, kind="bg", values=[i, i, i])
                    for i, p in enumerate(files, start=1)]
            stage_cache.record_batch(
                "bg", "probe-batch",
                label="扣背景 09-25 14:03（锚点 1 个，窗口 2°）",
                items=[(p, k) for p, k in zip(files, keys)], **_kw_of(w),
                settings={"mode": "anchor"})
            w.refresh_groups()
            group = _group_by_text(w, "扣背景 09-25 14:03")
            self.assertIsNotNone(group)
            group.setCheckState(0, Qt.Checked)
            w.file_list.raw_group.setCheckState(0, Qt.Unchecked)
            QApplication.processEvents()
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute) as compute:
                w.compare_btn.click()
                QApplication.processEvents()
                self.assertEqual(compute.call_count, 0, "产物不该重新积分")
            # 单槽：对比面板的键就是 "对比"（不再带文件串），直接取那张
            ax = gui_panel_state._content(w.plot_docks["对比"]).axes_1d
            self.assertEqual(len(ax.lines), 2)
            labels = [ln.get_label() for ln in ax.lines]
            self.assertTrue(all("处理" in lb for lb in labels), labels)
            self.assertIn("处理产物", w.log_text.toPlainText())
        finally:
            w.close()

    def test_delete_handles_products_too(self):
        """[删除] 与右键同一套口径：勾了产物也真删（原始数据只从列表移除）。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files], select=True)
            _store_product(w, files[0])
            w.refresh_groups()
            w.file_list.raw_group.setCheckState(0, Qt.Unchecked)
            _group_by_text(w, "1D 产物").child(0).setCheckState(0, Qt.Checked)
            QApplication.processEvents()
            w.findChild(QPushButton, "delete_btn").click()
            self.assertEqual(w.file_list.count(), 1, "原始数据还在列表里")
            self.assertIn("已删除 1 条产物", w.log_text.toPlainText())
            self.assertIsNone(_group_by_text(w, "1D 产物"), "产物分组跟着收")
            self.assertEqual(stage_cache.list_batches("bg"), [])
            # 原始数据一起勾上时：从列表移除（硬盘上的文件不动），与产物各记一行
            w.file_list.raw_group.setCheckState(0, Qt.Checked)
            QApplication.processEvents()
            w.findChild(QPushButton, "delete_btn").click()
            self.assertEqual(w.file_list.count(), 0)
            self.assertIn("已从文件栏移除 1 个文件", w.log_text.toPlainText())
            self.assertIn("硬盘上的文件未改动", w.log_text.toPlainText())
        finally:
            w.close()

    def test_delete_group_menu_drops_products(self):
        """右键产物分组 → 删掉这一组（台账 + 盘上的产物一起没）。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files], select=True)
            key = _store_product(w, files[0], kind="bg")
            stage_cache.record_batch(
                "bg", "menu-batch", label="扣背景 09-25 09:00（空扫相减）",
                items=[(files[0], key)], **_kw_of(w),
                settings={"mode": "blank"})
            w.refresh_groups()
            group = _group_by_text(w, "扣背景 09-25 09:00")
            self.assertIsNotNone(group)
            # 没显示的窗口不弹模态菜单（会等不到人点，卡死套件）——这一步
            # 单独守；删除本身走 drop_product_group（菜单确认后调同一个）
            gui_file_dock._entry_menu(w, group)
            self.assertEqual(w.file_list.groups()[-1].text(0),
                             group.text(0), "无头环境不弹菜单，也不该删掉什么")
            gui_file_dock.drop_product_group(w, group)
            QApplication.processEvents()
            self.assertIsNone(_group_by_text(w, "扣背景 09-25 09:00"))
            self.assertIn("已删除产物分组", w.log_text.toPlainText())
            self.assertFalse(stage_cache.has_key("bg", key), "盘上的产物也删了")
        finally:
            w.close()

    def test_delete_one_d_group_removes_its_products(self):
        """右键「1D 产物」组 → 那一组退出盘（成员文件 + 分组一起消失）。"""
        w = create_window()
        try:
            files = _tmp_files(2)
            w.add_files([str(p) for p in files])
            keys = [_store_product(w, p) for p in files]
            w.refresh_groups()
            group = _group_by_text(w, "1D 产物")
            self.assertIsNotNone(group)
            npz = [stage_cache.CACHE_ROOT / "1d" / f"{k}.npz" for k in keys]
            self.assertTrue(all(p.exists() for p in npz))
            n = gui_file_dock.drop_product_group(w, group)
            self.assertEqual(n, 2)
            self.assertTrue(all(not p.exists() for p in npz), "盘上的产物删掉了")
            self.assertIsNone(_group_by_text(w, "1D 产物"), "分组也没了")
            self.assertIn("已删除产物分组", w.log_text.toPlainText())
        finally:
            w.close()

    def test_delete_single_product_item(self):
        """右键单条产物 → 只删这一条，同组别的条目还在。"""
        w = create_window()
        try:
            files = _tmp_files(2)
            w.add_files([str(p) for p in files])
            keys = [_store_product(w, p) for p in files]
            w.refresh_groups()
            group = _group_by_text(w, "1D 产物")
            victim, survivor = group.child(0), group.child(1)
            n = gui_file_dock.drop_product_item(w, victim)
            self.assertEqual(n, 1)
            self.assertFalse((stage_cache.CACHE_ROOT / "1d"
                              / f"{keys[0]}.npz").exists())
            self.assertTrue((stage_cache.CACHE_ROOT / "1d"
                             / f"{keys[1]}.npz").exists())
            after = _group_by_text(w, "1D 产物")
            self.assertIsNotNone(after, "还有一条产物，分组要留着")
            self.assertEqual(after.childCount(), 1)
            self.assertIn(survivor.text(0), after.child(0).text(0))
            self.assertIn("已删除产物", w.log_text.toPlainText())
        finally:
            w.close()

    def test_delete_single_bg_item_prunes_ledger(self):
        """删 bg 的单条 → 产物与台账条目一起没；那一批空了就整条消失。"""
        w = create_window()
        try:
            files = _tmp_files(2)
            w.add_files([str(p) for p in files])
            keys = [_store_product(w, p, kind="bg") for p in files]
            stage_cache.record_batch(
                "bg", "two-items", label="扣背景 09-25 06:00（空扫相减）",
                items=[(files[0], keys[0]), (files[1], keys[1])], **_kw_of(w),
                settings={"mode": "blank"})
            w.refresh_groups()
            group = _group_by_text(w, "扣背景 09-25 06:00")
            self.assertEqual(group.childCount(), 2)
            self.assertEqual(gui_file_dock.drop_product_item(w, group.child(0)), 1)
            batch = stage_cache.list_batches("bg")[0]
            self.assertEqual(len(batch["items"]), 1, "台账里也摘掉了一条")
            # 删掉剩下那条 → 这一批空了，整条从台账消失
            left = _group_by_text(w, "扣背景 09-25 06:00")
            self.assertEqual(left.childCount(), 1)
            gui_file_dock.drop_product_item(w, left.child(0))
            self.assertEqual(stage_cache.list_batches("bg"), [])
            self.assertIsNone(_group_by_text(w, "扣背景 09-25 06:00"))
        finally:
            w.close()

    def test_product_menu_ignores_raw_items(self):
        """右键原始数据（组或条目）不弹产物菜单：它们的删除入口是 [删除]。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            add_checked(w, [str(p) for p in files])
            # 无头环境本来就不弹菜单；这里守的是"原始数据不进这条路"
            self.assertEqual(gui_file_dock.drop_product_group(
                w, w.file_list.raw_group), 0)
            self.assertEqual(gui_file_dock.drop_product_item(
                w, w.file_list.item(0)), 0)
            gui_file_dock._entry_menu(w, w.file_list.item(0))   # 不炸即可
            self.assertEqual(w.file_list.count(), 1)
        finally:
            w.close()

    def test_remove_from_list_leaves_the_file_alone(self):
        """右键「从列表移除」：只动列表，硬盘上的文件一个字节都不碰。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            add_checked(w, [str(p) for p in files])
            size_before = files[0].stat().st_size
            gui_file_dock._remove_from_list(w, [w.file_list.item(0)])
            self.assertEqual(w.file_list.count(), 0)
            self.assertTrue(files[0].exists(), "原始文件必须还在")
            self.assertEqual(files[0].stat().st_size, size_before)
            log = w.log_text.toPlainText()
            self.assertIn("已从文件栏移除 1 个文件", log)
            self.assertIn("硬盘上的文件未改动", log)
        finally:
            w.close()

    def test_clear_cache_action_empties_products(self):
        """「删除所有缓存」：**点一下就删**（2026-10-14 起）。

        简化史见 gui_file_dock.ask_clear_cache：确认框（10-13 之前，
        这台机器上它好几秒才画上屏、"取消"吃过三回冤枉）→ 日志提醒 +
        再点一次（10-13）→ 一击即删（10-14 用户："删除全部缓存，改成
        就一次"）。缓存只是重算得回来的产物，删掉代价 = 下次出图重积分。
        """
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files])
            _store_product(w, files[0])
            w.refresh_groups()
            self.assertIsNotNone(_group_by_text(w, "1D 产物"))
            gui_file_dock.ask_clear_cache(w)      # 一下就删
            self.assertIsNone(_group_by_text(w, "1D 产物"))
            log = w.log_text.toPlainText()
            self.assertIn("已删除所有缓存", log)
            self.assertNotIn("再点一次 [删除所有缓存…]", log,
                             "一击即删后不该再出现'再点一次'的提示")
        finally:
            w.close()

    def test_export_specific_sources_ignores_checks(self):
        """右键「导出这一条」：导出给定来源，不改动勾选状态。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files])      # 一个都不勾
            key = _store_product(w, files[0], kind="bg", values=[7.0, 8.0, 9.0])
            stage_cache.record_batch("bg", "export-by-menu",
                                     label="处理后 09-25 05:00（空扫相减）",
                                     items=[(files[0], key)], **_kw_of(w),
                                     settings={"mode": "blank"})
            w.refresh_groups()
            src = gui_sources.source_of(
                _group_by_text(w, "处理产物 09-25 05:00").child(0))
            outdir = Path(tempfile.mkdtemp())
            with mock.patch.object(gui_export, "_build_export_dialog",
                                   return_value={"dir": outdir,
                                                 "suffix": ".txt",
                                                 "csv": False, "bg": False,
                                                 "name": "source_probe"}):
                gui_export._run_export(w, sources=[src])
            # 单条导出 = 平铺一个文件（用户 2026-10-02 定的布局）
            target = outdir / "source_probe.txt"
            self.assertTrue(target.exists(), "右键导出该落一份文件")
            data = np.loadtxt(str(target))
            self.assertEqual(list(data[:, 1]), [7.0, 8.0, 9.0])
            self.assertEqual(len(gui_sources.checked_sources(w)), 0,
                             "导出不该改动勾选状态")
        finally:
            w.close()

    def test_calib_standard_ignores_product_items(self):
        """校准取标样只认原始数据：勾了产物不算数。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files], select=True)
            _store_product(w, files[0])
            w.refresh_groups()
            group = _group_by_text(w, "1D 产物")
            group.child(0).setCheckState(0, Qt.Checked)
            w.file_list.raw_group.setCheckState(0, Qt.Unchecked)
            QApplication.processEvents()
            self.assertIsNone(gui_calib_panel._calib_standard_path(w))
            w.file_list.raw_group.setCheckState(0, Qt.Checked)
            self.assertEqual(gui_calib_panel._calib_standard_path(w), files[0])
        finally:
            w.close()

    def test_export_names_product_with_stage_suffix(self):
        """导出产物条目：名字带 `_扣背景`/`_1D` 后缀（与原始结果不撞名）。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files], select=True)
            key = _store_product(w, files[0], kind="bg",
                                 values=[4.0, 5.0, 6.0])
            stage_cache.record_batch("bg", "export-batch", label="扣背景 batch",
                                     items=[(files[0], key)], **_kw_of(w),
                                     settings={"mode": "anchor"})
            w.refresh_groups()
            self.assertIsNone(_group_by_text(w, "1D 产物"), "bg 产物不进 1D 组")
            w.refresh_groups()
            _group_by_text(w, "扣背景 batch").child(0).setCheckState(0, Qt.Checked)
            w.file_list.raw_group.setCheckState(0, Qt.Unchecked)
            QApplication.processEvents()
            out = gui_export._checked_1d_results(w)
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0][0], f"{files[0].stem}_处理产物")
        finally:
            w.close()

    def test_new_product_clears_the_previous_round_checks(self):
        """出现新产物 = 新一轮：上一轮的勾选全清（用户 2026-10-01 第 8 条，甲）。

        没有新产物的重建照旧不许动勾选（那是 2026-09-28 定的"重建不许改
        用户在哪儿"），两种情形在这里各钉一条。
        """
        w = create_window()
        try:
            files = _tmp_files(2)
            w.add_files([str(p) for p in files], select=True)
            w.refresh_groups()          # 没有新产物 → 勾选原样
            self.assertEqual(len(gui_sources.checked_sources(w)), 2,
                             "没有新产物时，重建不许动勾选")
            _store_product(w, files[0])   # 冒出一条 1D 产物
            w.refresh_groups()
            self.assertEqual(gui_sources.checked_sources(w), [],
                             "出现新产物 = 上一轮勾选该清零")
            self.assertIn("上一轮的勾选已清空", w.log_text.toPlainText(),
                          "勾选自己没了要说清楚去哪了")
            # 同一份产物再来一次重建：键是同一把，不算"新产物" → 不再清
            # （也正因为身份认的是 (kind, key) 而不是条数）
            w.add_files([str(files[0])], select=True)
            w.refresh_groups()
            self.assertEqual(len(gui_sources.checked_sources(w)), 1,
                             "同一批产物重新登记不算新产物")
        finally:
            w.close()

    def test_importing_a_file_with_cached_products_keeps_checks(self):
        """导入"以前算过"的文件：产物只是**刚被列出来**，不是新算的。

        判"新产物"的地基必须是**上一次重建时看到的盘面**，不是文件栏里
        现在有什么——拿文件栏当地基时，这里会把导入后刚勾好的对号清掉，
        用户看到的是"点 [1D] 没反应"（2026-10-01 真数据探针当场抓到：
        "未勾选任何项"）。
        """
        w = create_window()
        try:
            files = _tmp_files(1)
            _store_product(w, files[0])        # 先"以前算过"（盘上有产物）
            w.add_files([str(p) for p in files], select=True)
            w.refresh_groups()
            self.assertEqual(len(gui_sources.checked_sources(w)), 1,
                             "产物早就存在，导入时的勾选不许被清")
            self.assertNotIn("上一轮的勾选已清空", w.log_text.toPlainText())
        finally:
            w.close()

    def test_clear_cache_empties_groups(self):
        """「删除所有缓存…」（文件栏右键）→ 产物分组跟着消失（台账也清了）。

        清缓存只剩这一处入口了：原图页那个 [清空缓存] 按钮已删
        （用户 2026-10-01："都删了"）。2026-10-14 起一击即删——分组
        与台账一起清，没有再点一次的中间态。
        """
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files], select=True)
            _store_product(w, files[0])
            w.refresh_groups()
            self.assertIsNotNone(_group_by_text(w, "1D 产物"))
            gui_file_dock.ask_clear_cache(w)   # 一下就删
            QApplication.processEvents()
            self.assertIsNone(_group_by_text(w, "1D 产物"))
        finally:
            w.close()

    def test_clear_cache_menu_item_keeps_its_plain_label(self):
        """右键那一行恒为「删除所有缓存…」——一击即删后不再有"上膛"写法。

        （2026-10-13 那版会上膛成「确认删除所有缓存（N 个文件）」，
        2026-10-14 用户拍板一击即删后整段退休。）
        """
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files])
            _store_product(w, files[0])

            def clear_label():
                acts = gui_file_dock._entry_menu_actions(QMenu(w), w, None)
                return next(a.text() for a, what in acts.items()
                            if what == "clear_cache")

            self.assertEqual(clear_label(), "删除所有缓存…")
            gui_file_dock.ask_clear_cache(w)   # 删完
            self.assertEqual(clear_label(), "删除所有缓存…", "删后写法不变")
        finally:
            w.close()
