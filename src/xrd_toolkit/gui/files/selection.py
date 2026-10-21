"""文件栏选择/勾选：全选、按条件选、组态、勾选标签、选择对话框。

2026-10-21 从 file_dock.py 原样搬出（只改结构，不动逻辑）。
file_dock 再导出，tests/scripts 照旧经 gui_file_dock 取用。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QPushButton, QRadioButton, QSpinBox, QWidget)

from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui.files.model import (
    _group_state, _is_checkable, _refresh_group_badges, is_group)
from xrd_toolkit.gui.panel_state import _log


def _sync_current_to_checks(window: QMainWindow) -> None:
    """Qt 的"当前项"不许停在没勾选的条目上；停在别处就清掉当前项。

    防的是"点一行就留下一块底部/键盘焦点的痕迹"——点对号方块取消勾选时
    Qt 会先把那行设为当前项，不纠正它就留在一个"没勾的行"上。

    旧版这里叫"高亮"，因为那会儿整行深色是 Qt 的当前项底色；2026-09-28
    起**整行颜色专供"正在打开"**（见 refresh_open_marks，选中底色已在
    FileTree 的样式里取消），所以本函数现在只管 Qt 的当前项本身（键盘
    导航 / 滚动锚点还认它），不再负责任何颜色。

    旧版这里是"跳到第一个勾选条目"：那个条目通常就是列表最顶上那条文件，
    于是取消勾选、点组节点、[全选] 都会把列表拽回顶端（用户 2026-09-26
    起的反复投诉）。改成**直接清掉当前项**（`setCurrentItem(None)`）之后
    没有可跳的目标，也就没有了"同步放回/延后放回"的时序竞态——用户
    2026-09-27："没打字就跳了"，正是那种偶发。
    """
    current = window.file_list.currentItem()
    if current is None or is_group(current) \
            or current.checkState(0) == Qt.Checked:
        return      # 没高亮 / 高亮在组节点上 / 就是勾选着的条目：都不动
    window.file_list.setCurrentItem(None)

def _refresh_file_label(window: QMainWindow) -> None:
    """状态行文件标签跟随对号集合：0 个 = 未打开 / 1 个 = 名字 /
    N 个 = 已选 N 个（其中有产物条目就点明几个，免得"文件数"对不上）。"""
    checked = gui_sources.checked_sources(window)
    odd = [s for s in checked if s.kind != gui_sources.RAW]
    if not checked:
        window.file_label.setText("未打开任何文件")
    elif len(checked) == 1:
        window.file_label.setText(checked[0].display)
    else:
        tail = f"（含 {len(odd)} 条产物）" if odd else ""
        window.file_label.setText(f"已勾选 {len(checked)} 项{tail}")

def checked_items(window: QMainWindow) -> list:
    """当前打对号的**叶子**条目（原始数据 + 各组产物；组节点不算）。"""
    return [src.item for src in gui_sources.checked_sources(window)]

def checked_raw_items(window: QMainWindow) -> list:
    """当前打对号的原始数据条目（[删除] 与 [全选] 的口径）。"""
    return [src.item for src in gui_sources.checked_sources(window)
            if src.kind == gui_sources.RAW]

def _sync_group_states(window: QMainWindow) -> None:
    """按子项把每个组的状态重算一遍（组 = 全勾 / 不勾，两态）。

    批量改对号（导入、[全选]、[按条件选]、删除）都是**屏蔽信号**做的，
    itemChanged 的联动不会跑，所以这些地方收尾要显式补一次——否则界面上
    会出现"组上没勾、组里全是勾"这种自相矛盾的样子（2026-09-25 踩过：
    导入默认全勾时组态还停在 Unchecked，勾"整组取消"就没反应）。
    """
    tree = window.file_list
    for node in [tree.raw_group] + tree.groups():
        if not _is_checkable(node):
            continue    # 「打开的图」（不给勾）：没有组态这回事
        node.setCheckState(0, _group_state(
            node.child(i).checkState(0) for i in range(node.childCount())))
    _refresh_group_badges(window)   # 批量改完，折叠着的组上的数字也要跟上

def _sync_select_label(window: QMainWindow) -> None:
    """出图按钮与全选/全不选切换按钮的**文字**跟着勾选集合走。

    全选按钮（2026-09-27 用户："全选全部不选合一"）做哪件事由当前状态决定，
    标签必须跟着变，否则按下去会发生什么全靠猜。

    出图按钮上的数字（2026-09-27 用户："选中 81 个图出对比图，结果出了 162
    个文件的对比图"）：对比没错，是**勾选集比他想的大**——为了出 1D 图勾过
    81 个原始文件，之后又点了「1D 产物」组行，81+81=162；这些数字原先只写在
    状态行角落。把它写在按钮上、并在原图页给一句构成说明，就再也藏不住。
    """
    btn = getattr(window, "select_all_btn", None)
    if btn is not None:
        raw = window.file_list.raw_group
        n = raw.childCount()
        checked = sum(1 for i in range(n)
                      if raw.child(i).checkState(0) == Qt.Checked)
        # 标签把**作用范围**写出来（用户 2026-09-28 第 3 条）：这个按钮本来就
        # 是"勾只勾原始数据、清却清全部"（见 select_all 的说明），两个方向
        # 的范围不同——不写出来，用户按下去发生什么全靠猜
        btn.setText("全不选（全部条目）" if n and checked == n
                    else "全选（原始数据）")
    _refresh_check_labels(window)

def _check_summary(window: QMainWindow) -> str:
    """勾选集合的一句话说明：条数 + 构成（+ "同一条曲线画两遍"提醒）。"""
    checked = gui_sources.checked_sources(window)
    if not checked:
        return ("已勾选 0 项：点条目行：勾这一条，点组那一行：整组一起勾"
                "（对号方块可以取消）")
    kinds = {}
    for src in checked:
        kinds[src.kind] = kinds.get(src.kind, 0) + 1
    parts = [f"{text} {kinds[kind]}" for kind, text in
             ((gui_sources.RAW, "原始数据"), (gui_sources.ONED, "1D 产物"),
              (gui_sources.BG, "处理产物")) if kinds.get(kind)]
    out = f"已勾选 {len(checked)} 项：" + " ｜ ".join(parts)
    dup = _duplicate_raw_paths(checked)
    if dup:
        # 同一文件的原始条目与它的 1D 产物是同一条曲线，两个都勾 = 画两遍
        out += (f"；⚠ 其中 {len(dup)} 个文件同时勾了原始数据条目和它的 "
                "1D 产物（同一条曲线，会画两遍）")
    return out

def _duplicate_raw_paths(checked) -> set:
    """同一文件既勾了原始条目、又勾了它的 1D 产物 → 那些源文件路径。"""
    raw = {str(s.path) for s in checked if s.kind == gui_sources.RAW}
    prod = {str(s.path) for s in checked if s.kind == gui_sources.ONED}
    return raw & prod

def _refresh_check_labels(window: QMainWindow) -> None:
    """把"勾了多少条"写到 1D 页的出图按钮上，并刷新摘要那句构成说明。"""
    n = len(gui_sources.checked_sources(window))
    text = f"出图（已勾选 {n} 项）" if n else "出图（尚未勾选）"
    btn = getattr(window, "plot_1d_btn", None)
    if btn is not None:
        btn.setText(text)
    # 处理页合并后的产出按钮（2026-10-07）：勾了走批量、没勾存当前这张，
    # 行为不同、文字必须说清走的是哪一种
    btn = getattr(window, "proc_save_btn", None)
    if btn is not None:
        btn.setText(f"存成产物（已勾选 {n} 项）" if n else "存成产物（当前这张）")
    lbl = getattr(window, "check_summary_lbl", None)
    if lbl is not None:
        lbl.setText(_check_summary(window))

def _set_checks(window: QMainWindow, wanted: list) -> int:
    """一次性改一批对号，返回改动条数。

    wanted = [(条目, True/False), ...]——用列表而不是字典：PySide6 的
    条目类定义了 __eq__ 却没定义 __hash__（不可作字典键，2026-09-25
    踩过：报 TypeError，而槽函数里的异常只会打到后台，界面上表现为
    "点了没反应"）。要按身份去重就一律用 id()（`in` 走 __eq__，而
    __eq__ 比的是什么得看绑定实现，别赌）。屏蔽信号、改完统一刷新：
    200 个条目逐条触发 itemChanged 会记 200 行日志 + 刷 200 次状态行，
    界面直接卡住（与 add_files 批量加一个道理）。
    """
    changed = 0
    window.file_list.blockSignals(True)
    for item, want in wanted:
        if not _is_checkable(item):
            continue    # 「打开的图」那些行不给勾：别给它凭空长出一个复选框
        state = Qt.Checked if want else Qt.Unchecked
        if item.checkState() != state:
            item.setCheckState(state)
            changed += 1
    _sync_group_states(window)   # 组态跟着子项走（屏蔽信号时不会自动跑）
    window.file_list.blockSignals(False)
    if changed:
        _sync_current_to_checks(window)
        _refresh_file_label(window)
    _sync_select_label(window)
    return changed

def _selection_matches(window: QMainWindow, spec: dict) -> list:
    """按条件挑条目（纯读；弹窗预览与真正执行共用同一套判定）。

    序号一律按**列表顺序从 1 数**（界面上看到第几个就是第几个）：
      - 区间：第 start 到第 stop 个；
      - 间隔：从第 offset 个起，每 every 个选 1 个；
      - 名称包含（可选）：不区分大小写的子串，与上面两条叠加。
    """
    text = (spec.get("text") or "").strip().lower()
    picked = []
    for i in range(window.file_list.count()):
        item = window.file_list.item(i)
        n = i + 1
        if spec.get("mode") == "stride":
            every = max(1, int(spec["every"]))
            offset = int(spec["offset"])
            if n < offset or (n - offset) % every:
                continue
        elif not (int(spec["start"]) <= n <= int(spec["stop"])):
            continue
        if text and text not in item.text().lower():
            continue
        picked.append(item)
    return picked

def apply_selection(window: QMainWindow, spec: dict) -> list:
    """执行一次"按条件选"：勾上命中的条目，返回它们。

    默认"只选这些"——没命中的一律取消对号；spec["append"]=True 则只加
    不减（跨几个区间攒一批用）。整批改完只记一行日志。
    """
    picked = _selection_matches(window, spec)
    hit = {id(it) for it in picked}
    append = bool(spec.get("append"))
    wanted = []
    for i in range(window.file_list.count()):
        item = window.file_list.item(i)
        wanted.append((item, id(item) in hit
                       or (append and item.checkState() == Qt.Checked)))
    _set_checks(window, wanted)
    if spec.get("mode") == "stride":
        what = (f"每 {int(spec['every'])} 个选 1"
                f"（从第 {int(spec['offset'])} 个起）")
    else:
        what = f"第 {int(spec['start'])}–{int(spec['stop'])} 个"
    text = (spec.get("text") or "").strip()
    if text:
        what += f" 且名称包含“{text}”"
    if picked:
        _log(window, f"按条件勾选 {len(picked)} 项（{what}"
                     + ("，追加到已有对号）" if append else "）"))
    else:
        _log(window, f"没有命中任何文件（{what}）")
    return picked

def _selection_dialog_spec(window: QMainWindow, total: int):
    """「按条件选…」弹窗：区间 / 间隔 + 名称筛选 + 追加开关，带实时预览。

    返回 spec（结构见 _selection_matches / apply_selection）或 None
    （取消）。窗口没显示（测试环境）不弹模态框、直接返回 None——与
    _ask_duplicate 同理防挂死；要测选择逻辑直接调 apply_selection。
    """
    if not window.isVisible():
        return None
    dlg = QDialog(window)
    dlg.setWindowTitle("按条件选择文件")
    form = QFormLayout(dlg)

    rb_range = QRadioButton("区间：第")
    rb_range.setObjectName("sel_range")
    sp_start = QSpinBox()
    sp_start.setObjectName("sel_start")
    sp_start.setRange(1, total)
    sp_start.setValue(1)
    sp_stop = QSpinBox()
    sp_stop.setObjectName("sel_stop")
    sp_stop.setRange(1, total)
    sp_stop.setValue(total)
    row_range = QHBoxLayout()
    row_range.addWidget(rb_range)
    row_range.addWidget(sp_start)
    row_range.addWidget(QLabel("到第"))
    row_range.addWidget(sp_stop)
    row_range.addWidget(QLabel("个"))
    row_range.addStretch(1)
    box_range = QWidget()
    box_range.setLayout(row_range)
    form.addRow(box_range)

    rb_stride = QRadioButton("间隔：每")
    rb_stride.setObjectName("sel_stride")
    sp_every = QSpinBox()
    sp_every.setObjectName("sel_every")
    sp_every.setRange(1, total)
    sp_every.setValue(2)
    sp_off = QSpinBox()
    sp_off.setObjectName("sel_offset")
    sp_off.setRange(1, total)
    sp_off.setValue(1)
    row_stride = QHBoxLayout()
    row_stride.addWidget(rb_stride)
    row_stride.addWidget(sp_every)
    row_stride.addWidget(QLabel("个选 1 个，从第"))
    row_stride.addWidget(sp_off)
    row_stride.addWidget(QLabel("个起"))
    row_stride.addStretch(1)
    box_stride = QWidget()
    box_stride.setLayout(row_stride)
    form.addRow(box_stride)

    # 互斥靠 QButtonGroup（两个单选钮各套了一层容器，不是兄弟控件，
    # Qt 默认的"同父互斥"不成立；组挂在弹窗上随它销毁）
    group = QButtonGroup(dlg)
    group.addButton(rb_range)
    group.addButton(rb_stride)
    rb_range.setChecked(True)

    edit_text = QLineEdit()
    edit_text.setObjectName("sel_text")
    edit_text.setPlaceholderText("留空则不筛（不区分大小写）")
    form.addRow("名称包含", edit_text)
    chk_append = QCheckBox("追加到当前选择（不取消已勾的）")
    chk_append.setObjectName("sel_append")
    form.addRow(chk_append)
    preview = QLabel()
    preview.setObjectName("sel_preview")
    form.addRow(preview)

    def current_spec():
        return {"mode": "range" if rb_range.isChecked() else "stride",
                "start": sp_start.value(), "stop": sp_stop.value(),
                "every": sp_every.value(), "offset": sp_off.value(),
                "text": edit_text.text(),
                "append": chk_append.isChecked()}

    def refresh_preview(*_):
        n = len(_selection_matches(window, current_spec()))
        preview.setText(f"预览：将勾选 {n} 项（共 {total} 项）")

    for radio in (rb_range, rb_stride):
        radio.toggled.connect(refresh_preview)
    for spin in (sp_start, sp_stop, sp_every, sp_off):
        spin.valueChanged.connect(refresh_preview)
    edit_text.textChanged.connect(refresh_preview)
    refresh_preview()

    ok = QPushButton("确定")
    cancel = QPushButton("取消")
    row_btn = QHBoxLayout()
    row_btn.addWidget(ok)
    row_btn.addWidget(cancel)
    form.addRow(row_btn)
    ok.clicked.connect(dlg.accept)
    cancel.clicked.connect(dlg.reject)
    if dlg.exec() != QDialog.Accepted:
        return None
    return current_spec()

