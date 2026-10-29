"""核心接线：选择/五个入口/视图/焦点/保存/曲线颜色/待应用参数

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


class TestSelectionOnly(unittest.TestCase):
    """选文件 ≠ 计算：点选只是登记当前文件。"""

    def test_selecting_file_does_not_run(self):
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute) as fake:
                add_checked(w, ["data/fake_b.tif"])
                time.sleep(0.6)          # 给足"防抖级"时间
                QApplication.processEvents()
                self.assertEqual(fake.call_count, 0,
                                 "选文件不应触发任何计算")
            self.assertEqual(w.file_label.text(), "fake_b.tif")
            self.assertIn("已添加 1 个文件", w.log_text.toPlainText())
        finally:
            w.close()


class TestViewButtonRuns(unittest.TestCase):
    """点击作图按钮 = 为当前文件开面板并计算（纯动作，点一次算一次）。"""

    def test_1d_click_computes_and_draws(self):
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "1D")
                drawn = _wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
                self.assertTrue(drawn, "点 1D 后应画出曲线")
            dock = _dock(w, "1D", "data/fake_b.tif")
            # 面板标题 = 视图_文件名，多张图一眼分清
            self.assertEqual(dock.windowTitle(), "1D_fake_b.tif")
            self.assertFalse(dock.isHidden())
            self.assertTrue(
                _axes(w, "1D", "data/fake_b.tif").get_title()
                .startswith("fake_b.tif"))
            self.assertIn("打开 1D 面板：fake_b.tif", w.log_text.toPlainText())
            self.assertIn("积分完成：fake_b.tif", w.log_text.toPlainText())
            # x 轴范围跟随参数坞的 2θ 上下限
            self.assertEqual(_axes(w, "1D", "data/fake_b.tif").get_xlim(),
                             (1.0, 8.0))
            # 计算完成的图自动成为编辑对象（指向具体面板）
            self.assertEqual(w.focus_panel, "1D|data/fake_b.tif")
            self.assertIn("1D_fake_b.tif", w.focus_label.text())
        finally:
            w.close()

    def test_reclicking_the_current_type_replots(self):
        """再点一次**已高亮**的类型按钮 = 按现在的勾选重出图。

        2026-10-08 删掉原图页底部的 [出图] 后，这是"重出图"的唯一手势
        （它与那个按钮本来就是同一次调用）：点已勾选的 checkable 按钮
        照常发 clicked，高亮由 _select_plot_type 复原——不会停在未选态。
        """
        w = create_window()
        try:
            w.show()
            calls = []
            with mock.patch.object(gui_param_pages, "_plot_view",
                                   side_effect=lambda win, name:
                                   calls.append(name)):
                w.view_buttons["剖面"].click()
                w.view_buttons["剖面"].click()   # 已高亮：再点 = 重出
            self.assertEqual(calls, ["剖面", "剖面"], "两次点击各出一次图")
            self.assertTrue(w.view_buttons["剖面"].isChecked(), "高亮保住")
            self.assertFalse(w.view_buttons["2D"].isChecked())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_single_1d_completion_refreshes_the_file_bar(self):
        """单张 1D 算完，文件栏**当场**长出「1D 产物」。

        回归（用户 2026-10-05）：刷新文件栏原先只挂在"批量（≥2 张）收尾"
        里（_batch_step），单张算完从来不刷——要等下一次别的动作（比如再
        打开一张文件）才"忽然出现"。用户原话："打开单张进行 1d 处理后
        在文件栏不显示，如果再打开另一张，就显示了"。
        """
        w = create_window()
        try:
            folder = Path(tempfile.mkdtemp(prefix="xrd_1d_single_"))
            p = folder / "single-00001.tif"
            p.write_bytes(b"x" * 2048)          # 真文件：产物要真落盘才列得出来
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, [str(p)])
                _open_view(w, "1D")
                drawn = _wait_until(
                    lambda: len(_axes(w, "1D", str(p)).lines) > 0)
                self.assertTrue(drawn, "点 1D 后应画出曲线")
                self.assertIsNotNone(
                    _group_by_text(w, "1D 产物"),
                    "单张算完，文件栏该当场出现「1D 产物」组")
        finally:
            w.close()

    def test_view_button_is_action_not_toggle(self):
        """点两次 = 算两次。旧版勾选按钮的第二次点击会关掉面板、
        不算图，用户误以为"点击画图画不了"——回归保护。"""
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute) as fake:
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(lambda: fake.call_count >= 1))
                _open_view(w, "1D")   # 第二次点击：必须重新计算
                self.assertTrue(_wait_until(lambda: fake.call_count >= 2))
                # 同一文件重复点 = 刷新同一张面板，不是开新面板
                self.assertEqual(len(w.plot_docks), 1)
                self.assertFalse(_dock(w, "1D", "data/fake_b.tif").isHidden(),
                                 "重复点击不应关掉面板")
        finally:
            w.close()

    def test_too_many_files_compute_only_and_listed(self):
        """勾选超过开图上限 → 一张都不画（只算不画），结果进文件栏产物组。

        用户 2026-09-26："如果原始很多，进行 1d 图时全部打开，只会打开
        二十来张。能不能…不弹框，直接列出在文件区，然后可以点开看"——
        做法：超过 MAX_PANELS_PER_BATCH 就全走"只算不画"那条老路，
        批走完刷产物分组（「1D 产物」），看哪张点哪张。
        """
        w = create_window()
        files = _tmp_files(gui_views.MAX_PANELS_PER_BATCH + 1)
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files([str(p) for p in files], select=True)
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: "批完成" in w.log_text.toPlainText(), 60000))
            self.assertEqual(len(w.plot_docks), 0, "一张面板都不该开")
            log = w.log_text.toPlainText()
            self.assertIn("全部只算不画", log)
            self.assertIn("1D 产物", log)        # 指路：结果在文件栏
            self.assertIsNotNone(_group_by_text(w, "1D 产物"),
                                 "产物分组该长出来（有东西可点开）")
        finally:
            w.close()

    def test_double_click_on_raw_opens_the_2d_panel(self):
        """双击**原始条目** = 打开它本身的 2D 图；右键那条（显式）照旧开 1D。

        2026-10-08 用户拍板改的：旧行为（2026-09-27"双击打不开、只给指路"）
        的理由是"原始数据能出多种图"——但"双击必有反应"是更硬的习惯，而
        原始数据的本体就是那张 2D 图（零计算、秒开）；1D / 剖面 / 瀑布仍
        走顶部类型按钮（那些才要计算）。产物条目双击照旧开 1D（另有用例）。
        """
        w = create_window()
        files = _tmp_files(2)
        try:
            w.add_files([str(p) for p in files], select=False)
            item = w.file_list.raw_group.child(0)
            with mock.patch.object(gui_plot_workers, "load_diffraction_image",
                                   return_value=np.ones((8, 8)) * 3.0):
                w.file_list.itemDoubleClicked.emit(item, 0)
                key2d = f"2D|{files[0]}"
                self.assertTrue(_wait_until(lambda: key2d in w.plot_docks),
                                "双击原始条目该打开它本身的 2D 图")
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                gui_file_dock._open_entry_view(w, item, explicit=True)
                key = f"1D|{files[0]}"
                self.assertTrue(_wait_until(
                    lambda: key in w.plot_docks
                    and len(_axes(w, "1D", str(files[0])).lines) > 0, 30000),
                    "右键 [打开 1D 图] 该开出这一张")
            self.assertEqual(len(w.plot_docks), 2, "双击开 2D、右键开 1D，各开各的")
        finally:
            w.close()

    def test_open_group_opens_every_entry(self):
        """右键 [打开整组 1D 图]：组里每条各开一张（小组不弹确认）。"""
        w = create_window()
        files = _tmp_files(3)
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                w.add_files([str(p) for p in files], select=False)
                group = w.file_list.raw_group
                gui_file_dock._open_group_views(w, group)
                self.assertTrue(_wait_until(
                    lambda: all(f"1D|{p}" in w.plot_docks for p in files),
                    60000), "组里三条都该开出来")
            self.assertIn("打开整组 1D 图完成：3 张", w.log_text.toPlainText())
        finally:
            w.close()

    def test_open_group_asks_before_opening_a_huge_group(self):
        """整组打开超过防爆线：先弹确认；选 No 就一张都不开（防 1.4 GB）。"""
        w = create_window()
        files = _tmp_files(gui_views.MAX_PANELS_PER_BATCH + 1)
        try:
            w.show()      # 确认框只在窗口显示时弹（无头/测试场景直接放行）
            w.add_files([str(p) for p in files], select=False)
            group = w.file_list.raw_group
            self.assertEqual(group.childCount(), len(files))
            with mock.patch.object(gui_file_actions, "_confirm_open_many",
                                   return_value=False):
                gui_file_dock._open_group_views(w, group)
            self.assertEqual(len(w.plot_docks), 0, "选取消就不该开图")
        finally:
            w.close()

    def test_multi_select_batch_plots_all(self):
        """多选 = 批量：勾两个文件点一次 1D → 两张图都出（旧模型
        会把慢的先算完的当过期丢弃）。"""
        w = create_window()
        try:
            a_done = threading.Event()

            def fake_ordered(path_str, geom, npt):
                # A 慢 0.2 s；B 等 A 完成再返回：完成顺序固定 A 先
                # B 后。不能靠"B 快所以 B 先完成"——线程启动时机在
                # 全套件负载下不可控（实测 B 的线程晚 0.4 s 才起，
                # 反而最后完成），焦点断言会飘
                if path_str.endswith("fake_a.tif"):
                    time.sleep(0.2)
                    a_done.set()
                elif path_str.endswith("fake_b.tif"):
                    a_done.wait(timeout=5)
                    # 再垫 0.2 s：a_done 在 A 返回之前就置位了，B
                    # 光等事件可能比 A 更早投递完成信号（跨线程排队
                    # 投递不保证顺序），垫一下让 B 确定最后完成
                    time.sleep(0.2)
                return np.array([0.5, 1.0, 8.5]), np.array([1.0, 2.0, 3.0])

            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=fake_ordered):
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                self.assertEqual(w.file_label.text(), "已勾选 2 项")
                _open_view(w, "1D")   # 批量：点一下，两个文件各开一张
                drawn_b = _wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
                drawn_a = _wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0)
                self.assertTrue(drawn_b, "B 的曲线应画出")
                self.assertTrue(drawn_a, "A 的曲线也应画出（各自的面板）")
            self.assertEqual(len(w.plot_docks), 2)
            dock_a = _dock(w, "1D", "data/fake_a.tif")
            dock_b = _dock(w, "1D", "data/fake_b.tif")
            self.assertEqual(dock_a.windowTitle(), "1D_fake_a.tif")
            self.assertEqual(dock_b.windowTitle(), "1D_fake_b.tif")
            self.assertFalse(dock_a.isHidden())
            self.assertFalse(dock_b.isHidden())
            # 各自的图挂各自的文件标题；没有结果被当过期丢弃；
            # 最后完成的图成为编辑对象（B 被安排等 A 完成再返回）
            self.assertTrue(
                _axes(w, "1D", "data/fake_a.tif").get_title()
                .startswith("fake_a.tif"))
            self.assertNotIn("已忽略", w.log_text.toPlainText())
            self.assertEqual(w.focus_panel, "1D|data/fake_b.tif")
        finally:
            w.close()

    def test_four_views_registered(self):
        """四个视图按钮全部接线：注册表齐套（旧版 2D 是占位面板）。

        热图只占 builder 表（多文件 → 一张面板，走 _plot_heatmap，
        不占 runner 表——它复用 1D 的 _spawn 后台积分）。
        """
        self.assertEqual(set(gui_views._VIEW_RUNNERS),
                         {"2D", "剖面", "1D", "瀑布"})
        self.assertEqual(set(gui_plot_panels._VIEW_BUILDERS),
                         {"2D", "剖面", "1D", "瀑布", "热图"})


class TestNewViews(unittest.TestCase):
    """2D / 剖面 / 瀑布 视图接线：各自后台计算 → 画进面板 →
    图像参数 [应用]（只重画，只有剖面角度变过才重算）。"""

    def test_2d_view_draws_image_and_caches(self):
        w = create_window()
        try:
            fake_image = np.arange(400, dtype=float).reshape(20, 20)
            with mock.patch.object(gui_plot_workers, "load_diffraction_image",
                                   return_value=fake_image):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "2D")
                drawn = _wait_until(lambda: len(gui_panel_state._content(
                    _dock(w, "2D", "data/fake_b.tif")).axes_2d.images) > 0)
                self.assertTrue(drawn, "点 2D 后应画出图像")
            dock = _dock(w, "2D", "data/fake_b.tif")
            self.assertEqual(dock.windowTitle(), "2D_fake_b.tif")
            ax = gui_panel_state._content(dock).axes_2d
            self.assertEqual(len(ax.images), 1)
            # 束心十字**默认关**（2026-10-09）；勾上后画在当前配置的
            # beam_center 上（(行, 列) → x=列, y=行）——即改即画，这里顺带钉
            self.assertEqual(len(ax.lines), 0, "默认不画束心")
            w.params["显示束心"].setChecked(True)
            QApplication.processEvents()
            cy, cx = w.config["beam_center"]
            self.assertAlmostEqual(ax.lines[0].get_xdata()[0], cx)
            self.assertAlmostEqual(ax.lines[0].get_ydata()[0], cy)
            self.assertIn("读取完成：fake_b.tif", w.log_text.toPlainText())
            self.assertEqual(w.focus_panel, "2D|data/fake_b.tif")
            # 图进了路径键缓存（自动对比度直接复用，不重复解码）
            self.assertIn("data/fake_b.tif", w._image_cache)
        finally:
            w.close()

    def test_profile_view_uses_snapshot_angle_and_recomputes_on_change(self):
        w = create_window()
        try:
            profile_calls = []

            def fake_profile(image, center, angle_deg=0.0):
                profile_calls.append((center, angle_deg))
                t = np.linspace(-100.0, 100.0, 21)
                return t, 10.0 * np.ones_like(t)

            with mock.patch.object(gui_plot_workers, "load_diffraction_image",
                                   return_value=np.zeros((10, 10))), \
                 mock.patch.object(gui_plot_workers, "line_profile",
                                   side_effect=fake_profile):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "剖面")
                drawn = _wait_until(lambda: len(gui_panel_state._content(
                    _dock(w, "剖面", "data/fake_b.tif")).axes_profile.lines) > 0)
                self.assertTrue(drawn, "点 剖面 后应画出曲线")
            dock = _dock(w, "剖面", "data/fake_b.tif")
            self.assertEqual(dock.windowTitle(), "剖面_fake_b.tif")
            # 首画：角度 = 面板快照默认 0，中心 = 配置 beam_center
            self.assertEqual(profile_calls[0][1], 0.0)
            self.assertEqual(profile_calls[0][0], tuple(w.config["beam_center"]))
            self.assertIn("剖面完成：fake_b.tif", w.log_text.toPlainText())
            # 改角度 → 即改即画（2026-10-08：[应用显示设置] 已删，焦点是
            # 剖面面板 → 按新角度重算，不是只重画）
            with mock.patch.object(gui_plot_workers, "load_diffraction_image",
                                   return_value=np.zeros((10, 10))), \
                 mock.patch.object(gui_plot_workers, "line_profile",
                                   side_effect=fake_profile):
                w.params["剖面角度 (°)"].setValue(45.0)
                recomputed = _wait_until(lambda: len(profile_calls) == 2)
                self.assertTrue(recomputed, "角度变了应重算剖面")
            self.assertEqual(profile_calls[1][1], 45.0)
            self.assertIn("开始计算剖面 fake_b.tif（后台运行，角度 45°）",
                          w.log_text.toPlainText())
            # 改一个**不用重算**的显示参数（显示束心）→ 只重画，不再算
            # （束心默认关着，这里勾上才真的触发一次改动）
            with mock.patch.object(gui_plot_workers, "load_diffraction_image",
                                   return_value=np.zeros((10, 10))), \
                 mock.patch.object(gui_plot_workers, "line_profile",
                                   side_effect=fake_profile):
                w.params["显示束心"].setChecked(True)
                QApplication.processEvents()
            self.assertEqual(len(profile_calls), 2, "束心开关不该重算剖面")
        finally:
            w.close()

    def test_waterfall_stacks_36_sectors(self):
        w = create_window()
        try:
            tth = np.linspace(1.0, 8.0, 30)
            i2d = np.ones((30, 36))
            chi = np.linspace(-175.0, 175.0, 36)   # 真引擎惯例：χ 从 -175° 起
            # 束心空箱的 0：真数据里 155/165/175 这些扇区的前 1~3 箱
            # 是空的（亚像素半径没有像素中心落进去，dummy=0）——开头的 0
            # 不是探测器边缘截断，不许把整条曲线截空（2026-10-12 用户：
            # "只有 33 条"，就是这三条被截没了）
            i2d[:2, 33] = 0.0

            def fake_sectors(image, **kw):
                self.assertEqual(kw["n_sectors"], 36)
                return tth, i2d, chi

            with mock.patch.object(gui_plot_workers, "load_diffraction_image",
                                   return_value=np.zeros((10, 10))), \
                 mock.patch.object(gui_plot_workers, "integrate_sectors",
                                   side_effect=fake_sectors):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "瀑布")
                drawn = _wait_until(lambda: len(gui_panel_state._content(
                    _dock(w, "瀑布", "data/fake_b.tif")).axes_waterfall.lines) > 0)
                self.assertTrue(drawn, "点 瀑布 后应画出堆叠曲线")
            dock = _dock(w, "瀑布", "data/fake_b.tif")
            ax = gui_panel_state._content(dock).axes_waterfall
            self.assertEqual(len(ax.lines), 36, "36 条扇区曲线")
            # 每一行都真有数据：k=33 开头两格是空箱 0，曲线应从第 3 个点
            # 起画（28 点）而不是整条被截空；其余各行 30 点
            self.assertEqual([len(ln.get_xdata()) for ln in ax.lines],
                             [30] * 33 + [28, 30, 30],
                             "开头的空箱 0 不截曲线（155° 行从第 3 点起画）")
            # χ 刻度 = 纵轴的刻度，默认就画（2026-10-12 用户拍板"甲"：
            # "瀑布图只能看一个文件的不同角度，而且现在不知道每条线代表
            # 什么"——那是一轴的读数不是"数据名字"，从此退出开关）；曲线
            # 名照旧 = 扇区名（悬停读数，与刻度无关）
            self.assertEqual(ax.get_yticklabels()[0].get_text(), "-175°",
                             "χ 刻度默认就画（第一条 -175°）")
            self.assertEqual(ax.get_yticklabels()[-1].get_text(), "175°",
                             "首尾锚定：最上一行永远有数字（抽稀只在中间）")
            self.assertEqual(ax.lines[0].get_label(), "-175°")
            w.params["显示数据名"].setChecked(True)   # 对比那把：与瀑布无关
            QApplication.processEvents()
            self.assertEqual(ax.get_yticklabels()[0].get_text(), "-175°",
                             "对比那把开着也不动瀑布刻度")
            w.params["显示数据名"].setChecked(False)
            self.assertIn("扇形积分完成：fake_b.tif（36 扇区 × 30 点）",
                          w.log_text.toPlainText())
        finally:
            w.close()

    def test_2d_contrast_changes_redraw_live(self):
        """2D 对比度参数：关自动 + 手填 → 即改即画，按手填值重画。

        2026-10-08 起不再有 [应用显示设置]（那个按钮与「未应用」灰字随
        显示参数全量即改即画一起删了）——改值本身就是触发。
        """
        w = create_window()
        try:
            fake_image = np.arange(400, dtype=float).reshape(20, 20)
            with mock.patch.object(gui_plot_workers, "load_diffraction_image",
                                   return_value=fake_image):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "2D")
                self.assertTrue(_wait_until(lambda: len(gui_panel_state._content(
                    _dock(w, "2D", "data/fake_b.tif")).axes_2d.images) > 0))
            ax = gui_panel_state._content(_dock(w, "2D", "data/fake_b.tif")).axes_2d
            self.assertGreater(ax.images[0].norm.vmin, 1.0, "自动 = 分位值")
            w.params["自动对比度"].setChecked(False)
            w.params["对比度下限"].setValue(5.0)
            w.params["对比度上限"].setValue(50.0)
            QApplication.processEvents()
            self.assertEqual(ax.images[0].norm.vmin, 5.0)
            self.assertEqual(ax.images[0].norm.vmax, 50.0)
            self.assertEqual(
                _dock(w, "2D", "data/fake_b.tif").params_snapshot["对比度下限"],
                5.0, "快照跟着控件走（即改即画）")
        finally:
            w.close()

    def test_beam_cross_is_off_by_default(self):
        """[显示束心]：2D 图上那个白色 + **默认不画**，勾上才出现（2026-10-09）。

        用户："实现默认 2D 图是不显示束心的"——默认先看一张干净的衍射图，
        要核对束心位置再勾上（2026-10-01 起就有这个开关，当时默认画：
        "二维图的圆心用户自己选择是否添加"）。只对原图页的 2D 图；校准图
        不参与（那张图上根本没有这个标记）。
        """
        w = create_window()
        try:
            fake_image = np.arange(400, dtype=float).reshape(20, 20)
            with mock.patch.object(gui_plot_workers, "load_diffraction_image",
                                   return_value=fake_image):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "2D")
                ax = gui_panel_state._content(
                    _dock(w, "2D", "data/fake_b.tif")).axes_2d
                # 等图出来（束心关着时 ax.lines 是空的，只能等 imshow 的图）
                self.assertTrue(_wait_until(lambda: len(ax.images) > 0))
            self.assertFalse(w.params["显示束心"].isChecked(), "默认不勾")
            self.assertEqual(len(ax.lines), 0, "默认不画十字")
            w.params["显示束心"].setChecked(True)    # 即改即画（2026-10-08）
            QApplication.processEvents()
            self.assertEqual(len(ax.lines), 1, "勾上后才叠束心十字")
            w.params["显示束心"].setChecked(False)
            QApplication.processEvents()
            self.assertEqual(len(ax.lines), 0, "取消后不留十字")
        finally:
            w.close()

    def test_waterfall_uniform_rows_and_processing_chain(self):
        """瀑布：行距统一（第二高的行峰 ×0.7）+ 处理链逐扇区跑（裁剪/平滑）。

        用户 2026-09-26："不要按照各自的最高峰归一化，所有的图" +
        "瀑布图也是在处理后画，去掉无效的峰就看得清了"。
        4 个扇区、共同基线 10：0 号有巨峰 100，1/2 号只有弱峰 11/12 ——
        旧规则下行高按各自的峰值算（弱扇区的行被压得只剩一点点）；
        2026-09-26 改成所有行同高（按全场 100）；2026-10-02 再把基准从
        "全场最大"换成"**第二高**的那一行"（用户："瀑布图……行间距小一点，
        让峰明显一点"）：0 号的巨峰不再决定行高，行距由 12 定（= 8.4，
        而不是 70）——弱扇区的特征因此看得见。真数据上的来龙去脉见
        services/stacking 的模块头。
        """
        w = create_window()
        try:
            tth = np.linspace(1.0, 8.0, 60)
            i2d = np.full((60, 4), 10.0)          # 基线（真实曲线不是 0）
            i2d[(tth >= 1.0) & (tth <= 2.0), 0] = 100.0   # 0 号：巨峰
            i2d[(tth >= 5.0) & (tth <= 5.2), 1] = 11.0    # 1 号：弱峰
            i2d[(tth >= 5.0) & (tth <= 5.2), 2] = 12.0    # 2 号：稍强
            chi = np.linspace(-175.0, 175.0, 4)
            i_mid = int(np.argmin(np.abs(tth - 4.0)))     # 两峰之间：全是基线
            with mock.patch.object(gui_plot_workers, "load_diffraction_image",
                                   return_value=np.zeros((10, 10))), \
                 mock.patch.object(gui_plot_workers, "integrate_sectors",
                                   return_value=(tth, i2d, chi)):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "瀑布")
                dock = _dock(w, "瀑布", "data/fake_b.tif")
                ax = gui_panel_state._content(dock).axes_waterfall
                self.assertTrue(_wait_until(lambda: len(ax.lines) > 0))

                def bases():
                    """各行的 y 基线（中段全是背景，值 = 偏移 + 10）"""
                    return [float(np.asarray(l.get_ydata())[i_mid])
                            for l in ax.lines]

                # 行距 = 第二高的行峰（扇区 2 的 12）× 0.7——**不是**全场峰值
                # 100（扇区 0 的巨峰）——那正是 2026-10-02 改掉的东西：
                # 一个特别强的扇区不该把其余行压成平线
                step = 12.0 * 0.7
                for k, b in enumerate(bases()):
                    self.assertAlmostEqual(b - 10.0, k * step,
                                           delta=step * 0.05)
                # 裁掉巨峰所在区间——走真实路径：填起止 + [添加]
                # （清单是窗口级的，[添加] 会把它拷进各面板快照并实时重画）
                w.params["裁剪起点 (°)"].setValue(0.9)
                w.params["裁剪终点 (°)"].setValue(2.1)
                w.findChild(QPushButton, "cut_add_btn").click()
                QApplication.processEvents()
            # 裁掉巨峰：它那一行也降回基线 10 → 第二高变成扇区 1 的 11
            step2 = 11.0 * 0.7
            self.assertLess(step2, step, "巨峰被裁掉，行距跟着降一点")
            for k, b in enumerate(bases()):
                self.assertAlmostEqual(b - 10.0, k * step2,
                                       delta=step2 * 0.05)
            # 裁掉的区间在所有行上都空着（逐扇区跑了裁剪）
            for line in ax.lines:
                y = np.asarray(line.get_ydata())
                seg = y[(tth >= 1.0) & (tth <= 2.0)]
                self.assertTrue(np.isnan(seg).all(), "裁剪区间应被挖空")
        finally:
            w.close()

    def test_waterfall_manual_row_step_multiplies_the_offset(self):
        """瀑布 [手动行距]：勾上后行距 = 自动行距 × 倍数（用户 2026-10-05：
        "对比的堆叠添加让用户自己改 y off 还有瀑布图也是"）。

        纯显示参数：改了立刻重画（同对比那对即改即画），不勾 = 自动
        口径（第二高的行峰 × 0.7）不动；只重画、不重新扇形积分。
        """
        w = create_window()
        try:
            tth = np.linspace(1.0, 8.0, 60)
            i2d = np.full((60, 4), 10.0)
            i2d[(tth >= 1.0) & (tth <= 2.0), 0] = 100.0   # 巨峰（不该定行高）
            i2d[(tth >= 5.0) & (tth <= 5.2), 2] = 12.0    # 第二高 = 12
            chi = np.linspace(-175.0, 175.0, 4)
            i_mid = int(np.argmin(np.abs(tth - 4.0)))     # 两峰之间：全是基线
            with mock.patch.object(gui_plot_workers, "load_diffraction_image",
                                   return_value=np.zeros((10, 10))), \
                 mock.patch.object(gui_plot_workers, "integrate_sectors",
                                   return_value=(tth, i2d, chi)) as fake_sectors:
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "瀑布")
                dock = _dock(w, "瀑布", "data/fake_b.tif")
                ax = gui_panel_state._content(dock).axes_waterfall
                self.assertTrue(_wait_until(lambda: len(ax.lines) > 0))

                def row1_offset():
                    """1 号扇区行的基线（中段 = 基线 10 + 偏移）"""
                    return float(np.asarray(ax.lines[1].get_ydata())[i_mid]) - 10.0

                step_auto = 12.0 * 0.7
                self.assertAlmostEqual(row1_offset(), step_auto, places=5)
                self.assertFalse(w.params["瀑布行距倍数"].isEnabled(),
                                 "自动行距下倍数框置灰")
                w.params["瀑布手动行距"].setChecked(True)    # 即改即画
                self.assertTrue(w.params["瀑布行距倍数"].isEnabled())
                w.params["瀑布行距倍数"].setValue(2.0)
                QApplication.processEvents()
                self.assertAlmostEqual(row1_offset(), step_auto * 2.0,
                                       places=5, msg="行距该按倍数放大")
                self.assertEqual(fake_sectors.call_count, 1,
                                 "改行距只重画，不重新扇形积分")
                w.params["瀑布手动行距"].setChecked(False)   # 回到自动口径
                QApplication.processEvents()
                self.assertAlmostEqual(row1_offset(), step_auto, places=5)
        finally:
            w.close()

    def test_waterfall_row_step_rescales_the_zoomed_y_window(self):
        """先缩放、后调行距：钉住的 y 窗口跟着行距一起换算（同一批行留下）。

        用户 2026-10-13："瀑布图在先缩放后调整行距时会 yoff 失效"——偏移
        每次都按新行距重算（那一环没毛病），坏的是缩放窗口按旧行距的坐标
        数值原样套回，视野里换成了另一段角度的行；修后窗口 × 倍数、并写回
        快照，Home 也记得新行距下的天然量程（不是缩放窗口）。
        """
        w = create_window()
        try:
            tth = np.linspace(1.0, 8.0, 60)
            i2d = np.full((60, 4), 10.0)
            i2d[(tth >= 1.0) & (tth <= 2.0), 0] = 100.0
            i2d[(tth >= 5.0) & (tth <= 5.2), 2] = 12.0
            chi = np.linspace(-175.0, 175.0, 4)
            with mock.patch.object(gui_plot_workers, "load_diffraction_image",
                                   return_value=np.zeros((10, 10))), \
                 mock.patch.object(gui_plot_workers, "integrate_sectors",
                                   return_value=(tth, i2d, chi)):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "瀑布")
                dock = _dock(w, "瀑布", "data/fake_b.tif")
                content = gui_panel_state._content(dock)
                ax = content.axes_waterfall
                self.assertTrue(_wait_until(lambda: len(ax.lines) > 0))
                step_auto = 12.0 * 0.7
                # 模拟手势缩放：只框住第 0.8~3.2 行的窗口（走真实写回槽）
                lo, hi = 0.8 * step_auto, 3.2 * step_auto
                ax.set_ylim(lo, hi)
                QApplication.processEvents()
                snap = dock.params_snapshot
                self.assertAlmostEqual(snap["视图 y 范围"][0], lo, places=6)
                # 调倍数 ×2 → 窗口换算 = 旧窗口 ×2（同一批行仍留在视野）
                w.params["瀑布手动行距"].setChecked(True)
                w.params["瀑布行距倍数"].setValue(2.0)
                QApplication.processEvents()
                ylo, yhi = ax.get_ylim()
                self.assertAlmostEqual(ylo, lo * 2.0, places=5,
                                       msg="缩放窗口该跟着行距换算")
                self.assertAlmostEqual(yhi, hi * 2.0, places=5)
                self.assertAlmostEqual(snap["视图 y 范围"][0], lo * 2.0,
                                       places=5, msg="换算结果要写回快照")
                # Home：回新行距下的天然量程（最上一行峰顶可见），
                # 而不是停在缩放窗口
                content.toolbar._actions["home"].trigger()
                QApplication.processEvents()
                hylo, hyhi = ax.get_ylim()
                top_row = 10.0 + 3.0 * step_auto * 2.0
                self.assertGreaterEqual(hyhi, top_row * 0.95,
                                        "Home 该看到整个瀑布（含最上一行）")
                self.assertLess(hylo, lo * 2.0, "Home 不该停在缩放窗口")
                # Home 后快照写回的 ≈ 家窗口（天然量程）：再调行距时该
                # **丢弃**、让新行距自己量程——不是把天然窗口按比例放大
                self.assertIn("视图 y 范围", snap,
                              "Home 后快照该写回视图窗口（与 x 同待遇）")
                w.params["瀑布行距倍数"].setValue(3.0)
                QApplication.processEvents()
                self.assertNotIn("视图 y 范围", snap,
                                 "≈ 家的窗口该丢弃、交给自动量程")
                top_row3 = 10.0 + 3.0 * step_auto * 3.0
                self.assertGreaterEqual(ax.get_ylim()[1], top_row3 * 0.95,
                                        "×3 行距下整条瀑布都在视野里")
        finally:
            w.close()

    def test_waterfall_display_change_redraws_without_recompute(self):
        """瀑布面板改显示参数：只重画已有结果，不重新扇形积分。"""
        w = create_window()
        try:
            tth = np.linspace(1.0, 8.0, 10)
            i2d = np.ones((10, 36))
            chi = np.linspace(5.0, 355.0, 36)
            with mock.patch.object(gui_plot_workers, "load_diffraction_image",
                                   return_value=np.zeros((10, 10))), \
                 mock.patch.object(
                    gui_plot_workers, "integrate_sectors",
                    return_value=(tth, i2d, chi)) as fake_sectors:
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "瀑布")
                self.assertTrue(_wait_until(lambda: len(gui_panel_state._content(
                    _dock(w, "瀑布", "data/fake_b.tif")).axes_waterfall.lines) > 0))
                # 改显示参数（手动行距）→ 即改即画，不重新积分
                w.params["瀑布手动行距"].setChecked(True)
                w.params["瀑布行距倍数"].setValue(1.5)
                QApplication.processEvents()
                self.assertEqual(fake_sectors.call_count, 1,
                                 "显示参数改动不应重新积分")
        finally:
            w.close()


class TestApplyAndFocus(unittest.TestCase):
    """[应用] 重算焦点面板；点图面板切焦点。"""

    def test_apply_reruns_focused_view(self):
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute) as fake:
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(lambda: fake.call_count >= 1))
                self.assertTrue(_wait_until(
                    lambda: w.focus_panel == "1D|data/fake_b.tif"))
                w.findChild(QPushButton, "apply_btn").click()
                self.assertTrue(_wait_until(lambda: fake.call_count >= 2))
                self.assertIn("[应用] 重算编辑对象：1D_fake_b.tif",
                              w.log_text.toPlainText())
        finally:
            w.close()

    def test_apply_without_focus_hints(self):
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute) as fake:
                add_checked(w, ["data/fake_b.tif"])
                w.findChild(QPushButton, "apply_btn").click()
                self.assertEqual(fake.call_count, 0)
                self.assertIn("先点击要更新的图面板",
                              w.log_text.toPlainText())
        finally:
            w.close()

    def test_clicking_placeholder_focuses_view(self):
        w = create_window()
        try:
            add_checked(w, ["data/fake_b.tif"])
            _open_view(w, "2D")
            self.assertIsNone(w.focus_panel)
            # 模拟点击面板内容 → 事件过滤器切焦点（焦点=具体面板）
            QTest.mouseClick(gui_panel_state._content(_dock(w, "2D", "data/fake_b.tif")),
                             Qt.LeftButton)
            self.assertEqual(w.focus_panel, "2D|data/fake_b.tif")
            self.assertIn("2D_fake_b.tif", w.focus_label.text())
        finally:
            w.close()

    def test_same_panel_latest_task_wins(self):
        """同面板连点两次：先开的慢任务晚到 → 丢弃，不得覆盖新图。"""
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute) as fake:
                add_checked(w, ["data/fake_a.tif"])   # A 慢 0.2 s
                _open_view(w, "1D")   # 任务 1（慢）
                _open_view(w, "1D")   # 任务 2（即时）= 最新任务
                drawn = _wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) == 1)
                ignored = _wait_until(
                    lambda: "已忽略 fake_a.tif 的过期结果"
                    in w.log_text.toPlainText())
                self.assertTrue(drawn, "最新任务的结果应画出")
                self.assertTrue(ignored, "旧任务的结果应被丢弃")
            self.assertEqual(fake.call_count, 2)
            # 旧结果不得覆盖：曲线仍只有一条
            self.assertEqual(len(_axes(w, "1D", "data/fake_a.tif").lines), 1)
        finally:
            w.close()


class TestFileCheckSelection(unittest.TestCase):
    """文件列表：对号是唯一的选择表达（点行 = 加选不取消；取消对号
    只能点对号方块；背景高亮跟随对号集合）。

    导入默认**不勾选**（用户 2026-09-25 定）——勾选出 [全选] /
    [按条件选…] / 点方块三条路；select=True 保留老行为（脚本/测试用）。"""

    def test_add_files_unchecked_by_default(self):
        """导入（选入/拖入/文件夹扫描）默认一个都不勾。"""
        w = create_window()
        try:
            w.add_files(["data/fake_a.tif", "data/fake_b.tif"])
            self.assertEqual(w.file_list.item(0).checkState(), Qt.Unchecked)
            self.assertEqual(w.file_list.item(1).checkState(), Qt.Unchecked)
            self.assertIsNone(w.file_list.currentItem())   # 没对号就不高亮
            self.assertEqual(w.file_label.text(), "未打开任何文件")
            self.assertIn("已添加 2 个文件", w.log_text.toPlainText())
            self.assertIn("未勾选", w.log_text.toPlainText())
        finally:
            w.close()

    def test_add_files_select_true_checks_all(self):
        """select=True（脚本/测试用）仍是一批全勾 + 高亮最后一条。"""
        w = create_window()
        try:
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
            self.assertEqual(w.file_list.item(0).checkState(), Qt.Checked)
            self.assertEqual(w.file_list.item(1).checkState(), Qt.Checked)
            self.assertIs(w.file_list.currentItem(), w.file_list.item(1))
            self.assertEqual(w.file_label.text(), "已勾选 2 项")
        finally:
            w.close()

    def test_row_click_adds_check_keeps_others(self):
        """点行 = 加选：勾上这一行，其他对号不动。"""
        w = create_window()
        try:
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
            # 先点 A 的方块取消 A → 只剩 B 勾着
            item_a = w.file_list.item(0)
            w._press_item, w._press_state = item_a, Qt.Checked
            item_a.setCheckState(Qt.Unchecked)
            w.file_list.itemClicked.emit(item_a, 0)
            self.assertEqual(item_a.checkState(), Qt.Unchecked)
            # 再点 A 行体 → 勾回 A，B 的对号不动（Qt 原生：按下时
            # 事件过滤器记录 A 为未勾、并把当前项移到 A；手动补上）
            w._press_item, w._press_state = item_a, Qt.Unchecked
            w.file_list.setCurrentItem(item_a)
            w.file_list.itemClicked.emit(item_a, 0)
            self.assertEqual(item_a.checkState(), Qt.Checked)
            self.assertEqual(w.file_list.item(1).checkState(), Qt.Checked)
            self.assertIs(w.file_list.currentItem(), item_a)
            self.assertEqual(w.file_label.text(), "已勾选 2 项")
        finally:
            w.close()

    def test_row_click_checked_item_is_noop(self):
        """已勾的行再点 = 没反应（点行不会取消对号）。"""
        w = create_window()
        try:
            add_checked(w, ["data/fake_b.tif"])
            w.file_list.itemClicked.emit(w.file_list.item(0), 0)   # 点它
            self.assertEqual(w.file_list.item(0).checkState(), Qt.Checked)
            self.assertEqual(w.file_label.text(), "fake_b.tif")
        finally:
            w.close()

    def test_square_click_auto_toggle_honored(self):
        """点对号方块：Qt 已自动切换（按下时记着旧状态）→ 取消对号
        生效——这是取消对号的唯一途径。"""
        w = create_window()
        try:
            add_checked(w, ["data/fake_b.tif"])
            item = w.file_list.item(0)
            w._press_item, w._press_state = item, Qt.Checked   # 按下时勾着
            item.setCheckState(Qt.Unchecked)   # Qt 在弹起时自动取消
            w.file_list.itemClicked.emit(item, 0)
            self.assertEqual(item.checkState(), Qt.Unchecked)
            self.assertEqual(w.file_label.text(), "未打开任何文件")
        finally:
            w.close()

    def test_plot_uses_checked_file(self):
        """以对号为准：取消 B 的对号（点方块），作图只用对号文件 A。"""
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                # 点 B 的对号方块取消 → 只剩 A 勾着
                item_b = w.file_list.item(1)
                w._press_item, w._press_state = item_b, Qt.Checked
                item_b.setCheckState(Qt.Unchecked)
                w.file_list.itemClicked.emit(item_b, 0)
                self.assertEqual(w.file_list.item(0).checkState(), Qt.Checked)
                self.assertEqual(w.file_list.item(1).checkState(),
                                 Qt.Unchecked)
                _open_view(w, "1D")
                drawn = _wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0)
                self.assertTrue(drawn, "应画对号文件 A 的图")
            self.assertEqual(len(w.plot_docks), 1)
        finally:
            w.close()

    def test_delete_removes_all_checked(self):
        """删除 = 批量：所有对号文件一起移除。"""
        w = create_window()
        try:
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
            w.findChild(QPushButton, "delete_btn").click()
            self.assertEqual(w.file_list.count(), 0)
            self.assertEqual(w.file_label.text(), "未打开任何文件")
            self.assertIn("已从文件栏移除 2 个文件", w.log_text.toPlainText())
        finally:
            w.close()

    # ── 选择工具（全选 / 全不选 / 按条件选）──
    @staticmethod
    def _add_many(w, n=9):
        """加 n 个文件（名字 s1.tif…s9.tif），**不勾**（导入默认）。"""
        w.add_files([f"data/s{i + 1}.tif" for i in range(n)])

    def test_select_all_and_none_buttons(self):
        """全选/全不选是**一个**切换按钮：按第一次全勾、再按全取消。

        用户 2026-09-27："全选全部不选合一"——标签跟着状态走（全勾上时
        显示"全不选"），按下去做哪件事由状态决定。

        2026-09-28 第 3 条之后，标签把**作用范围**也写出来了：两个方向的
        范围本来就不一样（勾只勾「原始数据」、清清全部条目，见下一条测试）
        ——不写出来，按下去发生什么全靠猜。
        """
        w = create_window()
        try:
            w.add_files([f"data/s{i + 1}.tif" for i in range(5)])
            btn = w.findChild(QPushButton, "select_all_btn")
            self.assertEqual(btn.text(), "全选（原始数据）",
                             "初始（没勾）该显示 全选")
            btn.click()
            self.assertTrue(all(w.file_list.item(i).checkState() == Qt.Checked
                                for i in range(5)))
            self.assertEqual(btn.text(), "全不选（全部条目）",
                             "全勾上后标签该翻过来")
            self.assertEqual(w.file_label.text(), "已勾选 5 项")
            self.assertIn("全选：勾上「原始数据」整组 5 个文件",
                          w.log_text.toPlainText())
            btn.click()                     # 同一个按钮：这次是"全不选"
            self.assertTrue(all(w.file_list.item(i).checkState() == Qt.Unchecked
                                for i in range(5)))
            self.assertEqual(btn.text(), "全选（原始数据）")
            self.assertEqual(w.file_label.text(), "未打开任何文件")
            self.assertIn("全不选：清掉全部 5 项的对号",
                          w.log_text.toPlainText())
        finally:
            w.close()

    def test_select_all_leaves_products_alone_but_none_clears_them(self):
        """「全选」只勾原始数据；「全不选」清掉全树的勾——这不对称是**写明的规矩**。

        用户 2026-09-28 第 3 条报的就是这个不对称（"全选只全选原始数据，
        全不选会包含产物"）。定下来的口径（2026-09-28）：勾**只勾「原始数据」
        整组**（产物分组自己点组名勾——勾选集是出图 / 批量处理的输入，顺手
        把 81 个产物也勾上会让输入集合悄悄翻倍，用户 2026-09-27 踩过"选中 81
        出 162"）；清**清掉全树**（清是"归零"，归零就该彻底）。两者都写在
        按钮标签上。
        """
        w = create_window()
        try:
            files = _tmp_files(2)
            w.add_files([str(p) for p in files])
            for f in files:
                _store_product(w, f)
            w.refresh_groups()
            QApplication.processEvents()
            raws = [s for s in gui_sources.all_sources(w)
                    if s.kind == gui_sources.RAW]
            prod = [s for s in gui_sources.all_sources(w)
                    if s.kind == gui_sources.ONED]
            self.assertEqual((len(raws), len(prod)), (2, 2),
                             "前提：2 条原始 + 2 条 1D 产物")
            w.select_all_btn.click()
            QApplication.processEvents()
            self.assertTrue(all(s.item.checkState(0) == Qt.Checked
                                for s in raws), "全选：原始数据整组勾上")
            self.assertTrue(all(s.item.checkState(0) == Qt.Unchecked
                                for s in prod), "全选不该顺手勾上产物")
            w.select_all_btn.click()        # 现在它是"全不选（全部条目）"
            QApplication.processEvents()
            self.assertTrue(all(s.item.checkState(0) == Qt.Unchecked
                                for s in raws + prod),
                            "全不选：连产物一起清（归零就该彻底）")
        finally:
            w.close()

    def test_select_all_on_empty_list_logs(self):
        w = create_window()
        try:
            w.findChild(QPushButton, "select_all_btn").click()
            self.assertIn("文件栏是空的", w.log_text.toPlainText())
        finally:
            w.close()

    def test_select_by_range_replaces_selection(self):
        """区间：第 3 到第 6 个 → 只有这 4 个勾上（默认"只选这些"）。"""
        w = create_window()
        try:
            self._add_many(w)
            gui_file_dock.apply_selection(
                w, {"mode": "range", "start": 3, "stop": 6, "text": "",
                    "append": False})
            got = [i + 1 for i in range(9)
                   if w.file_list.item(i).checkState() == Qt.Checked]
            self.assertEqual(got, [3, 4, 5, 6])
            self.assertEqual(w.file_label.text(), "已勾选 4 项")
            self.assertIn("按条件勾选 4 项", w.log_text.toPlainText())
        finally:
            w.close()

    def test_select_by_stride(self):
        """间隔：从第 2 个起每 3 个选 1 个 → 第 2、5、8 个。"""
        w = create_window()
        try:
            self._add_many(w)
            gui_file_dock.apply_selection(
                w, {"mode": "stride", "every": 3, "offset": 2, "text": "",
                    "append": False})
            got = [i + 1 for i in range(9)
                   if w.file_list.item(i).checkState() == Qt.Checked]
            self.assertEqual(got, [2, 5, 8])
        finally:
            w.close()

    def test_select_by_name_filter_stacks_with_range(self):
        """名字包含与区间叠加（都命中才选）：第 1–6 个里名字含 "s1" 的。"""
        w = create_window()
        try:
            self._add_many(w)
            gui_file_dock.apply_selection(
                w, {"mode": "range", "start": 1, "stop": 6, "text": "s1",
                    "append": False})
            got = [i + 1 for i in range(9)
                   if w.file_list.item(i).checkState() == Qt.Checked]
            self.assertEqual(got, [1])   # s1.tif（s10 不存在）
            self.assertIn("名称包含“s1”", w.log_text.toPlainText())
        finally:
            w.close()

    def test_select_append_keeps_existing_checks(self):
        """追加：跨两个区间攒一批（前面的对号不被取消）。"""
        w = create_window()
        try:
            self._add_many(w)
            gui_file_dock.apply_selection(
                w, {"mode": "range", "start": 1, "stop": 2, "text": "",
                    "append": False})
            gui_file_dock.apply_selection(
                w, {"mode": "range", "start": 8, "stop": 9, "text": "",
                    "append": True})
            got = [i + 1 for i in range(9)
                   if w.file_list.item(i).checkState() == Qt.Checked]
            self.assertEqual(got, [1, 2, 8, 9])
        finally:
            w.close()

    def test_select_without_match_logs_and_clears(self):
        """没有命中：记一行日志、对号清空（不静默什么都不做）。"""
        w = create_window()
        try:
            self._add_many(w)
            gui_file_dock.apply_selection(
                w, {"mode": "range", "start": 1, "stop": 2, "text": "",
                    "append": False})
            gui_file_dock.apply_selection(
                w, {"mode": "range", "start": 1, "stop": 3, "text": "没有这个词",
                    "append": False})
            self.assertEqual(w.file_label.text(), "未打开任何文件")
            self.assertIn("没有命中任何文件", w.log_text.toPlainText())
        finally:
            w.close()

    def test_select_dialog_spec_and_preview(self):
        """弹窗：勾"间隔" + 填名字 → 预览行实时更新，确定返回 spec。

        模态 exec 用 patch 兜住（跟导出弹窗的测法同一路）：side_effect
        拿到的是弹窗实例，按 objectName 找到控件驱动它。
        """
        w = create_window()
        try:
            self._add_many(w)
            w.show()
            seen = {}

            def fake_exec(self):    # new= 打补丁 → 描述符协议 → self = 弹窗
                seen["preview0"] = self.findChild(
                    QLabel, "sel_preview").text()
                self.findChild(QRadioButton, "sel_stride").setChecked(True)
                self.findChild(QSpinBox, "sel_every").setValue(2)
                self.findChild(QSpinBox, "sel_offset").setValue(1)
                self.findChild(QLineEdit, "sel_text").setText("s")
                seen["preview1"] = self.findChild(
                    QLabel, "sel_preview").text()
                return QDialog.Accepted

            with mock.patch.object(QDialog, "exec", new=fake_exec):
                spec = gui_file_dock._selection_dialog_spec(w, 9)
            self.assertEqual(spec["mode"], "stride")
            self.assertEqual((spec["every"], spec["offset"]), (2, 1))
            self.assertEqual(spec["text"], "s")
            self.assertIn("预览：将勾选 9 项（共 9 项）", seen["preview0"])
            self.assertIn("预览：将勾选 5 项", seen["preview1"])   # 1,3,5,7,9
        finally:
            w.close()

    def test_select_dialog_hidden_window_returns_none(self):
        """窗口没显示（测试/无头环境）不弹模态框，直接返回 None。"""
        w = create_window()
        try:
            add_checked(w, ["data/fake_a.tif"])
            self.assertIsNone(gui_file_dock._selection_dialog_spec(w, 1))
        finally:
            w.close()


class TestSaveFigures(unittest.TestCase):
    """[保存]：弹窗勾选要保存的图 → 逐个选文件名存 PNG（主动操作）。"""

    def test_save_with_no_figures_logs(self):
        w = create_window()
        try:
            add_checked(w, ["data/fake_b.tif"])   # 只选中没出图
            w.findChild(QPushButton, "save_btn").click()
            self.assertIn("没有可保存的图", w.log_text.toPlainText())
        finally:
            w.close()

    def test_save_flow_saves_chosen_panel(self):
        w = create_window()
        try:
            dock = _draw_one_1d(w)
            fig = gui_panel_state._content(dock).figure
            with mock.patch.object(gui_export, "_choose_panels",
                                   return_value=[dock]), \
                 mock.patch.object(gui_export, "_ask_save_options",
                                   return_value={"dpi": 300, "fmt": "png"}), \
                 mock.patch.object(QFileDialog, "getSaveFileName",
                                   return_value=("/tmp/out", "PNG 图片 (*.png)")) as dlg, \
                 mock.patch.object(fig, "savefig") as savefig:
                w.findChild(QPushButton, "save_btn").click()
                self.assertTrue(dlg.called)
                savefig.assert_called_once_with("/tmp/out.png", dpi=300)   # 自动补 .png + 300 dpi
                self.assertTrue(dock.figure_saved)
                self.assertIn("已保存 1D_fake_b.tif → /tmp/out.png",
                              w.log_text.toPlainText())
        finally:
            w.close()

    def test_cancel_choice_saves_nothing(self):
        w = create_window()
        try:
            _draw_one_1d(w)
            with mock.patch.object(gui_export, "_choose_panels",
                                   return_value=None), \
                 mock.patch.object(QFileDialog, "getSaveFileName") as dlg:
                self.assertFalse(gui_export._save_figures(w))
                self.assertFalse(dlg.called)
                self.assertIn("已取消保存", w.log_text.toPlainText())
        finally:
            w.close()

    def test_ok_with_none_checked_is_not_cancel(self):
        """确定但一张都没勾 → 提示"没有勾选"，不是"这次不加"（修前混为一谈）。"""
        w = create_window()
        try:
            _draw_one_1d(w)
            with mock.patch.object(gui_export, "_choose_panels",
                                   return_value=[]), \
                 mock.patch.object(QFileDialog, "getSaveFileName") as dlg:
                self.assertFalse(gui_export._save_figures(w))
                self.assertFalse(dlg.called)
                self.assertIn("未勾选任何要保存的图",
                              w.log_text.toPlainText())
        finally:
            w.close()

    def test_skip_one_figure_returns_false(self):
        """逐个存盘时跳过一张 → 返回 False（关窗流程应留在程序里，
        不能当成"已全部保存"静默关掉）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines)
                    > 0 and len(_axes(w, "1D", "data/fake_b.tif").lines)
                    > 0))
            d1 = _dock(w, "1D", "data/fake_a.tif")
            d2 = _dock(w, "1D", "data/fake_b.tif")
            with mock.patch.object(gui_export, "_choose_panels",
                                   return_value=[d1, d2]), \
                 mock.patch.object(gui_export, "_ask_save_options",
                                   return_value={"dpi": 300, "fmt": "png"}), \
                 mock.patch.object(QFileDialog, "getSaveFileName",
                                   side_effect=[("/tmp/a", ""),
                                                ("", "")]) as dlg, \
                 mock.patch.object(gui_panel_state._content(d1).figure, "savefig"):
                self.assertFalse(gui_export._save_figures(w))
            self.assertEqual(dlg.call_count, 2)
            log = w.log_text.toPlainText()
            self.assertIn("已跳过保存 1D_fake_b.tif", log)
            self.assertIn("保存完成：1 张图", log)
        finally:
            w.close()

    def test_savefig_failure_logs_and_returns_false(self):
        """目标路径写不进去（如目录不存在）→ 报错日志 + 返回 False，
        不崩、不装成保存成功。"""
        w = create_window()
        try:
            dock = _draw_one_1d(w)
            with mock.patch.object(gui_export, "_choose_panels",
                                   return_value=[dock]), \
                 mock.patch.object(gui_export, "_ask_save_options",
                                   return_value={"dpi": 300, "fmt": "png"}), \
                 mock.patch.object(QFileDialog, "getSaveFileName",
                                   return_value=("/no/such/dir/out.png", "")), \
                 mock.patch.object(gui_panel_state._content(dock).figure, "savefig",
                                   side_effect=OSError("磁盘写不进")):
                self.assertFalse(gui_export._save_figures(w))
            log = w.log_text.toPlainText()
            self.assertIn("保存失败 1D_fake_b.tif", log)
        finally:
            w.close()


class TestSaveOptions(unittest.TestCase):
    """保存选项弹窗：分辨率 dpi + 格式（PNG/TIF）贯穿单张与批量保存。"""

    def test_dialog_defaults(self):
        """默认 300 dpi + PNG（论文/报告印刷的常用起步值）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_plot_export.QDialog, "exec",
                                   return_value=QDialog.Accepted):
                self.assertEqual(gui_plot_export._ask_save_options(w),
                                 {"dpi": 300, "fmt": "png"})
        finally:
            w.close()

    def test_dialog_custom_values(self):
        """改 600 dpi + TIF → 返回对应值（fmt 同时当扩展名用）。"""
        w = create_window()
        try:
            def fake_exec(dlg):
                dlg.findChild(QSpinBox, "save_dpi_spin").setValue(600)
                dlg.findChild(QComboBox, "save_fmt_combo").setCurrentIndex(1)
                return QDialog.Accepted
            # new= 放普通函数：函数是描述符，实例访问自动绑定 dlg；
            # return_value 的 MagicMock 不绑定（Shiboken 方法也不吃 autospec）
            with mock.patch.object(gui_plot_export.QDialog, "exec", new=fake_exec):
                self.assertEqual(gui_plot_export._ask_save_options(w),
                                 {"dpi": 600, "fmt": "tif"})
        finally:
            w.close()

    def test_dialog_cancel_returns_none(self):
        w = create_window()
        try:
            with mock.patch.object(gui_plot_export.QDialog, "exec",
                                   return_value=QDialog.Rejected):
                self.assertIsNone(gui_plot_export._ask_save_options(w))
        finally:
            w.close()

    def test_batch_cancel_options_aborts_save(self):
        """批量保存：选项弹窗取消 → 不弹文件名框、返回 False（关窗留在程序里）。"""
        w = create_window()
        try:
            dock = _draw_one_1d(w)
            with mock.patch.object(gui_export, "_choose_panels",
                                   return_value=[dock]), \
                 mock.patch.object(gui_export, "_ask_save_options",
                                   return_value=None), \
                 mock.patch.object(QFileDialog, "getSaveFileName") as dlg:
                self.assertFalse(gui_export._save_figures(w))
                self.assertFalse(dlg.called)
                self.assertIn("已取消保存", w.log_text.toPlainText())
        finally:
            w.close()

    def test_batch_tif_saves_with_extension_and_dpi(self):
        """批量保存 TIF：文件名过滤器带 TIF、自动补 .tif + savefig 收到 dpi。"""
        w = create_window()
        try:
            dock = _draw_one_1d(w)
            fig = gui_panel_state._content(dock).figure
            with mock.patch.object(gui_export, "_choose_panels",
                                   return_value=[dock]), \
                 mock.patch.object(gui_export, "_ask_save_options",
                                   return_value={"dpi": 600, "fmt": "tif"}), \
                 mock.patch.object(QFileDialog, "getSaveFileName",
                                   return_value=("/tmp/b", "TIF 图片 (*.tif)")) as dlg, \
                 mock.patch.object(fig, "savefig") as savefig:
                self.assertTrue(gui_export._save_figures(w))
                self.assertIn("TIF", dlg.call_args[0][3])
                savefig.assert_called_once_with("/tmp/b.tif", dpi=600)
        finally:
            w.close()

    def test_panel_save_cancel_options_keeps_unsaved(self):
        """单面板工具栏 [Save]：选项弹窗取消 → 不弹文件名框、记账不动。

        patch 目标 = 调用点所在模块 plot_panels（_save_panel 在那里做
        全局名查找）：打 plot_export 里的同名函数拦不住这条路径，真弹窗
        会让 offscreen 套件卡死在 exec() 上（实测）。
        """
        w = create_window()
        try:
            dock = _draw_one_1d(w)
            with mock.patch.object(gui_plot_panel_build, "_ask_save_options",
                                   return_value=None), \
                 mock.patch.object(QFileDialog, "getSaveFileName") as dlg:
                gui_panel_state._content(dock).toolbar.save_figure()
                self.assertFalse(dlg.called)
                self.assertFalse(dock.figure_saved)
        finally:
            w.close()

    def test_panel_save_tif_dpi(self):
        """单面板保存 TIF 600 dpi：自动补 .tif + savefig 收到 dpi。"""
        w = create_window()
        try:
            dock = _draw_one_1d(w)
            fig = gui_panel_state._content(dock).figure
            with mock.patch.object(gui_plot_panel_build, "_ask_save_options",
                                   return_value={"dpi": 600, "fmt": "tif"}), \
                 mock.patch.object(QFileDialog, "getSaveFileName",
                                   return_value=("/tmp/panel_tif", "TIF 图片 (*.tif)")) as dlg, \
                 mock.patch.object(fig, "savefig") as savefig:
                gui_panel_state._content(dock).toolbar.save_figure()
                self.assertIn("TIF", dlg.call_args[0][3])
                savefig.assert_called_once_with("/tmp/panel_tif.tif", dpi=600)
                self.assertTrue(dock.figure_saved)
        finally:
            w.close()


class TestCurveColors(unittest.TestCase):
    """色彩（2026-10-13 改版）：单色渐变配色参数 +「外观」渐变色系。

    用户当天："对比图的颜色改为一种颜色的渐变过程，用户在 customize
    里面可以自行选择……只留渐变顺序，颜色这种选项"（原话里对旧逐条
    改色的评价："不是每个数据都能改颜色，非常无效"）——原先的 8 槽
    分类色板与"逐条自定义色"一起退休。
    """

    # 蓝渐变的两个端点（2026-10-14 加宽后）：第 1 条 = 基色与黑混 30%，
    # 最后一条 = 基色与白混 55%（浅端不变，见 panel_state._GRADIENT_DARK_MIX）
    _DARK_2 = "#1d5496"
    _LIGHT_2 = "#9fc2ed"

    def test_gradient_color_sampling(self):
        """单条 = 基色；多条从最深一头到最浅一头；未知色系兜底蓝；越界夹回。"""
        self.assertEqual(gui_state._gradient_color("蓝", 0, 1), "#2a78d6")
        self.assertEqual(gui_state._gradient_color("绿", 0, 1), "#1baf7a")
        self.assertEqual([gui_state._gradient_color("蓝", i, 2)
                          for i in range(2)],
                         [self._DARK_2, self._LIGHT_2])
        # 越界夹回范围（防御）：负索引 → 第 0 条、超界 → 最后一条
        self.assertEqual(gui_state._gradient_color("蓝", -3, 2), self._DARK_2)
        self.assertEqual(gui_state._gradient_color("蓝", 99, 2), self._LIGHT_2)
        self.assertEqual(gui_state._gradient_color("乱写的", 0, 1), "#2a78d6")

    def test_rainbow_family_is_ordered_and_never_cycles(self):
        """「彩虹」（2026-10-14）：瀑布同款 viridis——按顺序、不循环、可对表。

        用户："对比图不堆叠时颜色很难区分，要不要改成瀑布图的配色那种"＋
        "除了不要循环的颜色，按顺序的数据能获得有规律的按顺序颜色就可以"。
        取样与瀑布（plot_views 的 `cm.viridis(linspace(0,1,n))`）逐位一致；
        n 条就是 n 个互不重复的颜色；单曲线取中段青绿。
        """
        from matplotlib import cm
        from matplotlib.colors import to_hex
        for n in (2, 5, 8, 81):
            mine = [gui_state._gradient_color("彩虹", i, n) for i in range(n)]
            ref = [to_hex(c) for c in cm.viridis(np.linspace(0, 1, n))]
            self.assertEqual(mine, ref, f"n={n} 应与瀑布取样一致")
            self.assertEqual(len(set(mine)), n, "颜色不循环、不重复")
        self.assertEqual(gui_state._gradient_color("彩虹", 0, 1), "#21918c",
                         "单曲线取中段")
        # 越界夹回（防御）
        self.assertEqual(gui_state._gradient_color("彩虹", -1, 4),
                         gui_state._gradient_color("彩虹", 0, 4))
        self.assertEqual(gui_state._gradient_color("彩虹", 99, 4),
                         gui_state._gradient_color("彩虹", 3, 4))

    def test_draw_1d_follows_gradient_family(self):
        """1D 单曲线：默认蓝基色；快照切绿重画 = 绿基色。"""
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0))
            ax = _axes(w, "1D", "data/fake_b.tif")
            self.assertEqual(ax.lines[0].get_color(), "#2a78d6")
            dock = _dock(w, "1D", "data/fake_b.tif")
            dock.params_snapshot["渐变色系"] = "绿"
            gui_views._draw_1d(w, dock, dock.last_tth, dock.last_intensity)
            self.assertEqual(ax.lines[0].get_color(), "#1baf7a",
                             "单曲线永远用基色本身（加宽只影响多条）")
        finally:
            w.close()

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

    def test_compare_uses_the_gradient(self):
        """对比曲线按勾选顺序渐变（第 1 条最深、最后一条最浅）；换色系重画不被打回。"""
        w = create_window()
        try:
            key, ax = self._plot_compare(w)
            dock = w.plot_docks[key]
            self.assertEqual([line.get_color() for line in ax.lines],
                             [self._DARK_2, self._LIGHT_2])
            # 换橙 → 重画按新参数着色（旧色不被套回）
            dock.params_snapshot["渐变色系"] = "橙"
            gui_plot_compare._redraw_compare(w, key)
            self.assertEqual(ax.lines[0].get_color(), "#a44924")
            self.assertNotEqual(ax.lines[1].get_color(), self._LIGHT_2,
                                "第 2 条的浅色应跟着色系换")
        finally:
            w.close()

    def test_family_widget_and_reset(self):
        """参数坞有渐变色系下拉；[恢复默认] 回蓝；默认表收录新参数。"""
        w = create_window()
        try:
            w.params["渐变色系"].setCurrentIndex(
                w.params["渐变色系"].findData("绿"))
            w.findChild(QPushButton, "reset_image_btn").click()
            self.assertEqual(w.params["渐变色系"].currentData(), "蓝")
            self.assertEqual(gui_state._DISPLAY_DEFAULTS["渐变色系"], "蓝")
        finally:
            w.close()

    def test_customize_compare_family_section(self):
        """对比面板「外观」：渐变色系单选（应用才写回、随即重画）。"""
        w = create_window()
        try:
            key, ax = self._plot_compare(w)
            dock = w.plot_docks[key]
            content = gui_panel_state._content(dock)
            dlg = gui_customize._build_customize_dialog(w, dock, content.axes_1d,
                                                  content.figure)
            combo = dlg.findChild(QComboBox, "gradient_family_combo")
            self.assertIsNotNone(combo, "对比面板应有渐变色系单选")
            self.assertEqual(combo.currentData(), "蓝", "预填当前色系")
            combo.setCurrentIndex(combo.findData("紫"))
            gui_customize._apply_customize(w, dock, content.axes_1d,
                                     content.figure, dlg)
            self.assertEqual(dock.params_snapshot["渐变色系"], "紫")
            self.assertEqual(ax.lines[0].get_color(), "#342975")
        finally:
            w.close()

    def test_rainbow_family_reaches_the_figure_via_customize(self):
        """「彩虹」进两处下拉，走「外观」[应用] 真路径落到曲线上。

        参数坞下拉（新图默认）与「外观」单选（这张图）都应列到「彩虹色
        渐变」；选它 [应用] 后，两条对比曲线取 viridis 两端（深紫/黄）。
        """
        w = create_window()
        try:
            dock_combo = w.params["渐变色系"]
            idx = dock_combo.findData("彩虹")
            self.assertGreaterEqual(idx, 0, "参数坞下拉应有彩虹")
            self.assertEqual(dock_combo.itemText(idx), "彩虹色渐变")
            key, ax = self._plot_compare(w)
            dock = w.plot_docks[key]
            content = gui_panel_state._content(dock)
            dlg = gui_customize._build_customize_dialog(w, dock, content.axes_1d,
                                                  content.figure)
            combo = dlg.findChild(QComboBox, "gradient_family_combo")
            idx = combo.findData("彩虹")
            self.assertGreaterEqual(idx, 0, "「外观」单选应有彩虹")
            self.assertEqual(combo.itemText(idx), "彩虹色渐变")
            combo.setCurrentIndex(idx)
            gui_customize._apply_customize(w, dock, content.axes_1d,
                                     content.figure, dlg)
            self.assertEqual(dock.params_snapshot["渐变色系"], "彩虹")
            self.assertEqual(ax.lines[0].get_color(), "#440154", "第 1 条 = 深紫")
            self.assertEqual(ax.lines[1].get_color(), "#fde725", "第 2 条 = 黄")
        finally:
            w.close()

    def test_classic_family_rounds_and_lighten(self):
        """「经典色」（2026-10-15 用户："对比图添加论文中常用颜色"）：
        Okabe–Ito 前 8 条一色一条、互不重复；第 9 条起按轮压暗
        （×0.75）；黑色压不动改往白提亮（不然每轮长得一模一样）；
        单曲线取色 1；越界夹回。"""
        first = [gui_state._gradient_color("经典色", i, 8) for i in range(8)]
        self.assertEqual(first, ["#0072B2", "#D55E00", "#009E73", "#E69F00",
                                 "#56B4E9", "#CC79A7", "#F0E442", "#000000"])
        self.assertEqual(len(set(first)), 8, "前 8 条互不重复")
        self.assertEqual(gui_state._gradient_color("经典色", 8, 81),
                         "#005686", "第 9 条 = 色 1 压暗 25%（0.75 轮）")
        self.assertEqual(gui_state._gradient_color("经典色", 15, 81),
                         "#404040", "黑色压不动 → 往白提亮 25%")
        self.assertNotEqual(gui_state._gradient_color("经典色", 0, 81),
                            gui_state._gradient_color("经典色", 8, 81),
                            "第二轮不与第一轮同色")
        self.assertEqual(gui_state._gradient_color("经典色", 0, 1), "#0072B2",
                         "单曲线取色 1")
        # 越界夹回（防御）
        self.assertEqual(gui_state._gradient_color("经典色", -1, 4),
                         gui_state._gradient_color("经典色", 0, 4))
        self.assertEqual(gui_state._gradient_color("经典色", 99, 4),
                         gui_state._gradient_color("经典色", 3, 4))

    def test_classic_family_reaches_the_figure_via_customize(self):
        """「经典色」进两处下拉（显示名就叫「经典色」，不带"渐变"后缀），
        选它 [应用] 后，两条对比曲线取 Okabe–Ito 色 1 / 色 2。"""
        w = create_window()
        try:
            dock_combo = w.params["渐变色系"]
            idx = dock_combo.findData("经典色")
            self.assertGreaterEqual(idx, 0, "参数坞下拉应有经典色")
            self.assertEqual(dock_combo.itemText(idx), "经典色")
            key, ax = self._plot_compare(w)
            dock = w.plot_docks[key]
            content = gui_panel_state._content(dock)
            dlg = gui_customize._build_customize_dialog(w, dock, content.axes_1d,
                                                  content.figure)
            combo = dlg.findChild(QComboBox, "gradient_family_combo")
            idx = combo.findData("经典色")
            self.assertGreaterEqual(idx, 0, "「外观」单选应有经典色")
            self.assertEqual(combo.itemText(idx), "经典色")
            combo.setCurrentIndex(idx)
            gui_customize._apply_customize(w, dock, content.axes_1d,
                                     content.figure, dlg)
            self.assertEqual(dock.params_snapshot["渐变色系"], "经典色")
            self.assertEqual(ax.lines[0].get_color(), "#0072B2")
            self.assertEqual(ax.lines[1].get_color(), "#D55E00")
        finally:
            w.close()

    def _color_scale_artists(self, ax):
        tag = gui_plot_compare.COLOR_SCALE_TAG
        return [a for a in ax.get_children() if a.get_gid() == tag]

    def _plot_compare_n(self, w, n):
        """勾 n 个文件出一张对比图（>12 条时测色标用）；先各出过 1D
        （栅栏前置）再点 [对比]。"""
        paths = [f"data/fake_{i:02d}.tif" for i in range(n)]
        _prime_1d(w, paths)
        w.compare_btn.click()
        keys = [k for k in w.plot_docks if k.startswith("对比")]
        ax = gui_panel_state._content(w.plot_docks[keys[0]]).axes_1d
        self.assertTrue(_wait_until(lambda: len(ax.lines) >= n))
        return keys[0], ax

    def test_color_scale_is_a_toggle_default_off(self):
        """「显示色标」= 朴素开关、默认不显示（用户 2026-10-15 晚："把这个
        改为显示图例的选项，默认不显示"——上午的"自动 >12 才画"退休）。
        勾上 → 画（条数不论），勾掉 → 不画。"""
        w = create_window()
        try:
            chk = w.params["色标"]
            self.assertFalse(chk.isChecked(), "默认不显示")
            n = gui_plot_compare.LEGEND_MAX_CURVES + 3      # 15 条
            key, ax = self._plot_compare_n(w, n)
            self.assertEqual(self._color_scale_artists(ax), [], "默认不画")
            chk.setChecked(True)                    # 即改即画（真控件路径）
            self.assertTrue(self._color_scale_artists(ax), "勾上该画出来")
            chk.setChecked(False)
            self.assertEqual(self._color_scale_artists(ax), [], "勾掉不画")
        finally:
            w.close()

    def test_color_scale_on_draws_for_any_count(self):
        """开关打开时两条也画（做论文图用）；说明文字默认"颜色按文件名
        尾号顺序"，两端短名 = 色号最小/最大的那条。"""
        w = create_window()
        try:
            key, ax = self._plot_compare(w)        # 2 条
            w.params["色标"].setChecked(True)
            arts = self._color_scale_artists(ax)
            self.assertGreaterEqual(len(arts), 10, "两条也该画")
            texts = [t.get_text() for t in ax.texts
                     if t.get_gid() == gui_plot_compare.COLOR_SCALE_TAG]
            self.assertIn("颜色按文件名尾号顺序", texts, "默认说明文字")
            short = gui_plot_compare._short_labels(
                ["fake_a.tif", "fake_b.tif"], ax)
            self.assertIn(short[0], texts, "左端 = 色号最小的短名")
            self.assertIn(short[-1], texts, "右端 = 色号最大的短名")
        finally:
            w.close()

    def test_color_order_follows_the_filename_number(self):
        """颜色序号按**文件名尾号**排（用户 2026-10-15 晚："识别的不对，
        应该是文件名最后几位数的顺序"）：勾选/文件栏顺序打乱也不影响谁
        最深——尾号最小的拿最深色，色标左端/右端跟着它走。"""
        w = create_window()
        try:
            paths = ["data/fake_20.tif", "data/fake_05.tif",
                     "data/fake_11.tif"]   # 加的先后 ≠ 尾号顺序
            _prime_1d(w, paths)            # 栅栏前置：先各出过 1D
            w.compare_btn.click()
            keys = [k for k in w.plot_docks if k.startswith("对比")]
            ax = gui_panel_state._content(w.plot_docks[keys[0]]).axes_1d
            self.assertTrue(_wait_until(lambda: len(ax.lines) >= 3))
            by_label = {ln.get_label(): ln.get_color() for ln in ax.lines}
            self.assertEqual(by_label["fake_05.tif"],
                             gui_state._gradient_color("蓝", 0, 3),
                             "尾号最小的拿最深色")
            self.assertEqual(by_label["fake_11.tif"],
                             gui_state._gradient_color("蓝", 1, 3))
            self.assertEqual(by_label["fake_20.tif"],
                             gui_state._gradient_color("蓝", 2, 3),
                             "尾号最大的拿最浅色")
            # 色标两端跟着色号走：左 = 05、右 = 20
            w.params["色标"].setChecked(True)
            by_text = {t.get_text(): t for t in ax.texts
                       if t.get_gid() == gui_plot_compare.COLOR_SCALE_TAG}
            self.assertIn("05", by_text, "左端 = 尾号最小的短名")
            self.assertIn("20", by_text, "右端 = 尾号最大的短名")
            self.assertLess(by_text["05"].get_position()[0],
                            by_text["20"].get_position()[0],
                            "05 在左、20 在右")
        finally:
            w.close()

    def test_color_scale_caption_editable_via_customize(self):
        """色标说明可在「外观…」里改（用户 2026-10-15："自行更改描述方式"）：
        输入框预填默认文案；改成自定义 → 图上那行字跟着换；清空 → 不画
        这行（两端短名保留）；[恢复默认] 把输入框带回默认文案。"""
        w = create_window()
        try:
            n = gui_plot_compare.LEGEND_MAX_CURVES + 3     # 15 条
            key, ax = self._plot_compare_n(w, n)
            w.params["色标"].setChecked(True)              # 说明要画出来才看得见
            dock = w.plot_docks[key]
            content = gui_panel_state._content(dock)
            dlg = gui_customize._build_customize_dialog(w, dock, content.axes_1d,
                                                  content.figure)
            edit = dlg.findChild(QLineEdit, "color_scale_note_edit")
            self.assertIsNotNone(edit, "对比面板应有「色标说明」输入框")
            self.assertEqual(edit.text(), "颜色按文件名尾号顺序", "预填默认文案")

            def texts():
                return [t.get_text() for t in ax.texts
                        if t.get_gid() == gui_plot_compare.COLOR_SCALE_TAG]

            edit.setText("第 1 条（左）到第 15 条（右）")
            gui_customize._apply_customize(w, dock, content.axes_1d,
                                     content.figure, dlg)
            self.assertEqual(dock.params_snapshot["色标说明"],
                             "第 1 条（左）到第 15 条（右）")
            self.assertIn("第 1 条（左）到第 15 条（右）", texts())
            self.assertNotIn("颜色按文件名尾号顺序", texts())
            # 清空 = 不画那行字；两端短名仍在（只剩 2 条文字）
            edit.setText("")
            gui_customize._apply_customize(w, dock, content.axes_1d,
                                     content.figure, dlg)
            short = gui_plot_compare._short_labels(
                [f"fake_{0:02d}.tif", f"fake_{n - 1:02d}.tif"], ax)
            self.assertEqual(sorted(texts()), sorted(short),
                             "只剩两端的短名")
            # [恢复默认] 把输入框带回默认文案
            gui_customize._reset_customize_fields(dock, dlg._fields)
            self.assertEqual(edit.text(), "颜色按文件名尾号顺序")
        finally:
            w.close()

    def test_color_scale_corner_editable_via_customize(self):
        """色标位置四选（用户 2026-10-16："实现色标仅保留横向以及可以选择
        放在图的哪个角落"——同日的横竖二选在发版前退休，固定横版；默认角
        当天再由用户定为"右上"："色标位置默认右上"）：默认右上角；在
        [外观…] 里选"左下" → 卡片整块挪到左下角，说明文字永远横排
        （rotation 0）；[恢复默认] 回右上。"""
        w = create_window()
        try:
            n = gui_plot_compare.LEGEND_MAX_CURVES + 3
            key, ax = self._plot_compare_n(w, n)
            w.params["色标"].setChecked(True)
            dock = w.plot_docks[key]
            content = gui_panel_state._content(dock)

            def card_xy():
                for a in self._color_scale_artists(ax):
                    fc = getattr(a, "get_facecolor", None)
                    if fc is not None and tuple(fc()) == (1.0, 1.0, 1.0, 1.0):
                        return tuple(a.get_xy())
                return None

            def caption():
                for t in ax.texts:
                    if (t.get_gid() == gui_plot_compare.COLOR_SCALE_TAG
                            and t.get_text() == "颜色按文件名尾号顺序"):
                        return t
                return None

            self.assertEqual(card_xy(), (0.58, 0.79), "默认右上角")
            self.assertEqual(caption().get_rotation(), 0, "永远横排")
            dlg = gui_customize._build_customize_dialog(w, dock, content.axes_1d,
                                                  content.figure)
            pos_combo = dlg.findChild(QComboBox, "color_scale_pos_combo")
            self.assertIsNotNone(pos_combo, "对比面板应有「色标位置」下拉")
            self.assertEqual(pos_combo.currentData(), "右上", "预填当前位置")
            pos_combo.setCurrentIndex(pos_combo.findData("左下"))
            gui_customize._apply_customize(w, dock, content.axes_1d,
                                     content.figure, dlg)
            self.assertEqual(dock.params_snapshot["色标位置"], "左下")
            self.assertEqual(card_xy(), (0.02, 0.28), "卡片整块挪到左下角")
            self.assertAlmostEqual(caption().get_position()[0], 0.22,
                                   places=6, msg="说明文字跟着卡片走")
            self.assertEqual(caption().get_rotation(), 0, "仍横排")
            gui_customize._reset_customize_fields(dock, dlg._fields)
            self.assertEqual(pos_combo.currentData(), "右上",
                             "[恢复默认] 回右上")
        finally:
            w.close()

    def test_compare_family_survives_param_change(self):
        """「外观」选的色系，之后改别的显示参数不被参数坞下拉的旧值打回默认。

        用户 2026-10-14："对比图颜色在改参数后会恢复默认"——「外观」[应用]
        照设计只写快照（每张图各记各的），参数坞那把下拉还停在旧值；随后
        任一显示参数一走 _refresh_compare 的"控件值优先"重建，色系就被
        下拉的旧值打回去了。修法 = 色系退出控件采集（同视图窗口那套），
        下拉自己那条路显式写快照（_refresh_compare_family）。
        """
        w = create_window()
        try:
            key, ax = self._plot_compare(w)
            dock = w.plot_docks[key]
            content = gui_panel_state._content(dock)
            dlg = gui_customize._build_customize_dialog(w, dock, content.axes_1d,
                                                  content.figure)
            combo = dlg.findChild(QComboBox, "gradient_family_combo")
            combo.setCurrentIndex(combo.findData("紫"))
            gui_customize._apply_customize(w, dock, content.axes_1d,
                                     content.figure, dlg)
            self.assertEqual(ax.lines[0].get_color(), "#342975")
            # 真实控件路径：勾「显示数据名」→ 走 _refresh_compare 就地重画
            w.params["显示数据名"].setChecked(True)
            self.assertEqual(dock.params_snapshot["渐变色系"], "紫",
                             "色系不该被参数坞下拉的旧值打回默认")
            self.assertEqual(ax.lines[0].get_color(), "#342975")
        finally:
            w.close()

    def test_family_widget_change_repaints(self):
        """参数坞那把色系下拉拨一下 = 立刻按新色系重画（显式写快照那条路）。

        下拉不参与"控件值优先"重建、要有自己的写回（2026-10-14 色系修复的
        另一半）——防止修完复现路径后把这条正常路径改坏。
        """
        w = create_window()
        try:
            key, ax = self._plot_compare(w)
            dock = w.plot_docks[key]
            box = w.params["渐变色系"]
            box.setCurrentIndex(box.findData("绿"))
            self.assertEqual(dock.params_snapshot["渐变色系"], "绿")
            self.assertEqual(ax.lines[0].get_color(), "#137a55")
        finally:
            w.close()

    def test_family_before_plotting_is_the_new_figures_start(self):
        """没有对比面板时拨色系 → 进"待应用"账本，出图的新面板就用它。"""
        w = create_window()
        try:
            box = w.params["渐变色系"]
            box.setCurrentIndex(box.findData("橙"))
            self.assertEqual(w.pending_display.get("渐变色系"), "橙")
            key, ax = self._plot_compare(w)
            self.assertEqual(w.plot_docks[key].params_snapshot["渐变色系"], "橙")
            self.assertEqual(ax.lines[0].get_color(), "#a44924")
        finally:
            w.close()


class TestPendingDisplayParams(unittest.TestCase):
    """出图前调好的显示参数 = 新图的起点（2026-10-13）。

    用户："对比热图页面如果在出图之前调整参数会无效，出图瞬间所有的
    参数都会恢复默认，怀疑所有实时改变的参数都会有这种情况"——属实：
    没有对应面板时各家 handler 早退/空转，改动只留在控件上（出图时
    新面板统一从默认起步，焦点一回写就"瞬间恢复默认"）。现在记进
    窗口级"待应用"账本（panel_state._stash_pending_display），出图
    （_data_snapshot base=None）时叠在默认值上；焦点回放把控件刷成
    别的图的设置时作废（"切走 = 放弃"）。
    """

    def test_heat_params_set_before_plotting_are_used(self):
        """一张图都没有时改热图色图/对数 → 出热图时就是这套值，不回弹。"""
        w = create_window()
        try:
            # 栅栏前置（2026-10-16）：先各出过 1D。顺序要紧——账本只记
            # "没有面板可接"的改动，调参数必须排在出 1D 之后
            _prime_1d(w, ["data/fake_a.tif", "data/fake_b.tif"],
                      stub=_fake_compute)
            # 先调参数（此时没有热图面板；此前这些改动直接丢）
            combo = w.params["热图色图"]
            combo.setCurrentIndex(combo.findData("viridis"))
            w.params["热图对数"].setChecked(True)
            self.assertEqual(w.pending_display.get("热图色图"), "viridis",
                             "无面板可接：改动该记进待应用账本")
            # 再出图：新面板就用这套值
            w.heat_btn.click()
            self.assertTrue(_wait_until(
                lambda: any(k.startswith("热图")
                            and getattr(d, "heat_data", None) is not None
                            for k, d in w.plot_docks.items())))
            key = next(k for k in w.plot_docks if k.startswith("热图"))
            dock = w.plot_docks[key]
            self.assertEqual(dock.params_snapshot["热图色图"], "viridis")
            self.assertTrue(dock.params_snapshot["热图对数"])
            self.assertEqual(w.params["热图色图"].currentData(), "viridis",
                             "控件不许被弹回默认")
            ax = gui_panel_state._content(dock).axes_heat
            self.assertEqual(ax.images[0].get_cmap().name, "viridis",
                             "画出来就该是出图前的色图")
        finally:
            w.close()

    def test_compare_params_set_before_plotting_are_used(self):
        """渐变色系 / 堆叠 / 视图 2θ 框：出图前调好，出图时就是它。"""
        w = create_window()
        try:
            # 栅栏前置（2026-10-16）：先各出过 1D。顺序要紧——账本只记
            # "没有面板可接"的改动，调参数必须排在出 1D 之后
            _prime_1d(w, ["data/fake_a.tif", "data/fake_b.tif"],
                      stub=_fake_compare_compute)
            combo = w.params["渐变色系"]
            combo.setCurrentIndex(combo.findData("绿"))
            w.params["对比堆叠"].setChecked(True)
            w.params["视图 2θ 下限 (°)"].setValue(2.0)
            w.params["视图 2θ 上限 (°)"].setValue(5.0)
            w.compare_btn.click()
            self.assertTrue(_wait_until(
                lambda: any(k.startswith("对比")
                            and len(gui_panel_state._content(
                                w.plot_docks[k]).axes_1d.lines) >= 2
                            for k in w.plot_docks), 60000))
            key = next(k for k in w.plot_docks if k.startswith("对比"))
            dock = w.plot_docks[key]
            snap = dock.params_snapshot
            self.assertEqual(snap["渐变色系"], "绿")
            self.assertTrue(snap["对比堆叠"])
            ax = gui_panel_state._content(dock).axes_1d
            self.assertEqual(ax.lines[0].get_color(), "#137a55",
                             "渐变按出图前的色系画")
            self.assertAlmostEqual(ax.get_xlim()[0], 2.0, places=1,
                                   msg="出图前的视图窗口该生效")
        finally:
            w.close()

    def test_1d_display_set_before_plotting_is_used(self):
        """对数纵轴：一张图都没有时勾上 → 新 1D 图就是对数轴。"""
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                w.params["对数纵轴"].setChecked(True)
                self.assertTrue(w.pending_display.get("对数纵轴"))
                add_checked(w, ["data/fake_a.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0))
            dock = _dock(w, "1D", "data/fake_a.tif")
            self.assertTrue(dock.params_snapshot["对数纵轴"])
            self.assertEqual(_axes(w, "1D", "data/fake_a.tif").get_yscale(),
                             "log", "新图该按出图前的对数设置画")
        finally:
            w.close()

    def test_pending_applies_to_a_whole_batch(self):
        """批量出图：账本给同批的**每一张**当起点（不是只喂第一张）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                w.params["对数纵轴"].setChecked(True)
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: all(len(_axes(w, "1D", p).lines) > 0
                                for p in ("data/fake_a.tif",
                                          "data/fake_b.tif"))))
            for path in ("data/fake_a.tif", "data/fake_b.tif"):
                self.assertTrue(
                    _dock(w, "1D", path).params_snapshot["对数纵轴"],
                    f"{path} 该也拿到出图前的对数设置")
        finally:
            w.close()

    def test_processing_params_set_before_plotting_are_used(self):
        """处理页参数：没有编辑对象时把背景调成自动基线 → 新图就按它画。"""
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                combo = w.params["背景扣除模式"]
                combo.setCurrentIndex(combo.findData("auto"))
                self.assertEqual(w.pending_display.get("背景扣除模式"), "auto",
                                 "无编辑对象：处理参数该记进账本")
                add_checked(w, ["data/fake_a.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0))
            dock = _dock(w, "1D", "data/fake_a.tif")
            self.assertEqual(dock.params_snapshot["背景扣除模式"], "auto",
                             "新图该按出图前的处理设置画")
        finally:
            w.close()

    def test_pending_dropped_when_widget_replayed_with_other_values(self):
        """切到别的图（焦点回放刷成不同值）= 待应用值作废。"""
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_a.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0))
            w.focus_panel = None    # 模拟"刚才没在看任何面板"
            combo = w.params["热图色图"]
            combo.setCurrentIndex(combo.findData("viridis"))
            self.assertEqual(w.pending_display.get("热图色图"), "viridis")
            # 焦点回放到已有 1D 面板（快照里是默认 magma）→ 作废
            gui_panel_state._set_focus(w, "1D|data/fake_a.tif", "fake_a.tif")
            self.assertNotIn("热图色图",
                             getattr(w, "pending_display", None) or {},
                             "控件已被刷成别的图的值：待应用值该作废")
        finally:
            w.close()
