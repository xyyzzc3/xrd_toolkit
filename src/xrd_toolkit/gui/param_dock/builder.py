"""参数坞构建器：2026-10-21 从 gui/app.py 原样搬出（只改结构，不动逻辑）。

`_build_param_dock` 一个函数建完参数坞的固定行和五个入口页；本模块只负责
组装，业务仍在 services/ 与各 plot 模块。app.py 再导出，外部照旧经 gui_app 取用。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDockWidget, QDoubleSpinBox, QHBoxLayout, QLabel,
    QMainWindow, QPushButton, QScrollArea, QSpinBox, QStackedWidget,
    QVBoxLayout, QWidget)

from xrd_toolkit.config import CONFIGS, DEFAULT_CONFIG, entry_display
from xrd_toolkit.gui.calib import _build_calib_form
from xrd_toolkit.gui.config_ops import _sync_del_config_btn
from xrd_toolkit.gui.panel_state import (
    DATA_PARAM_DEFAULTS, FOCUS_EMPTY_TEXT, _apply_config)
from xrd_toolkit.gui.panels import _FocusElideLabel
from xrd_toolkit.gui.param_dock.helpers import (
    _connect_pending_hooks, _fit_max_width, _sync_bg_rows)
from xrd_toolkit.gui.param_dock.page_1d import build_1d_page
from xrd_toolkit.gui.param_dock.page_compare import (
    build_compare_buttons, build_compare_page, build_heatmap_section)
from xrd_toolkit.gui.param_dock.page_draw import (
    build_draw_page, install_draw_reset)
from xrd_toolkit.gui.param_dock.page_processing import build_processing_page
from xrd_toolkit.gui.param_dock.pages import (
    _build_check_summary, _build_plot_type_row, _sync_cut_label,
    _sync_data_row, _sync_geom_row, _sync_smooth_rows)
from xrd_toolkit.gui.param_dock.widgets import make_page


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
        box.setSingleStep(0.1)      # 2θ 范围按 0.1° 走，默认 1° 太粗
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
    npt.setSingleStep(100)           # 3000 点按 1 点滚太慢，按 100 点
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

    build_1d_page(window, form_1d, btns_1d)

    hint_row, auto = build_draw_page(window, form_draw)

    build_compare_page(window, form_cmp)

    build_processing_page(window, form_bg, btns_bg)

    build_heatmap_section(window, form_cmp)

    build_compare_buttons(window, btns_cmp)

    install_draw_reset(window, hint_row, auto)

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
