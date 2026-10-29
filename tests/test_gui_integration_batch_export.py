"""批量进度、导出、PONI 导入、配置删除

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


class TestBatchProgress(unittest.TestCase):
    """批量进度计数：多文件一次出图，完成/失败日志末尾贴（k/n）。"""

    def test_batch_logs_progress_suffix_and_clears(self):
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                _open_view(w, "1D")
                # fake_a 慢 0.2 s → fake_b 先完成 =（1/2），fake_a =（2/2）
                self.assertTrue(_wait_until(
                    lambda: all(
                        getattr(_dock(w, "1D", p), "last_tth", None)
                        is not None
                        for p in ("data/fake_a.tif", "data/fake_b.tif"))))
            log = w.log_text.toPlainText()
            # 不写死"谁先完成"：BackgroundTask 每任务一个线程，完成回调
            # 到主线程的先后由调度决定（教训 12 的定时炸弹——本测试曾
            # 因机器负载翻转而红）。断言的是"两条都计数、且合计是 1/2
            # 与 2/2"，顺序交给调度。
            done = re.findall(r"积分完成：(fake_[ab]\.tif)（3 点，"
                              r"2θ 0\.500–8\.500°）（(\d)/2）", log)
            self.assertEqual({name for name, _ in done},
                             {"fake_a.tif", "fake_b.tif"},
                             f"两个文件都该报完成：{done}")
            self.assertEqual({k for _, k in done}, {"1", "2"},
                             f"进度计数应是 1/2 与 2/2：{done}")
            self.assertNotIn("正在开面板", log,
                             "两块面板是瞬时的，不该报开面板进度")
            self.assertFalse(hasattr(w, "_batch"),
                             "批走完应清账（之后零散任务不再计数）")
        finally:
            w.close()

    def test_many_panels_log_opening_progress(self):
        """一批 9 块面板：每 8 块报一次进度、顺手消化事件（E-2）。

        开面板是主线程上的活（真数据 130~290 ms/块），一口气开几十块
        会闷住界面十几秒——用户反馈"图一多就很卡"的真身。

        这里**故意把 _open_plot_panel / _run_view 换成桩**、不真建 9 块
        面板：offscreen 下在同一进程里连建十来块 Qt 工具栏会偶发在 Qt
        的动作事件里递归挂死（实测 3/12，关窗后的延迟删除只是背景条
        件；与本次改动无关，Qt/mpl 侧的老毛病）。所以真建面板那条路
        由上面的两文件批量用例覆盖，这条只钉"记账与节奏"：总数、每
        8 块报一次、每块都真的去开。
        """
        paths = [f"data/fake_n{i}.tif" for i in range(1, 10)]
        opened = []
        w = create_window()
        try:
            add_checked(w, paths)
            with mock.patch.object(
                    gui_plot_view_flow, "_open_plot_panel",
                    side_effect=lambda win, name, key, title:
                    opened.append(key) or mock.MagicMock()), \
                    mock.patch.object(gui_plot_view_flow, "_run_view"):
                _open_view(w, "1D")
            log = w.log_text.toPlainText()
            # 第 9 块（i=8）跨过 8 的倍数 → 报一次；9 块都真的去开了
            self.assertEqual(opened, [f"1D|data/fake_n{i}.tif"
                                      for i in range(1, 10)],
                             "九块面板都该去开，顺序照勾选顺序")
            self.assertIn("正在开面板：9/9…", log)
            self.assertEqual(log.count("正在开面板："), 1,
                             "9 块只跨过 8 一次")
            # 9 张 > 合并阈值 → 开面板合并成一行（不再 9 行"打开面板"）
            self.assertIn("打开 1D 面板 9 张：fake_n1.tif、fake_n2.tif、"
                          "fake_n3.tif、fake_n4.tif、fake_n5.tif…", log)
            self.assertNotIn("打开 1D 面板：fake_n9.tif", log)
        finally:
            w.close()

    def test_the_batch_cap_is_twenty_five(self):
        """防爆线 = 25 张（2026-10-09 用户："超过一次最多画的 24 张改为
        最多25"，此前是 10-08 拍的 24 全画）：25 张照画、26 张起防爆。
        数字是用户点名的，钉住。"""
        self.assertEqual(gui_views.MAX_PANELS_PER_BATCH, 25)

    def test_pixel_views_also_refuse_the_whole_batch_over_the_cap(self):
        """原图（2D/剖面/瀑布）超上限也**一张都不弹**（2026-10-08 用户：
        "原图批量出图没有防爆图"——此前它只画前 N 张、其余记日志）。"""
        folder = tempfile.mkdtemp()
        paths = [str(Path(folder, f"v{i}.tif")) for i in range(1, 4)]
        for p in paths:
            Path(p).touch()
        w = create_window()
        try:
            with mock.patch.object(gui_plot_view_flow, "MAX_PANELS_PER_BATCH", 2), \
                    mock.patch.object(gui_plot_workers, "load_diffraction_image",
                                      return_value=np.zeros((8, 8))):
                add_checked(w, paths)
                _open_view(w, "2D")
                QApplication.processEvents()
            self.assertEqual([k for k in w.plot_docks if k.startswith("2D|")],
                             [], "超上限就该一张都不弹")
            log = w.log_text.toPlainText()
            self.assertIn("一张都不弹", log)
            self.assertIn("[热图] / [对比]", log)
        finally:
            w.close()

    def test_batch_cap_opens_only_max_panels(self):
        """勾选超过上限 → 一张都不画（全部只算不画）；没超过照旧全画。

        2026-09-26 用户改的规则（原来 = 画前 N 张、其余只算不画）：
        "如果原始很多…全部打开只会打开二十来张"，于是超过上限就一张
        都不画、结果列进文件栏，看哪张点哪张。
        """
        folder = tempfile.mkdtemp()
        paths = [str(Path(folder, f"c{i}.tif")) for i in range(1, 5)]
        for p in paths:      # 真文件：产物落盘要用到真路径（file 指纹）
            Path(p).touch()
        w = create_window()
        try:
            cache_kw = None
            with mock.patch.object(gui_plot_view_flow, "MAX_PANELS_PER_BATCH", 4), \
                    mock.patch.object(gui_plot_spawn, "_compute_integration",
                                      side_effect=_fake_compute):
                # ① 4 张 ≤ 上限 4：照旧一张一张全画
                add_checked(w, paths)
                _open_view(w, "1D")
                self.assertTrue(_wait_until(lambda: not hasattr(w, "_batch")),
                                "这批判完应清账")
                self.assertEqual(
                    sorted(k for k in w.plot_docks if k.startswith("1D|")),
                    ["1D|" + p for p in paths], "没超上限应全画")
            for p in paths:      # 关掉这批面板，下一轮干净
                dock = w.plot_docks.get("1D|" + p)
                if dock is not None:
                    dock.close()
            recheck_all(w)   # ① 出过图 → 勾选已清零（第 8 条）：② 要重新勾
            with mock.patch.object(gui_plot_view_flow, "MAX_PANELS_PER_BATCH", 2), \
                    mock.patch.object(gui_plot_spawn, "_compute_integration",
                                      side_effect=_fake_compute):
                # ② 4 张 > 上限 2：一张都不画
                _open_view(w, "1D")
                self.assertTrue(_wait_until(lambda: not hasattr(w, "_batch")),
                                "这批判完应清账")
                self.assertEqual(sorted(k for k in w.plot_docks
                                        if k.startswith("1D|")),
                                 [], "超上限就该一张都不画")
                log = w.log_text.toPlainText()
                self.assertIn("全部只算不画", log)
                self.assertIn("1D 产物", log)      # 指向文件栏
                # 计数含全部只算不画那几张：总数不对的话 k/n 到不了 n
                self.assertIn("（1/4）", log)
                self.assertNotIn("（2/2）", log)
            # 只算不画的真结果：产物落了盘（下次点开就是复用缓存）
            geom = gui_panel_state._collect_geometry(w)
            kw = dict(config=w.config_name,
                      npt=int(w.params["输出点数"].value()),
                      tth_min=geom.get("tth_min_deg"),
                      tth_max=geom.get("tth_max_deg"))
            from xrd_toolkit.services import stage_cache
            for p in paths[2:]:
                self.assertTrue(stage_cache.has_1d(Path(p), **kw),
                                f"超限的 {Path(p).name} 该已算完入库")
        finally:
            w.close()

    def test_over_cap_batch_fully_cached_still_has_a_closing(self):
        """超限 + 产物全在（对比刚算完/上一轮刚算过）→ 点 [1D] 也要有下文：
        刷新文件栏（产物进栏 + "出现 N 条新产物"一行）+ 一句收尾说明。

        用户 2026-10-15："导入原始数据后，直接进行对比出图，然后再点击
        1d 出图，就会卡住"——复现实测主线程没卡（看门狗 8 s 阈值没留档），
        是这条**全缓存静默路**：0 个任务 → 没有进度条、也没有批收尾
        （完成行与文件栏刷新都挂在批收尾上）、「1D 产物」不进文件栏，
        看着就像死机（用户当天因此强退过两次）。
        """
        w = create_window()
        try:
            n = gui_views.MAX_PANELS_PER_BATCH + 2      # 27 张 > 上限 25
            paths = _tmp_files(n, prefix="cap_")
            add_checked(w, [str(p) for p in paths])
            for p in paths:
                _store_product(w, p)      # 结果都在（等价：对比/上一轮刚算完）
            w._products_seen = set()      # 基线清零：这 27 条都算"新产物"
            _open_view(w, "1D")
            QApplication.processEvents()
            log = w.log_text.toPlainText()
            self.assertIn("超过一次最多画的 25 张", log, "防爆线那行照旧在")
            self.assertIn("都已经算好", log, "全缓存也要有收尾说明")
            # 不断死数：缓存目录是整个测试模块共用的，别的用例留下的
            # 产物也会被算成"新产物"（只认这行的机制发生过）
            self.assertIn("文件栏出现 ", log, "产物要进文件栏")
            self.assertEqual(
                sorted(k for k in w.plot_docks if k.startswith("1D|")), [],
                "超上限一张都不弹")
            group = _group_by_text(w, "1D 产物")
            self.assertIsNotNone(group, "「1D 产物」组该在文件栏里")
            self.assertGreaterEqual(group.childCount(), n)
        finally:
            w.close()

    def test_batch_progress_bar_shows_and_hides(self):
        """状态栏那条批量进度条：批里出现、范围=总数、批走完收起。

        （用慢假积分把第一条卡住，才好在"飞行中"看它——批走完那一下
        收起来，是"批结束"最直观的信号。）"""
        def slow(path_str, geom, npt):
            time.sleep(0.3)
            return np.array([0.5, 1.0, 8.5]), np.array([1.0, 2.0, 3.0])

        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=slow):
                add_checked(w, ["data/fake_p1.tif", "data/fake_p2.tif"])
                _open_view(w, "1D")
                self.assertFalse(w.batch_progress.isHidden(), "批里该出现进度条")
                self.assertEqual(w.batch_progress.maximum(), 2, "范围 = 这一批的总数")
                self.assertTrue(_wait_until(lambda: not hasattr(w, "_batch")),
                                "这批判完应清账")
            self.assertTrue(w.batch_progress.isHidden(), "批走完该收起")
            self.assertEqual(w.batch_progress.value(), 2, "该走到 n")
        finally:
            w.close()

    def test_big_batch_merges_log_lines(self):
        """大批量（> BATCH_LOG_MERGE_AFTER）：每 8 张一行进度 + 收尾汇总。

        用户 2026-09-25："81 张 = 81 行「打开面板」+ 81 行「积分完成」，
        把日志刷没了"。这条直接喂 _batch_step（不必真开 10 张面板），
        钉三件事：合并模式、进度行的节奏（每 8）、收尾汇总（含用时与
        复用缓存张数）——以及批照样清账。
        """
        w = create_window()
        try:
            w._batch = {"view": "1D", "total": 10, "done": 0,
                        "start": time.time(), "cached": 3}
            quiet_flags = []
            for i in range(10):
                suffix, quiet = gui_views._batch_step(
                    w, f"1D|data/p{i}.tif", f"f{i}.tif")
                quiet_flags.append(quiet)
                self.assertEqual(suffix, f"（{i + 1}/10）")
            log = w.log_text.toPlainText()
            self.assertTrue(all(quiet_flags), "大批量该整批进入合并模式")
            self.assertIn("1D 进度：8/10（最近：f7.tif）", log)
            self.assertEqual(log.count("1D 进度："), 1, "10 张只跨过 8 一次")
            self.assertIn("1D 批完成：10 张", log)
            self.assertIn("复用缓存 3 张", log)
            self.assertFalse(hasattr(w, "_batch"), "批该清账")
        finally:
            w.close()

    def test_replot_inside_the_cap_reuses_panels(self):
        """额度内的重复点 = 复用那几张面板，不新建也不报"少画"。"""
        w = create_window()
        try:
            with mock.patch.object(gui_plot_view_flow, "MAX_PANELS_PER_BATCH", 2), \
                    mock.patch.object(gui_plot_spawn, "_compute_integration",
                                      side_effect=_fake_compute):
                paths = [f"data/fake_d{i}.tif" for i in range(1, 4)]
                add_checked(w, paths)
                # 先只勾前两张（额度刚好用满）
                for i in range(w.file_list.count()):
                    w.file_list.item(i).setCheckState(
                        Qt.Checked if i < 2 else Qt.Unchecked)
                _open_view(w, "1D")
                self.assertTrue(_wait_until(lambda: not hasattr(w, "_batch")))
                n_first = len(w.plot_docks)
                _open_view(w, "1D")   # 同样的勾选再点一次
                self.assertTrue(_wait_until(lambda: not hasattr(w, "_batch")))
            log = w.log_text.toPlainText()
            self.assertEqual(len(w.plot_docks), n_first,
                             "重复点该复用面板，不该再建")
            self.assertNotIn("先画前", log, "两张都在额度内，不该报少画")
        finally:
            w.close()

    def test_single_file_has_no_progress_suffix(self):
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: getattr(_dock(w, "1D", "data/fake_b.tif"),
                                    "last_tth", None) is not None))
            log = w.log_text.toPlainText()
            self.assertIn("积分完成：fake_b.tif（3 点", log)
            self.assertNotIn("（1/1）", log)
        finally:
            w.close()

    def test_error_counts_toward_progress(self):
        """一个文件积分失败：错误日志同样计数，批照常走完。"""
        def flaky(path_str, geom, npt):
            if path_str.endswith("fake_a.tif"):
                raise RuntimeError("解码失败")
            return _fake_compute(path_str, geom, npt)

        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=flaky):
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: not hasattr(w, "_batch")))
            log = w.log_text.toPlainText()
            self.assertIn("积分失败：fake_a.tif —— RuntimeError: 解码失败",
                          log)
            self.assertIn("积分完成：fake_b.tif（3 点，2θ 0.500–8.500°）"
                          "（2/2）", log)
        finally:
            w.close()


class TestFolderImport(unittest.TestCase):
    """[文件夹]：遍历目录（支持格式白名单，与 CLI 一致）+ 重复自动跳过。"""

    def test_folder_scan_adds_supported_and_skips_duplicates(self):
        folder = tempfile.mkdtemp()
        for name in ("a.tif", "b.edf", "c.TIF", "d.txt", "e.cbf"):
            Path(folder, name).touch()
        w = create_window()
        try:
            with mock.patch.object(QFileDialog, "getExistingDirectory",
                                   return_value=folder):
                w.open_folder_action.trigger()
            names = [w.file_list.item(i).text()
                     for i in range(w.file_list.count())]
            self.assertEqual(names, ["a.tif", "b.edf", "c.TIF", "e.cbf"])
            self.assertIn("找到 4 个数据文件", w.log_text.toPlainText())
            # 整目录重导：全部重复 → 逐个跳过不弹窗，列表不长
            with mock.patch.object(QFileDialog, "getExistingDirectory",
                                   return_value=folder):
                w.open_folder_action.trigger()
            self.assertEqual(w.file_list.count(), 4)
            self.assertIn("已跳过重复文件",
                          w.log_text.toPlainText())
        finally:
            w.close()

    def test_empty_folder_logs_hint(self):
        folder = tempfile.mkdtemp()
        w = create_window()
        try:
            with mock.patch.object(QFileDialog, "getExistingDirectory",
                                   return_value=folder):
                w.open_folder_action.trigger()
            self.assertIn("文件夹里没有支持的数据文件",
                          w.log_text.toPlainText())
            self.assertEqual(w.file_list.count(), 0)
        finally:
            w.close()

    def test_folder_dialog_cancelled_does_nothing(self):
        w = create_window()
        try:
            with mock.patch.object(QFileDialog, "getExistingDirectory",
                                   return_value=""):
                w.open_folder_action.trigger()
            self.assertEqual(w.file_list.count(), 0)
        finally:
            w.close()


class TestRecentOpens(unittest.TestCase):
    """「最近打开」（2026-10-08 用户："最近打开——做"）：导入过的文件夹/
    文件记进 [打开…] →「最近打开」子菜单，点击可再导入；失效路径自动消失。"""

    def _texts(self, w):
        w.recent_menu.aboutToShow.emit()   # = 弹出前的重建钩子
        return [a.text() for a in w.recent_menu.actions()]

    def test_records_reimports_and_prunes(self):
        folder = tempfile.mkdtemp()
        Path(folder, "a.tif").touch()
        # recent.json 存公共输出目录、全测试共享——本测把它指到**单独的**
        # 临时文件，免得别的用例（如 TestFolderImport）先跑留下的记录污染
        # "空态"断言（2026-10-08 全量跑抓到的）；也不能写进 folder，
        # 不然最后 os.rmdir 会因目录非空失败
        recent_file = (Path(tempfile.mkdtemp(prefix="xrd_recent_"))
                       / "recent.json")
        w = create_window()
        try:
            with mock.patch.object(gui_file_importing, "_recent_path",
                                   return_value=recent_file):
                self.assertEqual(self._texts(w), ["（空）"],
                                 "没打开过 → 空态占位")
                with mock.patch.object(QFileDialog, "getExistingDirectory",
                                       return_value=folder):
                    w.open_folder_action.trigger()
                self.assertIn(Path(folder).name, self._texts(w))
                # 点「最近打开」里那条 → 走同一条导入口（整目录重复 → 跳过）
                act = next(a for a in w.recent_menu.actions()
                           if a.text() == Path(folder).name)
                act.trigger()
                self.assertIn("已跳过重复文件", w.log_text.toPlainText())
                # 路径失效 → 不再出现在菜单里
                os.remove(Path(folder, "a.tif"))
                os.rmdir(folder)
                self.assertEqual(self._texts(w), ["（空）"],
                                 "失效路径该从菜单消失")
        finally:
            w.close()


class TestRangeMemory(unittest.TestCase):
    """甲方案（2026-10-03 用户定）：新开一张图默认用**该文件自己上次**的
    2θ / 点数，坞顶那行"别处用过的值"不再漏给新图。

    用户原话："2theta 的值现在全局统一，是不对的……我再次对原始数据进行
    处理时，默认使用了刚才对比的 2theta 也是不对的"。
    """

    def _seed_product(self, w, path, lo, hi, npt):
        """给文件落一份 2θ=[lo, hi]、npt 点数的 1D 产物（= 它"上次用过的"）。"""
        kw = _kw_of(w)
        kw.update(npt=npt, tth_min=lo, tth_max=hi)
        tth = np.linspace(lo, hi, 5)
        stage_cache.store_1d(path, tth, np.ones(5), **kw)

    def _open_single(self, w, path):
        """单张出图（勾一个文件点 [1D]），等面板出现。"""
        w.add_files([str(path)], select=True)
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_compute):
            w.view_buttons["1D"].click()
            QApplication.processEvents()
            self.assertTrue(_wait_until(
                lambda: _dock(w, "1D", str(path)) is not None), "面板该开了")
        return _dock(w, "1D", str(path))

    def _set_boxes(self, w, lo, hi, npt):
        """模拟"别处留下的值"（比如刚画完对比，盒子还停在那个范围）。"""
        w.params["2θ 下限 (°)"].setValue(lo)
        w.params["2θ 上限 (°)"].setValue(hi)
        w.params["输出点数"].setValue(npt)
        QApplication.processEvents()

    def test_new_panel_uses_the_files_own_range(self):
        """盒子里的遗留值（已被用掉）不外溢：新图用该文件上次的范围。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            self._seed_product(w, files[0], 2.0, 7.0, 1234)
            self._set_boxes(w, 3.0, 6.0, 999)
            w._pending_consumed = {"数据": True}   # 那把值是"别处用过的"
            dock = self._open_single(w, files[0])
            snap = dock.params_snapshot
            self.assertAlmostEqual(snap["2θ 下限 (°)"], 2.0,
                                   msg="该用文件自己上次的范围")
            self.assertAlmostEqual(snap["2θ 上限 (°)"], 7.0)
            self.assertEqual(snap["输出点数"], 1234)
            self.assertAlmostEqual(w.params["2θ 上限 (°)"].value(), 7.0,
                                   msg="盒子本身也该换成该文件的范围")
        finally:
            w.close()

    def test_a_fresh_edit_still_wins(self):
        """刚填、还没用掉的值是指令：填数出图照旧（甲不改这条）。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            self._seed_product(w, files[0], 2.0, 7.0, 1234)
            self._set_boxes(w, 3.0, 6.0, 999)      # 刚填 → consumed=False
            dock = self._open_single(w, files[0])
            snap = dock.params_snapshot
            self.assertAlmostEqual(snap["2θ 下限 (°)"], 3.0)
            self.assertAlmostEqual(snap["2θ 上限 (°)"], 6.0)
            self.assertEqual(snap["输出点数"], 999)
        finally:
            w.close()

    def test_never_computed_file_falls_back_to_defaults(self):
        """从没算过的文件：回到出厂默认，而不是继承盒子里的遗留值。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            self._set_boxes(w, 3.0, 6.0, 999)
            w._pending_consumed = {"数据": True}
            dock = self._open_single(w, files[0])
            from xrd_toolkit.gui.panel_state import DATA_PARAM_DEFAULTS as D
            snap = dock.params_snapshot
            self.assertAlmostEqual(snap["2θ 下限 (°)"], D["2θ 下限 (°)"])
            self.assertAlmostEqual(snap["2θ 上限 (°)"], D["2θ 上限 (°)"])
            self.assertEqual(snap["输出点数"], D["输出点数"])
        finally:
            w.close()


class TestExportData(unittest.TestCase):
    """[导出数据]：1D 结果批量落盘（镜像 CLI 格式）+ 取消/跳过/单文件失败。"""

    def _two_1d_results(self, w):
        """勾两个文件出 1D（mock 计算），等结果进面板缓存。"""
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: all(
                    getattr(_dock(w, "1D", p), "last_tth", None)
                    is not None
                    for p in ("data/fake_a.tif", "data/fake_b.tif"))))

    def _one_1d_result(self, w):
        """勾一个文件出 1D（mock 计算），等结果进面板缓存。"""
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, ["data/fake_a.tif"])
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: getattr(_dock(w, "1D", "data/fake_a.tif"),
                                "last_tth", None) is not None))

    def test_second_export_does_not_overwrite(self):
        """同一条曲线导两次：第二份顺延 `_2`，绝不覆盖（2026-10-03）。

        用户问过"同一条数据导出两次怎么区分"——原始/处理过那两份靠名字与
        表头就能分；真正会丢数据的是**同一条曲线两套参数各导一次**（两次
        同名），所以导出从这一版起不覆盖已有文件。
        """
        w = create_window()
        try:
            self._one_1d_result(w)
            outdir = Path(tempfile.mkdtemp())
            with mock.patch.object(gui_export, "_build_export_dialog",
                                   return_value={"dir": outdir,
                                                 "suffix": ".txt",
                                                 "csv": False,
                                                 "name": "重复导出"}):
                gui_export._run_export(w)
                gui_export._run_export(w)
            self.assertTrue((outdir / "重复导出.txt").is_file())
            self.assertTrue((outdir / "重复导出_2.txt").is_file(),
                            "第二次导出不许覆盖第一份")
            self.assertIn("已存在，这一份存为 重复导出_2.txt",
                          w.log_text.toPlainText())
        finally:
            w.close()

    def test_export_writes_cli_format_files(self):
        """两个文件 → 一个批量文件夹：txt/ 里每条一个 + 开始/完成日志。"""
        w = create_window()
        try:
            self._two_1d_results(w)
            outdir = Path(tempfile.mkdtemp())
            with mock.patch.object(gui_export, "_build_export_dialog",
                                   return_value={"dir": outdir,
                                                 "suffix": ".txt",
                                                 "csv": False}):
                gui_export._run_export(w)
            batch = _only_batch_dir(outdir)
            # 默认名 = 数据类型-样品-时间（2026-10-24）
            self.assertIn("原始", batch.name,
                          f"批量文件夹名要带内容标签：{batch.name}")
            self.assertTrue(batch.name.startswith("原始-fake_a等2张-"),
                            f"默认名格式不对：{batch.name}")
            target = batch / data_export.TXT_DIR_NAME / "fake_a.txt"
            self.assertTrue(target.is_file())
            lines = target.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], "# 2theta(deg)  intensity")
            data = np.loadtxt(str(target))
            np.testing.assert_allclose(data[:, 0], [0.5, 1.0, 8.5])
            np.testing.assert_allclose(data[:, 1], [1.0, 2.0, 3.0])
            self.assertTrue(
                (batch / data_export.TXT_DIR_NAME / "fake_b.txt").is_file())
            log = w.log_text.toPlainText()
            self.assertIn(f"开始导出：2 个文件 → {batch}", log)
            self.assertIn("导出完成：2 个文件", log)
            self.assertIn("用时", log, "完成行要带用时（多久出来）")
        finally:
            w.close()

    def test_single_export_is_one_flat_file(self):
        """单个数据集：光一个 txt，不包文件夹、不出总表（用户 2026-10-02）。

        弹窗结果里故意给 csv=True：单条的硬闸必须挡住总表。
        """
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: getattr(_dock(w, "1D", "data/fake_b.tif"),
                                    "last_tth", None) is not None))
            outdir = Path(tempfile.mkdtemp())
            with mock.patch.object(gui_export, "_build_export_dialog",
                                   return_value={"dir": outdir,
                                                 "suffix": ".txt",
                                                 "csv": True}):
                gui_export._run_export(w)
            target = _only_file(outdir)
            self.assertIn("fake_b", target.name)
            self.assertIn("原始", target.name)
            self.assertEqual(len(list(outdir.iterdir())), 1,
                             "单条导出不建文件夹、不出总表")
            self.assertIn("导出完成：1 个文件", w.log_text.toPlainText())
        finally:
            w.close()

    def test_bg_checkbox_with_empty_chain_stays_raw(self):
        """勾了"扣背景后的曲线"但三项全关：照实写 Raw + none，名字不加尾缀。

        链是空的 = 没做任何处理，文件里就是原始值——写 Processed 或给
        文件名加 `_处理产物` 都是谎报（样例导出时抓到：值逐点等于原始
        曲线，头里却写着 Processed）。
        """
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: getattr(_dock(w, "1D", "data/fake_b.tif"),
                                    "last_tth", None) is not None))
            outdir = Path(tempfile.mkdtemp())
            with mock.patch.object(gui_export, "_build_export_dialog",
                                   return_value={"dir": outdir,
                                                 "suffix": ".txt",
                                                 "csv": False, "bg": True}):
                gui_export._run_export(w)
            target = _only_file(outdir)
            self.assertNotIn("处理产物", target.name,
                             "没做处理的曲线不该标成处理产物")
            text = target.read_text()
            self.assertIn("# data category: Raw", text)
            self.assertIn("# chain: none", text)
        finally:
            w.close()

    def test_export_chi_suffix(self):
        w = create_window()
        try:
            self._two_1d_results(w)
            outdir = Path(tempfile.mkdtemp())
            with mock.patch.object(gui_export, "_build_export_dialog",
                                   return_value={"dir": outdir,
                                                 "suffix": ".chi",
                                                 "csv": False}):
                gui_export._run_export(w)
            batch = _only_batch_dir(outdir)
            self.assertTrue(
                (batch / data_export.TXT_DIR_NAME / "fake_b.chi").is_file())
        finally:
            w.close()

    def test_export_cancel_writes_nothing(self):
        w = create_window()
        try:
            self._two_1d_results(w)
            outdir = Path(tempfile.mkdtemp())
            with mock.patch.object(gui_export, "_build_export_dialog",
                                   return_value=None):
                gui_export._run_export(w)
            self.assertIn("已取消导出", w.log_text.toPlainText())
            self.assertEqual(list(outdir.iterdir()), [])
        finally:
            w.close()

    def test_export_without_1d_results_logs_hint(self):
        """勾了文件但没出过 1D：逐条提示先点 [1D]，不弹导出设置框。"""
        w = create_window()
        try:
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
            with mock.patch.object(gui_export, "_build_export_dialog") as dlg:
                gui_export._run_export(w)
            dlg.assert_not_called()
            log = w.log_text.toPlainText()
            self.assertIn("跳过 fake_a.tif：还没有 1D 曲线", log)
            self.assertIn("跳过 fake_b.tif：还没有 1D 曲线", log)
            self.assertIn("没有可导出的 1D 曲线", log)
        finally:
            w.close()

    def test_export_one_failure_does_not_abort_batch(self):
        """单个文件写盘失败只记日志，其余照常导出。"""
        w = create_window()
        try:
            self._two_1d_results(w)
            outdir = Path(tempfile.mkdtemp())
            real_write = gui_export._write_export
            calls = {"n": 0}

            def flaky_write(target, tth, intensity, chain="", **kw):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise OSError("磁盘已满")
                real_write(target, tth, intensity, chain, **kw)

            with mock.patch.object(gui_export, "_build_export_dialog",
                                   return_value={"dir": outdir,
                                                 "suffix": ".txt",
                                                 "csv": False}), \
                 mock.patch.object(gui_export, "_write_export",
                                   side_effect=flaky_write):
                gui_export._run_export(w)
            log = w.log_text.toPlainText()
            self.assertIn("导出失败 fake_a", log)
            self.assertIn("导出完成：1 个文件", log)
            batch = _only_batch_dir(outdir)
            self.assertEqual(
                sorted(p.name for p in (batch / data_export.TXT_DIR_NAME)
                       .iterdir()),
                ["fake_b.txt"])
        finally:
            w.close()


class TestExportCsv(unittest.TestCase):
    """全部数据总表：同网格直拼 / 网格不一致三路（交集/跳过/取消）。

    直调 _write_csv_summary 的用例给合成数据（3 元组也行——会补成
    Raw/none）；写盘细节（BOM、列明细、ASCII）另有 test_data_export.py。
    """

    def test_same_grid_csv(self):
        w = create_window()
        try:
            tth = np.array([0.5, 1.0, 8.5])
            results = [("a", tth, np.array([1.0, 2.0, 3.0])),
                       ("b", tth, np.array([4.0, 5.0, 6.0]))]
            outdir = Path(tempfile.mkdtemp())
            with mock.patch.object(gui_export, "_ask_csv_range") as ask:
                gui_export._write_csv_summary(w, results, outdir)
            ask.assert_not_called()   # 网格一致不打扰用户
            target = outdir / data_export.CSV_NAME
            self.assertEqual(target.read_bytes()[:3], b"\xef\xbb\xbf",
                             "CSV 带 BOM，Excel 打开不乱码")
            names, data = _summary_csv(target)
            self.assertEqual(names, ["2theta(deg)", "a", "b"])
            self.assertEqual(data.shape, (3, 3))
            np.testing.assert_allclose(data[:, 0], tth)
            np.testing.assert_allclose(data[:, 1], [1, 2, 3])
            self.assertIn("已生成全部数据总表（2 列）",
                          w.log_text.toPlainText())
        finally:
            w.close()

    def test_cut_columns_become_blank_cells(self):
        """裁剪过的列：那几格留空、写明 blank 说明行、不出现字面 nan。"""
        w = create_window()
        try:
            tth = np.array([1.0, 2.0, 3.0])
            results = [("a", tth, np.array([1.0, np.nan, 3.0]),
                        "bg=auto/win=0.2", data_export.CATEGORY_PROCESSED,
                        None),
                       ("b", tth, np.array([4.0, 5.0, 6.0]))]
            outdir = Path(tempfile.mkdtemp())
            with mock.patch.object(gui_export, "_ask_csv_range"):
                gui_export._write_csv_summary(w, results, outdir)
            text = (outdir / data_export.CSV_NAME).read_text(
                encoding="utf-8-sig")
            self.assertIn("# blank cells", text)
            data_rows = [ln for ln in text.splitlines()
                         if ln and not ln.startswith("#")][1:]   # 去掉列名行
            self.assertTrue(any("" in ln.split(",") for ln in data_rows),
                            "裁剪段在总表里应当是空单元格")
            self.assertNotIn("nan", text, "空值不能写成字面 nan")
            self.assertIn("1 列有裁剪区", w.log_text.toPlainText())
        finally:
            w.close()

    def test_mismatch_intersect_reinterpolates(self):
        w = create_window()
        try:
            results = [("a", np.array([1.0, 2.0, 3.0]),
                        np.array([1.0, 2.0, 3.0])),
                       ("b", np.array([2.0, 3.0, 4.0]),
                        np.array([2.0, 3.0, 4.0]))]
            outdir = Path(tempfile.mkdtemp())
            with mock.patch.object(gui_export, "_ask_csv_range",
                                   return_value="intersect"):
                gui_export._write_csv_summary(w, results, outdir)
            names, data = _summary_csv(outdir / data_export.CSV_NAME)
            # 公共交集 2~3° 按最大点数均匀取样，两列都重插到公共网格
            self.assertEqual(data.shape, (3, 3))
            np.testing.assert_allclose(data[:, 0], [2.0, 2.5, 3.0])
            np.testing.assert_allclose(data[:, 1], [2.0, 2.5, 3.0])
            np.testing.assert_allclose(data[:, 2], [2.0, 2.5, 3.0])
            self.assertIn("取公共交集", w.log_text.toPlainText())
        finally:
            w.close()

    def test_mismatch_skip_drops_different(self):
        w = create_window()
        try:
            results = [("a", np.array([1.0, 2.0, 3.0]),
                        np.array([1.0, 1.0, 1.0])),
                       ("b", np.array([0.0, 1.0]),
                        np.array([2.0, 2.0]))]
            outdir = Path(tempfile.mkdtemp())
            with mock.patch.object(gui_export, "_ask_csv_range",
                                   return_value="skip"):
                gui_export._write_csv_summary(w, results, outdir)
            names, data = _summary_csv(outdir / data_export.CSV_NAME)
            self.assertEqual(data.shape, (3, 2))   # 只留网格一致的文件
            self.assertIn("跳过 1 个", w.log_text.toPlainText())
        finally:
            w.close()

    def test_mismatch_cancel_writes_nothing(self):
        w = create_window()
        try:
            results = [("a", np.array([1.0, 2.0]), np.array([1.0, 1.0])),
                       ("b", np.array([3.0, 4.0]), np.array([2.0, 2.0]))]
            outdir = Path(tempfile.mkdtemp())
            with mock.patch.object(gui_export, "_ask_csv_range",
                                   return_value="cancel"):
                gui_export._write_csv_summary(w, results, outdir)
            self.assertIn("已取消全部数据总表", w.log_text.toPlainText())
            self.assertEqual(list(outdir.iterdir()), [])
        finally:
            w.close()

    def test_ask_csv_range_defaults_to_skip_when_not_visible(self):
        """窗口未显示（测试环境）：问询对话框不弹，直接按"跳过"处理。"""
        w = create_window()
        try:
            self.assertEqual(gui_export._ask_csv_range(w), "skip")
        finally:
            w.close()

    def test_export_flow_writes_csv_when_checked(self):
        """两条及以上且勾选总表 → 批量文件夹里 txt/ 与大集合一起出。"""
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: all(
                        getattr(_dock(w, "1D", p), "last_tth", None)
                        is not None
                        for p in ("data/fake_a.tif", "data/fake_b.tif"))))
            outdir = Path(tempfile.mkdtemp())
            with mock.patch.object(gui_export, "_build_export_dialog",
                                   return_value={"dir": outdir,
                                                 "suffix": ".txt",
                                                 "csv": True}):
                gui_export._run_export(w)
            batch = _only_batch_dir(outdir)
            self.assertTrue(
                (batch / data_export.TXT_DIR_NAME / "fake_b.txt").is_file())
            csv = batch / data_export.summary_filename(batch.name)
            names, data = _summary_csv(csv)
            self.assertEqual(names, ["2theta(deg)", "fake_a", "fake_b"])
            self.assertEqual(data.shape, (3, 3))
        finally:
            w.close()


class TestPoniImport(unittest.TestCase):
    """[加载参数]：读 pyFAI 几何 → 用户条目落盘 + 下拉框自动选中。

    _import_poni 真调 pyFAI.load（读临时 .poni 文件）；USER_CONFIG_PATH
    指向临时路径（同 TestSaveCalibConfig），收尾还原内存字典。
    """

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

    def _poni_file(self, name, **over):
        lines = {
            "Version": "2.1",
            "Detector": "Pilatus",
            "Detector_config":
                '{"pixel1": 0.0002, "pixel2": 0.0002,'
                ' "max_shape": [2048, 2048]}',
            "Distance": "1.5958",
            "Poni1": str(1045.2 * 200e-6),
            "Poni2": str(1022.0 * 200e-6),
            "Rot1": str(np.radians(-0.005)),
            "Rot2": str(np.radians(-0.163)),
            "Rot3": "0",
            "Wavelength": "1.223e-11",
        }
        lines.update(over)
        p = Path(self._tmpdir, name)
        p.write_text("\n".join(f"{k}: {v}" for k, v in lines.items())
                     + "\n", encoding="utf-8")
        return p

    def _click_poni(self, w, path):
        with mock.patch.object(QFileDialog, "getOpenFileName",
                               return_value=(str(path),
                                             "pyFAI 几何 (*.poni)")):
            w.findChild(QPushButton, "poni_btn").click()

    def test_import_adds_entry_and_selects(self):
        w = create_window()
        try:
            p = self._poni_file("mygeom.poni")
            self._click_poni(w, p)
            idx = w.config_combo.findData("mygeom")
            self.assertGreaterEqual(idx, 0)
            self.assertEqual(w.config_combo.currentIndex(), idx)
            self.assertEqual(w.config_name, "mygeom")
            cfg = w.config
            self.assertAlmostEqual(cfg["geometry"]["dist_m"], 1.5958)
            self.assertAlmostEqual(cfg["geometry"]["pixel_size_m"], 200e-6)
            self.assertAlmostEqual(cfg["geometry"]["rot1_deg"], -0.005)
            self.assertAlmostEqual(cfg["geometry"]["rot2_deg"], -0.163)
            # 束心 = pyFAI getFit2D 直射束落点（含倾斜修正，B ≠ PONI）
            import pyFAI
            f = pyFAI.load(str(p)).getFit2D()
            self.assertAlmostEqual(cfg["beam_center"][0], f.centerY,
                                   places=4)
            self.assertAlmostEqual(cfg["beam_center"][1], f.centerX,
                                   places=4)
            # 落盘真文件 + 日志
            self.assertTrue(self._path.is_file())
            self.assertIn("已导入 .poni → 配置条目 mygeom",
                          w.log_text.toPlainText())
        finally:
            w.close()

    def test_key_sanitized_and_collision_suffixed(self):
        w = create_window()
        try:
            self._click_poni(w, self._poni_file("123 weird name!.poni"))
            self.assertIn("poni_123_weird_name_", config_mod.USER_CONFIGS)
            # 与内置条目撞名：自动补 _poni1
            self._click_poni(w, self._poni_file("lmfp1_lab6.poni"))
            self.assertIn("lmfp1_lab6_poni1", config_mod.USER_CONFIGS)
            self.assertEqual(w.config_name, "lmfp1_lab6_poni1")
            self.assertIn("lmfp1_lab6_poni1", w.log_text.toPlainText())
        finally:
            w.close()

    def test_missing_fields_rejected(self):
        """旧版 v1 .poni 不带探测器 → 像素缺失 → 拒绝导入。"""
        w = create_window()
        try:
            v1 = Path(self._tmpdir, "old.poni")
            v1.write_text(
                "Distance: 1.5958\nPoni1: 0.20904\nPoni2: 0.2044\n"
                "Rot1: -8.7e-05\nRot2: -0.0028\nRot3: 0\n"
                "Wavelength: 1.223e-11\n", encoding="utf-8")
            self._click_poni(w, v1)
            log = w.log_text.toPlainText()
            self.assertIn("缺少几何字段", log)
            self.assertIn("pixel", log)
            self.assertNotIn("old", config_mod.USER_CONFIGS)
        finally:
            w.close()


class TestDeleteConfig(unittest.TestCase):
    """[删除] 几何配置条目：只删用户条目（内置置灰 + 处理函数双保险）、
    确认框、删后回退默认、磁盘同步。

    remove 走真 config.remove_user_config（写临时目录的真文件），
    USER_CONFIG_PATH 指向临时路径（不碰仓库真文件），收尾还原
    USER_CONFIGS / CONFIGS 内存字典。
    """

    ENTRY = {
        "label": "tmp 删除测试条目",
        "geometry": {"pixel_size_m": 200e-6, "wavelength_m": 1.223e-10,
                     "dist_m": 1.6, "poni1_m": 0.21, "poni2_m": 0.20,
                     "rot1_deg": 0.0, "rot2_deg": -0.16},
        "beam_center": (1022.0, 1022.3),
    }

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

    def _close(self, w):
        with mock.patch.object(gui_app, "_confirm_close",
                               return_value="discard"):
            w.close()

    def test_remove_user_config_unit(self):
        """config.remove_user_config：删除 / 再删无操作 / 内置报错 / 落盘。"""
        config_mod.save_user_config("tmp_del", dict(self.ENTRY))
        self.assertTrue(config_mod.remove_user_config("tmp_del"))
        self.assertNotIn("tmp_del", config_mod.USER_CONFIGS)
        self.assertNotIn("tmp_del", config_mod.CONFIGS)
        # 再删同一条：无操作返回 False
        self.assertFalse(config_mod.remove_user_config("tmp_del"))
        # 内置条目：人工登记注册表，报错拒绝
        with self.assertRaises(ValueError):
            config_mod.remove_user_config("lmfp1_lab6")
        # 磁盘文件同步：不含已删条目
        self.assertNotIn("tmp_del", self._path.read_text(encoding="utf-8"))

    def test_delete_button_removes_and_falls_back_to_default(self):
        """选中用户条目 → [删除] 确认 → 条目消失 + 回退默认 + 按钮置灰。"""
        config_mod.save_user_config("tmp_del", dict(self.ENTRY))
        w = create_window()
        try:
            w.show()
            idx = w.config_combo.findData("tmp_del")
            self.assertGreaterEqual(idx, 0)
            w.config_combo.setCurrentIndex(idx)   # 选中用户条目 → 按钮可用
            self.assertTrue(w.del_config_btn.isEnabled())
            with mock.patch.object(gui_app.QMessageBox, "question",
                                   return_value=QMessageBox.Yes) as ask:
                w.del_config_btn.click()
            ask.assert_called_once()
            # 注册表 + 下拉框 + 磁盘：条目消失
            self.assertNotIn("tmp_del", config_mod.USER_CONFIGS)
            self.assertEqual(w.config_combo.findData("tmp_del"), -1)
            self.assertNotIn("tmp_del", self._path.read_text(encoding="utf-8"))
            # 回退默认条目（内置）→ [删除] 重新置灰
            self.assertEqual(w.config_combo.currentData(),
                             config_mod.DEFAULT_CONFIG)
            self.assertFalse(w.del_config_btn.isEnabled())
            self.assertIn("已删除配置条目 tmp_del", w.log_text.toPlainText())
        finally:
            self._close(w)

    def test_delete_works_from_the_calibration_page(self):
        """校准页上就地选条目、就地删（[删除] 就住在那页，不用跨页）。

        2026-09-26 晚：这条是被用户问出来的——先前坞顶下拉框在校准页
        被置灰，删条目得"去分析页选中 → 回校准页点删除"，两页来回。
        """
        config_mod.save_user_config("tmp_del", dict(self.ENTRY))
        w = create_window()
        try:
            w.show()
            w.calib_btn.click()              # 进校准页（不勾文件也行）
            self.assertEqual(w.param_stack.currentIndex(),
                             w.PARAM_PAGES["校准"])
            self.assertTrue(w.config_combo.isEnabled())   # 校准页也能选
            idx = w.config_combo.findData("tmp_del")
            w.config_combo.setCurrentIndex(idx)
            self.assertTrue(w.del_config_btn.isEnabled())
            self.assertIn("tmp_del", w.del_config_btn.toolTip())   # 删哪条写明
            with mock.patch.object(gui_app.QMessageBox, "question",
                                   return_value=QMessageBox.Yes):
                w.del_config_btn.click()
            self.assertNotIn("tmp_del", config_mod.USER_CONFIGS)
            self.assertIn("已删除配置条目 tmp_del", w.log_text.toPlainText())
        finally:
            self._close(w)

    def test_builtin_selected_button_disabled_and_handler_refuses(self):
        """内置条目选中：按钮置灰；直调处理函数也不弹框、注册表不动。"""
        w = create_window()
        try:
            w.show()
            self.assertIn(w.config_combo.currentData(),
                          config_mod.BUILTIN_CONFIGS)
            self.assertFalse(w.del_config_btn.isEnabled())
            # 置灰是体验层，处理函数是安全层：直调也被拒
            with mock.patch.object(gui_app.QMessageBox, "question") as ask:
                gui_config_ops._delete_config(w)   # 处理函数归校准页
            ask.assert_not_called()
            self.assertIn("内置条目", w.log_text.toPlainText())
        finally:
            self._close(w)

    def test_delete_cancel_keeps_entry(self):
        """确认框选 No：条目原样保留，选中不变。"""
        config_mod.save_user_config("tmp_del", dict(self.ENTRY))
        w = create_window()
        try:
            w.show()
            w.config_combo.setCurrentIndex(w.config_combo.findData("tmp_del"))
            with mock.patch.object(gui_app.QMessageBox, "question",
                                   return_value=QMessageBox.No):
                w.del_config_btn.click()
            self.assertIn("tmp_del", config_mod.USER_CONFIGS)
            self.assertEqual(w.config_combo.currentData(), "tmp_del")
        finally:
            self._close(w)


class TestSavePoni(unittest.TestCase):
    """[保存参数]：当前选中配置 → 标准 .poni 文件（pyFAI Geometry.save）。

    真调 pyFAI 写读往返：写出的文件能被 pyFAI.load 读回，几何 7 字段
    与原配置一致（保存内容 = 距离/中心/像素/波长/倾斜角）。
    """

    def test_buttons_labelled_save_and_load(self):
        """作业规格按钮名：[加载参数] + [保存参数]。"""
        w = create_window()
        try:
            self.assertEqual(w.findChild(QPushButton, "poni_btn").text(),
                             "加载几何…")
            self.assertEqual(
                w.findChild(QPushButton, "save_poni_btn").text(),
                "保存几何…")
        finally:
            w.close()

    def test_save_writes_poni_roundtrip(self):
        w = create_window()
        try:
            out = Path(tempfile.mkdtemp()) / "sub" / "lmfp1_lab6.poni"
            with mock.patch.object(
                    QFileDialog, "getSaveFileName",
                    return_value=(str(out), "pyFAI 几何 (*.poni)")):
                w.findChild(QPushButton, "save_poni_btn").click()
            self.assertTrue(out.is_file())
            import pyFAI
            g = pyFAI.load(str(out))
            cfg = w.config["geometry"]
            self.assertAlmostEqual(g.dist, cfg["dist_m"])
            self.assertAlmostEqual(g.poni1, cfg["poni1_m"])
            self.assertAlmostEqual(g.poni2, cfg["poni2_m"])
            self.assertAlmostEqual(g.rot1, np.radians(cfg["rot1_deg"]))
            self.assertAlmostEqual(g.rot2, np.radians(cfg["rot2_deg"]))
            self.assertAlmostEqual(g.pixel1, cfg["pixel_size_m"])
            self.assertAlmostEqual(g.wavelength, cfg["wavelength_m"])
            log = w.log_text.toPlainText()
            self.assertIn("已保存几何参数", log)
            self.assertIn("距离", log)
            self.assertIn("中心点 poni1", log)
            self.assertIn("倾斜 rot1", log)
        finally:
            w.close()

    def test_save_cancel_writes_nothing(self):
        w = create_window()
        try:
            with mock.patch.object(QFileDialog, "getSaveFileName",
                                   return_value=("", "")):
                w.findChild(QPushButton, "save_poni_btn").click()
            self.assertNotIn("已保存几何参数", w.log_text.toPlainText())
        finally:
            w.close()

    def test_save_writes_the_current_config_not_the_entry(self):
        """校准页里按 [保存参数]：存的是**当前配置**（含手输），不是选中条目。

        用户 2026-09-28 第 2 条（2甲①）报的就是这个：手输改完当前配置再按
        保存，导出的还是那条旧条目的数字（"改完保存，文件里是旧的"）。
        现在与同栏的 [保存为配置] 一个口径。
        """
        w = create_window()
        try:
            out = Path(tempfile.mkdtemp()) / "current.poni"
            entry = w.config["geometry"]
            # 造一个与条目**明显不同**的当前配置（不弹对话框，直接进状态）
            w.calib_state = {
                "points": [], "results": [],
                "slots": {"current": None, "A": None, "B": None},
                "pinned": {"A": False, "B": False}, "next_slot": "A",
                "current_geom": dict(entry, dist_m=entry["dist_m"] + 0.02),
                "current_from": "手输", "current_metrics": None,
                "current_error": None, "custom": True, "counters": {}}
            with mock.patch.object(QFileDialog, "getSaveFileName",
                                   return_value=(str(out), "pyFAI 几何 (*.poni)")):
                w.findChild(QPushButton, "save_poni_btn").click()
            import pyFAI
            g = pyFAI.load(str(out))
            self.assertAlmostEqual(g.dist, entry["dist_m"] + 0.02,
                                   msg="该存当前配置（校准页表里那一列）")
            self.assertIn("校准页「当前配置」", w.log_text.toPlainText(),
                          "日志要说清存的是哪一份")
        finally:
            w.close()
