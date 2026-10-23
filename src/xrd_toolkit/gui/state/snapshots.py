"""面板参数快照：控件值拍平、数据/显示参数各取各的口径。

2026-10-21 从 panel_state.py 原样搬出（只改结构，不动逻辑）。
panel_state 再导出，各视图照旧经 gui_panel_state 取用。
"""
from PySide6.QtWidgets import QCheckBox, QComboBox, QMainWindow


_DISPLAY_DEFAULTS = {
    "自动对比度": True,
    "对比度下限": 1.0,
    "对比度上限": 100000.0,
    "剖面角度 (°)": 0.0,
    # 2D 面板的色图（Customize 里改；与 CLI view_diffraction 的默认一致）
    "2D 色图": "magma",
    # 2D 图上那个白色十字（当前几何配置的束心）画不画。**默认不画**
    # （用户 2026-10-09："默认 2D 图是不显示束心的"——先看干净的衍射图，
    # 要核对束心位置再勾上；2026-10-01 起这个开关就有，只是当时默认画）。
    # 校准图不参与：那张图上没有这个标记
    "显示束心": False,
    "对数纵轴": False,
    "纵轴自动": True,
    "纵轴下限": 1.0,
    "纵轴上限": 100000.0,
    # 寻峰显示（2026-10-23）：默认关——不勾就不会在别人图上冒标记；
    # 参数按面板各记各的，改完即画（同「1D 显示」）
    "显示峰位": False,
    "标注全部峰位": False,
    "寻峰显著度": 5.0,
    "寻峰最小峰距 (°)": 0.008,
    "寻峰最小 FWHM (°)": 0.0,
    "寻峰最多根数": 20,
    # 对比归一化 = 模式（global 全图最强峰 / file 指定数据 /
    # off 不归一化，默认 off = 原样画原始强度；"各自最强峰"已删，
    # 见 _compare_shown_curves 的说明），
    # "归一化目标" = file 模式用哪个文件
    "对比归一化": "off",
    "归一化目标": "",
    # 渐变色系 = 对比曲线的配色（2026-10-13 用户："对比图的颜色改为一种
    # 颜色的渐变过程……只留渐变顺序，颜色这种选项"——原先的分类色板与
    # "逐条自定义色"一起退休）：同一种颜色的深浅渐变，**按文件名尾号**从
    # 深到浅（第 1 条 = 基色、最后一条最浅；顺序口径 2026-10-15 晚用户
    # 裁定："识别的不对，应该是文件名最后几位数的顺序"——颜色序号按
    # 文件名最后一串数字排，00029 最深；文件栏怎么排只动行/图例位置，
    # 见 plot_compare._color_rank_by_number）；单曲线面板（1D/剖面）用基色。
    # 色系取值见 _GRADIENT_FAMILIES。
    "渐变色系": "蓝",
    # 「色标」= 对比图右下角那条颜色刻度（颜色 ↔ 数据顺序的读数）画不画
    # （2026-10-15 晚用户："把这个改为显示图例的选项，默认不显示"——
    # 上午的"自动（>12 才画）"退休，改成朴素开关）：False = 不画（默认），
    # True = 画（两条也画）。参数坞是「显示色标」勾选。
    # 「色标说明」= 色标下方那行小字的文字（用户："自行更改描述方式"，
    # 在面板 [外观…] 里改）：空串 = 不画这行字（两端短名保留）。
    # **没有参数坞控件**（说明文字只在「外观」里编辑）——照 2026-10-14
    # 「渐变色系」的规矩：图自己的显示键不许从控件采集（见 _display_snapshot）。
    # 默认文案按用户 2026-10-15 晚的口径："颜色…应该是文件名最后几位数的
    # 顺序"——颜色顺序 = 文件名尾号（见 plot_compare._color_rank_by_number）。
    "色标": False,
    "色标说明": "颜色按文件名尾号顺序",
    # 「色标位置」= 色标卡片放图的哪个角（用户 2026-10-16："实现色标仅保留
    # 横向以及可以选择放在图的哪个角落"——同日的横竖二选在发版前退休，
    # 色标固定横版；默认角当天再由用户改为"右上"："色标位置默认右上"）：
    # "右上"（默认）/"右下"/"左下"/"左上"。
    # 同样**没有参数坞控件**，在面板 [外观…] 里选（与「色标说明」同处）。
    "色标位置": "右上",
    # 对比堆叠 = 瀑布式错开叠放（每条曲线抬到自己的行上，y 刻度 =
    # 样品名；堆叠下纵轴范围/对数不适用，同瀑布）
    "对比堆叠": False,
    # "数据名字"开关，一把管一处（2026-10-12 用户："对比热图现在共用
    # 一个显示数据名，但是是在对比的板块" → 拍板"甲，拆开"）：
    #   「显示数据名」=「对比显示」小节那把：平铺图例 + 堆叠纵轴样品名；
    #   「热图显示数据名」=「热图显示」小节自己的那把：只管热图行名。
    # 瀑布的 χ 刻度同日也拍板"甲"后**退出开关**——那是纵轴的刻度（一条
    # 轴），不是"名字"，默认就画（见 plot_views._draw_waterfall）。
    # 两把都默认关——名字多了糊成一条黑带（2026-10-08，81 条堆叠全名实测
    # 占掉图宽 29%、完全不认读）；勾上按"放得下"的条件显示（短名 +
    # 抽稀）。认线/认行兜底 = 悬停读数（全名）。
    "显示数据名": False,
    "热图显示数据名": False,
    "视图 2θ 下限 (°)": None,   # None = 跟随积分 2θ 范围；缩放/平移后写回显式值
    "视图 2θ 上限 (°)": None,
    "热图视图 2θ 下限 (°)": None,   # 热图专用（2026-10-08 晚拆键）；语义同上面那对
    "热图视图 2θ 上限 (°)": None,
    # 热图显示：色图 / 归一化（与对比同款 each/global/off 三模式）/
    # 对数强度 / 强度范围（自动 = 1%/99.9% 分位）
    "热图色图": "magma",
    "热图归一化": "off",
    "热图对数": False,
    "热图自动范围": True,
    "热图下限": 1.0,
    "热图上限": 100000.0,
    # 背景扣除（1D/对比/瀑布/热图四条曲线路径共用；2D 是原始图、剖面是
    # 沿线的像素强度，都不参与）。模式 off 关闭 / blank 空扫相减 /
    # auto 自动基线 / anchor 手动锚点；锚点列表与空扫曲线不是参数，
    # 放窗级属性（window.bg_anchors / window.bg_blank）
    "背景扣除模式": "off",
    "背景窗口 (°)": 0.2,
    "空扫归一化": 1.0,
    # 锚点之间的拟合：保单调平滑（pchip，过点、不过冲）。新面板的显示
    # 参数从这份默认起步、再回放进控件——所以这里不跟着改的话，界面
    # 上下拉框会被刷回"折线"（app.py 的 [恢复默认] 那份也要同口径）
    "锚点拟合方式": "pchip",
    # 显示原始曲线对比（实时预览"扣前 vs 扣后"）与负值截断
    "背景显示原始": True,
    "负值截断为 0": False,
    # 处理页的另两项（平滑 / 裁剪）：同样"画的时候才做"，改它即重画、
    # 不需要重新积分。默认都关着——不勾就是没做处理（产物键也因此不变）。
    "平滑曲线": False,
    "平滑窗口 (°)": 0.10,
    # 平滑默认法=SG（用户 2026-10-07："平滑默认选择SG方法"）：同样窗口
    # 削峰少得多、峰形保得住；"滑动平均"仍在下拉里可选
    "平滑方法": "savgol",
    "平滑阶数": 3,
    "裁剪区间": False,
    "裁剪起点 (°)": 2.0,
    "裁剪终点 (°)": 3.0,
    # 堆叠行距（2026-10-05 用户："让用户自己改 y offset"）：默认自动 =
    # 第二高的行峰 × 0.7（services/stacking.row_step）；勾"手动行距"后
    # 按倍数微调。纯显示参数，改了立刻重画。
    "手动行距": False,
    "行距倍数": 1.0,
    # 瀑布的行距（2026-10-07）：与上面那对同口径，但键独立——对比那对
    # 控件住在「对比」页、瀑布这对住在「原图」页；一个键只挂一个控件，
    # 共用就没法两页各摆一个（在瀑布边上改不着对比的框，反之亦然）。
    "瀑布手动行距": False,
    "瀑布行距倍数": 1.0,
}
_DISPLAY_PARAMS = frozenset(_DISPLAY_DEFAULTS)   # 显示参数 = 以上全部

# 视图窗口的键：None = 跟随积分范围，缩放/平移后写回显式值（见
# plot_panels._on_xlim_changed）。控件（「对比」/「热图显示」小节各自
# 那对框）只是**展示与输入**：快照采集**不从这里拿控件值**——框里显示
# 的往往是"跟随"折算出来的数（该面板的积分范围），采集会把"跟随"钉成
# 显式窗口，之后改积分范围重算，旧窗口就把新曲线裁错了（_display_snapshot
# / 快照回放这条路）。用户真填进框里的值由 plot_compare._refresh_heat_view
# / _refresh_heatmap_view 显式写回快照。热图那对是 2026-10-08 晚拆出来的
# 专用键（用户："对比与热图分开管理"）。
_VIEW_WINDOW_KEYS = ("视图 2θ 下限 (°)", "视图 2θ 上限 (°)",
                     "热图视图 2θ 下限 (°)", "热图视图 2θ 上限 (°)")

def _snapshot_params(window: QMainWindow) -> dict:
    """拍下参数面板当前状态：配置条目 + 各控件值 + 自动对比度开关。

    每张面板在开图/重算时拍一张快照挂在 dock.params_snapshot 上 =
    "这张图是用什么参数画的"；点选面板时参数坞回放快照（既能看也
    能改，改完 [应用] 重算并刷新快照）。显示参数（对数纵轴/纵轴
    范围/对比归一化）也收在快照里：每张图各记各的，画图时只读
    自己面板的快照（见 _panel_param）。
    """
    snap = {"config": window.config_name}
    for name, w in window.params.items():
        if isinstance(w, QCheckBox):
            snap[name] = w.isChecked()
        elif isinstance(w, QComboBox):
            snap[name] = w.currentData()   # 下拉框记 data（文本只是显示名）
        else:
            snap[name] = w.value()
    return snap

def _data_snapshot(window: QMainWindow, base: dict = None) -> dict:
    """数据参数快照：数据组取控件当前值，显示参数沿用 base（无 base
    用默认值）。

    base = 面板自己的旧快照（重算时传入）：显示参数每张图各记各的，
    重算不改它的长相；新面板（base=None）显示参数从默认起步——不
    受上一张焦点图的设置影响（开新图永远是"默认长相"）。

    **base=None 时叠"待应用"账本**（2026-10-13）：用户先在没有对应
    面板的时候调好的显示参数，出图时作为新面板的起点（_stash_pending_
    display 记的账）。账本不清在这里——一次 [出图] 可能连开好几张
    （批量），一组图该有同一套设置；作废时机见 _stash_pending_display。
    """
    snap = _snapshot_params(window)
    for name in _DISPLAY_PARAMS:
        if base is not None and name in base:
            snap[name] = base[name]
        else:
            snap[name] = _DISPLAY_DEFAULTS[name]
    if base is None:
        pending = getattr(window, "pending_display", None)
        if pending:
            for name, value in pending.items():
                if name in _DISPLAY_PARAMS:
                    snap[name] = value
    return snap

def _display_snapshot(window: QMainWindow, base: dict = None) -> dict:
    """显示参数快照：显示组取控件当前值，数据参数沿用 base。

    base = 面板自己的旧快照（图像 [应用] 时传入）：图像 [应用] 只改
    显示不重算，数据参数保持"这张图当时是用什么算的"，顺手改了
    数据控件也不会冒充成这张图的计算参数。
    """
    given = base is not None      # 要在 "or {}" 之前判（None 会被吞掉）
    base = base or {}
    snap = {"config": window.config_name}
    for name, w in window.params.items():
        if isinstance(w, QCheckBox):
            value = w.isChecked()
        elif isinstance(w, QComboBox):
            value = w.currentData()   # 下拉框记 data（文本只是显示名）
        else:
            value = w.value()
        if name in _DISPLAY_PARAMS:
            snap[name] = value
        elif name in base:
            snap[name] = base[name]
        elif not given:
            snap[name] = value
        # 给了 base 而 base 里没这个键 = 这张图**故意不管**这个键（对比/热图
        # 的 2θ/点数，2026-10-03）：保持缺省，别从控件把它救回来——救回来
        # 就等于"看一眼对比就把坞顶那行染上了"
    # 视图窗口不从控件采集（理由见 _VIEW_WINDOW_KEYS）：沿用 base 里的旧值，
    # 没旧值 = 跟随积分范围
    for name in _VIEW_WINDOW_KEYS:
        snap[name] = base[name] if name in base else _DISPLAY_DEFAULTS[name]
    # 渐变色系同理不从控件采集（用户 2026-10-14："对比图颜色在改参数后会
    # 恢复默认"）：它是**图自己**的显示项——改它的两条路各自显式写快照
    # （参数坞那把下拉走 plot_compare._refresh_compare_family、「外观」
    # 对话框直接写），谁都不许在下一次按控件重建时把它打回默认。原先从
    # 控件采集：在「外观」里选完色系，随后任一显示参数走 _refresh_compare
    # 重建，下拉还停着的旧值（默认蓝）就把快照里的色系盖掉了。
    snap["渐变色系"] = (base["渐变色系"] if "渐变色系" in base
                        else _DISPLAY_DEFAULTS["渐变色系"])
    # 没有控件的键从旧快照原样带过来：通用兜底——上面那圈按控件重建，
    # 带不过来的话 [应用] 一次就把显式值丢掉。视图 2θ 那两个键
    # 2026-09-27～10-07 控件撤掉的那段时期就靠这条活着（缩放/平移仍在
    # 写它们）；10-07 那对框在「热图显示」小节回来之后，由上面那条
    # "不从控件采集"专门接管。
    for name, value in base.items():
        snap.setdefault(name, value)
    return snap

