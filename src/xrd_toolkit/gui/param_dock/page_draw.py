"""参数坞「原图」页：2D 视图 / 剖面 / 瀑布三节参数 + 显示参数复位。

2026-10-22 从 builder.py 的 _build_param_dock 原样搬出（只改结构，不动逻辑）。
build_draw_page 返回 (页顶提示行, 自动对比度勾选)，供 install_draw_reset 挂
[恢复默认] 并复用它。
"""
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QHBoxLayout, QPushButton, QWidget)

from xrd_toolkit.gui.panel_state import (
    _apply_auto_contrast, _apply_auto_heatlim, _apply_auto_ylim, _log,
    _param_box_set)
from xrd_toolkit.gui.param_dock.helpers import _sync_bg_rows
from xrd_toolkit.gui.param_dock.widgets import (
    add_caption, add_float, add_range)
from xrd_toolkit.gui.plot_views import (
    _refresh_image_display, _refresh_profile_angle, _refresh_waterfalls)


def build_draw_page(window, form_draw):
    # ── 原图页的参数按"类型行里的三个功能"分节（2026-10-07 用户：
    # "绘图页的参数分别对应功能，现在太乱了"）：2D 视图 / 剖面 / 瀑布，
    # 各节和自己的类型按钮一一对应（原来把 2D 与剖面的参数混在一节里）。
    # 这一页的参数是**看图参数**：不参与计算；2026-10-08 起重清扫后
    # **全部即改即画**（2D 三项 + 剖面角度 + 热图范围接上实时，
    # [应用显示设置] 与「未应用（显示设置）」灰字一并删了）。
    # 页顶那条"本页参数只影响显示…"的灰字同日删除（用户："显示不全……
    # 有没有必要保留"）：即改即画全站一致、图上一改就动，它是重复信息，
    # 窄坞下还会断行挤在 [恢复默认] 旁边看着像不全。空行布局留给
    # [恢复默认]——它在下面构建时挂进这一行，位置仍在页顶。
    hint_row = QHBoxLayout()
    form_draw.addRow(hint_row)   # 全宽一行

    add_caption(form_draw, "2D 视图")

    # 自动对比度（默认开）：显示区间按编辑对象（焦点图）数据的
    # 1%/99.9% 分位自定，与 view_diffraction 的默认行为一致；取消勾
    # 选后手填两个输入框（对应 --vmin/--vmax 的"指定时覆盖自动值"
    # 语义）。自动模式下输入框置灰 = 只读展示程序正在用的区间；
    # 勾回自动 = 立刻按焦点图重算并填回（恢复默认对比度）。
    # 2026-10-08 起**即改即画**（_refresh_image_display）：改任一项立刻
    # 重画编辑对象；程序回填（自动算值进框）走 _param_box_set 旗标，
    # 不会被即改即画钩子当成用户输入。
    auto = QCheckBox("自动对比度")
    auto.setChecked(True)
    auto.setToolTip("显示区间按图像 1%–99.9% 分位自动确定")
    window.params["自动对比度"] = auto
    form_draw.addRow(auto)

    add_range(window, form_draw, "对比度下限", "对比度上限", 0.0, 1e9, 1.0, 100000.0,
              label="显示范围", decimals=1,
              tooltip="取消自动对比度后手填的显示区间（下限–上限）")

    def sync_contrast(checked, silent=False):
        window.params["对比度下限"].setEnabled(not checked)
        window.params["对比度上限"].setEnabled(not checked)
        if checked:
            _apply_auto_contrast(window, silent=silent)
        _refresh_image_display(window)   # 开关本身也是改动（即改即画）

    auto.toggled.connect(sync_contrast)
    sync_contrast(True, silent=True)   # 初始状态：自动开 → 输入框置灰（不刷日志）

    for _box in (window.params["对比度下限"], window.params["对比度上限"]):
        _box.valueChanged.connect(
            lambda _v=0.0: _refresh_image_display(window))

    # 束心十字（2D 图上那个白色 +）画不画：**默认不画**——用户 2026-10-09：
    # "实现默认 2D 图是不显示束心的"（默认先看干净的衍射图，要核对束心
    # 位置再勾上）。即改即画（同对比度那套）；校准图不受影响——那张图上
    # 没有这个标记（用户 2026-10-01："绘图的 2d……校准不要加"）
    beam_cross = QCheckBox("显示束心")
    beam_cross.setChecked(False)
    beam_cross.setToolTip("在 2D 图上画出当前几何配置的束心十字（+）；"
                          "默认不画，勾上才叠这个标记（校准图不参与）")
    window.params["显示束心"] = beam_cross
    beam_cross.toggled.connect(lambda _on: _refresh_image_display(window))
    form_draw.addRow(beam_cross)

    # ── 剖面（小节）：剖面面板自己的参数 ——
    add_caption(form_draw, "剖面")
    angle = add_float(window, form_draw, "剖面角度 (°)", -180.0, 180.0, 0.0,
                      decimals=1, label="剖面角度", suffix="°",
                      tooltip="剖面线相对参考方向的角度（改了即刻按新角度重算）")
    angle.setSingleStep(5.0)   # 步进 5°，对应 view_diffraction 的 --angle
    # 即改即画里的"要重算"那一档（2026-10-08）：焦点是剖面面板就地重算，
    # 否则只写快照（见 _refresh_profile_angle）
    angle.valueChanged.connect(lambda _v=0.0: _refresh_profile_angle(window))

    # ── 瀑布显示（小节）── 瀑布 = 36 个扇区的堆叠曲线，行距口径与
    # 对比堆叠同源（services/stacking：第二高的行峰 × 0.7）。控件单开
    # 一对（键带「瀑布」前缀）：对比那对住「对比」页、这里住「原图」页
    # ——瀑布的面板就是从本页开出去的，用户在哪儿出图就在哪儿调。
    # 纯显示参数、即改即画（同对比那对），不勾 = 自动口径。
    add_caption(form_draw, "瀑布")

    wf_step_chk = QCheckBox("手动行距")
    wf_step_chk.setToolTip("默认自动：行距 = 第二高的行峰 × 0.7（各扇区同一个"
                           "行高，强弱能横向比）；勾上后按倍数手动定（只影响"
                           "显示，不动数据）。对比的 [手动行距] 用同一条规则，"
                           "两边各管各的图")
    window.params["瀑布手动行距"] = wf_step_chk
    wf_step_mult = QDoubleSpinBox()
    wf_step_mult.setRange(0.1, 10.0)
    wf_step_mult.setSingleStep(0.1)
    wf_step_mult.setDecimals(2)
    wf_step_mult.setValue(1.0)
    wf_step_mult.setEnabled(False)
    wf_step_mult.setToolTip("行距 = 自动行距 × 这个倍数（1.00 = 与自动相同）")
    window.params["瀑布行距倍数"] = wf_step_mult
    wf_row = QWidget()
    wf_lay = QHBoxLayout(wf_row)
    wf_lay.setContentsMargins(0, 0, 0, 0)
    wf_lay.setSpacing(2)
    wf_lay.addWidget(wf_step_chk, 1)
    wf_lay.addWidget(wf_step_mult, 1)
    form_draw.addRow(wf_row)

    def sync_wf_step_mult(*_):
        wf_step_mult.setEnabled(wf_step_chk.isChecked())

    wf_step_chk.toggled.connect(sync_wf_step_mult)
    sync_wf_step_mult()   # 初始：自动行距 → 倍数框置灰
    wf_step_chk.toggled.connect(lambda _on: _refresh_waterfalls(window))
    wf_step_mult.valueChanged.connect(lambda _v: _refresh_waterfalls(window))
    return hint_row, auto


def install_draw_reset(window, hint_row, auto):
    img_defaults = {
        "对比度下限": 1.0,
        "对比度上限": 100000.0,
        "剖面角度 (°)": 0.0,
        "对数纵轴": False,
        "纵轴自动": True,
        "对比归一化": "off",
        "归一化目标": "",
        "渐变色系": "蓝",
        "对比堆叠": False,
        "显示数据名": False,
        "热图显示数据名": False,
        "手动行距": False,
        "行距倍数": 1.0,
        "瀑布手动行距": False,
        "瀑布行距倍数": 1.0,
        "热图色图": "magma",
        "热图归一化": "off",
        "热图对数": False,
        "热图自动范围": True,
        "热图下限": 1.0,
        "热图上限": 100000.0,
        "背景扣除模式": "off",
        "背景窗口 (°)": 0.2,
        "空扫归一化": 1.0,
        "锚点拟合方式": "pchip",
        "背景显示原始": True,
        "负值截断为 0": False,
    }
    btn_reset_img = QPushButton("恢复默认")
    btn_reset_img.setObjectName("reset_image_btn")
    btn_reset_img.setToolTip(
        "把显示参数恢复出厂默认——2D 视图 / 剖面 / 瀑布，以及住在别页的"
        "「1D 显示」、对比、热图、背景扣除显示项都在复位范围内（这按钮"
        "一直是全量的）；数据参数（2θ / 点数）不动。复位后立刻按新值重画。")

    def reset_image():
        if not auto.isChecked():
            auto.setChecked(True)   # 触发 sync_contrast → 按焦点图重算自动值
        else:
            _apply_auto_contrast(window)   # 已勾着 toggled 不响，手动重算
        window.params["剖面角度 (°)"].setValue(img_defaults["剖面角度 (°)"])
        window.params["对数纵轴"].setChecked(img_defaults["对数纵轴"])
        window.params["纵轴自动"].setChecked(img_defaults["纵轴自动"])
        window.params["对比归一化"].setCurrentIndex(
            window.params["对比归一化"].findData(img_defaults["对比归一化"]))
        window.params["归一化目标"].setCurrentIndex(0)
        window.params["渐变色系"].setCurrentIndex(
            window.params["渐变色系"].findData(img_defaults["渐变色系"]))
        window.params["对比堆叠"].setChecked(img_defaults["对比堆叠"])
        window.params["显示数据名"].setChecked(img_defaults["显示数据名"])
        window.params["热图显示数据名"].setChecked(
            img_defaults["热图显示数据名"])
        window.params["手动行距"].setChecked(img_defaults["手动行距"])
        window.params["行距倍数"].setValue(img_defaults["行距倍数"])
        window.params["瀑布手动行距"].setChecked(img_defaults["瀑布手动行距"])
        window.params["瀑布行距倍数"].setValue(img_defaults["瀑布行距倍数"])
        if window.params["纵轴自动"].isChecked():
            _apply_auto_ylim(window)   # 已勾着 toggled 不响，手动重算填回
        window.params["热图色图"].setCurrentIndex(
            window.params["热图色图"].findData(img_defaults["热图色图"]))
        window.params["热图归一化"].setCurrentIndex(
            window.params["热图归一化"].findData(img_defaults["热图归一化"]))
        window.params["热图对数"].setChecked(img_defaults["热图对数"])
        window.params["热图自动范围"].setChecked(img_defaults["热图自动范围"])
        if window.params["热图自动范围"].isChecked():
            _apply_auto_heatlim(window)   # 已勾着 toggled 不响，手动重算填回
        # 背景扣除：模式回"关闭"即回到不扣（锚点与空扫图属于"用户挑的
        # 数据"不是参数，留着手动清理——[清空锚点] / 重选空扫图）
        window.params["背景扣除模式"].setCurrentIndex(
            window.params["背景扣除模式"].findData(
                img_defaults["背景扣除模式"]))
        window.params["背景窗口 (°)"].setValue(img_defaults["背景窗口 (°)"])
        window.params["空扫归一化"].setValue(img_defaults["空扫归一化"])
        window.params["锚点拟合方式"].setCurrentIndex(
            window.params["锚点拟合方式"].findData(
                img_defaults["锚点拟合方式"]))
        window.params["背景显示原始"].setChecked(img_defaults["背景显示原始"])
        window.params["负值截断为 0"].setChecked(img_defaults["负值截断为 0"])
        _sync_bg_rows(window)
        # 视图 2θ 范围回到"跟随积分范围"：从焦点面板快照里删掉显式视图值
        # （None = 跟随）。那两个键的输入框已撤（用户 2026-09-27："显示范围
        # 用户自己放大就行了"）→ 控件那一份用 _param_box_set 刷，没有就跳过
        # （踩过：直接下标会 KeyError，而 Qt 槽里的异常只打 stderr、这一行
        # 之后的复位会被静默跳过）
        dock = window.plot_docks.get(window.focus_panel)
        if dock is not None and getattr(dock, "params_snapshot", None):
            for name in ("视图 2θ 下限 (°)", "视图 2θ 上限 (°)",
                         "热图视图 2θ 下限 (°)", "热图视图 2θ 上限 (°)"):
                dock.params_snapshot.pop(name, None)
        for name, base in (("视图 2θ 下限 (°)", "2θ 下限 (°)"),
                           ("视图 2θ 上限 (°)", "2θ 上限 (°)"),
                           ("热图视图 2θ 下限 (°)", "2θ 下限 (°)"),
                           ("热图视图 2θ 上限 (°)", "2θ 上限 (°)")):
            _param_box_set(window, name, window.params[base].value())
        # 复位完成后按最终状态把编辑对象收一笔重画（2026-10-08）：自动
        # 开关/回填都挂旗标不触发即改即画，而视图窗口刚刚才被弹回"跟随"
        # ——不在这里重画，画面会停在弹之前的旧窗口
        _refresh_image_display(window)
        _log(window, "图像参数已恢复默认")

    btn_reset_img.clicked.connect(reset_image)

    # [恢复默认] 挂到**页顶提示行**旁（2026-10-08 用户："挪走"；原先是
    # 页底动作栏的孤零一位）——它复位的是**全部显示参数**（2D / 剖面 /
    # 瀑布 + 住在别页的「1D 显示」/ 对比 / 热图 / 背景扣除显示项），是
    # 整页级别的动作，放进「2D 视图」节会让人以为只复位那一节；提示行
    # 讲的正是"本页参数全部即时生效"，两者同类。btns_draw（页底动作栏）
    # 本页从此为空。复位动作本身即改即画：值一改，钩子就把编辑对象重画。
    # 本页的按钮删过五轮，别再长回来：[导出图片…] 与 [清空缓存]（用户
    # 2026-10-01："都删了，保存图片在图片自身的工具栏有"——存图走面板
    # [Save]、关窗询问；清缓存走文件栏右键，那里多一次二次确认）；
    # [只重画，不重算]（2026-09-30）；[出图（勾选 N 个）]（2026-10-08：
    # 与页顶三个类型按钮是**同一次调用的两个入口**——都走
    # _select_plot_type，"再出一次"就是再点一次已高亮的类型按钮）；
    # [应用显示设置]（同一天：显示参数全部即改即画，见
    # plot_views._refresh_image_display / _refresh_profile_angle）。
    hint_row.addWidget(btn_reset_img)
