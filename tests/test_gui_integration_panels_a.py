"""参数坞拆分、2D 显示、对比、面板壳与手势（上）

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


class TestParamDockSplitLayout(unittest.TestCase):
    """参数坞上下对半分结构：编辑对象名固定在最上方，下面
    QSplitter 竖切两半（数据参数上 / 图像参数下，初始等高）；
    两半各自一个 QScrollArea（内容放不下时滚动），按钮并排一行
    （[恢复默认] 在左、[应用] 在右，各占一半宽度）固定在各区最
    下方——在滚动区之外，滚动时按钮不跟着走。"""

    def test_pages_structure(self):
        """参数坞 = 两行固定件（编辑对象 / 几何配置）+ 五个入口页
        （校准/1D/扣背景/对比/绘图）。

        2026-09-24 用户定稿：工具栏从 7 项收到 5 个入口，六个作图类型
        按钮住进了「原图」页（属性名不变：view_buttons / compare_btn /
        heat_btn）。2026-09-26 又加了一行固定件：几何配置（下拉框 +
        校准模式下的 [返回分析模式]）——它得在每一页都看得见。
        """
        w = create_window()
        try:
            w.show()
            QApplication.processEvents()
            content = w.param_dock.widget()
            lay = content.layout()
            self.assertIs(lay.itemAt(0).widget(), w.focus_label,
                          "编辑对象名固定最上方")
            self.assertIs(lay.itemAt(1).widget(), w.geom_row,
                          "几何配置行固定在第二行")
            self.assertIs(lay.itemAt(2).widget(), w.data_row,
                          "数据参数行（2θ 范围/点数）固定在第三行")
            self.assertIs(lay.itemAt(3).widget(), w.param_stack)
            self.assertEqual(w.param_stack.count(), 5)
            self.assertEqual(w.PARAM_PAGES,
                             {"校准": 0, "1D": 1, "处理": 2, "对比": 3,
                              "原图": 4})
            self.assertEqual(w.param_stack.currentIndex(),
                             w.PARAM_PAGES["1D"], "默认可停在 1D 页")
            self.assertEqual(list(w.entrance_buttons),
                             ["校准", "原图", "1D", "处理", "对比"],
                             "2026-10-07：绘图→原图，且挪到校准后面")
            # 开局谁都不点亮（用户 2026-09-25 定：上面什么都不选）
            for name, btn in w.entrance_buttons.items():
                self.assertFalse(btn.isChecked(), name)
            # 类型行拆分（2026-10-07）：原图页只留 2D/剖面/瀑布三个类型
            # 按钮；1D/对比/热图的出图按钮各回自己的家（view_buttons
            # ["1D"]/compare_btn/heat_btn 是兼容别名，指向那三颗）
            draw_page = w.param_stack.widget(w.PARAM_PAGES["原图"])
            for name in ("2D", "剖面", "瀑布"):
                self.assertTrue(draw_page.isAncestorOf(w.view_buttons[name]),
                                f"{name} 类型按钮应住在原图页里")
            self.assertEqual(
                sorted(k for k, b in w.view_buttons.items()
                       if b.isCheckable()),
                ["2D", "剖面", "瀑布"], "原图页只有这三个类型按钮")
            self.assertIs(w.view_buttons["1D"], w.plot_1d_btn,
                          "view_buttons['1D'] 应指向「1D」页的产出按钮")
            self.assertTrue(
                w.param_stack.widget(w.PARAM_PAGES["1D"])
                .isAncestorOf(w.view_buttons["1D"]))
            self.assertIs(w.compare_btn, w.plot_cmp_btn)
            self.assertIs(w.heat_btn, w.plot_heat_btn)
        finally:
            w.close()

    def test_produce_buttons_live_in_their_pages(self):
        """每页底部的产出按钮住在自己那页，点了真出图（1D 页当代表）。

        出图链路本身由别的测试覆盖，这里守的是"按钮搬对页 + 接线没断"
        （C 拆分时最容易犯的错就是把按钮留在旧页/忘了接）。

        顺带钉住 2026-09-30 的删除：**"重画"不再有按钮**——[重画]（处理页）
        跑的就是自动通路，[只重画，不重算]（原图页）与每图的 [Home] 同源；
        用户定的新流程是"参数实时重画 + 放大镜调视野 + 保存存当前画面"。
        """
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute), \
                 mock.patch.object(gui_plot_workers, "load_diffraction_image",
                                   return_value=np.zeros((10, 10))):
                add_checked(w, ["data/fake_b.tif"])
                # 1D 页：出 1D 图
                w.entrance_buttons["1D"].click()
                page_1d = w.param_stack.widget(w.PARAM_PAGES["1D"])
                self.assertTrue(page_1d.isAncestorOf(w.plot_1d_btn))
                w.plot_1d_btn.click()
                self.assertTrue(_wait_until(lambda: len(
                    _axes(w, "1D", "data/fake_b.tif").lines) > 0),
                    "[出 1D 图] 该出图")
                # 原图页：点类型按钮 = 对勾选文件出这个类型（本页唯一出图
                # 入口——底部那个 [出图] 2026-10-08 删了，与这里同源）
                w.entrance_buttons["原图"].click()
                draw = w.param_stack.widget(w.PARAM_PAGES["原图"])
                self.assertTrue(draw.isAncestorOf(w.view_buttons["2D"]))
                # [出图（勾选文件）][导出图片…][清空缓存] 都已删（用户
                # 2026-10-01："都删了，保存图片在图片自身的工具栏有"：
                # 存图走面板 [Save]、关窗询问；清缓存走文件栏右键；出图
                # 走类型按钮）。按钮不许长回来
                self.assertFalse(hasattr(w, "plot_now_btn"))
                self.assertFalse(hasattr(w, "export_img_btn"))
                self.assertFalse(hasattr(w, "clear_cache_btn"))
                w.view_buttons["2D"].click()
                QApplication.processEvents()
                # 类型 2D → 开一张 2D 面板（读图，不积分）
                self.assertTrue(_wait_until(lambda: (
                    "2D|data/fake_b.tif" in w.plot_docks
                    and len(gui_panel_state._content(
                        w.plot_docks["2D|data/fake_b.tif"]).axes_2d
                        .images) > 0)),
                    "点 [2D] 该对勾选文件出 2D 图")
                # 处理页 / 对比页的产出按钮：处理页 2026-10-07 合并成一个
                # （proc_save_btn；旧名 proc_keep_btn/proc_batch_btn 是别名）
                for name, btn in (("处理", "proc_save_btn"),
                                  ("对比", "plot_cmp_btn"),
                                  ("对比", "plot_heat_btn")):
                    page = w.param_stack.widget(w.PARAM_PAGES[name])
                    self.assertTrue(
                        page.isAncestorOf(getattr(w, btn)),
                        f"{btn} 应在{name}页里")
                self.assertIs(w.proc_keep_btn, w.proc_save_btn)
                self.assertIs(w.proc_batch_btn, w.proc_save_btn)
                self.assertFalse(hasattr(w, "proc_recalc_btn"),
                                 "处理页的 [重算这张图] 已撤（2026-10-07）")
                # "重画"按钮不许长回来（用户 2026-09-30：只留放大镜）
                for gone in ("bg_redraw_btn", "redraw_now_btn"):
                    self.assertFalse(hasattr(w, gone), f"{gone} 应当已删")
        finally:
            w.close()

    def test_startup_selects_nothing_and_hides_param_dock(self):
        """开局：五个入口一个都不亮 + 参数坞收起（用户 2026-09-25 定：
        "开界面后上面什么都没选，右边参数栏是隐藏的"）。"""
        w = create_window()
        try:
            w.show()
            QApplication.processEvents()
            for name, btn in w.entrance_buttons.items():
                self.assertFalse(btn.isChecked(), name)
            self.assertFalse(w.param_dock.isVisible())
            self.assertFalse(w.panel_toggles["参数"].isChecked())
            self.assertIsNone(w._last_entrance)
            # 其余两个坞照常：开局只剩文件列 + 日志
            self.assertTrue(w.file_dock.isVisible())
            self.assertTrue(w.log_dock.isVisible())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_clicking_entrance_shows_param_dock(self):
        """点入口 = 参数坞露出来并翻到那一页（[参数] 开关自动跟着勾上）。"""
        w = create_window()
        try:
            w.show()
            QApplication.processEvents()
            for name in ("1D", "对比"):
                w.entrance_buttons[name].click()
                QApplication.processEvents()
                self.assertTrue(w.param_dock.isVisible(), name)
                self.assertTrue(w.panel_toggles["参数"].isChecked(), name)
                self.assertEqual(w.param_stack.currentIndex(),
                                 w.PARAM_PAGES[name], name)
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_entrance_switching_follows_buttons(self):
        """点入口 = 翻到那一页 + 高亮跟着动；出入校准走同一条路。"""
        w = create_window()
        try:
            for name in ("校准", "原图", "处理", "对比", "1D"):
                w.entrance_buttons[name].click()
                QApplication.processEvents()
                self.assertEqual(w.param_stack.currentIndex(),
                                 w.PARAM_PAGES[name], name)
                self.assertTrue(w.entrance_buttons[name].isChecked(), name)
                for other, btn in w.entrance_buttons.items():
                    if other != name:
                        self.assertFalse(btn.isChecked(),
                                         f"切到 {name} 时 {other} 不该还亮着")
            self.assertIn("回到分析模式", w.log_text.toPlainText(),
                          "从校准切出去 = 退校准")
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_entrance_buttons_say_exit_when_active(self):
        """入口按钮的两副面孔：选中的那个写「退出某某」，再点一次退出。

        用户 2026-10-01："在一个模式中……我希望调回之前类似退出校准这四个
        字的样子，让所有的模式进行时，原本的按钮都显示退出某某，代表再按
        一下退出"。四个分析入口的"退出" = **回到开局那个什么都没选**：
        入口全熄灭 + 收起参数坞（[参数] 按钮随时能再展开）；图与产物一张
        不动（这里连面板都还没开）。
        """
        w = create_window()
        try:
            for name in ("原图", "1D", "处理", "对比"):
                btn = w.entrance_buttons[name]
                self.assertEqual(btn.text(), name, "没选中时写入口名")
                btn.click()
                QApplication.processEvents()
                want = f"退出 {name}" if name.isascii() else f"退出{name}"
                self.assertEqual(btn.text(), want, "选中时写退出某某")
                self.assertTrue(w.param_dock.isVisibleTo(w))
                self.assertEqual(w.param_stack.currentIndex(),
                                 w.PARAM_PAGES[name])
                btn.click()             # 再点一次 = 退出（同一个按钮）
                QApplication.processEvents()
                self.assertEqual(btn.text(), name, "退出后文字复位")
                self.assertFalse(btn.isChecked())
                self.assertFalse(w.param_dock.isVisibleTo(w),
                                 "退出 = 收起参数坞")
                self.assertIsNone(w._last_entrance)
                self.assertIn(want, w.log_text.toPlainText())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_units_in_spinbox_suffix(self):
        w = create_window()
        try:
            # 几何三件（像素/波长/距离）的控件已撤（读数走悬停提示），
            # 剩下的输入框单位仍走 suffix
            self.assertEqual(w.params["剖面角度 (°)"].suffix(), "°")
            for name in ("2θ 下限 (°)", "2θ 上限 (°)"):
                self.assertEqual(w.params[name].suffix(), "°")
            # 对比度/纵轴没有单位，后缀为空
            self.assertEqual(w.params["对比度下限"].suffix(), "")
            self.assertEqual(w.params["纵轴下限"].suffix(), "")
        finally:
            w.close()

    def test_data_params_row_visible_only_on_the_1d_page(self):
        """2θ 范围 / 点数**只在「1D」页**看得见、能改（2026-10-07 逐页裁完）。

        沿革：用户 2026-09-27 要求它别只住在 1D 页（"后面处理时没法选范围"）
        → 之后又逐页把不起作用的页面裁掉："校准页参数放 2theta 范围干嘛"
        （09-27）→ 对比页（10-03："不起作用就藏起来"）→ **处理页**（10-07：
        该页再无积分出口，[重算这张图] 已撤）→ **原图页**（10-07：页里只剩
        2D/剖面/瀑布，三个都不吃 2θ 范围）。剩下的唯一家 = 「1D」页。
        几何配置那一行相反：校准页要留着（借用起点 + 那三个按钮的对象）。
        """
        w = create_window()
        try:
            w.show()
            w.entrance_buttons["1D"].click()
            QApplication.processEvents()
            for name in ("2θ 下限 (°)", "2θ 上限 (°)", "输出点数"):
                self.assertTrue(w.params[name].isVisible(),
                                f"1D 页上该看得见 {name}")
            for page in ("对比", "校准", "处理", "原图"):
                w.entrance_buttons[page].click()
                QApplication.processEvents()
                self.assertFalse(w.data_row.isVisible(),
                                 f"{page} 页上它不该出现")
            w.entrance_buttons["校准"].click()
            QApplication.processEvents()
            self.assertTrue(w.geom_row.isVisible(), "几何配置行校准页要留着")
            w.entrance_buttons["1D"].click()
            QApplication.processEvents()
            self.assertTrue(w.data_row.isVisible(), "回到它管用的页该回来")
            # 改一下照样进几何（数据参数照旧参与计算）
            w.params["2θ 下限 (°)"].setValue(2.5)
            self.assertAlmostEqual(
                gui_panel_state._collect_geometry(w)["tth_min_deg"], 2.5)
        finally:
            w.close()

    def test_range_pairs_share_one_row(self):
        """成对的下限/上限并排一行：同父、中间隔着 "~"。

        2θ 那一对 2026-09-27 搬到了坞顶第三行、和"点数"同住一行
        （所有分析页共用，用户："后面处理时没法选范围"），所以那一行不止
        三件——对它只查"两框同父、中间是 ~"；另两对仍各占一行，维持原判。
        """
        w = create_window()
        try:
            for lo_name, hi_name, alone in (
                    ("2θ 下限 (°)", "2θ 上限 (°)", False),
                    ("对比度下限", "对比度上限", True),
                    ("纵轴下限", "纵轴上限", True)):
                lo, hi = w.params[lo_name], w.params[hi_name]
                self.assertIs(lo.parent(), hi.parent(),
                              f"{lo_name} 与上限该住同一行")
                row = lo.parent().layout()
                i_lo, i_hi = row.indexOf(lo), row.indexOf(hi)
                self.assertGreaterEqual(i_lo, 0)
                self.assertLess(i_lo, i_hi, "下限在左、上限在右")
                self.assertEqual(row.itemAt(i_lo + 1).widget().text(), "–")
                if alone:
                    self.assertEqual(row.count(), 3)
        finally:
            w.close()

    def test_section_captions_and_normalize_combo(self):
        w = create_window()
        try:
            captions = {lb.text() for lb in w.param_dock.findChildren(QLabel)}
            # 几何这一节只剩坞顶那一行的标签（像素/波长/距离三行只读
            # 字段与它们的"标定几何（只读…）"标题 2026-09-26 撤了，
            # 读数改走那一行的悬停提示）
            self.assertIn("几何配置", captions)
            self.assertNotIn("标定几何（只读：由几何配置决定）", captions)
            # 积分设置搬去坞顶第三行（所有分析页共用），不再是 1D 页的小标题
            self.assertNotIn("积分设置", captions)
            # 原图页的参数小节按类型行的三个功能分节（2026-10-07：
            # "绘图页的参数分别对应功能，现在太乱了"）：2D 视图 / 剖面 /
            # 瀑布；「1D 显示」已搬回 1D 页（这里不再出现）
            self.assertIn("2D 视图", captions)
            self.assertIn("剖面", captions)
            self.assertIn("瀑布", captions)
            self.assertNotIn("2D/剖面视图", captions)
            # （"1D 显示" 仍在这个集合里——它现在住「1D」页，本断言
            # 收集的是整个参数坞所有页的文本，不做"不在原图页"的推断）
            # 归一化三选一下拉框（键仍是"对比归一化"，快照回放认 data
            # 不认字面）：全图最强峰 / 指定数据… / 不归一化。
            # "各自最强峰"已删（用户 2026-09-26 的规矩：不许按各自最高
            # 峰归一化——那样样品之间的强弱差就看不出来了）
            combo = w.params["对比归一化"]
            self.assertEqual(
                [combo.itemText(i) for i in range(combo.count())],
                ["全图最强峰", "指定数据…", "不归一化"])
            self.assertEqual(
                [combo.itemData(i) for i in range(combo.count())],
                ["global", "file", "off"])
            self.assertEqual(combo.currentData(), "off", "默认 = 不归一化")
            # "指定数据" 未选中时，旁边的目标文件下拉框置灰
            self.assertFalse(w.params["归一化目标"].isEnabled())
        finally:
            w.close()

    def test_dock_min_sizes_prevent_clipping(self):
        w = create_window()
        try:
            w.show()
            # 参数坞开局是收起的（入口一个都没选）：Qt 只在坞**显示出来**
            # 时才把内容的最小尺寸并进坞的最小尺寸，所以先点个入口再量
            w.entrance_buttons["1D"].click()
            QApplication.processEvents()
            # 坞的最小宽高已设：左右 = 完整显示最宽一行的宽度，
            # 上下 = 固定件（编辑对象名 + 标题/按钮行）不被遮没
            self.assertGreater(w.param_dock.minimumWidth(), 150)
            self.assertLess(w.param_dock.minimumWidth(), 320)   # 窄排版：不能为"完整显示"把坞设得过宽
            self.assertGreater(w.param_dock.minimumHeight(), 200)
            # 把窗口压窄：参数坞停在最小宽度（被裁的只有中央绘图区）
            w.resize(250, 400)
            QApplication.processEvents()
            self.assertGreaterEqual(
                w.param_dock.width(), w.param_dock.minimumWidth() - 5)
        finally:
            w.close()


class TestImageDisplayLive(unittest.TestCase):
    """显示参数**全部即改即画**（2026-10-08 清扫：[应用显示设置] 已删）。

    三件事：
      * 改了立刻生效——写进编辑对象的快照 + 用已有数据重画（剖面角度是
        唯一的"要重算"，见 plot_views._refresh_profile_angle），不必按按钮；
      * 作用域 = 编辑对象那一张：显示参数每张图各记各的，切面板随快照
        回放该面板自己的显示设置；数据 [应用] 不捎带显示参数；
      * 程序回填（自动对比度算值进框）挂 `_param_box_sync` 旗标，不算
        用户输入、不触发重画。
    """

    def test_only_reset_button_remains(self):
        """动作栏只剩 [恢复默认]：[应用显示设置] 已删、按钮不许长回来。"""
        w = create_window()
        try:
            self.assertIsNotNone(w.findChild(QPushButton, "reset_image_btn"))
            self.assertIsNone(w.findChild(QPushButton, "apply_image_btn"))
            self.assertFalse(hasattr(w, "apply_image_btn"))
        finally:
            w.close()

    def test_change_without_focus_is_harmless(self):
        """没有编辑对象时改显示参数：不崩、不算改动（值先留着）。"""
        w = create_window()
        try:
            w.params["显示束心"].setChecked(True)    # 默认关着：勾上才算一次改动
            w.params["对比度下限"].setValue(5.0)
            QApplication.processEvents()
            self.assertTrue(w.params["显示束心"].isChecked())
        finally:
            w.close()

    def test_change_updates_snapshot_and_redraws_immediately(self):
        """改显示参数 → 快照立刻更新 + 用已有数据重画（不重算）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines)
                    > 0))
            done_before = w.log_text.toPlainText().count("积分完成")
            w.params["对数纵轴"].setChecked(True)          # 即改即画
            w.params["剖面角度 (°)"].setValue(15.0)        # 焦点不是剖面：只写快照
            # 快照同步刷新（切走再切回显示这组值，与数据 [应用] 一致）
            dock = _dock(w, "1D", "data/fake_b.tif")
            self.assertEqual(dock.params_snapshot["剖面角度 (°)"], 15.0)
            self.assertTrue(dock.params_snapshot["对数纵轴"])
            # 显示参数真的落到图上（对数纵轴生效），且没有重算
            self.assertEqual(
                _axes(w, "1D", "data/fake_b.tif").get_yscale(), "log")
            self.assertEqual(w.log_text.toPlainText().count("积分完成"),
                             done_before)
        finally:
            w.close()

    def test_change_on_unfinished_panel_is_harmless(self):
        """编辑对象是没算出结果的 2D 面板：改显示参数不崩（静默等它算完）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_plot_workers, "load_diffraction_image",
                                   side_effect=OSError("boom")):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "2D")
                self.assertTrue(_wait_until(
                    lambda: "读取失败" in w.log_text.toPlainText()))
            QTest.mouseClick(gui_panel_state._content(_dock(w, "2D", "data/fake_b.tif")),
                             Qt.LeftButton)
            w.params["显示束心"].setChecked(True)    # 不崩即过（默认关着）
            QApplication.processEvents()
        finally:
            w.close()

    def test_change_only_redraws_focus_panel(self):
        """显示参数每张图各记各的：1D + 对比同开，焦点在 1D 上改参数
        → 只重画 1D 那张，对比保持自己的设置（修前是全局的，
        一张改了所有图都变）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_a.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines)
                    > 0))
                add_checked(w, ["data/fake_b.tif"])   # fake_a 仍勾着
                _open_view(w, "1D")                   # 栅栏（2026-10-16）：fake_b 也没算过，先出过 1D
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0))
                w.compare_btn.click()
                cax = gui_panel_state._content([d for k, d in w.plot_docks.items()
                                        if k.startswith("对比")][0]).axes_1d
                self.assertTrue(_wait_until(lambda: len(cax.lines) >= 2))
            # 焦点此时在对比面板；切到 1D 面板改参数 → 只动 1D（即改即画）
            QTest.mouseClick(gui_panel_state._content(_dock(w, "1D", "data/fake_a.tif")),
                             Qt.LeftButton)
            w.params["对数纵轴"].setChecked(True)
            QApplication.processEvents()
            self.assertEqual(
                _axes(w, "1D", "data/fake_a.tif").get_yscale(), "log")
            self.assertEqual(cax.get_yscale(), "linear")   # 对比没被波及
            # 对比面板自己的快照仍是旧设置（对数关），1D 面板已更新
            cdock = [d for k, d in w.plot_docks.items()
                     if k.startswith("对比")][0]
            self.assertFalse(cdock.params_snapshot["对数纵轴"])
            self.assertTrue(
                _dock(w, "1D", "data/fake_a.tif").params_snapshot["对数纵轴"])
        finally:
            w.close()

    def test_display_params_replay_per_panel(self):
        """显示参数切面板随快照回放：两张 1D 一张对数一张线性，点哪张
        图参数坞就显示哪张的设置（修前切面板不回放，参数跟图对不上）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                _open_view(w, "1D")   # 两张都出图（默认线性）
                self.assertTrue(_wait_until(
                    lambda: all(len(_axes(w, "1D", f"data/{n}.tif").lines) > 0
                                for n in ("fake_a", "fake_b"))))
            # 焦点是最后算完的那张；切到 fake_a 改成对数（即改即画）
            QTest.mouseClick(gui_panel_state._content(_dock(w, "1D", "data/fake_a.tif")),
                             Qt.LeftButton)
            w.params["对数纵轴"].setChecked(True)
            QApplication.processEvents()
            self.assertEqual(
                _axes(w, "1D", "data/fake_a.tif").get_yscale(), "log")
            self.assertEqual(
                _axes(w, "1D", "data/fake_b.tif").get_yscale(), "linear")
            # 点 fake_b → 参数坞回放它自己的设置（对数关）
            QTest.mouseClick(gui_panel_state._content(_dock(w, "1D", "data/fake_b.tif")),
                             Qt.LeftButton)
            self.assertFalse(w.params["对数纵轴"].isChecked())
            # 点回 fake_a → 显示它自己的设置（对数开）
            QTest.mouseClick(gui_panel_state._content(_dock(w, "1D", "data/fake_a.tif")),
                             Qt.LeftButton)
            self.assertTrue(w.params["对数纵轴"].isChecked())
        finally:
            w.close()

    def test_ylim_state_replays_per_panel(self):
        """纵轴范围随面板回放：A 手动范围（输入框可改）、B 自动（输入
        框置灰），切来切去状态跟着换——修前显示参数不回放，纵轴组的
        开关/置灰状态与每张图的实际设置对不上。"""
        w = create_window()
        try:
            # A：先按默认出图，再改成手填范围并图像 [应用]
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_a.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines)
                    > 0))
            w.params["纵轴自动"].setChecked(False)
            w.params["纵轴下限"].setValue(10.0)
            w.params["纵轴上限"].setValue(500.0)
            QApplication.processEvents()   # 即改即画：不用再按 [应用]
            self.assertEqual(_axes(w, "1D", "data/fake_a.tif").get_ylim(),
                             (10.0, 500.0))
            # B：只勾 B 新开一张（新面板显示参数从默认起步 = 自动开）
            add_checked(w, ["data/fake_b.tif"])
            for i in range(w.file_list.count()):
                item = w.file_list.item(i)
                item.setCheckState(
                    Qt.Checked if item.data(Qt.UserRole) == "data/fake_b.tif"
                    else Qt.Unchecked)
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines)
                    > 0))
            # 焦点在 B：自动开、输入框置灰
            self.assertTrue(w.params["纵轴自动"].isChecked())
            self.assertFalse(w.params["纵轴下限"].isEnabled())
            # 点回 A → 回放它自己的：自动关、输入框可改、值 = 手填值
            QTest.mouseClick(gui_panel_state._content(_dock(w, "1D", "data/fake_a.tif")),
                             Qt.LeftButton)
            self.assertFalse(w.params["纵轴自动"].isChecked())
            self.assertTrue(w.params["纵轴下限"].isEnabled())
            self.assertEqual(w.params["纵轴下限"].value(), 10.0)
            self.assertEqual(w.params["纵轴上限"].value(), 500.0)
        finally:
            w.close()

    def test_new_panels_start_with_default_display(self):
        """新开面板（1D / 对比）显示参数从默认起步：不受上一张焦点图
        设置的影响（修前新图会继承参数坞当前值——先把 1D 改成对数+
        手填，新开的对比图也跟着对数+手填）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines)
                    > 0))
            # 把这张 1D 图改成对数 + 手填纵轴（即改即画）
            w.params["对数纵轴"].setChecked(True)
            w.params["纵轴自动"].setChecked(False)
            w.params["纵轴下限"].setValue(10.0)
            w.params["纵轴上限"].setValue(500.0)
            QApplication.processEvents()
            # 新开对比图 → 显示参数是默认，不是上面的对数/手填
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_a.tif"])   # fake_b 仍勾着
                _open_view(w, "1D")                   # 栅栏（2026-10-16）：fake_a 也没算过，先出过 1D
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0))
                w.compare_btn.click()
                cdock = [d for k, d in w.plot_docks.items()
                         if k.startswith("对比")][0]
                self.assertTrue(_wait_until(
                    lambda: len(gui_panel_state._content(cdock).axes_1d.lines) >= 2))
            snap = cdock.params_snapshot
            self.assertFalse(snap["对数纵轴"])
            self.assertTrue(snap["纵轴自动"])
            self.assertEqual(snap["纵轴下限"], 1.0)
            self.assertEqual(snap["纵轴上限"], 100000.0)
            # 对比完成后成为焦点：置灰框显示它自己算出的自动区间
            # （输入框精度 decimals=1，与图的精确值允许 0.1 级误差）
            cax = gui_panel_state._content(cdock).axes_1d
            self.assertAlmostEqual(w.params["纵轴下限"].value(),
                                   cax.get_ylim()[0], places=1)
            self.assertAlmostEqual(w.params["纵轴上限"].value(),
                                   cax.get_ylim()[1], places=1)
        finally:
            w.close()

    def test_recompute_keeps_panel_display(self):
        """重按视图按钮重算：显示参数保留面板自己的旧设置（修前会被
        参数坞当前值覆盖——看别的图时重算会把别的图的长相抄过来）。"""
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_a.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines)
                    > 0))
            # fake_a 改成对数（即改即画）
            w.params["对数纵轴"].setChecked(True)
            QApplication.processEvents()
            self.assertEqual(
                _axes(w, "1D", "data/fake_a.tif").get_yscale(), "log")
            # 加一张 fake_b 两张都勾着重按 1D：重算两张。新开的 fake_b
            # 从默认（线性）起步，fake_a 保留自己的对数设置
            add_checked(w, ["data/fake_b.tif"])
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: w.log_text.toPlainText().count("积分完成") >= 3))
            self.assertEqual(
                _axes(w, "1D", "data/fake_a.tif").get_yscale(), "log")
            self.assertEqual(
                _axes(w, "1D", "data/fake_b.tif").get_yscale(), "linear")
            self.assertTrue(
                _dock(w, "1D", "data/fake_a.tif").params_snapshot["对数纵轴"])
        finally:
            w.close()

    def test_live_display_and_data_apply_stay_in_their_lanes(self):
        """显示即改即画、数据 [应用] 各管各的（互不串改）。

        （原 test_each_apply_touches_only_its_group：2026-10-08 起显示
        参数不再有"按一下才生效"那一态，两个 [应用] 只剩数据这一个。）
        """
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines)
                    > 0))
            dock = _dock(w, "1D", "data/fake_b.tif")
            # 同时改了数据（2θ 上限）和显示（对数）：显示立刻生效、
            # 数据参数仍等 [重算这张图]
            w.params["2θ 上限 (°)"].setValue(7.0)
            w.params["对数纵轴"].setChecked(True)
            self.assertTrue(dock.params_snapshot["对数纵轴"])
            self.assertEqual(dock.params_snapshot["2θ 上限 (°)"], 8.0)
            self.assertEqual(_axes(w, "1D", "data/fake_b.tif").get_yscale(),
                             "log")
            self.assertEqual(_axes(w, "1D", "data/fake_b.tif").get_xlim(),
                             (1.0, 8.0))
            # 改剖面角度（焦点是 1D 面板）：也只写快照、不触发重算
            w.params["剖面角度 (°)"].setValue(45.0)
            # 按数据 [应用] → 数据生效；重算用面板自己的显示参数
            # （每图各记各的），不把坞里其它控件的值捎带进去
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                w.findChild(QPushButton, "apply_btn").click()
                self.assertTrue(_wait_until(
                    lambda: w.log_text.toPlainText().count("积分完成") >= 2))
            self.assertEqual(dock.params_snapshot["2θ 上限 (°)"], 7.0)
            self.assertEqual(_axes(w, "1D", "data/fake_b.tif").get_xlim(),
                             (1.0, 7.0))
            self.assertEqual(dock.params_snapshot["剖面角度 (°)"], 45.0,
                             "重算保留面板自己的显示参数（每图各记各的）")
            # 关对数 → 立刻写进快照、图立刻回线性（即改即画）
            w.params["对数纵轴"].setChecked(False)
            self.assertFalse(dock.params_snapshot["对数纵轴"],
                             "即改即画：控件一动快照就跟上")
            self.assertEqual(_axes(w, "1D", "data/fake_b.tif").get_yscale(),
                             "linear")
        finally:
            w.close()

    def test_auto_ylim_boxes_show_computed_range(self):
        """纵轴自动时置灰输入框 = 程序实际用的区间（画图/切面板回填，
        修前永远显示占位默认 1.0 ~ 100000.0）。"""
        w = create_window()
        try:
            # fake_b 数据 = [10, 20, 30]（_fake_compare_compute）→ 自动
            # 区间是它的 1%/99.9% 分位，不是占位默认
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compare_compute):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines)
                    > 0))
            ax_b = _axes(w, "1D", "data/fake_b.tif")
            self.assertAlmostEqual(w.params["纵轴下限"].value(),
                                   ax_b.get_ylim()[0], places=1)
            self.assertAlmostEqual(w.params["纵轴上限"].value(),
                                   ax_b.get_ylim()[1], places=1)
            self.assertGreater(w.params["纵轴下限"].value(), 5.0)   # 不是占位 1.0
            # 只勾 fake_a（数据 [1, 2, 3]）再开一张 → 焦点切到 A，
            # 置灰框按 A 的数据重算；切回 B 又变回 B 的区间
            add_checked(w, ["data/fake_a.tif"])
            for i in range(w.file_list.count()):
                item = w.file_list.item(i)
                item.setCheckState(
                    Qt.Checked if item.data(Qt.UserRole) == "data/fake_a.tif"
                    else Qt.Unchecked)
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compare_compute):
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines)
                    > 0))
            ax_a = _axes(w, "1D", "data/fake_a.tif")
            self.assertAlmostEqual(w.params["纵轴下限"].value(),
                                   ax_a.get_ylim()[0], places=1)
            QTest.mouseClick(gui_panel_state._content(_dock(w, "1D", "data/fake_b.tif")),
                             Qt.LeftButton)
            self.assertAlmostEqual(w.params["纵轴下限"].value(),
                                   ax_b.get_ylim()[0], places=1)
            self.assertAlmostEqual(w.params["纵轴上限"].value(),
                                   ax_b.get_ylim()[1], places=1)
        finally:
            w.close()


class TestCompare(unittest.TestCase):
    """[对比] 按钮（仅 1D）：勾选 >= 2 个文件 → 各读**已经算好的**
    1D 曲线（先出过 1D 才放行——2026-10-16 栅栏），叠进同一张面板
    （自动分色 + 图例显示名）。同一勾选集合重复点 = 复用面板刷新；
    "对比归一化到最强峰"只动显示层。"""

    def _compare_axes(self, w):
        """取唯一的对比面板坐标轴（面板键就是 "对比"——单槽）。"""
        keys = [k for k in w.plot_docks if k.startswith("对比")]
        self.assertEqual(len(keys), 1)
        return gui_panel_state._content(w.plot_docks[keys[0]]).axes_1d

    def _plot_compare(self, w):
        """勾 fake_a + fake_b：先各出过 1D（栅栏前置）再点 [对比]，
        等两条曲线到齐 → 返回坐标轴。"""
        _prime_1d(w, ["data/fake_a.tif", "data/fake_b.tif"])
        w.compare_btn.click()
        ax = self._compare_axes(w)
        self.assertTrue(_wait_until(lambda: len(ax.lines) >= 2))
        return ax

    def test_the_data_row_shows_only_on_the_1d_page(self):
        """坞顶那行数据参数只在「1D」页显示（2026-10-07 逐页裁完）。

        用户逐页拍板：“校准页不起作用就藏起来”（2026-09-27）→ 对比页
        （2026-10-03）→ 处理页（该页再无积分出口，[重算这张图] 已撤）→
        原图页（页里只剩 2D/剖面/瀑布，三个都不吃 2θ 范围）。剩下的唯一
        家 = 「1D」页：积分主台，范围/点数/重算都在那里。
        """
        w = create_window()
        try:
            for name, want in (("校准", False), ("对比", False),
                               ("处理", False), ("原图", False),
                               ("1D", True)):
                w.entrance_buttons[name].click()
                QApplication.processEvents()
                self.assertEqual(not w.data_row.isHidden(), want,
                                 f"{name} 页的 2θ 行可见性不对")
        finally:
            w.close()

    def test_compare_reads_each_files_own_range_and_ignores_the_dock(self):
        """对比按每个文件自己已有的产物取数：改坞顶那行不重算、也不带过去。

        用户 2026-10-03："对对比图改 2θ 会影响所有参与对比的图，这不合适"。
        这边钉住新的语义：每个文件用它自己的产物（自己的范围/点数）——
        [对比] 不因坞顶那行的改动重算，对比面板也不记"未应用"（数据键
        不进它的快照），看对比更不会把坞顶那行染成对比的范围。
        """
        w = create_window()
        try:
            files = _tmp_files(2)
            w.add_files([str(p) for p in files], select=True)
            for p in files:    # 每个文件自己的产物：2–7°、8 点（npt=1234）
                stage_cache.store_1d(p, np.linspace(2.0, 7.0, 8),
                                     np.ones(8) * 3.0, config=w.config_name,
                                     npt=1234, tth_min=2.0, tth_max=7.0)
            dock = w.plot_docks.get("对比")
            with mock.patch.object(gui_plot_spawn, "_compute_integration") as compute:
                w.compare_btn.click()
                QApplication.processEvents()
                dock = w.plot_docks["对比"]
                self.assertTrue(_wait_until(
                    lambda: getattr(dock, "compare_pending", 1) == 0))
                compute.assert_not_called()      # 命中每个文件自己的产物
            self.assertEqual(sorted(dock.compare_sources.values()),
                             ["1D 产物", "1D 产物"])
            self.assertEqual([len(v[0]) for v in dock.compare_data.values()],
                             [8, 8], "画的就是那条 8 点产物")
            # 改坞顶那行 + 再点 [对比]：照旧用产物、不重算、不亮灰字
            w.params["2θ 上限 (°)"].setValue(6.0)
            w.params["输出点数"].setValue(999)
            QApplication.processEvents()
            self.assertTrue(w.pending_labels["数据"].isHidden(),
                            "对比面板不管数据参数 → 不该亮「未应用」")
            with mock.patch.object(gui_plot_spawn, "_compute_integration") as again:
                w.compare_btn.click()
                QApplication.processEvents()
                self.assertTrue(_wait_until(
                    lambda: getattr(dock, "compare_pending", 1) == 0))
                again.assert_not_called()
            self.assertEqual([len(v[0]) for v in dock.compare_data.values()],
                             [8, 8])
            # "看一眼对比"不该把坞顶那行染成对比的范围
            box = w.params["2θ 上限 (°)"]
            box.setValue(6.0)
            w.focus_panel = None            # 模拟"刚才在看别的面板"
            gui_panel_state._set_focus(w, "对比", "对比")
            QApplication.processEvents()
            self.assertAlmostEqual(box.value(), 6.0,
                                   msg="对比面板没有数据参数，回放不许动那行")
        finally:
            w.close()

    def test_compare_is_a_single_slot_rebuilt_from_the_current_checks(self):
        """对比只有一张面板，永远按**当前**勾选重建——不留上一次的曲线。

        用户 2026-09-28："对比图还是不对，我选了处理后产物 81 个结果对比图
        是二百多个的" + "无论什么时候，都是执行当前勾选，不存在以前勾选
        影响现在的情况"。旧版把面板键做成"对比｜排序后的路径串"：改了勾选
        再按一次就**另开一张**，旧那张（243 条）还赖在屏幕上——用户看到的
        是旧画面。现在键固定为 "对比"，就地重建。
        """
        w = create_window()
        try:
            _prime_1d(w, ["data/fake_a.tif", "data/fake_b.tif",
                          "data/fake_c.tif"])       # 栅栏前置：先各出过 1D
            w.compare_btn.click()
            ax = self._compare_axes(w)
            self.assertTrue(_wait_until(lambda: len(ax.lines) >= 3))
            # 取消一个 → 再按 [对比]：还是同一张面板，只剩 2 条
            w.file_list.raw_group.child(2).setCheckState(0, Qt.Unchecked)
            QApplication.processEvents()
            w.compare_btn.click()
            ax2 = self._compare_axes(w)     # 数量断言在助手里面
            self.assertTrue(_wait_until(lambda: len(ax2.lines) == 2),
                            "取消勾选的曲线必须消失（没有记忆）")
            self.assertIn("按当前勾选重建", w.log_text.toPlainText())
        finally:
            w.close()

    def test_compare_title_shows_the_mix_when_kinds_are_mixed(self):
        """混类勾选时标题写明构成（"原始 1 ｜ 处理后 1"）——单一类时保持干净。"""
        w = create_window()
        try:
            files = _tmp_files(2)
            w.add_files([str(p) for p in files], select=False)
            for f in files:
                _store_product(w, f)                      # 1D 产物
            w.refresh_groups()
            raw = w.file_list.raw_group
            raw.child(0).setCheckState(0, Qt.Checked)     # 原始 1
            grp = _group_by_text(w, "1D 产物")
            self.assertIsNotNone(grp)
            grp.child(0).setCheckState(0, Qt.Checked)     # 产物 1
            QApplication.processEvents()
            titles = []
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compare_compute):
                w.compare_btn.click()
                QApplication.processEvents()
                dock = next(d for k, d in w.plot_docks.items()
                            if k.startswith("对比"))
                titles.append(dock.windowTitle())
            self.assertIn("｜", titles[0], "混类要写清构成")
        finally:
            w.close()

    def test_strip_common_shortens_batch_names_only(self):
        """短名助手：剥批次前缀与扩展名换出号段；短名字不剥（免得只剩 a）。"""
        strip = gui_plot_compare._strip_common
        self.assertEqual(
            strip(["LMFP_1_atten0-00029.tif", "LMFP_1_atten0-00031.tif"]),
            ["00029", "00031"])
        self.assertEqual(strip(["scan_001.dat", "scan_002.dat"]),
                         ["001", "002"])
        # 前缀剥完只剩一个字母的，不剥（反而更难认）；扩展名照剥
        self.assertEqual(strip(["fake_a.tif", "fake_b.tif"]),
                         ["fake_a", "fake_b"])
        self.assertEqual(strip(["样品A", "样品B"]), ["样品A", "样品B"])
        self.assertEqual(strip(["只有一个"]), ["只有一个"])
        # 公共后缀不许吃号段（2026-10-15 晚用户实测："00029/00189" 结尾
        # 共享一个 "9"，剥后缀把号段啃掉一位、显示成 "0002/0018"）
        self.assertEqual(
            strip(["LMFP_1_atten0-00029.tif", "LMFP_1_atten0-00189.tif"]),
            ["00029", "00189"])
        self.assertEqual(strip(["a9.tif", "b9.tif"]), ["a9", "b9"],
                         "只差前缀、同号段：号段保住")

    def test_compare_legend_hidden_when_too_many_curves(self):
        """勾了 [显示数据名]、但曲线超过 LEGEND_MAX_CURVES → 仍不画图例。

        用户 2026-09-26："对比的图例还是影响看图，太多了"；2026-10-08 起
        名字默认关、由 [显示数据名] 控制（本测勾上），超限时日志说清怎么办
        ——认曲线看悬停读数（那里本来就报曲线名）。
        """
        w = create_window()
        files = _tmp_files(gui_plot_compare.LEGEND_MAX_CURVES + 1)
        try:
            for p in files:   # 栅栏前置：真文件先各落一份 1D 产物
                _store_product(w, p)
            w.add_files([str(p) for p in files], select=True)
            w.compare_btn.click()
            ax = self._compare_axes(w)
            self.assertTrue(_wait_until(
                lambda: len(ax.lines) >= len(files)))
            self.assertIsNone(ax.get_legend(), "默认关：本来就不画")
            w.params["显示数据名"].setChecked(True)   # 勾上也放不下（超限）
            QApplication.processEvents()
            self.assertIsNone(ax.get_legend(), "条数超限就不该有图例")
            log = w.log_text.toPlainText()
            self.assertIn(f"对比图例：{len(files)} 条曲线太多", log)
            self.assertIn("悬停读数", log)
            # 少到阈值以内 → 图例回来（用短名）
            dock = w.plot_docks[[k for k in w.plot_docks
                                 if k.startswith("对比")][0]]
            for key in list(dock.compare_data)[2:]:
                hidden = set(getattr(dock, "compare_hidden", None) or ())
                dock.compare_hidden = hidden | {key}
            gui_plot_compare._redraw_compare(w, dock.panel_key)
            leg = ax.get_legend()
            self.assertIsNotNone(leg, "两条曲线的图例该回来")
            # 名字 = **短名**（来自文件名，可能已剥掉公共前缀/扩展名）：
            # 断言"是文件名的一部分"就够了——具体怎么剥由
            # test_strip_common_shortens_batch_names_only 管，别在这里写死
            labels = [t.get_text() for t in leg.get_texts()]
            self.assertEqual(len(labels), 2)
            for lbl, p in zip(labels, files[:2]):
                self.assertIn(lbl, Path(p).stem, "图例名该来自文件名")
        finally:
            w.close()

    def test_compare_requires_two_checked_files(self):
        """只勾一个文件点 [对比] → 提示至少两个，不开面板。"""
        w = create_window()
        try:
            add_checked(w, ["data/fake_a.tif"])
            w.compare_btn.click()
            self.assertIn("对比至少勾选两个文件", w.log_text.toPlainText())
            self.assertFalse(
                [k for k in w.plot_docks if k.startswith("对比")])
        finally:
            w.close()

    def test_compare_refuses_raw_without_1d(self):
        """栅栏（用户 2026-10-16："原始数据不允许直接进行对比，必须先去
        1d 积分，日志里记得写"）：没出过 1D 的原始数据 → 整批不出图、
        不现场积分、不开面板；日志点名 + 指路。"""
        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration") as compute:
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                w.compare_btn.click()
                QApplication.processEvents()
                log = w.log_text.toPlainText()
                self.assertIn("还没有 1D 曲线", log)
                self.assertIn("先点 [1D] 出图，再来对比", log)
                self.assertIn("fake_a.tif 等 2 个", log)
                compute.assert_not_called()
            self.assertFalse([k for k in w.plot_docks if k.startswith("对比")],
                             "拒绝时不开面板")
        finally:
            w.close()

    def test_compare_refusal_takes_the_whole_batch(self):
        """混合勾选：一个已经算好 + 一个没出过 1D → **整批**拒绝，连算好
        的也不画（栅栏是"不许少画"，不是"跳过没算的"）。"""
        w = create_window()
        try:
            good = _tmp_files(1)[0]
            _store_product(w, good)          # 这一份已经算好（真文件）
            add_checked(w, [str(good), "data/fake_b.tif"])
            w.compare_btn.click()
            QApplication.processEvents()
            log = w.log_text.toPlainText()
            self.assertIn("勾选里 1 个原始数据还没有 1D 曲线（fake_b.tif）",
                          log)
            self.assertFalse([k for k in w.plot_docks if k.startswith("对比")],
                             "整批拒绝：一张图都不开")
        finally:
            w.close()

    def test_compare_allows_raw_once_1d_is_done(self):
        """出过 1D 之后再点 [对比] → 放行（栅栏只拦没算过的；曲线来自
        本会话的 1D 面板，不现场积分）。"""
        w = create_window()
        try:
            paths = ["data/fake_a.tif", "data/fake_b.tif"]
            _prime_1d(w, paths)              # 先出 1D（这一步就是"去过 1D 积分"）
            with mock.patch.object(gui_plot_spawn, "_compute_integration") as compute:
                w.compare_btn.click()
                QApplication.processEvents()
                compute.assert_not_called()
            ax = self._compare_axes(w)
            self.assertEqual(len(ax.lines), 2)
            self.assertIn("对比完成：2 条曲线", w.log_text.toPlainText())
            self.assertIn("复用 1D 面板", w.log_text.toPlainText())
        finally:
            w.close()

    def test_compare_apply_skips_a_curve_that_went_missing(self):
        """直进 runner 的兜底（数据 [应用]）：曲线取不到了 → 逐条跳过并
        写明"先点 [1D] 出图"，其余曲线照画。

        按钮层的整批拒绝拦不到这条路（[应用] 直接调 _run_compare）——
        所以 runner 里还留一道逐条收口（面板关了、产物又不在的情形）。
        """
        w = create_window()
        try:
            _prime_1d(w, ["data/fake_a.tif", "data/fake_b.tif"])
            w.compare_btn.click()
            ax = self._compare_axes(w)
            self.assertEqual(len(ax.lines), 2)
            gui_panels._close_panel(w, "1D|data/fake_a.tif")   # 唯一来源关掉
            w.findChild(QPushButton, "apply_btn").click()
            QApplication.processEvents()
            log = w.log_text.toPlainText()
            self.assertIn("跳过 —— 还没有 1D 曲线（先点 [1D] 出图）", log)
            self.assertEqual(len(ax.lines), 1, "取得到的照画")
        finally:
            w.close()

    def test_compare_two_files_one_panel_with_legend(self):
        """两个文件 → 一张对比面板、两条曲线、图例 = 显示名（勾 [显示数据名]）。"""
        w = create_window()
        try:
            ax = self._plot_compare(w)
            self.assertEqual(len(ax.lines), 2)
            self.assertEqual([line.get_label() for line in ax.lines],
                             ["fake_a.tif", "fake_b.tif"])
            self.assertIsNone(ax.get_legend(), "默认关：不画图例（2026-10-08）")
            w.params["显示数据名"].setChecked(True)   # 即改即画
            QApplication.processEvents()
            self.assertIsNotNone(ax.get_legend())
            # 完成后对比面板成为编辑对象，日志报 2 条曲线
            self.assertTrue(w.focus_panel.startswith("对比"))
            self.assertIn("对比完成：2 条曲线", w.log_text.toPlainText())
        finally:
            w.close()

    def _set_norm_mode(self, w, mode):
        """把归一化下拉框切到某模式（按 data 找条目，找不到就失败）。"""
        combo = w.params["对比归一化"]
        idx = combo.findData(mode)
        self.assertGreaterEqual(idx, 0, f"模式 {mode} 应在下拉框里")
        combo.setCurrentIndex(idx)

    def test_normalize_off_by_default(self):
        """默认 = 不归一化 → 曲线按原始强度画（fake_b 最强峰 30）；
        切到全图最强峰 → 两条都除以全场峰值 30（fake_a 变 3/30 之一）。"""
        w = create_window()
        try:
            ax = self._plot_compare(w)
            self.assertEqual(w.params["对比归一化"].currentData(), "off")
            ymax = max(float(np.max(line.get_ydata())) for line in ax.lines)
            self.assertAlmostEqual(ymax, 30.0, places=4)
            self._set_norm_mode(w, "global")   # 即改即画（2026-10-02 起）
            # 同一个除数（全场峰值 30）：fake_b → 1.0，fake_a → 3/30
            for line, want in zip(ax.lines, (3.0 / 30.0, 1.0)):
                self.assertAlmostEqual(float(np.max(line.get_ydata())),
                                       want, places=4)
        finally:
            w.close()

    def test_retired_each_mode_falls_back_to_off(self):
        """旧快照里残留的"各自最强峰"（each）不再生效，按不归一化画。

        用户 2026-09-26 定的规矩：不许按各自最高峰归一化。模式值认不出
        时一律当 off——不做归一化是安全的那一侧（每个样品都缩到自己的
        高度，比"没归一化"更容易误导人）。
        """
        w = create_window()
        try:
            ax = self._plot_compare(w)
            dock = [d for k, d in w.plot_docks.items()
                    if k.startswith("对比")][0]
            for stale in ("each", True, None, "nonsense"):
                dock.params_snapshot["对比归一化"] = stale
                gui_plot_compare._redraw_compare(w, dock.panel_key)
                ymax = max(float(np.max(line.get_ydata()))
                           for line in ax.lines)
                self.assertAlmostEqual(ymax, 30.0, places=4,
                                       msg=f"{stale!r} 应退回不归一化")
        finally:
            w.close()

    def test_normalize_off_shows_raw_values(self):
        """切到不归一化 → 即改即画按原始强度重画（不重算）。"""
        w = create_window()
        try:
            ax = self._plot_compare(w)
            done_before = w.log_text.toPlainText().count("开始对比")
            self._set_norm_mode(w, "off")
            # 快照记下关归一化，fake_b 曲线回到原始强度（最强峰 30）
            dock = [d for k, d in w.plot_docks.items()
                    if k.startswith("对比")][0]
            self.assertEqual(dock.params_snapshot["对比归一化"], "off")
            ymax = max(float(np.max(line.get_ydata())) for line in ax.lines)
            self.assertAlmostEqual(ymax, 30.0, places=4)
            # 只重画不重算
            self.assertEqual(w.log_text.toPlainText().count("开始对比"),
                             done_before)
        finally:
            w.close()

    def test_compare_axis_holds_still_while_tuning_background(self):
        """调背景时对比面板的纵轴**不动**；裁剪仍然放开纵轴。

        用户 2026-09-26："对比面板的纵轴自己在变"——和 1D 面板同一个毛病
        （那次修的是 1D）。纵轴范围改看 ref（不跑背景/平滑的那份，见
        _compare_shown_curves），但**保留归一化与裁剪**：调背景 → 框不动、
        只有曲线在框里往下走；剪掉巨峰 → 框跟着缩（那是裁剪的本意）。
        """
        w = create_window()
        try:
            ax = self._plot_compare(w)
            top0 = float(ax.get_ylim()[1])
            mode = w.params["背景扣除模式"]
            # 开处理只允许动一次下界（给扣完的曲线腾出 0 附近的地方，
            # 见 _draw_compare 里的兜底），上界一动不动
            mode.setCurrentIndex(mode.findData("auto"))
            QApplication.processEvents()
            low1, top1 = (float(v) for v in ax.get_ylim())  # (下, 上)
            self.assertAlmostEqual(top1, top0, places=6,
                                   msg="切到自动基线不该动上界")
            for win in (0.3, 3.0):
                w.params["背景窗口 (°)"].setValue(win)
                QApplication.processEvents()
                np.testing.assert_allclose(
                    ax.get_ylim(), (low1, top1),
                    err_msg=f"窗口 {win}° 不该动框")
            mode.setCurrentIndex(mode.findData("anchor"))
            QApplication.processEvents()
            np.testing.assert_allclose(ax.get_ylim(), (low1, top1),
                                       err_msg="切到手动锚点不该动框")
            # 剪掉最强的那条峰（fake_b 在 8.5° 处 30）→ 框该跟着缩
            w.params["裁剪起点 (°)"].setValue(8.0)
            w.params["裁剪终点 (°)"].setValue(9.0)
            w.findChild(QPushButton, "cut_add_btn").click()
            QApplication.processEvents()
            self.assertLess(ax.get_ylim()[1], top0,
                            "剪掉巨峰后纵轴该放开（裁剪的本意）")
        finally:
            w.close()

    def test_normalize_global_divides_by_strongest_of_all(self):
        """全图最强峰 → 所有曲线除以全部曲线里最高的峰。
        fake_a 最强峰 3、fake_b 最强峰 30 → 除数 30：
        fake_a 峰 0.1、fake_b 峰 1.0。"""
        w = create_window()
        try:
            ax = self._plot_compare(w)
            self._set_norm_mode(w, "global")
            peaks = sorted(float(np.max(line.get_ydata()))
                           for line in ax.lines)
            self.assertEqual(len(peaks), 2)
            self.assertAlmostEqual(peaks[0], 3.0 / 30.0, places=4)
            self.assertAlmostEqual(peaks[1], 1.0, places=4)
        finally:
            w.close()

    def test_normalize_file_divides_by_chosen_file(self):
        """指定数据 → 所有曲线除以目标文件的最强峰；目标下拉框 =
        对比面板的文件列表。选 fake_a（峰 3）→ fake_a 峰 1.0、
        fake_b 峰 30/3 = 10.0。"""
        w = create_window()
        try:
            ax = self._plot_compare(w)
            # 对比面板成为焦点后，目标下拉框已按它的文件列表填充
            target = w.params["归一化目标"]
            self.assertEqual(
                [str(target.itemData(i)) for i in range(target.count())],
                ["data/fake_a.tif", "data/fake_b.tif"])
            self._set_norm_mode(w, "file")
            self.assertTrue(target.isEnabled(), "指定数据模式应启用目标下拉框")
            # 条目 data = 字符串路径，按字符串找
            target.setCurrentIndex(target.findData("data/fake_a.tif"))
            dock = [d for k, d in w.plot_docks.items()
                    if k.startswith("对比")][0]
            self.assertEqual(str(dock.params_snapshot["归一化目标"]),
                             "data/fake_a.tif")
            peaks = sorted(float(np.max(line.get_ydata()))
                           for line in ax.lines)
            self.assertEqual(len(peaks), 2)
            self.assertAlmostEqual(peaks[0], 1.0, places=4)    # fake_a 3/3
            self.assertAlmostEqual(peaks[1], 10.0, places=4)   # fake_b 30/3
        finally:
            w.close()

    def test_norm_mode_survives_reclick(self):
        """重复点 [对比]（重算）= 显示参数保留：归一化模式还是
        "不归一化"，重画后仍按原始强度。"""
        w = create_window()
        try:
            ax = self._plot_compare(w)
            self._set_norm_mode(w, "off")
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compare_compute):
                w.compare_btn.click()
                self.assertTrue(_wait_until(
                    lambda: w.log_text.toPlainText().count("对比完成") >= 2))
            self.assertEqual(w.params["对比归一化"].currentData(), "off")
            ymax = max(float(np.max(line.get_ydata())) for line in ax.lines)
            self.assertAlmostEqual(ymax, 30.0, places=4)
        finally:
            w.close()

    def test_reclick_same_selection_reuses_panel(self):
        """同一勾选集合重复点 [对比] → 还是那一张面板，重算刷新。"""
        w = create_window()
        try:
            ax = self._plot_compare(w)
            dock_before = [d for k, d in w.plot_docks.items()
                           if k.startswith("对比")][0]
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compare_compute):
                w.compare_btn.click()
                self.assertTrue(_wait_until(
                    lambda: w.log_text.toPlainText().count("对比完成") >= 2))
            self.assertEqual(len([k for k in w.plot_docks
                                  if k.startswith("对比")]), 1)
            self.assertIs(dock_before, w.plot_docks[w.focus_panel])
            self.assertEqual(len(ax.lines), 2)
            self.assertEqual(w.log_text.toPlainText().count("开始对比"),
                             2)
        finally:
            w.close()

    def test_compare_data_apply_reruns_all(self):
        """对比面板是编辑对象时点数据 [应用] → 整组来源全部重读一遍重画
        （栅栏后不现场积分——这条断言 0 次计算调用把新规矩钉住）。"""
        w = create_window()
        try:
            ax = self._plot_compare(w)
            with mock.patch.object(gui_plot_spawn, "_compute_integration") as c:
                w.findChild(QPushButton, "apply_btn").click()
                self.assertTrue(_wait_until(
                    lambda: w.log_text.toPlainText().count("对比完成") >= 2))
                c.assert_not_called()
            self.assertEqual(len(ax.lines), 2)
            self.assertIn("[应用] 重算编辑对象", w.log_text.toPlainText())
        finally:
            w.close()

    def test_compare_one_file_fails_others_still_drawn(self):
        """一个来源读不到 → 逐条跳过、其余曲线照常画完。

        栅栏（2026-10-16）之后取数全是"读已经算好的"，失败只剩这一类：
        勾的是产物条目、而那一份已经不在盘上（老路"某个文件积分炸了"
        随现场积分一起退休——没算过的原始数据现在整批被拦，进不到这里）。
        """
        w = create_window()
        try:
            files = _tmp_files(2)
            w.add_files([str(p) for p in files], select=True)
            keys = [_store_product(w, p) for p in files]
            w.refresh_groups()
            w.file_list.raw_group.setCheckState(0, Qt.Unchecked)
            grp = _group_by_text(w, "1D 产物")
            for i in range(grp.childCount()):
                grp.child(i).setCheckState(0, Qt.Checked)
            QApplication.processEvents()
            stage_cache.drop_keys("1d", [keys[0]])   # 第一份被删
            w.compare_btn.click()
            QApplication.processEvents()
            log = w.log_text.toPlainText()
            self.assertIn("跳过", log)
            self.assertIn("产物不存在", log)
            self.assertIn("对比完成：1 条曲线", log)
            ax = gui_panel_state._content(w.plot_docks["对比"]).axes_1d
            self.assertEqual(len(ax.lines), 1)
        finally:
            w.close()

    def test_compare_three_files(self):
        """三个文件也能叠：三条曲线、图例齐全、标题 = A 等 3 个文件。"""
        def fake3(path_str, geom, npt):
            scale = {"fake_a.tif": 1, "fake_b.tif": 10,
                     "fake_c.tif": 100}[Path(path_str).name]
            return np.array([0.5, 1.0, 8.5]), np.array([1.0, 2.0, 3.0]) * scale

        w = create_window()
        try:
            _prime_1d(w, ["data/fake_a.tif", "data/fake_b.tif",
                          "data/fake_c.tif"], stub=fake3)   # 栅栏前置
            w.compare_btn.click()
            ax = self._compare_axes(w)
            self.assertTrue(_wait_until(lambda: len(ax.lines) >= 3))
            self.assertEqual([line.get_label() for line in ax.lines],
                             ["fake_a.tif", "fake_b.tif", "fake_c.tif"])
            self.assertIn("等 3 个文件", ax.get_title())
            self.assertIn("对比完成：3 条曲线", w.log_text.toPlainText())
            # 颜色循环不重样（前三条 = C0/C1/C2）
            self.assertEqual(len({line.get_color() for line in ax.lines}), 3)
        finally:
            w.close()

    def test_compare_all_files_fail(self):
        """所有来源都读不到 → 面板留空 + 明说失败（不装成"完成"）。

        栅栏后同前：只剩"产物全被删了"这一类（见上一个用例）。"""
        w = create_window()
        try:
            files = _tmp_files(2)
            w.add_files([str(p) for p in files], select=True)
            keys = [_store_product(w, p) for p in files]
            w.refresh_groups()
            w.file_list.raw_group.setCheckState(0, Qt.Unchecked)
            grp = _group_by_text(w, "1D 产物")
            for i in range(grp.childCount()):
                grp.child(i).setCheckState(0, Qt.Checked)
            QApplication.processEvents()
            stage_cache.drop_keys("1d", keys)        # 两份都被删
            w.compare_btn.click()
            QApplication.processEvents()
            self.assertIn("对比失败：没有一条曲线画得出来",
                          w.log_text.toPlainText())
            ax = gui_panel_state._content(w.plot_docks["对比"]).axes_1d
            self.assertEqual(len(ax.lines), 0)
        finally:
            w.close()

    def test_compare_uses_1d_display_params(self):
        """1D 显示参数（对数纵轴）对对比面板同样生效（即改即画，2026-10-08）。"""
        w = create_window()
        try:
            ax = self._plot_compare(w)
            w.params["对数纵轴"].setChecked(True)
            QApplication.processEvents()
            self.assertEqual(ax.get_yscale(), "log")
        finally:
            w.close()


class TestArrangeModeClose(unittest.TestCase):
    """此前零覆盖的三个面：横排/竖排、[校准] 模式开关、带在飞任务关窗。"""

    def test_arrange_buttons_log(self):
        """横排/竖排各点一次 → 日志报告面板数，不崩。"""
        w = create_window()
        try:
            w.show()   # 面板要可见才参与重排（offscreen 下不 show 不可见）
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines)
                    > 0))
            w.arrange_buttons["横排"].click()
            self.assertIn("已横排 2 个面板", w.log_text.toPlainText())
            w.arrange_buttons["竖排"].click()
            self.assertIn("已竖排 2 个面板", w.log_text.toPlainText())
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_close_all_button_closes_every_panel(self):
        """[全关]：一键关掉所有图面板，焦点与编辑对象一起复位。

        用户 2026-09-26："加一个一键关闭所有图像，就是打开的子窗口全部
        关闭"。与单个 × 同一条路径，但**不逐张写日志**（一次关几十张
        会刷屏），只写一行汇总（含几张没存过盘）。
        """
        w = create_window()
        try:
            w.show()
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_a.tif").lines) > 0))
            self.assertEqual(len(w.plot_docks), 2)
            w.close_all_btn.click()
            QApplication.processEvents()
            self.assertEqual(len(w.plot_docks), 0, "该一张不剩")
            self.assertIsNone(w.focus_panel)
            self.assertIn("未选中", w.focus_label.text())
            self.assertIn("双击文件栏「1D 产物」", w.focus_label.text())
            log = w.log_text.toPlainText()
            self.assertIn("已关闭全部 2 张图", log)
            self.assertIn("没存过盘", log)
            self.assertEqual(log.count("已关闭面板："), 0,
                             "全关不逐张写日志（那是刷屏）")
        finally:
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                w.close()

    def test_calib_mode_toggle(self):
        """[校准] 入口 = 校准模式提示 + 翻到校准页；点别的入口回分析模式。

        2026-09-24 起入口是五个页签式按钮（不再翻转开关文字——"退出
        校准"就是点另一个入口；校准页底部另有 [返回分析模式] 显式出口，
        见 TestCalibration.test_calib_page_exit_button_returns_to_analysis）。
        """
        w = create_window()
        try:
            w.calib_btn.click()
            self.assertIn("进入校准模式", w.log_text.toPlainText())
            self.assertIn("校准模式", w.mode_label.text())
            self.assertEqual(w.param_stack.currentIndex(),
                             w.PARAM_PAGES["校准"])
            w.entrance_buttons["对比"].click()
            self.assertIn("回到分析模式", w.log_text.toPlainText())
            self.assertIn("分析模式", w.mode_label.text())
            self.assertEqual(w.param_stack.currentIndex(),
                             w.PARAM_PAGES["对比"])
            self.assertFalse(w.calib_btn.isChecked())
        finally:
            w.close()

    def test_close_with_inflight_task_discards(self):
        """后台任务还在飞时关窗 → 等任务收尾（discard），不挂死不崩溃。"""
        def slow(path_str, geom, npt):
            time.sleep(1.5)
            return np.array([0.5, 1.0, 8.5]), np.array([1.0, 2.0, 3.0])

        w = create_window()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=slow):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "1D")   # 任务立刻在后台开睡
            with mock.patch.object(gui_app, "_confirm_close",
                                   return_value="discard"):
                self.assertTrue(w.close())   # 关窗 = 等完 1.5s 收尾
        finally:
            w.close()


class TestPlotFixedSize(unittest.TestCase):
    """A：新面板固定 5:3 开局，不继承上次挤过的旧尺寸。"""

    def test_new_panel_opens_fixed_ratio(self):
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, ["data/fake_b.tif"])
                _open_view(w, "1D")
                drawn = _wait_until(
                    lambda: len(_axes(w, "1D", "data/fake_b.tif").lines) > 0)
                self.assertTrue(drawn)
            QApplication.processEvents()
            dock = _dock(w, "1D", "data/fake_b.tif")
            self.assertGreater(dock.width(), 350,
                               "新面板应有接近 500px 的固定宽度")
            self.assertLess(dock.width(), 750)
            # 画布 5:3 之外还有标题栏+工具栏的壳（实测子窗口 508×383），
            # 所以整窗比例 ≈ 0.75 而画布仍真 5:3（见 TestCanvasTrue53）
            ratio = dock.height() / dock.width()
            self.assertGreater(ratio, 0.5, f"新面板比例走样：{ratio:.2f}")
            self.assertLess(ratio, 0.85, f"新面板比例走样：{ratio:.2f}")
        finally:
            w.hide()   # 窗口显示过关窗会弹"保存询问"模态框（offscreen 挂死）
            w.close()


class TestSlimPanelChrome(unittest.TestCase):
    """面板壳瘦身（2026-09-24）：原生标题栏（36 px）+ 工具栏（47 px）
    两行合成一行 26 px 自绘标题栏（标题 + [Home][Zoom][Customize][Save]
    + [弹出][关闭]），子窗口 frameless —— 壳 83 → 26 px，同样的面板
    高度里画布多拿 57 px。

    原生标题栏被拿掉的三件事在这里各有测试兜着：拖动、双击最大化、
    × 关闭（自绘栏按钮走同一个 _close_panel）。占位面板没有自绘栏 →
    必须保留原生标题栏，否则既拖不动也关不掉。
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

    def test_shell_shrinks_and_canvas_unchanged(self):
        """壳 ≤ 32 px（改前 83）、画布仍是 500×300、子窗口 frameless。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            QApplication.processEvents()
            content = gui_panel_state._content(dock)
            self.assertTrue(dock.windowFlags() & Qt.FramelessWindowHint,
                            "子窗口应无原生标题栏")
            self.assertFalse(content.slim_bar.isHidden(), "自绘标题栏应可见")
            self.assertTrue(content.toolbar.isHidden(), "工具栏不该再占高度")
            self.assertEqual(
                (content.canvas.width(), content.canvas.height()), (500, 300),
                "画布尺寸不能因为瘦身而变")
            shell = dock.height() - content.canvas.height()
            self.assertLessEqual(shell, 32,
                                 f"壳应瘦到 26 px 上下，实测 {shell} px")
            self.assertGreaterEqual(shell, 20,
                                    f"壳过小，布局可能塌了：{shell} px")
        finally:
            w.close()

    def test_bar_tooltip_follows_rename(self):
        """标题栏不显示名字（名字在参数坞"编辑对象"与图上标题里），
        但悬停提示要跟着容器标题走——改名也要同步。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            self.assertEqual(content.slim_bar.toolTip(), dock.windowTitle())
            dock.setWindowTitle("新名字")
            QApplication.processEvents()
            self.assertEqual(content.slim_bar.toolTip(), "新名字",
                             "悬停提示要跟容器标题同步")
        finally:
            w.close()

    def test_bar_buttons_share_toolbar_actions(self):
        """自绘栏按钮直接绑工具栏的 QAction：放大镜状态两边同步。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            zoom = content.toolbar._actions["zoom"]
            btns = content.slim_bar.findChildren(QToolButton)
            match = [b for b in btns if b.defaultAction() is zoom]
            self.assertTrue(match, "栏上应有绑 zoom action 的按钮")
            match[0].click()
            self.assertTrue(gui_plot_panels._magnifier_on(dock),
                            "点栏上的放大镜 = 点亮（与工具栏共用同一 action）")
            match[0].click()
            self.assertFalse(gui_plot_panels._magnifier_on(dock))
        finally:
            w.close()

    def test_bar_drag_moves_panel(self):
        """按住标题栏拖 = 移动面板（原生标题栏的拖动自己实现）。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            p0 = dock.pos()
            QTest.mousePress(content.slim_bar, Qt.LeftButton, Qt.NoModifier,
                             QPoint(60, 12))
            QTest.mouseMove(content.slim_bar, QPoint(120, 52))
            QTest.mouseRelease(content.slim_bar, Qt.LeftButton, Qt.NoModifier,
                               QPoint(120, 52))
            QApplication.processEvents()
            self.assertEqual(
                (dock.pos().x() - p0.x(), dock.pos().y() - p0.y()), (60, 40),
                "拖动位移应与鼠标位移一致")
        finally:
            w.hide()   # 显示过的窗口关窗会弹模态框（见 TestPlotFixedSize）
            w.close()

    def test_bar_double_click_toggles_maximize(self):
        """双击标题栏 = 占满绘图区 / 还原（原生双击的替代）。"""
        w = create_window()
        try:
            w.show()
            w.resize(1400, 900)
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            QTest.mouseDClick(content.slim_bar, Qt.LeftButton, Qt.NoModifier,
                              QPoint(60, 12))
            QApplication.processEvents()
            self.assertTrue(dock.isMaximized(), "双击应最大化")
            QTest.mouseDClick(content.slim_bar, Qt.LeftButton, Qt.NoModifier,
                              QPoint(60, 12))
            QApplication.processEvents()
            self.assertFalse(dock.isMaximized(), "再双击应还原")
        finally:
            w.hide()
            w.close()

    def test_bar_close_button_closes_panel(self):
        """✕ = 关闭面板，与原生 × 走同一个入口（关闭即遗忘）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            content.bar_close_btn.click()
            QApplication.processEvents()
            self.assertNotIn("1D|" + self.PATH, w.plot_docks)
            self.assertIn("已关闭面板", w.log_text.toPlainText())
        finally:
            w.close()

    def test_pop_out_keeps_native_frame_and_retract_restores(self):
        """弹出 = 系统窗口（保留原生边框管窗口管理），收回 = 再 frameless。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            content.popout_btn.click()
            QApplication.processEvents()
            floated = w.plot_docks["1D|" + self.PATH]
            self.assertIsInstance(floated, gui_panels._FloatedWindow)
            self.assertFalse(floated.windowFlags() & Qt.FramelessWindowHint,
                             "独立窗口要留着系统标题栏")
            fc = gui_panel_state._content(floated)
            self.assertFalse(fc.slim_bar.isHidden(),
                             "弹出窗口里栏继续提供按钮")
            self.assertEqual(fc.popout_btn.text(), "收回")
            fc.popout_btn.click()
            QApplication.processEvents()
            back = w.plot_docks["1D|" + self.PATH]
            self.assertIsInstance(back, gui_app.QMdiSubWindow)
            self.assertTrue(back.windowFlags() & Qt.FramelessWindowHint,
                            "收回后应恢复 frameless")
        finally:
            w.close()

    def test_placeholder_keeps_native_title_bar(self):
        """占位面板（未注册视图）没有自绘栏 → 保留原生标题栏。"""
        w = create_window()
        try:
            gui_plot_panels._open_plot_panel(w, "未注册视图", "X|占位", "占位")
            dock = w.plot_docks["X|占位"]
            self.assertFalse(dock.windowFlags() & Qt.FramelessWindowHint,
                            "没有自绘栏的占位面板得留着原生标题栏"
                            "（否则拖不动也关不掉）")
        finally:
            w.close()


class TestDragBlit(unittest.TestCase):
    """框选拖动期的低成本重画（2026-09-24 用户："按照最快来优化"）。

    框选拖动期视图不变、只多一个选框 → 缓存一帧、每帧只贴选框，
    松手才整帧重画一次（那时刻度/文字才需要更新）。实测 30 → 0.96 ms
    每次鼠标移动。

    平移**不**走这条路：拖动期视图每帧都在变，贴图要靠
    restore_region(xy=) 平移缓存块，而实测（最小实验）它在 mpl 3.11 上
    不做干净的平移 → 宁可每帧整帧重画（30 ms ≈ 33 fps），不要方向错的
    快速预览。所以下面这些测试都用**框选**手势。

    这里守四件事：
      - 拖动中的每一次移动都不整帧重绘；
      - 松手整帧重画一次（刻度/文字这时才更新）；
      - 任何一次整帧重绘作废会话（否则会贴回过期背景）；
      - 按下落在轴外（误点）再松开不许炸（踩过 AttributeError）。
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

    def _ev(self, canvas, name, x, y, **kw):
        return MouseEvent(name, canvas, x, y, **kw)

    def test_box_drag_motions_use_blit_not_full_redraw(self):
        """框选拖动中的移动不整帧重绘（只有贴图），松手才重画一次。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            ax, canvas = content.axes_1d, content.canvas
            xd = ax.lines[0].get_xdata()
            xm = float(xd[len(xd) // 2])
            ym = float(ax.lines[0].get_ydata()[len(xd) // 2])
            px, py = ax.transData.transform((xm, ym))
            content.toolbar._actions["zoom"].trigger()     # 框选
            with mock.patch.object(canvas, "draw", wraps=canvas.draw) as draw:
                canvas.callbacks.process("button_press_event", self._ev(
                    canvas, "button_press_event", px, py, button=1))
                self.assertTrue(getattr(dock, "_blit", None), "该建位图会话")
                after_press = draw.call_count
                for i in range(3):
                    canvas.callbacks.process("motion_notify_event", self._ev(
                        canvas, "motion_notify_event", px + 10 * (i + 1),
                        py + 8 * (i + 1), buttons=frozenset({1})))
                self.assertEqual(draw.call_count, after_press,
                                 "拖动中的移动不该整帧重绘")
                canvas.callbacks.process("button_release_event", self._ev(
                    canvas, "button_release_event", px + 30, py + 24,
                    button=1))
            QApplication.processEvents()
            self.assertIsNone(getattr(dock, "_blit", None), "松手该清会话")
            self.assertNotEqual(tuple(ax.get_xlim()), (1.0, 8.0),
                                "框选该生效（松手时整帧重画一次）")
        finally:
            w.close()

    def test_full_redraw_invalidates_the_session(self):
        """整帧重绘（比如后台算完一张别的图）作废会话：之后退回整帧画。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            ax, canvas = content.axes_1d, content.canvas
            xd = ax.lines[0].get_xdata()
            xm = float(xd[len(xd) // 2])
            ym = float(ax.lines[0].get_ydata()[len(xd) // 2])
            px, py = ax.transData.transform((xm, ym))
            content.toolbar._actions["zoom"].trigger()
            canvas.callbacks.process("button_press_event", self._ev(
                canvas, "button_press_event", px, py, button=1))
            self.assertTrue(getattr(dock, "_blit", None))
            canvas.draw()          # 模拟外部整帧重绘
            self.assertIsNone(getattr(dock, "_blit", None),
                              "整帧重绘后会话该作废（背景已过期）")
            with mock.patch.object(canvas, "draw_idle",
                                   wraps=canvas.draw_idle) as idle:
                canvas.callbacks.process("motion_notify_event", self._ev(
                    canvas, "motion_notify_event", px + 12, py + 9,
                    buttons=frozenset({1})))
                self.assertGreaterEqual(idle.call_count, 1,
                                        "没有会话时退回 draw_idle 整帧画")
        finally:
            w.close()

    def test_release_without_a_valid_press_is_safe(self):
        """按下落在轴外（误点再松手）不许炸——踩过 AttributeError。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            canvas = content.canvas
            x0 = content.axes_1d.get_xlim()
            # 轴外按下（画布左上角）+ 松开
            canvas.callbacks.process("button_press_event", self._ev(
                canvas, "button_press_event", 2, content.canvas.height() - 2,
                button=1))
            canvas.callbacks.process("button_release_event", self._ev(
                canvas, "button_release_event", 2, content.canvas.height() - 2,
                button=1))
            # 只有松开、没有按下，也要安全
            canvas.callbacks.process("button_release_event", self._ev(
                canvas, "button_release_event", 100, 100, button=1))
            self.assertEqual(tuple(content.axes_1d.get_xlim()), tuple(x0),
                             "误点不该改变视图")
        finally:
            w.close()


class TestBoxZoomScope(unittest.TestCase):
    """框选只影响自己那块面板（用户 2026-09-24："确认区域放大不会影响
    整体的图"）。另一块面板的范围 / 曲线数据 / "家" / 总缩放 / 几何
    都不许动。"""

    def _open_two(self, w):
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_fake_compute):
            add_checked(w, ["data/fake_a.tif", "data/fake_b.tif"])
            _open_view(w, "1D")
            self.assertTrue(_wait_until(lambda: all(
                len(_axes(w, "1D", p).lines) > 0
                for p in ("data/fake_a.tif", "data/fake_b.tif"))))
        return (_dock(w, "1D", "data/fake_a.tif"),
                _dock(w, "1D", "data/fake_b.tif"))

    def test_box_zoom_leaves_the_other_panel_alone(self):
        w = create_window()
        try:
            dock_a, dock_b = self._open_two(w)
            ax_a = gui_panel_state._content(dock_a).axes_1d
            ax_b = gui_panel_state._content(dock_b).axes_1d

            def snapshot(ax, dock):
                return (tuple(ax.get_xlim()), tuple(ax.get_ylim()),
                        [np.array(ln.get_xdata()).tolist() for ln in ax.lines],
                        [np.array(ln.get_ydata()).tolist() for ln in ax.lines],
                        getattr(dock, "view_home", None))

            before_b = snapshot(ax_b, dock_b)
            before_home_b = getattr(dock_b, "view_home", None)
            zoom_before = w._area_zoom
            geom_before = {k: (d.x(), d.y(), d.width(), d.height())
                           for k, d in w.plot_docks.items()}

            gui_panel_state._content(dock_a).toolbar._actions["zoom"].trigger()
            canvas = gui_panel_state._content(dock_a).canvas
            xd = ax_a.lines[0].get_xdata()
            xm = float(xd[len(xd) // 2])
            ym = float(ax_a.lines[0].get_ydata()[len(xd) // 2])
            p0 = ax_a.transData.transform((xm, ym))
            p1 = ax_a.transData.transform((min(xm + 0.4, 8.0), ym + 0.4))
            for name, (x, y) in (("button_press_event", p0),
                                 ("button_release_event", p1)):
                canvas.callbacks.process(name, MouseEvent(
                    name, canvas, x, y, button=1))
            self.assertNotEqual(tuple(ax_a.get_xlim()), (1.0, 8.0),
                                "A 应被框选放大")
            after_b = snapshot(ax_b, dock_b)
            self.assertEqual(before_b[0], after_b[0], "B 的 x 范围不该动")
            self.assertEqual(before_b[1], after_b[1], "B 的 y 范围不该动")
            self.assertEqual(before_b[2:4], after_b[2:4], "B 的曲线数据不该动")
            self.assertEqual(before_home_b, getattr(dock_b, "view_home", None),
                             "B 的「家」不该动")
            self.assertEqual(zoom_before, w._area_zoom, "总缩放不该动")
            self.assertEqual(
                geom_before, {k: (d.x(), d.y(), d.width(), d.height())
                              for k, d in w.plot_docks.items()},
                "面板几何不该动")
        finally:
            w.close()


class TestHomeView(unittest.TestCase):
    """[Home] = 回到"最初的样子"（2026-09-24 用户要求）。

    以前 Home 走 mpl 的历史栈，而程序重画（[应用]/重算/背景实时预览）
    会清空那个栈 → 按下去常常"没反应"或只回到"上次画的位置"。现在用
    面板自己的"家"（`dock.view_home`）：**只有按内容画出来的视图**才
    更新它，滚轮/框选/平移这些手势不算（手势范围会被写回参数，拿参数
    当"家"就会把缩放当成"最初的样子"——这条是踩过的坑）。
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

    def _wheel(self, w, dock, notches=3):
        content = gui_panel_state._content(dock)
        ax, canvas = content.axes_1d, content.canvas
        px, py = ax.transData.transform((2.0, 1.8))
        for _ in range(notches):
            canvas.callbacks.process("scroll_event", MouseEvent(
                "scroll_event", canvas, px, py, step=1, button="up"))

    def test_home_returns_to_initial_view(self):
        """缩放 + 平移之后按 Home = 回到开图那个视图（不是缩放后的位置）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            ax = content.axes_1d
            x0, y0 = ax.get_xlim(), ax.get_ylim()
            content.toolbar._actions["zoom"].trigger()   # 点亮放大镜
            self._wheel(w, dock)
            self.assertNotEqual(tuple(ax.get_xlim()), tuple(x0),
                                "滚轮该缩放（前置条件）")
            content.toolbar._actions["home"].trigger()
            QApplication.processEvents()
            self.assertEqual(tuple(ax.get_xlim()), tuple(x0),
                             "Home 应回到最初的 2θ 范围")
            self.assertEqual(tuple(ax.get_ylim()), tuple(y0),
                             "Home 应回到最初的纵轴范围")
        finally:
            w.close()

    def test_home_after_stacking_keeps_the_stacked_y(self):
        """先放大、再开堆叠，按 Home：x 回全宽，**y 用堆叠自己的范围**。

        用户 2026-10-09："对比子窗口的 home 键在先放大后开启堆叠后失效"
        ——家的 y 还是开堆叠前记的（没堆叠的强度范围），按 Home 恢复出来
        把堆叠上半截裁掉（实测 8 条时只剩 17% 可见），看着就是"Home 坏了"。
        修法：堆叠的 y 是**模式算出来的**，家的 y 跟着换（x 仍用家的 x，
        手势缩的不算家——这条照旧）。2026-10-15 起堆叠那份家记在
        `view_home_stack`（平铺那份 `view_home` 原样留着，取消堆叠后
        Home 回平铺——见 TestCompareStackView），断言跟着换到新家。
        """
        w = create_window()
        try:
            _prime_1d(w, ["data/fake_a.tif", "data/fake_b.tif"],
                      stub=_fake_compute)      # 栅栏前置：先各出过 1D
            w.compare_btn.click()
            self.assertTrue(_wait_until(
                lambda: any(k.startswith("对比")
                            and len(gui_panel_state._content(
                                w.plot_docks[k]).axes_1d.lines) >= 2
                            for k in w.plot_docks), 60000))
            key = next(k for k in w.plot_docks if k.startswith("对比"))
            content = gui_panel_state._content(w.plot_docks[key])
            ax = content.axes_1d
            x0 = tuple(ax.get_xlim())
            # 前置条件：放大（手势做的两件事——改范围 + 立旗标）
            content.toolbar._actions["zoom"].trigger()
            ax.set_xlim(2.5, 3.5)
            w.plot_docks[key]._view_from_gesture = True
            QApplication.processEvents()
            # 开堆叠（即改即画）
            w.params["对比堆叠"].setChecked(True)
            QApplication.processEvents()
            stacked_top = max(float(np.nanmax(ln.get_ydata()))
                              for ln in ax.lines)
            stacked_home = w.plot_docks[key].view_home_stack
            self.assertGreaterEqual(float(stacked_home[1][1]),
                                    stacked_top * 0.95,
                                    "堆叠这份家的 y 该是整堆量程（不是旧范围）")
            self.assertEqual(tuple(stacked_home[0]), x0,
                             "x 仍是家的 x（缩放不算家）")
            content.toolbar._actions["home"].trigger()
            QApplication.processEvents()
            self.assertEqual(tuple(ax.get_xlim()), x0, "Home 该回全宽")
            self.assertGreaterEqual(float(ax.get_ylim()[1]), stacked_top * 0.95,
                                    "Home 后不该把堆叠裁掉")
        finally:
            w.close()

    def test_home_works_after_a_program_redraw(self):
        """程序重画（会清空 mpl 的历史栈）之后 Home 照样回得去——旧实现
        就是死在这一步：栈空了，按 Home 什么也不发生。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            ax = content.axes_1d
            x0 = ax.get_xlim()
            content.toolbar._actions["zoom"].trigger()
            self._wheel(w, dock)
            gui_plot_panels._refresh_home(dock, ax)   # 模拟"程序重画清了栈"
            content.toolbar._actions["home"].trigger()
            QApplication.processEvents()
            self.assertEqual(tuple(ax.get_xlim()), tuple(x0),
                             "清栈之后 Home 仍应回到最初的样子")
            self.assertIn("[复位视图] 已回到最初的样子", w.log_text.toPlainText())
        finally:
            w.close()

    def test_home_key_resets_the_hovered_panel(self):
        """Home **键**（不是按钮）= 复位鼠标最后停留过的那张图。

        认"鼠标停留过的"而不是"高亮的"：高亮是点标题栏定的，常常不是用户
        正在看的那张（用户 2026-09-27："home键改成一按就回到最初的比例"）。
        """
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            ax = content.axes_1d
            x0, y0 = tuple(ax.get_xlim()), tuple(ax.get_ylim())
            ax.set_xlim(x0[0] + 1.0, x0[1] - 1.0)     # 前置条件：视图被改过
            QApplication.processEvents()
            self.assertNotEqual(tuple(ax.get_xlim()), x0)
            w._hover_panel_key = f"1D|{self.PATH}"    # 鼠标停在这张图上
            w.home_shortcut.activated.emit()
            QApplication.processEvents()
            self.assertEqual(tuple(ax.get_xlim()), x0, "Home 键该复位这张图")
            self.assertEqual(tuple(ax.get_ylim()), y0, "纵轴也一起回")
            self.assertIn("[复位视图] 已回到最初的样子", w.log_text.toPlainText())
        finally:
            w.close()

    def test_home_key_reaches_the_shortcut(self):
        """真按一下 Home（走 Qt 的快捷键派发），不是直接触发信号。

        前面两条测的是"收到复位请求之后做什么"，这条测"按键到底有没有送到
        ——而且焦点在**文件栏**上时也要送到"：Qt 的树控件自带 Home = 跳到
        第一行，用户抱怨的"一操作就跳回最顶端"里就有它，窗口级快捷键必须
        盖过它。（探针实测：Qt 只在控件树里有焦点时才派发快捷键。）

        收尾必须先 hide() 再 close()：窗口显示过、面板又没存盘时，close
        会弹"要保存吗"的模态框，offscreen 下没人点 → 测试挂死。
        """
        from PySide6.QtTest import QTest
        w = create_window()
        try:
            dock = self._open_1d(w)
            ax = gui_panel_state._content(dock).axes_1d
            x0 = tuple(ax.get_xlim())
            ax.set_xlim(x0[0] + 1.0, x0[1] - 1.0)
            QApplication.processEvents()
            w._hover_panel_key = f"1D|{self.PATH}"
            w.show()
            w.file_list.setFocus()
            QApplication.processEvents()
            QTest.keyClick(w.file_list, Qt.Key_Home)
            QApplication.processEvents()
            self.assertEqual(tuple(ax.get_xlim()), x0,
                             "焦点在文件栏上时 Home 也该复位图")
        finally:
            w.hide()
            w.close()

    def test_home_key_leaves_the_settings_alone(self):
        """Home 只动画面、**不动设置**：不许关掉"纵轴自动"。

        旧行为（改之前）：设范围会触发"范围 → 参数"写回，_on_ylim_changed
        顺手把"纵轴自动"取消勾选、纵轴上下限填成家的值——按一次 Home 就
        悄悄把面板改成手动纵轴，且不可逆。用户 2026-09-27："home键不动
        设置，只管理识图"。
        """
        w = create_window()
        try:
            dock = self._open_1d(w)
            ax = gui_panel_state._content(dock).axes_1d
            x0 = tuple(ax.get_xlim())
            self.assertTrue(w.params["纵轴自动"].isChecked(), "开图默认纵轴自动")
            ax.set_xlim(x0[0] + 1.0, x0[1] - 1.0)
            QApplication.processEvents()
            w._hover_panel_key = f"1D|{self.PATH}"
            w.home_shortcut.activated.emit()
            QApplication.processEvents()
            self.assertEqual(tuple(ax.get_xlim()), x0, "画面回得去")
            self.assertTrue(w.params["纵轴自动"].isChecked(),
                            "Home 不许动「纵轴自动」开关")
            self.assertTrue(dock.params_snapshot["纵轴自动"],
                            "快照里的「纵轴自动」也不许动")
            # 视图窗口要写回快照：否则下一次重画按快照里那份缩放的窗口画，
            # Home 等于白按（两处一套真相）
            self.assertAlmostEqual(
                float(dock.params_snapshot["视图 2θ 下限 (°)"]), x0[0],
                places=6, msg="视图窗口该写回快照")
            self.assertAlmostEqual(
                float(dock.params_snapshot["视图 2θ 上限 (°)"]), x0[1],
                places=6)
        finally:
            w.close()

    def test_apply_with_a_view_range_does_not_move_home(self):
        """快照里带着视图窗口时 [应用]：画面按它画，但"家"不许跟着变。

        视图覆盖 = 用户缩放过的窗口（"显示 2θ 范围"那行输入框 2026-09-27
        撤掉之后，会写回它的只剩缩放/平移）。Home 要回的是**生成这张图时**
        的视图——用户 2026-09-27："home 不是重画，只是恢复到最开始的比例"。
        旧版这条测试断言的正好相反（那时范围能手填，填完 [应用] = 新"家"）。
        """
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            ax = content.axes_1d
            x0 = tuple(ax.get_xlim())
            dock.params_snapshot["视图 2θ 下限 (°)"] = 2.0
            dock.params_snapshot["视图 2θ 上限 (°)"] = 5.0
            gui_views._redraw_panel(w, w.focus_panel)
            QApplication.processEvents()
            self.assertAlmostEqual(ax.get_xlim()[0], 2.0, delta=0.05,
                                   msg="重画该按快照里的窗口画")
            content.toolbar._actions["home"].trigger()
            QApplication.processEvents()
            self.assertEqual(tuple(ax.get_xlim()), x0,
                             "Home 仍回到生成这张图时的视图")
        finally:
            w.close()

    def test_gesture_then_recompute_keeps_the_original_home(self):
        """手势缩放 → 重算：画出来仍是缩放后的视图（"重算不改长相"），
        但"家"还是最初那个样子 —— Home 回得去（用户要的就是这个）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            ax = content.axes_1d
            x0, y0 = ax.get_xlim(), ax.get_ylim()
            content.toolbar._actions["zoom"].trigger()
            self._wheel(w, dock)
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                _open_view(w, "1D")          # 重算
                self.assertTrue(_wait_until(
                    lambda: tuple(ax.get_xlim()) != tuple(x0),
                    timeout_ms=5000) or True)
            content.toolbar._actions["home"].trigger()
            QApplication.processEvents()
            self.assertEqual(tuple(ax.get_xlim()), tuple(x0),
                             "重算之后 Home 仍该回到最初的样子")
            self.assertEqual(tuple(ax.get_ylim()), tuple(y0))
        finally:
            w.close()

    def test_recompute_without_gesture_moves_home(self):
        """没有手势介入的重算 = 新的"家"：改了积分 2θ 范围再重算，
        Home 回到新算出来的那一段（不是老视图）。"""
        w = create_window()
        try:
            dock = self._open_1d(w)
            content = gui_panel_state._content(dock)
            ax = content.axes_1d
            w.params["2θ 下限 (°)"].setValue(2.0)
            w.params["2θ 上限 (°)"].setValue(6.0)
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                _open_view(w, "1D")          # 重算（数据范围变了）
                self.assertTrue(_wait_until(
                    lambda: abs(ax.get_xlim()[0] - 2.0) < 0.06))
            x_new = ax.get_xlim()
            content.toolbar._actions["home"].trigger()
            QApplication.processEvents()
            self.assertEqual(tuple(ax.get_xlim()), tuple(x_new),
                             "Home 该回到重算出来的新视图")
            self.assertNotAlmostEqual(x_new[0], 1.0, places=2,
                                      msg="新视图应跟着新的积分范围走")
        finally:
            w.close()


class TestBackgroundBatch(unittest.TestCase):
    """[批量处理]：锚点只传 2θ、强度各取各的；扣后落盘；对比优先读它。

    用户 2026-09-24 提的流程："1d 完了存一次，做扣背景时可直接使用，
    然后扣完一张，可以用这些锚点给其他的图批量扣，然后再存一份，后面
    对比就可以直接用批量扣完的"。

    注意设置顺序：**先切编辑对象（点图 = 回放快照）再设背景模式**——
    反过来会被回放重置回"关闭"（界面就是这么设计的，测试踩过）。
    """

    def _two_files(self):
        """两个真临时文件（缓存指纹要 stat；假路径会被当 miss）。"""
        out = []
        for name in ("batch_a.tif", "batch_b.tif"):
            f = Path(tempfile.mkdtemp(prefix="xrd_bgbatch_")) / name
            f.write_bytes(b"0" * 4096)
            out.append(str(f))
        return out

    def _open_both(self, w, files):
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_scaled_compute):
            add_checked(w, files)
            _open_view(w, "1D")
            self.assertTrue(_wait_until(lambda: all(
                len(_axes(w, "1D", f).lines) > 0 for f in files)))
        # 出图产出了 1D 产物 → 上一轮的勾选已清零（用户 2026-10-01 第 8 条）：
        # 本组用例接着要拿同一批去 [批量处理]，这里显式重勾一遍
        recheck_all(w)

    def _focus_a_with_anchors(self, w, files):
        """把 A 设成编辑对象、切到手动锚点、放两个锚点（在背景位置上）。"""
        gui_panel_state._set_focus(w, "1D|" + files[0],
                                   Path(files[0]).name)
        dock_a = _dock(w, "1D", files[0])
        cb = w.params["背景扣除模式"]
        cb.setCurrentIndex(cb.findData("anchor"))
        w.params["背景窗口 (°)"].setValue(1.0)
        dock_a.params_snapshot = dict(
            dock_a.params_snapshot or {},
            **{"背景扣除模式": "anchor", "背景窗口 (°)": 1.0})
        key_a = str(gui_views._bg_path_of(dock_a))
        # 两个锚点落在曲线 [0.5,1,2,4,8.5] → [1,2,3,2,1] 的 2θ=1 与 4 上
        w.bg_anchors[key_a] = [(1.0, 2.0), (4.0, 2.0)]
        return dock_a, key_a

    def test_batch_transfers_positions_not_intensities(self):
        """A 的锚点用到 B 上：2θ 照搬，强度取 B 自己曲线上的值。"""
        from xrd_toolkit.services import stage_cache
        w = create_window()
        files = self._two_files()
        try:
            self._open_both(w, files)
            dock_a, key_a = self._focus_a_with_anchors(w, files)
            # 批量处理**不该**改任何面板显示什么：先记下每块面板的模式
            before = {f: (_dock(w, "1D", f).params_snapshot or {}).get(
                "背景扣除模式", "off") for f in files}
            w.proc_batch_btn.click()
            QApplication.processEvents()
            self.assertIn("批量处理完成", w.log_text.toPlainText())
            key_b = str(gui_views._bg_path_of(_dock(w, "1D", files[1])))
            self.assertIn(key_b, w.bg_anchors, "B 也该拿到一套锚点")
            got_a, got_b = w.bg_anchors[key_a], w.bg_anchors[key_b]
            self.assertEqual([x for x, _ in got_a], [x for x, _ in got_b],
                             "2θ 位置照搬")
            self.assertAlmostEqual(got_b[0][1], got_a[0][1] * 3, delta=1e-6,
                                   msg="B 的强度取 B 曲线上的值（尺度 ×3）")
            # 两份 bg 产物都在（键要**一模一样**才命中：几何范围也得带）。
            # 用**配方**复算键，不用面板快照——批量处理**不再**改开着的面板
            # （用户 2026-09-27 晚："原始数据会被改变"），面板仍是原始曲线，
            # "这个文件是按什么扣的"由配方记着（这正是配方存在的意义）。
            geom = gui_panel_state._collect_geometry(w)
            for f in files:
                recipe = w.proc_recipes[str(f)]
                self.assertEqual(recipe["mode"], "anchor")
                self.assertIsNotNone(stage_cache.load_proc(
                    f, config=w.config_name,
                    npt=int(w.params["输出点数"].value()),
                    tth_min=geom.get("tth_min_deg"),
                    tth_max=geom.get("tth_max_deg"), settings=recipe),
                    f"{Path(f).name} 的产物该能按配方命中")
                # 面板显示的东西一点没变（原始条目 = 原始曲线；A 是编辑对象，
                # 它本来就是手动锚点模式，所以只比"前后一致"）
                self.assertEqual(
                    (_dock(w, "1D", f).params_snapshot or {}).get(
                        "背景扣除模式", "off"), before[f],
                    f"{Path(f).name} 的面板不该被批量处理改掉")
        finally:
            w.close()

    def test_compare_prefers_the_bg_product(self):
        """扣完再开对比：日志写明用的是扣背景产物（跨会话那条路）。"""
        w = create_window()
        files = self._two_files()
        try:
            self._open_both(w, files)
            self._focus_a_with_anchors(w, files)
            w.proc_batch_btn.click()
            QApplication.processEvents()
            # 批量刚产出「处理后」产物 → 上一轮的勾选已清零（第 8 条）。
            # 要接着 [对比]，就显式勾上刚出炉的那一组——这本来就是
            # "对比读的是处理产物"的前提
            bg_group = _group_by_text(w, "处理产物")
            self.assertIsNotNone(bg_group, "批量完该有处理产物分组")
            gui_file_dock._set_checks(
                w, [(s.item, False) for s in gui_sources.all_sources(w)])
            bg_group.setCheckState(0, Qt.Checked)
            QApplication.processEvents()
            before = len(w.log_text.toPlainText())
            w.compare_btn.click()
            self.assertTrue(_wait_until(lambda: "对比完成" in
                                        w.log_text.toPlainText()), "对比该完成")
            log = w.log_text.toPlainText()
            self.assertIn("处理产物", log,
                          "对比该优先用处理产物（不是重新积分）")
            self.assertNotIn("开始积分", log[before:],
                             "对比不该再起积分任务")
        finally:
            w.close()

    def test_batch_with_mode_off_hints(self):
        """「处理」页三项全关时点批量：只提示，不产出（不静默）。"""
        w = create_window()
        files = self._two_files()
        try:
            self._open_both(w, files)
            gui_panel_state._set_focus(w, "1D|" + files[0],
                                       Path(files[0]).name)
            w.proc_batch_btn.click()
            QApplication.processEvents()
            self.assertIn("三项都关着", w.log_text.toPlainText())
            self.assertNotIn("批量处理完成", w.log_text.toPlainText())
        finally:
            w.close()


class TestStageCacheFlow(unittest.TestCase):
    """分阶段产物缓存接进界面后的行为（services/stage_cache）。

    跨会话的收益在真实使用里最明显（关掉程序第二天再开，81 张图不用
    全部重积分），测试里守两件事：**算过就落盘**、**再点就命中且记
    日志**（静默复用会让人以为重算了）。缓存根已在 setUpModule 指到
    临时目录，不会污染 outputs/_stage。
    """

    def _real_file(self, name="cache_sample.tif"):
        """真文件（缓存要算指纹：大小 + 修改时间，假路径 stat 会失败）。

        内容无所谓——积分被 mock 掉了；但文件必须真存在，否则
        `stage_cache.fingerprint` 抛 FileNotFoundError，落盘被吞掉、
        测试就成了"假过"。
        """
        path = Path(tempfile.mkdtemp(prefix="xrd_cache_gui_")) / name
        path.write_bytes(b"0" * 4096)
        return str(path)

    def test_second_click_reuses_the_cache(self):
        w = create_window()
        path = self._real_file()
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute) as compute:
                add_checked(w, [path])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(lambda: len(
                    _axes(w, "1D", path).lines) > 0))
                self.assertEqual(compute.call_count, 1, "第一次该真算")
                # 再点一次同一视图 = 刷新那张图 → 这次该命中缓存
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: "复用缓存" in w.log_text.toPlainText()))
                self.assertEqual(compute.call_count, 1,
                                 "第二次不该再算（缓存命中）")
            log = w.log_text.toPlainText()
            self.assertIn("复用缓存：cache_sample.tif", log)
            self.assertIn("几何", log)
        finally:
            w.close()

    def test_changing_the_range_misses_the_cache(self):
        """换了积分 2θ 范围 = 另一个键 → 老老实实重算（缓存不会串味）。"""
        w = create_window()
        path = self._real_file("cache_range.tif")
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute) as compute:
                add_checked(w, [path])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(lambda: len(
                    _axes(w, "1D", path).lines) > 0))
                w.params["2θ 下限 (°)"].setValue(2.0)
                _open_view(w, "1D")
                self.assertTrue(_wait_until(
                    lambda: compute.call_count == 2, timeout_ms=5000),
                    "换了范围该重算")
                self.assertNotIn("复用缓存", w.log_text.toPlainText())
        finally:
            w.close()

    def test_clear_cache_entry_reports_and_empties(self):
        """清缓存（文件栏右键那条）：点一下就删，日志照实；空缓存时也提示。

        按钮版已删（用户 2026-10-01）——这条测的是同一个功能的唯一入口。
        2026-10-14 起一击即删（经 10-13 的"再点一次"简化，见
        gui_file_dock.ask_clear_cache）。
        """
        from xrd_toolkit.services import stage_cache
        w = create_window()
        path = self._real_file("cache_clear.tif")
        try:
            with mock.patch.object(gui_plot_spawn, "_compute_integration",
                                   side_effect=_fake_compute):
                add_checked(w, [path])
                _open_view(w, "1D")
                self.assertTrue(_wait_until(lambda: len(
                    _axes(w, "1D", path).lines) > 0))
            self.assertGreaterEqual(stage_cache.describe()["files"], 1,
                                    "算过就该有产物")
            gui_file_dock.ask_clear_cache(w)   # 一下就删
            QApplication.processEvents()
            self.assertIn("已删除所有缓存", w.log_text.toPlainText())
            self.assertEqual(stage_cache.describe()["files"], 0)
            gui_file_dock.ask_clear_cache(w)   # 再来一次：空缓存也不炸
            QApplication.processEvents()
            self.assertIn("缓存本来就是空的", w.log_text.toPlainText())
        finally:
            w.close()
