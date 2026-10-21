"""参数坞页面级 helper：几何/数据行、图类型、裁剪、平滑。

2026-10-21 从 gui/app.py 原样搬出（只改结构，不动逻辑）。app.py 再导出，
外部照旧经 gui_app 取用。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QPushButton, QWidget

from xrd_toolkit.config import entry_display
from xrd_toolkit.gui.panel_state import _log
from xrd_toolkit.gui.plot_views import _plot_view, _refresh_proc


def _sync_geom_row(window: QMainWindow) -> None:
    """坞顶"几何配置"那一行的悬停读数（换条目 / 翻页时刷新）。

    一行管一件事：分析/积分用哪条几何条目（_collect_geometry 取
    window.config）。五个入口页共用一个下拉框，选它就换分析用的几何
    （currentIndexChanged → _apply_config → 再回调本函数刷新读数）。

    校准页上**照样能换**：校准用的是那一页自己的「当前配置」（借用 /
    手改 / 校准结果），换这里的条目不会动它——提示里写明这层区别。
    2026-09-26 晚订正：先前把这一行在校准页做成置灰（"只显示不能改"），
    结果 [删除] 正好住在校准页、却在校准页换不了选中项，删条目要
    "去分析页选中 → 回校准页点删除"跨两页（用户问"现在几何配置怎么
    删除"）。用户原话"只留能选的那一行，不能改"指的是几何**数值**不
    能改，不是不能换条目，遂放开置灰。

    读数（像素/波长/距离）来自 window.config（完整条目，_apply_config
    里挂上）；建坞阶段它还没设，此时只留条目名、提示留空。
    """
    combo = window.config_combo
    cfg = getattr(window, "config", None)
    if cfg is None:
        tip = ""
    else:
        g = cfg["geometry"]
        tip = (f"{entry_display(window.config_name, cfg)}\n"
               f"像素尺寸 {g['pixel_size_m'] * 1e6:.1f} µm\n"
               f"波长 {g['wavelength_m'] * 1e10:.4f} Å\n"
               f"距离 {g['dist_m'] * 1e3:.2f} mm")
        if cfg.get("calibrant"):
            tip += f"\n标样 {cfg['calibrant']}"
    if window.param_stack.currentIndex() == window.PARAM_PAGES["校准"]:
        tip += ("\n（校准用的是本页「当前配置」那份几何；这里换的是"
                "分析/积分用的条目）")
    combo.setToolTip(tip)
    window.geom_row.setToolTip(tip)

def _sync_data_row(window: QMainWindow) -> None:
    """坞顶"数据参数"那一行（2θ 范围 + 点数）随页显隐：**只在 1D 页显示**。

    这一行管的是 1D 积分区间。2026-10-07 用户逐页裁完（"校准页不起作用
    就藏起来"的思路推广到全部页面）：
      * 校准页：不读 2θ 区间（ring_metrics 只吃几何），藏（2026-09-27）；
      * 对比页：对比/热图只画每个文件**自己**已有的曲线，藏（2026-10-03）；
      * 处理页：该页不再有任何积分出口（[重算这张图] 已撤），藏；
      * 「原图」页：页里只剩 2D / 剖面 / 瀑布——**三个都不吃 2θ
        范围**（2D/剖面不换算；瀑布的扇区积分走全范围、显示自动定界），
        只有瀑布吃"点数"（默认 3000，无感），藏。
    于是它只剩一个家：**1D 页**（积分主台：范围 / 点数 / [重算这张图] /
    [出图（勾选文件）] 都在一起）。几何配置那一行相反，校准页要留着：
    它是借用起点，也是 [加载几何…] / [保存几何…] / [删除] 的作用对象。
    """
    page = window.param_stack.currentIndex()
    window.data_row.setVisible(page == window.PARAM_PAGES["1D"])

def _select_plot_type(window: QMainWindow, name: str) -> None:
    """点某个类型按钮：高亮它 + 立即出图（沿用旧工具栏手感）。

    本页的**出图入口就是这三个按钮**（2026-10-08：页底部那个 [出图] 与
    这里同源、纯重复，已删）——点一次出一次，"再出一次" = 再点一次已
    高亮的按钮（点已勾选的 checkable 按钮照常发 clicked，高亮由下面的
    setChecked 复原，不会停在未选中态）。

    2026-10-07 拆分后本页只剩 2D / 剖面 / 瀑布 三种类型（1D 归「1D」页、
    对比/热图归「对比」页——它们在那里本来就有产出按钮）。window.view_buttons
    里还留着 "1D" 这个名字，但它指向的是「1D」页的产出按钮（兼容别名，
    见 _build_param_dock），**不参与**类型高亮。
    """
    for key, btn in window.view_buttons.items():
        if btn.isCheckable():
            btn.setChecked(key == name)
    _plot_view(window, name)

def _build_plot_type_row(window: QMainWindow) -> QWidget:
    """「原图」页顶部的类型选择行：**2D / 剖面 / 瀑布**（2026-10-07 拆分）。

    三个都是"直接对原图"的单文件视图；1D / 对比 / 热图各回自己的家
    （1D 页的产出按钮、对比页的 [出对比]/[出热图]），本页不再摆它们的
    复制品。点一个 = 选中该类型**并立即出图**（对勾选文件）——沿用旧的
    工具栏手感（点一次算一次，纯动作不是开关）；"再出一次"就是**再点一次
    已经高亮的那个**（tooltip 里写明）。2026-10-08 前页底部另有
    [出图（勾选 N 个）]，与这里同源（同一次 _select_plot_type 调用）——
    纯重复，删了：本页的出图入口收敛到这三个按钮。按钮属性名沿用
    window.view_buttons（测试与其它模块按它们找按钮；"1D"/"对比"/"热图"
    三个名字仍可用——指向各自页的按钮，兼容别名）。
    """
    row = QWidget()
    box = QHBoxLayout(row)
    box.setContentsMargins(2, 2, 2, 2)
    box.setSpacing(3)
    window.view_buttons = {}   # 登记按钮（测试用）
    for name in ("2D", "剖面", "瀑布"):
        btn = QPushButton(name)
        btn.setCheckable(True)     # 高亮 = 当前选的类型
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setToolTip(f"对<b>文件栏里勾选</b>的条目出 {name} 图（点一次出"
                       f"一次）；它已是当前类型时再点一次 = 按现在的勾选重出")
        box.addWidget(btn)
        window.view_buttons[name] = btn
        btn.clicked.connect(lambda checked=False, n=name:
                            _select_plot_type(window, n))
    return row

def _build_check_summary(window: QMainWindow) -> QWidget:
    """「原图」页的一句话：**勾了多少条、都是什么**（外加重复勾选提醒）。

    用户 2026-09-27："选中81个图出对比图，结果出了162个文件的对比图"——
    对比没错，是勾选集比他想的大（为了出 1D 图勾过 81 个原始文件，之后
    又点了「1D 产物」组行，81+81）。这些数字原先只写在状态行角落，出图
    按钮上什么都没有，所以看不出来。放在出图按钮正上方，改一次勾选刷一次。
    """
    lbl = QLabel("")
    lbl.setObjectName("check_summary_lbl")
    lbl.setWordWrap(True)
    lbl.setStyleSheet("color: gray;")
    lbl.setToolTip("点条目行：勾这一条；点组那一行：整组一起勾；"
                   "对号方块可以取消勾选。出图按钮只画勾选的条目。")
    window.check_summary_lbl = lbl
    return lbl

def _sync_smooth_rows(window: QMainWindow) -> None:
    """平滑阶数只有 Savitzky–Golay 用得上：别的模式下置灰，别让人白填。"""
    sg = window.params["平滑方法"].currentData() == "savgol"
    window.params["平滑阶数"].setEnabled(
        bool(window.params["平滑曲线"].isChecked()) and sg)

def _on_cut_toggled(window: QMainWindow, on: bool) -> None:
    """勾上「裁剪区间」：清单空时把旁边那一段直接加进去（勾了就该有反应）。

    清单才是权威（可以删好几段），但"填了起止、勾上开关"是最自然的手势——
    空清单时按这个手势补一段，符合直觉；[添加] 用于往清单里再加一段。
    """
    if on and not window.cut_list:
        _on_cut_add(window)          # 内部会实时重画
        return
    _refresh_proc(window)

def _on_cut_add(window: QMainWindow) -> None:
    """[添加]：把当前起止加进裁剪清单（同一段重复加只留一份）。"""
    lo = float(window.params["裁剪起点 (°)"].value())
    hi = float(window.params["裁剪终点 (°)"].value())
    if not hi > lo:
        _log(window, "裁剪区间：终点要大于起点")
        return
    cuts = window.cut_list
    if (lo, hi) in cuts:
        _log(window, f"裁剪区间：{lo:g}–{hi:g}° 已经在清单里了")
        return
    cuts.append((lo, hi))
    cuts.sort()
    window.params["裁剪区间"].setChecked(True)   # 添加即生效（勾选框跟着亮）
    _sync_cut_label(window)
    _log(window, f"裁剪区间：加了 {lo:g}–{hi:g}°"
                 f"（共 {len(cuts)} 段：{_cut_text(cuts)}）")
    _refresh_proc(window)

def _on_cut_clear(window: QMainWindow) -> None:
    """[清空]：清掉裁剪清单（勾选框也弹起 = 不裁剪）。"""
    n = len(window.cut_list)
    window.cut_list.clear()
    window.params["裁剪区间"].setChecked(False)
    _sync_cut_label(window)
    _log(window, f"裁剪区间：已清空清单（原有 {n} 段）")
    _refresh_proc(window)

def _cut_text(cuts) -> str:
    """"2–3°、7–8°"。"""
    return "、".join(f"{float(lo):g}–{float(hi):g}°" for lo, hi in cuts)

def _sync_cut_label(window: QMainWindow) -> None:
    """清单标签：非空时顺带写明"怎么改"——勾上以后再改起止框不会自动生效
    （清单才是权威），这句话省掉一次"怎么没反应"。
    """
    cuts = window.cut_list
    full = (f"清单：{_cut_text(cuts)}（改起止后点 [添加]）" if cuts
            else "清单：空")
    window.cut_list_lbl.setText(full)   # _ElideLabel：窄了省略号、悬停看全
    # 悬停放全文之外再带上"哪里生效"的说明（_ElideLabel.setText 会把
    # tooltip 刷成正文，这句得在它之后写）
    window.cut_list_lbl.setToolTip(
        (full + "\n" if cuts else "")
        + "当前要挖掉的全部区间（图上、处理产物与导出文件里同样生效）")

