"""校准工作台、判环、保存配置、看门狗

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


class TestCalibration(unittest.TestCase):
    """校准工作台：模式切换 / 自动校准 / 手动选点 / 结果与模板 / 守卫。

    patch 目标 = gui_calib 模块里的裸名（后台线程也读模块全局）；
    引擎函数全部 mock（pyFAI 慢且非确定），只验接线与状态机。
    """

    FAKE_AUTO = {
        "dist_m": 1.5958, "poni1_px": 1045.2, "poni2_px": 1022.0,
        "offset_px": (1.0, 2.0), "rot1_deg": -0.005, "rot2_deg": -0.163,
        "rot3_deg": 0.0, "residual_deg": 0.004,
        "control_points": [(1050.0, 1020.0, 2), (1060.0, 1030.0, 4)],
    }
    FAKE_MANUAL = {
        "dist_m": 1.5962, "poni1_px": 1044.0, "poni2_px": 1021.0,
        "offset_px": (0.0, 0.0), "rot1_deg": -0.004, "rot2_deg": -0.160,
        "rot3_deg": 0.0, "residual_deg": 0.012,
    }
    FAKE_CENTER = {"cx": 1022.0, "cy": 1021.5}

    def _ring_xy(self, ring, ang):
        """(环号, 方位角°) → 图像坐标 (x=列, y=行)，按 lmfp1 配置几何。

        行列注意：配置里 poni1_m↔行/y、poni2_m↔列/x，而点击坐标是
        (x=列, y=行)——x 用 poni2、y 用 poni1。
        """
        cfg = gui_app.CONFIGS["lmfp1_lab6"]["geometry"]
        theo = lab6_theoretical_2theta(cfg["wavelength_m"])
        r = cfg["dist_m"] * np.tan(np.radians(theo[ring])) \
            / cfg["pixel_size_m"]
        a = np.radians(ang)
        return (cfg["poni2_m"] / cfg["pixel_size_m"] + r * np.cos(a),
                cfg["poni1_m"] / cfg["pixel_size_m"] + r * np.sin(a))

    def _click_rings(self, w, specs):
        """按 lmfp1 配置几何在指定 (环号, 方位角) 处模拟校准图点击
        （等价于点图时点中该环：吸附判环应判回同一环）。"""
        for ring, ang in specs:
            x, y = self._ring_xy(ring, ang)
            gui_calib_panel._on_calib_click(w, w.calib_key,
                                      SimpleNamespace(xdata=x, ydata=y,
                                                      inaxes=w.calib_ax))

    def _calib_drag(self, w, data0, data1):
        """在中央校准图上按真事件的形状拖一个框（同 TestBoxZoom._drag_box：
        press 带 button、move 带 buttons、release 带 button）。"""
        ax, canvas = w.calib_ax, w.calib_canvas
        p0 = ax.transData.transform(data0)
        p1 = ax.transData.transform(data1)
        canvas.callbacks.process("button_press_event", MouseEvent(
            "button_press_event", canvas, p0[0], p0[1], button=1))
        canvas.callbacks.process("motion_notify_event", MouseEvent(
            "motion_notify_event", canvas, p1[0], p1[1],
            buttons=frozenset({1})))
        canvas.callbacks.process("button_release_event", MouseEvent(
            "button_release_event", canvas, p1[0], p1[1], button=1))

    def _logs(self, w):
        return w.log_text.toPlainText()

    def _enter_with_fake_a(self, w):
        """加 fake_a.tif 并进校准模式（图像读取需调用方已 mock）。

        注意：自动/手动校准的后台 worker 还会再读一次图，调用方要
        自己把 load_diffraction_image 的 patch 罩住整个测试体。
        """
        add_checked(w, ["data/fake_a.tif"])
        w.calib_btn.click()
        self.assertEqual(w.param_stack.currentIndex(),
                         w.PARAM_PAGES["校准"])
        self.assertIsNotNone(w.calib_dock)
        w.calib_pixel_chk.setChecked(True)   # 校准前置：确认像素尺寸

    def test_enter_mode_opens_panel_and_exit_restores(self):
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
            self.assertEqual(w.calib_key, "校准|data/fake_a.tif")
            self.assertIn("打开校准面板", self._logs(w))
            self.assertIn("校准模式", w.mode_label.text())
            # 16 条理论环路径按当前几何画上（青线；精确反解后不再是
            # Circle patch，见 theoretical_ring_paths）
            rings = [ln for ln in w.calib_ax.lines
                     if str(ln.get_color()).lower()
                     == gui_calib_model.RING_COLOR.lower()]
            self.assertGreaterEqual(len(rings), 16)
            # 退出：面板关、参数坞还原
            w.entrance_buttons["1D"].click()   # 点别的入口 = 退出校准
            self.assertEqual(w.param_stack.currentIndex(),
                             w.PARAM_PAGES["1D"])
            self.assertIsNone(w.calib_dock)
            self.assertIn("回到分析模式", self._logs(w))
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_calibrant_selector_defaults_to_lab6(self):
        """校准页有标样下拉框：默认 LaB₆，环数默认 16（当前几何）。"""
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
            self.assertEqual(w.calib_standard_combo.currentData(), "LaB6")
            self.assertEqual(w.calib_state["calibrant"], "LaB6")
            self.assertEqual(w.calib_state["n_rings"], 16)
            self.assertEqual(w.calib_ring_count_spin.value(), 16)
            names = {w.calib_standard_combo.itemData(i)
                     for i in range(w.calib_standard_combo.count())}
            for name in ("LaB6", "CeO2", "Si_SRM640",
                         "alpha_Al2O3_SRM676"):
                self.assertIn(name, names)
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_switch_calibrant_resets_session_but_keeps_geometry(self):
        """换标样：选点/结果清空、几何保留，日志点名新旧标样。"""
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
            self._click_rings(w, [(2, 0), (4, 90), (6, 180)])
            old_geom = dict(w.calib_state["current_geom"])
            with mock.patch.object(gui_calib, "_refresh_current_metrics"), \
                 mock.patch.object(gui_calib.QMessageBox, "question",
                                   return_value=QMessageBox.Yes):
                idx = w.calib_standard_combo.findData("CeO2")
                w.calib_standard_combo.setCurrentIndex(idx)
            st = w.calib_state
            self.assertEqual(st["calibrant"], "CeO2")
            self.assertEqual(st["points"], [])
            self.assertEqual([r for r in st["results"]
                              if r["kind"] != "raw"], [])
            self.assertEqual(st["current_geom"], old_geom)
            self.assertEqual(len(st["results"]), 1)   # 重新登记"原始"
            self.assertIn("标样切换为 CeO₂", self._logs(w))
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_switch_calibrant_cancel_keeps_the_session(self):
        """确认框点 No：标样/选点/结果都不动，下拉框还原。"""
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
            self._click_rings(w, [(2, 0)])
            with mock.patch.object(gui_calib, "_refresh_current_metrics"), \
                 mock.patch.object(gui_calib.QMessageBox, "question",
                                   return_value=QMessageBox.No):
                idx = w.calib_standard_combo.findData("CeO2")
                w.calib_standard_combo.setCurrentIndex(idx)
            self.assertEqual(w.calib_state["calibrant"], "LaB6")
            self.assertEqual(len(w.calib_state["points"]), 1)
            self.assertEqual(w.calib_standard_combo.currentData(), "LaB6")
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_ring_count_override_updates_state_and_spin_bounds(self):
        """手改环数：记住用户值、关掉自动、选中点的环号框上界跟着变。"""
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
            with mock.patch.object(gui_calib, "_refresh_current_metrics"), \
                 mock.patch.object(gui_calib, "_redraw_calib_if_open"):
                w.calib_ring_count_spin.setValue(10)
            self.assertEqual(w.calib_state["n_rings"], 10)
            self.assertFalse(w.calib_state["n_rings_auto"])
            self.assertEqual(w.calib_ring_spin.maximum(), 9)
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_calib_button_reads_exit_calibration(self):
        """[校准] 按钮选中时写「退出校准」（用户 2026-10-01 要的"两副面孔"）。

        5707a6f 那版就是这个手感，2026-09-26 改成坞顶的 [返回分析模式] 时
        丢了；现在五个入口统一由 _highlight_entrance 换文字。
        """
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
            self.assertEqual(w.calib_btn.text(), "退出校准")
            self.assertIn("退出", w.calib_btn.toolTip())
            w.calib_btn.click()          # 再点一次 = 退出校准（老手感）
            QApplication.processEvents()
            self.assertEqual(w.calib_btn.text(), "校准")
            self.assertIsNone(w.calib_dock)
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_calib_panel_gets_the_resize_grip(self):
        """校准图子窗口也带右下角那个 ▙ 把手（用户 2026-10-01 第 3 条：
        "校准时二维图……没有 1D 图右下角那个标准的缩放，加上这个"）。

        抓手认容器走的是 `_get_dock`——校准面板不进 plot_docks，默认
        那条路（window.plot_docks）查不到它，所以这里两样都钉住：
        把手在不在、拉伸的是不是校准窗口。
        """
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
            content = w.calib_dock.widget()
            grip = getattr(content, "_resize_grip", None)
            self.assertIsNotNone(grip, "校准面板要有右下角把手")
            self.assertEqual(grip.text(), "▙")
            self.assertEqual(grip.height(), 18, "与 1D 图那个把手同尺寸")
            filt = content._grip_filter
            self.assertIs(filt._get_dock(), w.calib_dock,
                          "抓手拉伸的必须是校准窗口（它不住 plot_docks）")
            # 拖右下角 = 改窗口几何（真正走 _apply_drag 那条路）
            dock = w.calib_dock
            before = dock.width()
            filt._drag = (0, 0, dock.x(), dock.y(), dock.width(),
                          dock.height(), "bottomright")
            filt._apply_drag(QPoint(40, 30))
            self.assertEqual(dock.width(), before + 40)
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_calib_page_exit_button_returns_to_analysis(self):
        """校准页底部 [返回分析模式] = 把工具栏开关弹起（同源切换）。

        没选过别的入口就进校准（测试就是这么走的）→ 退出来回到"什么都
        没选"：入口不亮、参数坞收起（用户 2026-09-25 定，另一种情形
        见下一条 test_exit_calib_to_prior_entrance_keeps_dock）。
        """
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
            self.assertTrue(w.calib_btn.isChecked())
            # 页面出口：点 [返回分析模式] → 入口弹起 → 回分析侧
            w.calib_exit_btn.click()
            self.assertFalse(w.calib_btn.isChecked())
            self.assertEqual(w.param_stack.currentIndex(),
                             w.PARAM_PAGES["1D"])
            self.assertIsNone(w.calib_dock)
            self.assertFalse(w.param_dock.isVisible())
            self.assertIsNone(w._last_entrance)
            self.assertIn("回到分析模式", self._logs(w))
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_exit_calib_to_prior_entrance_keeps_dock(self):
        """先点过 [1D] 再进校准 → 退出来回 1D 页、参数坞还开着。"""
        w = create_window()
        try:
            w.show()
            w.entrance_buttons["1D"].click()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
            w.calib_exit_btn.click()
            self.assertEqual(w.param_stack.currentIndex(),
                             w.PARAM_PAGES["1D"])
            self.assertTrue(w.param_dock.isVisible())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_reclick_calib_exits_instead_of_reentering(self):
        """再点一次已亮着的 [校准] = 退出，不是"退出后立刻重进"。

        2026-09-26 用户报"没有退出校准的按钮了"：那条路其实一直在，只是
        Qt 先弹起勾选（toggled → _on_mode(False) 退出），紧接着 clicked
        又把它勾回去（重进）——净效果是闪一下，选点还被清空。
        """
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
                gen = w.calib_gen
                w.calib_btn.click()          # 再点一次 = 退出
            self.assertFalse(w.calib_btn.isChecked())
            self.assertIsNone(w.calib_dock)
            self.assertEqual(w.param_stack.currentIndex(),
                             w.PARAM_PAGES["1D"])
            self.assertFalse(w.param_dock.isVisible())   # 没选过入口 → 收回
            self.assertEqual(w.calib_gen, gen + 1)       # 只关一次（重进会是 +2）
            logs = self._logs(w)
            self.assertEqual(logs.count("进入校准模式"), 1)
            self.assertEqual(logs.count("回到分析模式"), 1)
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_exit_button_sits_in_dock_header_not_page_bottom(self):
        """[返回分析模式] 钉在坞顶那一行、只在校准模式显示。

        以前它在校准表单最底部（窗口 1000 高时落在内容 y=1236，要滚
        434 px 才看得见），用户以为"没有退出按钮"。
        """
        w = create_window()
        try:
            w.show()
            self.assertFalse(w.calib_exit_btn.isVisible())   # 分析模式不显示
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
            self.assertTrue(w.calib_exit_btn.isVisible())
            self.assertIs(w.calib_exit_btn.parentWidget(), w.geom_row)
            self.assertFalse(w.calib_scroll.isAncestorOf(w.calib_exit_btn))
            w.calib_exit_btn.click()
            self.assertFalse(w.calib_btn.isChecked())
            self.assertFalse(w.calib_exit_btn.isVisible())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_geom_row_stays_selectable_on_every_page(self):
        """几何配置行：五页都能换条目，读数看悬停；校准页的提示补一句区别。

        （2026-09-26 晚订正：先前在校准页把它置灰，结果 [删除] 正好住在
        校准页、却在校准页换不了选中项——删条目得跨两页。）
        """
        w = create_window()
        try:
            w.show()
            w.entrance_buttons["1D"].click()
            self.assertTrue(w.config_combo.isEnabled())
            self.assertIn("1595.80 mm", w.geom_row.toolTip())
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
            self.assertTrue(w.config_combo.isEnabled())   # 校准页也能换
            self.assertIn("1595.80 mm", w.geom_row.toolTip())
            self.assertIn("当前配置", w.geom_row.toolTip())  # 写明区别
            w.entrance_buttons["1D"].click()
            self.assertTrue(w.config_combo.isEnabled())
            self.assertNotIn("当前配置", w.geom_row.toolTip())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_enter_mode_without_file_logs_hint(self):
        w = create_window()
        try:
            w.calib_btn.click()
            self.assertEqual(w.param_stack.currentIndex(),
                             w.PARAM_PAGES["校准"])
            self.assertIsNone(getattr(w, "calib_dock", None))
            self.assertIn("请先在文件列表勾选标样文件", self._logs(w))
        finally:
            w.close()

    def test_auto_calib_fills_result_and_enables_save(self):
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10), \
                 mock.patch.object(gui_calib_workers, "fit_center_from_rings",
                                   return_value=self.FAKE_CENTER), \
                 mock.patch.object(gui_calib_workers, "calibrate_geometry",
                                   return_value=self.FAKE_AUTO) as fake:
                self._enter_with_fake_a(w)
                w.calib_start_auto.click()
                self.assertTrue(_wait_until(
                    lambda: any(r["name"].startswith("自动")
                                for r in w.calib_state["results"]), 8000))
            # 引擎初值 = 自动定环心 (cx, cy) + 当前配置（借来的条目）
            self.assertEqual(fake.call_args.kwargs["center0_px"],
                             (self.FAKE_CENTER["cx"], self.FAKE_CENTER["cy"]))
            self.assertAlmostEqual(fake.call_args.kwargs["dist0_m"], 1.5958)
            # 结果累积：原始1（借来的起点）+ 自动取点1，并填进 A 槽
            self.assertEqual([r["name"] for r in w.calib_state["results"]],
                             ["原始1", "自动取点1"])
            self.assertEqual(w.calib_state["slots"]["A"], "自动取点1")
            vals = w.calib_vals["A"]
            self.assertEqual(vals["dist"].text(), "1595.80")
            self.assertEqual(vals["poni1"].text(), "1045.20")
            self.assertEqual(vals["poni2"].text(), "1022.00")
            # 白名单：自洽残差不入对比表
            self.assertNotIn("resid", vals)
            self.assertIn("自动取点完成（自动取点1，LaB₆）", self._logs(w))
            # 图按新几何重画：控制点绿点（一条 2 点散点线）画上
            self.assertTrue(any(len(line.get_xdata()) == 2
                                for line in w.calib_ax.lines))
            # 保存区：说明当前保存的是哪一份 + 保存按钮启用
            self.assertTrue(w.calib_save_btn.isEnabled())
            self.assertIn("将保存：", w.calib_save_hint.text())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_rings_off_image_guard_warns_but_keeps_the_view(self):
        """守卫：几何把理论环全推出图像时，红字 + 日志说清楚，**视野不动**。

        （静默什么都不显示是最坏的失败方式；但也不能把视野放大到包住环
        ——2026-09-26 晚用户报"青环变得很大时图会被迫变小"：视野被撑到
        11 倍宽后图像在画布上缩成一小块，看着像"图动了"。图像是这张图上
        唯一不动的参照系。）
        """
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
                view_before = (w.calib_ax.get_xlim(), w.calib_ax.get_ylim())
                # 几何只认当前配置（分析页字段是只读显示）→ 直接把距离
                # 改大 1000 倍（16 条环全被推出图像）
                w.calib_state["current_geom"]["dist_m"] *= 1000.0
                gui_calib_panel._redraw_calib(w)
            self.assertIn("全部落在图像外", self._logs(w))
            # 图上红字在（说清楚为什么不画线），视野还是图像那一框
            self.assertTrue(any("rings fall outside the image" in t.get_text()
                                for t in w.calib_ax.texts))
            self.assertEqual((w.calib_ax.get_xlim(), w.calib_ax.get_ylim()),
                             view_before)
            # 同一几何重画（撤销/清空选点）不重复刷屏
            n = self._logs(w).count("全部落在图像外")
            gui_calib_panel._redraw_calib(w)
            self.assertEqual(self._logs(w).count("全部落在图像外"), n)
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_fit_rings_toggle_expands_view_on_demand(self):
        """[看环全貌] 是视野放大的唯一入口：默认锁图像，勾上才包住环。

        （2026-09-26 晚用户定：视野不许自动跟着环跑——环跑到图像外时
        自动放大会把图像挤成画布上的一小块，看着像"图动了/图被迫变小"。）
        """
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
                chk = w.findChild(QPushButton, "calib_fit_rings")
                self.assertIsNotNone(chk, "校准图面板上应有 [看环全貌]")
                self.assertFalse(chk.isChecked())        # 默认：锁图像
                # 几何离谱 → 环全跑图像外：默认视野仍锁在图像那一框
                w.calib_state["current_geom"]["dist_m"] *= 1000.0
                gui_calib_panel._redraw_calib(w)
                locked = w.calib_ax.get_xlim()
                # 图上红字把出口指出来
                self.assertTrue(any("看环全貌" in t.get_text()
                                    for t in w.calib_ax.texts))
                # 勾上 → 视野放大到包住环
                chk.setChecked(True)
                grown = w.calib_ax.get_xlim()
                self.assertGreater(grown[1] - grown[0],
                                   (locked[1] - locked[0]) * 1.5)
                # 取消 → 回到图像那一框
                chk.setChecked(False)
                self.assertEqual(w.calib_ax.get_xlim(), locked)
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_rings_and_snapping_follow_result_geometry(self):
        """青线与判环同源：都按"最近一次校准结果"的几何画/判。

        结果几何（PONI 980/1060、rot2 −0.5°）与配置条目（1045.2/1022.0、
        −0.163°）明显不同：用前者画的环上点，若按后者判会滑到隔壁环号
        （实测环 2/6/9 → 3/7/10）。"""
        w = create_window()
        res = dict(self.FAKE_AUTO, dist_m=1.58, poni1_px=980.0,
                   poni2_px=1060.0, rot1_deg=0.02, rot2_deg=-0.50)
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
            calls = []
            real_paths = gui_calib_panel_draw.theoretical_ring_paths

            def spy(**kw):
                calls.append(kw)
                return real_paths(**kw)

            with mock.patch.object(gui_calib_panel_draw, "theoretical_ring_paths",
                                   side_effect=spy):
                gui_calib._on_calib_result(w, "auto", res)
            self.assertEqual(calls[-1]["poni1_px"], 980.0)
            self.assertEqual(calls[-1]["poni2_px"], 1060.0)
            self.assertAlmostEqual(calls[-1]["rot2_deg"], -0.50)
            # 按结果几何取环上的点 → 点它应判回同一环
            paths = real_paths(
                pixel_size_m=calls[-1]["pixel_size_m"],
                wavelength_m=calls[-1]["wavelength_m"], dist_m=res["dist_m"],
                poni1_px=res["poni1_px"], poni2_px=res["poni2_px"],
                rot1_deg=res["rot1_deg"], rot2_deg=res["rot2_deg"],
                image_shape=(256, 256))
            for ring in (2, 6, 9):
                x, y = paths["rings"][ring][1][0]
                gui_calib_panel._on_calib_click(
                    w, w.calib_key,
                    SimpleNamespace(xdata=float(x), ydata=float(y),
                                    inaxes=w.calib_ax))
                self.assertEqual(w.calib_state["points"][-1][2], ring)
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_manual_points_and_refine(self):
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
            # 初始：0 点，手动按钮置灰
            self.assertEqual(w.calib_points_label.text(), "已选 0 个点 / 0 个环")
            self.assertFalse(w.calib_start_manual.isEnabled())
            # 点环 2/4/6（自动判环吸附回同一环）
            self._click_rings(w, ((2, 0), (4, 90), (6, 180)))
            self.assertEqual(len(w.calib_state["points"]), 3)
            self.assertEqual(w.calib_points_label.text(), "已选 3 个点 / 3 个环")
            self.assertTrue(w.calib_start_manual.isEnabled())
            self.assertIn("已记录第 3 个点", self._logs(w))
            # 束心附近点（离首环 1.7°）吸不上：日志忽略、列表不变
            gui_calib_panel._on_calib_click(
                w, w.calib_key,
                SimpleNamespace(xdata=1022.0, ydata=1022.0,
                                inaxes=w.calib_ax))
            self.assertEqual(len(w.calib_state["points"]), 3)
            self.assertIn("已忽略", self._logs(w))
            # 撤销 → 2 点回灰；清空 → 0 点、撤销按钮回灰
            w.calib_undo_btn.click()
            self.assertEqual(w.calib_points_label.text(), "已选 2 个点 / 2 个环")
            self.assertFalse(w.calib_start_manual.isEnabled())
            w.calib_clear_btn.click()
            self.assertEqual(w.calib_points_label.text(), "已选 0 个点 / 0 个环")
            self.assertFalse(w.calib_undo_btn.isEnabled())
            # 重新点 3 点 → 手动校准（mock 引擎）
            self._click_rings(w, ((2, 0), (4, 90), (6, 180)))
            with mock.patch.object(gui_calib_workers, "refine_geometry_from_points",
                                   return_value=self.FAKE_MANUAL) as fake:
                w.calib_start_manual.click()
                self.assertTrue(_wait_until(
                    lambda: any(r["name"].startswith("手动")
                                for r in w.calib_state["results"]), 8000))
            # 引擎输入：3 点 + 环号 + 束心 (行,列) 换序 + 面板几何
            self.assertEqual(len(fake.call_args.args[0]), 3)
            self.assertEqual(fake.call_args.args[1], [2, 4, 6])
            self.assertEqual(fake.call_args.kwargs["center0_px"],
                             (1022.3, 1022.0))
            # 手动列 + 点数少的可信度提示
            self.assertEqual(w.calib_vals["A"]["dist"].text(), "1596.20")
            self.assertIn("手动选点完成（手动选点1，LaB₆）", self._logs(w))
            self.assertIn("点数较少", self._logs(w))
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_the_baseline_dropdown_is_gone(self):
        """参照系固定「当前配置」：页面上不再有「对比基准」下拉框。

        用户 2026-09-30："直接把对比基准删了，直接出结论当前配置和 a、b
        对比分别怎么样，随着用户选择 ab 当前进行变化"——删掉这一层，Δ 行
        与结论就永远讲同一对人（当前配置 vs A、vs B），也不会再出现"基准
        选着 A、结论在讲 B"。这条钉住"别长回来"。
        """
        w = create_window()
        try:
            self.assertFalse(hasattr(w, "calib_base_combo"),
                             "「对比基准」下拉框应当已经删掉")
            page = w.calib_scroll.widget()
            labels = [x.text() for x in page.findChildren(QLabel)]
            self.assertNotIn("对比基准", labels)
            # 说明文字改讲"参照 = 当前配置"（Δ 行与结论同一对人）
            notes = [t for t in labels if t.startswith("Δ = 该列")]
            self.assertTrue(notes, "该有一句说明 Δ 与结论的参照系")
            self.assertIn("当前配置", notes[0])
        finally:
            w.close()

    def test_current_slot_combo_does_not_duplicate_the_result(self):
        """当前配置指向某条结果时，那个下拉里不许有两条同名项。

        用户 2026-09-30："自定义 自定义1，当前选中 2 在选项中还有 2"。
        首项只在"当前配置没指向结果"（手输的自定义 / 借来的条目）时才插——
        那会儿它显示的是结果列表里没有的东西。
        """
        w = create_window()
        try:
            state = gui_calib._calib_state(w)
            res = {"dist_m": 1.5958, "poni1_px": 1045.2, "poni2_px": 1022.0,
                   "rot1_deg": 0.0, "rot2_deg": 0.0, "residual_deg": 0.004,
                   "beam_center_rc": (1021.5, 1022.0), "metrics": None}
            gui_calib._add_result(state, "auto", res)
            state["slots"]["current"] = "自动取点1"
            gui_calib._sync_slot_combos(w)
            combo = w.calib_slot_combo["current"]
            texts = [combo.itemText(i) for i in range(combo.count())]
            self.assertEqual(texts.count("自动取点1"), 1, f"不许重复：{texts}")
            self.assertEqual(combo.currentText(), "自动取点1",
                             "选中的那条就是列表里它自己")
            # 没指向结果时（手输的自定义）首项照旧要有：它显示的东西不在列表里
            state["slots"]["current"] = None
            state["custom"] = True
            gui_calib._sync_slot_combos(w)
            texts = [combo.itemText(i) for i in range(combo.count())]
            self.assertEqual(texts[0], "自定义")
        finally:
            w.close()

    def test_picking_a_point_blits_instead_of_redrawing_everything(self):
        """选点走**贴图**，不整幅重画（用户 2026-10-01："选点时卡死"）。

        实测：整幅重绘一次 ~0.6 s（2048² 图的 LogNorm 上色 + 重采样，探针
        实测 610 ms/次），原先每点一下都付这份钱、事件循环整个被占住——连点
        几下就像死机。现在拍一帧当底图，选点只贴新加的圈和数字（10 ms）。
        这条钉住路径：有底图 → blit、不 draw；没底图（面板刚建/后端不支持）
        → 老实退回整幅画。
        """
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
                for _ in range(10):
                    QApplication.processEvents()
            canvas = w.calib_canvas
            self.assertIsNotNone(getattr(w, "calib_bg", None),
                                 "整幅画完该把底图拍下来")
            calls = {"blit": 0, "draw": 0}
            with mock.patch.object(canvas, "blit",
                                   side_effect=lambda *a, **k:
                                   calls.__setitem__("blit",
                                                     calls["blit"] + 1)), \
                 mock.patch.object(canvas, "draw_idle",
                                   side_effect=lambda *a, **k:
                                   calls.__setitem__("draw",
                                                     calls["draw"] + 1)):
                self._click_rings(w, ((2, 0),))
            self.assertEqual(calls["blit"], 1, "选点该走贴图（blit）")
            self.assertEqual(calls["draw"], 0, "不许整幅重画")
            # 没有底图时退回整幅画（面板刚建、后端不支持贴图）
            w.calib_bg = None
            with mock.patch.object(canvas, "draw_idle") as idle:
                self._click_rings(w, ((4, 90),))
            self.assertTrue(idle.called, "没有底图就老实整幅画")
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_manual_button_explains_why_it_is_disabled(self):
        """不够格时要**写出原因**（用户 2026-10-01："手选完了点击用选点精修无效"）。

        那时按钮是灰的，点下去 Qt 直接丢掉、日志一个字都不写——而最可能的
        原因正是判环挤在一起（几个点都判成同一个环号）。现在那一行会说明
        "至少几个点/几个环"，并指出出路（右键改环号）；按钮 tooltip 也写。
        """
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
                self._click_rings(w, ((2, 0), (2, 90), (2, 180)))
            self.assertFalse(w.calib_start_manual.isEnabled(),
                             "3 个点全在同一个环上：不该让点")
            txt = w.calib_points_label.text()
            self.assertIn("至少", txt, "要写清门槛")
            self.assertIn("右键", txt, "要把出路一起指出来")
            self.assertIn("不同的环", w.calib_start_manual.toolTip())
            # 覆盖到第 2 个环 → 松绑、说明恢复成一句话
            self._click_rings(w, ((4, 45),))
            self.assertTrue(w.calib_start_manual.isEnabled())
            self.assertNotIn("至少", w.calib_points_label.text())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_undo_and_clear_do_not_redraw_the_whole_figure(self):
        """撤销/清空只动标记，不整幅重画（用户 2026-10-01："撤销和全清都卡了"）。

        原先两个动作都走 `_redraw_calib`（整幅 2048² 图再过一遍 LogNorm——
        就是几分钟前刚修掉的"点一下卡一下"那个成本）。现在它们只重建标记
        （贴图路径）：探针实测撤销 8 ms、清空 5 ms，标记数也对。
        """
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
                self._click_rings(w, ((2, 0), (4, 90), (6, 180)))
            self.assertEqual(len(w.calib_marker_artists), 6, "3 个点 × 2 artist")
            with mock.patch.object(w.calib_canvas, "draw_idle") as idle, \
                 mock.patch.object(w.calib_canvas, "blit") as blit:
                gui_calib_panel._undo_calib_point(w)
                QApplication.processEvents()
                self.assertTrue(blit.called, "撤销该走贴图")
                self.assertFalse(idle.called, "撤销不许整幅重画")
            self.assertEqual(len(w.calib_marker_artists), 4)
            self.assertEqual(len(w.calib_state["points"]), 2)
            with mock.patch.object(w.calib_canvas, "draw_idle") as idle2, \
                 mock.patch.object(w.calib_canvas, "blit") as blit2:
                gui_calib_panel._clear_calib_points(w)
                QApplication.processEvents()
                self.assertTrue(blit2.called, "清空该走贴图")
                self.assertFalse(idle2.called, "清空不许整幅重画")
            self.assertEqual(w.calib_marker_artists, [])
            self.assertEqual(w.calib_state["points"], [])
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_blitted_picks_redraw_every_marker(self):
        """贴图重画时**所有**选点标记都要重画（用户 2026-10-01："圆环会消失"）。

        底图是上一次**整幅画**的那一帧，里面没有后来贴上去的标记——只画新点
        的话，"贴回底图"这一步会把先前的点全擦掉（贴图上线后实测：连点两个
        点，第一个圈就没了）。所以每次贴图都要按 calib_marker_artists 把全部
        标记重画一遍。
        """
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
                for _ in range(10):
                    QApplication.processEvents()
            ax = w.calib_ax
            seen = []
            with mock.patch.object(ax, "draw_artist",
                                   side_effect=lambda a: seen.append(a)):
                self._click_rings(w, ((2, 0),))
                first = len(seen)
                self._click_rings(w, ((4, 90),))
                second = len(seen) - first
            self.assertEqual(first, 2, "第 1 个点：圈 + 环号两个 artist")
            self.assertEqual(second, 4,
                             f"第 2 个点要重画两个点的 4 个 artist（实际 {second}）")
            self.assertEqual(len(w.calib_marker_artists), 4)
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_pixel_confirmation_sits_above_the_action_sections(self):
        """像素尺寸确认住在页面**顶部**（操作组下面、自动取点/手动选点之前）。

        它现在只是一条**提醒**（2026-10-05 起不拦动作，见
        `_warn_pixel_unchecked`），但仍要待在顶部看得见——搬过三趟：挤在
        「操作」按钮堆里被忽视（2026-09-26）→ 数据表上方 → 2026-09-30
        数据表挪到最下之后它跟着沉底（用户 2026-10-01："把像素尺寸放上面，
        太下面了不方便"）。这条钉住位置，别再沉下去。
        """
        w = create_window()
        try:
            page = w.calib_scroll.widget()
            layout = page.layout()
            names = []
            for i in range(layout.count()):
                item = layout.itemAt(i)
                wid = item.widget()
                if wid is w.calib_pixel_chk or (
                        wid is None and item.layout() is not None
                        and item.layout().indexOf(w.calib_pixel_chk) >= 0):
                    names.append("像素确认")   # 勾选框和橙色提醒同住一行
                elif isinstance(wid, QGroupBox):
                    names.append(wid.title())
            self.assertIn("像素确认", names, "像素确认框该在页面布局里")
            self.assertLess(names.index("像素确认"), names.index("自动取点"))
            self.assertLess(names.index("像素确认"), names.index("手动选点"))
            self.assertLess(names.index("像素确认"), names.index("数据"))
        finally:
            w.close()

    def test_manual_section_sits_above_auto_section(self):
        """校准页顺序：手动选点在自动取点**上面**。

        用户 2026-10-13："校准顺序调为手动在上，自动在下"——手动选点
        是常用路径，打开校准页第一眼就该看到它；数据表照旧在最下。
        """
        w = create_window()
        try:
            page = w.calib_scroll.widget()
            layout = page.layout()
            names = []
            for i in range(layout.count()):
                wid = layout.itemAt(i).widget()
                if isinstance(wid, QGroupBox):
                    names.append(wid.title())
            self.assertLess(names.index("手动选点"), names.index("自动取点"))
            self.assertLess(names.index("自动取点"), names.index("数据"))
        finally:
            w.close()

    def test_calib_zoom_box_wheel_and_fit_reset(self):
        """校准图放大镜：框选放大 / 滚轮缩放 / [看环全貌] 复位，重画不弹回。

        用户 2026-10-13："校准图添加放大功能"——放大镜点亮 = 左键拖 =
        框选放大、滚轮以光标为中心缩放；手势视野存 window.calib_view，
        换几何等整幅重画时回放（不弹回图像框）；[看环全貌] 翻面即复位。
        """
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
                zoom_btn = w.findChild(QPushButton, "calib_zoom_btn")
                self.assertIsNotNone(zoom_btn, "校准图该有 [放大镜]")
                ax = w.calib_ax
                zoom_btn.setChecked(True)
                QApplication.processEvents()
                self._calib_drag(w, (60.0, 60.0), (120.0, 120.0))
                QApplication.processEvents()
                xlo, xhi = ax.get_xlim()
                self.assertAlmostEqual(xlo, 60.0, delta=6.0)
                self.assertAlmostEqual(xhi, 120.0, delta=6.0)
                self.assertAlmostEqual(ax.get_ylim()[0], 60.0, delta=6.0)
                self.assertAlmostEqual(ax.get_ylim()[1], 120.0, delta=6.0)
                self.assertIsNotNone(w.calib_view, "手势视野该被记住")
                # 整幅重画（换几何/改环号走的那条路）不弹回图像框
                gui_calib_panel._redraw_calib(w)
                QApplication.processEvents()
                self.assertAlmostEqual(ax.get_xlim()[0], 60.0, delta=6.0)
                # 滚轮（点亮时）以光标为中心放大
                span0 = ax.get_xlim()[1] - ax.get_xlim()[0]
                canvas = w.calib_canvas
                px, py = ax.transData.transform((90.0, 90.0))
                canvas.callbacks.process("scroll_event", MouseEvent(
                    "scroll_event", canvas, px, py, step=1, button="up"))
                QApplication.processEvents()
                self.assertLess(ax.get_xlim()[1] - ax.get_xlim()[0], span0,
                                "上滚该放大")
                # [看环全貌] 翻面 = 复位（手势窗口清掉，视野交给该开关）
                w.findChild(QPushButton, "calib_fit_rings").setChecked(True)
                QApplication.processEvents()
                self.assertIsNone(w.calib_view)
                self.assertGreater(ax.get_xlim()[1] - ax.get_xlim()[0], 200.0,
                                   "看环全貌下视野比框选大得多")
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_calib_home_key_resets_gesture_view(self):
        """校准图复位视野：Home 键与 [复位视图] 按钮同一条路；[看环全貌] 开关不动。

        用户 2026-10-14："校准图添加 home 键，还是回到最初的视图"、
        随后"校准没有 home 键"（别家面板标题栏都有 [复位视图]，校准图
        此前只有 [看环全貌]——那是视野**模式**不是复位）。认图方式与
        普通面板同一条线：鼠标停在图上（motion 记哨兵值 CALIB_HOVER_KEY）
        → Home 复位这张图的手势缩放；开关状态是"模式"，复位不翻它——
        开着 [看环全貌] 时回到"包住环"那一框。
        """
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
                w.findChild(QPushButton, "calib_zoom_btn").setChecked(True)
                QApplication.processEvents()
                ax = w.calib_ax
                x0 = tuple(ax.get_xlim())
                log0 = self._logs(w)
                w.home_shortcut.activated.emit()   # 还没缩放：本来就是最初
                QApplication.processEvents()
                self.assertEqual(self._logs(w), log0, "没缩放过不必记日志")
                self._calib_drag(w, (60.0, 60.0), (120.0, 120.0))
                QApplication.processEvents()
                self.assertIsNotNone(w.calib_view, "前置条件：手势缩放过")
                self.assertEqual(w._hover_panel_key,
                                 gui_panel_state.CALIB_HOVER_KEY,
                                 "拖动经过 motion → Home 该认这张图")
                w.home_shortcut.activated.emit()
                QApplication.processEvents()
                self.assertIsNone(w.calib_view, "Home 该清掉手势视野")
                self.assertEqual(tuple(ax.get_xlim()), x0, "回到最初的视野")
                self.assertIn("视野：已复位到最初", self._logs(w))
                # 开关是"模式"不是"视野"：开着 [看环全貌] 放大再按 Home，
                # 回到的是包住环那一框、开关仍开着
                fit_btn = w.findChild(QPushButton, "calib_fit_rings")
                fit_btn.setChecked(True)
                QApplication.processEvents()
                # [看环全貌] 下视野可能远大于图像（环跑到图像外），按**当
                # 前视野的比例**拖，保证像素位移够大（框选而不是点击）
                xlo, xhi = ax.get_xlim()
                ylo, yhi = ax.get_ylim()
                self._calib_drag(
                    w, (xlo + (xhi - xlo) * 0.3, ylo + (yhi - ylo) * 0.3),
                    (xlo + (xhi - xlo) * 0.7, ylo + (yhi - ylo) * 0.7))
                QApplication.processEvents()
                self.assertIsNotNone(w.calib_view)
                w.home_shortcut.activated.emit()
                QApplication.processEvents()
                self.assertIsNone(w.calib_view)
                self.assertTrue(fit_btn.isChecked(), "Home 不翻 [看环全貌]")
                self.assertGreater(ax.get_xlim()[1] - ax.get_xlim()[0], 200.0,
                                   "复位到的是看环全貌那一框")
                # [复位视图] 按钮：与 Home 键同一个落点（同一张图、同样
                # 不翻开关），两条路都钉住
                home_btn = w.findChild(QPushButton, "calib_home_btn")
                self.assertIsNotNone(home_btn, "校准图该有 [复位视图]")
                xlo, xhi = ax.get_xlim()
                ylo, yhi = ax.get_ylim()
                self._calib_drag(
                    w, (xlo + (xhi - xlo) * 0.3, ylo + (yhi - ylo) * 0.3),
                    (xlo + (xhi - xlo) * 0.7, ylo + (yhi - ylo) * 0.7))
                QApplication.processEvents()
                self.assertIsNotNone(w.calib_view, "前置条件：按钮前先缩放过")
                home_btn.click()
                QApplication.processEvents()
                self.assertIsNone(w.calib_view, "[复位视图] 该清掉手势视野")
                self.assertTrue(fit_btn.isChecked(), "[复位视图] 也不翻开关")
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_calib_zoom_click_pick_behaviour(self):
        """放大镜未点亮 = 按下即记（老行为）；点亮后小位移松开也算点击
        选点（按**按下处**判环）、拖大了是框选（不加点）。"""
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
                ax = w.calib_ax
                state = w.calib_state
                # 环坐标（真实 2048 探测器几何）都在 256² 假图外：先开
                # [看环全貌] 把视野放大到包住环，后面真事件才点得到它们
                w.findChild(QPushButton, "calib_fit_rings").setChecked(True)
                QApplication.processEvents()
                # 熄灭：一点即记（左键按下直接走选点）
                x, y = self._ring_xy(2, 0.0)
                gui_calib_panel._on_calib_press(
                    w, w.calib_key,
                    SimpleNamespace(xdata=x, ydata=y, x=10.0, y=10.0,
                                    inaxes=ax, button=1))
                self.assertEqual([int(p[2]) for p in state["points"]], [2],
                                 "未点亮：按下即记（老行为）")
                # 点亮：小位移松开 = 点击 → 按按下处选点
                w.findChild(QPushButton, "calib_zoom_btn").setChecked(True)
                QApplication.processEvents()
                x4, y4 = self._ring_xy(4, 90.0)
                canvas = w.calib_canvas
                p = ax.transData.transform((x4, y4))
                canvas.callbacks.process("button_press_event", MouseEvent(
                    "button_press_event", canvas, p[0], p[1], button=1))
                canvas.callbacks.process("button_release_event", MouseEvent(
                    "button_release_event", canvas, p[0] + 2.0, p[1] + 1.0,
                    button=1))
                QApplication.processEvents()
                self.assertEqual([int(pp[2]) for pp in state["points"]],
                                 [2, 4], "小位移松开按按下处选点")
                # 点亮：拖大 = 框选放大、不加点（框在 [看环全貌] 视野内）
                self._calib_drag(w, (1000.0, 1000.0), (1100.0, 1100.0))
                QApplication.processEvents()
                self.assertEqual(len(state["points"]), 2, "框选不加点")
                self.assertIsNotNone(w.calib_view)
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_right_click_selects_then_panel_edits_the_ring(self):
        """右键某个选点 = 选中它；环号在「手动」小节里改（2026-10-03 起不弹框）。

        自动判环（含按尺度重判）都没救回来时，用户自己知道该是第几环——
        右键选中 + 数字框改，比再点一遍碰运气可靠。原版右键直接弹
        QInputDialog：macOS 全屏下"对话框开着时切走再切回"会把应用卡成
        收不到任何输入（AppKit 模态收尾没做完，实测复现），改成两步后
        模态窗口彻底不存在。
        """
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
                self._click_rings(w, ((2, 0), (4, 90), (6, 180)))
            state = w.calib_state
            self.assertEqual([int(p[2]) for p in state["points"]], [2, 4, 6])
            x, y, _old = state["points"][1]
            self.assertFalse(w.calib_ring_spin.isEnabled(), "没选中时该是灰的")
            self._right_click_at(w, x, y)
            self.assertEqual(state["selected"], 1, "右键 = 选中这个点")
            self.assertIn("已选中第 2 个点", self._logs(w))
            self.assertTrue(w.calib_ring_spin.isEnabled())
            self.assertEqual(w.calib_ring_spin.value(), 4, "回填当前环号")
            self.assertIn("第 2 个", w.calib_selected_lbl.text())
            # 在面板上改成 5 → 按 [改]
            w.calib_ring_spin.setValue(5)
            w.calib_ring_apply.click()
            self.assertEqual([int(p[2]) for p in state["points"]], [2, 5, 6])
            self.assertEqual((state["points"][1][0], state["points"][1][1]),
                             (x, y), "只改环号，位置不动")
            self.assertIn("环号：4 → 5", self._logs(w))
            # 右键落在空处：选中不动、只提示
            self._right_click_at(w, x + 100.0, y + 100.0)
            self.assertEqual(state["selected"], 1)
            self.assertEqual([int(p[2]) for p in state["points"]], [2, 5, 6])
            self.assertIn("右键要落在某个选点上", self._logs(w))
            # 改成和现在一样：写明白"没改"，不静默
            w.calib_ring_apply.click()
            self.assertIn("现在就是环 5，没改", self._logs(w))
            # 清空选点 → 选中自动失效、控件禁用
            w.calib_clear_btn.click()
            self.assertIsNone(state["selected"])
            self.assertFalse(w.calib_ring_spin.isEnabled())
            self.assertIn("—", w.calib_selected_lbl.text())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_undo_keeps_or_clears_the_selection(self):
        """撤销不越界：撤掉的不是选中项 → 选中还在；撤掉它 → 选中清空。"""
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
                self._click_rings(w, ((2, 0), (4, 90), (6, 180)))
            state = w.calib_state
            x, y, _ = state["points"][0]
            self._right_click_at(w, x, y)
            self.assertEqual(state["selected"], 0)
            w.calib_undo_btn.click()             # 撤掉第 3 个点：选中(第 1 个)不受影响
            self.assertEqual(state["selected"], 0)
            w.calib_undo_btn.click()             # 撤掉第 2 个点
            self.assertEqual(state["selected"], 0)
            w.calib_undo_btn.click()             # 撤掉选中的那个 → 清空
            self.assertIsNone(state["selected"])
            self.assertFalse(w.calib_ring_spin.isEnabled())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def _right_click_at(self, w, x, y):
        """在校准图 (x, y) 处发一次右键点击（选中选点的入口）。"""
        gui_calib_panel._on_calib_click(
            w, w.calib_key,
            SimpleNamespace(xdata=x, ydata=y, inaxes=w.calib_ax,
                            button=MouseButton.RIGHT))

    def test_delta_column_and_last_save_source(self):
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10), \
                 mock.patch.object(gui_calib_workers, "fit_center_from_rings",
                                   return_value=self.FAKE_CENTER), \
                 mock.patch.object(gui_calib_workers, "calibrate_geometry",
                                   return_value=self.FAKE_AUTO), \
                 mock.patch.object(gui_calib_workers, "refine_geometry_from_points",
                                   return_value=self.FAKE_MANUAL), \
                 mock.patch.object(gui_calib_metrics, "ring_metrics",
                                   side_effect=lambda image, **kw:
                                   {"dev_px": 0.20 if kw["dist_m"] == 1.5958
                                    else 0.50,
                                    "clip_frac": 0.0, "n_complete": 16,
                                    "rings": [{}] * 16,
                                    "a": {"spread_ppm": 500.0}}):
                self._enter_with_fake_a(w)
                self._click_rings(w, ((2, 0), (4, 90), (6, 180)))
                # 只跑手动 → 结果进列表并填 A 槽；当前配置是"借来的起点"
                # → 第一条结果**总是采纳**（新批次规则）
                w.calib_start_manual.click()
                self.assertTrue(_wait_until(
                    lambda: w.calib_state["slots"]["A"] == "手动选点1", 8000))
                self.assertEqual([r["name"] for r in w.calib_state["results"]],
                                 ["原始1", "手动选点1"])
                self.assertEqual(w.calib_state["current_from"], "手动选点1")
                self.assertIn("新批次的第一条结果",
                              w.log_text.toPlainText())
                d = w.calib_vals["delta"]
                # 基准那一列自己写"（基准）"（用户 2026-09-27："表述不清"——
                # 原来那个破折号看着像"这格没数据"）
                self.assertEqual(d["dist"]["current"].text(), "（基准）")
                self.assertEqual(d["dist"]["A"].text(), "+0.00")     # 就是它自己
                # 再跑自动（环位偏差 0.20 < 手动 0.50）→ 轮换填 B 并采纳
                w.calib_start_auto.click()
                self.assertTrue(_wait_until(
                    lambda: w.calib_state["slots"]["B"] == "自动取点1", 8000))
                self.assertEqual(w.calib_state["slots"]["A"], "手动选点1")
                self.assertEqual(w.calib_state["current_from"], "自动取点1")
                # Δ = 该列 − 基准（基准 = 当前配置 = 自动取点1）
                self.assertEqual(d["dist"]["A"].text(), "+0.40")     # 手动−自动
                self.assertEqual(d["dev"]["A"].text(), "+0.30")      # 0.50−0.20
                self.assertEqual(d["dist"]["B"].text(), "+0.00")
                # 参照系固定 = 当前配置，A、B 两个候选都报出来（用户
                # 2026-09-30："直接把对比基准删了，直接出结论当前配置和
                # a、b 对比分别怎么样，随着用户选择 ab 当前进行变化"）。
                # 候选按**槽名**报（与表头 当前配置/A/B 对齐）：此时
                # 当前配置 = 自动取点1（dev 0.20）、A = 手动选点1（dev 0.50）、
                # B = 自动取点1（同一条结果，差 0.00）
                self.assertIn("A 比 当前配置 差 0.30 px",
                              w.calib_verdict.text())
                self.assertIn("B 与 当前配置 差不多",
                              w.calib_verdict.text())
                # "随着用户选择 ab 变化"：把 A 槽换成另一条结果 → Δ 与结论
                # 立刻跟着变（不再需要先挑"对比基准"）
                combo_a = w.calib_slot_combo["A"]
                combo_a.setCurrentIndex(combo_a.findData("自动取点1"))
                QApplication.processEvents()
                self.assertEqual(d["dist"]["A"].text(), "+0.00",
                                 "A 换成了自动取点1：Δ 跟着变")
                self.assertNotIn("手动选点1", w.calib_verdict.text(),
                                 "结论里不该再提手动选点1（它已经不在 A/B 槽里）")
                # 保存对象 = 当前配置（此时是被采纳的 自动取点1）
                self.assertIn("自动取点1", w.calib_save_hint.text())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_late_result_after_exit_is_dropped(self):
        """任务在飞时退出模式：迟到结果作废（不崩、不填列）。"""
        started = threading.Event()
        release = threading.Event()

        def slow_calib(*args, **kwargs):
            started.set()
            release.wait(10)
            return self.FAKE_AUTO

        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10), \
                 mock.patch.object(gui_calib_workers, "fit_center_from_rings",
                                   return_value=self.FAKE_CENTER), \
                 mock.patch.object(gui_calib_workers, "calibrate_geometry",
                                   side_effect=slow_calib):
                self._enter_with_fake_a(w)
                w.calib_start_auto.click()
                self.assertTrue(started.wait(5))
                w.entrance_buttons["1D"].click()   # 点别的入口 = 退出
                self.assertIsNone(w.calib_dock)
                release.set()
                # 等任务收尾投递后：状态与结果区仍为空（作废不炸）
                self.assertTrue(_wait_until(lambda: not w._tasks, 5000))
                self.assertEqual(w.calib_state["results"], [])
                self.assertEqual(w.calib_vals["A"]["dist"].text(), "—")
        finally:
            release.set()
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_calib_page_fits_the_dock_width(self):
        """校准页不许有控件越界：参数坞窄、滚动区水平条关闭，超宽就够不着。

        （判据是**实际几何**：把坞压到最小宽后逐控件量右边缘；用
        sizeHint 会误判——滚动区本来就会把页面压到视口宽。）
        """
        w = create_window()
        try:
            w.show()
            w.resize(420, 700)
            QApplication.processEvents()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
            w.param_dock.setMinimumWidth(w.param_dock.minimumWidth())
            QApplication.processEvents()
            page = w.param_stack.widget(w.PARAM_PAGES["校准"])
            over = []
            for child in page.findChildren(QWidget):
                if not child.isVisible() or child.width() == 0:
                    continue
                right = child.mapTo(page, QPoint(0, 0)).x() + child.width()
                if right > page.width() + 1:
                    over.append((type(child).__name__,
                                 right - page.width()))
            self.assertEqual(over, [], f"越界控件（类型, 超出 px）：{over}")
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_restart_drops_stale_auto_result(self):
        """连点 [开始自动校准]：旧任务慢、新任务快 → 旧结果晚到作废。"""
        calls = {"n": 0}
        slow_entered = threading.Event()
        release = threading.Event()
        fast = dict(self.FAKE_AUTO, dist_m=1.6000, residual_deg=0.009)

        def two_speed_calib(*args, **kwargs):
            i = calls["n"]
            calls["n"] += 1
            if i == 0:
                slow_entered.set()
                release.wait(10)
                return self.FAKE_AUTO
            return fast

        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10), \
                 mock.patch.object(gui_calib_workers, "fit_center_from_rings",
                                   return_value=self.FAKE_CENTER), \
                 mock.patch.object(gui_calib_workers, "calibrate_geometry",
                                   side_effect=two_speed_calib):
                self._enter_with_fake_a(w)
                w.calib_start_auto.click()
                self.assertTrue(slow_entered.wait(5))
                w.calib_start_auto.click()   # 连点重跑：新任务说了算
                self.assertTrue(_wait_until(
                    lambda: any(r["name"].startswith("自动")
                                for r in w.calib_state["results"]), 8000))
                self.assertEqual(w.calib_vals["A"]["dist"].text(),
                                 "1600.00")
                release.set()   # 旧任务这时才完成 → 迟到作废
                self.assertTrue(_wait_until(lambda: not w._tasks, 5000))
                self.assertEqual(w.calib_vals["A"]["dist"].text(),
                                 "1600.00", "旧结果不得覆盖新结果")
                self.assertEqual(
                    w.calib_state["results"][-1]["result"]["dist_m"],
                    fast["dist_m"], "结果列表里是新结果")
        finally:
            release.set()
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_closing_panel_forgets_state(self):
        """× 关校准面板 = 选点/结果全清（关闭即遗忘，重开全新）。"""
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                self._enter_with_fake_a(w)
                self._click_rings(w, ((2, 0), (4, 90), (6, 180)))
                self.assertEqual(len(w.calib_state["points"]), 3)
                dock = w.calib_dock
                dock.close()   # × 按钮
                QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                self.assertIsNone(w.calib_dock)
                self.assertIn("已关闭校准面板", self._logs(w))
                self.assertEqual(w.calib_state["points"], [])
                self.assertEqual(w.calib_state["results"], [])
                self.assertEqual(w.calib_points_label.text(),
                                 "已选 0 个点 / 0 个环")
                self.assertFalse(w.calib_start_manual.isEnabled())
                self.assertFalse(w.calib_save_btn.isEnabled())
                self.assertEqual(w.calib_save_hint.text(), "还没有可保存的几何")
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()


class TestSaveCalibConfig(unittest.TestCase):
    """[保存为配置]：校验 / 落盘 / 下拉框同步选中 / 覆盖确认 / worker 束心。

    save 走真 config.save_user_config（写临时目录的真文件），
    USER_CONFIG_PATH 指向临时路径（不碰仓库真文件），收尾还原
    USER_CONFIGS / CONFIGS 内存字典。引擎照旧 mock。
    """

    FAKE_AUTO = dict(TestCalibration.FAKE_AUTO)
    FAKE_MANUAL = dict(TestCalibration.FAKE_MANUAL)

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._path = Path(self._tmpdir) / "config_user.json"
        patcher = mock.patch.object(config_mod, "USER_CONFIG_PATH",
                                    self._path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._user_backup = dict(config_mod.USER_CONFIGS)
        self._confs_backup = dict(config_mod.CONFIGS)
        # 清空用户条目再测：真 config_user.json 里若有同名 key（本地标定
        # 存档，如 lmfp2_lab6），保存/删除会撞上"覆盖确认"等模态框——
        # offscreen 下没人应答，测试原地挂死。隔离不能依赖真文件内容
        # （test_config_user 已踩过同一个坑）。tearDown 负责还原。
        config_mod.USER_CONFIGS.clear()
        for key in self._user_backup:
            config_mod.CONFIGS.pop(key, None)

    def tearDown(self):
        config_mod.USER_CONFIGS.clear()
        config_mod.USER_CONFIGS.update(self._user_backup)
        config_mod.CONFIGS.clear()
        config_mod.CONFIGS.update(self._confs_backup)

    def _auto_calib(self, w):
        """进校准模式跑一次 mock 自动校准（worker 真跑、引擎 mock）。"""
        with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                               return_value=np.ones((256, 256)) * 10), \
             mock.patch.object(gui_calib_workers, "fit_center_from_rings",
                               return_value=TestCalibration.FAKE_CENTER), \
             mock.patch.object(gui_calib_workers, "calibrate_geometry",
                               return_value=dict(self.FAKE_AUTO)):
            add_checked(w, ["data/fake_a.tif"])
            w.calib_btn.click()
            w.calib_pixel_chk.setChecked(True)   # 校准前置：确认像素尺寸
            w.calib_start_auto.click()
            self.assertTrue(_wait_until(
                lambda: any(r["name"].startswith("自动")
                            for r in w.calib_state["results"]), 8000))

    def test_default_name_strips_the_dash_digit_tail(self):
        """预填名 = 标样文件名去掉末尾「-数字」段（用户 2026-10-07）。

        没有标样时退回按当前条目递推（lmfp1_lab6 → lmfp2_lab6）。
        """
        class _Stub:      # _default_config_name 只读 window.calib_path
            calib_path = "/tmp/lab6-00024.tif"

        self.assertEqual(gui_calib._default_config_name(_Stub()), "lab6")
        _Stub.calib_path = "/tmp/LMFP_1_atten0-00029.tif"
        self.assertEqual(gui_calib._default_config_name(_Stub()),
                         "LMFP_1_atten0")
        _Stub.calib_path = "/tmp/fake_a.tif"
        self.assertEqual(gui_calib._default_config_name(_Stub()), "fake_a")
        _Stub.calib_path = None
        self.assertEqual(gui_calib._default_config_name(_Stub()),
                         gui_config_ops._suggest_config_key(
                             gui_app.DEFAULT_CONFIG))

    def test_enter_calib_prefills_name_but_keeps_user_edits(self):
        """进校准 → 名称框按标样文件名预填；用户改过的名字不被覆盖。"""
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
            self.assertEqual(w.calib_name_edit.text(), "fake_a",
                             "进校准该按标样文件名预填")
            w.calib_name_edit.setText("我自己的名字")
            w.calib_btn.setChecked(False)      # 退出校准
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                w.calib_btn.click()            # 再进：用户改过的名字不动
            self.assertEqual(w.calib_name_edit.text(), "我自己的名字")
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_save_adds_to_combo_and_selects(self):
        """保存 → 下拉框出现新条目并自动选中（读数跟到几何行 + 落盘）。

        2026-10-07 起保存表单只有一个「条目名称」框：名字就是备注，
        命令行标识由它洗出来（名字本身合法时两者相同，下拉框显示 = 名字）。
        """
        w = create_window()
        try:
            w.show()
            self._auto_calib(w)
            w.calib_name_edit.setText("lmfp2_lab6")
            w.calib_save_btn.click()
            # 下拉框：新条目出现 + 自动选中（显示一条 = 这个名字）
            idx = w.config_combo.findData("lmfp2_lab6")
            self.assertGreaterEqual(idx, 0)
            self.assertEqual(w.config_combo.currentIndex(), idx)
            self.assertEqual(w.config_combo.currentText(), "lmfp2_lab6")
            self.assertEqual(w.config_name, "lmfp2_lab6")
            # 几何跟到坞顶：悬停读数 = 校准结果（1595.80，不是初值 1600）
            self.assertIn("1595.80 mm", w.geom_row.toolTip())
            # 条目内容：结果几何 + 参数坞像素/波长 + 新拟合束心 B
            cfg = w.config
            self.assertEqual(cfg["label"], "lmfp2_lab6")
            self.assertAlmostEqual(cfg["geometry"]["dist_m"], 1.5958)
            self.assertAlmostEqual(cfg["geometry"]["poni1_m"],
                                   1045.2 * 200e-6)
            self.assertEqual(cfg["beam_center"],
                             (TestCalibration.FAKE_CENTER["cy"],
                              TestCalibration.FAKE_CENTER["cx"]))
            # 落盘真文件 + 内存注册表 + 日志
            self.assertTrue(self._path.is_file())
            self.assertIn("lmfp2_lab6", config_mod.CONFIGS)
            self.assertIn("已保存新配置条目", w.log_text.toPlainText())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_save_sanitizes_name_into_key(self):
        """中文名 → 标识自动洗成下划线形式；两者不同时显示成 `key_备注`。"""
        w = create_window()
        try:
            w.show()
            self._auto_calib(w)
            w.calib_name_edit.setText("LMFP 第 2 批")
            w.calib_save_btn.click()
            self.assertIn("LMFP_2", config_mod.USER_CONFIGS,
                          "非字母数字该换下划线、去掉首尾")
            entry = config_mod.USER_CONFIGS["LMFP_2"]
            self.assertEqual(entry["label"], "LMFP 第 2 批")
            idx = w.config_combo.findData("LMFP_2")
            self.assertEqual(w.config_combo.itemText(idx), "LMFP_2_LMFP 第 2 批",
                             "key 与备注不同时显示成一条 key_备注")
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_save_validation_errors(self):
        """空名 / 内置重名拒绝：只记日志、不落盘。"""
        w = create_window()
        try:
            w.show()
            self._auto_calib(w)
            # 空名
            w.calib_name_edit.setText("  ")
            w.calib_save_btn.click()
            self.assertIn("请先填写条目名称", w.log_text.toPlainText())
            # 内置重名（名字洗出来的标识撞上内置条目）
            w.calib_name_edit.setText("lmfp1_lab6")
            w.calib_save_btn.click()
            self.assertIn("与内置条目重名", w.log_text.toPlainText())
            self.assertFalse(self._path.exists())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_save_warns_but_proceeds_without_pixel_confirmation(self):
        """没核对像素尺寸 → **照常保存** + 日志里醒目提醒。

        用户 2026-10-05："改为提醒的样式，不要求用户必须选择了"——原来
        这里是一道硬门禁（不确认就拒绝落盘）；现在只是把风险说明白：条目
        会被别的批次和 CLI 脚本原样拿去用，像素填错时拟合会把距离同比例
        凑回来（看着正常、距离存疑）。
        """
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()          # 没勾"已核对像素尺寸"
            w.calib_name_edit.setText("lmfp9_lab6")
            w.calib_save_btn.click()
            self.assertIn("lmfp9_lab6", config_mod.USER_CONFIGS)
            self.assertTrue(self._path.exists())
            self.assertIn("像素尺寸还没核对", w.log_text.toPlainText())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_calibration_runs_with_warning_without_pixel_confirmation(self):
        """没核对像素尺寸 → 校准照常跑（不再被拦），日志里醒目提醒。

        与上面那条同一个口径（用户 2026-10-05："不要求用户必须选择了"）。
        """
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10), \
                 mock.patch.object(gui_calib_workers, "fit_center_from_rings",
                                   return_value=TestCalibration.FAKE_CENTER), \
                 mock.patch.object(gui_calib_workers, "calibrate_geometry",
                                   return_value=dict(self.FAKE_AUTO)):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
                # 故意不勾"已核对像素尺寸"
                w.calib_start_auto.click()
                self.assertTrue(_wait_until(
                    lambda: any(r["name"].startswith("自动")
                                for r in w.calib_state["results"]), 8000),
                    "没核对像素尺寸也该照常跑出结果")
            self.assertIn("像素尺寸还没核对", w.log_text.toPlainText())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_save_overwrite_asks_and_can_confirm(self):
        """已存用户条目重名：弹确认，No 不动 / Yes 覆盖。"""
        w = create_window()
        try:
            w.show()
            self._auto_calib(w)
            w.calib_name_edit.setText("lmfp2_lab6")
            w.calib_save_btn.click()
            self.assertIn("lmfp2_lab6", config_mod.USER_CONFIGS)
            # 同 key 再存 → 确认框 No：不覆盖
            with mock.patch.object(gui_calib.QMessageBox, "question",
                                   return_value=QMessageBox.No):
                w.calib_save_btn.click()
            self.assertEqual(config_mod.USER_CONFIGS["lmfp2_lab6"]["label"],
                             "lmfp2_lab6")
            # 确认框 Yes：覆盖（换个备注也一样洗出同一个标识 → 走覆盖）
            w.calib_name_edit.setText("lmfp2_lab6（重标定）")
            with mock.patch.object(gui_calib.QMessageBox, "question",
                                   return_value=QMessageBox.Yes):
                w.calib_save_btn.click()
            self.assertEqual(config_mod.USER_CONFIGS["lmfp2_lab6"]["label"],
                             "lmfp2_lab6（重标定）")
            self.assertIn("已覆盖配置条目", w.log_text.toPlainText())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_suggest_config_key(self):
        """key 建议名：第一个数字段 +1，后缀数字（_lab6 的 6）不动。"""
        self.assertEqual(gui_config_ops._suggest_config_key("lmfp1_lab6"),
                         "lmfp2_lab6")
        self.assertEqual(gui_config_ops._suggest_config_key("lmfp12_lab6"),
                         "lmfp13_lab6")
        self.assertEqual(gui_config_ops._suggest_config_key("n7m3"), "n8m3")
        self.assertEqual(gui_config_ops._suggest_config_key("no_digits"),
                         "lab6_calib")

    def test_save_requires_a_geometry(self):
        """没有当前配置（借不到条目/几何为空）：按钮置灰 + 直调也拒绝。

        注意语义变化：现在保存的是**当前配置**——借来的条目、手改的
        几何、采纳的结果都可以存（新批次"借来→改→存"就是这么用的）；
        只有连几何都没有时才拒。
        """
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
            # 借到条目 → 可以存（借来的那份也是"当前配置"）
            self.assertTrue(w.calib_save_btn.isEnabled())
            self.assertIn("预填自 lmfp1_lab6", w.calib_save_hint.text())
            # 把几何清掉 → 置灰 + 直调拒绝
            gui_calib_model._calib_state(w)["current_geom"] = None
            gui_calib._calib_sync(w)
            self.assertFalse(w.calib_save_btn.isEnabled())
            self.assertEqual(w.calib_save_hint.text(), "还没有可保存的几何")
            gui_config_ops._save_calib_config(w)   # 按钮置灰点不到：直调处理函数
            self.assertIn("还没有可保存的几何", w.log_text.toPlainText())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_workers_attach_beam_center(self):
        """worker 附带 beam_center_rc：自动 = 新拟合环心，手动 = 初值 B。"""
        image = np.ones((256, 256)) * 10
        geom = {"pixel_size_m": 200e-6, "wavelength_m": 0.1223e-10,
                "dist_m": 1.5958}   # worker 先读几何键再调引擎（引擎 mock）
        with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                               return_value=image), \
             mock.patch.object(gui_calib_workers, "fit_center_from_rings",
                               return_value=TestCalibration.FAKE_CENTER), \
             mock.patch.object(gui_calib_workers, "calibrate_geometry",
                               return_value=dict(self.FAKE_AUTO)):
            res = gui_calib._auto_calib_worker("data/fake_a.tif", geom)
        self.assertEqual(res["beam_center_rc"],
                         (TestCalibration.FAKE_CENTER["cy"],
                          TestCalibration.FAKE_CENTER["cx"]))
        # 取点拟合失败 → FFT 兜底同样附带
        with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                               return_value=image), \
             mock.patch.object(gui_calib_workers, "fit_center_from_rings",
                               return_value=None), \
             mock.patch.object(gui_calib_workers, "find_ring_center",
                               return_value=(1021.0, 1023.0)), \
             mock.patch.object(gui_calib_workers, "calibrate_geometry",
                               return_value=dict(self.FAKE_AUTO)):
            res = gui_calib._auto_calib_worker("data/fake_a.tif", geom)
        self.assertEqual(res["beam_center_rc"], (1021.0, 1023.0))
        # 手动 worker：束心 = 初值 (列, 行) → (行, 列)
        # （首参 None = 没有标样路径 → 跳过引擎指标，本测试只管束心）
        with mock.patch.object(gui_calib_workers, "refine_geometry_from_points",
                               return_value=dict(self.FAKE_MANUAL)):
            res = gui_calib._manual_calib_worker(
                None, [(1, 2), (3, 4), (5, 6)], [0, 1, 2], geom,
                (1022.3, 1022.0))
        self.assertEqual(res["beam_center_rc"], (1022.0, 1022.3))


class TestHangWatchdog(unittest.TestCase):
    """卡死现场记录（用户 2026-10-01 报"出图按钮卡死"、本地复现不出来）。

    猜不得：真卡住时栈里写着那一刻每个线程停在哪一行。这条用一个**故意
    停摆**的界面线程验证——不处理事件 2 秒，看门狗该把现场写进文件。
    """

    def test_a_stalled_ui_thread_gets_its_stacks_written(self):
        folder = Path(tempfile.mkdtemp(prefix="xrd_hang_"))
        # 看门狗是 create_window() 挂上的：参数要在建窗口**之前**换好
        with mock.patch.object(gui_watchdog, "STALL_SECONDS", 0.4), \
             mock.patch.object(gui_watchdog, "POLL_SECONDS", 0.1), \
             mock.patch.object(gui_watchdog, "OUT_DIR", folder):
            w = create_window()
            try:
                QApplication.processEvents()
                time.sleep(2.0)          # 不处理事件 = 界面线程停摆
            finally:
                w.close()
            files = sorted(folder.glob("hang-*.txt"))
            self.assertTrue(files, "停摆后该写出一个现场文件")
            text = files[0].read_text(encoding="utf-8")
            self.assertIn("界面无响应", text)
            self.assertIn("Thread", text, "要带各线程的栈（faulthandler）")

    def test_a_busy_ui_thread_writes_nothing(self):
        """界面线程一直在转（正常情况）→ 不写现场、不打扰。"""
        folder = Path(tempfile.mkdtemp(prefix="xrd_hang_ok_"))
        with mock.patch.object(gui_watchdog, "STALL_SECONDS", 1.0), \
             mock.patch.object(gui_watchdog, "POLL_SECONDS", 0.1), \
             mock.patch.object(gui_watchdog, "OUT_DIR", folder):
            w = create_window()
            beat = w._hang_state
            # 打拍子交给**后台线程**：主线程在整套测试的负载下偶尔会被调度拖住，
            # 而"界面无响应"是相对墙钟的——靠主线程打拍子会偶发误判（这条用例抖过两次）
            stop = threading.Event()

            def tick():
                while not stop.is_set():
                    beat["beat"] += 1
                    time.sleep(0.02)

            t = threading.Thread(target=tick, daemon=True)
            t.start()
            try:
                deadline = time.time() + 2.5      # 心跳一直在跳 = 界面活着
                while time.time() < deadline:
                    QApplication.processEvents()
                    time.sleep(0.01)
            finally:
                stop.set()
                t.join(timeout=1.0)
            try:
                self.assertEqual(list(folder.glob("hang-*.txt")), [],
                                 "界面活着就不该写现场")
            finally:
                w.close()

    def test_closing_the_window_stops_the_monitor(self):
        """窗口关掉 → 监控线程自己退（防残留监控一起刷栈快照）。

        2026-10-02 实测：窗口关了监控还在；全量测试每个用例一个窗口，
        跑到后面几百个残留监控一起写全线程栈快照，套件被拖到一小时都
        跑不完。修法：closeEvent 后一拍停表（对话框取消关窗时不停）。
        """
        folder = Path(tempfile.mkdtemp(prefix="xrd_hang_close_"))

        def monitors():
            return sum(1 for t in threading.enumerate()
                       if t.name == "xrd-hang-watchdog")

        with mock.patch.object(gui_watchdog, "STALL_SECONDS", 0.4), \
             mock.patch.object(gui_watchdog, "POLL_SECONDS", 0.1), \
             mock.patch.object(gui_watchdog, "OUT_DIR", folder):
            w = create_window()
            QApplication.processEvents()
            before = monitors()
            w.close()
            deadline = time.time() + 3.0
            while time.time() < deadline and monitors() >= before:
                QApplication.processEvents()   # 让"停表那一拍"跑起来
                time.sleep(0.02)
            self.assertLess(monitors(), before, "关窗后监控线程该退出")


class TestManualReindex(unittest.TestCase):
    """手动判环可疑时的"尺度重判"（用户 2026-09-30 报的判错环）。

    原话："如果第 0 青环里面套了两个真实的环，点击真实的环，里面的两个环都会
    标记为第 0 环"——判环是拿当前几何把点换算成 2θ 再找最近的理论环，低角侧
    没有更低的环可判，所以**尺度一偏、最里面几个真实环全挤到第 0 环**。
    修法：同一环号横跨两个半径 = 火警 → 扫几个距离尺度、各自重判环再拟合，
    挑环位偏差最小的那套（只在明显更好时才换，口径与采纳门槛同一个 0.05 px）。
    """

    GEOM = {"pixel_size_m": 200e-6, "wavelength_m": 1.223e-11, "dist_m": 1.5958,
            "poni1_px": 1045.2, "poni2_px": 1022.0,
            "rot1_deg": 0.0, "rot2_deg": 0.0}
    # 同一份几何的**米制键**形状——这才是 GUI 实际交给后台的形状
    # （state["current_geom"] = 条目 geometry，poni1_m/poni2_m）
    GEOM_M = {"pixel_size_m": 200e-6, "wavelength_m": 1.223e-11, "dist_m": 1.5958,
              "poni1_m": 1045.2 * 200e-6, "poni2_m": 1022.0 * 200e-6,
              "rot1_deg": 0.0, "rot2_deg": 0.0}
    CENTER = (1022.0, 1021.5)          # (列, 行) = 束心
    # 半径 190 / 220 / 400 px 的三个点：前两个在"同一个环号"上但半径差 16%
    PTS = [(1022.0, 1211.5), (1022.0, 1241.5), (1022.0, 1421.5)]

    def test_suspicious_when_one_ring_index_spans_two_radii(self):
        self.assertTrue(gui_calib._rings_look_suspicious(
            self.PTS, [0, 0, 1], self.CENTER))
        self.assertFalse(gui_calib._rings_look_suspicious(
            self.PTS, [0, 1, 2], self.CENTER))
        # 同一个环上点两次（半径只差几个百分点）不算可疑
        same = [(1022.0, 1211.5), (1022.0, 1220.0), (1022.0, 1421.5)]
        self.assertFalse(gui_calib._rings_look_suspicious(
            same, [0, 0, 1], self.CENTER))

    @staticmethod
    def _fake_refine(points, rings, **kw):
        return {"rings_used": list(rings), "dist_m": kw["dist0_m"],
                "poni1_px": 1045.2, "poni2_px": 1022.0,
                "rot1_deg": 0.0, "rot2_deg": 0.0, "residual_deg": 0.004}

    def test_scale_search_picks_the_better_indexing(self):
        """尺度 0.9 那套拟合明显更好（0.10 vs 0.40 px）→ 换它，并写明理由。"""

        def fake_attach(res, image, geom, initial=None, **kw):
            dev = 0.10 if res["rings_used"] == [0, 1, 2] else 0.40
            res["metrics"] = {"dev_px": dev, "clip_frac": 0.0, "n_complete": 16,
                              "rings": [{}] * 16, "a": {"spread_ppm": 500.0}}

        with mock.patch.object(
                gui_calib_scale, "_rings_at_scale",
                side_effect=lambda p, g, c, s, **kw: ([0, 1, 2] if abs(s - 0.9) < 1e-9
                                                else [0, 0, 1])), \
             mock.patch.object(gui_calib_scale, "refine_geometry_from_points",
                               side_effect=self._fake_refine), \
             mock.patch.object(gui_calib_scale, "_attach_metrics",
                               side_effect=fake_attach):
            got, note, dist0 = gui_calib._reindex_by_scale(
                self.PTS, [0, 0, 1], self.GEOM, self.CENTER, object())
        self.assertEqual(got, [0, 1, 2], "该换成尺度 0.9 那套判环")
        self.assertIn("尺度 0.9", note)
        self.assertIn("0.10 px", note)
        self.assertIn("原判法 0.40 px", note)
        # 起点距离跟着赢家那套走（只换判环不换起点会落回另一支差解）
        self.assertAlmostEqual(dist0, self.GEOM["dist_m"] * 0.9)

    def test_metric_geometry_shape_survives_the_scale_search(self):
        """GUI 实际形状（米制键 poni1_m）也要能扫尺度，且与 px 键形状同判。

        回归（2026-10-05）：_rings_at_scale 曾经只认 px 键，而 GUI 交出的
        是米制键——用户一触发尺度扫描（任一环号横跨两个半径，正是这个兜底
        功能该干活的时候）就 KeyError: 'poni1_px'，日志写成"校准失败
        （手动）"。上面的用例都用 px 键桩几何 + mock 掉 _rings_at_scale，
        形状对不上也看不见。
        """
        def attach(res, img, g, initial=None, **kw):
            res.update({"metrics": {"dev_px": 0.30}})   # 各尺度持平 → 不换

        with mock.patch.object(gui_calib_scale, "refine_geometry_from_points",
                               side_effect=self._fake_refine), \
             mock.patch.object(gui_calib_scale, "_attach_metrics", side_effect=attach):
            got_m, note_m, d0_m = gui_calib._reindex_by_scale(
                self.PTS, [0, 0, 1], self.GEOM_M, self.CENTER, object())
            got_px, note_px, d0_px = gui_calib._reindex_by_scale(
                self.PTS, [0, 0, 1], self.GEOM, self.CENTER, object())
        self.assertEqual(got_m, got_px, "两种几何形状的判环结果必须一致")
        self.assertEqual(note_m, note_px)
        self.assertAlmostEqual(d0_m, d0_px)

    def test_no_switch_without_a_clear_win(self):
        """所有尺度一样好（或更差）→ 原样不动、不给说明（不猜）。"""
        with mock.patch.object(gui_calib_scale, "_rings_at_scale",
                               side_effect=lambda p, g, c, s, **kw: [0, 1, 2]), \
             mock.patch.object(gui_calib_scale, "refine_geometry_from_points",
                               side_effect=self._fake_refine), \
             mock.patch.object(gui_calib_scale, "_attach_metrics",
                               side_effect=lambda res, img, g, initial=None, **kw:
                               res.update({"metrics": {"dev_px": 0.30}})):
            got, note, dist0 = gui_calib._reindex_by_scale(
                self.PTS, [0, 0, 1], self.GEOM, self.CENTER, object())
        self.assertEqual(got, [0, 0, 1])
        self.assertEqual(note, "")
        self.assertEqual(dist0, self.GEOM["dist_m"])   # 不动 = 原距离照旧

    def test_no_image_means_no_judgement(self):
        """没有图像（面板没开）→ 连扫都不扫（没有判据就不猜）。"""
        got, note, dist0 = gui_calib._reindex_by_scale(
            self.PTS, [0, 0, 1], self.GEOM, self.CENTER, None)
        self.assertEqual(got, [0, 0, 1])
        self.assertEqual(note, "")
        self.assertEqual(dist0, self.GEOM["dist_m"])

    def test_worker_refits_from_the_winning_scale(self):
        """尺度扫描赢了 → 最终重拟合的起点距离也用赢家那套（不是原来的）。

        回归（2026-10-05）：曾经只把"判环"交出去、起点还用原距离，重拟合
        从错的初值出发落回另一支更差的解——真数据实测 0.31 px vs 7.55 px，
        日志里写"重判更合理——0.31 px"、拿到的却是 7.55 px 的几何。
        """
        seen = []

        def fake_refine(points, rings, **kw):
            seen.append(kw["dist0_m"])
            return self._fake_refine(points, rings, **kw)

        def attach(res, img, g, initial=None, **kw):
            dev = 0.10 if res["rings_used"] == [0, 1, 2] else 0.40
            res["metrics"] = {"dev_px": dev, "clip_frac": 0.0, "n_complete": 16,
                              "rings": [{}] * 16, "a": {"spread_ppm": 500.0}}

        with mock.patch.object(
                gui_calib_scale, "_rings_at_scale",
                side_effect=lambda p, g, c, s, **kw: ([0, 1, 2] if abs(s - 0.9) < 1e-9
                                                else [0, 0, 1])), \
             mock.patch.object(gui_calib_scale, "refine_geometry_from_points",
                               side_effect=fake_refine), \
             mock.patch.object(gui_calib_scale, "_attach_metrics", side_effect=attach), \
             mock.patch.object(gui_calib_panel, "load_diffraction_image",
                               return_value=np.ones((64, 64))):
            gui_calib._manual_calib_worker(
                "data/fake_a.tif", self.PTS, [0, 0, 1], self.GEOM_M, self.CENTER)
        self.assertAlmostEqual(seen[-1], self.GEOM_M["dist_m"] * 0.9,
                               msg="最终重拟合该从赢家尺度出发")


class TestCalibMetrics(unittest.TestCase):
    """引擎指标接线：附在结果 dict 上 + 写进日志后缀；失败不静默、不拖垮校准。

    指标本身（引擎语义、阈值、检出下限）在 test_ring_metrics.py 里用真
    几何合成图标定；这里只验 GUI 侧的接线与措辞。
    """

    FAKE_AUTO = dict(TestCalibration.FAKE_AUTO, dist_m=1.5958)
    FAKE_MANUAL = dict(TestCalibration.FAKE_MANUAL)
    FAKE_CENTER = TestCalibration.FAKE_CENTER
    GEO = {"pixel_size_m": 200e-6, "wavelength_m": 0.1223e-10,
           "dist_m": 1.6000, "poni1_m": 1045.2 * 200e-6,
           "poni2_m": 1022.0 * 200e-6, "rot1_deg": -0.005, "rot2_deg": -0.163}
    METRICS = {"dev_px": 0.52, "dev_signed_px": 0.1, "dev_rms_px": 0.6,
               "clip_frac": 0.0, "n_complete": 16, "n_rings_used": 16,
               "rings": [{} for _ in range(16)],
               "a": {"mean_angstrom": 4.1568, "spread_ppm": 812.0}}
    INITIAL = dict(METRICS, dev_px=3.14)

    def _logs(self, w):
        return w.log_text.toPlainText()

    # ── worker 侧：指标的附着位置 ──────────────────────────────
    def test_auto_worker_attaches_metrics_for_result_and_initial(self):
        """自动 worker：结果几何与初值几何各附一份（初值 = 面板预精修值）。"""
        seen = []

        def fake_rm(image, **kw):
            seen.append(kw["dist_m"])
            return dict(self.METRICS)

        with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                               return_value=np.ones((64, 64)) * 10), \
             mock.patch.object(gui_calib_workers, "fit_center_from_rings",
                               return_value=self.FAKE_CENTER), \
             mock.patch.object(gui_calib_workers, "calibrate_geometry",
                               return_value=dict(self.FAKE_AUTO)), \
             mock.patch.object(gui_calib_metrics, "ring_metrics",
                               side_effect=fake_rm):
            res = gui_calib._auto_calib_worker("data/fake_a.tif", self.GEO)
        self.assertEqual(res["metrics"]["dev_px"], 0.52)
        self.assertEqual(res["metrics_initial"]["dev_px"], 0.52)
        self.assertNotIn("metrics_error", res)
        # 两次调用的距离：先结果几何、后初值几何（GEOM 初值 1.6000 ≠ 结果 1.5958）
        self.assertEqual(seen, [self.FAKE_AUTO["dist_m"], self.GEO["dist_m"]])

    def test_manual_worker_metrics_need_a_path(self):
        """手动 worker：给了标样路径才读图算指标；没路径就跳过（不写键）。"""
        with mock.patch.object(gui_calib_workers, "refine_geometry_from_points",
                               return_value=dict(self.FAKE_MANUAL)), \
             mock.patch.object(gui_calib_panel, "load_diffraction_image",
                               return_value=np.ones((64, 64)) * 10), \
             mock.patch.object(gui_calib_metrics, "ring_metrics",
                               return_value=dict(self.METRICS)):
            res = gui_calib._manual_calib_worker(
                "data/fake_a.tif", [(1, 2), (3, 4), (5, 6)], [0, 1, 2],
                self.GEO, (1022.3, 1022.0))
        self.assertIsNotNone(res["metrics"])
        with mock.patch.object(gui_calib_workers, "refine_geometry_from_points",
                               return_value=dict(self.FAKE_MANUAL)):
            res_none = gui_calib._manual_calib_worker(
                None, [(1, 2), (3, 4), (5, 6)], [0, 1, 2],
                self.GEO, (1022.3, 1022.0))
        self.assertNotIn("metrics", res_none)

    # ── 失败路径：不许抛出，也不许静默 ─────────────────────────
    def test_attach_metrics_records_failure_without_raising(self):
        """几何键残缺（半截字典）→ 记 metrics_error，绝不抛出。"""
        res = dict(self.FAKE_MANUAL)
        gui_calib._attach_metrics(res, np.ones((8, 8)), {"dist_m": 1.0})
        self.assertIsNone(res["metrics"])
        self.assertIsNone(res["metrics_initial"])
        self.assertIn("KeyError", res["metrics_error"])
        # 日志后缀里也不许裸异常（2026-10-05：报错要让用户看得懂）
        self.assertIn("程序内部出错", res["metrics_error"])

    def test_worker_survives_engine_exception(self):
        """引擎抛异常 → 只丢指标；校准结果与束心完好（指标是显示器）。"""
        with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                               return_value=np.ones((64, 64)) * 10), \
             mock.patch.object(gui_calib_workers, "fit_center_from_rings",
                               return_value=self.FAKE_CENTER), \
             mock.patch.object(gui_calib_workers, "calibrate_geometry",
                               return_value=dict(self.FAKE_AUTO)), \
             mock.patch.object(gui_calib_metrics, "ring_metrics",
                               side_effect=RuntimeError("boom")):
            res = gui_calib._auto_calib_worker("data/fake_a.tif", self.GEO)
        self.assertIsNone(res["metrics"])
        self.assertEqual(res["metrics_error"], "RuntimeError: boom")
        self.assertEqual(res["dist_m"], self.FAKE_AUTO["dist_m"])
        self.assertEqual(res["beam_center_rc"],
                         (self.FAKE_CENTER["cy"], self.FAKE_CENTER["cx"]))

    def test_manual_worker_image_failure_only_loses_metrics(self):
        """手动侧读图失败（文件被挪走）→ 只丢指标，精修结果照常返回。"""
        with mock.patch.object(gui_calib_workers, "refine_geometry_from_points",
                               return_value=dict(self.FAKE_MANUAL)), \
             mock.patch.object(gui_calib_panel, "load_diffraction_image",
                               side_effect=OSError("文件不见了")):
            res = gui_calib._manual_calib_worker(
                "data/gone.tif", [(1, 2), (3, 4), (5, 6)], [0, 1, 2],
                self.GEO, (1022.3, 1022.0))
        self.assertIsNone(res["metrics"])
        self.assertIn("OSError", res["metrics_error"])
        self.assertEqual(res["dist_m"], self.FAKE_MANUAL["dist_m"])

    # ── 日志后缀：三种状态的措辞 ───────────────────────────────
    def test_metrics_note_formats_all_three_states(self):
        note = gui_calib._metrics_note(
            {"metrics": dict(self.METRICS), "metrics_initial": self.INITIAL})
        self.assertIn("环位偏差中位 0.52 px（初值 3.14 px）", note)
        self.assertIn("完整环 16/16", note)
        self.assertIn("标样 d 值离散度 812 ppm", note)
        # 无可用环信号：给证据（贴窗边比例）而不是数字
        nan = {"metrics": dict(self.METRICS, dev_px=float("nan"),
                               clip_frac=0.87, n_complete=0)}
        note_nan = gui_calib._metrics_note(nan)
        self.assertIn("无可用环信号", note_nan)
        self.assertIn("87%", note_nan)
        # clip_frac 本身也无值（搜索窗都放不进图像）时不许给用户看 nan%
        note_noclip = gui_calib._metrics_note(
            {"metrics": dict(self.METRICS, dev_px=float("nan"),
                             clip_frac=float("nan"), n_complete=0)})
        self.assertIn("搜索窗在图像内放不下", note_noclip)
        self.assertNotIn("nan", note_noclip)
        # 指标不可用：如实报错，不静默
        note_err = gui_calib._metrics_note(
            {"metrics": None, "metrics_error": "RuntimeError: boom"})
        self.assertIn("指标不可用", note_err)
        self.assertIn("boom", note_err)
        # 完全没指标（老结果/手动无路径）：后缀为空，不改动原日志
        self.assertEqual(gui_calib._metrics_note({}), "")

    def test_metrics_note_avoids_the_guard_phrase(self):
        """文案分离："全部落在图像外"是 _warn_rings_off_image 的守卫措辞，
        有测试在数它的出现次数——指标后缀不许复用同一句话。"""
        for state in ({"metrics": dict(self.METRICS)},
                      {"metrics": dict(self.METRICS, dev_px=float("nan"),
                                       clip_frac=1.0)},
                      {"metrics": None, "metrics_error": "X"}):
            self.assertNotIn("全部落在图像外",
                             gui_calib._metrics_note(state))

    def test_auto_done_log_carries_metrics_suffix(self):
        """端到端（mock 引擎与指标）：完成日志带指标后缀。"""
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10), \
                 mock.patch.object(gui_calib_workers, "fit_center_from_rings",
                                   return_value=self.FAKE_CENTER), \
                 mock.patch.object(gui_calib_workers, "calibrate_geometry",
                                   return_value=dict(self.FAKE_AUTO,
                                                     dist_m=1.5970)), \
                 mock.patch.object(gui_calib_metrics, "ring_metrics",
                                   side_effect=lambda image, **kw:
                                   dict(self.METRICS if kw["dist_m"] == 1.5970
                                        else self.INITIAL)):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
                w.calib_pixel_chk.setChecked(True)   # 校准前置：确认像素尺寸
                w.calib_start_auto.click()
                self.assertTrue(_wait_until(
                    lambda: any(r["name"].startswith("自动")
                                for r in w.calib_state["results"]), 8000))
            logs = self._logs(w)
            self.assertIn("自动取点完成（自动取点1，LaB₆）", logs)
            self.assertIn("环位偏差中位 0.52 px（初值 3.14 px）", logs)
            self.assertIn("标样 d 值离散度 812 ppm", logs)
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()


class TestCalibModel(unittest.TestCase):
    """校准功能页的纯逻辑：累积命名 / 槽轮换与钉住 / 采纳判据 / 表格与 Δ。"""

    @staticmethod
    def _res(dev=0.30, dist=1.5958, poni=(1045.2, 1022.0),
             rot=(-0.005, -0.163), center=(1021.5, 1022.0)):
        return {"dist_m": dist, "poni1_px": poni[0], "poni2_px": poni[1],
                "rot1_deg": rot[0], "rot2_deg": rot[1],
                "beam_center_rc": center, "residual_deg": 0.004,
                "metrics": None if dev is None else {
                    "dev_px": dev, "clip_frac": 0.0, "n_complete": 16,
                    "rings": [{}] * 16, "a": {"spread_ppm": 500.0}}}

    @staticmethod
    def _state(**kw):
        st = {"points": [], "results": [],
              "slots": {"current": None, "A": None, "B": None},
              "pinned": {"A": False, "B": False}, "next_slot": "A",
              "current_geom": {"pixel_size_m": 200e-6,
                               "wavelength_m": 0.1223e-10, "dist_m": 1.5958},
              "current_from": "预填自 lmfp1_lab6",
              "current_metrics": None, "current_error": None,
              "custom": False, "counters": {}}
        st.update(kw)
        return st

    def _with(self, st, kind, **kw):
        """往状态里挂一条结果，返回名字。"""
        return gui_calib_model._add_result(st, kind, self._res(**kw))

    def test_clear_session_keeps_geometry_and_readds_raw(self):
        """换标样复位：清选点/结果/槽/指标，但当前几何与标样设置保留。"""
        geo = {"pixel_size_m": 200e-6, "wavelength_m": 0.1223e-10,
               "dist_m": 1.5958, "poni1_m": 1045.2 * 200e-6,
               "poni2_m": 1022.0 * 200e-6, "rot1_deg": -0.005,
               "rot2_deg": -0.163, "beam_center_rc": (1022.0, 1022.3)}
        st = self._state(
            points=[(1.0, 2.0, 3)], results=[{
                "name": "自动取点1", "kind": "auto",
                "result": self._res()}],
            slots={"current": "自动取点1", "A": "自动取点1", "B": None},
            pinned={"A": True, "B": True}, counters={"auto": 1},
            current_from="自动取点1",
            current_geom=geo, calibrant="CeO2", n_rings=10,
            n_rings_auto=False)
        gui_calib_model._clear_calib_session(st)
        self.assertEqual(st["points"], [])
        self.assertEqual(st["selected"], None)
        self.assertEqual(len(st["results"]), 1)
        self.assertEqual(st["results"][0]["kind"], "raw")
        self.assertEqual(st["results"][0]["name"], "原始1")
        self.assertEqual(st["slots"], {"current": None, "A": None, "B": None})
        self.assertEqual(st["pinned"], {"A": False, "B": False})
        self.assertEqual(st["counters"], {"raw": 1})
        self.assertIs(st["current_geom"], geo)       # 几何原样保留
        self.assertEqual(st["current_from"], "换标样前保留的几何")
        self.assertEqual(st["calibrant"], "CeO2")
        self.assertEqual(st["n_rings"], 10)

    # ── 累积命名 ──────────────────────────────────────────
    def test_names_accumulate_per_kind(self):
        st = self._state()
        self.assertEqual(self._with(st, "raw"), "原始1")
        self.assertEqual(self._with(st, "auto"), "自动取点1")
        self.assertEqual(self._with(st, "manual"), "手动选点1")
        self.assertEqual(self._with(st, "auto"), "自动取点2")
        self.assertEqual([r["name"] for r in st["results"]],
                         ["原始1", "自动取点1", "手动选点1", "自动取点2"])

    # ── 槽：轮换 + 钉住 ───────────────────────────────────
    def test_slots_fill_a_then_b(self):
        st = self._state()
        n1 = self._with(st, "auto")
        self.assertIn("A", gui_calib_model._fill_slot(st, n1))
        n2 = self._with(st, "auto")
        self.assertIn("B", gui_calib_model._fill_slot(st, n2))
        self.assertEqual((st["slots"]["A"], st["slots"]["B"]), (n1, n2))
        n3 = self._with(st, "manual")
        gui_calib_model._fill_slot(st, n3)
        self.assertEqual(st["slots"]["A"], n3)      # 回到 A（轮换）

    def test_pinned_slot_is_skipped(self):
        st = self._state()
        st["slots"]["A"] = "手动选点1"
        st["pinned"]["A"] = True
        note = gui_calib_model._fill_slot(st, "自动取点1")
        self.assertIn("B", note)
        self.assertEqual(st["slots"]["A"], "手动选点1")   # 钉住的没被覆盖
        self.assertEqual(st["slots"]["B"], "自动取点1")

    def test_both_slots_pinned_keeps_result_only_in_list(self):
        st = self._state()
        st["pinned"] = {"A": True, "B": True}
        note = gui_calib_model._fill_slot(st, "自动取点1")
        self.assertIn("只进列表", note)
        self.assertIsNone(st["slots"]["A"])
        self.assertIsNone(st["slots"]["B"])

    # ── 采纳判据（"拟合得好不好"）─────────────────────────
    def test_first_result_adopted_when_nothing_to_compare(self):
        st = self._state()          # 借来的起点 → 直接采纳
        name = self._with(st, "auto", dev=0.30)
        take, note = gui_calib_model._adopt_decision(st, name)
        self.assertTrue(take)
        self.assertIn("当前配置 → 自动取点1", note)

    def _from_result(self, dev, **kw):
        """当前配置 = 某条跑出来的结果（这时才用得上 0.05 px 门槛）。"""
        st = self._state(**kw)
        st["slots"]["current"] = "自动取点1"
        st["current_from"] = "自动取点1"
        st["current_metrics"] = {"dev_px": dev}
        # 走 _add_result：计数器要跟着推进，否则下一条又命名成"自动取点1"
        gui_calib_model._add_result(st, "auto", self._res(dev=dev))
        return st

    def test_borrowed_start_is_replaced_by_the_first_result(self):
        """新批次：当前配置还是借来的出发点 → 第一条结果**总是**采纳。

        借来的几何是在别的批次的图上量出来的，它的环位偏差在这张图上没有
        可比性——它是起点，不是候选者。"""
        st = self._state(current_metrics={"dev_px": 0.10})   # 借来的"看着更好"
        name = self._with(st, "auto", dev=0.40)
        take, note = gui_calib_model._adopt_decision(st, name)
        self.assertTrue(take)
        self.assertIn("新批次的第一条结果", note)

    def test_better_result_is_adopted(self):
        st = self._from_result(0.52)
        name = self._with(st, "auto", dev=0.28)
        take, note = gui_calib_model._adopt_decision(st, name)
        self.assertTrue(take)
        self.assertIn("自动取点2", note)
        self.assertIn("比 自动取点1 的 0.52 px 好 0.24 px", note)

    def test_marginally_better_is_adopted_and_says_so(self):
        """现在**一律采纳**（用户 2026-09-30："只要是用户操作的……都填入当前，
        让用户看到变化"），0.05 px 只用来分档措辞（优于 / 差不多 / 差多少）。"""
        st = self._from_result(0.30)
        name = self._with(st, "auto", dev=0.28)
        take, note = gui_calib_model._adopt_decision(st, name)
        self.assertTrue(take, "用户按钮跑出来的结果一律进当前配置")
        self.assertIn("当前配置 → 自动取点2", note)
        self.assertIn("差不多", note)

    def test_worse_result_is_adopted_and_says_how_much_worse(self):
        """更差也采纳（按钮是用户按的），但日志要写明差了多少。"""
        st = self._from_result(0.24)
        name = self._with(st, "manual", dev=0.31)
        take, note = gui_calib_model._adopt_decision(st, name)
        self.assertTrue(take)
        self.assertIn("当前配置 → 手动选点1", note)
        self.assertIn("差 0.07 px", note)

    def test_hand_edited_geometry_is_replaced_but_kept_in_the_list(self):
        """手输/手改的几何现在也会被按钮结果覆盖（同上），但说明里必须点明
        "那份仍在结果列表里"——东西没丢，只是当前用的换了。"""
        st = self._state(custom=True, current_metrics={"dev_px": 0.52})
        name = self._with(st, "auto", dev=0.10)
        take, note = gui_calib_model._adopt_decision(st, name)
        self.assertTrue(take)
        self.assertIn("自定义", note)
        self.assertIn("随时能选回来", note)

    def test_metrics_dev_accepts_both_shapes(self):
        """口径：结果 dict 里套着 metrics，而"当前配置"的指标就是 metrics。"""
        self.assertEqual(
            gui_calib_model._result_dev({"metrics": {"dev_px": 0.25}}), 0.25)
        self.assertEqual(gui_calib_model._metrics_dev({"dev_px": 0.25}), 0.25)
        for bad in (None, {}, {"dev_px": float("nan")}):
            self.assertIsNone(gui_calib_model._metrics_dev(bad))
            self.assertIsNone(gui_calib_model._result_dev({"metrics": bad}))
            self.assertIsNone(gui_calib_model._result_dev(bad))

    # ── 表格：取值 / Δ 行 / 结论 ──────────────────────────
    def test_row_values_and_delta_text(self):
        a = self._res(dist=1.5962, dev=0.28, center=(1021.0, 1022.0))
        base = self._res(dist=1.5958, dev=0.52, center=(1024.0, 1022.0))
        vals = gui_calib_model._row_values(a)
        self.assertAlmostEqual(vals["dist"], 1.5962)
        self.assertAlmostEqual(vals["center_r"], 1021.0)
        self.assertEqual(vals["dev"], 0.28)
        self.assertEqual(gui_calib_model._fmt_row("dist", vals["dist"]), "1596.20")
        self.assertEqual(gui_calib_model._delta_text(base, a, "dist"), "+0.40")
        self.assertEqual(gui_calib_model._delta_text(base, a, "dev"), "-0.24")
        # Δ 只给"距离 / 环位偏差"两行（白名单），其余量显示 —
        self.assertEqual(gui_calib_model._delta_text(base, a, "center_r"), "—")
        self.assertEqual(gui_calib_model._delta_text(base, a, "poni1"), "—")
        # 基准列自身 → —
        self.assertEqual(gui_calib_model._delta_text(a, a, "dist"), "—")

    def test_delta_rows_are_whitelisted(self):
        rows = [k for k, _n, _s, _f, has_d in gui_calib_model.COMPARE_ROWS]
        self.assertEqual(rows, ["dist", "center_r", "center_c", "dev",
                                "poni1", "poni2", "rot1", "rot2"])
        has_delta = [k for k, _n, _s, _f, d in gui_calib_model.COMPARE_ROWS if d]
        self.assertEqual(has_delta, ["dist", "dev"])   # 只给这两行 Δ
        self.assertFalse(any("残差" in n for _k, n, *_ in
                             gui_calib_model.COMPARE_ROWS))
        self.assertIn("退化方向", gui_calib_model.COMPARE_HINT)

    def test_verdict_names_both_sides(self):
        """结论必须写清"谁和谁比"：每个候选 vs **当前配置**，数字都带名字。

        用户 2026-09-27："对比基准和结论表述不清，不知道是谁在和谁比"——
        中间有一版结论那一对是从 A/B 里挑的（基准选 A 时"当前配置"被撇开），
        同一屏里 Δ 行和结论讲的不是一对人。2026-09-30 用户把基准下拉整个
        删了：参照系固定「当前配置」，A、B 都报（见 _verdict 的说明）。
        """
        v = gui_calib_model._verdict(self._res(dev=0.52), "当前配置",
                                     [("A", self._res(dev=0.28))])
        self.assertIn("A 比 当前配置 好 0.24 px", v)
        self.assertIn("当前配置 0.52 px", v, "数字要带名字")
        self.assertIn("A 0.28 px", v)
        # 候选更差也要明说（旧版只报"谁更好"，看不出谁比谁差）
        v = gui_calib_model._verdict(self._res(dev=0.28), "当前配置",
                                     [("A", self._res(dev=0.52))])
        self.assertIn("A 比 当前配置 差 0.24 px", v)
        # 两个候选一起报，各自点名
        v = gui_calib_model._verdict(self._res(dev=0.30), "当前配置",
                                     [("A", self._res(dev=0.28)),
                                      ("B", self._res(dev=0.90))])
        self.assertIn("A 与 当前配置 差不多", v)
        self.assertIn("B 比 当前配置 差 0.60 px", v)
        # 判不了的时候也点名（谁缺值说谁）
        self.assertIn("当前配置 没有可用环位偏差", gui_calib_model._verdict(
            self._res(dev=None), "当前配置", [("A", self._res(dev=0.28))]))
        self.assertIn("A 没有可用环位偏差", gui_calib_model._verdict(
            self._res(dev=0.30), "当前配置", [("A", self._res(dev=None))]))
        self.assertIn("还没有可比的候选", gui_calib_model._verdict(
            self._res(dev=0.30), "当前配置", []))
        # 缺值显示 —（不许把 nan 打给用户）
        self.assertEqual(gui_calib_model._fmt_row("dev", float("nan")), "—")

    def test_slot_label_states(self):
        st = self._state()
        self.assertEqual(gui_calib_model._slot_label(st, "current"), "预填自 lmfp1_lab6")
        self.assertEqual(gui_calib_model._slot_label(st, "A"), "—")
        st["custom"] = True
        self.assertEqual(gui_calib_model._slot_label(st, "current"), "自定义")
        st["slots"]["A"] = "自动取点1"
        self.assertEqual(gui_calib_model._slot_label(st, "A"), "自动取点1")


class TestCalibCurrent(unittest.TestCase):
    """当前配置：默认借条目 / 编辑对话框 → 自定义 / 像素确认规则 (b)。"""

    def test_enter_borrows_the_selected_entry(self):
        """进校准模式：当前配置默认借分析页选中的条目，并登记"原始1"。"""
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
            st = w.calib_state
            cfg = gui_app.CONFIGS["lmfp1_lab6"]["geometry"]
            self.assertAlmostEqual(st["current_geom"]["dist_m"], cfg["dist_m"])
            self.assertEqual(st["current_from"], "预填自 lmfp1_lab6")
            self.assertEqual([r["name"] for r in st["results"]], ["原始1"])
            self.assertFalse(st["custom"])
            self.assertIn("预填自 lmfp1_lab6", w.calib_current_lbl.text())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_edit_dialog_marks_custom_and_resets_pixel_ok(self):
        """[编辑…] 改过 → 标"自定义"，像素确认作废（像素值可能变了）。"""
        from PySide6.QtWidgets import QDialog
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
                w.calib_pixel_chk.setChecked(True)
                self.assertTrue(gui_calib._pixel_ok(w))
                # 替换 exec：既能拿到对话框对象，也能在"确定"前改控件值
                holder = {"pixel": None}

                def fake_exec(self):
                    holder["dlg"] = self
                    boxes = self.findChildren(QDoubleSpinBox)
                    if holder["pixel"] is not None:
                        boxes[0].setValue(holder["pixel"])
                    return QDialog.Accepted
                with mock.patch.object(QDialog, "exec", new=fake_exec):
                    gui_calib._edit_current(w)
                st = w.calib_state
                self.assertTrue(st["custom"])
                self.assertEqual(st["current_from"], "手输")
                self.assertEqual(gui_calib_model._slot_label(st, "current"), "自定义")
                self.assertIsNone(st["slots"]["current"])
                self.assertIn("自定义", w.log_text.toPlainText())
                # 规则 (b)：数值没动过 → 像素确认**保持**（不打扰）
                self.assertTrue(gui_calib._pixel_ok(w))
                # 真改像素 → 确认作废（勾选框也同步取消）
                self.assertIn("µm",
                              holder["dlg"].findChildren(QDoubleSpinBox)[0]
                              .suffix())
                holder["pixel"] = 150.0
                with mock.patch.object(QDialog, "exec", new=fake_exec):
                    gui_calib._edit_current(w)
                self.assertFalse(gui_calib._pixel_ok(w))
                self.assertFalse(w.calib_pixel_chk.isChecked())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    # ── 青线跟着"当前配置"走（2026-09-26 用户报：编辑当前配置青环不动）──

    def _ring_lines(self, w):
        """画布上的青环折线 (x, y)：此刻没有绿点/选点标记，lines 就是环。"""
        return [(np.asarray(line.get_xdata(), dtype=float),
                 np.asarray(line.get_ydata(), dtype=float))
                for line in w.calib_ax.lines]

    def _assert_rings_match_geometry(self, w):
        """画出来的每条青线 == 按"当前配置"独立算出的那条环路径。

        用"逐点等于重算结果"而不是"和上一次不同"：前者钉住的是真正
        要的不变量（画的就是当前配置的几何），几何微调也能抓到。
        """
        px = gui_calib_panel._geom_px_keys(w.calib_state["current_geom"])
        paths = gui_calib_panel_draw.theoretical_ring_paths(
            pixel_size_m=px["pixel_size_m"], wavelength_m=px["wavelength_m"],
            dist_m=px["dist_m"], poni1_px=px["poni1_px"],
            poni2_px=px["poni2_px"], rot1_deg=px["rot1_deg"],
            rot2_deg=px["rot2_deg"], image_shape=(256, 256))
        drawn = self._ring_lines(w)
        self.assertEqual(len(drawn), len(paths["rings"]))
        for (got_x, got_y), (_, want) in zip(drawn, paths["rings"]):
            np.testing.assert_allclose(got_x, want[:, 0], equal_nan=True)
            np.testing.assert_allclose(got_y, want[:, 1], equal_nan=True)

    def _rings_equal(self, first, second):
        """两次采样的青线是否逐点相同（用来抓"根本没重画"）。"""
        return all(all(np.allclose(a, b, equal_nan=True)
                       for a, b in zip(p, q))
                   for p, q in zip(first, second))

    def test_edit_dialog_redraws_the_rings(self):
        """[编辑…] 改几何 → 青线按新几何重画（表里的数字换了，图也得换）。

        原来只换状态与表格、漏了重画：改完距离，图上还是旧几何的环。
        """
        from PySide6.QtWidgets import QDialog
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
                w.calib_pixel_chk.setChecked(True)
                before = self._ring_lines(w)
                self._assert_rings_match_geometry(w)
                dist0 = w.calib_state["current_geom"]["dist_m"]

                def fake_exec(self):
                    boxes = self.findChildren(QDoubleSpinBox)
                    boxes[2].setValue(boxes[2].value() * 1.05)   # 距离 +5%
                    return QDialog.Accepted

                with mock.patch.object(QDialog, "exec", new=fake_exec):
                    gui_calib._edit_current(w)
                self.assertAlmostEqual(
                    w.calib_state["current_geom"]["dist_m"], dist0 * 1.05,
                    places=5)
                after = self._ring_lines(w)
                self.assertFalse(self._rings_equal(before, after),
                                 "几何改了而青线与改前逐点相同 = 没有重画")
                self._assert_rings_match_geometry(w)
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_borrow_entry_redraws_rings_and_skips_closed_panel(self):
        """借用条目换了几何 → 青线跟着换；面板没开时借用不碰画布。

        借用是 [加载参数]（导入 .poni）那条通道，分析模式下就能点到：
        那时画布随面板一起没了（calib_dock=None，calib_ax 是悬空旧对象），
        重画必须跳过而不是画到已删除的画布上。
        """
        other = {"label": "测试几何", "beam_center": (120.0, 130.0),
                 "geometry": {"pixel_size_m": 200e-6,
                              "wavelength_m": 1.223e-11, "dist_m": 1.30,
                              "poni1_m": 0.20904, "poni2_m": 0.20440,
                              "rot1_deg": 0.0, "rot2_deg": 0.4}}
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
                w.calib_pixel_chk.setChecked(True)
                before = self._ring_lines(w)
                with mock.patch.dict(config_mod.CONFIGS, {"test_geom": other}):
                    gui_calib._borrow_entry(w, "test_geom")
                    self.assertIn("预填自条目 test_geom", w.log_text.toPlainText())
                    after = self._ring_lines(w)
                    self.assertFalse(self._rings_equal(before, after),
                                     "借用了新几何而青线与借前逐点相同 = 没有重画")
                    self._assert_rings_match_geometry(w)
                    # 退出校准模式（面板关闭）后再借用：静默跳过重画。
                    # 五个入口按钮互斥，click() 不会取消已选中的那个，
                    # 退出走 setChecked(False)（[返回分析模式] 也是这么做的）
                    w.calib_btn.setChecked(False)
                    self.assertIsNone(w.calib_dock)
                    with mock.patch.object(
                            gui_calib_panel_draw, "_redraw_calib",
                            side_effect=AssertionError("画到已关面板上")):
                        gui_calib._borrow_entry(w, "test_geom")
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_pixel_confirmation_only_rearms_when_value_changes(self):
        """规则 (b)：换来源/再借同一像素的条目**不**要求重确认；改像素才要。"""
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
                w.calib_pixel_chk.setChecked(True)
                self.assertTrue(gui_calib._pixel_ok(w))
                # 借另一条像素尺寸相同的条目 → 仍然算已确认
                gui_calib._borrow_entry(w, "lmfp1_lab6")
                self.assertTrue(gui_calib._pixel_ok(w))
                self.assertTrue(w.calib_pixel_chk.isChecked())
                # 手改像素（对话框里改 200 µm → 150 µm）→ 确认作废
                from PySide6.QtWidgets import QDialog
                dlg_holder = {}

                def fake_exec(self):
                    dlg_holder["dlg"] = self
                    return QDialog.Rejected      # 先看看对话框里的控件
                with mock.patch.object(QDialog, "exec", new=fake_exec):
                    gui_calib._edit_current(w)
                dlg = dlg_holder["dlg"]
                boxes = dlg.findChildren(QDoubleSpinBox)
                self.assertTrue(boxes)
                gui_calib._set_pixel_ok(w, True)
                # 改掉几何里的像素（等价于对话框里改过像素）+ 同步界面
                st = w.calib_state
                st["current_geom"]["pixel_size_m"] = 150e-6
                gui_calib._calib_sync(w)
                self.assertFalse(gui_calib._pixel_ok(w))
                self.assertFalse(w.calib_pixel_chk.isChecked())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_calibration_warns_but_runs_without_pixel_confirmation(self):
        """没核对像素尺寸 → 照常跑，只提醒（用户 2026-10-05："改为提醒的
        样式，不要求用户必须选择了"；原来这里是硬门禁，不建任务）。"""
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10), \
                 mock.patch.object(gui_calib_workers, "fit_center_from_rings",
                                   return_value=TestCalibration.FAKE_CENTER), \
                 mock.patch.object(gui_calib_workers, "calibrate_geometry",
                                   return_value=dict(TestCalibration.FAKE_AUTO)):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
                self.assertTrue(w.calib_pixel_warn.isVisible(),
                                "没核对该亮着橙色提醒")
                w.calib_start_auto.click()
                self.assertIn("calib_auto", w._latest_task)   # 任务照常建了
                self.assertIn("像素尺寸还没核对", w.log_text.toPlainText())
                w.calib_pixel_chk.setChecked(True)
                self.assertFalse(w.calib_pixel_warn.isVisible(),
                                 "核对后提醒该灭")
            self.assertIn("已核对像素尺寸：200.0 µm", w.log_text.toPlainText())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def _hand_edit(self, w, pixel=None, beam_row=None, beam_col=None):
        """走一遍 [编辑当前配置…] 的对话框（exec 换成"直接确定"）。"""
        def fake_exec(self):
            for name, value in (("beam_row_spin", beam_row),
                                ("beam_col_spin", beam_col)):
                if value is not None:
                    self.findChild(QDoubleSpinBox, name).setValue(value)
            if pixel is not None:
                self.findChildren(QDoubleSpinBox)[0].setValue(pixel)
            return QDialog.Accepted
        with mock.patch.object(QDialog, "exec", new=fake_exec):
            gui_calib._edit_current(w)

    def _enter_calib(self, w):
        w.show()
        with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                               return_value=np.ones((256, 256)) * 10):
            add_checked(w, ["data/fake_a.tif"])
            w.calib_btn.click()
            w.calib_pixel_chk.setChecked(True)
        return w.calib_state

    def test_hand_entered_geometry_lands_in_the_result_list(self):
        """手输几何要**进累积结果列表**：A/B 下拉能选到它、能被采纳。

        用户 2026-09-28 第 1 条："校准自定义的几何配置不会计入原始值或者说
        不会存储在选框里"——改之前它只活在 state["current_geom"] 里（没有
        户口）：切走就找不回来、放不进 A/B 槽、也参与不了对比。
        """
        w = create_window()
        try:
            st = self._enter_calib(w)
            self._hand_edit(w, pixel=150.0, beam_row=1021.0, beam_col=1023.0)
            self.assertTrue(st["custom"])
            names = [r["name"] for r in st["results"]]
            self.assertIn("自定义1", names, "手输几何该进结果列表")
            # A 槽的下拉里能选到它（选项 = 结果名）
            gui_calib_table._sync_slot_combos(w)
            combo = w.calib_slot_combo["A"]
            self.assertGreaterEqual(combo.findData("自定义1"), 0,
                                    "A 槽下拉里要能选到「自定义1」")
            # 放进 A 槽 → 表里 A 列就是这条几何（环心行跟着它）
            combo.setCurrentIndex(combo.findData("自定义1"))
            QApplication.processEvents()
            self.assertEqual(st["slots"]["A"], "自定义1")
            self.assertEqual(w.calib_vals["A"]["center_r"].text(), "1021.00")
            # 再选回"当前配置"里的自定义那条 → 保护标记跟着回来
            # （否则下一次自动结果会把手输的东西悄悄替换掉）
            gui_calib._adopt_result(w, "自定义1", why="")
            self.assertTrue(st["custom"], "重新选中自定义那条要恢复保护")
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_edit_dialog_sets_the_beam_center(self):
        """对话框里能改**环心行/列**（用户 2026-09-28 第 2 条，2甲②）。

        改之前对话框里根本没有束心输入框：`_beam` 存了却从没被读回，
        手输几何的束心永远是接手时那一个——而束心正是校准最常动的东西。
        """
        w = create_window()
        try:
            st = self._enter_calib(w)
            self._hand_edit(w, beam_row=1234.5, beam_col=987.25)
            self.assertEqual(st["current_geom"]["beam_center_rc"],
                             (1234.5, 987.25))
            # 表里的"环心行/环心列"两行跟着它
            self.assertIn("1234.5", w.calib_vals["current"]["center_r"].text())
            self.assertIn("987.2", w.calib_vals["current"]["center_c"].text())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()


class TestCalibFlow(unittest.TestCase):
    """三步动作 + 槽 + 采纳 + 坞宽 + 保存血缘（真窗口，引擎 mock）。"""

    FAKE_AUTO = dict(TestCalibration.FAKE_AUTO)
    FAKE_CENTER = TestCalibration.FAKE_CENTER

    def _metrics(self, dev):
        return {"dev_px": dev, "clip_frac": 0.0, "n_complete": 16,
                "rings": [{}] * 16, "a": {"spread_ppm": 500.0}}

    def test_auto_lands_in_results_slot_and_adopts_when_better(self):
        """①：结果进列表 + 填 A 槽；比借来的几何好 → 采纳为当前配置。"""
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10), \
                 mock.patch.object(gui_calib_workers, "fit_center_from_rings",
                                   return_value=self.FAKE_CENTER), \
                 mock.patch.object(gui_calib_workers, "calibrate_geometry",
                                   return_value=dict(self.FAKE_AUTO,
                                                     dist_m=1.5965)), \
                 mock.patch.object(gui_calib_metrics, "ring_metrics",
                                   side_effect=lambda image, **kw:
                                   self._metrics(0.20 if kw["dist_m"] > 1.596
                                                 else 0.60)):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
                w.calib_pixel_chk.setChecked(True)
                _wait_until(lambda: gui_calib._pixel_ok(w) and
                            w.calib_state["current_metrics"] is not None, 8000)
                w.calib_start_auto.click()
                self.assertTrue(_wait_until(
                    lambda: w.calib_state["slots"]["A"] == "自动取点1", 8000))
            st = w.calib_state
            self.assertEqual([r["name"] for r in st["results"]],
                             ["原始1", "自动取点1"])
            self.assertEqual(st["slots"]["current"], "自动取点1")   # 0.20 < 0.60
            self.assertFalse(st["custom"])
            self.assertIn("当前配置 ← 自动取点1", w.log_text.toPlainText())
            self.assertEqual(w.calib_vals["current"]["dev"].text(), "0.20")
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_the_refined_button_is_gone(self):
        """2026-10-08 裁决：删 [再精修]、两个入口收敛成一个。

        依据是真 lab6 实测：它与 [定位束心并精修] 等价（环位偏差
        0.24 vs 0.23 px，在重复跑抖动 0.014~0.029 px 以内），连按两个
        按钮结果不变。这条守住删除本身，防将来无意加回。
        """
        w = create_window()
        try:
            self.assertIsNone(w.findChild(QPushButton, "start_refined_calib"))
            self.assertFalse(hasattr(w, "calib_start_refined"))
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_slot_selection_pins_and_adopts(self):
        """槽下拉选一条结果 → 钉住（新结果不再覆盖）+ 采纳为当前配置。"""
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10), \
                 mock.patch.object(gui_calib_workers, "fit_center_from_rings",
                                   return_value=self.FAKE_CENTER), \
                 mock.patch.object(gui_calib_workers, "calibrate_geometry",
                                   return_value=dict(self.FAKE_AUTO)), \
                 mock.patch.object(gui_calib_metrics, "ring_metrics",
                                   side_effect=lambda image, **kw:
                                   self._metrics(0.60)):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
                w.calib_pixel_chk.setChecked(True)
                w.calib_start_auto.click()
                self.assertTrue(_wait_until(
                    lambda: w.calib_state["slots"]["A"] == "自动取点1", 8000))
                # 再来一条：A 被钉住 → 进 B（仍在 mock 生效范围内）
                w.calib_slot_combo["A"].setCurrentIndex(
                    w.calib_slot_combo["A"].findData("原始1"))
                w.calib_start_auto.click()
                self.assertTrue(_wait_until(
                    lambda: w.calib_state["slots"]["B"] == "自动取点2", 8000))
            # 手动把 A 切回"原始1" → 钉住（对比位不动当前配置）
            combo = w.calib_slot_combo["A"]
            combo.setCurrentIndex(combo.findData("原始1"))
            st = w.calib_state
            self.assertEqual(st["slots"]["A"], "原始1")
            self.assertTrue(st["pinned"]["A"])
            self.assertIn("对比位 A → 原始1（钉住", w.log_text.toPlainText())
            # 在"当前配置"那一列选原始1 → 采纳（换图上的青线）
            combo_cur = w.calib_slot_combo["current"]
            combo_cur.setCurrentIndex(combo_cur.findData("原始1"))
            self.assertEqual(st["slots"]["current"], "原始1")
            self.assertFalse(st["custom"])
            self.assertIn("当前配置 ← 原始1", w.log_text.toPlainText())
            self.assertEqual(st["slots"]["B"], "自动取点2")   # 新结果没覆盖 A
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_dock_widens_on_enter_and_restores_on_exit(self):
        """进校准模式按内容拉宽参数坞（给绘图区留 620 px），退出还原。

        先点过 [1D]（参数坞可见）再进校准——开局它是收起的，量不到
        "之前的宽度"，那种情况由下一条测试单独守。
        """
        w = create_window()
        try:
            w.show()
            w.entrance_buttons["1D"].click()
            QApplication.processEvents()
            before = w.param_dock.width()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
            QApplication.processEvents()
            page = w.calib_scroll.widget()
            self.assertGreaterEqual(w.param_dock.width(),
                                    page.sizeHint().width())
            self.assertLessEqual(w.param_dock.width(),
                                 w.width() - gui_calib_model.CALIB_PANEL_RESERVE_PX + 2)
            w.entrance_buttons["1D"].click()   # 点别的入口 = 退出校准
            QApplication.processEvents()
            self.assertAlmostEqual(w.param_dock.width(), before, delta=2)
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_calib_widens_dock_when_opened_first_thing(self):
        """开局第一件事就点 [校准]（参数坞刚从隐藏转可见）也要按内容拉宽。

        量宽度前先手动走一遍布局（app._clear_entrance 让坞开局收起，
        刚 setVisible(True) 时 width() 还是旧值——2026-09-25 修）。
        """
        w = create_window()
        try:
            w.show()
            QApplication.processEvents()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
            QApplication.processEvents()
            page = w.calib_scroll.widget()
            self.assertGreaterEqual(w.param_dock.width(),
                                    page.sizeHint().width())
            # 退出校准：没选过入口 → 坞收起；再点 [1D] 露出时必须是分析
            # 模式的窄宽度（量错的"假宽度"会把坞撑到 640 上下）
            w.entrance_buttons["1D"].click()
            QApplication.processEvents()
            self.assertTrue(w.param_dock.isVisible())
            self.assertLessEqual(w.param_dock.width(),
                                 w.param_dock.minimumSizeHint().width() + 20)
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_saved_entry_carries_provenance(self):
        """[存为配置] 保存"当前配置"，血缘写 method / derived_from / created。"""
        w = create_window()
        saved = {}
        try:
            w.show()
            with mock.patch.object(gui_calib_panel, "load_diffraction_image",
                                   return_value=np.ones((256, 256)) * 10), \
                 mock.patch.object(gui_calib.config, "save_user_config",
                                   side_effect=lambda k, e: saved.update(
                                       {k: e}) or True), \
                 mock.patch.object(gui_config_ops, "_reload_config_combo"):
                add_checked(w, ["data/fake_a.tif"])
                w.calib_btn.click()
                w.calib_pixel_chk.setChecked(True)   # 保存前置：确认像素尺寸
                w.calib_name_edit.setText("lmfp9_lab6")
                w.calib_save_btn.click()
            entry = saved["lmfp9_lab6"]
            self.assertEqual(entry["method"], "raw")     # 还没跑过校准
            self.assertEqual(entry["derived_from"], "lmfp1_lab6")
            self.assertRegex(entry["created"], r"^\d{4}-\d\d-\d\dT\d\d:")
            self.assertEqual(entry["calibrant"], "LaB6")
            self.assertEqual(entry["calibrant_n_rings"], 16)
            cfg = gui_app.CONFIGS["lmfp1_lab6"]["geometry"]
            self.assertAlmostEqual(entry["geometry"]["dist_m"], cfg["dist_m"])
            self.assertAlmostEqual(entry["geometry"]["pixel_size_m"],
                                   cfg["pixel_size_m"])
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()
