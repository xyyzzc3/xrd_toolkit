"""参数坞构建器：2026-10-21 从 gui/app.py 原样搬出（只改结构，不动逻辑）。

`_build_param_dock` 一个函数建完参数坞的固定行和五个入口页；本模块只负责
组装，业务仍在 services/ 与各 plot 模块。app.py 再导出，外部照旧经 gui_app 取用。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDockWidget, QDoubleSpinBox, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton, QScrollArea,
    QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from xrd_toolkit.config import CONFIGS, DEFAULT_CONFIG, entry_display
from xrd_toolkit.gui.calib import _build_calib_form
from xrd_toolkit.gui.config_ops import _sync_del_config_btn
from xrd_toolkit.gui.panel_state import (
    DATA_PARAM_DEFAULTS, FOCUS_EMPTY_TEXT, _GRADIENT_FAMILY_ORDER,
    _apply_auto_contrast, _apply_auto_heatlim, _apply_auto_ylim,
    _apply_config, _family_label, _log, _param_box_set)
from xrd_toolkit.gui.panels import _ElideLabel, _FocusElideLabel
from xrd_toolkit.gui.param_dock.helpers import (
    _apply_selected_recipe, _connect_pending_hooks, _delete_selected_recipe,
    _fit_max_width, _on_bg_mode, _on_choose_blank, _on_clear_anchors,
    _on_pick_anchor, _save_recipe_dialog, _sync_bg_rows)
from xrd_toolkit.gui.param_dock.widgets import (
    add_caption, add_float, add_range, bg_group, bg_row, make_page)
from xrd_toolkit.gui.param_dock.pages import (
    _build_check_summary, _build_plot_type_row, _on_cut_add, _on_cut_clear,
    _on_cut_toggled, _sync_cut_label, _sync_data_row, _sync_geom_row,
    _sync_smooth_rows)
from xrd_toolkit.gui.plot_compare import (
    _plot_compare, _plot_heatmap, _refresh_compare, _refresh_compare_family,
    _refresh_heat, _refresh_heat_view, _refresh_heatmap_view)
from xrd_toolkit.gui.plot_views import (
    _apply_params, _plot_view, _proc_save, _refresh_1d_display,
    _refresh_heatmap_names, _refresh_image_display, _refresh_profile_angle,
    _refresh_proc, _refresh_waterfalls)
from xrd_toolkit.services.background import AUTO_WINDOW_DEG


# ══ 右侧：参数面板 ═════════════════════════════════════════

def _build_param_dock(window: QMainWindow) -> QDockWidget:
    """参数坞：顶部两行固定件（"编辑对象"名 / "几何配置"条目）+ 五个
    入口页（校准 │ 原图 / 1D / 处理 / 对比），翻页由工具栏那五个
    入口按钮负责。

    固定件不随页面滚动：
      - 编辑对象：视图 [应用] 作用在它身上；
      - 几何配置：分析/积分/出图一律用这条条目（_collect_geometry 取
        window.config）。一行只放下拉框，像素/波长/距离的读数走悬停
        提示（_sync_geom_row 填）；校准模式下同一行还多一个
        [返回分析模式] —— 本模式的显式出口，钉在这里永远可见。
    各页自己套滚动区，页底按钮（产出按钮 / [恢复默认][应用]）固定
    不随滚动走。
    排版约定（为窄排版）：单位放输入框后缀里（标签不带括号单位）；
    成对上下限并排一行（中间 ~ 连接，转盘限宽到数值能完整显示的
    底限）；小节用全宽灰色小标题；每项悬停有人话提示。坞有尺寸下限
    （拖动边框时不会把控件裁掉）：左右 = 最宽一行完整显示的宽度与
    坞顶两行的宽度取大，上下 = 固定件不被遮没的高度。
    对照 CLI：数据参数来自 integrate/calibrate 脚本，图像参数来自
    view_diffraction 的 --vmin/--vmax/--angle。
    """
    dock = QDockWidget("参数", window)
    dock.setObjectName("param_dock")
    # 同文件坞：可移动/可浮动/可关闭，收起参数列给图让地方
    dock.setFeatures(QDockWidget.DockWidgetMovable
                     | QDockWidget.DockWidgetFloatable
                     | QDockWidget.DockWidgetClosable)

    content = QWidget()
    lay = QVBoxLayout(content)
    lay.setContentsMargins(0, 0, 0, 0)

    # 参数坞 = 两行固定件（编辑对象 / 几何配置）+ **五个入口页**（校准 │
    # 原图 / 1D / 处理 / 对比）。工具栏那五个入口按钮翻页（位置 A =
    # 窗口顶部，见 _build_toolbar / _switch_entrance）。
    # 页 0 = 校准（校准表单，calib.py 建）；其余四页放本阶段的参数，
    # 底部各带"产出"按钮（原图：**没有了**——产出 = 页顶类型按钮，
    # 2026-10-08 删 [出图]；1D：[重算这张图][出图…]；
    # 处理：[存成产物]（2026-10-07 合并了原 [存成产物]+[批量处理]）；
    # 对比：[出对比][出热图]）。
    # **"重画"不再有按钮**（用户 2026-09-30）：改参数本来
    # 就实时重画，视野另有每张图标题栏的 [Home]；保存存的就是当前画面。
    # 控件与键名全部沿用拆分前（window.params 白名单、快照回放、测试
    # 都按这些键找控件），变的只是"住在哪一页"。
    window.param_stack = QStackedWidget()
    window.PARAM_PAGES = {"校准": 0, "1D": 1, "处理": 2, "对比": 3,
                          "原图": 4}

    # 编辑对象：五个入口共用的一行，固定在坞顶（不随页面滚动）。点图
    # 面板（_FocusMarker）或某视图计算完成（_on_integration_done）时
    # 更新；[应用] 作用在它身上。名字可能很长：_ElideLabel 单行缩略，
    # 中间打省略号保留首尾（重名条目的区分后缀在尾部），悬停看全名，
    # 不撑宽参数坞
    window.focus_label = _FocusElideLabel(FOCUS_EMPTY_TEXT,
                                     Qt.ElideMiddle)
    window.focus_label.setStyleSheet("color: gray;")
    lay.addWidget(window.focus_label)     # 固定最上方，不随页面滚动

    # 几何配置：五个入口共用的第二行，同样固定在坞顶（不随页面滚动）。
    # 分析/积分/出图一律用这条条目（_collect_geometry 取 window.config），
    # 所以它得在做分析的每一页都看得见——2026-09-26 之前它住在校准页
    # 顶部，还被挤进一个 88 px 高的内嵌滚动区（视口 88 / 内容 130），
    # 波长和距离都看不全（用户："位置看不清"）。
    # 一行三件：标签 + 下拉框（只放短 key）+ [返回分析模式（校准模式才显示）]；
    # 像素/波长/距离这些只读值改走悬停提示（文案在 _sync_geom_row 里
    # 拼），不再占三行灰色字段。提示同时挂在**整行容器**上：这样悬停
    # "几何配置"这个标签也能看到读数（下拉框自己的提示只有悬停它才出）。
    # 控件登记表：坞顶这几行（几何配置 / 数据参数）里的控件也要登记进来，
    # 所以先建表再建行（下面页面那段不再重复建）
    window.params = {}
    window.geom_row = QWidget()
    geom_lay = QHBoxLayout(window.geom_row)
    geom_lay.setContentsMargins(0, 0, 0, 0)
    geom_lay.setSpacing(4)
    geom_lay.addWidget(QLabel("几何配置"))
    # 下拉框只显示短 key（如 lmfp1_lab6），完整批次备注挂在**条目**的
    # 悬停提示上（下拉列表里逐条看）——**条目文字 2026-10-07 起统一写成
    # `key_备注` 一条**（用户："旧条目也变成一条，key_备注的格式"、
    # "都改，统一"，见 config.entry_display）；key 藏在 itemData 里给
    # 程序用。超长时闭合框里会被裁掉尾巴，下拉列表与悬停里看全名。
    # 完整条目（含 beam_center）挂在 window.config，2D/剖面视图直接取用。
    # 注意顺序：先填条目、设默认，再连接信号——建坞阶段日志区还没建好，
    # 信号此刻触发会去写一个还不存在的控件；默认值改由 create_window
    # 收尾时显式调用 _apply_config 应用。
    window.config_combo = QComboBox()
    for name, entry in CONFIGS.items():
        window.config_combo.addItem(entry_display(name, entry), name)
        window.config_combo.setItemData(
            window.config_combo.count() - 1, entry["label"], Qt.ToolTipRole)
    window.config_combo.setCurrentIndex(
        window.config_combo.findData(DEFAULT_CONFIG))
    window.config_combo.currentIndexChanged.connect(
        lambda i: (_apply_config(window, i),
                   _sync_del_config_btn(window)))
    geom_lay.addWidget(window.config_combo, 1)
    # 校准页的显式出口：固定在坞顶、永远可见。以前它在校准表单的最
    # 底部（窗口 1000 高时按钮落在内容 y=1236，要往下滚 434 px），
    # 用户 2026-09-26 报"没有退出校准的按钮了"。按下的效果与工具栏
    # [校准] 弹起同源（那条 toggled → _on_mode(False)）。
    btn_exit = QPushButton("退出校准")
    btn_exit.setObjectName("exit_calib_btn")
    btn_exit.setToolTip("退出校准工作台，回到分析模式"
                        "（选点与校准结果会一起清零）")
    btn_exit.clicked.connect(lambda: window.calib_btn.setChecked(False))
    btn_exit.setVisible(False)      # 只在校准模式显示（_enter/_exit_calib）
    geom_lay.addWidget(btn_exit)
    window.calib_exit_btn = btn_exit
    lay.addWidget(window.geom_row)        # 固定第二行，不随页面滚动

    # 数据参数（2θ 积分范围 + 输出点数）：**所有分析页共用**，固定在坞顶
    # 第三行。用户 2026-09-27："1d 画图时能选范围，后面处理时没法选范围，
    # 比如对比时，参数里加上"——原先这两项只在 1D 页，切到对比/处理页就
    # 改不了（它们是"参与计算的数据参数"，与几何配置同一类，所以并排住）
    data_row = QWidget()
    dlay = QHBoxLayout(data_row)
    dlay.setContentsMargins(0, 0, 0, 0)
    dlay.setSpacing(2)
    dlay.addWidget(QLabel("2θ"))
    for key in ("2θ 下限 (°)", "2θ 上限 (°)"):
        box = QDoubleSpinBox()
        box.setRange(0.0, 90.0)
        box.setValue(DATA_PARAM_DEFAULTS[key])   # 初值只有一处定义（见 panel_state）
        box.setDecimals(1)
        box.setSuffix("°")
        _fit_max_width(box)          # 上限 = 完整显示 "90.0°" 所需（随平台字体）
        # 显式小下限 = 允许被压窄而不是把整行撑出坞外：Windows 上
        # 转盘的最小尺寸提示更大，不设的话窄坞里最右边的「点数」会被裁
        # （2026-10-04 试用反馈："3000 点显示不全"）
        box.setMinimumWidth(58)
        box.setToolTip("参与积分的 2θ 范围——只在「1D」页显示（别页不读它）；"
                       "改了要重出图才生效")
        window.params[key] = box
    dlay.addWidget(window.params["2θ 下限 (°)"], 1)
    dlay.addWidget(QLabel("–"))
    dlay.addWidget(window.params["2θ 上限 (°)"], 1)
    npt = QSpinBox()
    npt.setRange(100, 100000)
    npt.setValue(DATA_PARAM_DEFAULTS["输出点数"])
    _fit_max_width(npt)              # 上限 = 完整显示 "100000" 所需
    npt.setMinimumWidth(56)
    npt.setToolTip("2θ 范围内的采样点数——只在「1D」页显示；"
                   "改了要重出图才生效")
    window.params["输出点数"] = npt
    dlay.addWidget(QLabel("点数"))
    dlay.addWidget(npt, 1)
    # 「未应用」灰字（用户 2026-09-30 的"丙"）：你改了这两项但还没按
    # [重算这张图] 时亮着——"图上还是按旧范围算的"一眼可见。切图/记住由
    # panel_state 那一套管（_PENDING_GROUPS 的"数据"组）
    lbl_data_pending = QLabel("未应用")
    lbl_data_pending.setStyleSheet("color: gray;")
    lbl_data_pending.setToolTip("改的 2θ 范围 / 点数还没生效（图上仍是按旧"
                                "范围算的）——按「1D」页或「处理」页的 "
                                "[重算这张图]")
    lbl_data_pending.setVisible(False)
    dlay.addWidget(lbl_data_pending)
    lay.addWidget(data_row)               # 固定第三行，不随页面滚动
    window.data_row = data_row

    lay.addWidget(window.param_stack)     # 下面才是五个入口页

    page_1d, form_1d, btns_1d = make_page()
    page_bg, form_bg, btns_bg = make_page()
    page_cmp, form_cmp, btns_cmp = make_page()
    page_draw, form_draw, btns_draw = make_page()

    # 校准页 = 校准表单（calib.py；见其 docstring）。原先压在这一页顶部的
    # "几何配置 + 标定几何（只读）"整块撤掉了：几何配置挪进坞顶公共行、
    # 只读值改悬停（用户 2026-09-26 定）——顺带消掉了那块被压成 88 px 的
    # 内嵌滚动区（"波长/距离看不见"的根因）
    window.param_stack.addWidget(_build_calib_form(window))   # 0 校准
    window.param_stack.addWidget(page_1d)      # 1 1D
    window.param_stack.addWidget(page_bg)      # 2 处理
    window.param_stack.addWidget(page_cmp)     # 3 对比
    window.param_stack.addWidget(page_draw)    # 4 原图
    # 「原图」页最上面：三个类型选择（2D/剖面/瀑布，点一个 = 选中并立即出图）
    page_draw.layout().insertWidget(0, _build_plot_type_row(window))
    # 勾选摘要紧跟在类型行下面：三个类型按钮画的都是
    # 这一句话描述的那批条目，数字摆在动作旁边
    page_draw.layout().insertWidget(1, _build_check_summary(window))

    # 几何配置行跟着页走：校准页上它只是显示（置灰）——校准用的是
    # 「当前配置」那份几何，这里的条目只决定分析侧用哪条，在校准页
    # 能改会让人以为改了就换了校准的几何。悬停文案与置灰状态都由
    # _sync_geom_row 一处写（翻页、_apply_config 换条目两条路都调它）。
    window.param_stack.currentChanged.connect(
        lambda _i: (_sync_geom_row(window), _sync_data_row(window)))
    window._geom_row_sync = lambda: _sync_geom_row(window)
    _sync_geom_row(window)
    _sync_data_row(window)

    # 标定几何那三行只读字段（像素尺寸/波长/分析用距离）撤了：它们的
    # 值改在坞顶"几何配置"那一行的悬停提示里看（用户 2026-09-26 定：
    # "只留能选的那一行，下面的灰色的变成鼠标长放显示"）。控件撤掉后
    # window.params 里不再有这三个键，快照也就不会再记它们——几何随
    # 快照回放一直是走 "config" 那条（见 _snapshot_params/_restore）。

    # 积分设置（2θ 范围 + 输出点数）搬去坞顶第三行：所有分析页共用，
    # 见 _build_param_dock 里 data_row 的说明（用户 2026-09-27）
    # 这一页因此只剩"出图"这件事——留一行灰字指路（用户 2026-09-27 让
    # 我按自己的想法收尾）：整页空白看着像没做完
    page_hint = QLabel(
        "2θ 范围与点数在参数面板顶部，改了按 [重算这张图]。勾选超过 25 项"
        "只计算、不弹面板——结果都在文件栏「1D 产物」：双击看单张、右键"
        "整组打开")
    page_hint.setWordWrap(True)
    page_hint.setStyleSheet("color: gray;")
    form_1d.addRow(page_hint)

    # ── 1D 显示（小节，2026-10-07 从「原图」页搬回本页）──
    # 它本来就是曲线图自己的显示参数，原图页改名后屋里只剩 2D/剖面/瀑布，
    # 这一节留在那儿名不对题（用户："搬回 1d"）。**同时改成即改即画**：
    # 原来要按 [应用显示设置]，而那个按钮住在「原图」页——搬过来就够不着
    # 了（这个应用反复在修的"够不着"病；对照 10-02 对比四参数、10-07 瀑布
    # 行距的同款处理）。作用域 = **编辑对象这一个面板**：显示参数每张图各
    # 记各的（10-02 起的规矩），不学 _refresh_compare 的"所有同类面板"——
    # 1D 面板可以同时开很多张，每张各有各的缩放/刻度。
    add_caption(form_1d, "1D 显示")
    log_y = QCheckBox("对数纵轴")
    log_y.setToolTip("对数刻度：强弱峰差几个数量级时弱峰也看得清（改完立刻重画）")
    window.params["对数纵轴"] = log_y
    form_1d.addRow(log_y)

    auto_y = QCheckBox("纵轴自动")
    auto_y.setChecked(True)
    auto_y.setToolTip("按曲线 1%–99.9% 分位自动确定纵轴区间")
    window.params["纵轴自动"] = auto_y
    form_1d.addRow(auto_y)

    add_range(window, form_1d, "纵轴下限", "纵轴上限", 0.0, 1e9, 1.0, 100000.0,
              label="纵轴范围", decimals=1,
              tooltip="取消自动后手填的纵轴区间（下限–上限）；改完立刻重画")

    def sync_ylim(checked):
        window.params["纵轴下限"].setEnabled(not checked)
        window.params["纵轴上限"].setEnabled(not checked)
        if checked:
            _apply_auto_ylim(window)   # 勾回自动：立刻按焦点图算并填回（同对比度套路）

    auto_y.toggled.connect(sync_ylim)
    sync_ylim(True)   # 初始状态：自动开 → 输入框置灰
    # 即改即画（先接 sync_ylim 再接它：连接按顺序触发，先填值后重画）
    log_y.toggled.connect(lambda _=False: _refresh_1d_display(window))
    auto_y.toggled.connect(lambda _=False: _refresh_1d_display(window))
    window.params["纵轴下限"].valueChanged.connect(
        lambda _v=0.0: _refresh_1d_display(window))
    window.params["纵轴上限"].valueChanged.connect(
        lambda _v=0.0: _refresh_1d_display(window))

    # [恢复默认] + [应用] 并排：[恢复默认] 只把参数复位（几何回到
    # 当前配置条目、区间/点数回到初值），不计算；[应用] 才重算焦点视图
    data_defaults = {
        "2θ 下限 (°)": 1.0,
        "2θ 上限 (°)": 8.0,
        "输出点数": 3000,
    }
    btn_reset_data = QPushButton("恢复默认")
    btn_reset_data.setObjectName("reset_data_btn")
    btn_reset_data.setToolTip("把上面三项数据参数复位成初值（几何回到当前配置"
                              "条目）——<b>只复位，不计算</b>，要算再点右边的"
                              " [重算这张图]")
    # 名字按"对谁、算什么"写（用户 2026-09-27："1d 的应用和出图有点歧义"）：
    # 这个按钮只作用于**编辑对象**那一张（见 _apply_params），与下面那个
    # 对一批勾选文件出图的按钮是两件事
    btn_apply = QPushButton("重算这张图")
    btn_apply.setObjectName("apply_btn")
    btn_apply.setToolTip("按参数面板顶部的 2θ 范围 / 点数重新积分，"
                         "并重画<b>编辑对象</b>那张图（不是一批）")
    btn_apply.clicked.connect(lambda: _apply_params(window))

    def reset_data():
        _apply_config(window, window.config_combo.currentIndex())
        for name, value in data_defaults.items():
            window.params[name].setValue(value)
        _log(window, "数据参数已恢复默认（未计算，点 [重算这张图] 生效）")

    btn_reset_data.clicked.connect(reset_data)

    btn_col = QHBoxLayout()
    btn_col.setSpacing(4)
    # 通栏宽一分为二（与用户讨论定稿）：[恢复默认] 在左、[应用] 在右
    btn_col.addWidget(btn_reset_data, 1)
    btn_col.addWidget(btn_apply, 1)
    btns_1d.addLayout(btn_col)   # 按钮固定在本页最下方（滚动区之外）
    # 本页产出：对勾选文件出 1D 图（常用循环不用切到「原图」页）
    btn_plot_1d = QPushButton("出图（尚未勾选）")
    btn_plot_1d.setObjectName("plot_1d_btn")
    btn_plot_1d.setToolTip("对<b>文件栏里勾选</b>的条目出 1D 图（勾了多少项，"
                           "按钮上就写着多少）：勾原始文件 = 现场积分；"
                           "勾产物条目 = 直接读那一份，不重算")
    window.plot_1d_btn = btn_plot_1d   # 登记按钮（测试用）
    # 兼容别名（2026-10-07 类型行拆分）：view_buttons["1D"] 从此指向这颗
    # 按钮——1D 的出图入口就在「1D」页，其它视图名（对比/热图）同理见下。
    window.view_buttons["1D"] = btn_plot_1d
    btn_plot_1d.clicked.connect(lambda: _plot_view(window, "1D"))
    btns_1d.addWidget(btn_plot_1d)

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

    # ── 背景扣除（小节）：1D/对比/瀑布/热图四条曲线路径共用 ──
    # 三种模式 = 对"背景长什么样"的三个不同假设（物理依据见
    # services/background.py 的模块头注释）：
    #   空扫相减 = 实测：把"没有样品的那个世界"拍一遍逐点减掉
    #   自动基线 = 算法猜：假设背景比峰宽且平滑（滑动窗估计）
    #   手动锚点 = 人判断：用户指出"这几处是纯背景"
    # 锚点列表与空扫曲线不是快照参数（快照只认 QCheckBox/QComboBox/
    # spinbox 三种控件），放窗级属性 window.bg_anchors / window.bg_blank。
    # 处理对象怎么选（用户 2026-10-05："没选中面板就显示没选中——改为
    # 双击文件栏 1D 产出进行处理，让用户看明白"）：页顶常显这行指路，
    # 空态不再是干巴巴一句"没选中"。双击 1D 产物本来就打开面板并把它
    # 设为编辑对象（plot_views._open_product_panel → _set_focus）。
    proc_hint = QLabel("处理的是「编辑对象」那张图：在文件栏双击「1D 产物」"
                       "即可选定；参数改了立刻重画")
    proc_hint.setWordWrap(True)
    proc_hint.setStyleSheet("color: gray;")
    form_bg.addRow(proc_hint)
    # 计算顺序说清楚（用户 2026-10-07）：页面顺序（裁剪在最上）只管操作
    # 顺手，实际计算永远按 背景 → 平滑 → 裁剪 走（services/process.
    # apply_chain 的固定次序）——两件事不说明白，看着顺序和算的顺序反着
    order_hint = QLabel("计算顺序固定：扣背景 → 平滑 → 裁剪"
                        "（与页面上的排列顺序无关）")
    order_hint.setWordWrap(True)
    order_hint.setStyleSheet("color: gray;")
    form_bg.addRow(order_hint)

    # ── 裁剪区间（小节）：把一段 2θ 从曲线里挖掉，其余看得清 ──
    # **页面顺序 = 裁剪 → 扣背景 → 平滑**（用户 2026-10-07："最上方是
    # 裁剪-扣背景-平滑的顺序"）：他就是这么用的——先把压扁其余的巨峰
    # 裁掉，再估背景、最后平滑。计算顺序不受本页顺序影响（见上面那行
    # 灰字与 services/process.apply_chain）
    add_caption(form_bg, "裁剪区间")

    cut_chk = QCheckBox("裁剪区间")
    cut_chk.setToolTip("把指定 2θ 范围从曲线里挖掉：图上那一段空着，"
                       "纵轴自动范围也跟着跳过它。\n"
                       "典型用途：某个巨峰把其余部分压扁了，挖掉它让其余"
                       "看得清。\n"
                       "<b>影响的是数据</b>：处理产物与导出文件里这段同样是空的"
                       "（导出文件头会写明删了哪一段）")
    window.params["裁剪区间"] = cut_chk
    form_bg.addRow(cut_chk)

    cut_lo = QDoubleSpinBox()
    cut_hi = QDoubleSpinBox()
    for box, val in ((cut_lo, 2.0), (cut_hi, 3.0)):
        box.setRange(0.0, 180.0)
        box.setDecimals(3)
        box.setSingleStep(0.1)
        box.setValue(val)
        box.setSuffix("°")
        _fit_max_width(box, 84)      # 旧上限 84 降为下限，上限 = 完整显示 "180.000°"
        box.setToolTip("裁剪区间的起止 2θ（含两端）。起点 ≥ 终点时视为"
                       "不裁剪（不会出错）")
    window.params["裁剪起点 (°)"] = cut_lo
    window.params["裁剪终点 (°)"] = cut_hi
    cut_add_btn = QPushButton("添加")
    cut_add_btn.setObjectName("cut_add_btn")
    cut_add_btn.setStyleSheet("padding: 2px 5px;")
    cut_add_btn.setToolTip("把这一段的起止加进下面的清单——可以删好几段"
                           "（例如同时删 2–3° 和 7–8°）")
    form_bg.addRow(bg_row((cut_lo, 1), (QLabel("–"), 0), (cut_hi, 1),
                          (cut_add_btn, 0)))
    # 动态长文本（几段区间串起来能到二三百 px）：省略号 + 悬停看全，
    # min_cap 封顶——不封的话窄坞下这一行把页面顶宽、右缘被裁
    cut_list_lbl = _ElideLabel("清单：空", min_cap=180)
    cut_list_lbl.setStyleSheet("color: gray;")
    cut_list_lbl.setToolTip("当前要挖掉的全部区间（图上、处理产物与导出文件"
                            "里同样生效）")
    window.cut_list_lbl = cut_list_lbl
    cut_clear_btn = QPushButton("清空")
    cut_clear_btn.setObjectName("cut_clear_btn")
    cut_clear_btn.setStyleSheet("padding: 2px 5px;")
    cut_clear_btn.setToolTip("清空清单（= 不裁剪）")
    form_bg.addRow(bg_row((cut_list_lbl, 2), (cut_clear_btn, 0)))

    add_caption(form_bg, "背景扣除")

    bg_mode = QComboBox()
    # 顺序按常用程度（用户 2026-10-07："把自动基线放第一个"）：最常用的
    # 自动基线打头，"关闭"垫底。**默认值显式点回 off**——不能靠插入顺序
    # 默认第一项，不然重排顺序会把默认模式悄悄改成自动基线
    for text, data in (("自动基线（推荐）", "auto"),
                       ("空扫相减", "blank"),
                       ("手动锚点", "anchor"),
                       ("关闭", "off")):
        bg_mode.addItem(text, data)
    bg_mode.setCurrentIndex(bg_mode.findData("off"))
    bg_mode.setToolTip(
        "背景：不含样品结构信息的加性信号（空气散射、非晶漫散射、"
        "荧光、暗电流、直射束光晕）。\n"
        "空扫相减：实测——先拍一张没有样品的图，从样品图里逐点减掉"
        "（最干净，但必须真有空扫、曝光/几何一致）。\n"
        "自动基线：自动估计——假设背景比峰宽且平滑，按窗口宽度估计"
        "（一键，无需额外数据）。\n"
        "手动锚点：手动标定——在图上点几个只有背景的位置连成底线"
        "（最可控，适合宽鼓包样品）。")
    window.params["背景扣除模式"] = bg_mode
    form_bg.addRow(bg_mode)

    # 空扫模式：选图 + 归一化系数
    bg_blank_btn = QPushButton("选择空扫图")
    bg_blank_btn.setObjectName("bg_blank_btn")
    bg_blank_btn.setToolTip("挑一张没有样品的空扫/空白图（同一几何、同一曝光）")
    bg_blank_btn.setStyleSheet("padding: 2px 5px;")
    bg_scale_box = QDoubleSpinBox()
    bg_scale_box.setRange(0.01, 100.0)
    bg_scale_box.setDecimals(2)
    bg_scale_box.setSingleStep(0.1)
    bg_scale_box.setValue(1.0)
    _fit_max_width(bg_scale_box, 84)     # 旧上限 84 降为下限，上限 = 完整显示 "100.00"
    bg_scale_box.setToolTip("空扫归一化系数：样品与空扫的曝光时间/束流不一致"
                            "时填比值（样品÷空扫），一致就保持 1.0")
    window.params["空扫归一化"] = bg_scale_box
    blank_row = bg_group([bg_row((bg_blank_btn, 1), (bg_scale_box, 1))])
    form_bg.addRow(blank_row)

    # 自动模式：窗口宽度（唯一的旋钮）
    # 初始值 = 引擎默认（background.AUTO_WINDOW_DEG，2026-09-28 起 0.2°）：
    # 改之前这里是写死的 1.0，与面板默认/恢复默认（都是引擎那个值）不一致
    # ——没聚焦任何面板时 `_proc_settings` 会退回读控件，读到的就是那个 1.0
    bg_window_box = QDoubleSpinBox()
    bg_window_box.setRange(0.1, 10.0)
    bg_window_box.setDecimals(2)
    bg_window_box.setSingleStep(0.1)
    bg_window_box.setValue(AUTO_WINDOW_DEG)
    bg_window_box.setSuffix("°")
    _fit_max_width(bg_window_box, 84)    # 旧上限 84 降为下限，上限 = 完整显示 "10.00°"
    bg_window_box.setToolTip("窗口宽度 (°)：多宽的一段算「背景」而不是「峰」。"
                             "取最宽峰宽的 3–10 倍（本数据峰宽约 0.1–0.3°，"
                             f"默认 {AUTO_WINDOW_DEG:g}°）；取小了峰会被"
                             "当背景扣掉，取大了跟不上背景自身的起伏")
    window.params["背景窗口 (°)"] = bg_window_box
    auto_row = bg_group([bg_row((bg_window_box, 1), (QWidget(), 1))])
    form_bg.addRow(auto_row)

    # 锚点模式：拾取开关 + 清空 + 计数；下一行是拟合方式
    bg_pick_btn = QPushButton("拾取锚点")
    bg_pick_btn.setObjectName("bg_pick_btn")
    bg_pick_btn.setCheckable(True)
    bg_pick_btn.setToolTip("打开后在 1D 图上左键点选「只有背景」的位置；"
                           "再点已有的锚点即可删除。锚点按文件各记各的")
    bg_clear_btn = QPushButton("清空锚点")
    bg_clear_btn.setObjectName("bg_clear_btn")
    bg_clear_btn.setToolTip("清空当前 1D 面板所对应文件的全部锚点")
    for _b in (bg_pick_btn, bg_clear_btn):
        _b.setStyleSheet("padding: 2px 5px;")
    # 动态长文本（"21 个锚点（1.29–2.69°）"）：省略号 + 悬停看全，
    # min_cap 封顶——实测它是把处理页内容顶到 301 > 坞最小 280 的主力
    bg_count_lbl = _ElideLabel("", min_cap=140)
    bg_count_lbl.setStyleSheet("color: gray;")
    bg_fit_combo = QComboBox()
    # 默认 = 保单调平滑：三种都严格过锚点，差别在锚点之间——弯背景上
    # 折线偏高（实测中段 +29）、自然样条会过冲（扣过头），pchip 既平滑
    # 又不过冲，合成真值上平均绝对偏差 6.1（折线 28.8、样条 16.4）
    for text, data in (("保单调平滑（默认）", "pchip"),
                       ("折线（直线连锚点）", "linear"),
                       ("样条（可能过冲）", "spline")):
        bg_fit_combo.addItem(text, data)
    bg_fit_combo.setToolTip("三种都严格过锚点，差别在锚点之间：\n"
                            "保单调平滑：光滑但不过冲（推荐）；\n"
                            "折线：相邻锚点直线相连（实验室惯例、最透明，"
                            "弯背景上会扣不干净）；\n"
                            "样条：自然三次样条（更平滑，但可能在锚点之间"
                            "冲到真值以下，扣过头）。\n"
                            "锚点数：折线 2 个就能画；保单调平滑与样条至少 3 个，"
                            "不足时自动退回折线")
    window.params["锚点拟合方式"] = bg_fit_combo
    anchor_row = bg_group([
        bg_row((bg_pick_btn, 1), (bg_clear_btn, 1), (bg_count_lbl, 0)),
        bg_row((bg_fit_combo, 1)),
    ])
    form_bg.addRow(anchor_row)

    bg_show_raw = QCheckBox("显示原始曲线对比")
    bg_show_raw.setToolTip("实时预览：把未扣背景的原始曲线（虚线）与基线"
                           "（点线）一起画出来，看清扣掉了什么。\n"
                           "只作用于 1D 单曲线面板——对比/瀑布/热图里多条"
                           "曲线叠在一起，再叠一层原始线会看不清")
    window.params["背景显示原始"] = bg_show_raw
    form_bg.addRow(bg_show_raw)

    bg_clip = QCheckBox("负值截断为 0")
    bg_clip.setToolTip("默认不截断：背景是从两侧对称估的，扣完噪声摆到 0 "
                       "以下是正常的（噪声地板露出），强行截断会把噪声平均"
                       "抬高约 1σ。只在出图需要非负值时打开")
    window.params["负值截断为 0"] = bg_clip
    form_bg.addRow(bg_clip)

    # ── 平滑（小节）：先只给最朴素的一种（滑动平均） ──
    # 为什么不给 Savitzky–Golay：先把"平滑了多宽、削掉多少峰高"这件事做
    # 对；方法以后要加就是一个下拉框的事（唯一入口在 services/process）。
    add_caption(form_bg, "平滑")

    smooth_chk = QCheckBox("平滑曲线")
    smooth_chk.setToolTip("滑动平均：窗口内取平均。\n"
                          "窗口按 <b>2θ</b> 宽度给（不是点数），所以改输出点数"
                          "重算之后「平滑了多宽」仍然一样。\n"
                          "代价：峰会变矮变宽——窗口要远小于峰宽，"
                          "旁边那行灰字会告诉你它折成几个点")
    window.params["平滑曲线"] = smooth_chk
    form_bg.addRow(smooth_chk)

    smooth_box = QDoubleSpinBox()
    smooth_box.setRange(0.0, 2.0)
    smooth_box.setDecimals(2)
    smooth_box.setSingleStep(0.05)
    smooth_box.setValue(0.10)
    smooth_box.setSuffix("°")
    _fit_max_width(smooth_box, 84)       # 旧上限 84 降为下限，上限 = 完整显示 "2.00°"
    smooth_box.setToolTip("窗口宽度 (°)：参与平均的 2θ 跨度。\n"
                          "典型峰宽 0.1–0.3°，窗口取到峰宽量级就会明显削峰；"
                          "先取 0.05–0.15° 试，看削掉多少再定")
    window.params["平滑窗口 (°)"] = smooth_box
    smooth_pts_lbl = QLabel("")           # 折成几个点（刷新时按当前曲线填）
    smooth_pts_lbl.setStyleSheet("color: gray;")
    window.smooth_points_lbl = smooth_pts_lbl
    form_bg.addRow(bg_row((smooth_box, 1), (smooth_pts_lbl, 1)))

    smooth_method = QComboBox()
    for text, data in (("滑动平均", "boxcar"),
                       ("Savitzky–Golay", "savgol")):
        smooth_method.addItem(text, data)
    # 默认 SG（用户 2026-10-07："平滑默认选择SG方法"）——同样窗口削峰
    # 少得多、峰形保得住；与 panel_state._DISPLAY_DEFAULTS 同一口径
    smooth_method.setCurrentIndex(smooth_method.findData("savgol"))
    smooth_method.setToolTip(
        "滑动平均：窗口内取平均（最简单，削峰明显）。\n"
        "Savitzky–Golay：窗口内拟合多项式再取中心值：<b>同样的窗口宽度削峰"
        "少得多</b>、峰形保得更住，代价是接触陡边（低角鼓包）时可能压出轻微"
        "负值下冲。窄峰、要做峰形分析时用它。")
    window.params["平滑方法"] = smooth_method

    smooth_order = QSpinBox()
    smooth_order.setRange(2, 6)
    smooth_order.setValue(3)
    smooth_order.setSuffix(" 阶")
    _fit_max_width(smooth_order, 60)     # 旧上限 60 降为下限，上限 = 完整显示 "6 阶"
    smooth_order.setToolTip("Savitzky–Golay 的多项式阶数（2–3 常用：阶数越高"
                            "越贴合峰形，但也越容易跟着噪声抖）")
    window.params["平滑阶数"] = smooth_order
    form_bg.addRow(bg_row((smooth_method, 2), (smooth_order, 1)))

    smooth_chk.toggled.connect(lambda _=False: _sync_smooth_rows(window))
    smooth_method.currentIndexChanged.connect(
        lambda _=0: _sync_smooth_rows(window))
    window._sync_smooth_rows = lambda: _sync_smooth_rows(window)

    # ── 配方（小节，**放在整页最下方**）：一份设置存下来，别的批次能照用 ──
    # 用户 2026-09-27："我觉得配方可以保存用也挺好，两批数据用同一个锚点
    # 可行吗"——可行：跨批次沿用锚点的**位置**（那是装置的性质），强度到每张
    # 图自己的曲线上重取（同 [批量处理] 一贯的口径，见 services/recipes）。
    # 配方的来源有两个：本图配方（那个文件上次用的）与已保存的命名配方。
    # **位置**（用户 2026-10-02 第 3 条："配方位置 最下方？"）：一份配方管的是
    # **整条链**（背景 + 平滑 + 裁剪），原先夹在背景扣除与平滑之间，看着像
    # 只管背景——而且你在它下面改的平滑/裁剪其实也会被存进去，顺序跟含义
    # 反着。挪到三节之后：先把整条链摆好，再存下来。
    add_caption(form_bg, "配方")
    # 处理来处（用户 2026-09-27 立项："在扣背景的参数栏可以显示这个图是以 a
    # 的锚点为基准进行扣除的。按文件。"；2026-10-07 用户："配方详细内容的
    # 灰字位置改一下，和配方放一起"——它讲的本来就是配方，挪到配方标题下）
    bg_prov_lbl = QLabel("")
    bg_prov_lbl.setStyleSheet("color: gray;")
    bg_prov_lbl.setWordWrap(True)
    window.bg_prov_lbl = bg_prov_lbl
    form_bg.addRow(bg_prov_lbl)
    recipe_combo = QComboBox()
    recipe_combo.setToolTip("选一份配方：本图配方：这个文件上次处理用的那套；"
                            "下面是已保存的命名配方。选好点 [套用] 才生效——"
                            "打开原始条目永远是原始曲线，不会自动扣。")
    window.recipe_combo = recipe_combo
    recipe_apply_btn = QPushButton("套用")
    recipe_apply_btn.setObjectName("recipe_apply_btn")
    recipe_apply_btn.setToolTip("把选中的配方用到<b>编辑对象</b>这张图上："
                                "锚点位置照搬、强度按本图曲线重取，"
                                "其余设置填进本页控件并立刻重画")
    recipe_apply_btn.clicked.connect(lambda: _apply_selected_recipe(window))
    # [存成配方…]（原名 [保存为配方…]）：存的是**设置**，不是数据——与
    # 下面的 [存成产物] 成对：一个存"怎么做"，一个存"做出来的东西"。
    # 用户 2026-10-02 第 2 条说这两个按钮重复，其实是名字太像：
    # "保存为配方" / "采用这份结果"都像"保存"，各改一个字把差别写在脸上
    recipe_save_btn = QPushButton("存成配方…")
    recipe_save_btn.setObjectName("recipe_save_btn")
    recipe_save_btn.setToolTip("把<b>本页当前这套设置</b>（背景 / 平滑 / 裁剪）"
                               "存成一个命名配方，以后任何一批都能套用"
                               "（配方文件：outputs/recipes.json）。存的是设置；"
                               "要把结果留下用下面的 [存成产物]")
    recipe_save_btn.clicked.connect(lambda: _save_recipe_dialog(window))
    recipe_del_btn = QPushButton("删除")
    recipe_del_btn.setObjectName("recipe_del_btn")
    recipe_del_btn.setToolTip("删掉下拉里选中的那份<b>已保存</b>配方")
    recipe_del_btn.clicked.connect(lambda: _delete_selected_recipe(window))
    window.recipe_btns = (recipe_apply_btn, recipe_save_btn, recipe_del_btn)
    form_bg.addRow(bg_row((recipe_combo, 2)))
    form_bg.addRow(bg_row((recipe_apply_btn, 1), (recipe_save_btn, 1),
                          (recipe_del_btn, 0)))

    # 面板绑定这组控件（_sync_bg_rows 在小节外也要用）
    window.bg_rows = {"blank": blank_row, "auto": auto_row,
                      "anchor": anchor_row}
    window.bg_blank_btn = bg_blank_btn
    window.bg_pick_btn = bg_pick_btn
    window.bg_clear_btn = bg_clear_btn
    window.bg_count_lbl = bg_count_lbl
    bg_mode.currentIndexChanged.connect(lambda _=0: _on_bg_mode(window))
    bg_window_box.valueChanged.connect(lambda _=0.0: _refresh_proc(window))
    window.params["空扫归一化"].valueChanged.connect(
        lambda _=0.0: _refresh_proc(window))
    bg_show_raw.toggled.connect(lambda _=False: _refresh_proc(window))
    bg_clip.toggled.connect(lambda _=False: _refresh_proc(window))
    bg_fit_combo.currentIndexChanged.connect(lambda _=0: _refresh_proc(window))
    # 处理页的另两项（平滑 / 裁剪）：改参数即重画，与背景扣除同一条实时通路
    smooth_chk.toggled.connect(lambda _=False: _refresh_proc(window))
    smooth_box.valueChanged.connect(lambda _=0.0: _refresh_proc(window))
    cut_chk.toggled.connect(lambda on: _on_cut_toggled(window, on))
    cut_add_btn.clicked.connect(lambda: _on_cut_add(window))
    cut_clear_btn.clicked.connect(lambda: _on_cut_clear(window))
    smooth_method.currentIndexChanged.connect(
        lambda _=0: _refresh_proc(window))
    smooth_order.valueChanged.connect(lambda _=0: _refresh_proc(window))
    bg_blank_btn.clicked.connect(lambda: _on_choose_blank(window))
    bg_pick_btn.toggled.connect(lambda on: _on_pick_anchor(window, on))
    bg_clear_btn.clicked.connect(lambda: _on_clear_anchors(window))
    # 注意：_sync_bg_rows（按模式收起无用行）**不在这里调**——坞的最小
    # 宽度在本函数末尾按 minimumSizeHint 算一次，隐藏的行不计入尺寸；
    # 先收起再量会把宽度量小、切模式时被裁。所以放到末尾、量完之后。

    # ── 处理页唯一的产出按钮（2026-10-07 用户定：两个合成一个）──
    #   * **勾了文件** → 批量处理勾选的每一份（原 [批量处理] 的语义）；
    #   * **一个没勾** → 存编辑对象这一张（原 [存成产物] 的语义）。
    # 两者本来就走同一台机器（`_process_source` 逐条处理 + 同一套台账），
    # 只是目标集不同；文字跟着勾选数走（file_dock._refresh_check_labels）。
    # [重算这张图] **已撤**（同一天用户 C："处理页不要 2θ/重算"）——处理页
    # 从此没有任何积分出口，"改范围重算"统一回「1D」页（那页行与按钮都在）。
    # 绝对强度不能跨文件套，见 plot_views._proc_batch_apply 的说明。
    btn_proc_save = QPushButton("存成产物")
    btn_proc_save.setObjectName("proc_save_btn")
    btn_proc_save.setToolTip("勾了文件 = 把当前这套处理（背景 / 平滑 / 裁剪）"
                             "用到勾选的每一份（锚点只传 2θ 位置、强度逐文件"
                             "重取）；一个都没勾 = 存编辑对象这一张。结果都进"
                             "文件栏「处理产物 …」，对比 / 热图 / 导出直接复用；"
                             "存的是数据，要存「这套设置」用最下面的 [存成配方…]")
    btn_proc_save.clicked.connect(lambda: _proc_save(window))
    window.proc_save_btn = btn_proc_save
    # 兼容别名：旧名（测试与探针按它们找按钮）现在都指向这一颗，行为 = 合并语义
    window.proc_keep_btn = btn_proc_save
    window.proc_batch_btn = btn_proc_save
    btns_bg.addWidget(btn_proc_save)

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
    window.pending_labels = {"数据": lbl_data_pending}
    _connect_pending_hooks(window)

    # 拖动坞边框的尺寸下限（上下左右都设）：左右 = 最宽一张表单的
    # 最小宽度 + "壳"（滚动条 + 分组/表单边距，另加少量余量），拖
    # 再窄也到这就停，不会裁掉控件；上下 = 固定件（编辑对象名 +
    # 两组标题/按钮行 + 各几行内容）不被遮没的高度，再矮由各半
    # 自己的滚动条接管。用表单 minimumSizeHint 而不是 content 的
    # sizeHint：转盘限宽后 sizeHint 低估了成对行的最小宽度（实测
    # 会横向裁掉近 20px）
    forms = (form_1d, form_bg, form_cmp, form_draw)
    form_min = max(f.parentWidget().minimumSizeHint().width()
                   for f in forms)   # 量装表单的部件，不是布局
    # 坞顶那两行固定件也要放得下（编辑对象名 / 几何配置行）。几何行里
    # 的 [返回分析模式] 平时是隐藏的，而隐藏件不计入 minimumSize ——
    # 量出来会偏小，一到校准模式按钮现身就被裁，所以显式补上它的宽
    head_min = (max(window.focus_label.minimumSizeHint().width(),
                    window.geom_row.minimumSize().width()
                    + window.calib_exit_btn.sizeHint().width() + 8,
                    window.data_row.minimumSize().width() + 8,
                    # 「未应用」灰字平时隐藏（隐藏件不计入 minimumSize）：
                    # 一亮就多占一行宽，坞的宽下限要预留它，否则它出现
                    # 的那一刻把「点数」挤出可视区（2026-10-04 反馈）
                    window.data_row.minimumSize().width()
                    + window.pending_labels["数据"].sizeHint().width()
                    + 8))
    form_min = max(form_min, head_min)
    # 壳：一页的滚动条宽度 + 那一页表单的左右边距（各页相同）
    # 壳 = 滚动条宽 + 表单左右边距 + 8（原来 QGroupBox 那圈 4 px 内边距，
    # 页面化以后由页面自己的布局接管——漏算它会让校准页最宽的那行
    # 差 5 px 越界，实测抓到）
    chrome = (page_1d.findChild(QScrollArea).verticalScrollBar().sizeHint()
              .width()
              + form_1d.contentsMargins().left()
              + form_1d.contentsMargins().right() + 8)
    dock.setMinimumWidth(form_min + chrome + 2)
    content.setMinimumWidth(form_min + chrome + 2)   # 双保险：坞本身也算上
    # 坞的最小高度：编辑对象名 + 几何配置行 + 一页的固定件（滚动区最低
    # 110 = 露出标题 + 几个控件；按钮行 ~40）。翻页栈的最小尺寸只报当前
    # 页，且嵌套后布局 minimumSize 不再含子件 minimumSizeHint（探针
    # 验证），靠布局算会把下限塌成一行标签的高度
    dock.setMinimumHeight(
        window.focus_label.minimumSizeHint().height()
        + window.geom_row.minimumSize().height()
        + window.data_row.minimumSize().height() + 12 + 110 + 40)

    _sync_cut_label(window)      # 裁剪清单标签：空
    _sync_smooth_rows(window)    # 平滑阶数只在 SG 下可编辑
    # 宽度量完再按当前模式收起"背景扣除"的无用行（隐藏的行不计入
    # minimumSizeHint，先收再量会把坞宽量小、切模式时被裁）
    _sync_bg_rows(window)

    dock.setWidget(content)
    window.addDockWidget(Qt.RightDockWidgetArea, dock)
    return dock
