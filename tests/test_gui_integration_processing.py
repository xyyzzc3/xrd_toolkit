"""处理链、参数页、勾选/去重/拖入、寻峰显示

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


class TestBackgroundFromProduct(unittest.TestCase):
    """1D 产物条目也能扣背景（用户 2026-09-25 问起的那条）。

    1D 产物就是那条原始积分曲线，只是钉在某一份缓存上——所以它跟原始
    文件一个待遇（锚点/自动基线照用、也能进 [批量处理]），差别只在
    **结果挂在那一份 1D 的键下面**（键 = 那条 1D 键 + 设置哈希）：勾的是
    哪一条就扣哪一条，不按当前设置另算一条。扣背景产物本身仍然跳过
    （它已经是扣完的，再扣就是二次相减）。
    """

    def setUp(self):
        # 台账是模块级共享的临时缓存根：每个用例自己清一份，断言才数得准
        stage_cache.write_batches("bg", [])

    def _mine(self, path):
        """这个文件相关的扣背景批次（台账里按**源文件路径**找）。"""
        key = str(Path(path).resolve())
        return [b for b in stage_cache.list_batches("bg") if key in b["items"]]

    def _one_d_panel(self, w, values=(1.0, 2.0, 3.0)):
        """建一个"只有 1D 产物被勾着"的窗口，返回 (文件, 1D 键, 面板)。"""
        files = _tmp_files(1)
        w.add_files([str(p) for p in files])          # 导入默认不勾
        key1d = _store_product(w, files[0], values=values)
        w.refresh_groups()
        _group_by_text(w, "1D 产物").child(0).setCheckState(0, Qt.Checked)
        QApplication.processEvents()
        _open_view(w, "1D")                           # 产物条目出 1D 面板
        QApplication.processEvents()
        return files[0], key1d, w.plot_docks[f"1D|1d#{key1d}"]

    def _anchors_on(self, w, dock, path, xs):
        """把模式切到手动锚点并在给定 2θ 上放锚点（用面板自己的曲线取强度）。"""
        cb = w.params["背景扣除模式"]
        cb.setCurrentIndex(cb.findData("anchor"))
        w.params["背景窗口 (°)"].setValue(1.0)
        dock.params_snapshot = dict(
            dock.params_snapshot or {},
            **{"背景扣除模式": "anchor", "背景窗口 (°)": 1.0})
        tth = np.asarray(dock.last_tth, dtype=float)
        inten = np.asarray(dock.last_intensity, dtype=float)
        w.bg_anchors[str(path)] = [(x, float(np.interp(x, tth, inten)))
                                   for x in xs]

    def test_batch_background_subtracts_one_d_product(self):
        """勾 1D 产物 → [批量处理]：真扣一份，且挂在**那份 1D 的键**下面。"""
        w = create_window()
        try:
            path, key1d, dock = self._one_d_panel(w)
            tth = np.asarray(dock.last_tth, dtype=float)
            self._anchors_on(w, dock, path, [float(tth[0]), float(tth[-1])])
            w.proc_batch_btn.click()
            QApplication.processEvents()
            log = w.log_text.toPlainText()
            self.assertIn("批量处理完成：1/1", log)
            self.assertIn("其中 1 条来自 1D 产物", log)
            batches = self._mine(path)
            self.assertEqual(len(batches), 1)
            meta = batches[0]["items"][str(Path(path).resolve())]
            self.assertTrue(stage_cache.has_key("bg", meta["key"]),
                            "产物真落盘了")
            with np.load(stage_cache.CACHE_ROOT / "bg"
                         / f"{meta['key']}.npz") as data:
                stored = json.loads(str(data["meta"]))
            self.assertEqual(stored["base_key"], key1d,
                             "挂在勾的那份 1D 产物键下面（不按当前设置另算）")
            # 文件栏里长出一个扣背景分组，且子项可以整组勾上去比
            self.refresh_ok = _wait_until(
                lambda: _group_by_text(w, "处理") is not None)
            self.assertTrue(self.refresh_ok, "扣完要出现扣背景分组")
        finally:
            w.close()

    def test_batch_writes_a_per_file_recipe_and_later_panels_show_it(self):
        """批量扣完，之后点开**没开过**的文件也是"扣过"的样子 + 写出来处。

        用户 2026-09-27："给 a 扣完，然后应用到其余批量选图上，在双击查看
        这些选图时，在扣背景的参数栏可以显示这个图是以 a 的锚点为基准进行
        扣除的。按文件。"——原先设置只写进"当时开着"的面板快照，别的文件
        之后再点开显示的是"不扣"。
        """
        w = create_window()
        try:
            files = _tmp_files(3)
            w.add_files([str(p) for p in files])       # 导入默认不勾
            for f in files:
                _store_product(w, f)                   # 三份 1D 缓存
            w.refresh_groups()
            # 只勾 A、开 A 的面板（焦点 = A，批量的锚点基准就是它）
            raw = w.file_list.raw_group
            raw.child(0).setCheckState(0, Qt.Checked)
            QApplication.processEvents()
            _open_view(w, "1D")
            QApplication.processEvents()
            dockA = w.plot_docks.get(f"1D|{files[0]}")
            self.assertIsNotNone(dockA, "A 的面板该开出来")
            tth = np.asarray(dockA.last_tth, dtype=float)
            self._anchors_on(w, dockA, files[0], [float(tth[0]), float(tth[-1])])
            # 三张全勾上 → [批量处理]
            for i in range(raw.childCount()):
                raw.child(i).setCheckState(0, Qt.Checked)
            QApplication.processEvents()
            w.proc_batch_btn.click()
            QApplication.processEvents()
            self.assertIn("批量处理完成：3/3", w.log_text.toPlainText())
            recipes = getattr(w, "proc_recipes", {})
            for f in files:
                recipe = recipes.get(str(f))
                self.assertIsNotNone(recipe, f"{f.name} 该有配方")
                # 配方存的是**设置那套键**（与 services/recipes 同一形状），
                # 不再用显示控件的键名——这样"本图配方"与"命名配方"是一回事
                self.assertEqual(recipe["mode"], "anchor")
                self.assertIn("chain", recipe, "带一行给人看的摘要")
                self.assertEqual(recipe["anchor_source"],
                                 getattr(dockA, "panel_display", None),
                                 "来处 = 焦点文件（A）")
                self.assertTrue(w.bg_anchors.get(str(f)), "锚点也按文件存了")
            # 右键打开从没开过的 B（显式动作）：曲线是**原始**的，配方只写在
            # 处理页那行字上——用户 2026-09-27 晚："原始数据现在会被改变…
            # 按理来说原始数据应该打不开的，因为是原始 tif"。旧版这里断言
            # 的正是"点开就是扣过的样子"。
            item = next(raw.child(i) for i in range(raw.childCount())
                        if files[1].name in raw.child(i).text(0))
            gui_file_dock._open_entry_view(w, item, explicit=True)
            keyB = f"1D|{files[1]}"
            self.assertTrue(_wait_until(
                lambda: keyB in w.plot_docks
                and len(_axes(w, "1D", str(files[1])).lines) > 0))
            QApplication.processEvents()
            dockB = w.plot_docks[keyB]
            self.assertEqual((dockB.params_snapshot or {}).get("背景扣除模式"),
                             "off", "打开原始条目 = 原始曲线，不套配方")
            QApplication.processEvents()
            self.assertIn("本图配方", w.bg_prov_lbl.text(),
                          "配方只在处理页显示")
            self.assertIn(getattr(dockA, "panel_display", "").split(".")[0],
                          w.bg_prov_lbl.text(), "来处写清楚（以 A 的锚点为基准）")
        finally:
            w.close()

    def test_recipe_line_hides_the_anchor_note_without_anchors(self):
        """零锚点的配方行不挂"（锚点本图手点）"（2026-10-09）。

        自动基线 / 空扫都不靠锚点；重拍说明书背景图时看到"自动基线、
        窗口 0.2°（锚点本图手点）"的尾巴——零锚点却说"手点"，像动过点。
        """
        plain = {"chain": "自动基线、窗口 0.2°", "anchors": [],
                 "anchor_source": None}
        self.assertEqual(gui_views.recipe_text(plain),
                         "本图配方：自动基线、窗口 0.2°")
        hand = {"chain": "自动基线、锚点 2 个", "anchors": [(1.0, 2.0),
                                                            (3.0, 4.0)],
                "anchor_source": None}
        self.assertIn("（锚点本图手点）", gui_views.recipe_text(hand))
        borrowed = {"chain": "手动锚点、锚点 1 个", "anchors": [(1.0, 2.0)],
                    "anchor_source": "a.tif"}
        self.assertIn("（锚点来处：a.tif）", gui_views.recipe_text(borrowed))

    def test_batch_redoes_a_bg_product_from_its_source_curve(self):
        """处理产物在批里**按它底下的原始 1D 曲线重做**，不跳过、也不二次相减。

        用户 2026-09-28 第 3 条："套用别的配方的处理图不能二次处理了，修正"。
        旧口径是"处理产物跳过（幂等，不再来一遍）"——可用户要的是"换套参数
        重做"。现在的做法：输入换成它血缘上那条 1D 曲线（元数据里的 base_key
        → 台账兜底），所以既做得成、又不会在扣完的曲线上再扣一遍。
        """
        w = create_window()
        try:
            files = _tmp_files(1)
            w.add_files([str(p) for p in files])
            kw = _kw_of(w)
            # 真血缘：先有一条 1D 曲线，再有一条从它派生的处理产物
            tth = np.linspace(1.0, 8.0, 5)
            inten = np.array([9.0, 6.0, 3.0, 2.0, 1.0])
            stage_cache.store_1d(files[0], tth, inten, **kw)
            key = _store_product(w, files[0], kind="bg")
            stage_cache.record_batch(
                "bg", "redo-batch", label="扣背景 09-25 08:00（手动锚点）",
                items=[(files[0], key)], **kw, settings={"mode": "anchor"})
            w.refresh_groups()
            _group_by_text(w, "扣背景 09-25 08:00").child(0).setCheckState(
                0, Qt.Checked)
            QApplication.processEvents()
            # 给个编辑对象（否则先卡在"先点一张 1D 图"）
            _open_view(w, "1D")
            QApplication.processEvents()
            dock = next(d for k, d in w.plot_docks.items()
                        if k.startswith("1D|"))
            cb = w.params["背景扣除模式"]
            cb.setCurrentIndex(cb.findData("anchor"))
            dock.params_snapshot = dict(dock.params_snapshot or {},
                                        **{"背景扣除模式": "anchor"})
            w.bg_anchors[str(files[0])] = [(1.0, 9.0), (8.0, 1.0)]
            w.proc_batch_btn.click()
            QApplication.processEvents()
            log = w.log_text.toPlainText()
            self.assertIn("按它底下的原始 1D 曲线重做", log)
            self.assertIn("批量处理完成：1/1", log, "这条不再被跳过")
            self.assertIn("其中 1 条是处理产物", log)
            batches = self._mine(files[0])
            self.assertGreaterEqual(len(batches), 2,
                                    "重做要另记一批（新参数 = 新一批）")
            self.assertTrue(stage_cache.has_key("bg", batches[0]["items"][
                str(Path(files[0]).resolve())]["key"]), "产物真落盘")
        finally:
            w.close()

    def test_batch_background_dedupes_same_file(self):
        """同一文件既勾了原始又勾了 1D 产物：只扣一份，另一条记一行跳过。"""
        w = create_window()
        try:
            files = _tmp_files(1)
            add_checked(w, [str(p) for p in files])   # 原始也勾上
            _store_product(w, files[0])
            w.refresh_groups()
            recheck_all(w)   # 产物刚冒出来 → 上一轮的勾选已清零（第 8 条）
            _group_by_text(w, "1D 产物").child(0).setCheckState(0, Qt.Checked)
            QApplication.processEvents()
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                _open_view(w, "1D")
                QApplication.processEvents()
            dock = _dock(w, "1D", str(files[0]))
            self._anchors_on(w, dock, files[0], [0.5, 1.0])
            w.proc_batch_btn.click()
            QApplication.processEvents()
            log = w.log_text.toPlainText()
            self.assertIn("同一文件在批里只扣一份", log)
            self.assertIn("批量处理完成：1/2 个文件", log)
            self.assertEqual(
                sum(len(b["items"]) for b in self._mine(files[0])), 1)
        finally:
            w.close()


class TestProcessingChain(unittest.TestCase):
    """「处理」页三项（背景扣除 / 平滑 / 裁剪）端到端。

    用户 2026-09-25 定：三项都进产物、都能批量应用，处理完的那一批进
    「处理后」分组。这里守的是三个最要紧的性质：
      ① 改参数即重画（实时预览这一条不能断）；
      ② 裁剪让纵轴自动范围跳过那一段（这是用户要它的**唯一理由**）；
      ③ 产出的文件里那一段是空的、头里写明处理链（屏幕与文件同源）。
    """

    def setUp(self):
        stage_cache.write_batches("bg", [])

    def _panel(self, w):
        """开一张 1D 面板（窄尖峰曲线），返回 (路径, 面板)。"""
        files = _tmp_files(1)
        add_checked(w, [str(p) for p in files])
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_spiky_compute):
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, "1D", str(files[0])).lines) > 0))
        return files[0], w.plot_docks["1D|" + str(files[0])]

    def _proc_groups(self, w):
        """文件栏里「处理产物 …」组的标题列表（新的在前）。"""
        tree = w.file_list
        return [tree.topLevelItem(i).text(0)
                for i in range(tree.topLevelItemCount())
                if tree.topLevelItem(i).text(0).startswith("处理产物")]

    def test_same_settings_click_twice_is_one_group(self):
        """同一套参数重复点 [存成产物]：还是那一组（不冒"第 2 组"）。

        用户 2026-10-07："每次只有当前配置的一批进入文件栏"——点数 = 组数。
        下面 sleep 1.1 s 是跨过旧批次号里的秒级时间戳：改之前隔一秒再点
        必长一个"· 第 2 组"重复组，这条在老代码上会挂。
        """
        w = create_window()
        try:
            self._panel(w)
            w.params["平滑曲线"].setChecked(True)
            QApplication.processEvents()
            w.proc_keep_btn.click()
            QApplication.processEvents()
            self.assertEqual(len(self._proc_groups(w)), 1)
            time.sleep(1.1)
            w.proc_keep_btn.click()          # 同一套参数，再点
            QApplication.processEvents()
            groups = self._proc_groups(w)
            self.assertEqual(len(groups), 1, f"同一配置只该有一组：{groups}")
            self.assertNotIn("第 2 组", groups[0])
        finally:
            w.close()

    def test_different_settings_click_keeps_both_groups(self):
        """不同参数分别点击 → 各自进文件栏、并存（用户 2026-10-07：

        "如果不同参数多次点击，那就都进文件栏"）。"""
        w = create_window()
        try:
            self._panel(w)
            w.params["平滑曲线"].setChecked(True)
            w.params["平滑窗口 (°)"].setValue(0.2)
            QApplication.processEvents()
            w.proc_keep_btn.click()
            QApplication.processEvents()
            w.params["平滑窗口 (°)"].setValue(0.5)     # 换参数再点
            QApplication.processEvents()
            w.proc_keep_btn.click()
            QApplication.processEvents()
            groups = self._proc_groups(w)
            self.assertEqual(len(groups), 2, f"两套参数该两组：{groups}")
            self.assertTrue(any("0.2" in g for g in groups), groups)
            self.assertTrue(any("0.5" in g for g in groups), groups)
        finally:
            w.close()

    def test_tweaking_writes_nothing_until_a_click(self):
        """处理过程中调参不落任何产物：点按钮之前盘上与台账都是空的。

        用户 2026-10-07："用户处理过程中的产物不用保存进文件栏"。核查
        结论是调参 / 点锚点本来就不写盘——这条把它钉住，防以后有人
        "顺手缓存"进产物目录。
        """
        w = create_window()
        try:
            self._panel(w)
            bg_dir = Path(stage_cache.CACHE_ROOT) / "bg"
            before = set(bg_dir.glob("*.npz")) if bg_dir.exists() else set()
            combo = w.params["背景扣除模式"]
            combo.setCurrentIndex(combo.findData("auto"))
            QApplication.processEvents()
            for win in (0.2, 0.3, 0.5):
                w.params["背景窗口 (°)"].setValue(win)
                QApplication.processEvents()
            self.assertEqual(stage_cache.list_batches("bg"), [],
                             "没点按钮：台账不许有东西")
            after = set(bg_dir.glob("*.npz")) if bg_dir.exists() else set()
            self.assertEqual(after - before, set(),
                             "没点按钮：盘上不许新增处理产物（同进程里别的"
                             "用例可能先留了文件，这里只看增量）")
            w.proc_keep_btn.click()
            QApplication.processEvents()
            self.assertEqual(len(stage_cache.list_batches("bg")), 1,
                             "点一次才落一批")
        finally:
            w.close()

    def test_processing_defaults_match_the_users_picks(self):
        """处理页两处默认（用户 2026-10-07 拍板）：背景模式下拉=自动基线

        打头、默认仍"关闭"（开图先看原始曲线）；平滑方法默认 SG。"""
        w = create_window()
        try:
            combo = w.params["背景扣除模式"]
            self.assertEqual(
                [combo.itemData(i) for i in range(combo.count())],
                ["auto", "blank", "anchor", "off"],
                "自动基线放第一个、关闭垫底")
            self.assertEqual(combo.currentData(), "off", "默认模式仍是不扣")
            self.assertEqual(w.params["平滑方法"].currentData(), "savgol",
                             "平滑默认 SG")
        finally:
            w.close()

    def test_page_order_is_cut_then_bg_then_smooth(self):
        """处理页从上到下 = 裁剪 → 扣背景 → 平滑（用户 2026-10-07）。

        页面顺序只管操作顺手（先把压扁其余的巨峰裁掉、再估背景、最后
        平滑）；**计算顺序不受它影响**（apply_chain 固定 背景→平滑→
        裁剪），页顶那行灰字把这层区别写给用户看。
        """
        w = create_window()
        try:
            w.show()
            w.entrance_buttons["处理"].click()
            QApplication.processEvents()
            y_cut = w.params["裁剪区间"].mapTo(w, QPoint(0, 0)).y()
            y_bg = w.params["背景扣除模式"].mapTo(w, QPoint(0, 0)).y()
            y_smooth = w.params["平滑曲线"].mapTo(w, QPoint(0, 0)).y()
            self.assertLess(y_cut, y_bg, "裁剪该在最上")
            self.assertLess(y_bg, y_smooth, "扣背景该在平滑之前")
            labels = [lbl.text() for lbl in
                      w.param_stack.widget(2).findChildren(QLabel)]
            self.assertTrue(any("计算顺序固定" in t for t in labels),
                            f"页顶该说明计算顺序：{labels[:6]}")
        finally:
            w.close()

    def test_processing_page_points_at_double_click(self):
        """处理页顶部常显指路行：双击文件栏「1D 产物」= 把它选成处理对象。

        用户 2026-10-05："没选中面板就显示没选中——改为双击文件栏 1d 产出
        进行处理，让用户看明白"。这条钉住两件事：指路行在页面上；双击产物
        真的把它选成「编辑对象」（打开面板 + 焦点切过去）。
        """
        w = create_window()
        try:
            path, _dock = self._panel(w)          # 算出一份真 1D 产物
            # 指路行在「处理」页（param_stack 第 2 页）顶部
            labels = [lbl.text() for lbl in
                      w.param_stack.widget(2).findChildren(QLabel)]
            self.assertTrue(
                any("双击「1D 产物」" in t for t in labels),
                f"处理页该有指路行：{labels[:6]}")
            # 关掉刚才那张图（关闭即遗忘）→ 再从文件栏双击产物打开
            dock0 = w.plot_docks.get("1D|" + str(path))
            if dock0 is not None:
                dock0.close()
                QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            group = _group_by_text(w, "1D 产物")
            self.assertIsNotNone(group, "得先有产物组")
            leaf = group.child(0)
            gui_file_dock._open_entry_view(w, leaf)
            dock = w.plot_docks.get(w.focus_panel)
            self.assertIsNotNone(dock, "双击产物该打开一张面板并聚焦")
            self.assertIsNotNone(getattr(dock, "panel_source", None),
                                 "聚焦的该是产物面板")
            self.assertNotEqual(dock.panel_source.kind, gui_sources.RAW)
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def _enable(self, w, smooth=False, cut=False):
        """按界面上的路径打开处理项（改控件 → 实时重画）。"""
        if smooth:
            w.params["平滑曲线"].setChecked(True)
        if cut:
            w.params["裁剪区间"].setChecked(True)
        gui_views._refresh_proc(w)

    def test_smoothing_lowers_the_peak_on_screen(self):
        w = create_window()
        try:
            path, dock = self._panel(w)
            raw_peak = float(np.nanmax(dock.last_intensity))
            w.params["平滑窗口 (°)"].setValue(0.5)
            self._enable(w, smooth=True)
            drawn = np.asarray(_axes(w, "1D", str(path)).lines[0].get_ydata())
            self.assertEqual(len(drawn), 200, "平滑不该改变点数")
            self.assertLess(drawn.max(), raw_peak * 0.9,
                            "0.5° 窗口应当把 0.1° 宽的尖峰明显削矮")
            self.assertGreater(drawn.max(), raw_peak * 0.1, "别削没了")
        finally:
            w.close()

    def test_cut_leaves_a_gap_and_frees_the_y_axis(self):
        """裁剪：图上那段是空的，纵轴自动范围跟着跳过它（用户要的效果）。"""
        w = create_window()
        try:
            path, dock = self._panel(w)
            ax = _axes(w, "1D", str(path))
            self.assertGreater(ax.get_ylim()[1], 400, "先确认大峰压着纵轴")
            w.params["裁剪起点 (°)"].setValue(3.5)
            w.params["裁剪终点 (°)"].setValue(4.5)
            self._enable(w, cut=True)
            drawn = np.asarray(ax.lines[0].get_ydata())
            tth = np.asarray(ax.lines[0].get_xdata())
            inside = (tth >= 3.5) & (tth <= 4.5)
            self.assertTrue(inside.any(), "测试前提：区间里有采样点")
            self.assertTrue(np.isnan(drawn[inside]).all(), "区间内该是空的")
            self.assertTrue(np.isfinite(drawn[~inside]).all())
            self.assertLess(ax.get_ylim()[1], 100,
                            "纵轴该按剩下的数据自动定范围（大峰不再压扁它）")
            self.assertGreater(ax.get_ylim()[1], 5, "别把范围压没了")
        finally:
            w.close()

    def test_cut_does_not_uncheck_the_auto_y_axis(self):
        """裁剪不许把「纵轴自动」关掉（2026-10-08 真数据复现出的真凶）。

        焦点面板 = 这张图时，_draw_1d 会把自动算出的纵轴区间回填进置灰
        框；裸 setValue 触发即改即画钩子、重入一次 _draw_1d，内层 finally
        把 _setting_limits 旗标踩灭，外层 set_ylim 就被 _on_ylim_changed
        当成"用户动过纵轴"、悄悄取消勾选「纵轴自动」。用户口径：裁剪只
        改数据，显示开关是用户自己的选择。第二条重画是复现的关键一步
        （快照里被写成 False 后，下一次重画就走手动分支、纵轴卡在旧值）。
        """
        w = create_window()
        try:
            path, dock = self._panel(w)
            self.assertEqual(w.focus_panel, "1D|" + str(path), "前提：焦点=这张图")
            self.assertTrue(w.params["纵轴自动"].isChecked(), "前提：默认自动")
            w.params["裁剪起点 (°)"].setValue(3.5)
            w.params["裁剪终点 (°)"].setValue(4.5)
            self._enable(w, cut=True)
            QApplication.processEvents()
            self.assertTrue(w.params["纵轴自动"].isChecked(),
                            "裁剪后「纵轴自动」该保持勾选（程序不许替用户关掉）")
            self.assertTrue(dock.params_snapshot.get("纵轴自动", True),
                            "快照里也不许被写成手动")
            gui_views._refresh_proc(w)          # 再整幅重画一次（复现关键）
            QApplication.processEvents()
            self.assertTrue(w.params["纵轴自动"].isChecked(),
                            "第二次重画后「纵轴自动」还在（快照没被污染）")
            self.assertLess(_axes(w, "1D", str(path)).get_ylim()[1], 100,
                            "重画后纵轴仍是裁后数据的范围")
        finally:
            w.close()

    def test_cut_only_when_checked(self):
        """勾选框没勾时，起止框填了也不生效（三项都是可选项）。"""
        w = create_window()
        try:
            path, dock = self._panel(w)
            w.params["裁剪起点 (°)"].setValue(3.9)
            w.params["裁剪终点 (°)"].setValue(4.1)
            gui_views._refresh_proc(w)     # 没勾「裁剪区间」
            drawn = np.asarray(_axes(w, "1D", str(path)).lines[0].get_ydata())
            self.assertTrue(np.isfinite(drawn).all())
        finally:
            w.close()

    def test_multiple_cut_ranges(self):
        """多段裁剪：[添加] 攒清单、每段都挖空、[清空] 收回。"""
        w = create_window()
        try:
            path, dock = self._panel(w)
            w.params["裁剪起点 (°)"].setValue(1.5)
            w.params["裁剪终点 (°)"].setValue(2.0)
            _gui_app._on_cut_add(w)
            w.params["裁剪起点 (°)"].setValue(6.0)
            w.params["裁剪终点 (°)"].setValue(6.3)
            _gui_app._on_cut_add(w)
            QApplication.processEvents()
            drawn = np.asarray(_axes(w, "1D", str(path)).lines[0].get_ydata())
            tth = np.asarray(_axes(w, "1D", str(path)).lines[0].get_xdata())
            for lo, hi in ((1.5, 2.0), (6.0, 6.3)):
                band = (tth >= lo) & (tth <= hi)
                self.assertTrue(np.isnan(drawn[band]).all(),
                                f"{lo}–{hi}° 该是空的")
            self.assertIn("1.5–2°、6–6.3°", w.cut_list_lbl.text())
            self.assertEqual(dock.params_snapshot["裁剪区间"],
                             [(1.5, 2.0), (6.0, 6.3)],
                             "快照里存的是一串区间（面板各记各的）")
            _gui_app._on_cut_clear(w)
            self.assertEqual(w.cut_list, [])
            self.assertFalse(w.params["裁剪区间"].isChecked())
            drawn = np.asarray(_axes(w, "1D", str(path)).lines[0].get_ydata())
            self.assertTrue(np.isfinite(drawn).all(), "清空后不再有空洞")
        finally:
            w.close()

    def test_cut_add_refuses_inverted_and_duplicate(self):
        w = create_window()
        try:
            path, dock = self._panel(w)
            w.params["裁剪起点 (°)"].setValue(5.0)
            w.params["裁剪终点 (°)"].setValue(4.0)
            _gui_app._on_cut_add(w)
            self.assertIn("终点要大于起点", w.log_text.toPlainText())
            self.assertEqual(w.cut_list, [])
            w.params["裁剪终点 (°)"].setValue(5.5)
            _gui_app._on_cut_add(w)
            _gui_app._on_cut_add(w)      # 再加一次同样的
            self.assertEqual(w.cut_list, [(5.0, 5.5)], "同一段只留一份")
            self.assertIn("已经在清单里了", w.log_text.toPlainText())
        finally:
            w.close()

    def test_savgol_is_what_gets_stored(self):
        """切到 SG：链里带上方法与阶数，产物元数据也写明。"""
        w = create_window()
        try:
            path, dock = self._panel(w)
            w.params["平滑窗口 (°)"].setValue(0.5)
            self._enable(w, smooth=True)
            cb = w.params["平滑方法"]
            cb.setCurrentIndex(cb.findData("savgol"))
            self.assertTrue(w.params["平滑阶数"].isEnabled(), "SG 下阶数可编辑")
            gui_views._refresh_proc(w)
            box_peak = float(np.nanmax(
                np.asarray(_axes(w, "1D", str(path)).lines[0].get_ydata())))
            w.params["平滑阶数"].setValue(2)
            gui_views._refresh_proc(w)
            self.assertIn("smooth_method", process.chain_parts(
                gui_state._proc_settings(w, dock, str(path))))
            w.proc_batch_btn.click()
            QApplication.processEvents()
            self.assertIn("SG 0.5°/2阶", w.log_text.toPlainText())
            batch = stage_cache.list_batches("bg")[0]
            meta = stage_cache.meta_by_key(
                "bg", batch["items"][str(path.resolve())]["key"])
            self.assertIn("smooth=savgol/0.5°/p2", meta["chain"])
            # 滑动平均下阶数框置灰
            cb.setCurrentIndex(cb.findData("boxcar"))
            self.assertFalse(w.params["平滑阶数"].isEnabled())
            self.assertGreater(box_peak, 0)
        finally:
            w.close()

    def test_batch_writes_the_whole_chain_into_the_product(self):
        """[批量处理]：平滑 + 裁剪进产物，组名写清这一组做过什么。

        平滑方法 2026-10-07 起默认 SG（用户："平滑默认选择SG方法"），
        链的措辞跟着变成 "SG 0.5°/3阶"（原来是"平滑 0.5°"）。
        """
        w = create_window()
        try:
            path, dock = self._panel(w)
            w.params["平滑窗口 (°)"].setValue(0.5)
            w.params["裁剪起点 (°)"].setValue(3.9)
            w.params["裁剪终点 (°)"].setValue(4.1)
            self._enable(w, smooth=True, cut=True)
            w.proc_batch_btn.click()
            QApplication.processEvents()
            log = w.log_text.toPlainText()
            self.assertIn("批量处理完成", log)
            self.assertIn("SG 0.5°/3阶", log)
            self.assertIn("删 3.9–4.1°", log)
            group = _group_by_text(w, "处理产物")
            self.assertIsNotNone(group, "文件栏该长出「处理产物」分组")
            self.assertIn("SG 0.5°/3阶", group.text(0))
            # 产物本身：那一段是空的，元数据写明整条链
            batch = stage_cache.list_batches("bg")[0]
            meta = batch["items"][str(path.resolve())]
            with np.load(stage_cache.CACHE_ROOT / "bg"
                         / f"{meta['key']}.npz") as data:
                stored = np.asarray(data["intensity"], dtype=float)
                stored_meta = json.loads(str(data["meta"]))
            self.assertIn("smooth=savgol/0.5°", stored_meta["chain"])
            self.assertIn("cut=3.9–4.1°", stored_meta["chain"])
            self.assertGreater(int(np.isnan(stored).sum()), 0)
        finally:
            w.close()

    def test_export_skips_cut_points_and_notes_the_chain(self):
        """导出：裁剪点不写行、头里写明类别与处理链（纯 ASCII）。

        单个数据集 = 光一个文件（用户 2026-10-02）；这里用自定义名，
        文件头自己说清它是什么。总表里空单元格那条
        见 TestExportCsv.test_cut_columns_become_blank_cells。
        """
        w = create_window()
        try:
            path, dock = self._panel(w)
            w.params["裁剪起点 (°)"].setValue(3.5)
            w.params["裁剪终点 (°)"].setValue(4.5)
            self._enable(w, cut=True)
            outdir = Path(tempfile.mkdtemp())
            with mock.patch.object(gui_export, "_build_export_dialog",
                                   return_value={"dir": outdir,
                                                 "suffix": ".txt",
                                                 "csv": True, "bg": True,
                                                 "name": "cut_probe"}):
                gui_export._run_export(w)
            txt = outdir / "cut_probe.txt"
            self.assertTrue(txt.exists(), "该导出一份 txt")
            text = txt.read_text()
            self.assertIn("data category: Processed", text)
            self.assertIn("chain:", text, "头里要写明处理链")
            self.assertIn("cut=3.5-4.5deg", text)
            self.assertRegex(text, r"cut: \d+ points removed")
            for ln in [ln for ln in text.splitlines() if ln.startswith("#")]:
                self.assertTrue(ln.isascii(), ln)
            data = np.loadtxt(str(txt))
            self.assertTrue(np.isfinite(data[:, 1]).all(),
                            "文件里不该出现 nan 行")
            self.assertEqual(sorted(p.name for p in outdir.iterdir()),
                             ["cut_probe.txt"],
                             "单条导出不建文件夹、不出总表")
        finally:
            w.close()


class TestProcessingPageExits(unittest.TestCase):
    """「处理」页的两条出口 + 2θ 范围的键/数据一致性（用户 2026-09-28）。

    用户报的原话："处理阶段的 2theta 范围无效（检查所有 2theta 是否都能生效
    以及怎么生效）"、"处理单张 1d 数据应该在用户确定处理结果后出现在文件区的
    处理后数据中"。定下来的两条（2026-09-28 用户选择）：

      * 7乙：处理页也要有"按新 2θ 重算这张图"的出口（今名 [重算这张图]，
        2026-09-30 与 1D 页统一）——2θ 是**积分期**参数，
        处理链只吃手上那条曲线，处理页原先只有 [重画]（纯显示），改了 2θ
        没有任何地方能让数据跟上；
      * 第 4 条：[采用这份结果] 把单张的处理结果落成产物、进文件栏
        「处理后 …」（与 [批量处理] 落的是同一种东西）。

    外加一条真 bug 的回归：批量处理取原始曲线时**面板优先、无视当前 2θ**
    ——面板是按 1–8° 算的、用户改成 2–7° 再批量，产物会按 2–7° 的键写盘、
    里面装的却是 1–8° 的数据（键与数据对不上，以后按 2–7° 查还会命中它）。
    """

    def setUp(self):
        stage_cache.write_batches("bg", [])
        # 假积分**整条测试期间都挂着**：开图、[重算这张图]、[批量处理]
        # 都要走它。第一版只 mock 了开图那一小段，重算时打到真积分器上（拿
        # 空文件）+ 后台线程，看着像"按钮没用"——测试自己骗自己
        self._patcher = mock.patch.object(gui_plot_spawn, "_compute_integration",
                                          side_effect=self._fake)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    @staticmethod
    def _fake(path_str, geom, npt):
        """曲线端点跟着 geom 的 2θ 走（好验证"重算"真的换了范围）。"""
        lo = float(geom.get("tth_min_deg") or 0.5)
        hi = float(geom.get("tth_max_deg") or 8.5)
        tth = np.linspace(lo, hi, 200)
        y = 10.0 + 500.0 * np.exp(-0.5 * ((tth - (lo + hi) / 2) / 0.1) ** 2)
        return tth, y

    def _panel(self, w):
        """开一张 1D 面板（默认 2θ 1–8°）。"""
        files = _tmp_files(1)
        add_checked(w, [str(p) for p in files])
        _open_view(w, "1D")
        self.assertTrue(_wait_until(
            lambda: len(_axes(w, "1D", str(files[0])).lines) > 0))
        return files[0], w.plot_docks["1D|" + str(files[0])]

    def _mine(self, path):
        """台账里跟这个文件有关的那几批。"""
        out = []
        for node in stage_cache.list_batches("bg", prune=True):
            if str(Path(path).resolve()) in (node.get("items") or {}):
                out.append(node)
        return out

    def _anchor_mode(self, w, dock, path):
        """切到手动锚点并在数据两端各放一个锚点（背景扣得动）。"""
        cb = w.params["背景扣除模式"]
        cb.setCurrentIndex(cb.findData("anchor"))
        w.params["背景窗口 (°)"].setValue(1.0)
        tth = np.asarray(dock.last_tth, dtype=float)
        inten = np.asarray(dock.last_intensity, dtype=float)
        w.bg_anchors[str(path)] = [
            (float(tth[0]), float(inten[0])), (float(tth[-1]), float(inten[-1]))]
        gui_views._refresh_proc(w)
        QApplication.processEvents()

    def test_keep_this_result_writes_a_single_product(self):
        """合并按钮的"当前这张"分支：一个没勾时单张进文件栏（用户第 4 条）。

        2026-10-07 起 [采用这份结果]（后名 [存成产物]）与 [批量处理] 合并成
        一颗按钮——**勾了=批量、没勾=当前这张**；这条走"没勾"分支，勾选文件
        就变成那条批量测试的覆盖范围了（见 test_batch_*）。
        """
        w = create_window()
        try:
            path, dock = self._panel(w)
            self._anchor_mode(w, dock, path)
            for i in range(w.file_list.count()):     # 取消全部勾选
                w.file_list.item(i).setCheckState(Qt.Unchecked)
            QApplication.processEvents()
            w.proc_keep_btn.click()
            QApplication.processEvents()
            log = w.log_text.toPlainText()
            self.assertIn("已存成产物", log)
            batches = self._mine(path)
            self.assertEqual(len(batches), 1, "该落一条台账")
            self.assertEqual(batches[0]["note"], "1 个文件")
            meta = batches[0]["items"][str(Path(path).resolve())]
            self.assertTrue(stage_cache.has_key("bg", meta["key"]),
                            "产物真落盘")
            # 文件栏里立刻长出「处理后 …」分组，子项就是这一条
            self.assertTrue(_wait_until(
                lambda: _group_by_text(w, "处理产物") is not None),
                "文件栏里该出现处理产物分组")
            group = _group_by_text(w, "处理产物")
            self.assertEqual(group.childCount(), 1)
            self.assertIn(path.stem, group.child(0).text(0))
            # 与 [批量处理] 落的是同一种东西：双击能打开（读盘不重算）
            src = gui_sources.source_of(group.child(0))
            self.assertEqual(src.kind, gui_sources.BG)
            gui_views._open_product_panel(w, src)
            QApplication.processEvents()
            self.assertIn(f"1D|bg#{src.key}", w.plot_docks)
        finally:
            w.close()

    def test_keep_this_result_refuses_when_chain_is_off(self):
        """三项全关时 [采用这份结果] 只记一句、不写产物（存下来也是白存）。"""
        w = create_window()
        try:
            path, dock = self._panel(w)
            QApplication.processEvents()
            w.proc_keep_btn.click()
            QApplication.processEvents()
            self.assertIn("三项都关着", w.log_text.toPlainText())
            self.assertEqual(self._mine(path), [])
        finally:
            w.close()

    def test_a_second_batch_click_while_re_integrating_is_refused(self):
        """补算 1D 还没跑完时再点 [批量处理]：只记一句，不排第二拨。

        两拨回调各自接着跑一遍处理 → 产物与台账都会翻倍（同一份结果记两次）。
        所以宁可让用户等这一拨跑完再点（提示写明在重算）。
        """
        w = create_window()
        try:
            path, dock = self._panel(w)
            w.params["平滑曲线"].setChecked(True)
            w.params["平滑窗口 (°)"].setValue(0.2)
            w.params["2θ 下限 (°)"].setValue(2.0)
            w.params["2θ 上限 (°)"].setValue(7.0)

            def slow_fake(path_str, geom, npt):
                time.sleep(0.3)          # 让第一次点击的补算"还在路上"
                return TestProcessingPageExits._fake(path_str, geom, npt)

            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=slow_fake):
                w.proc_batch_btn.click()
                QApplication.processEvents()
                w.proc_batch_btn.click()      # 补算还没完：第二次点
                QApplication.processEvents()
                self.assertIn("还在按当前设置重算 1D",
                              w.log_text.toPlainText())
                self.assertTrue(_wait_until(
                    lambda: "批量处理完成：1/1" in w.log_text.toPlainText()))
            self.assertEqual(len(self._mine(path)), 1,
                             "只该落一拨产物（第二次点不许排队）")
        finally:
            w.close()

    def test_recompute_on_a_product_panel_drops_its_product_identity(self):
        """产物面板按新范围重算前，先摘掉"产物身份"（用户 2026-09-30）。

        用户的模型："本功能的范围**只影响本功能的产物**"——产物是冻结点。
        改之前实测（探针）：在「处理后」面板上按 [重算这张图]，原始文件会被
        按新范围重积分、**画进这张产物面板**，而面板仍记着自己是产物（后续
        [套用]/[采用这份结果]/[批量处理] 会按产物处理它）。同一类的第二处是
        1D 产物面板：画着新范围的曲线，[采用这份结果] 却回头去读旧范围那一份。
        现在：先换成它底下的 1D 曲线（同 [套用]），再按新范围重算。
        """
        w = create_window()
        try:
            path, dock = self._panel(w)
            w.params["平滑曲线"].setChecked(True)
            w.params["平滑窗口 (°)"].setValue(0.2)
            QApplication.processEvents()
            w.proc_keep_btn.click()             # 先落一条处理产物
            self.assertTrue(_wait_until(
                lambda: _group_by_text(w, "处理产物") is not None))
            src = gui_sources.source_of(
                _group_by_text(w, "处理产物").child(0))
            self.assertEqual(src.kind, gui_sources.BG)
            key = gui_views._open_product_panel(w, src)
            dockp = w.plot_docks[key]
            self.assertIsNotNone(dockp.panel_source, "前提：这是产物面板")
            self.assertAlmostEqual(
                float(np.asarray(dockp.last_tth)[-1]), 8.0, places=6,
                msg="前提：产物是按 1–8° 算的")
            # 改范围 → [重算这张图]（2026-10-07 起处理页没有这个出口了，
            # 重算统一回「1D」页；两边跑的是同一个 _apply_params）
            w.params["2θ 上限 (°)"].setValue(5.0)
            w.findChild(QPushButton, "apply_btn").click()
            self.assertTrue(_wait_until(
                lambda: getattr(dockp, "last_tth", None) is not None
                and abs(float(np.asarray(dockp.last_tth)[-1]) - 5.0) < 1e-6),
                "面板该按新范围重算")
            self.assertIsNone(dockp.panel_source, "产物身份必须摘掉")
            self.assertIn("已换成它底下的原始 1D 曲线",
                          w.log_text.toPlainText())
            # 旧的处理产物一个字节没动（本步的范围只影响本步产物）
            self.assertEqual(len(self._mine(path)), 1,
                             "旧的处理产物不该被这次重算动到")
        finally:
            w.close()

    def test_recalc_button_re_integrates_with_the_current_range(self):
        """[重算这张图]：坞顶填的新范围真的进积分（2026-10-07 起唯一的
        出口在「1D」页——处理页那颗已撤）。"""
        w = create_window()
        try:
            path, dock = self._panel(w)
            self.assertAlmostEqual(float(np.asarray(dock.last_tth)[-1]),
                                   8.0, places=6, msg="初始按默认范围（1–8°）算")
            w.params["2θ 上限 (°)"].setValue(5.0)
            w.findChild(QPushButton, "apply_btn").click()
            self.assertTrue(_wait_until(
                lambda: abs(float(np.asarray(w.plot_docks["1D|" + str(path)]
                                             .last_tth)[-1]) - 5.0) < 1e-6),
                "重算后的曲线端点该跟着新范围走")
            self.assertIn("重算", w.log_text.toPlainText())
        finally:
            w.close()

    def test_batch_re_integrates_when_the_range_changed(self):
        """改了 2θ 再 [批量处理]：按新范围补算 1D，再处理（用户 2026-09-30）。

        两层历史：① 旧版 `_curve_source` 只要面板开着就直接用它、**完全无视
        当前 2θ**——产物按新范围的键写盘、数据却是旧范围的（真 bug，2026-09-28
        修）；② 修法是"对不上就逐条跳过并说清原因"，于是改了范围点 [批量处理]
        只会得到一堆跳过（用户 2026-09-30 报的尾巴）。现在：缺的那批先在后台
        按当前设置补算 1D，算完自动接着处理。

        **底线不变**：这批的输入必须是"按当前设置算出来的那条曲线"（同一把
        键，见 _curve_source）——所以下面既验"补算真的按 2–7° 算"（端点），
        也验"面板里 1–8° 那条没被当成输入"。
        """
        w = create_window()
        try:
            path, dock = self._panel(w)
            w.params["平滑曲线"].setChecked(True)      # 一条不依赖锚点的链
            w.params["平滑窗口 (°)"].setValue(0.2)
            w.params["2θ 下限 (°)"].setValue(2.0)
            w.params["2θ 上限 (°)"].setValue(7.0)
            self.assertEqual(
                float(np.asarray(dock.last_tth)[-1]), 8.0,
                msg="前提：面板里那条是 1–8° 的旧范围曲线")
            w.proc_batch_btn.click()
            QApplication.processEvents()
            self.assertTrue(
                _wait_until(
                    lambda: "批量处理完成：1/1" in w.log_text.toPlainText()),
                "补算完该自动接着处理（不再逐条跳过）")
            log = w.log_text.toPlainText()
            self.assertIn("还没算过 1D", log)          # 补算那步说明了原因
            self.assertIn("1D 补算完成", log)
            # 产物落的是**新范围**那条曲线（不是面板里 1–8° 那份）
            batches = self._mine(path)
            self.assertEqual(len(batches), 1, "该落一条台账")
            key = batches[0]["items"][str(Path(path).resolve())]["key"]
            got = stage_cache.load_by_key("bg", key)
            self.assertIsNotNone(got, "产物真落盘")
            tth = np.asarray(got[0], dtype=float)
            self.assertAlmostEqual(float(tth[0]), 2.0, places=6)
            self.assertAlmostEqual(float(tth[-1]), 7.0, places=6)
            # 补算出来的 1D 也真落了盘（[对比]/[热图] 之后直接读它）
            self.assertTrue(stage_cache.has_1d(
                path, config=w.config_name,
                npt=int(w.params["输出点数"].value()),
                tth_min=2.0, tth_max=7.0))
        finally:
            w.close()


class TestPendingEdits(unittest.TestCase):
    """「未应用」灰字 + 按图记住（用户 2026-09-30 定的"丙"方案）。

    用户原话："参数页显示本图的参数，就是上方图片名称的本图参数。如果用户改了，
    添加一个灰字未应用来区分，切图再切回来保持"。三条：
      * 改了"要按一下才生效"的控件（只剩 2θ/点数——显示参数 2026-10-08 起
        全部即改即画、「图像」组与它的灰字已退休）→ 灰字亮，
        按了 [重算这张图] → 灭（它量的是"控件 vs 快照"的差）；
      * 切图再切回来：你填的值**还在**（按图记在面板上），灰字照旧亮着——
        改之前是回放直接把它冲掉、一声不响。
    """

    def _two_panels(self, w):
        """两张 1D 面板都算好，焦点停在第一张上（返回文件与两个面板键）。"""
        files = _tmp_files(2)
        add_checked(w, [str(p) for p in files])
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_compute):
            _open_view(w, "1D")
            self.assertTrue(_wait_until(lambda: all(
                getattr(w.plot_docks.get("1D|" + str(f)), "last_tth", None)
                is not None for f in files)), "两张 1D 图都该算好")
        ka, kb = ("1D|" + str(f) for f in files)
        gui_panel_state._set_focus(w, ka, "A")
        QApplication.processEvents()
        return files, ka, kb

    def test_data_group_marks_and_clears(self):
        """改 2θ → 灰字亮；[重算这张图] → 灭。"""
        w = create_window()
        try:
            self._two_panels(w)
            lbl = w.pending_labels["数据"]
            self.assertTrue(lbl.isHidden(), "一开始没有改动 → 灰字不该亮")
            w.params["2θ 上限 (°)"].setValue(7.0)
            QApplication.processEvents()
            self.assertFalse(lbl.isHidden(), "改了 2θ → 灰字该亮")
            w.findChild(QPushButton, "apply_btn").click()
            self.assertTrue(_wait_until(lambda: lbl.isHidden()),
                            "按了 [重算这张图] → 灰字该灭")
        finally:
            w.close()

    def test_edit_survives_a_focus_round_trip(self):
        """切图再切回来：用户填的值还在，灰字也还在（用户原话"保持"）。"""
        w = create_window()
        try:
            files, ka, kb = self._two_panels(w)
            box = w.params["2θ 上限 (°)"]
            box.setValue(7.0)
            QApplication.processEvents()
            gui_panel_state._set_focus(w, kb, "B")
            QApplication.processEvents()
            self.assertNotAlmostEqual(box.value(), 7.0,
                                      msg="切到 B：框里该显示 B 的参数")
            gui_panel_state._set_focus(w, ka, "A")
            QApplication.processEvents()
            self.assertAlmostEqual(box.value(), 7.0, places=6,
                                   msg="切回 A：用户填的值该还在")
            self.assertFalse(w.pending_labels["数据"].isHidden(),
                             "灰字也该跟着回来")
        finally:
            w.close()

    # （test_auto_owned_values_are_not_pending 2026-10-08 删除：它的代表键
    # 是「自动对比度」，随「图像」待办组改即改即画后不再有"未应用"这一态；
    # "程序回填不算用户改动"的护栏改挂在即改即画侧——见
    # TestImageDisplayLive 里自动对比度回填不触发重画/不算用户输入那两条。）

    def _plot_only(self, w, file, params):
        """只勾这一个文件、设参数、出图并等完成（TestParamSnapshot._plot_fake 的简版）。"""
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_compute):
            for i in range(w.file_list.count()):
                item = w.file_list.item(i)
                item.setCheckState(Qt.Checked
                                   if item.data(Qt.UserRole) == file
                                   else Qt.Unchecked)
            for name, value in params.items():
                w.params[name].setValue(value)
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: getattr(w.plot_docks.get("1D|" + file), "last_tth",
                                None) is not None))
        return f"1D|{file}"

    def test_values_used_to_plot_a_new_panel_are_not_pending(self):
        """"填数 → 出图"不算"上一张图的未应用改动"。

        用户填 2θ 出一个新范围的新图——那是**指令**，已经被用掉了；不区分的话
        切回原来那张图会凭空冒出一个"未应用"（TestParamSnapshot 那两条就是这么
        挂的：点回 A 该看到 A 自己的值，不是刚填给 B 的值）。
        """
        w = create_window()
        try:
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
            ka = self._plot_only(w, "data/fake_a.tif", {"2θ 下限 (°)": 1.5})
            self._plot_only(w, "data/fake_b.tif", {"2θ 下限 (°)": 2.5})
            gui_panel_state._set_focus(w, ka, "A")
            QApplication.processEvents()
            self.assertEqual(w.params["2θ 下限 (°)"].value(), 1.5,
                             "切回 A 该看到 A 自己的值，不是刚填给新图的值")
            self.assertTrue(w.pending_labels["数据"].isHidden(),
                            "刚填的值已经用掉了，不算 A 的未应用改动")
        finally:
            w.close()

    def test_without_an_edit_target_nothing_breaks(self):
        """没有编辑对象时改控件：不崩、也不亮（没有"本图"可言）。"""
        w = create_window()
        try:
            self.assertIsNone(w.focus_panel)
            w.params["2θ 上限 (°)"].setValue(7.0)
            QApplication.processEvents()
            self.assertTrue(w.pending_labels["数据"].isHidden())
        finally:
            w.close()


class TestFolderDrag(unittest.TestCase):
    """拖文件夹进窗口 = 扫描加入（与 [打开文件夹] 同一条逻辑）。"""

    def _tmp_folder(self, names):
        folder = tempfile.mkdtemp()
        for name in names:
            Path(folder, name).touch()
        return folder

    def test_drop_folder_scans_and_adds(self):
        folder = self._tmp_folder(("b.edf", "a.tif", "c.txt", "d.TIF"))
        w = create_window()
        try:
            _drop_event(w, [folder])
            names = [w.file_list.item(i).text()
                     for i in range(w.file_list.count())]
            self.assertEqual(names, ["a.tif", "b.edf", "d.TIF"])
            self.assertIn("文件夹扫描", w.log_text.toPlainText())
        finally:
            w.close()

    def test_drop_folder_twice_skips_duplicates(self):
        folder = self._tmp_folder(("a.tif",))
        w = create_window()
        try:
            _drop_event(w, [folder])
            _drop_event(w, [folder])
            self.assertEqual(w.file_list.count(), 1)
            self.assertIn("已跳过重复文件", w.log_text.toPlainText())
        finally:
            w.close()

    def test_drop_empty_folder_logs_hint(self):
        folder = tempfile.mkdtemp()
        w = create_window()
        try:
            _drop_event(w, [folder])
            self.assertIn("文件夹里没有支持的数据文件",
                          w.log_text.toPlainText())
            self.assertEqual(w.file_list.count(), 0)
        finally:
            w.close()

    def test_drop_mixed_files_and_dir(self):
        folder = self._tmp_folder(("a.tif",))
        w = create_window()
        try:
            abs_b = str(Path("data/fake_b.tif").resolve())
            _drop_event(w, [abs_b, "/tmp/x.txt", folder])
            names = [w.file_list.item(i).text()
                     for i in range(w.file_list.count())]
            self.assertEqual(names, ["fake_b.tif", "a.tif"])
        finally:
            w.close()

    def test_drag_enter_accepts_dir(self):
        folder = tempfile.mkdtemp()
        w = create_window()
        try:
            ev = _drop_event(w, [folder], kind="enter")
            self.assertTrue(ev.isAccepted(), "拖文件夹应接住（扫描加入）")
        finally:
            w.close()


class TestDuplicateFiles(unittest.TestCase):
    """同一文件再次加入：弹窗问覆盖 / 改名 / 取消，列表不重名。"""

    def test_duplicate_overwrite_keeps_single_entry(self):
        w = create_window()
        try:
            add_checked(w, ["data/fake_a.tif"])
            with mock.patch.object(gui_file_dock, "_ask_duplicate",
                                   return_value="overwrite"):
                add_checked(w, ["data/fake_a.tif"])   # 再拖入一次
            self.assertEqual(w.file_list.count(), 1)
            self.assertEqual(w.file_list.item(0).text(), "fake_a.tif")
            self.assertEqual(w.file_list.item(0).checkState(), Qt.Checked)
            self.assertIn("保留原条目", w.log_text.toPlainText())
            # 第二次加入没有新条目 → 不会再有第二条"已添加"
            self.assertEqual(w.log_text.toPlainText().count("已添加"), 1)
        finally:
            w.close()

    def test_duplicate_entries_compute_once_with_a_note(self):
        """重复条目出 1D：**只算一遍、图每条一张**（批尾从缓存补画）。

        用户 2026-10-07："重复的原始数据生成产物时会只生成一个，在日志里
        提到这个事情"。**2026-10-09**："去重，减少工作量"——只算一遍；
        **2026-10-10**："出 1d 图变少，这个怎么还没修复"——上一版把重复
        条目的面板也省了（勾 5 条出 4 张），现在面板照开、只是不排积分，
        批收尾时从产物缓存补画（毫秒级）。断言：两张面板都出来、积分
        只跑了一次。
        """
        p = _tmp_files(1, prefix="dup1_")[0]     # 真文件：产物要真指纹
        w = create_window()
        try:
            add_checked(w, [str(p)])
            with mock.patch.object(gui_file_dock, "_ask_duplicate",
                                   return_value="rename"):
                add_checked(w, [str(p)])                # 同一文件第二条条目
            self.assertEqual(w.file_list.count(), 2)
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len([k for k in w.plot_docks
                                 if k.startswith("1D")]) == 2
                    and all(len(gui_panel_state._content(w.plot_docks[k])
                                .axes_1d.lines) > 0
                            for k in w.plot_docks if k.startswith("1D"))),
                    "两条条目的图都该画出来（重复的那张从缓存补）")
            log = w.log_text.toPlainText()
            self.assertIn("是同一文件的重复条目", log)
            self.assertIn("只算一遍", log)
            self.assertEqual(log.count("开始积分"), 1,
                             "同一份数据只算一遍（补画不算）")
        finally:
            w.close()

    def test_duplicate_note_repeats_on_the_batch_summary(self):
        """带重复条目的批：只算一遍（8 张），批完成那行提一句（2026-10-09）。

        用户实测：勾选 83 条 → 同一份数据算了 83 遍、文件栏 81 条，
        盯着日志尾巴问"这个昨天不是也做了吗"——原来那句打在按 [1D] 的
        那一刻，几十行进度之前，没人会往回翻。现在：重复条目整条跳过
        （批的张数 = 不重复的文件数），批完成行再提一句。
        """
        files = [str(p) for p in _tmp_files(10)]
        w = create_window()
        try:
            add_checked(w, files[:9])
            with mock.patch.object(gui_file_dock, "_ask_duplicate",
                                   return_value="rename"):
                add_checked(w, files[:1])       # 第 10 条 = 同一文件第二条
            self.assertEqual(w.file_list.count(), 10)
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: "批完成" in w.log_text.toPlainText(), 60000),
                    "批要跑到收尾那行")     # 10 条勾选 → 9 份数据 > 合并阈值
            log = w.log_text.toPlainText()
            self.assertIn("批完成：9 张", log)      # 10 条勾选、9 份数据
            self.assertIn("是同一文件的重复条目", log)
            self.assertEqual(log.count("开始积分"), 9,
                             "重复的那条不该再算一遍")
        finally:
            w.close()

    def test_duplicate_entries_share_one_product_in_batch(self):
        """重复条目走 [存成产物]（批）：同一文件只扣一份，日志写明。

        1D 那条路 2026-10-07 有测试（上面那条）；批量这条当时只读过
        代码、没搭起门禁（2026-10-09 补）。**必须用真文件**：1D 键按
        文件指纹算，data/fake_* 这类假路径连键都建不出来（老会话
        "门禁搭不起来"踩的就是这个）。同一文件两条条目（改名重复）→
        开一项平滑（三项全关时批不写产物）→ 批处理只产一份，日志有
        "同一文件在批里只扣一份"和跳过计数。
        """
        p = _tmp_files(1)[0]        # 真文件：产物与 1D 键都要文件指纹
        w = create_window()
        try:
            add_checked(w, [str(p)])
            with mock.patch.object(gui_file_dock, "_ask_duplicate",
                                   return_value="rename"):
                add_checked(w, [str(p)])
            self.assertEqual(w.file_list.count(), 2)
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: sum(1 for k, d in w.plot_docks.items()
                                if k.startswith("1D")
                                and getattr(d, "last_tth", None)
                                is not None) >= 1), "面板算完再切")
                # 出图即产 1D 产物 → 既有规则"新产物 = 勾选清零"（甲）；
                # 重新勾上「原始数据」整组（两条重复条目都在里面；
                # 它恒在顶行——groups() 只列产物组，不含它）
                raw = w.file_list.topLevelItem(0)
                self.assertTrue(raw.text(0).startswith("原始数据"))
                raw.setCheckState(Qt.Checked)
                QApplication.processEvents()
                w.params["平滑曲线"].setChecked(True)   # 三项都关时批不写产物
                QApplication.processEvents()
                w.proc_save_btn.click()
                self.assertTrue(_wait_until(
                    lambda: "批量处理完成" in w.log_text.toPlainText()))
            log = w.log_text.toPlainText()
            self.assertIn("同一文件在批里只扣一份", log)
            self.assertIn("批量处理完成：1/2 个文件", log, "只产一份")
            self.assertIn("跳过 1 个", log)
        finally:
            w.close()

    def test_duplicate_rename_gets_numbered_name(self):
        """改名输入框预填编号名（测试环境直接返回预填值）。"""
        w = create_window()
        try:
            add_checked(w, ["data/fake_a.tif"])
            with mock.patch.object(gui_file_dock, "_ask_duplicate",
                                   return_value="rename"):
                add_checked(w, ["data/fake_a.tif"])
            self.assertEqual(w.file_list.count(), 2)
            self.assertEqual(w.file_list.item(0).text(), "fake_a.tif")
            self.assertEqual(w.file_list.item(1).text(), "fake_a (1).tif")
            # 两条条目指向同一个文件；新条目勾上，标签 = 2 个
            self.assertEqual(w.file_list.item(0).data(Qt.UserRole),
                             w.file_list.item(1).data(Qt.UserRole))
            self.assertEqual(w.file_list.item(1).checkState(), Qt.Checked)
            self.assertEqual(w.file_label.text(), "已勾选 2 项")
            # 第三次加入 → 编号继续涨，不与 (1) 撞名
            with mock.patch.object(gui_file_dock, "_ask_duplicate",
                                   return_value="rename"):
                add_checked(w, ["data/fake_a.tif"])
            texts = [w.file_list.item(i).text()
                     for i in range(w.file_list.count())]
            self.assertEqual(texts, ["fake_a.tif", "fake_a (1).tif",
                                     "fake_a (2).tif"])
        finally:
            w.close()

    def test_duplicate_cancel_skips(self):
        w = create_window()
        try:
            add_checked(w, ["data/fake_a.tif"])
            with mock.patch.object(gui_file_dock, "_ask_duplicate",
                                   return_value="cancel"):
                add_checked(w, ["data/fake_a.tif"])
            self.assertEqual(w.file_list.count(), 1)
            self.assertIn("跳过", w.log_text.toPlainText())
        finally:
            w.close()

    def test_rename_dialog_custom_name_used(self):
        """改名输入框：用户自己输入的名字生效。"""
        w = create_window()
        try:
            add_checked(w, ["data/fake_a.tif"])
            with mock.patch.object(gui_file_dock, "_ask_duplicate",
                                   return_value="rename"), \
                 mock.patch.object(gui_file_dock, "_ask_rename",
                                   return_value="我的数据.tif"):
                add_checked(w, ["data/fake_a.tif"])
            self.assertEqual(w.file_list.count(), 2)
            self.assertEqual(w.file_list.item(1).text(), "我的数据.tif")
            # 显示名随便改，但底层仍指向同一个文件
            self.assertEqual(w.file_list.item(0).data(Qt.UserRole),
                             w.file_list.item(1).data(Qt.UserRole))
            self.assertEqual(w.file_list.item(1).checkState(), Qt.Checked)
            self.assertIn("改名加入：我的数据.tif", w.log_text.toPlainText())
        finally:
            w.close()

    def test_rename_dialog_cancel_skips(self):
        """改名输入框取消（返回 None）→ 这次不加。"""
        w = create_window()
        try:
            add_checked(w, ["data/fake_a.tif"])
            with mock.patch.object(gui_file_dock, "_ask_duplicate",
                                   return_value="rename"), \
                 mock.patch.object(gui_file_dock, "_ask_rename",
                                   return_value=None):
                add_checked(w, ["data/fake_a.tif"])
            self.assertEqual(w.file_list.count(), 1)
            self.assertIn("已跳过重复文件", w.log_text.toPlainText())
        finally:
            w.close()

    def test_rename_dialog_rejects_taken_name(self):
        """输入的名字已被占用 → 要求换一个，输入框重新弹。"""
        w = create_window()
        try:
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
            with mock.patch.object(gui_file_dock, "_ask_duplicate",
                                   return_value="rename"), \
                 mock.patch.object(gui_file_dock, "_ask_rename",
                                   side_effect=["fake_b.tif", "自定义.tif"]):
                add_checked(w, ["data/fake_a.tif"])
            self.assertEqual(w.file_list.count(), 3)
            self.assertEqual(w.file_list.item(2).text(), "自定义.tif")
            self.assertIn("显示名 fake_b.tif 已被占用", w.log_text.toPlainText())
        finally:
            w.close()

    def test_ask_rename_hidden_window_defaults_default_name(self):
        """窗口没显示（测试环境）→ 不弹模态框，返回预填的默认名。"""
        w = create_window()
        try:
            self.assertEqual(gui_file_dock._ask_rename(w, "xxx (1).tif"),
                             "xxx (1).tif")
        finally:
            w.close()

    def test_ask_duplicate_hidden_window_defaults_overwrite(self):
        """窗口没显示（测试环境）→ 不弹模态框，默认覆盖（防挂死）。"""
        w = create_window()
        try:
            self.assertEqual(gui_file_dock._ask_duplicate(w, "fake_a.tif"),
                             "overwrite")
        finally:
            w.close()

    def test_renamed_entry_gets_own_panel(self):
        """改名条目：**算一遍、图各一张**（批尾从缓存补画，互不顶掉）。

        2026-09-24 的老坑：两条条目抢同一个键、只出一张图（后来者把先开
        的那张当过期刷掉）——所以键补显示名区分、"互不当过期"。2026-10-09
        "去重"只去掉**计算**；2026-10-10 用户"出 1d 图变少"：重复条目的
        面板照开、批收尾从缓存补画。**真文件**（产物要真指纹才存得下、
        补画才命中缓存，假路径会退化成重算）。
        """
        p = _tmp_files(1, prefix="ren_")[0]
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, [str(p)])
                with mock.patch.object(gui_file_dock, "_ask_duplicate",
                                       return_value="rename"):
                    add_checked(w, [str(p)])
                _open_view(w, "1D")
                key2 = f"1D|{p}|{p.name.split('.')[0]} (1).tif"
                self.assertTrue(_wait_until(
                    lambda: len([k for k in w.plot_docks
                                 if k.startswith("1D")]) == 2
                    and all(len(gui_panel_state._content(w.plot_docks[k])
                                .axes_1d.lines) > 0
                            for k in w.plot_docks if k.startswith("1D"))),
                    "两条条目的图都该出来（第二条从缓存补画）")
                self.assertIn(key2, w.plot_docks,
                              "改名条目的面板有自己的键（视图|路径|显示名）")
                log = w.log_text.toPlainText()
                self.assertEqual(log.count("开始积分"), 1,
                                 "计算只跑一次（补画命中缓存）")
                self.assertIn("只算一遍", log)
            self.assertEqual(len(w.plot_docks), 2)
            self.assertEqual(w.plot_docks[key2].windowTitle(),
                             f"1D_{p.name.split('.')[0]} (1).tif")
            self.assertNotIn("已忽略", w.log_text.toPlainText())
        finally:
            w.close()


class TestCollectGeometry(unittest.TestCase):
    """参数面板 → 积分几何：输入覆盖配置值，PONI/倾斜取配置条目。"""

    def test_geometry_comes_from_the_selected_config(self):
        """几何一律取配置条目，面板上没有能改它的控件。

        （换条目才是改几何的入口——分析页只读后没有"面板覆盖配置"
        这条隐藏通路了。2026-09-26 起那三行只读字段也撤了：读数只在
        坞顶"几何配置"行的悬停提示里显示，压根没有可写的几何控件。）
        """
        w = create_window()
        try:
            cfg = w.config["geometry"]
            geom = gui_panel_state._collect_geometry(w)
            self.assertAlmostEqual(geom["dist_m"], cfg["dist_m"])
            self.assertAlmostEqual(geom["pixel_size_m"],
                                   cfg["pixel_size_m"])
            self.assertAlmostEqual(geom["wavelength_m"], cfg["wavelength_m"])
            self.assertEqual(geom["poni1_m"], cfg["poni1_m"])
            self.assertEqual(geom["rot1_deg"], cfg["rot1_deg"])
            # 读数只在提示里，参数坞里没有几何输入控件
            for gone in ("初始距离 (mm)", "像素尺寸 (µm)", "波长 (Å)"):
                self.assertNotIn(gone, w.params)
            # 2θ 上下限 = 积分设置，仍随面板走（改了就进 geom）
            self.assertAlmostEqual(geom["tth_min_deg"], 1.0)
            self.assertAlmostEqual(geom["tth_max_deg"], 8.0)
            w.params["2θ 下限 (°)"].setValue(2.5)
            w.params["2θ 上限 (°)"].setValue(7.5)
            geom2 = gui_panel_state._collect_geometry(w)
            self.assertAlmostEqual(geom2["tth_min_deg"], 2.5)
            self.assertAlmostEqual(geom2["tth_max_deg"], 7.5)
        finally:
            w.close()


class TestTthRangeFlowsToCompute(unittest.TestCase):
    """2θ 上下限进计算链路：点 [1D]/[应用] 时 geom 带当前区间值。

    引擎按区间重积分由 TestIntegrate1DRange（test_partial_ring.py）
    兜底；这里只验 GUI 把参数传对。
    """

    def test_range_values_reach_compute_and_rerun(self):
        w = create_window()
        try:
            calls = []
            def recorder(path_str, geom, npt):
                calls.append(dict(geom))
                return np.array([1.0, 2.0, 3.0]), np.array([1.0, 2.0, 3.0])
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=recorder):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(lambda: len(calls) >= 1))
                self.assertAlmostEqual(calls[0]["tth_min_deg"], 1.0)
                self.assertAlmostEqual(calls[0]["tth_max_deg"], 8.0)
                # 先等首轮计算的焦点回放落地（回放把快照写回参数坞，
                # 会覆盖"在飞"的控件改动）——真实用户也是等出图后
                # 再改参数
                self.assertTrue(
                    _wait_until(lambda: w.focus_panel == "1D|data/fake_b.tif"),
                    "出图后焦点面板应就位")
                w.params["2θ 下限 (°)"].setValue(2.5)
                w.params["2θ 上限 (°)"].setValue(7.5)
                w.findChild(QPushButton, "apply_btn").click()
                self.assertTrue(
                    _wait_until(lambda: len(calls) >= 2),
                    f"[应用] 未触发重算；calls 区间值 = "
                    f"{[c['tth_min_deg'] for c in calls]}；"
                    f"日志 = {w.log_text.toPlainText().splitlines()[-2:]}")
                self.assertAlmostEqual(calls[-1]["tth_min_deg"], 2.5)
                self.assertAlmostEqual(calls[-1]["tth_max_deg"], 7.5)
        finally:
            w.close()


class TestAutoContrast(unittest.TestCase):
    """自动对比度：勾回自动 = 按编辑对象（焦点图）重算并填回；没
    焦点图填占位默认；[恢复默认] 也会重算。对比度只对 2D/剖面 有
    意义：焦点是 1D/对比面板时不读文件（占位默认，防主线程卡）。"""

    def _focus_2d(self, w):
        """开一张 fake_a 的 2D 面板并等它画出来 → 编辑对象 = 该面板。"""
        fake_image = np.linspace(0, 1000, 3000).reshape(50, 60)
        with mock.patch.object(gui_plot_workers, "load_diffraction_image",
                               return_value=fake_image):
            add_checked(w, ["data/fake_a.tif"])
            _open_view(w, "2D")
            self.assertTrue(_wait_until(lambda: len(gui_panel_state._content(
                _dock(w, "2D", "data/fake_a.tif")).axes_2d.images) > 0))
        QTest.mouseClick(gui_panel_state._content(_dock(w, "2D", "data/fake_a.tif")),
                         Qt.LeftButton)
        return w.focus_panel

    def _focus_1d(self, w):
        """画一张 fake_a 的 1D 图并等它完成 → 编辑对象 = 该面板。"""
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, ["data/fake_a.tif"])
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0))
        return w.focus_panel

    def test_recheck_auto_restores_computed_values(self):
        """改完手动对比度再勾回自动 → 按焦点图重算，数字跳回自动值。"""
        w = create_window()
        try:
            self._focus_2d(w)   # 编辑对象 = fake_a 的 2D 面板
            auto = w.params["自动对比度"]
            auto.setChecked(False)   # 手动模式
            w.params["对比度下限"].setValue(5.0)
            w.params["对比度上限"].setValue(200.0)
            fake_image = np.linspace(0, 1000, 3000).reshape(50, 60)
            w._image_cache.clear()   # 2D 面板已缓存其图：清掉让重算走 mock
            with mock.patch.object(gui_state_ranges, "load_diffraction_image",
                                   return_value=fake_image):
                auto.setChecked(True)   # 勾回自动 → 按焦点图重算
            self.assertAlmostEqual(
                w.params["对比度下限"].value(),
                float(np.percentile(fake_image, 1.0)), places=1)
            self.assertAlmostEqual(
                w.params["对比度上限"].value(),
                float(np.percentile(fake_image, 99.9)), places=1)
            self.assertFalse(w.params["对比度下限"].isEnabled())
            self.assertFalse(w.params["对比度上限"].isEnabled())
            self.assertIn("自动对比度：按 fake_a.tif 算得",
                          w.log_text.toPlainText())
        finally:
            w.close()

    def test_recheck_auto_without_focus_fills_defaults(self):
        """没有编辑对象时勾回自动 → 恢复占位默认值。"""
        w = create_window()
        try:
            auto = w.params["自动对比度"]
            auto.setChecked(False)
            w.params["对比度下限"].setValue(5.0)
            w.params["对比度上限"].setValue(200.0)
            auto.setChecked(True)
            self.assertEqual(w.params["对比度下限"].value(), 1.0)
            self.assertEqual(w.params["对比度上限"].value(), 100000.0)
            self.assertIn("没有编辑对象", w.log_text.toPlainText())
        finally:
            w.close()

    def test_reset_image_recomputes_auto(self):
        """[恢复默认]：勾回自动并重算（不是填死的占位值），角度归零。"""
        w = create_window()
        try:
            self._focus_2d(w)
            w.params["自动对比度"].setChecked(False)
            w.params["对比度下限"].setValue(5.0)
            w.params["剖面角度 (°)"].setValue(45.0)
            fake_image = np.linspace(0, 1000, 3000).reshape(50, 60)
            w._image_cache.clear()   # 2D 面板已缓存其图：清掉让重算走 mock
            with mock.patch.object(gui_state_ranges, "load_diffraction_image",
                                   return_value=fake_image):
                w.findChild(QPushButton, "reset_image_btn").click()
            self.assertTrue(w.params["自动对比度"].isChecked())
            self.assertAlmostEqual(
                w.params["对比度下限"].value(),
                float(np.percentile(fake_image, 1.0)), places=1)
            self.assertEqual(w.params["剖面角度 (°)"].value(), 0.0)
            self.assertIn("图像参数已恢复默认", w.log_text.toPlainText())
        finally:
            w.close()

    def test_auto_contrast_load_failure_falls_back(self):
        """焦点图读取失败且面板没有内存图 → 填占位默认并记日志，不崩溃。

        2026-10-08 起勾回自动会立刻重画（即改即画）：2D 面板**手里有图**
        时按内存图算值、与画图口径一致（那种情形不再走读文件这条路）；
        这条测的是连内存图都没有（读取本来就失败）的情形。
        """
        w = create_window()
        try:
            add_checked(w, ["data/fake_a.tif"])
            with mock.patch.object(gui_plot_workers, "load_diffraction_image",
                                   side_effect=OSError("boom")):
                _open_view(w, "2D")
                self.assertTrue(_wait_until(
                    lambda: "读取失败" in w.log_text.toPlainText()))
            # 焦点指到这张没算出来的 2D 面板
            QTest.mouseClick(gui_panel_state._content(
                _dock(w, "2D", "data/fake_a.tif")), Qt.LeftButton)
            w.params["自动对比度"].setChecked(False)
            w.params["对比度下限"].setValue(5.0)
            w._image_cache.clear()
            with mock.patch.object(gui_state_ranges, "load_diffraction_image",
                                   side_effect=OSError("boom")):
                w.params["自动对比度"].setChecked(True)
            self.assertEqual(w.params["对比度下限"].value(), 1.0)
            self.assertEqual(w.params["对比度上限"].value(), 100000.0)
            self.assertIn("读取 fake_a.tif 失败", w.log_text.toPlainText())
        finally:
            w.close()

    def test_1d_focus_does_not_read_file(self):
        """焦点是 1D 面板时勾回自动 → 不读文件（防主线程解码大图），
        填占位默认。修前每次点焦点都读一遍文件，批量出图会卡。"""
        w = create_window()
        try:
            self._focus_1d(w)
            w.params["自动对比度"].setChecked(False)
            w.params["对比度下限"].setValue(5.0)
            with mock.patch.object(gui_state_ranges, "load_diffraction_image",
                                   side_effect=OSError("boom")) as load:
                w.params["自动对比度"].setChecked(True)
            self.assertFalse(load.called)
            self.assertEqual(w.params["对比度下限"].value(), 1.0)
            self.assertEqual(w.params["对比度上限"].value(), 100000.0)
            self.assertIn("编辑对象不是 2D/剖面 视图",
                          w.log_text.toPlainText())
        finally:
            w.close()


class TestParamSnapshot(unittest.TestCase):
    """点哪张图，参数面板就显示哪张图作图时的参数（快照）。

    快照在开图/重算时拍下：改参数 → [应用] → 快照跟着刷新。
    重复点同一面板不冲掉正在改的值。
    """

    def _plot_fake(self, w, file, params):
        """只勾这一个文件、改参数、出图并等完成 → 返回面板键。"""
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, [file])
            # 只留这一个对号（其余取消），点作图按钮才只画它
            for i in range(w.file_list.count()):
                item = w.file_list.item(i)
                item.setCheckState(
                    Qt.Checked if item.data(Qt.UserRole) == file
                    else Qt.Unchecked)
            for name, value in params.items():
                w.params[name].setValue(value)
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, "1D", file).lines) > 0))
        return f"1D|{file}"

    def test_switch_focus_shows_each_panels_params(self):
        """点 A 显示 A 的参数，点 B 显示 B 的参数（不是只能改不能看）。

        拿 2θ 下限当"每张面板各记各的"的记号（原来用测距那格，2026-09-26
        撤了那三个只读字段，快照里也没有它了）。
        """
        w = create_window()
        try:
            key_a = self._plot_fake(
                w, "data/fake_a.tif",
                {"2θ 下限 (°)": 1.5, "2θ 上限 (°)": 7.0,
                 "输出点数": 2500})
            self.assertEqual(w.focus_panel, key_a)
            # 完成时焦点 = A → 参数坞已回放 A 的快照
            self.assertEqual(w.params["2θ 下限 (°)"].value(), 1.5)
            self.assertEqual(w.params["2θ 上限 (°)"].value(), 7.0)
            self.assertEqual(w.params["输出点数"].value(), 2500)
            # 打开 B（参数不同）→ 焦点切 B，参数显示 B 的值
            key_b = self._plot_fake(
                w, "data/fake_b.tif",
                {"2θ 下限 (°)": 2.5, "2θ 上限 (°)": 8.0})
            self.assertEqual(w.focus_panel, key_b)
            self.assertEqual(w.params["2θ 下限 (°)"].value(), 2.5)
            self.assertEqual(w.params["2θ 上限 (°)"].value(), 8.0)
            # 点回 A → 参数显示 A 的值（能看）
            QTest.mouseClick(gui_panel_state._content(_dock(w, "1D", "data/fake_a.tif")),
                             Qt.LeftButton)
            self.assertEqual(w.focus_panel, key_a)
            self.assertEqual(w.params["2θ 下限 (°)"].value(), 1.5)
            self.assertEqual(w.params["2θ 上限 (°)"].value(), 7.0)
            self.assertEqual(w.params["输出点数"].value(), 2500)
            # 配置条目也回放（注册表只有一条，至少不串台）
            self.assertEqual(w.config_combo.currentData(), w.config_name)
        finally:
            w.close()

    def test_apply_updates_snapshot(self):
        """改参数 [应用] 后快照刷新：切走再切回显示新值。"""
        w = create_window()
        try:
            self._plot_fake(w, "data/fake_a.tif",
                            {"2θ 下限 (°)": 1.5})
            w.params["2θ 下限 (°)"].setValue(2.5)
            done_before = w.log_text.toPlainText().count("积分完成")
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                w.findChild(QPushButton, "apply_btn").click()
                self.assertTrue(_wait_until(
                    lambda: w.log_text.toPlainText().count("积分完成")
                    > done_before))
            # 快照已随 [应用] 同步刷新
            self.assertEqual(
                _dock(w, "1D", "data/fake_a.tif").params_snapshot
                ["2θ 下限 (°)"], 2.5)
            # 切到 B 再切回 A → 显示新值 2.5
            self._plot_fake(w, "data/fake_b.tif",
                            {"2θ 下限 (°)": 4.0})
            QTest.mouseClick(gui_panel_state._content(_dock(w, "1D", "data/fake_a.tif")),
                             Qt.LeftButton)
            self.assertEqual(w.params["2θ 下限 (°)"].value(), 2.5)
        finally:
            w.close()

    def test_same_panel_reclick_keeps_edits(self):
        """重复点同一面板不冲掉正在改的值（跳过快照回放）。"""
        w = create_window()
        try:
            key = self._plot_fake(w, "data/fake_a.tif",
                                  {"2θ 下限 (°)": 1.5})
            w.params["2θ 下限 (°)"].setValue(3.5)   # 未应用的编辑
            QTest.mouseClick(gui_panel_state._content(_dock(w, "1D", "data/fake_a.tif")),
                             Qt.LeftButton)
            self.assertEqual(w.focus_panel, key)
            self.assertEqual(w.params["2θ 下限 (°)"].value(), 3.5)
        finally:
            w.close()

    def test_manual_contrast_restored_from_snapshot(self):
        """手动对比度也进快照：A 保持自动，B 图像 [应用] 改手动，
        切来切去各自回放各自的状态。"""
        w = create_window()
        try:
            # A 作图时自动对比度勾着（默认）
            self._plot_fake(w, "data/fake_a.tif",
                            {"2θ 下限 (°)": 1.5})
            # B 新开（默认自动开）；对 B 关自动、改值（即改即画，2026-10-08）
            # → B 的快照 = 手动模式 + 这组值
            self._plot_fake(w, "data/fake_b.tif",
                            {"2θ 下限 (°)": 2.5})
            w.params["自动对比度"].setChecked(False)
            w.params["对比度下限"].setValue(123.0)
            w.params["对比度上限"].setValue(456.0)
            QApplication.processEvents()
            # 切回 A：A 的快照里自动是勾着的 → 自动开、输入框置灰
            QTest.mouseClick(gui_panel_state._content(_dock(w, "1D", "data/fake_a.tif")),
                             Qt.LeftButton)
            self.assertTrue(w.params["自动对比度"].isChecked())
            self.assertFalse(w.params["对比度下限"].isEnabled())
            # 再切回 B：手动模式 + 123/456 原样回放
            QTest.mouseClick(gui_panel_state._content(_dock(w, "1D", "data/fake_b.tif")),
                             Qt.LeftButton)
            self.assertFalse(w.params["自动对比度"].isChecked())
            self.assertEqual(w.params["对比度下限"].value(), 123.0)
            self.assertEqual(w.params["对比度上限"].value(), 456.0)
        finally:
            w.close()


class TestPanelToggles(unittest.TestCase):
    """[文件][参数][日志] 收起开关：收起给图让地方，再点展开；
    用标题栏 × 关坞也会同步弹起按钮。"""

    def setUp(self):
        self.w = create_window()
        self.w.show()   # 坞的 isVisible 依赖主窗口可见，offscreen 也能 show

    def tearDown(self):
        # 窗口可见时关窗会弹"未保存图"模态框（offscreen 没人点会挂住）；
        # 关窗询问已有专门测试（TestClosePrompt），这里直接当作选"不保存"
        with mock.patch.object(gui_app, "_confirm_close",
                               return_value="discard"):
            self.w.close()

    def test_default_docks_visible(self):
        """初始状态：[文件][日志] 可见，**[参数] 收起**（开局什么都没选）。

        用户 2026-09-25 定："开界面时上面什么都不选、右边参数栏是隐藏的"
        ——参数坞跟入口走（点入口才露出来），见 TestEntrances。
        """
        for name in ("文件", "日志"):
            self.assertTrue(self.w.panel_toggles[name].isChecked(), name)
        self.assertTrue(self.w.file_dock.isVisible())
        self.assertTrue(self.w.log_dock.isVisible())
        self.assertFalse(self.w.panel_toggles["参数"].isChecked())
        self.assertFalse(self.w.param_dock.isVisible())

    def test_toggle_hides_and_restores_each_dock(self):
        """点开关收起、再点展开，三个坞各试一遍（从各自当前状态起步）。"""
        for name, dock in (("文件", self.w.file_dock),
                           ("参数", self.w.param_dock),
                           ("日志", self.w.log_dock)):
            if not dock.isVisible():             # [参数] 开局就是收起的
                self.w.panel_toggles[name].click()
            self.assertTrue(dock.isVisible())
            self.w.panel_toggles[name].click()   # 收起
            self.assertFalse(dock.isVisible())
            self.assertFalse(self.w.panel_toggles[name].isChecked())
            self.w.panel_toggles[name].click()   # 展开
            self.assertTrue(dock.isVisible())
            self.assertTrue(self.w.panel_toggles[name].isChecked())

    def test_close_button_syncs_toggle(self):
        """点标题栏 × 关坞 → 按钮自动弹起；再点按钮还能展开。"""
        self.w.panel_toggles["参数"].click()   # 先展开（开局收起）
        self.assertTrue(self.w.param_dock.isVisible())
        self.w.param_dock.close()   # 等价于标题栏 ×
        self.assertFalse(self.w.param_dock.isVisible())
        self.assertFalse(self.w.panel_toggles["参数"].isChecked())
        self.w.panel_toggles["参数"].click()
        self.assertTrue(self.w.param_dock.isVisible())

    def test_all_hidden_plot_still_works(self):
        """全收起来只剩绘图区，照常出图（开关不影响作图流程）。"""
        for name, dock in (("文件", self.w.file_dock),
                           ("参数", self.w.param_dock),
                           ("日志", self.w.log_dock)):
            if dock.isVisible():
                self.w.panel_toggles[name].click()
        for dock in (self.w.file_dock, self.w.param_dock, self.w.log_dock):
            self.assertFalse(dock.isVisible())
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(self.w, ["data/fake_b.tif"])
            _open_view(self.w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(self.w, "1D", "data/fake_b.tif").lines)
                > 0))
        self.assertEqual(self.w.focus_panel, "1D|data/fake_b.tif")


class Test1dDisplay(unittest.TestCase):
    """1D 显示参数（图像参数组里的子分组）：对数纵轴 + 纵轴范围。
    改后点图像 [应用] 用已有数据重画（不重算）；新开面板一律从
    默认（对数关/自动开）起步。"""

    def _plot_fake_b(self, w):
        """画 fake_b 的 1D 图并等完成 → 返回坐标轴（强度 1/2/3）。"""
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, ["data/fake_b.tif"])
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0))
        return _axes(w, "1D", "data/fake_b.tif")

    def test_log_y_applied_at_plot(self):
        """勾上对数纵轴 → 即改即画 → 曲线画在对数刻度上。"""
        w = create_window()
        try:
            ax = self._plot_fake_b(w)
            w.params["对数纵轴"].setChecked(True)
            QApplication.processEvents()
            self.assertEqual(ax.get_yscale(), "log")
        finally:
            w.close()

    def test_manual_ylim_applied(self):
        """取消纵轴自动、手填范围 → 图像 [应用] → 曲线按手填范围画。"""
        w = create_window()
        try:
            ax = self._plot_fake_b(w)
            w.params["纵轴自动"].setChecked(False)
            w.params["纵轴下限"].setValue(10.0)
            w.params["纵轴上限"].setValue(500.0)
            QApplication.processEvents()   # 即改即画：不用再按 [应用]
            self.assertEqual(ax.get_ylim(), (10.0, 500.0))
            # 手动模式下输入框可用
            self.assertTrue(w.params["纵轴下限"].isEnabled())
        finally:
            w.close()

    def test_auto_ylim_uses_percentiles(self):
        """默认自动 → 纵轴范围 = 曲线强度的 1%/99.9% 分位。"""
        w = create_window()
        try:
            ax = self._plot_fake_b(w)   # 假强度 [1.0, 2.0, 3.0]
            ylo, yhi = ax.get_ylim()
            self.assertAlmostEqual(
                ylo, float(np.percentile([1.0, 2.0, 3.0], 1.0)), places=2)
            self.assertAlmostEqual(
                yhi, float(np.percentile([1.0, 2.0, 3.0], 99.9)), places=2)
            # 自动模式下输入框置灰（只读展示正在用的区间）
            self.assertFalse(w.params["纵轴下限"].isEnabled())
        finally:
            w.close()

    def test_log_y_off_goes_back_to_linear(self):
        """对数 → 取消勾选 → 曲线回到线性刻度（反方向也即改即画）。"""
        w = create_window()
        try:
            ax = self._plot_fake_b(w)
            w.params["对数纵轴"].setChecked(True)
            QApplication.processEvents()
            self.assertEqual(ax.get_yscale(), "log")
            w.params["对数纵轴"].setChecked(False)
            QApplication.processEvents()
            self.assertEqual(ax.get_yscale(), "linear")
        finally:
            w.close()

    def test_reset_restores_1d_display(self):
        """[恢复默认] 把 1D 显示参数也复位（对数关、纵轴自动开）。"""
        w = create_window()
        try:
            w.params["对数纵轴"].setChecked(True)
            w.params["纵轴自动"].setChecked(False)
            w.findChild(QPushButton, "reset_image_btn").click()
            self.assertFalse(w.params["对数纵轴"].isChecked())
            self.assertTrue(w.params["纵轴自动"].isChecked())
            self.assertFalse(w.params["纵轴下限"].isEnabled())
        finally:
            w.close()


class TestLongNames(unittest.TestCase):
    """长名称不破坏参数坞布局（两个长度问题）：
    1. 编辑对象标题超长 → 单行缩略、不撑宽参数区，悬停看全名；
    2. 几何配置灰色说明 → 单行缩略，不再换行被下一行控件遮住。
    """

    LONG = "data/very_long_sample_name_that_would_stretch_the_panel.tif"

    def _plot_long(self, w):
        """画一张长文件名的 1D 图并等完成（编辑对象 = 该面板）。"""
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, [self.LONG])
            _open_view(w, "1D")
            self.assertTrue(_wait_until(
                lambda: len(_axes(w, "1D", self.LONG).lines) > 0))

    def test_long_focus_title_does_not_stretch_param_dock(self):
        w = create_window()
        try:
            w.show()
            width_before = w.param_dock.width()
            self._plot_long(w)
            title = f"1D_{Path(self.LONG).name}"
            # 完整文字还在（text() 返回逻辑文字），悬停有全名提示
            self.assertEqual(w.focus_label.text(), f"编辑对象：{title}")
            self.assertEqual(w.focus_label.toolTip(), f"编辑对象：{title}")
            # 参数坞不被撑宽（修前会被标题文字宽度撑到 ~600px）
            self.assertLessEqual(w.param_dock.width(), width_before + 5)
            # 显示层面是单行（不换行占两行）
            one_line = w.focus_label.fontMetrics().height()
            self.assertLessEqual(w.focus_label.height(), one_line + 4)
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_config_combo_carries_full_label_as_tooltip(self):
        """几何配置说明行已删除（与用户讨论定稿：不单独占一行）——
        完整批次备注改为挂在配置下拉框各项的悬停提示上
        （悬停闭合下拉框 = 显示当前项的提示）。"""
        w = create_window()
        try:
            w.show()
            combo = w.config_combo
            idx = combo.currentIndex()
            self.assertEqual(combo.itemData(idx), gui_app.DEFAULT_CONFIG)
            self.assertEqual(
                combo.itemData(idx, Qt.ToolTipRole),
                gui_app.CONFIGS[gui_app.DEFAULT_CONFIG]["label"])
        finally:
            w.close()   # 没画图，关窗不会弹询问
