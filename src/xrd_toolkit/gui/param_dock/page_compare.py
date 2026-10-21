"""参数坞「对比」页：对比显示 / 热图显示两节 + 对比与热图产出按钮。

2026-10-22 从 builder.py 的 _build_param_dock 原样搬出（只改结构，不动逻辑）。
"""
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QPushButton, QWidget)

from xrd_toolkit.gui.panel_state import (
    DATA_PARAM_DEFAULTS, _apply_auto_heatlim, _family_label,
    _GRADIENT_FAMILY_ORDER)
from xrd_toolkit.gui.param_dock.widgets import add_caption, add_range
from xrd_toolkit.gui.plot_compare import (
    _plot_compare, _plot_heatmap, _refresh_compare, _refresh_compare_family,
    _refresh_heat, _refresh_heat_view, _refresh_heatmap_view)
from xrd_toolkit.gui.plot_views import _refresh_heatmap_names


def build_compare_page(window, form_cmp):
    # 对比页自己的小节标题（2026-10-09 用户："对比和热图的参数区没有
    # 很明显区分"——此前只有下半页的「热图显示」有标题，对比这半页光秃
    # 秃地接在上面，两套参数看起来是一坨）。和 原图 页的 2D/剖面/瀑布、
    # 处理页的 背景扣除/平滑 同一套灰字标题。
    add_caption(form_cmp, "对比显示")

    # 对比归一化（下拉框三选一）——叠图时强度差很大的文件不归一会被
    # 强者压扁。三种模式（**都是全场统一的比例**）：
    #   global 全图最强峰：所有曲线除以全部曲线里最高的峰
    #   file   指定数据：所有曲线除以旁边下拉框选的文件的最强峰
    #   off    不归一化（默认：原样画原始强度）
    # "各自最强峰"（每条除以自己的峰）已删：它把每条曲线都缩到同一
    # 高度，样品之间的强弱差就看不出来了（用户 2026-09-26 定的规矩——
    # "不要按照各自的最高峰归一化，所有的图"）。
    # 归一化只动显示层，原始结果原样保留在 compare_data。
    cmp_norm = QComboBox()
    for text, data in (("全图最强峰", "global"),
                       ("指定数据…", "file"), ("不归一化", "off")):
        cmp_norm.addItem(text, data)
    cmp_norm.setCurrentIndex(cmp_norm.findData("off"))   # 默认 = 不归一化
    cmp_norm.setToolTip("叠图归一化（都用全场统一的比例）："
                        "全图最强峰 / 指定数据的最强峰 / 不归一化")
    window.params["对比归一化"] = cmp_norm
    norm_target = QComboBox()
    norm_target.setToolTip("以哪个文件的最强峰归一化（列表就是对比面板的文件）")
    window.params["归一化目标"] = norm_target
    norm_row = QWidget()
    norm_lay = QHBoxLayout(norm_row)
    norm_lay.setContentsMargins(0, 0, 0, 0)
    norm_lay.setSpacing(2)
    norm_lay.addWidget(cmp_norm, 1)     # 同一行：模式在左、目标文件在右
    norm_lay.addWidget(norm_target, 1)
    form_cmp.addRow(norm_row)

    def sync_norm_target(*_):
        norm_target.setEnabled(cmp_norm.currentData() == "file")

    cmp_norm.currentIndexChanged.connect(sync_norm_target)
    sync_norm_target()   # 初始 = 不归一化 → 目标下拉框置灰

    # 渐变色系（显示参数）：对比曲线的单色渐变（2026-10-13 用户："对比图
    # 的颜色改为一种颜色的渐变过程……只留渐变顺序，颜色这种选项"——原先
    # 的分类色板下拉与"逐条自定义色"一起退休）。单色系按文件名尾号从深到
    # 浅，第 1 条 = 基色；1D/剖面单曲线用基色。「彩虹」（2026-10-14 加）
    # 不属单色系——瀑布同款 viridis 顺序取样（平铺更好认，见 panel_state）。
    # 「经典色」（2026-10-15 加）是 Okabe–Ito 8 色固定串色（色盲友好），
    # 超过 8 条每轮压暗一档。逐图的色系也可在「外观」里选（同一参数，
    # 两处改的是同一件事）。
    curve_palette = QComboBox()
    for name in _GRADIENT_FAMILY_ORDER:
        label = _family_label(name) + ("（推荐）" if name == "蓝" else "")
        curve_palette.addItem(label, name)
    curve_palette.setToolTip("对比曲线配色：单色系按文件名尾号从深到浅"
                             "（第 1 条最深、最后一条最浅）；彩虹是瀑布同款"
                             "顺序色（深紫→黄），不循环；经典色是期刊常用的"
                             " Okabe–Ito 八色（色盲友好），超过八条每轮压暗"
                             "一档。逐图的色系在面板标题栏的 [外观…] 里换")
    window.params["渐变色系"] = curve_palette
    form_cmp.addRow(curve_palette)

    cmp_stack = QCheckBox("堆叠显示")
    # 提示写的是**现行**规矩：所有行同一个行高（= 第二高的行峰 × 0.7，
    # 见 services/stacking），不按各自的峰（用户 2026-09-26 定）。旧文案
    # 还写着"按自身峰高抬到自己的行上"——那是被否掉的那条，实现早改了，
    # 2026-10-02 顺出来的
    cmp_stack.setToolTip("瀑布式错开叠放：所有行同一个行高（按多数行的峰定，"
                         "少数特别强的行会探进上一行），y 刻度为样品名；"
                         "堆叠下纵轴范围/对数不适用。勾上立刻重画，"
                         "不用手动应用")
    window.params["对比堆叠"] = cmp_stack
    form_cmp.addRow(cmp_stack)

    # 行距（2026-10-05 用户："对比的堆叠添加让用户自己改 y off"）：默认
    # 自动 = 第二高的行峰 × 0.7（services/stacking.row_step，少数特别强
    # 的行会探进上一行）；勾上 [手动行距] 后按倍数微调。纯显示参数，
    # 改了立刻重画（与上面那组同一个来由）。
    step_chk = QCheckBox("手动行距")
    step_chk.setToolTip("默认自动：行距 = 第二高的行峰 × 0.7；勾上后按倍数"
                        "手动定（只影响显示，不动数据）")
    window.params["手动行距"] = step_chk
    step_mult = QDoubleSpinBox()
    step_mult.setRange(0.1, 10.0)
    step_mult.setSingleStep(0.1)
    step_mult.setDecimals(2)
    step_mult.setValue(1.0)
    step_mult.setEnabled(False)
    step_mult.setToolTip("行距 = 自动行距 × 这个倍数（1.00 = 与自动相同）")
    window.params["行距倍数"] = step_mult
    step_row = QWidget()
    step_lay = QHBoxLayout(step_row)
    step_lay.setContentsMargins(0, 0, 0, 0)
    step_lay.setSpacing(2)
    step_lay.addWidget(step_chk, 1)
    step_lay.addWidget(step_mult, 1)
    form_cmp.addRow(step_row)

    def sync_step_mult(*_):
        step_mult.setEnabled(step_chk.isChecked())

    step_chk.toggled.connect(sync_step_mult)
    sync_step_mult()   # 初始：自动行距 → 倍数框置灰
    step_chk.toggled.connect(lambda _on: _refresh_compare(window))
    step_mult.valueChanged.connect(lambda _v: _refresh_compare(window))

    # 「显示数据名」（2026-10-08 用户："做按钮，默认不显示，点击开启后按
    # 条件显示"）：默认关——名字多了糊成一条黑带（原来只有对比有这毛病；
    # 81 条堆叠实测全名占图宽 29%）；勾上按"放得下"的条件显示。
    # 认线兜底 = 悬停读数（全名）。即改即画，同上面那组。
    # 2026-10-12 用户拍板"甲，拆开"（原话："对比热图现在共用一个显示数据
    # 名，但是是在对比的板块"）：热图行名拆给「热图显示」小节自己的同名
    # 勾选（键 =「热图显示数据名」），这把从此只管对比的两处（平铺图例 /
    # 堆叠纵轴名）；瀑布的 χ 刻度同日也拍板"甲"——那是**纵轴的刻度**不是
    # "名字"，退出开关、默认就画（见 plot_views._draw_waterfall）。
    name_chk = QCheckBox("显示数据名")
    name_chk.setToolTip("默认不显示（认线看悬停读数）。勾上按放得下的"
                        "方式显示：平铺画短名图例（12 条以内）、堆叠画"
                        "短名+抽稀的纵轴样品名。热图行名在「热图显示」"
                        "小节有自己的同名勾选，两把互不影响。勾上立刻重画")
    window.params["显示数据名"] = name_chk
    form_cmp.addRow(name_chk)
    name_chk.toggled.connect(lambda _on: _refresh_compare(window))

    # 「显示色标」（2026-10-15 晚用户："把这个改为显示图例的选项，默认
    # 不显示"——上午的"自动（超过 12 条才画）"三选退休，简化成朴素开关）：
    # 对比图那条颜色刻度（颜色 ↔ 数据顺序的读数，默认在右下角，见
    # plot_compare._draw_compare_color_scale），勾上才画。叫"色标"不叫
    # "图例"是为了跟 [显示数据名] 的图例（数据名）区分开。
    # 那条说明文字与放图的哪个角在面板 [外观…] 里可改（用户："自行更改
    # 描述方式"）。
    scale_chk = QCheckBox("显示色标")
    scale_chk.setToolTip("对比图那条颜色刻度（读颜色跟数据的对应：左端 = "
                         "颜色最深的、右端 = 最浅的，颜色按文件名尾号排）。"
                         "默认不显示；勾上两条也画。放图的哪个角与下方那行"
                         "说明文字在面板标题栏的 [外观…] 里改。勾上立刻重画")
    window.params["色标"] = scale_chk
    form_cmp.addRow(scale_chk)
    scale_chk.toggled.connect(lambda _on: _refresh_compare(window))
    # 这几个是**显示参数**：改了立刻重画对比面板，不需要 [应用]（用户
    # 2026-10-02 第 1 条"堆叠显示无效"的根因——它们住在「对比」页，而
    # [应用显示设置] 只在「原图」页，够不着）。照热图那组的老做法
    # （_refresh_heat）：显示参数不触发重算，"改了就该看见"。
    for _w in (cmp_norm, norm_target):
        _w.currentIndexChanged.connect(lambda _i: _refresh_compare(window))
    # 渐变色系走专用的写回（2026-10-14）：它不能跟着"控件值优先"的重建
    # 走，否则「外观」里选好的色系会被下拉旧值打回默认（用户："对比图
    # 颜色在改参数后会恢复默认"）——见 _refresh_compare_family 的说明
    curve_palette.currentIndexChanged.connect(
        lambda _i: _refresh_compare_family(window))
    cmp_stack.toggled.connect(lambda _on: _refresh_compare(window))

    # 视图 2θ（**对比那对**，老键）：对比横轴只看这一段——图上的缩放/
    # 平移实时写回这两个框，框里填数 → 立刻裁剪重画（_refresh_heat_view）。
    # 2026-10-08 晚与热图拆开（用户："对比与热图分开管理"）：热图有自己
    # 的一对（在「热图显示」小节，键名"热图视图 2θ …"），改一边不再动
    # 两边。1D 面板也共用这一对键显示它的缩放窗口（老行为，不动）。
    cv_lo, cv_hi = add_range(
        window, form_cmp, "视图 2θ 下限 (°)", "视图 2θ 上限 (°)", 0.0, 90.0,
        DATA_PARAM_DEFAULTS["2θ 下限 (°)"], DATA_PARAM_DEFAULTS["2θ 上限 (°)"],
        label="视图 2θ", suffix="°", decimals=1,
        tooltip="对比横轴只看这一段 2θ：填完立刻裁剪重画（重看，不重算）；"
                "图上的缩放/平移会实时写回这两个框")
    cv_lo.valueChanged.connect(lambda _v: _refresh_heat_view(window))
    cv_hi.valueChanged.connect(lambda _v: _refresh_heat_view(window))


def build_heatmap_section(window, form_cmp):
    # ── 热图显示（小节）：批量热图的显示参数 ──
    # 颜色映射 / 强度归一化 / 对数强度 / 强度范围。归一化与对比
    # 同款语义（热图没有"指定文件"模式）；对数强度 = 弱峰抬起来
    # （XRD 行规）；强度范围与对比度同套路：自动 = 按显示矩阵
    # 1%/99.9% 分位（含归一化后的口径，见 _heat_shown）
    add_caption(form_cmp, "热图显示")

    heat_cmap = QComboBox()
    for text, data in (("magma", "magma"), ("viridis", "viridis"),
                       ("plasma", "plasma"), ("inferno", "inferno"),
                       ("gray", "gray")):
        heat_cmap.addItem(text, data)
    heat_cmap.setToolTip("热图颜色映射（颜色表示强度）；改完立刻重画")
    window.params["热图色图"] = heat_cmap
    heat_cmap.currentIndexChanged.connect(lambda _i: _refresh_heat(window))
    form_cmp.addRow(heat_cmap)

    heat_norm = QComboBox()
    # "每行各自最强峰"已删（同对比面板：用户 2026-09-26 的规矩）
    for text, data in (("全图最强峰", "global"), ("不归一化", "off")):
        heat_norm.addItem(text, data)
    heat_norm.setCurrentIndex(heat_norm.findData("off"))
    heat_norm.setToolTip("热图归一化：全图最强峰（整批同一个比例）"
                         "/ 不归一化（原样画原始强度）")
    window.params["热图归一化"] = heat_norm
    heat_norm.currentIndexChanged.connect(lambda _i: _refresh_heat(window))
    form_cmp.addRow(heat_norm)

    heat_log = QCheckBox("对数强度")
    heat_log.setToolTip("颜色按对数强度：弱峰抬起来（XRD 行规）")
    window.params["热图对数"] = heat_log
    heat_log.toggled.connect(lambda _on: _refresh_heat(window))
    form_cmp.addRow(heat_log)

    auto_heat = QCheckBox("热图自动范围")
    auto_heat.setChecked(True)
    auto_heat.setToolTip("按显示矩阵 1%–99.9% 分位自动确定强度范围")
    window.params["热图自动范围"] = auto_heat
    form_cmp.addRow(auto_heat)

    add_range(window, form_cmp, "热图下限", "热图上限", 0.0, 1e9, 1.0, 100000.0,
              label="热图范围", decimals=1,
              tooltip="取消自动后手填的强度范围（下限–上限）")

    def sync_heatlim(checked):
        window.params["热图下限"].setEnabled(not checked)
        window.params["热图上限"].setEnabled(not checked)
        if checked:
            _apply_auto_heatlim(window)   # 勾回自动：立刻按焦点热图算并填回
        _refresh_heat(window)   # 开关本身也是改动（即改即画，2026-10-08）

    auto_heat.toggled.connect(sync_heatlim)
    # 热图范围两个手填框：2026-10-08 起即改即画（_refresh_heat）——它们
    # 原先在「未应用」名单里、而 [应用显示设置] 住在「原图」页，改完根本
    # 够不着（这个应用反复在修的老病；程序回填走 _param_box_set 旗标）
    for _box in (window.params["热图下限"], window.params["热图上限"]):
        _box.valueChanged.connect(lambda _v=0.0: _refresh_heat(window))
    sync_heatlim(True)   # 初始状态：自动开 → 输入框置灰

    # 「显示数据名」（**热图自己的那把**，2026-10-12 用户："对比热图现在
    # 共用一个显示数据名，但是是在对比的板块" → 拍板"甲，拆开"）：热图行名
    # 从「对比显示」小节那把里分出来，开关就住在本小节——看热图参数不用再
    # 跑回上半页。键独立成「热图显示数据名」（同 2θ 那对的拆法），默认关、
    # 勾上立刻重画（_refresh_heatmap_names，同上面这组显示参数）。
    heat_name_chk = QCheckBox("显示数据名")
    heat_name_chk.setToolTip("默认不显示（认行看悬停读数）。勾上按放得下"
                             "的方式显示热图行名（行数多时按面板高度抽稀）；"
                             "与「对比显示」小节的同名勾选相互独立——那里"
                             "管平铺图例/堆叠纵轴名，这里只管热图行名。"
                             "勾上立刻重画")
    window.params["热图显示数据名"] = heat_name_chk
    form_cmp.addRow(heat_name_chk)
    heat_name_chk.toggled.connect(
        lambda _on: _refresh_heatmap_names(window))

    # 视图 2θ：**热图自己的**一对（2026-10-08 晚用户："对比与热图分开
    # 管理"——此前两图共用一对老键，改一边动两边）。用户 2026-10-05：
    # "热图添加回 2theta 改变视图的功能，就是重看，不是重算"：图上缩放/
    # 平移实时写回这两个框，反过来在框里填数 → 立刻裁剪重画
    # （_refresh_heatmap_view）——换的只是窗口，数据一份不动。超出某文件
    # 自己算过的范围的那一段就是空白：画不出来也不重算，正是"重看"的
    # 含义。初始值 = 积分范围出厂默认，焦点是热图面板时一回放就被它的
    # 窗口（或"跟随"的折算值）覆盖。对比那对在「对比」小节里（老键）。
    wv_lo, wv_hi = add_range(
        window, form_cmp, "热图视图 2θ 下限 (°)", "热图视图 2θ 上限 (°)", 0.0, 90.0,
        DATA_PARAM_DEFAULTS["2θ 下限 (°)"], DATA_PARAM_DEFAULTS["2θ 上限 (°)"],
        label="视图 2θ", suffix="°", decimals=1,
        tooltip="热图横轴只看这一段 2θ：填完立刻裁剪重画（重看，不重算）；"
                "图上的缩放/平移会实时写回这两个框。数据按各文件自己的积分"
                "范围算——超出已算范围的那一段是空白")
    wv_lo.valueChanged.connect(lambda _v: _refresh_heatmap_view(window))
    wv_hi.valueChanged.connect(lambda _v: _refresh_heatmap_view(window))


def build_compare_buttons(window, btns_cmp):
    # 对比页产出：多文件才成立的两种图（对比叠图 / 批量热图）
    cmp_row = QHBoxLayout()
    cmp_row.setSpacing(4)
    btn_plot_cmp = QPushButton("出对比")
    btn_plot_cmp.setObjectName("plot_cmp_btn")
    # 栅栏（2026-10-16）：对比只叠已经算好的曲线，没出过 1D 的原始数据
    # 会被拦下并写日志——提示里先讲清，免得点了个寂寞
    btn_plot_cmp.setToolTip("把勾选文件的曲线叠成一张图；只叠已经算好的"
                            "曲线——还没出过 1D 的原始数据先点 [1D] 出图")
    window.plot_cmp_btn = btn_plot_cmp
    btn_plot_cmp.clicked.connect(lambda: _plot_compare(window))
    btn_plot_heat = QPushButton("出热图")
    btn_plot_heat.setObjectName("plot_heat_btn")
    btn_plot_heat.setToolTip("把勾选文件拼成一张 2θ × 样品的强度图；只拼"
                             "已经算好的曲线——还没出过 1D 的原始数据先"
                             "点 [1D] 出图")
    window.plot_heat_btn = btn_plot_heat
    # 兼容别名（2026-10-07 类型行拆分）：compare_btn / heat_btn 从此指向
    # 本页这两颗产出按钮（旧名沿自「原图」页类型行，测试与探针按它们找）
    window.compare_btn = btn_plot_cmp
    window.heat_btn = btn_plot_heat
    btn_plot_heat.clicked.connect(lambda: _plot_heatmap(window))
    cmp_row.addWidget(btn_plot_cmp, 1)
    cmp_row.addWidget(btn_plot_heat, 1)
    btns_cmp.addLayout(cmp_row)
