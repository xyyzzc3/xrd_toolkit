"""参数坞「处理」页：裁剪 / 背景扣除 / 平滑 / 配方 + [存成产物]。

2026-10-22 从 builder.py 的 _build_param_dock 原样搬出（只改结构，不动逻辑）。
"""
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QWidget)

from xrd_toolkit.gui.panels import _ElideLabel
from xrd_toolkit.gui.param_dock.helpers import (
    _apply_selected_recipe, _delete_selected_recipe, _fit_max_width,
    _on_bg_mode, _on_choose_blank, _on_clear_anchors, _on_pick_anchor,
    _save_recipe_dialog)
from xrd_toolkit.gui.param_dock.pages import (
    _on_cut_add, _on_cut_clear, _on_cut_toggled, _sync_smooth_rows)
from xrd_toolkit.gui.param_dock.widgets import add_caption, bg_group, bg_row
from xrd_toolkit.gui.plot_views import _proc_save, _refresh_proc
from xrd_toolkit.services.background import AUTO_WINDOW_DEG


def build_processing_page(window, form_bg, btns_bg):
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
