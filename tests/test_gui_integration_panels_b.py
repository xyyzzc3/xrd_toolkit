"""框选/缩放/摆图/抓手/外观/弹出与关闭（下）

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


class TestBoxZoomOnAggregateViews(unittest.TestCase):
    """框选放大在**对比图 / 热图**上也要留得住（用户 2026-09-28："热图和
    对比图的画框放大还是有bug"）。

    热图缺的是两块：面板建的时候 `sync=""`（范围**从不写回快照**）+ 画完没
    重连坐标轴回调（`ax.clear()` 会把回调注册表清空）→ 缩放永远进不了快照，
    任何一次重画都按数据自动缩放把它顶掉。对比图那条是把"重画按快照摆回
    视图"的口径补齐后验证没退化。
    """

    FILES = ["data/fake_a.tif", "data/fake_b.tif"]

    def _open_compare(self, w):
        _prime_1d(w, self.FILES)   # 栅栏前置：先各出过 1D
        w.compare_btn.click()
        self.assertTrue(_wait_until(
            lambda: "对比完成" in w.log_text.toPlainText()))
        dock = next(d for k, d in w.plot_docks.items() if k.startswith("对比"))
        return dock, gui_panel_state._content(dock).axes_1d

    def _open_heatmap(self, w):
        _prime_1d(w, self.FILES)   # 栅栏前置：先各出过 1D
        w.heat_btn.click()
        key = next((k for k in w.plot_docks if k.startswith("热图")), None)
        self.assertTrue(_wait_until(
            lambda: key is not None and getattr(w.plot_docks[key],
                                                "heat_data", None)
            is not None), "热图该算完")
        dock = w.plot_docks[key]
        return dock, gui_panel_state._content(dock).axes_heat

    def _box_zoom(self, dock, ax):
        """放大镜点亮 + 拖一个 80×60 像素的框（真事件的形状）。"""
        content = gui_panel_state._content(dock)
        content.toolbar._actions["zoom"].trigger()
        x0, y0 = ax.transData.transform((np.mean(ax.get_xlim()),
                                         np.mean(ax.get_ylim())))
        for name, dx, dy, kw in (
                ("button_press_event", 0, 0, {"button": 1}),
                ("motion_notify_event", 80, 60,
                 {"button": 1, "buttons": frozenset({1})}),
                ("button_release_event", 80, 60, {"button": 1})):
            content.canvas.callbacks.process(name, MouseEvent(
                name, content.canvas, x0 + dx, y0 + dy, **kw))
        QApplication.processEvents()

    def _survives_apply(self, w, dock, ax):
        before = (ax.get_xlim(), ax.get_ylim())
        self._box_zoom(dock, ax)
        zoomed = (ax.get_xlim(), ax.get_ylim())
        self.assertNotEqual(before, zoomed, "前提：框选真的缩了")
        gui_panel_state._set_focus(w, dock.panel_key, dock.windowTitle())
        QApplication.processEvents()
        gui_views._refresh_image_display(w)   # 即改即画同款：用已有数据重画
        QApplication.processEvents()
        for got, want in zip(ax.get_xlim(), zoomed[0]):
            self.assertAlmostEqual(got, want, delta=0.1,
                                   msg="重画之后 x 范围该保住")
        for got, want in zip(ax.get_ylim(), zoomed[1]):
            self.assertAlmostEqual(got, want, delta=0.1,
                                   msg="重画之后 y 范围该保住")

    def test_box_zoom_on_the_compare_survives_a_redraw(self):
        w = create_window()
        try:
            dock, ax = self._open_compare(w)
            self._survives_apply(w, dock, ax)
        finally:
            w.close()

    def test_box_zoom_on_the_heatmap_survives_a_redraw(self):
        w = create_window()
        try:
            dock, ax = self._open_heatmap(w)
            self._survives_apply(w, dock, ax)
            # 换色图 = 又一次重画，缩放同样要留住
            zoomed = (ax.get_xlim(), ax.get_ylim())
            combo = w.params.get("热图色图")
            combo.setCurrentIndex((combo.currentIndex() + 1) % combo.count())
            gui_views._refresh_image_display(w)
            QApplication.processEvents()
            for got, want in zip(ax.get_xlim(), zoomed[0]):
                self.assertAlmostEqual(got, want, delta=0.1,
                                       msg="换色图之后 x 范围该保住")
        finally:
            w.close()


class TestBoxZoomOnPixelViews(unittest.TestCase):
    """像素轴视图（2D / 剖面 / 瀑布）的框选放大也要留得住。

    用户 2026-09-28："别的图的放大检查一下"。这三张图的轴是**像素/行号**，
    没有 2θ / 强度那样的语义参数，范围原本**从不写回快照** → 任何一次重画
    （[应用]、换色图/对比度）都被自动缩放顶掉。现在它们走一对通用键
    （`视图 x 范围` / `视图 y 范围`，见 plot_views._apply_plain_view）。
    """

    def _box_zoom(self, dock, ax):
        content = gui_panel_state._content(dock)
        content.toolbar._actions["zoom"].trigger()
        x0, y0 = ax.transData.transform((np.mean(ax.get_xlim()),
                                         np.mean(ax.get_ylim())))
        for name, dx, dy, kw in (
                ("button_press_event", 0, 0, {"button": 1}),
                ("motion_notify_event", 60, 50,
                 {"button": 1, "buttons": frozenset({1})}),
                ("button_release_event", 60, 50, {"button": 1})):
            content.canvas.callbacks.process(name, MouseEvent(
                name, content.canvas, x0 + dx, y0 + dy, **kw))
        QApplication.processEvents()

    def _survives_apply(self, w, dock, ax):
        before = (ax.get_xlim(), ax.get_ylim())
        self._box_zoom(dock, ax)
        zoomed = (ax.get_xlim(), ax.get_ylim())
        self.assertNotEqual(before, zoomed, "前提：框选真的缩了")
        gui_panel_state._set_focus(w, dock.panel_key, dock.windowTitle())
        QApplication.processEvents()
        gui_views._refresh_image_display(w)   # 即改即画同款：用已有数据重画
        QApplication.processEvents()
        for got, want in zip(ax.get_xlim(), zoomed[0]):
            self.assertAlmostEqual(got, want, delta=0.5,
                                   msg="重画之后 x 范围该保住")
        for got, want in zip(ax.get_ylim(), zoomed[1]):
            self.assertAlmostEqual(got, want, delta=0.5,
                                   msg="重画之后 y 范围该保住")

    def test_box_zoom_on_the_2d_view_survives_a_redraw(self):
        w = create_window()
        try:
            fake_image = np.arange(400, dtype=float).reshape(20, 20)
            with mock.patch.object(gui_plot_workers, "load_diffraction_image",
                                   return_value=fake_image):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "2D")
                dock = _dock(w, "2D", "data/fake_b.tif")
                self.assertTrue(_wait_until(
                    lambda: len(gui_panel_state._content(
                        dock).axes_2d.images) > 0))
            self._survives_apply(
                w, dock, gui_panel_state._content(dock).axes_2d)
        finally:
            w.close()

    def test_box_zoom_on_the_profile_survives_a_redraw(self):
        w = create_window()
        try:
            def fake_profile(image, center, angle_deg=0.0):
                t = np.linspace(-100.0, 100.0, 41)
                return t, 10.0 + 5.0 * np.sin(t / 20.0)

            with mock.patch.object(gui_plot_workers, "load_diffraction_image",
                                   return_value=np.zeros((10, 10))), \
                 mock.patch.object(gui_plot_workers, "line_profile",
                                   side_effect=fake_profile):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "剖面")
                dock = _dock(w, "剖面", "data/fake_b.tif")
                self.assertTrue(_wait_until(
                    lambda: len(gui_panel_state._content(
                        dock).axes_profile.lines) > 0))
            self._survives_apply(
                w, dock, gui_panel_state._content(dock).axes_profile)
        finally:
            w.close()

    def test_box_zoom_on_the_waterfall_survives_a_redraw(self):
        w = create_window()
        try:
            with mock.patch.object(gui_plot_runners, "_compute_waterfall",
                                   side_effect=_fake_waterfall_compute):
                add_checked(w, ["data/fake_w.tif"])
                _open_view(w, "瀑布")
                dock = _dock(w, "瀑布", "data/fake_w.tif")
                self.assertTrue(_wait_until(
                    lambda: getattr(dock, "last_waterfall", None) is not None))
            self._survives_apply(
                w, dock, gui_panel_state._content(dock).axes_waterfall)
        finally:
            w.close()


class TestBoxZoom(unittest.TestCase):
    """放大镜点亮时左键拖 = 框选放大（2026-09-24 用户要求加回来：
    "再加回去放大镜框选放大，注意上次那个bug，不要再出现了"）。

    上次的 bug（用户 2026-09-18 原话）："画完框在缩小会把画框内的
    数据变小"——旧版用的是 matplotlib 的 zoom mode，它与我们自己的
    平移手势抢同一串鼠标事件、又各自记账。现在框选是自己实现的：
    自己画选框 + 松开时只改 set_xlim/set_ylim，**一个数据点都不碰**。
    test_box_zoom_then_wheel_out_keeps_data 就是那个 bug 的回归护栏。
    """

    PATH = "data/fake_b.tif"

    def _open_1d(self, w):
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, [self.PATH])
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, "1D", self.PATH).lines) > 0))
        return _dock(w, "1D", self.PATH)

    def _drag_box(self, dock, data0, data1):
        """按真事件的形状拖一个框：press 带 button，move 带 buttons
        （mpl 的 _mouse_handler 会把拖动中的 move 事件补上 button）。"""
        content = gui_panel_state._content(dock)
        ax, canvas = content.axes_1d, content.canvas
        p0 = ax.transData.transform(data0)
        p1 = ax.transData.transform(data1)
        canvas.callbacks.process("button_press_event", MouseEvent(
            "button_press_event", canvas, p0[0], p0[1], button=1))
        canvas.callbacks.process("motion_notify_event", MouseEvent(
            "motion_notify_event", canvas, p1[0], p1[1],
            buttons=frozenset({1})))
        canvas.callbacks.process("button_release_event", MouseEvent(
            "button_release_event", canvas, p1[0], p1[1], button=1))

    def test_box_zoom_narrows_both_axes_to_the_box(self):
        """框住哪块就放大到哪块：x/y 范围收拢到框内。

        框必须落在**当前可见范围之内**：起点在轴外时按下事件
        inaxes=None，被手势正确忽略（那是保护，不是缺陷）。
        """
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            ax = content.axes_1d
            content.toolbar._actions["zoom"].trigger()   # 点亮放大镜
            self._drag_box(dock, (1.2, 1.3), (3.0, 2.5))
            xlo, xhi = ax.get_xlim()
            ylo, yhi = ax.get_ylim()
            self.assertAlmostEqual(xlo, 1.2, delta=0.05, msg=f"x 下限 {xlo}")
            self.assertAlmostEqual(xhi, 3.0, delta=0.05, msg=f"x 上限 {xhi}")
            self.assertAlmostEqual(ylo, 1.3, delta=0.05, msg=f"y 下限 {ylo}")
            self.assertAlmostEqual(yhi, 2.5, delta=0.05, msg=f"y 上限 {yhi}")
        finally:
            w.close()

    def test_box_zoom_then_wheel_out_keeps_data(self):
        """**旧 bug 的回归护栏**：画框放大 → 再滚轮缩小，曲线数据一个
        点都不许变（旧版是"框内数据变小"，那是数据被动了）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            ax, canvas = content.axes_1d, content.canvas
            # 只比数据曲线：悬停圆点（单点假线）不算——鼠标一动它就
            # 会建出来，比"线数"会被它带偏
            def data_lines():
                return [ln for ln in ax.lines if len(ln.get_xdata()) > 1]
            before = [(ln.get_xdata().copy(), ln.get_ydata().copy())
                      for ln in data_lines()]
            content.toolbar._actions["zoom"].trigger()   # 点亮
            self._drag_box(dock, (1.2, 1.3), (3.0, 2.5))
            # 再缩小（放大镜仍点亮：滚轮向下 = 缩小）
            canvas.callbacks.process("scroll_event", MouseEvent(
                "scroll_event", canvas, 250, 150, step=-1, button="down"))
            after = [(ln.get_xdata(), ln.get_ydata()) for ln in data_lines()]
            self.assertEqual(len(before), len(after))
            for i, ((x0, y0), (x1, y1)) in enumerate(zip(before, after)):
                self.assertTrue(np.array_equal(x0, x1),
                                f"第 {i} 条曲线的 x 数据被改了")
                self.assertTrue(np.array_equal(y0, y1),
                                f"第 {i} 条曲线的 y 数据被改了")
            # 而且视图确实张开了（证明缩小这一步真的执行了）
            self.assertGreater(ax.get_xlim()[1] - ax.get_xlim()[0], 0.8)
        finally:
            w.close()

    def test_box_zoom_removes_the_rect_after_release(self):
        """选框是临时的：松开后轴上不留矩形（否则重画会叠罗汉）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            ax = content.axes_1d
            content.toolbar._actions["zoom"].trigger()
            self._drag_box(dock, (1.2, 1.3), (3.0, 2.5))
            self.assertEqual(len(ax.patches), 0, "松开后不应留下选框")
            self.assertIsNone(getattr(dock, "_box_patch", None))
        finally:
            w.close()

    def test_tiny_drag_is_a_click_not_a_zoom(self):
        """框太小（< 6 px）= 手抖，当点击：视图不动。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            ax = content.axes_1d
            content.toolbar._actions["zoom"].trigger()
            x0 = ax.get_xlim()
            self._drag_box(dock, (1.0, 1.5), (1.001, 1.501))
            self.assertEqual(tuple(ax.get_xlim()), tuple(x0),
                             "小抖动不该改变视图")
        finally:
            w.close()

    def test_magnifier_off_drag_still_pans(self):
        """放大镜熄灭时左键拖仍是平移（框选只在点亮时生效）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            ax = content.axes_1d
            self.assertFalse(gui_plot_panels._magnifier_on(dock))
            x0 = ax.get_xlim()
            self._drag_box(dock, (1.0, 1.5), (2.0, 2.0))
            x1 = ax.get_xlim()
            self.assertNotEqual(tuple(x0), tuple(x1), "熄灭时拖动应平移")
            # 平移不该留下选框
            self.assertEqual(len(ax.patches), 0)
        finally:
            w.close()


class TestZoomToolbar(unittest.TestCase):
    """D：每个 1D 面板带自己的精简工具栏 [Home][Zoom][Customize][Save]；
    放大镜 = 开关（点亮滚轮缩放/拖框放大，熄灭滚轮滚动/拖平移），
    抓手/前进后退/子图按钮退休；占位面板没有。

    2026-09-24 起工具栏是自绘的 `_SlimToolbar`（自己拿四个 QAction，
    不再继承 mpl 的 NavigationToolbar2QT——它构造时会在 offscreen 下
    偶发无限递归，见 scripts/stress_panels.py）。"""

    def test_toolbar_present_on_1d(self):
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "1D")
                _wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
            widget = gui_panel_state._content(_dock(w, "1D", "data/fake_b.tif"))
            self.assertIsInstance(widget.toolbar,
                                  gui_plot_panels._SlimToolbar)
            self.assertEqual(
                list(widget.toolbar._actions),
                ["home", "zoom", "edit_parameters", "save_figure"],
                "工具栏应精简为 Home/Zoom/Customize/Save 四个 action")
            # 砍掉的按钮不该有 action（抓手/前进/后退/子图）
            for gone in ("pan", "back", "forward", "configure_subplots"):
                self.assertNotIn(gone, widget.toolbar._actions,
                                 f"{gone} 按钮应已砍掉")
        finally:
            w.close()

    def test_unregistered_view_still_placeholder(self):
        """未注册视图（分发骨架的防御路径）：占位标签面板，无工具栏。"""
        w = create_window()
        try:
            gui_plot_panels._open_plot_panel(w, "不存在", "不存在|data/fake_b.tif",
                                       "不存在_fake_b.tif")
            widget = gui_panel_state._content(_dock(w, "不存在", "data/fake_b.tif"))
            self.assertIsInstance(widget, QLabel, "占位面板仍是标签")
            self.assertFalse(hasattr(widget, "toolbar"),
                             "占位面板不应有缩放工具栏")
        finally:
            w.close()


class TestHoverDot(unittest.TestCase):
    """E：鼠标悬停 = 曲线上出白边点 + 状态栏实时坐标；离开清空。"""

    def _open_1d(self, w):
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, ["data/fake_b.tif"])
            _open_view(w, "1D")
            _wait_until(
                lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
        return _dock(w, "1D", "data/fake_b.tif")

    def test_hover_shows_marker_and_coords(self):
        w = create_window()
        try:
            dock = self._open_1d(w)
            key = "1D|data/fake_b.tif"
            ax = _axes(w, "1D", "data/fake_b.tif")
            gui_plot_panels._hover_motion(w, key, _hover_event(ax, 0.6))
            marker = dock.hover_marker
            self.assertIsNotNone(marker, "悬停后应出现取点标记")
            self.assertTrue(marker.get_visible())
            # 吸附最近真实数据点：假积分 x=[0.5, 1.0, 8.5] → 0.6 归 0.5
            self.assertEqual(list(marker.get_xdata()), [0.5])
            self.assertEqual(list(marker.get_ydata()), [1.0])
            # 底部横带读数 = 2θ + 强度（2026-10-07 起单曲线不再带面板名
            # 前缀——见下一条；"1D_fake_b.tif：…" 那种长文本放不进框）
            text = w.coord_label.text()
            self.assertIn("2θ 0.5°", text)
            self.assertIn("强度 1", text)
        finally:
            w.close()

    def test_hover_readout_has_no_redundant_panel_name(self):
        """单曲线面板的读数不带"面板名："前缀（2026-10-07 用户："坐标现在
        显示不完全，框变大到能完全展示"）。

        1D 的曲线没有自己的标签，原来回退成面板标题（"1D_fake_b.tif：2θ
        …"），前缀把读数拉长到框里放不下；那条曲线就是眼前这张图，前缀是
        废话。带标签的（对比的文件名 / 瀑布的扇区号）照旧带着认曲线。
        """
        w = create_window()
        try:
            self._open_1d(w)
            key = "1D|data/fake_b.tif"
            ax = _axes(w, "1D", "data/fake_b.tif")
            gui_plot_panels._hover_motion(w, key, _hover_event(ax, 0.6))
            text = w.coord_label.text()
            self.assertIn("2θ", text)
            self.assertIn("强度", text)
            self.assertNotIn("1D_", text, "1D 读数不该带面板名前缀")
            # 直接钉格式化函数：空 name → 无前缀；有 name → 照旧带着
            dock = w.plot_docks[key]
            bare = gui_plot_panels._hover_label(dock, "", 3.3, 100.0)
            self.assertTrue(bare.startswith("2θ"), bare)
            named = gui_plot_panels._hover_label(dock, "a.tif", 3.3, 100.0)
            self.assertTrue(named.startswith("a.tif：2θ"), named)
        finally:
            w.close()

    def test_hover_clears_on_leave(self):
        w = create_window()
        try:
            dock = self._open_1d(w)
            key = "1D|data/fake_b.tif"
            ax = _axes(w, "1D", "data/fake_b.tif")
            gui_plot_panels._hover_motion(w, key, _hover_event(ax, 0.6))
            gui_plot_panels._hover_leave(w, key)
            self.assertFalse(dock.hover_marker.get_visible(),
                             "离开后取点标记应藏起来")
            self.assertEqual(w.coord_label.text(), "",
                             "离开后坐标标签应清空（标签本身常驻）")
        finally:
            w.close()

    def test_marker_recreated_after_redraw(self):
        """重算/[应用] 会 ax.clear() 掉旧标记 → 下次悬停懒重建。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            key = "1D|data/fake_b.tif"
            ax = _axes(w, "1D", "data/fake_b.tif")
            gui_plot_panels._hover_motion(w, key, _hover_event(ax, 0.6))
            old = dock.hover_marker
            tth, it = np.array([0.5, 1.0, 8.5]), np.array([1.0, 2.0, 3.0])
            gui_plot_views._draw_1d(w, dock, tth, it)
            self.assertIsNot(old.axes, ax, "重画后旧标记应与旧轴断开")
            gui_plot_panels._hover_motion(w, key, _hover_event(ax, 1.0))
            self.assertIs(dock.hover_marker.axes, ax,
                          "再次悬停应在当前轴上重建标记")
            self.assertEqual(list(dock.hover_marker.get_xdata()), [1.0])
        finally:
            w.close()

    def test_compare_hover_shows_filename(self):
        w = create_window()
        try:
            _prime_1d(w, ["data/fake_a.tif", "data/fake_b.tif"],
                      stub=_fake_compute)      # 栅栏前置：先各出过 1D
            w.compare_btn.click()
            key = next(k for k in w.plot_docks
                       if k.startswith("对比"))
            dock = w.plot_docks[key]
            self.assertTrue(_wait_until(
                lambda: len(gui_panel_state._content(dock).axes_1d.lines) >= 2))
            ax = gui_panel_state._content(dock).axes_1d
            gui_plot_panels._hover_motion(w, key, _hover_event(ax, 0.5))
            text = w.coord_label.text()
            self.assertIn("fake_a.tif", text,
                          "对比图坐标前缀 = 曲线（文件）名")
            marker = dock.hover_marker
            self.assertEqual(marker.get_color(),
                             ax.lines[0].get_color(),
                             "取点颜色 = 所选曲线的颜色")
        finally:
            w.close()


class TestArrangeGrid(unittest.TestCase):
    """C：横排/竖排重排 = 按类型分层摆位置（图保持各自大小，默认
    5:3 不被缩放），同类型同行/列、顶/左对齐，放不下靠滚动条兜底。"""

    def _open_two(self, w):
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
            _open_view(w, "1D")
            both = _wait_until(
                lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0
                and len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
            self.assertTrue(both)

    def _check_grid_ratio(self, w, mode):
        w.arrange_buttons[mode].click()
        QApplication.processEvents()
        docks = [d for d in w.plot_docks.values() if d.isVisible()]
        self.assertEqual(len(docks), 2)
        for d in docks:
            ratio = d.height() / d.width()
            self.assertGreater(ratio, 0.45,
                               f"{mode}后面板被挤扁：{ratio:.2f}")
            self.assertLess(ratio, 0.85,
                            f"{mode}后面板被拉长：{ratio:.2f}")
        self.assertIn(f"已{mode} 2 个面板", w.log_text.toPlainText())
        return docks

    def test_row_arrange_keeps_ratio(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_two(w)
            docks = self._check_grid_ratio(w, "横排")
            # 一行格子：两个面板顶边对齐、互不重叠
            self.assertAlmostEqual(docks[0].geometry().top(),
                                   docks[1].geometry().top(), delta=2)
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_column_arrange_keeps_ratio(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_two(w)
            docks = self._check_grid_ratio(w, "竖排")
            # 一列格子：左边对齐、上下不重叠
            self.assertAlmostEqual(docks[0].geometry().left(),
                                   docks[1].geometry().left(), delta=2)
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()


class TestTilingGroups(unittest.TestCase):
    """平铺按类型分层：横排每类一行、竖排每类一列；类型顺序与层内
    顺序 = 开图先后；只摆位置绝不缩放（溢出靠 QMdiArea 滚动条）。"""

    def _check_only(self, w, keep):
        """文件列表只勾 keep（作图用对号文件，见 TestFileCheckSelection）。"""
        for i in range(w.file_list.count()):
            item = w.file_list.item(i)
            item.setCheckState(
                Qt.Checked if item.text() == keep else Qt.Unchecked)

    def _open_mixed(self, w):
        """按开图先后：1D(fake_a) → 2D(fake_c) → 1D(fake_b)。"""
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif",
                         "data/fake_c.tif"])
            for keep, view in (("fake_a.tif", "1D"),
                               ("fake_c.tif", "2D"),
                               ("fake_b.tif", "1D")):
                self._check_only(w, keep)
                _open_view(w, view)
                QApplication.processEvents()
        # 1D 计算异步，等两张 1D 都出线（2D 只开面板不算）
        self.assertTrue(_wait_until(
            lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0
            and len(_axes(w, "1D", "data/fake_b.tif").lines) > 0))

    def _sizes(self, w):
        return {k: (d.width(), d.height()) for k, d in w.plot_docks.items()}

    def test_row_groups_by_type_keeps_sizes(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_mixed(w)
            before = self._sizes(w)
            w.arrange_buttons["横排"].click()
            QApplication.processEvents()
            d_a = _dock(w, "1D", "data/fake_a.tif")
            d_b = _dock(w, "1D", "data/fake_b.tif")
            d_c = _dock(w, "2D", "data/fake_c.tif")
            # 同类型同一行（顶对齐）
            self.assertAlmostEqual(d_a.geometry().top(),
                                   d_b.geometry().top(), delta=2)
            # 不同类型分层：2D 单独一行
            self.assertGreater(d_c.geometry().top(), d_a.geometry().top())
            # 层内顺序 = 开图先后：fake_a 在 fake_b 左边
            self.assertLess(d_a.geometry().left(), d_b.geometry().left())
            # 只摆位置不缩放
            self.assertEqual(self._sizes(w), before)
            self.assertIn("已横排 3 个面板", w.log_text.toPlainText())
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_column_groups_by_type_keeps_sizes(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_mixed(w)
            before = self._sizes(w)
            w.arrange_buttons["竖排"].click()
            QApplication.processEvents()
            d_a = _dock(w, "1D", "data/fake_a.tif")
            d_b = _dock(w, "1D", "data/fake_b.tif")
            d_c = _dock(w, "2D", "data/fake_c.tif")
            # 同类型同一列（左对齐）
            self.assertAlmostEqual(d_a.geometry().left(),
                                   d_b.geometry().left(), delta=2)
            # 不同类型分层：2D 单独一列
            self.assertGreater(d_c.geometry().left(), d_a.geometry().left())
            # 层内顺序 = 开图先后：fake_a 在 fake_b 上边
            self.assertLess(d_a.geometry().top(), d_b.geometry().top())
            self.assertEqual(self._sizes(w), before)
            self.assertIn("已竖排 3 个面板", w.log_text.toPlainText())
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_overflow_scrolls_without_scaling(self):
        """放不下 → 横向滚动条兜底，图不缩放。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            # 三张同类型 1D 挤在同一行，窗口收窄后必然放不下
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif",
                             "data/fake_c.tif"])
                _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, "1D", "data/fake_c.tif").lines) > 0))
            before = self._sizes(w)
            w.resize(600, 500)
            QApplication.processEvents()
            w.arrange_buttons["横排"].click()
            QApplication.processEvents()
            self.assertGreater(w.mdi.horizontalScrollBar().maximum(), 0,
                               "放不下 → 横向滚动条兜底")
            self.assertEqual(self._sizes(w), before, "溢出也不缩放图")
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()


class TestFreeResize(unittest.TestCase):
    """B：手动缩放完全自由：拖成什么样就停在什么样（不弹回任何比例，
    旧规则里的普通拖/Shift 拖之分随停靠分栏一起退场）；每拖一次 =
    记住当前画布比例；主窗口缩放不牵动子窗口。"""

    def _open_two(self, w):
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
            _open_view(w, "1D")
            both = _wait_until(
                lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0
                and len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
            self.assertTrue(both)
        QApplication.processEvents()

    def test_resize_records_canvas_pref(self):
        """拖面板边框 → 停在拖成的尺寸，当前画布尺寸记成新偏好比例。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_two(w)
            d1 = _dock(w, "1D", "data/fake_a.tif")
            _resize_panel(w, d1, 400, 450)   # 模拟用户拖成 400×450
            canvas = gui_panel_state._content(d1).canvas
            self.assertEqual((d1.width(), d1.height()), (400, 450),
                             "自由缩放：拖成什么样就停在什么样")
            self.assertTrue(d1._dragged, "拖过 = 记比例")
            self.assertEqual(d1._canvas_pref,
                             (canvas.width(), canvas.height()),
                             "记住的画布比例 = 拖成时的画布尺寸")
            # 不再有任何比例弹回：尺寸原样保持
            QApplication.processEvents()
            self.assertEqual((d1.width(), d1.height()), (400, 450))
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_main_window_resize_leaves_panels_alone(self):
        """拉大拉小主窗口：子窗口原地不动、不误标"拖过"。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_two(w)
            d1 = _dock(w, "1D", "data/fake_a.tif")
            geo_before = d1.geometry()
            w.resize(900, 600)
            QApplication.processEvents()
            self.assertEqual(d1.geometry(), geo_before,
                             "主窗口缩放不牵动子窗口")
            self.assertFalse(d1._dragged, "主窗口缩放不算拖过图")
            self.assertEqual(d1._canvas_pref, (500, 300),
                             "比例记忆保持默认")
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()


class TestResizeGrips(unittest.TestCase):
    """自装抓手：内容四边 8px 抓取带 + 四角 24px 抓取区 + 右下角
    可见把手（▙）；悬停换方向光标（应用级覆盖光标，macOS 上部件级
    setCursor 会被带过期坐标的合成事件打回原形），按住拖 = 拉伸
    容器（子窗口/弹出窗口都可用），拖完照常记"拖过"比例。"""

    def _open_one(self, w, view="1D", path_str="data/fake_b.tif"):
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, [path_str])
            _open_view(w, view)
        if view == "1D":
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, view, path_str).lines) > 0))
        QApplication.processEvents()

    def test_hover_swaps_cursor(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_one(w)
            content = gui_panel_state._content(_dock(w, "1D", "data/fake_b.tif"))
            # 方向光标 = 应用级覆盖光标（macOS 上部件级 setCursor 会被
            # 带过期坐标的合成事件打回原形，见 _PanelGripFilter docstring）
            # 右边缘中部（避开右下角把手）→ 水平双箭头
            self.assertTrue(_hover_until(
                content, QPoint(content.width() - 3, 200),
                lambda: QApplication.overrideCursor() is not None
                and QApplication.overrideCursor().shape() == Qt.SizeHorCursor))
            # 画布中央 → 撤销覆盖光标（回到普通箭头）
            self.assertTrue(_hover_until(
                content, QPoint(300, 200),
                lambda: QApplication.overrideCursor() is None))
            # 右下角被把手挡着（把手盖在画布上）→ 角区斜向双箭头
            grip = content._resize_grip
            self.assertTrue(_hover_until(
                content, QPoint(content.width() - 5, content.height() - 5),
                lambda: QApplication.overrideCursor() is not None
                and QApplication.overrideCursor().shape()
                == Qt.SizeFDiagCursor))
            # 顶边抓取带落在工具栏条上：真实落点 = 工具栏 → 垂直双箭头
            self.assertTrue(_hover_until(
                content, QPoint(250, 2),
                lambda: QApplication.overrideCursor() is not None
                and QApplication.overrideCursor().shape() == Qt.SizeVerCursor))
        finally:
            # 弹空覆盖光标栈：别把方向光标留给后面的测试
            while QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_drag_corner_grip_resizes_and_records(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_one(w)
            dock = _dock(w, "1D", "data/fake_b.tif")
            content = gui_panel_state._content(dock)
            grip = content._resize_grip
            w0, h0 = dock.width(), dock.height()
            # 真实用户行为：按住 ▙ 把手拖（不是直接投递给内容）
            QTest.mousePress(grip, Qt.LeftButton, Qt.NoModifier, QPoint(8, 8))
            QTest.mouseMove(grip, QPoint(58, 38))
            QTest.mouseRelease(grip, Qt.LeftButton, Qt.NoModifier,
                               QPoint(58, 38))
            QApplication.processEvents()
            self.assertEqual((dock.width(), dock.height()),
                             (w0 + 50, h0 + 30), "按住把手拖 = 拉伸右下角")
            canvas = content.canvas
            self.assertTrue(dock._dragged, "拖过 = 记比例")
            self.assertEqual(dock._canvas_pref,
                             (canvas.width(), canvas.height()),
                             "记住的画布比例 = 拖成时的画布尺寸")
            # 把手钉回右下角
            self.assertAlmostEqual(grip.pos().x(), content.width() - 18,
                                   delta=2)
            self.assertAlmostEqual(grip.pos().y(), content.height() - 18,
                                   delta=2)
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_drag_top_edge_resizes(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_one(w)
            dock = _dock(w, "1D", "data/fake_b.tif")
            content = gui_panel_state._content(dock)
            h0 = dock.height()
            # 顶边抓取带真实落点 = 自绘标题栏那一行（内容上边 5px；
            # 2026-09-24 面板壳瘦身：工具栏藏了，最上面是自绘栏）
            QTest.mousePress(content.slim_bar, Qt.LeftButton, Qt.NoModifier,
                             QPoint(content.width() // 2, 2))
            QTest.mouseMove(content.slim_bar,
                            QPoint(content.width() // 2, 2 - 40))
            QTest.mouseRelease(content.slim_bar, Qt.LeftButton, Qt.NoModifier,
                               QPoint(content.width() // 2, 2 - 40))
            QApplication.processEvents()
            self.assertEqual(dock.height(), h0 + 40, "顶边往上拖 = 容器变高")
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_placeholder_panel_also_has_grip(self):
        """2D 面板（画布内容）同样有把手和抓手——把手装在内容上，
        与视图类型无关。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_one(w, view="2D")
            dock = _dock(w, "2D", "data/fake_b.tif")
            content = gui_panel_state._content(dock)
            grip = content._resize_grip
            w0, h0 = dock.width(), dock.height()
            QTest.mousePress(grip, Qt.LeftButton, Qt.NoModifier, QPoint(8, 8))
            QTest.mouseMove(grip, QPoint(38, 28))
            QTest.mouseRelease(grip, Qt.LeftButton, Qt.NoModifier,
                               QPoint(38, 28))
            QApplication.processEvents()
            self.assertEqual((dock.width(), dock.height()), (w0 + 30, h0 + 20))
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()


class TestCustomizeDialog(unittest.TestCase):
    """自绘 Customize 轴属性对话框（替换 mpl 子图配置器）：标题/
    轴标签/纵轴刻度/图边距，表单标签左对齐 + [恢复默认][取消]
    [应用]；应用后用户改动受保护记账（重画不覆盖），边距接管 =
    摘掉 tight layout 引擎（否则 draw 时布局引擎把用户边距算回去）。"""

    def _open_one(self, w, path_str="data/fake_b.tif"):
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, [path_str])
            _open_view(w, "1D")
        self.assertTrue(_wait_until(
            lambda: len(_axes(w, "1D", path_str).lines) > 0))
        QApplication.processEvents()

    def _build(self, w, path_str="data/fake_b.tif"):
        dock = _dock(w, "1D", path_str)
        content = gui_panel_state._content(dock)
        return dock, gui_customize._build_customize_dialog(
            w, dock, content.axes_1d, content.figure)

    def test_dialog_scrolls_when_content_overflows(self):
        """内容多时进滚动区、按钮行钉在滚动区外（2026-10-08）。

        起因（用户报"有时包含图例、看不全"）：对比面板的「逐曲线颜色」
        每曲线一行、勾 81 条就是 81 行，对话框撑出屏幕、底部 [应用] 都
        够不着。2026-10-13 逐曲线色块退休（改「渐变色系」单选），滚动
        区留着兜底小屏幕——这里用"把对话框缩矮"复现内容超出。
        """
        w = create_window()
        try:
            self._open_one(w)
            dock, dlg = self._build(w)
            scroll = dlg.findChild(QScrollArea)
            self.assertIsNotNone(scroll, "对话框该有滚动区")
            apply_btn = next(b for b in dlg.findChildren(QPushButton)
                             if b.text() == "应用")
            self.assertFalse(scroll.isAncestorOf(apply_btn),
                             "[应用] 钉在滚动区外，永远够得着")
            text_box = next(b for b in dlg.findChildren(QGroupBox)
                            if b.title() == "标题与轴标签")
            self.assertTrue(scroll.isAncestorOf(text_box),
                            "内容区（标题与轴标签）在滚动区里")
            dlg.resize(460, 150)
            dlg.show()
            QApplication.processEvents()
            self.assertGreater(scroll.verticalScrollBar().maximum(), 0,
                               "内容超出时该出现滚动轮")
        finally:
            w.close()

    def test_dialog_prefills_current_axis_state(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_one(w)
            dock, dlg = self._build(w)
            content = gui_panel_state._content(dock)
            f = dlg._fields
            self.assertEqual(f["title"].text(), content.axes_1d.get_title())
            self.assertEqual(f["xlabel"].text(), content.axes_1d.get_xlabel())
            self.assertEqual(f["ylabel"].text(), content.axes_1d.get_ylabel())
            self.assertEqual(f["scale"].currentData(),
                             content.axes_1d.get_yscale())
            sp = content.figure.subplotpars
            self.assertAlmostEqual(f["left"].value(), sp.left, places=3)
            self.assertAlmostEqual(f["right"].value(), sp.right, places=3)
            # 表单左对齐（用户点名要的）：macOS 风格默认把表单内容
            # 整块水平居中，formAlignment 显式设左
            text_box = next(b for b in dlg.findChildren(QGroupBox)
                            if b.title() == "标题与轴标签")
            self.assertEqual(text_box.layout().labelAlignment(),
                             Qt.AlignLeft | Qt.AlignVCenter)
            self.assertEqual(text_box.layout().formAlignment(),
                             Qt.AlignLeft | Qt.AlignTop)
            # 标题输入框加长（用户点名要的）
            self.assertGreaterEqual(f["title"].minimumWidth(), 240)
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_apply_sets_title_labels_scale_and_margins(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_one(w)
            dock, dlg = self._build(w)
            f = dlg._fields
            f["title"].setText("我的衍射图")
            f["xlabel"].setText("角度")
            f["ylabel"].setText("计数")
            f["scale"].setCurrentIndex(f["scale"].findData("log"))
            f["left"].setValue(0.2)
            f["bottom"].setValue(0.15)
            f["right"].setValue(0.85)
            f["top"].setValue(0.8)
            content = gui_panel_state._content(dock)
            gui_customize._apply_customize(w, dock, content.axes_1d,
                                     content.figure, dlg)
            ax = content.axes_1d
            self.assertEqual(ax.get_title(), "我的衍射图")
            self.assertEqual(ax.get_xlabel(), "角度")
            self.assertEqual(ax.get_ylabel(), "计数")
            self.assertEqual(ax.get_yscale(), "log")
            sp = content.figure.subplotpars
            for name, value in (("left", 0.2), ("bottom", 0.15),
                                ("right", 0.85), ("top", 0.8)):
                self.assertAlmostEqual(getattr(sp, name), value, places=3)
            self.assertIsNone(content.figure.get_layout_engine(),
                              "边距由用户接管：tight layout 引擎退场")
        finally:
            w.hide()
            w.close()

    def test_2d_colormap_in_customize(self):
        """2D 色图在 Customize 里改：只出现在 2D 面板，改完立刻重画。

        用户 2026-09-27："二维图颜色，customize里"——原来 `_draw_2d` 写死
        magma，改不了；现在按面板快照读（每张图各记各的）。
        """
        w = create_window()
        try:
            with mock.patch.object(gui_plot_workers, "load_diffraction_image",
                                   return_value=np.ones((32, 32)) * 100):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "2D")
                key = next(k for k in w.plot_docks if k.startswith("2D|"))
                dock = w.plot_docks[key]
                ax = gui_panel_state._content(dock).axes_2d
                self.assertTrue(_wait_until(lambda: len(ax.images) > 0))
                self.assertEqual(ax.images[0].get_cmap().name, "magma")
                dlg = gui_customize._build_customize_dialog(
                    w, dock, ax, gui_panel_state._content(dock).figure)
                self.assertIn("cmap", dlg._fields, "2D 面板该有色图下拉")
                dlg._fields["cmap"].setCurrentIndex(
                    dlg._fields["cmap"].findData("viridis"))
                gui_customize._apply_customize(
                    w, dock, ax, gui_panel_state._content(dock).figure, dlg)
                QApplication.processEvents()
                self.assertEqual(ax.images[0].get_cmap().name, "viridis",
                                 "改完该立刻重画")
                self.assertEqual(dock.params_snapshot["2D 色图"], "viridis",
                                 "色图该记进这张面板的快照")
            # 1D 面板没有这个下拉（它的"颜色"是曲线色，另一套）
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_a.tif"])
                _open_view(w, "1D")
                d1 = _dock(w, "1D", "data/fake_a.tif")
                self.assertTrue(_wait_until(
                    lambda: len(gui_panel_state._content(d1).axes_1d.lines) > 0))
                dlg1 = gui_customize._build_customize_dialog(
                    w, d1, gui_panel_state._content(d1).axes_1d,
                    gui_panel_state._content(d1).figure)
                self.assertNotIn("cmap", dlg1._fields)
        finally:
            w.close()

    def test_custom_edits_survive_redraw(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_one(w)
            dock, dlg = self._build(w)
            f = dlg._fields
            f["title"].setText("我的衍射图")
            f["xlabel"].setText("角度")
            f["ylabel"].setText("计数")
            f["scale"].setCurrentIndex(f["scale"].findData("log"))
            f["left"].setValue(0.2)
            content = gui_panel_state._content(dock)
            gui_customize._apply_customize(w, dock, content.axes_1d,
                                     content.figure, dlg)
            # 程序重画（等价于图像参数 [应用] 走的 _draw_1d 路径）
            gui_plot_views._draw_1d(w, dock, dock.last_tth, dock.last_intensity)
            ax = content.axes_1d
            self.assertEqual(ax.get_title(), "我的衍射图", "标题：用户为准")
            self.assertEqual(ax.get_xlabel(), "角度", "X 标签：用户为准")
            self.assertEqual(ax.get_ylabel(), "计数", "Y 标签：用户为准")
            self.assertEqual(ax.get_yscale(), "log", "刻度：用户为准")
            self.assertAlmostEqual(
                content.figure.subplotpars.left, 0.2, places=3,
                msg="边距不被重画覆盖")
        finally:
            w.hide()
            w.close()

    def test_dialog_flow_accept_applies_cancel_keeps(self):
        """[应用]/[取消] 两条完整路径（exec 打补丁，不真弹模态框）。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_one(w)
            key = "1D|data/fake_b.tif"
            content = gui_panel_state._content(_dock(w, "1D", "data/fake_b.tif"))
            before = (content.axes_1d.get_title(),
                      content.axes_1d.get_xlabel(),
                      content.axes_1d.get_ylabel(),
                      content.axes_1d.get_yscale())
            # 取消路：exec 拒绝 → 图保持原样
            with mock.patch.object(QDialog, "exec",
                                   lambda self: QDialog.Rejected):
                gui_customize._open_customize_dialog(w, key)
            self.assertEqual(
                (content.axes_1d.get_title(), content.axes_1d.get_xlabel(),
                 content.axes_1d.get_ylabel(), content.axes_1d.get_yscale()),
                before, "取消不动图")
            # 接受路：exec 里改字段再接受 → 改动生效
            def _accept(self):
                self._fields["title"].setText("流程标题")
                self._fields["scale"].setCurrentIndex(
                    self._fields["scale"].findData("log"))
                return QDialog.Accepted
            with mock.patch.object(QDialog, "exec", _accept):
                gui_customize._open_customize_dialog(w, key)
            self.assertEqual(content.axes_1d.get_title(), "流程标题",
                             "应用生效")
            self.assertEqual(content.axes_1d.get_yscale(), "log")
        finally:
            w.hide()
            w.close()

    def test_toolbar_customize_button_opens_our_dialog(self):
        """工具栏 [Customize] 已从 mpl 子图配置器改接自绘对话框。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_one(w)
            content = gui_panel_state._content(_dock(w, "1D", "data/fake_b.tif"))
            # patch 目标 = 调用点所在模块 panel_build（_SlimToolbar 在
            # 那里查全局名）；gui_panels 是 panels.py，不是这个模块
            with mock.patch.object(gui_plot_panel_build,
                                   "_open_customize_dialog") as m:
                content.toolbar._actions["edit_parameters"].trigger()
            m.assert_called_once_with(w, "1D|data/fake_b.tif")
        finally:
            w.hide()
            w.close()


class TestWindowClickFocus(unittest.TestCase):
    """点面板窗口任何位置 = 选中该面板（标题栏/边框/图/工具栏都
    算，所有图都一样），参数坞跟着切。实现 = 应用级事件过滤器
    _PanelClickTracker：QWidget 的父过滤器收不到子部件事件（探针
    实证），从落点沿 parentWidget 链向上找面板容器。"""

    def _open_two(self, w):
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0
                and len(_axes(w, "1D", "data/fake_b.tif").lines) > 0))

    def test_click_canvas_selects_panel(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_two(w)
            w.focus_panel = None
            # 点画布（子部件，mpl 会 accept 鼠标按下）→ 也选中
            canvas = gui_panel_state._content(
                _dock(w, "1D", "data/fake_a.tif")).canvas
            QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier,
                             QPoint(100, 100))
            self.assertEqual(w.focus_panel, "1D|data/fake_a.tif")
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_click_title_bar_selects_panel(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_two(w)
            w.focus_panel = None
            # 点子窗口标题栏（不必点图本体）
            sub = _dock(w, "1D", "data/fake_b.tif")
            QTest.mouseClick(sub, Qt.LeftButton, Qt.NoModifier, QPoint(10, 10))
            self.assertEqual(w.focus_panel, "1D|data/fake_b.tif")
            # 占位面板也一样：点内容即选中
            w.focus_panel = None
            QTest.mouseClick(gui_panel_state._content(_dock(w, "1D", "data/fake_a.tif")),
                             Qt.LeftButton)
            self.assertEqual(w.focus_panel, "1D|data/fake_a.tif")
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_click_floated_window_selects_panel(self):
        """弹出窗口里点内容 → 也选中（parent 链终点 = _FloatedWindow）。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_two(w)
            key = "1D|data/fake_a.tif"
            dock = _dock(w, "1D", "data/fake_a.tif")
            gui_panel_state._content(dock).popout_btn.click()
            QApplication.processEvents()
            floated = w.plot_docks[key]
            self.assertNotIsInstance(floated, gui_panels._PlotSubWindow)
            w.focus_panel = None
            QTest.mouseClick(gui_panel_state._content(floated).canvas,
                             Qt.LeftButton, Qt.NoModifier, QPoint(100, 100))
            self.assertEqual(w.focus_panel, key)
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()


class TestCanvasTrue53(unittest.TestCase):
    """新面板的"画布"真 5:3：画布 500×300（面板总高 = 画布 +
    工具栏 + 标题栏的壳，比旧版 500×300 面板多出来的正是壳）。"""

    def test_new_panel_canvas_is_500x300(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "1D")
                _wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
            QApplication.processEvents()
            canvas = gui_panel_state._content(_dock(w, "1D", "data/fake_b.tif")).canvas
            self.assertGreater(canvas.width(), 450)
            self.assertLess(canvas.width(), 550)
            self.assertGreater(canvas.height(), 270)
            self.assertLess(canvas.height(), 330)
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()


class TestCustomRatioMemory(unittest.TestCase):
    """拖过 = 永远按拖成的比例缩放：开新图、横竖排列、别的图拖动，
    都保持同比例缩放；没拖过的按默认 5:3。"""

    def _open_two(self, w):
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
            _open_view(w, "1D")
            both = _wait_until(
                lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0
                and len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
            self.assertTrue(both)
        QApplication.processEvents()

    def test_new_panel_does_not_move_existing(self):
        """开新图：旧图原地不动（这正是"不再连在一起"的核心）。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_two(w)
            d2 = _dock(w, "1D", "data/fake_b.tif")
            geo = d2.geometry()
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_c.tif"])
                _open_view(w, "1D")
                _wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_c.tif").lines) > 0)
            QApplication.processEvents()
            d2 = _dock(w, "1D", "data/fake_b.tif")
            self.assertEqual(d2.geometry(), geo, "开新图不应挪动已有面板")
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_arrange_keeps_dragged_ratio(self):
        """横排/竖排按钮：拖过的图按自己的比例、没拖过的按默认 5:3。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_two(w)
            d2 = _dock(w, "1D", "data/fake_b.tif")
            _resize_panel(w, d2, 700, 400)   # 模拟用户拖成 700×400
            pref = d2._canvas_pref
            for mode in ("横排", "竖排"):
                w.arrange_buttons[mode].click()
                QApplication.processEvents()
                c = gui_panel_state._content(d2).canvas
                now = c.height() / c.width()
                expected = pref[1] / pref[0]
                self.assertAlmostEqual(now, expected, delta=0.06,
                                       msg=f"{mode}后拖过的图比例变了")
                # 没拖过的图保持默认 5:3
                d1 = _dock(w, "1D", "data/fake_a.tif")
                c1 = gui_panel_state._content(d1).canvas
                self.assertAlmostEqual(
                    c1.height() / c1.width(), 3 / 5,
                    delta=0.06, msg=f"{mode}后默认图比例变了")
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_resizing_one_panel_leaves_neighbor(self):
        """拖一张图的边框：邻居完全不动，自己记住拖成比例。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_two(w)
            d1 = _dock(w, "1D", "data/fake_a.tif")
            d2 = _dock(w, "1D", "data/fake_b.tif")
            geo2 = d2.geometry()
            _resize_panel(w, d1, 400, 450)
            self.assertEqual(d2.geometry(), geo2, "拖一张图牵动了邻居")
            self.assertTrue(d1._dragged)
            c1 = gui_panel_state._content(d1).canvas
            self.assertEqual(d1._canvas_pref, (c1.width(), c1.height()),
                             "拖过的图应记住拖成时的画布比例")
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()


class TestViewLimitSync(unittest.TestCase):
    """缩放/平移写回参数：视图 2θ 范围 + 纵轴窗口（动纵轴才关自动）。"""

    def _open_1d(self, w):
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, ["data/fake_b.tif"])
            _open_view(w, "1D")
            drawn = _wait_until(
                lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
            self.assertTrue(drawn)
        return _dock(w, "1D", "data/fake_b.tif")

    def test_x_zoom_writes_view_range_back(self):
        """纯 x 缩放：视图 2θ 范围写回快照 + 参数坞框，纵轴自动不动。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            ax = _axes(w, "1D", "data/fake_b.tif")
            ax.set_xlim(2.0, 3.0)   # 模拟缩放后的范围改动
            QApplication.processEvents()
            snap = dock.params_snapshot
            self.assertAlmostEqual(snap["视图 2θ 下限 (°)"], 2.0, places=4)
            self.assertAlmostEqual(snap["视图 2θ 上限 (°)"], 3.0, places=4)
            # 焦点面板 → 「对比」小节那对视图窗口框同步显示
            # （1D 页上仍不摆框，1D 与对比共用这对老键；热图 2026-10-08
            # 晚起有自己的一对，分别显示各自焦点面板的窗口）
            self.assertAlmostEqual(
                w.params["视图 2θ 下限 (°)"].value(), 2.0, places=4)
            self.assertAlmostEqual(
                w.params["视图 2θ 上限 (°)"].value(), 3.0, places=4)
            # 纯 x 缩放没动纵轴 → 自动仍开着
            self.assertTrue(snap["纵轴自动"])
        finally:
            w.close()

    def test_y_zoom_turns_auto_ylim_off(self):
        """动纵轴：纵轴窗口写回 + 纵轴自动关掉（窗口由用户接管）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            ax = _axes(w, "1D", "data/fake_b.tif")
            ax.set_ylim(5.0, 10.0)
            QApplication.processEvents()
            snap = dock.params_snapshot
            self.assertFalse(snap["纵轴自动"], "动过纵轴后自动应关掉")
            self.assertAlmostEqual(snap["纵轴下限"], 5.0, places=4)
            self.assertAlmostEqual(snap["纵轴上限"], 10.0, places=4)
            # 焦点面板 → 参数坞复选框与输入框同步
            self.assertFalse(w.params["纵轴自动"].isChecked())
            self.assertAlmostEqual(w.params["纵轴下限"].value(), 5.0, places=4)
            self.assertTrue(w.params["纵轴下限"].isEnabled(),
                            "自动关掉后上下限框应解除置灰")
        finally:
            w.close()

    def test_view_range_controls_redraw(self):
        """视图 2θ 范围参与重画：改完 [应用] → 图按新窗口重画。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            key = "1D|data/fake_b.tif"
            ax = _axes(w, "1D", "data/fake_b.tif")
            # 视图范围没有输入框了（用户 2026-09-27："显示范围用户自己
            # 放大就行了"）→ 直接写快照，与缩放/平移写回的是同一处
            dock.params_snapshot["视图 2θ 下限 (°)"] = 1.0
            dock.params_snapshot["视图 2θ 上限 (°)"] = 5.0
            gui_views._redraw_panel(w, w.focus_panel)
            QApplication.processEvents()
            xlo, xhi = ax.get_xlim()
            self.assertAlmostEqual(xlo, 1.0, places=4)
            self.assertAlmostEqual(xhi, 5.0, places=4)
        finally:
            w.close()

    def test_reset_image_returns_to_follow_integration(self):
        """恢复默认：视图范围回到跟随积分范围（快照里的显式值清掉）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            key = "1D|data/fake_b.tif"
            ax = _axes(w, "1D", "data/fake_b.tif")
            ax.set_xlim(2.0, 3.0)   # 先缩放 → 快照有了显式视图范围
            QApplication.processEvents()
            w.findChild(QPushButton, "reset_image_btn").click()
            snap = dock.params_snapshot
            self.assertIsNone(snap.get("视图 2θ 下限 (°)"),
                              "恢复默认后应回到跟随积分范围")
            self.assertIsNone(snap.get("视图 2θ 上限 (°)"))
            # 视图窗口框回填成"跟随"的显示值（= 积分范围）；真实窗口看下一行
            self.assertAlmostEqual(
                w.params["视图 2θ 下限 (°)"].value(),
                w.params["2θ 下限 (°)"].value(), delta=0.05)
            # [恢复默认] 复位后收一笔重画（2026-10-08 起，复位尾部的
            # _refresh_image_display）→ 视图窗口回到"跟随积分范围"
            QApplication.processEvents()
            self.assertAlmostEqual(
                ax.get_xlim()[0], w.params["2θ 下限 (°)"].value(), delta=0.05,
                msg="恢复默认后重画该回到跟随积分范围")
        finally:
            w.close()

    def test_live_refresh_keeps_follow_view_unpinned(self):
        """即改即画写快照不把"跟随积分范围"钉成显式窗口（2026-10-07 定，
        2026-10-08 触发改走即改即画：原先这条查的是 [应用显示设置]）。

        「对比」小节那对视图框（老键；1D 与对比共用）显示的是"跟随"
        折算出来的数（该面板的积分范围）；写快照时若把它采集进去，
        "跟随"就被钉成显式窗口——之后改积分范围重算，旧窗口会把新
        曲线裁错。
        """
        w = create_window()
        try:
            dock = self._open_1d(w)
            self.assertIsNone(dock.params_snapshot.get("视图 2θ 下限 (°)"),
                              "新面板默认跟随积分范围")
            w.params["对数纵轴"].setChecked(True)   # 任一即改即画都会写快照
            QApplication.processEvents()
            self.assertIsNone(
                dock.params_snapshot.get("视图 2θ 下限 (°)"),
                "即改即画不该把跟随的视图钉成显式窗口")
            self.assertIsNone(dock.params_snapshot.get("视图 2θ 上限 (°)"))
        finally:
            w.close()


class TestGestures(unittest.TestCase):
    """手势：左键拖 = 平移；滚轮 = 只滚动绘图区；放大镜点亮时滚轮
    以光标为中心缩放（每格 10%）+ 左键拖框放大，熄灭时让位。"""

    def _open_1d(self, w):
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, ["data/fake_b.tif"])
            _open_view(w, "1D")
            drawn = _wait_until(
                lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
            self.assertTrue(drawn)
        return _dock(w, "1D", "data/fake_b.tif")

    def _magnifier(self, w, display, on):
        """点放大镜开关（触发 QAction = 用户点按钮），断言模式到位。"""
        content = gui_panel_state._content(_dock(w, "1D", display))
        content.toolbar._actions["zoom"].trigger()
        QApplication.processEvents()
        self.assertEqual(gui_plot_panels._magnifier_on(_dock(w, "1D", display)), on,
                         f"放大镜应已{'点亮' if on else '熄灭'}")

    def test_magnifier_toggle_switches_mode(self):
        """放大镜 = 纯开关：点亮点灭只翻转按钮。

        （旧断言是"mpl 的模式永远停在 NONE"——2026-09-24 面板工具栏换成
        自绘的 _SlimToolbar 之后，mpl 那套模式机件整个不在场，所以改成
        断言"没有 mode 这个机件"：将来谁再把 mpl 工具栏塞回来，这条会红。）
        """
        w = create_window()
        try:
            self._open_1d(w)
            content = gui_panel_state._content(_dock(w, "1D", "data/fake_b.tif"))
            action = content.toolbar._actions["zoom"]
            self.assertTrue(action.isCheckable(), "放大镜按钮应可亮灭")
            self.assertFalse(action.isChecked())
            self._magnifier(w, "data/fake_b.tif", True)
            self.assertTrue(action.isChecked(), "点亮后按钮应亮起")
            self.assertFalse(hasattr(content.toolbar, "mode"),
                             "不该再有 mpl 的缩放模式机件")
            self._magnifier(w, "data/fake_b.tif", False)
            self.assertFalse(action.isChecked(), "熄灭后按钮应熄灭")
        finally:
            w.close()

    def test_magnifier_log_matches_the_live_gesture(self):
        """日志跟着开关说人话：点亮 = 框选放大，熄灭 = 平移。

        原先两半都写"左键拖 = 平移"，点亮那句是错的（用户 2026-10-01
        报"日志错误"）。手势本身由 test_press_pans_when_magnifier_off_
        and_boxes_when_on 钉着，这条只管文案。
        """
        w = create_window()
        try:
            self._open_1d(w)
            self._magnifier(w, "data/fake_b.tif", True)
            self.assertIn("放大镜已开启：滚轮以光标为中心缩放，左键拖可框选放大",
                          w.log_text.toPlainText())
            self._magnifier(w, "data/fake_b.tif", False)
            self.assertIn("放大镜已关闭：滚轮滚动绘图区，左键拖可平移",
                          w.log_text.toPlainText())
        finally:
            w.close()

    def test_wheel_ignored_until_magnifier_on(self):
        """放大镜熄灭 = 滚轮不缩图（事件穿透给绘图区滚动）；点亮才缩放。"""
        w = create_window()
        try:
            self._open_1d(w)
            key = "1D|data/fake_b.tif"
            ax = _axes(w, "1D", "data/fake_b.tif")
            ax.set_xlim(1.0, 8.0)
            gui_plot_panels._wheel_zoom(w, key, _scroll_event(ax, "up", 4.0, 2.0))
            self.assertEqual(ax.get_xlim(), (1.0, 8.0),
                             "放大镜熄灭时滚轮不应改范围")
            self._magnifier(w, "data/fake_b.tif", True)
            gui_plot_panels._wheel_zoom(w, key, _scroll_event(ax, "up", 4.0, 2.0))
            xlo, xhi = ax.get_xlim()
            self.assertAlmostEqual(xlo, 4.0 - 3.0 / 1.1, places=3,
                                   msg="点亮后滚轮应缩放")
            self.assertAlmostEqual(xhi, 4.0 + 4.0 / 1.1, places=3)
            self._magnifier(w, "data/fake_b.tif", False)
            ax.set_xlim(1.0, 8.0)
            gui_plot_panels._wheel_zoom(w, key, _scroll_event(ax, "up", 4.0, 2.0))
            self.assertEqual(ax.get_xlim(), (1.0, 8.0),
                             "再熄灭后滚轮应再次失效")
        finally:
            w.close()

    def test_press_pans_when_magnifier_off_and_boxes_when_on(self):
        """左键按下：熄灭 = 记平移起点；点亮 = 起框选放大的框（2026-09-24
        用户要求把框选放大加回来，见 TestBoxZoom）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            key = "1D|data/fake_b.tif"
            ax = _axes(w, "1D", "data/fake_b.tif")
            # 开局放大镜是熄灭的（_magnifier 是"点一下再断言"的语义，
            # 这里先直接断言默认态）
            self.assertFalse(gui_plot_panels._magnifier_on(dock))
            gui_plot_panels._pan_press(w, key, _press_event(ax, x=100, y=120))
            self.assertIsNotNone(getattr(dock, "_pan_start", None),
                                 "放大镜熄灭时左键拖 = 平移")
            gui_plot_panels._pan_release(
                w, key, _press_event(ax, x=100, y=120))
            self.assertIsNone(getattr(dock, "_pan_start", None))
            self._magnifier(w, "data/fake_b.tif", True)   # 点亮
            gui_plot_panels._pan_press(w, key, _press_event(ax, x=100, y=120))
            self.assertIsNotNone(getattr(dock, "_box_start", None),
                                 "放大镜点亮时左键拖 = 框选放大")
            self.assertIsNone(getattr(dock, "_pan_start", None),
                              "框选时不该同时记平移起点（两套手势不能抢）")
            gui_plot_panels._pan_release(
                w, key, _press_event(ax, x=100, y=120))
        finally:
            w.close()

    def test_wheel_zoom_centers_on_cursor(self):
        """放大镜点亮时滚轮向上：光标点钉在原地，范围按每格 10% 向光标收拢。"""
        w = create_window()
        try:
            self._open_1d(w)
            key = "1D|data/fake_b.tif"
            ax = _axes(w, "1D", "data/fake_b.tif")
            ax.set_xlim(1.0, 8.0)
            self._magnifier(w, "data/fake_b.tif", True)
            gui_plot_panels._wheel_zoom(w, key, _scroll_event(ax, "up", 4.0, 2.0))
            xlo, xhi = ax.get_xlim()
            # 系数 1.1（每格 10%）：两侧各收拢 1/1.1
            self.assertAlmostEqual(xlo, 4.0 - 3.0 / 1.1, places=3)
            self.assertAlmostEqual(xhi, 4.0 + 4.0 / 1.1, places=3)
            # 光标对着的点"钉在原地"= 它在范围内的相对位置不变
            # （4.0 不是 (1,8) 的中点，所以中点不守恒、分数守恒）
            frac = (4.0 - xlo) / (xhi - xlo)
            self.assertAlmostEqual(frac, 3 / 7, places=3,
                                   msg="光标点的相对位置缩放后应不变")
            gui_plot_panels._wheel_zoom(w, key, _scroll_event(ax, "down", 4.0, 2.0))
            xlo2, xhi2 = ax.get_xlim()
            self.assertAlmostEqual(xlo2, 1.0, places=3)
            self.assertAlmostEqual(xhi2, 8.0, places=3)
        finally:
            w.close()

    def test_wheel_zoom_log_axis_never_freezes(self):
        """放大镜点亮时对数纵轴连续缩小：下限一路走低且始终 > 0
        （加性缩放会把下限算到 0 以下 → matplotlib 忽略整次设置，
        纵轴卡死）。"""
        w = create_window()
        try:
            self._open_1d(w)
            key = "1D|data/fake_b.tif"
            ax = _axes(w, "1D", "data/fake_b.tif")
            ax.set_yscale("log")
            ax.set_ylim(0.5, 3.0)
            self._magnifier(w, "data/fake_b.tif", True)
            prev_lo = 0.5
            for _ in range(6):
                gui_plot_panels._wheel_zoom(
                    w, key, _scroll_event(ax, "down", 4.0, 1.0))
                ylo, yhi = ax.get_ylim()
                self.assertGreater(ylo, 0.0,
                                   "对数轴下限不应撞到 0 以下")
                self.assertLess(ylo, prev_lo,
                                "每次缩小下限都应继续走低")
                prev_lo = ylo
        finally:
            w.close()

    def test_home_returns_after_wheel_zoom(self):
        """滚轮缩放后 Home 回到初始视图（滚轮绕过 mpl 手势，需自己
        补记账——否则账本空着，Home 无事可做）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            key = "1D|data/fake_b.tif"
            ax = _axes(w, "1D", "data/fake_b.tif")
            content = gui_panel_state._content(dock)
            self._magnifier(w, "data/fake_b.tif", True)
            x0 = ax.get_xlim()
            gui_plot_panels._wheel_zoom(w, key, _scroll_event(ax, "up", 4.0, 2.0))
            self.assertNotEqual(ax.get_xlim(), x0, "滚轮缩放应先改范围")
            content.toolbar._actions["home"].trigger()
            QApplication.processEvents()
            xlo, xhi = ax.get_xlim()
            self.assertAlmostEqual(xlo, x0[0], places=6,
                                   msg="Home 应回到滚轮缩放前的视图")
            self.assertAlmostEqual(xhi, x0[1], places=6)
        finally:
            w.close()

    def test_home_survives_a_program_redraw(self):
        """程序重画**不会**把"家"改成重画时那个窗口（这里没有手势介入）。

        快照里那个"视图窗口"就是缩放来的（"显示 2θ 范围"输入框 2026-09-27
        撤掉后，会写回它的只剩缩放/平移），重画只是把它再套一遍，不该成为
        新"家"——用户 2026-09-27："home 不是重画，只是恢复到最开始的比例"。
        旧版这条测试断言的正好相反（那时范围能手填，填完重画 = 新"家"）。
        """
        w = create_window()
        try:
            dock = self._open_1d(w)
            ax = _axes(w, "1D", "data/fake_b.tif")
            content = gui_panel_state._content(dock)
            home = tuple(ax.get_xlim())
            # 程序重画（等价：快照里带着一个视图窗口时点 [应用]）
            dock.params_snapshot["视图 2θ 下限 (°)"] = 3.0
            dock.params_snapshot["视图 2θ 上限 (°)"] = 6.0
            gui_plot_views._draw_1d(w, dock, dock.last_tth, dock.last_intensity)
            QApplication.processEvents()
            self.assertEqual(tuple(ax.get_xlim()), (3.0, 6.0),
                             "重画该按快照里那个窗口画")
            content.toolbar._actions["home"].trigger()
            QApplication.processEvents()
            self.assertEqual(tuple(ax.get_xlim()), home,
                             "Home 仍回到最初那个视图（重画没顶掉它）")
        finally:
            w.close()

    def test_drag_pans_plot(self):
        """按住左键拖动 = 整图平移（范围随拖拽位移）。"""
        w = create_window()
        try:
            self._open_1d(w)
            key = "1D|data/fake_b.tif"
            ax = _axes(w, "1D", "data/fake_b.tif")
            ax.set_xlim(1.0, 8.0)
            ax.set_ylim(1.0, 3.0)
            # (100,120) 像素按下 → 拖到 (150,100)：matplotlib 事件的
            # y 从画布底边起算（鼠标在屏幕上向下 = y 变小）。图跟着
            # 鼠标走：向右下拖 → 数据范围整体向左上移动
            gui_plot_panels._pan_press(w, key, _press_event(ax, x=100, y=120))
            gui_plot_panels._pan_motion(w, key, _press_event(ax, x=150, y=100))
            gui_plot_panels._pan_release(w, key, _press_event(ax, x=150, y=100))
            xlo, xhi = ax.get_xlim()
            self.assertLess(xlo, 1.0, "向右拖图 → 数据范围应左移")
            self.assertLess(xhi, 8.0)
            ylo, yhi = ax.get_ylim()
            self.assertGreater(ylo, 1.0, "向下拖图 → 数据范围应上移")
            self.assertGreater(yhi, 3.0)
        finally:
            w.close()


class TestCustomizeProtection(unittest.TestCase):
    """Customize 对话框改动保护（与用户讨论定稿的规则）：标题/轴标签/
    曲线样式/纵轴刻度以用户为准，重画（应用/恢复默认/对比刷新）一律
    不覆盖；参数面板里又改了一遍（显示名 → 标题、[对数纵轴] → 刻度）
    才由参数接管。"""

    def _open_1d(self, w):
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, ["data/fake_a.tif"])
            _open_view(w, "1D")
            drawn = _wait_until(
                lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0)
            self.assertTrue(drawn)
        return _dock(w, "1D", "data/fake_a.tif")

    def _redraw(self, w, dock):
        """等价于点 [应用] 的程序重画路径。"""
        gui_plot_views._draw_1d(w, dock, dock.last_tth, dock.last_intensity)
        QApplication.processEvents()

    def test_custom_title_survives_redraw(self):
        """Customize 改过的标题重画不覆盖（连续重画也保留）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            ax = _axes(w, "1D", "data/fake_a.tif")
            ax.set_title("我的手改标题")   # 模拟在 Customize 里改
            self._redraw(w, dock)
            self.assertEqual(ax.get_title(), "我的手改标题",
                             "Customize 改过的标题不应被重画覆盖")
            self._redraw(w, dock)
            self.assertEqual(ax.get_title(), "我的手改标题",
                             "连续重画也应一直保留")
        finally:
            w.close()

    def test_title_follows_display_change(self):
        """显示名（参数源）改过 → 标题跟新的默认（参数接管）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            ax = _axes(w, "1D", "data/fake_a.tif")
            ax.set_title("我的手改标题")
            self._redraw(w, dock)
            self.assertEqual(ax.get_title(), "我的手改标题")
            dock.panel_display = "新名字"   # 参数源改了
            self._redraw(w, dock)
            self.assertEqual(ax.get_title(),
                             "新名字: full azimuthal integration",
                             "显示名改过 → 标题应跟新的默认")
        finally:
            w.close()

    def test_axis_labels_survive_redraw(self):
        """Customize 改过的轴标签重画不覆盖（无参数源 → 永远用户为准）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            ax = _axes(w, "1D", "data/fake_a.tif")
            ax.set_xlabel("我的 X")
            ax.set_ylabel("我的 Y")
            self._redraw(w, dock)
            self.assertEqual(ax.get_xlabel(), "我的 X")
            self.assertEqual(ax.get_ylabel(), "我的 Y")
            self._redraw(w, dock)
            self.assertEqual(ax.get_xlabel(), "我的 X", "连续重画也应保留")
        finally:
            w.close()

    def test_curve_style_survives_redraw(self):
        """改过的线型/线宽重画不覆盖；颜色跟"渐变色系"参数走
        （任务六起颜色有了参数入口，不再按旧快照保护）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            ax = _axes(w, "1D", "data/fake_a.tif")
            line = ax.lines[0]
            line.set_color("red")
            line.set_linestyle("--")
            line.set_linewidth(3)
            self._redraw(w, dock)
            new = ax.lines[0]
            self.assertEqual(new.get_color(), "#2a78d6",
                             "颜色跟配色参数（默认蓝基色）")
            self.assertEqual(new.get_linestyle(), "--")
            self.assertEqual(new.get_linewidth(), 3)
            self._redraw(w, dock)
            self.assertEqual(ax.lines[0].get_linestyle(), "--",
                             "连续重画也应保留线型")
        finally:
            w.close()

    def test_scale_user_wins_until_param_toggled(self):
        """Customize 改的纵轴刻度以用户为准，直到 [对数纵轴] 又改过。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            ax = _axes(w, "1D", "data/fake_a.tif")
            ax.set_yscale("log")   # 模拟在 Customize 里改刻度
            self._redraw(w, dock)
            self.assertEqual(ax.get_yscale(), "log",
                             "Customize 改的刻度不应被重画覆盖")
            dock.params_snapshot["对数纵轴"] = True   # 参数又改了一遍
            self._redraw(w, dock)
            self.assertEqual(ax.get_yscale(), "log")
            dock.params_snapshot["对数纵轴"] = False   # 再改一遍
            self._redraw(w, dock)
            self.assertEqual(ax.get_yscale(), "linear",
                             "参数又改过 → 参数接管刻度")
        finally:
            w.close()


class TestTileScrollReset(unittest.TestCase):
    """回归：绘图区滚动状态下点横排/竖排，Qt 会把滚动偏移混进子
    窗口 move 坐标——图被多推一段、灰区越排越多（探针实证：横滚
    628 时再排列，桌面多出 628px）。修复 = 摆图前滚动归零。"""

    def _open_six(self, w):
        for i in range(3):
            gui_plot_panels._open_plot_panel(w, "1D", f"1D|fake{i}.tif", f"1D_{i}")
        for i in range(3):
            gui_plot_panels._open_plot_panel(w, "2D", f"2D|fake{i}.tif", f"2D_{i}")
        for _ in range(10):
            QApplication.processEvents()

    @staticmethod
    def _scroll(w):
        h = w.mdi.horizontalScrollBar()
        v = w.mdi.verticalScrollBar()
        return h.value(), h.maximum(), v.value(), v.maximum()

    def test_arrange_after_scrolling_resets_and_never_drifts(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_six(w)
            w.arrange_buttons["横排"].click()
            for _ in range(10):
                QApplication.processEvents()
            _, hmax1, _, vmax1 = self._scroll(w)
            self.assertGreater(hmax1, 0, "两行三列应溢出视口出滚动条")
            # 用户滚到右下角看第 6 张图
            w.mdi.horizontalScrollBar().setValue(hmax1)
            w.mdi.verticalScrollBar().setValue(
                w.mdi.verticalScrollBar().maximum())
            for _ in range(10):
                QApplication.processEvents()
            # 滚动状态下点竖排：视图应归零、布局不漂移
            w.arrange_buttons["竖排"].click()
            for _ in range(10):
                QApplication.processEvents()
            hv, _, vv, _ = self._scroll(w)
            self.assertEqual((hv, vv), (0, 0),
                             "排列后视图应回到左上角（滚动归零）")
            # 再横排：桌面最大值应与第一次横排一致（无灰区增长）。
            # 与第一次横排比而不是写死数值：面板内容尺寸会随视图
            # 接线升级（2D 从占位标签变真画布面板），写死就不稳了
            w.arrange_buttons["横排"].click()
            for _ in range(10):
                QApplication.processEvents()
            _, hmax2, _, vmax2 = self._scroll(w)
            self.assertEqual((hmax2, vmax2), (hmax1, vmax1),
                             "反复排列后滚动范围不应增长（回归：滚动偏移混入 move）")
        finally:
            w.hide()
            w.close()


class TestCascadeCap(unittest.TestCase):
    """新图落点 = 左上角小错位级联：24px 一档、6 档循环回起点
    （下面几张的标题栏露出来，永远待在左上角区域）。"""

    def test_seventh_panel_cycles_back_to_origin(self):
        w = create_window()
        try:
            subs = []
            for i in range(7):
                subs.append(gui_plot_panels._open_plot_panel(
                    w, "1D", f"1D|fake{i}.tif", f"fake{i}"))
            for _ in range(10):
                QApplication.processEvents()
            # 24px 一档：第 2 张在 (40,40)，第 6 张在 (136,136)
            self.assertEqual((subs[1].x(), subs[1].y()), (40, 40))
            self.assertEqual((subs[5].x(), subs[5].y()), (136, 136))
            # 6 档循环：第 7 张回到第 1 张的落点
            self.assertEqual((subs[6].x(), subs[6].y()),
                             (subs[0].x(), subs[0].y()),
                             "第 7 张应循环回左上角起点")
        finally:
            w.close()


class TestAreaZoom(unittest.TestCase):
    """总缩放：Ctrl+滚轮（视口过滤器 / 图上方 mpl 层拦截）与底部
    − 100% + 按钮，绘图区全体围绕视口中心同比缩放（50%–200%，
    每格 10%）；弹出去的不参与、平铺不碰它；新开/收回的图按当前
    总缩放落位；缩放不记成"用户拖过"。"""

    def _open_two(self, w):
        a = gui_plot_panels._open_plot_panel(w, "1D", "1D|a.tif", "a")
        b = gui_plot_panels._open_plot_panel(w, "2D", "2D|b.tif", "b")
        for _ in range(10):
            QApplication.processEvents()
        return a, b

    def _ctrl_wheel(self, target, pos, delta=120):
        """构造真实 QWheelEvent（Ctrl 按住）投给目标部件。"""
        ev = QWheelEvent(
            QPointF(pos), QPointF(pos), QPoint(0, 0), QPoint(0, delta),
            Qt.NoButton, Qt.ControlModifier, Qt.ScrollPhase.ScrollUpdate,
            False, Qt.MouseEventNotSynthesized,
            QPointingDevice.primaryPointingDevice())
        QApplication.sendEvent(target, ev)
        QApplication.processEvents()

    def test_apply_scales_subs_around_center(self):
        """全体同比缩放：尺寸 ×k、位置围绕视口中心缩放、比例记忆不碰。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            a, b = self._open_two(w)
            geo = {d: (d.x(), d.y(), d.width(), d.height()) for d in (a, b)}
            # 锚点 = 缩放动手时的视口中心（缩放会触发滚动条出现/
            # 消失，事后量视口会和动手时差 18px）
            cx = w.mdi.viewport().width() / 2
            cy = w.mdi.viewport().height() / 2
            gui_panels._apply_area_zoom(w, 1.1)
            for _ in range(10):
                QApplication.processEvents()
            for d, (x, y, wd, ht) in geo.items():
                self.assertEqual(d.width(), round(wd * 1.1),
                                 "总缩放后宽度应 ×1.1")
                self.assertEqual(d.height(), round(ht * 1.1),
                                 "总缩放后高度应 ×1.1")
                self.assertEqual(d.x(), round(cx + (x - cx) * 1.1),
                                 "位置应围绕视口中心缩放")
                self.assertEqual(d.y(), round(cy + (y - cy) * 1.1),
                                 "位置应围绕视口中心缩放")
                self.assertFalse(getattr(d, "_dragged", False),
                                 "程序性缩放不应记成用户拖过")
            self.assertEqual(w.zoom_label.text(), "110%")
        finally:
            w.hide()
            w.close()

    def test_ctrl_wheel_on_viewport_zooms(self):
        """Ctrl+滚轮（灰底上）= 总缩放每格 10%。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_two(w)
            self._ctrl_wheel(w.mdi.viewport(), QPoint(50, 50))
            self.assertAlmostEqual(w._area_zoom, 1.1, places=6,
                                   msg="Ctrl+滚轮向上应放大 10%")
            self._ctrl_wheel(w.mdi.viewport(), QPoint(50, 50), delta=-120)
            self.assertAlmostEqual(w._area_zoom, 1.0, places=6,
                                   msg="Ctrl+滚轮向下应缩小回 100%")
        finally:
            w.hide()
            w.close()

    def test_ctrl_wheel_over_canvas_zooms_once(self):
        """光标在图上方 Ctrl+滚轮 = 总缩放（mpl 层拦截），且 accept
        掉 Qt 事件——否则传播到视口过滤器会再缩一次（双倍）。"""
        w = create_window()
        try:
            a, _ = self._open_two(w)
            ax = gui_panel_state._content(a).axes_1d
            ax.set_xlim(1.0, 8.0)
            accepted = []
            gui = SimpleNamespace(
                modifiers=lambda: Qt.ControlModifier,
                angleDelta=lambda: QPoint(0, 120),
                accept=lambda: accepted.append(True))
            gui_plot_panels._wheel_zoom(
                w, "1D|a.tif",
                SimpleNamespace(inaxes=ax, guiEvent=gui,
                                xdata=4.0, ydata=2.0))
            self.assertAlmostEqual(w._area_zoom, 1.1, places=6,
                                   msg="图上方 Ctrl+滚轮应触发总缩放")
            self.assertEqual(accepted, [True],
                             "应 accept 掉 Qt 事件防止传播到视口再缩一次")
            self.assertEqual(ax.get_xlim(), (1.0, 8.0),
                             "Ctrl+滚轮不应缩放图本身（放大镜也没点亮）")
        finally:
            w.close()

    def test_clamp_50_200(self):
        """总缩放夹逼在 50%–200%。"""
        w = create_window()
        try:
            self._open_two(w)
            gui_panels._apply_area_zoom(w, 0.01)
            self.assertAlmostEqual(w._area_zoom, 0.5, places=6)
            self.assertEqual(w.zoom_label.text(), "50%")
            gui_panels._apply_area_zoom(w, 99.0)
            self.assertAlmostEqual(w._area_zoom, 2.0, places=6)
            self.assertEqual(w.zoom_label.text(), "200%")
        finally:
            w.close()

    def test_buttons_step_10_percent(self):
        """底部 − / + 按钮：每点一次 10%。"""
        w = create_window()
        try:
            self._open_two(w)
            w.zoom_buttons["+"].click()
            QApplication.processEvents()
            self.assertAlmostEqual(w._area_zoom, 1.1, places=6)
            w.zoom_buttons["−"].click()
            QApplication.processEvents()
            self.assertAlmostEqual(w._area_zoom, 1.0, places=6)
        finally:
            w.close()

    def test_new_panel_opens_at_current_zoom(self):
        """新图按当前总缩放开（和周围的图大小一致）。"""
        w = create_window()
        try:
            w.show()   # 显示后布局才会激活（隐藏窗口的 resize 不挤画布）
            w.resize(1400, 900)
            a, _ = self._open_two(w)
            gui_panels._apply_area_zoom(w, 0.8)
            for _ in range(10):
                QApplication.processEvents()
            p = gui_plot_panels._open_plot_panel(w, "1D", "1D|c.tif", "c")
            for _ in range(10):
                QApplication.processEvents()
            # 契约 = 新图和周围已缩放的图一样大；画布被标题栏/工具栏
            # 壳吃掉固定高度，不会正好是 500×0.8，所以与邻居对比
            self.assertEqual((p.width(), p.height()), (a.width(), a.height()),
                             msg="新图子窗口应和周围已缩放的图一样大")
            canvas = gui_panel_state._content(p).canvas
            canvas_a = gui_panel_state._content(a).canvas
            self.assertEqual((canvas.width(), canvas.height()),
                             (canvas_a.width(), canvas_a.height()),
                             msg="新图画布应和周围图一致")
        finally:
            w.hide()
            w.close()

    def test_dock_back_lands_at_current_zoom(self):
        """弹出的图收回时按当前总缩放落位。"""
        w = create_window()
        try:
            w.show()   # 见 test_new_panel_opens_at_current_zoom
            w.resize(1400, 900)
            a, _ = self._open_two(w)
            ref = gui_plot_panels._open_plot_panel(w, "1D", "1D|ref.tif", "ref")
            for _ in range(10):
                QApplication.processEvents()
            gui_panels._toggle_pop_out(w, "1D|a.tif")
            for _ in range(10):
                QApplication.processEvents()
            gui_panels._apply_area_zoom(w, 0.8)
            for _ in range(10):
                QApplication.processEvents()
            gui_panels._toggle_pop_out(w, "1D|a.tif")
            for _ in range(10):
                QApplication.processEvents()
            sub = _dock(w, "1D", "a.tif")
            canvas2 = gui_panel_state._content(sub).canvas
            canvas_ref = gui_panel_state._content(ref).canvas
            # 收回 = 按当前总缩放落位：弹出时 100%、收回时 80%，
            # 应和没弹出过的 1D 邻居 ref 一致（弹出时的画布已带
            # 100% 缩放，直接乘 80% 会双重缩，见 _pop_zoom）
            self.assertEqual((sub.width(), sub.height()),
                             (ref.width(), ref.height()),
                             msg="收回的子窗口应和周围的图一样大")
            self.assertEqual((canvas2.width(), canvas2.height()),
                             (canvas_ref.width(), canvas_ref.height()),
                             msg="收回的画布应和周围的图一致")
        finally:
            w.hide()
            w.close()

    def test_popped_window_not_touched(self):
        """总缩放只动绘图区里的图，弹出的独立窗口大小不变。"""
        w = create_window()
        try:
            a, b = self._open_two(w)
            gui_panels._toggle_pop_out(w, "1D|a.tif")
            for _ in range(10):
                QApplication.processEvents()
            floated = w.plot_docks["1D|a.tif"]
            fw, fh = floated.width(), floated.height()
            bw = b.width()
            gui_panels._apply_area_zoom(w, 0.5)
            for _ in range(10):
                QApplication.processEvents()
            self.assertEqual((floated.width(), floated.height()), (fw, fh),
                             "弹出窗口不应被总缩放带动")
            self.assertEqual(b.width(), round(bw * 0.5),
                             "绘图区内的图应被缩放")
        finally:
            w.close()

    def test_tile_keeps_zoom(self):
        """平铺不碰总缩放（各管各的）。"""
        w = create_window()
        try:
            self._open_two(w)
            gui_panels._apply_area_zoom(w, 1.1)
            w.arrange_buttons["横排"].click()
            for _ in range(10):
                QApplication.processEvents()
            self.assertAlmostEqual(w._area_zoom, 1.1, places=6,
                                   msg="平铺不应重置总缩放")
            self.assertEqual(w.zoom_label.text(), "110%")
        finally:
            w.close()


class TestStatusBarPartition(unittest.TestCase):
    """状态行分区：进度条 | 竖分隔线 | 文件标签。

    **坐标读数 2026-10-07 搬去绘图区横带**（用户："实时坐标放到靠上
    一点便于观察"——原来在窗口最底部的状态行里，看图时眼睛够不着）；
    新家由 TestPlotStripLayout 钉着。"""

    def test_coord_frame_and_separator(self):
        w = create_window()
        try:
            self.assertEqual(w.coord_label.frameShape(), QFrame.StyledPanel)
            self.assertIn("monospace", w.coord_label.styleSheet())
            self.assertFalse(w.statusBar().isAncestorOf(w.coord_label),
                             "坐标读数已搬去绘图区横带")
            self.assertTrue(w.centralWidget().isAncestorOf(w.coord_label))
            vlines = [c for c in w.statusBar().findChildren(QFrame)
                      if c.frameShape() == QFrame.VLine]
            self.assertEqual(len(vlines), 1,
                             "进度与文件信息之间应有竖分隔线")
        finally:
            w.close()


class TestPlotStripLayout(unittest.TestCase):
    """绘图区横带的排布 + 日志"两边缩"（用户 2026-10-07 定的布局轮）。

    横带从左到右：`分析模式 | 空白 | 坐标读数 | 横排/竖排 | −100%+ |
    全部关闭`（全关在最右、离排布按钮拉开距离防手滑）；日志的底角
    归属改成左右区——文件栏/参数栏贯通到底，日志只占中间那段。"""

    def _all_open(self, w, wide=1440):
        for n in ("文件", "参数", "日志"):
            if not w.panel_toggles[n].isChecked():
                w.panel_toggles[n].click()
        w.show()
        w.resize(wide, 860)
        for _ in range(6):
            QApplication.processEvents()

    def test_strip_order_and_close_all_at_far_right(self):
        w = create_window()
        try:
            self._all_open(w)
            strip = w.coord_label.parentWidget()

            def x_of(widget):
                return widget.mapTo(strip, QPoint(0, 0)).x()

            self.assertLess(x_of(w.coord_label),
                            x_of(w.arrange_buttons["横排"]),
                            "坐标读数该在横排/竖排左边")
            self.assertLess(x_of(w.arrange_buttons["竖排"]),
                            x_of(w.zoom_buttons["−"]))
            x_close = x_of(w.close_all_btn)
            self.assertGreater(x_close, x_of(w.zoom_buttons["+"]),
                               "全部关闭该在缩放区右边（最右端）")
            self.assertGreater(
                x_close - x_of(w.arrange_buttons["竖排"]), 100,
                "[全部关闭] 该离 [横排]/[竖排] 拉开距离（防手滑全关）")
        finally:
            w.close()

    def test_coord_slot_stretches_but_not_with_text(self):
        """读数格吃剩余宽（2026-10-10 改）：宽敞整句显示、挤不下才省略，
        但宽度只跟版面有关、**与文本长短无关**（右边按钮不会被挤得跳）。

        用户："对比图的坐标显示还是有问题……挪位置以前怎么没这个问题"
        ——10-07 搬进横带时给了固定 230 px 上限，对比读数必被裁；现在
        去上限、行里 stretch=1。
        """
        w = create_window()
        try:
            self._all_open(w)
            w.coord_label.setText("2θ 3.335°  I 48230")
            QApplication.processEvents()
            slot = w.coord_label.width()
            self.assertGreater(slot, 260, "宽敞时该比旧上限 230 宽得多")
            # 造一段"刚好放得下"的长文本：起点本身比旧上限 230 长
            fm = w.coord_label.fontMetrics()
            base = "LMFP_1_atten0-00029.tif · 2θ 3.214° I 1234.5"
            self.assertGreater(fm.horizontalAdvance(base), 230,
                               "前置：这段比旧上限长（旧行为必被裁）")
            long_text = base
            while fm.horizontalAdvance(long_text + " · 12345") < slot - 16:
                long_text += " · 12345"
            w.coord_label.setText(long_text)
            QApplication.processEvents()
            self.assertEqual(w.coord_label.width(), slot,
                             "槽宽不随文本长短变")
            self.assertEqual(QLabel.text(w.coord_label), long_text,
                             "放得下就整句显示（不再被 230 截断）")
            w.coord_label.setText("X" * 500 + " 2θ 3.335°")
            QApplication.processEvents()
            shown = QLabel.text(w.coord_label)   # 跳过 _ElideLabel 的覆写
            self.assertIn("…", shown, "实在挤不下才显示省略号")
            self.assertEqual(w.coord_label.width(), slot,
                             "省略也不改槽宽")
        finally:
            w.close()

    def test_log_shortens_between_the_side_columns(self):
        """日志"两边缩"：三栏全开只占中间；只开日志时铺满一行。"""
        w = create_window()
        try:
            self._all_open(w)
            self.assertEqual(w.corner(Qt.BottomLeftCorner),
                             Qt.LeftDockWidgetArea)
            self.assertEqual(w.corner(Qt.BottomRightCorner),
                             Qt.RightDockWidgetArea)
            self.assertLess(w.log_dock.width(), w.width() - 400,
                            "三栏全开：日志该只占中间一段（实测 878/1440）")
            w.panel_toggles["文件"].click()
            w.panel_toggles["参数"].click()
            for _ in range(6):
                QApplication.processEvents()
            self.assertGreaterEqual(w.log_dock.width(), w.width() - 20,
                                    "只开日志：该铺满一行（实测 1440/1440）")
        finally:
            w.close()


class TestPopOut(unittest.TestCase):
    """[弹出]/[收回]：面板搬进独立 OS 窗口再收回，内容与状态不丢；
    弹出窗口 × = 关闭即遗忘；平铺只排主窗口内的子窗口。"""

    def _open_1d(self, w):
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, ["data/fake_b.tif"])
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0))
        QApplication.processEvents()

    def test_pop_out_and_back_keeps_content(self):
        """弹出 → 内容同一个对象、曲线还在、按钮变"收回"；收回反向。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_1d(w)
            key = "1D|data/fake_b.tif"
            content = gui_panel_state._content(_dock(w, "1D", "data/fake_b.tif"))
            gui_panels._toggle_pop_out(w, key)
            QApplication.processEvents()
            floated = w.plot_docks[key]
            self.assertIsInstance(floated, gui_panels._FloatedWindow)
            self.assertIs(gui_panel_state._content(floated), content,
                          "弹出后内容应是同一个对象")
            self.assertEqual(content.popout_btn.text(), "收回")
            self.assertEqual(len(content.axes_1d.lines), 1, "曲线应保留")
            gui_panels._toggle_pop_out(w, key)
            QApplication.processEvents()
            back = w.plot_docks[key]
            self.assertIsInstance(back, gui_app.QMdiSubWindow)
            self.assertIs(gui_panel_state._content(back), content)
            self.assertEqual(content.popout_btn.text(), "弹出")
            self.assertEqual(len(content.axes_1d.lines), 1)
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_pop_out_view_sync_still_writes(self):
        """弹出后缩放/平移仍写回参数快照（回调按 key 找容器）。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_1d(w)
            key = "1D|data/fake_b.tif"
            gui_panels._toggle_pop_out(w, key)
            QApplication.processEvents()
            floated = w.plot_docks[key]
            ax = gui_panel_state._content(floated).axes_1d
            ax.set_xlim(2.0, 3.0)
            QApplication.processEvents()
            self.assertAlmostEqual(
                float(floated.params_snapshot["视图 2θ 下限 (°)"]), 2.0,
                places=4, msg="弹出后视图范围应仍写回参数快照")
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_pop_out_close_forgets(self):
        """弹出窗口 × = 关闭即遗忘：登记移除。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_1d(w)
            key = "1D|data/fake_b.tif"
            gui_panels._toggle_pop_out(w, key)
            QApplication.processEvents()
            w.plot_docks[key].close()
            QApplication.processEvents()
            self.assertNotIn(key, w.plot_docks, "弹出窗口 × 应抹掉登记")
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_arrange_excludes_floated(self):
        """平铺只排主窗口内的子窗口，弹出去的窗口原地不动。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0
                    and len(_axes(w, "1D", "data/fake_b.tif").lines) > 0))
            QApplication.processEvents()
            key_b = "1D|data/fake_b.tif"
            gui_panels._toggle_pop_out(w, key_b)
            QApplication.processEvents()
            floated = w.plot_docks[key_b]
            geo_before = floated.geometry()
            w.arrange_buttons["横排"].click()
            QApplication.processEvents()
            self.assertIn("已横排 1 个面板", w.log_text.toPlainText())
            self.assertEqual(floated.geometry(), geo_before,
                             "平铺不应挪动弹出窗口")
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()


class TestPanelClose(unittest.TestCase):
    """关闭面板（子窗口 ×）= 关闭即遗忘：登记移除、焦点移交、重开
    全新默认；在飞任务迟到结果静默丢弃、旧代结果不串新图。"""

    def _open_two(self, w):
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0
                and len(_axes(w, "1D", "data/fake_b.tif").lines) > 0))
        QApplication.processEvents()

    def test_close_removes_and_hands_over_focus(self):
        """关掉焦点面板 → 编辑对象移给下一张；全关 → 未选中。"""
        w = create_window()
        try:
            self._open_two(w)
            focus = w.focus_panel
            other = next(k for k in w.plot_docks if k != focus)
            gui_panels._close_panel(w, focus)
            QApplication.processEvents()
            self.assertNotIn(focus, w.plot_docks)
            self.assertEqual(w.focus_panel, other, "焦点应移交给剩余面板")
            gui_panels._close_panel(w, other)
            QApplication.processEvents()
            self.assertFalse(w.plot_docks)
            self.assertIsNone(w.focus_panel)
            self.assertEqual(
                w.focus_label.text(), gui_state.FOCUS_EMPTY_TEXT,
                "空态文案（含双击指路）来自 panel_state 的唯一一份")
        finally:
            w.close()

    def test_reopen_is_fresh_default(self):
        """关掉前拖过/动过显示参数 → 重开 = 全新的默认图。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            self._open_two(w)
            key_a = "1D|data/fake_a.tif"
            _resize_panel(w, _dock(w, "1D", "data/fake_a.tif"), 700, 400)
            _axes(w, "1D", "data/fake_a.tif").set_ylim(1.0, 2.0)
            gui_panels._close_panel(w, key_a)
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                _open_view(w, "1D")   # 文件仍在列表里，重新出图
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0))
            QApplication.processEvents()
            new = _dock(w, "1D", "data/fake_a.tif")
            self.assertFalse(new._dragged, "重开不应继承拖过状态")
            self.assertEqual(new._canvas_pref, (500, 300),
                             "重开应回到默认画布比例")
            canvas = gui_panel_state._content(new).canvas
            self.assertGreater(canvas.width(), 450)
            self.assertLess(canvas.width(), 550)
            ylo, yhi = _axes(w, "1D", "data/fake_a.tif").get_ylim()
            self.assertNotAlmostEqual(ylo, 1.0,
                                      msg="重开不应继承旧显示范围")
            self.assertNotAlmostEqual(yhi, 2.0,
                                      msg="重开不应继承旧显示范围")
        finally:
            w.hide()   # 见 TestPlotFixedSize：显示过的窗口关窗会弹模态框
            w.close()

    def test_close_with_inflight_task_drops_late_result(self):
        """后台还在算时关掉面板：迟到结果静默丢弃，不崩不串。"""
        def slow(path_str, geom, npt):
            time.sleep(0.3)
            return np.array([0.5, 1.0, 8.5]), np.array([1.0, 2.0, 3.0])

        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=slow):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "1D")   # 后台开算（0.3s）
                key = "1D|data/fake_b.tif"
                self.assertTrue(_wait_until(lambda: key in w.plot_docks))
                gui_panels._close_panel(w, key)
            for _ in range(50):   # 等迟到结果送达（不崩 = 通过）
                QApplication.processEvents()
                time.sleep(0.01)
            self.assertNotIn("积分完成", w.log_text.toPlainText())
        finally:
            w.close()

    def test_compare_reopen_after_close_draws_fresh(self):
        """关掉对比面板再点 [对比]：重开一张、2 条曲线，旧面板的数据
        不许混进新图。

        老版本钉的是"在飞的旧代任务结果作废"——现场积分退休（2026-10-16
        栅栏）后没有在飞任务了；epoch/gen 守卫保留为安全网但已不可达，
        这条测试改钉 close→reopen 的结果隔离。
        """
        w = create_window()
        try:
            _prime_1d(w, ["data/fake_a.tif", "data/fake_b.tif"])
            w.compare_btn.click()
            keys = [k for k in w.plot_docks if k.startswith("对比")]
            self.assertTrue(keys)
            gui_panels._close_panel(w, keys[0])
            w.compare_btn.click()              # 重开新面板
            keys = [k for k in w.plot_docks if k.startswith("对比")]
            self.assertTrue(_wait_until(lambda: keys))
            ax = gui_panel_state._content(w.plot_docks[keys[0]]).axes_1d
            self.assertEqual(len(ax.lines), 2)
            self.assertEqual(w.log_text.toPlainText().count("对比完成"), 2,
                             "两次点 [对比] 各完成一次；旧面板已关、不混线")
        finally:
            w.close()


class TestToolbarSave(unittest.TestCase):
    """面板自带工具栏 [Save]：存 PNG 并置 figure_saved（关窗不再问）。"""

    def test_toolbar_save_sets_figure_saved(self):
        w = create_window()
        try:
            dock = _draw_one_1d(w)
            fig = gui_panel_state._content(dock).figure
            with mock.patch.object(gui_plot_panel_build, "_ask_save_options",
                                   return_value={"dpi": 300, "fmt": "png"}), \
                 mock.patch.object(QFileDialog, "getSaveFileName",
                                   return_value=("/tmp/panel_out", "PNG 图片 (*.png)")) as dlg, \
                 mock.patch.object(fig, "savefig") as savefig:
                gui_panel_state._content(dock).toolbar.save_figure()
                self.assertTrue(dlg.called)
                savefig.assert_called_once_with("/tmp/panel_out.png", dpi=300)
                self.assertTrue(dock.figure_saved)
                self.assertIn("已保存 1D_fake_b.tif → /tmp/panel_out.png",
                              w.log_text.toPlainText())
        finally:
            w.close()


class TestSavePromptExcludesClosed(unittest.TestCase):
    """关软件只问当时开着的图：已关闭的面板不进入询问。"""

    def test_close_prompt_counts_only_open_panels(self):
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0
                    and len(_axes(w, "1D", "data/fake_b.tif").lines) > 0))
            gui_panels._close_panel(w, "1D|data/fake_b.tif")
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard") as confirm:
                self.assertTrue(w.close())
            self.assertEqual(confirm.call_args.args[1], 1,
                             "已关闭的面板不应再询问")
        finally:
            w.close()
