"""文件栏分组：产物分组、打开的图、组态徽标、1D 组命名。

2026-10-21 从 file_dock.py 原样搬出（只改结构，不动逻辑）。
file_dock 再导出，tests/scripts 照旧经 gui_file_dock 取用。
"""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui.files.model import (
    GROUP_BATCH_ROLE, GROUP_MASKED2D_ROLE, PANEL_KEY_ROLE, FileItem,
    _group_title, _make_group, _refresh_group_badges, _set_group_title,
    is_group)
from xrd_toolkit.gui.files.selection import (
    _refresh_file_label, _sync_current_to_checks, _sync_group_states,
    _sync_select_label)
from xrd_toolkit.gui.files.sorting import _set_current_keeping_scroll
# 打开的图（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.files.open_figures import (
    OPEN_FIGURES_TITLE, _OPEN_ROW_BG, _open_dot, _open_dot_icon,
    _sync_open_figures, _top_groups, refresh_open_marks)
from xrd_toolkit.gui.panel_state import _collect_geometry, _log
from xrd_toolkit.services import masked2d, stage_cache

def _walk_items(tree):
    """文件栏全部条目（含组内叶子）；映射产物路径时不能只看顶层。"""
    stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    while stack:
        item = stack.pop()
        yield item
        stack.extend(item.child(i) for i in range(item.childCount()))


def _oned_sig(meta, lo, hi) -> tuple:
    """一套积分设置的指纹 = 1D 产物分组的身份。

    取的就是 1D 缓存键里那些**人看得见**的因子（几何条目 / 点数 / 2θ 范围）
    加上积分实现版本——同指纹的产物是"同一套设置下算出来的同一批"，正是
    用户 2026-09-28 第 4 条要的那种分组（"同一批原始数据出两组 theta 不同的
    1d 图会掺在一起"）。文件指纹不在其中：它是"哪张图"，不是"哪套设置"。
    """
    return (str(meta.get("config") or ""), int(meta.get("npt") or 0),
            round(float(lo), 6), round(float(hi), 6),
            int(meta.get("engine") or 0))

def _oned_sig_is_current(sig, cur) -> bool:
    """这套设置是不是坞顶**当前**那一套（组名标「当前设置」+ 排最前）。"""
    return (sig[0] == str(cur["config"]) and sig[1] == int(cur["npt"])
            and abs(sig[2] - cur["tth_min"]) <= 0.01
            and abs(sig[3] - cur["tth_max"]) <= 0.01)

def _fmt_deg(x) -> str:
    """2θ 端点给人看的写法：「1.001」「7.999」「12.5」（三位小数、去尾零）。

    为什么要它：范围是从**曲线端点**读的（元数据里老产物没记范围，曲线永远
    知道自己在哪儿），直接 `:g` 会打出「1.00117–7.99883°」这种全精度噪声
    ——真数据探针里一眼就看见它了。三位小数足够区分实际会出现的范围差；
    万一两组因此重名，文件栏那边还有"· 第 N 组"尾注兜底。
    """
    return f"{float(x):.3f}".rstrip("0").rstrip(".")

def _oned_group_title(sig, cur) -> str:
    """1D 产物组的组名：把"这一组是按什么算的"写在名字里。

    例子：「1D 产物 2θ 1.001–7.999°（当前设置）」「1D 产物 2θ 3–12° ·
    1000 点 · 几何 lmfp2_lab6」。与当前设置一致的部分不重复写（省宽度），
    不一致的才补出来——一眼就能分清哪一组是刚才那两下算出来的。

    "1D 产物"这个家族名留在最前面（徽标、右键菜单、日志都按它认这一族）。
    """
    config, npt_, lo, hi, engine = sig
    bits = [f"2θ {_fmt_deg(lo)}–{_fmt_deg(hi)}°"]
    if npt_ != int(cur["npt"]):
        bits.append(f"{npt_} 点")
    if config and config != str(cur["config"]):
        bits.append(f"几何 {config}")
    if engine != stage_cache.INTEGRATION_VERSION:
        bits.append("旧积分版本")
    title = "1D 产物 " + " · ".join(bits)
    if _oned_sig_is_current(sig, cur):
        title += "（当前设置）"
    return title

def _oned_group_tip(sig, cur) -> str:
    """1D 产物组的悬停提示：这一组是什么、与当前设置差在哪儿。"""
    config, npt_, lo, hi, _engine = sig
    txt = (f"按这套设置算好的 1D 产物：几何 {config or '—'}、{npt_} 点、"
           f"2θ {_fmt_deg(lo)}–{_fmt_deg(hi)}°。\n整组勾上可去 [对比] / [热图]；"
           "点开读的就是这一份，不重算。")
    if not _oned_sig_is_current(sig, cur):
        txt += (f"\n（当前设置：几何 {cur['config']}、{cur['npt']} 点、"
                f"2θ {_fmt_deg(cur['tth_min'])}–{_fmt_deg(cur['tth_max'])}°"
                f"——要按当前设置再算一批，勾上文件点 [1D]）")
    return txt

def _group_key(item, tree):
    """组节点的稳定身份（重建前后认人用）。

    重建前后组节点是**新对象**，只能靠数据槽认人：处理组用台账批次号
    （GROUP_BATCH_ROLE，右键"删除这一组"用的也是它）；原始数据组与 1D
    产物组没有批次号，各给一个固定名——**不能都退回同一个名字**，否则
    "收起原始数据"会被误判成"收起 1D 产物"，把好端端展开着的组收起来。
    """
    if item is tree.raw_group:
        return ("raw",)
    if item.data(0, GROUP_MASKED2D_ROLE):
        return ("masked2d",)
    batch = item.data(0, GROUP_BATCH_ROLE)
    return ("bg", str(batch)) if batch else ("1d",)

def _tree_state(window: QMainWindow) -> dict:
    """重建前记下"用户在哪儿"：滚动位置 / 收起的组 / 勾着的产物条目。

    为什么非要记：`refresh_product_groups` 是"clear_groups → 重新 add →
    expandAll"三步，这三样全都会丢，列表被弹回最顶端——用户 2026-09-27：
    "文件里面选取文字，或者不选时，只要是操作，包括画图现在会自动跳到最
    顶端"。产物条目的勾选也一起丢（重建的叶子硬编码成不勾），而勾选正是
    "拿哪几条去 [对比]/[热图]"的表达，中途按一次 [1D] 就没了。

    原始数据组不参与重建（clear_groups 不碰它），所以它的勾选天然还在，
    这里只管产物条目。
    """
    tree = window.file_list
    out = {
        "scroll": tree.verticalScrollBar().value(),
        "collapsed": {_group_key(it, tree) for it in _top_groups(tree)
                      if not it.isExpanded()},
        "checked": {(s.kind, s.key) for s in gui_sources.all_sources(window)
                    if s.kind != gui_sources.RAW
                    and s.item.checkState(0) == Qt.Checked},
        "current": None,
    }
    cur = tree.currentItem()
    if cur is not None and not is_group(cur):
        src = gui_sources.source_of(cur)
        out["current"] = (src.kind, src.key if src.kind != gui_sources.RAW
                          else src.path)
    return out

def _restore_tree_state(window: QMainWindow, keep: dict,
                        restore_checks: bool = True) -> None:
    """把 `_tree_state` 记下的东西放回去（重建之后调）。

    顺序有讲究：先恢复展开/勾选（都在信号屏蔽期间做，免得每条都触发一次
    联动 + 一行日志），再恢复当前项与滚动位置——`setCurrentItem` 自己会
    滚动，所以它必须在最后。

    `restore_checks=False`：**只不还勾选**，滚动/收起/当前项照还。
    什么时候要这样：重建时冒出了新产物 → 上一轮的勾选已经按规矩清掉了
    （见 _uncheck_everything），不能又叫 `keep` 把它们放回来。用户在
    哪儿那几样照旧要还——那是 2026-09-27"一操作就跳回顶端"投诉的另一半。
    """
    tree = window.file_list
    for node in _top_groups(tree):
        if _group_key(node, tree) in keep["collapsed"]:
            node.setExpanded(False)
    if restore_checks and keep["checked"]:
        for src in gui_sources.all_sources(window):
            if src.kind == gui_sources.RAW:
                continue        # 原始数据组的勾选从来没被动过
            if (src.kind, src.key) in keep["checked"]:
                src.item.setCheckState(0, Qt.Checked)
        _sync_group_states(window)   # 组态跟着子项走（屏蔽信号时不会自动跑）
    # 先放滚动位置，再放高亮：放高亮那一步自己会保滚动（见 _set_current_*）
    tree.verticalScrollBar().setValue(keep["scroll"])
    if keep["current"] is not None:
        for src in gui_sources.all_sources(window):
            same = ((src.kind, src.key) if src.kind != gui_sources.RAW
                    else (src.kind, src.path))
            if same == keep["current"]:
                _set_current_keeping_scroll(tree, src.item)
                break
    if not restore_checks:
        # 勾选刚被清过：当前项不许停在没勾的行上（同 _sync_current_to_checks）
        _sync_current_to_checks(window)

def _product_keys(window: QMainWindow) -> set:
    """文件栏里全部产物条目的身份 `(kind, key)`（原始数据不算）。"""
    return {(s.kind, s.key) for s in gui_sources.all_sources(window)
            if s.kind != gui_sources.RAW}

def _product_keys_on_disk() -> set:
    """**盘上**现有的全部产物身份 `(kind, key)`——不管它在不在文件栏里。

    判"冒出新产物"要拿它当地基，不能拿"文件栏里现在有什么"：导入一个
    以前算过的文件时，它的产物本来就在盘上、只是**刚被列出来**，那不是
    新产物——拿文件栏当地基会把这种情况误判成新产物，把用户导入后就勾好
    的那批对号当场清掉（2026-10-01 探针抓到：点 [1D] 只等到"没有选中的
    文件"）。

    单次"扫一遍"也不够：产物是**先落盘、后重建**的（算完 → store_1d →
    refresh），所以"重建时现扫"必然把刚写的键也算进去。真正的基线是
    **上一次重建时看到的盘面**（见 refresh_product_groups 里的
    `_products_seen`）。
    """
    keys = {("1d", key)
            for key, _meta, _lo, _hi in stage_cache.list_products("1d")}
    for node in stage_cache.list_batches("bg"):
        for _path, meta in node["items"].items():
            # 台账里"产物已经没了"的条目也在这里：它照样占着这把键，
            # 免得到时候"重新出现"被当成新产物（prune 在下面照常做）
            keys.add(("bg", meta.get("key")))
    return keys

def _uncheck_everything(window: QMainWindow) -> int:
    """清掉全树的勾（原始数据组 + 各产物组），返回清了几条。

    用户 2026-10-01 第 8 条（方案"甲"）：**文件栏出现新产物 = 上一轮的活
    干完了**，对号该清零重来，而不是继续挂着冒充"这一轮的选择"。

    调用方负责信号屏蔽（重建期间本来就在屏蔽中，见 refresh_product_groups）
    ——这里只改状态，标签/按钮文字由调用方收尾时统一刷。
    """
    changed = 0
    for src in gui_sources.all_sources(window):
        if src.item.checkState(0) != Qt.Unchecked:
            src.item.setCheckState(0, Qt.Unchecked)
            changed += 1
    _sync_group_states(window)   # 组态跟着子项走（屏蔽信号时不会自动跑）
    return changed

def refresh_product_groups(window: QMainWindow, quiet: bool = False) -> None:
    """重建文件栏里的产物分组：各组「1D 产物 …」+ 各组「处理后 …」。

    quiet=True（单张 1D 算完的即时刷新）：产物**照常列出来**，但不走
    "新产物 = 勾选清零"那一步、也不写"出现新产物"那行日志——清零是给
    "一批活干完"（批量出图/批量处理）定的规矩；单张看一眼就把用户刚勾的
    文件清掉，等于顺手废掉"再点一次 [1D] 看缓存"这条路（2026-10-05：
    单张即时刷新上线时撞上，四条测试同时变红才发现的）。

    数据来源两处，各有各的道理：
      - 1D 产物**扫盘 + 按积分设置分组**：磁盘上有什么就显示什么（不许
        因为改了设置就凭空消失），同指纹的归一组、组名写着是哪一套设置；
      - 处理产物**读台账**：产物键里含设置哈希，反查不出来，只能靠
        [批量处理] 当时记的那一笔（见 services/stage_cache 的台账一节）。

    只在"有事发生"时调（导入/删除、批量处理、清空缓存）：每次要给列表
    里每个文件算一次指纹（读 64 KiB），200 个文件 ≈ 100 ms，定时刷新是
    白烧 CPU。台账里"产物已经没了"的条目顺手落盘清掉。

    **重建不许动用户在哪儿**：滚动位置、收起的组、勾着的产物条目、当前
    高亮，重建前后必须一样（见 _tree_state / _restore_tree_state）。
    """
    tree = window.file_list
    keep = _tree_state(window)     # 重建不许改"用户在哪儿"（见其 docstring）
    # "冒出新产物"的基线 = **上一次重建时看到的盘面**（不是这次的盘面：
    # 产物先落盘、后重建，现扫必然把刚写的也算进去；也不是文件栏里已有的：
    # 那会把"导入以前算过的文件"误判成新产物。见 _product_keys_on_disk）
    now_on_disk = _product_keys_on_disk()
    seen_before = getattr(window, "_products_seen", None)
    newborn = set()
    if seen_before is not None:
        newborn = now_on_disk - seen_before
    window._products_seen = now_on_disk
    if quiet:
        # 单张即时刷新：产物照常列出来，但不算"冒出新产物"（见 docstring）
        newborn = set()

    def add_leaf(group, raw_item, kind, key, tail):
        """往组里加一条产物条目（默认不勾）。"""
        leaf = FileItem([f"{raw_item.text(0)} · {tail}"])
        leaf.setFlags(leaf.flags() | Qt.ItemIsUserCheckable)
        leaf.setCheckState(0, Qt.Unchecked)
        gui_sources.set_item_source(leaf, raw_item.data(0, Qt.UserRole),
                                    kind, key)
        leaf.setToolTip(0, f"{raw_item.toolTip(0)}\n{_group_title(group)}")
        group.addChild(leaf)
        return leaf

    # 重建期间屏蔽信号：新条目是 Unchecked，逐条会触发联动/刷新（每个
    # 条目都要重算一次组态 + 刷状态行，200 个就是 200 次白跑）
    tree.blockSignals(True)
    try:
        tree.clear_groups()
        _set_group_title(tree.raw_group, "原始数据")
        by_path = {}
        for it in _walk_items(tree):
            raw = it.data(0, Qt.UserRole)
            if not raw:
                continue
            by_path[str(Path(raw).resolve())] = it

        # ① Mask 后的 2D：manifest（原图 + mask）当**源数据**条目
        #    （2026-10-28）。它们和原始数据一样能出 2D/1D；1D 产物会按
        #    manifest 路径挂回这一条（见下面 by_path_name）。
        masked_rows = []
        for meta in masked2d.list_products():
            raw_item = by_path.get(
                str(Path(meta.get("raw_path") or "").resolve()))
            masked_rows.append((raw_item, meta))
        if masked_rows:
            order = {id(it): i for i, it in enumerate(_walk_items(tree))}
            masked_rows.sort(key=lambda kv: order.get(id(kv[0]), 10 ** 9))
            mgroup = _make_group(
                "Mask 后的 2D",
                "Mask 后的 2D 数据（原始像素 + mask manifest）。"
                "双击看 2D；右键可开 1D。")
            mgroup.setData(0, GROUP_MASKED2D_ROLE, True)
            for raw_item, meta in masked_rows:
                label = str(meta.get("label") or "").strip()
                tag = f"（{label}）" if label else ""
                raw_name = Path(meta.get("raw_path") or "").name
                base_text = (raw_item.text(0) if raw_item is not None
                             else f"{raw_name}（不在文件栏）")
                leaf = FileItem([f"{base_text} · Mask2D{tag}"])
                leaf.setFlags(leaf.flags() | Qt.ItemIsUserCheckable)
                leaf.setCheckState(0, Qt.Unchecked)
                gui_sources.set_item_source(
                    leaf, meta.get("path"), gui_sources.MASKED2D,
                    meta.get("key"))
                px = int(meta.get("masked_pixels") or 0)
                tot = int(meta.get("total_pixels") or 0)
                pct = 100.0 * px / tot if tot else 0.0
                tip = (raw_item.toolTip(0) if raw_item is not None else "")
                leaf.setToolTip(
                    0, f"{tip}\nMask 后的 2D：{px} px"
                       f"（{pct:.2f}%），原始图 {meta.get('raw_path')}")
                mgroup.addChild(leaf)
                by_path[str(Path(meta["path"]).resolve())] = leaf
            tree.addTopLevelItem(mgroup)

        # ① 1D 产物：**扫盘**列出（磁盘上有什么就显示什么），**按积分设置分组**
        #
        # 旧版按"当前设置"正查键，于是**改了 2θ 范围之后那一组会整个空掉**
        # （键里含范围）——用户 2026-09-28 报的"处理后的把 1D 的替换了"
        # 就是这个：其实一份都没少，只是不显示。规矩定成"缓存里有什么就
        # 显示什么，删不删由我自己决定"，与设置不一致的**在名字里标出来**。
        #
        # **分组 = 一套积分设置**（2026-09-28 用户第 4 条："同一批原始数据出
        # 两组 theta 不同的 1d 图会掺在一起，应该有分组区分"）：改之前全库
        # 只有一个"1D 产物"组，靠行尾 `1D（2θ 1–8°）` 区分，而且那截尾巴是
        # **相对当前设置**算的——把坞顶的 2θ 一改，尾巴会从 A 行跳到 B 行，
        # 看着像两组数据换了身份。现在与处理产物（一批一组）同一个形状：
        # 一组一套设置，组名就写着是哪一套。
        geom = _collect_geometry(window)
        npt = int(window.params["输出点数"].value())
        cur = {"tth_min": float(geom.get("tth_min_deg") or 0.0),
               "tth_max": float(geom.get("tth_max_deg") or 0.0),
               "config": window.config_name, "npt": npt}
        # 认人：**全路径优先**（元数据里有 path），老产物只有文件名 →
        # 退回按名字；两个目录里同名的文件因此不会挂错（2026-09-28 踩到）
        by_path_name, by_name = {}, {}
        for it in _walk_items(tree):
            raw = it.data(0, Qt.UserRole)
            if not raw:
                continue
            p = Path(raw)
            by_path_name.setdefault(str(p), it)
            by_name.setdefault(p.name, it)
        groups_1d = {}          # 设置指纹 → [(条目, 键, 元数据, lo, hi), ...]
        for key, meta, lo, hi in stage_cache.list_products("1d"):
            it = by_path_name.get(str(meta.get("path") or ""))
            if it is None:
                it = by_name.get(str(meta.get("source") or ""))
            if it is None:
                continue        # 不在当前文件栏里 → 不显示
            groups_1d.setdefault(_oned_sig(meta, lo, hi), []).append(
                (it, key, meta, lo, hi))
        # not 不能省：False 排在 True 前，取反后「当前设置」那一组才是第一
        # （2026-10-08 修——原键漏了 not，实际排在最后，与本文件 1322/1343
        # 与 NOTES「当前设置那一组排最前」相反；当时没有测试钉住顺序）
        for sig in sorted(groups_1d, key=lambda s: (not _oned_sig_is_current(s, cur),
                                                    s[2], s[3])):
            rows = groups_1d[sig]
            # 组内按**文件栏的行序**排（同一文件的两份产物都在时也能对上眼）
            order = {id(tree.item(i)): i for i in range(tree.count())}
            rows.sort(key=lambda r: order.get(id(r[0]), 10 ** 9))
            group = _make_group(_oned_group_title(sig, cur),
                                _oned_group_tip(sig, cur))
            for it, key, meta, lo, hi in rows:
                add_leaf(group, it, gui_sources.ONED, key, "1D")
            tree.addTopLevelItem(group)

        # ② 处理：一次 [批量处理] = 一组（台账，新的在上）
        # 组内行序 = **文件栏行序**（2026-10-08 用户拍板：与 1D 产物组同
        # 口径；原先按路径字符串排，文件栏一排序就与其它组对不上）
        row_of = {id(tree.item(i)): i for i in range(tree.count())}
        raw_batches = stage_cache.list_batches("bg")
        batches = stage_cache.list_batches("bg", prune=True)
        if sum(len(n["items"]) for n in raw_batches) != \
                sum(len(n["items"]) for n in batches):
            stage_cache.write_batches("bg", batches)   # 产物没了的条目落盘清掉
        stale = 0
        seen_labels = {}      # 组名 → 已出现几次（重名时加尾注，见下）
        for node in batches:
            kids = []
            for path, meta in sorted(
                    node["items"].items(),
                    key=lambda kv: row_of.get(
                        id(by_path.get(str(Path(kv[0]).resolve()))), 10 ** 9)):
                raw_item = by_path.get(str(Path(path).resolve()))
                if raw_item is None or not stage_cache.has_key(
                        "bg", meta.get("key")):
                    continue    # 不在当前文件栏里 / 产物没了 → 不显示
                # 旧背景算法算的那批**照样列出来**，只在名字里标"不可信"
                # ——用户 2026-09-28："缓存里有什么就显示什么，删不删由我
                # 自己决定"。上一版是直接不显示，与这条规矩冲突（他刚被
                # "东西怎么会不见"咬过一次）。
                is_stale = stage_cache.bg_product_stale(meta.get("key"))
                if is_stale:
                    stale += 1
                kids.append((raw_item, meta, is_stale))
            if not kids:
                continue
            # 2θ 范围可能没记（老台账、或批处理时没设范围）→ 别让
            # f-string 拿 None 去格式化：那会**在重建时抛异常**，整个文件栏
            # 建不起来（测试用最小台账记一批就复现了）
            span = ("2θ 范围未记录"
                    if node.get("tth_min") is None
                    or node.get("tth_max") is None
                    else f"2θ {_fmt_deg(node['tth_min'])}–"
                         f"{_fmt_deg(node['tth_max'])}°")
            # 台账那套设置与**当前**设置不同时，把当前值也写进悬停提示
            # （跟 1D 产物一样的道理：一眼看得出这批是按什么算的）
            now = f"2θ {_fmt_deg(cur['tth_min'])}–{_fmt_deg(cur['tth_max'])}°"
            differs = (node.get("config") != cur["config"]
                       or (node.get("tth_min") is not None
                           and abs(float(node["tth_min"]) - cur["tth_min"]) > 0.01)
                       or (node.get("tth_max") is not None
                           and abs(float(node["tth_max"]) - cur["tth_max"]) > 0.01))
            more = f"\n（当前设置：几何 {cur['config']}、{cur['npt']} 点、{now}）" \
                if differs else ""
            # 组名重了要能分开（2026-09-28 探针逮到）：批标签只到"分钟"，
            # 同一分钟里跑了两批**可见参数相同、锚点强度不同**的（哈希因此
            # 不同 = 两批），名字就一模一样——两个长得一样的组里各有一条同一
            # 文件的条目，删了哪一个都看不出来。重名的第二组起加尾注。
            label = str(node["label"])
            # 旧缓存里存的是「处理后 …」（2026-10-02 改名前的批次）：
            # 显示时归一到现名——同一批数据的组名不该新旧两套并存
            if label.startswith("处理后"):
                label = "处理产物" + label[len("处理后"):]
            seen_labels[label] = seen_labels.get(label, 0) + 1
            if seen_labels[label] > 1:
                label = f"{label} · 第 {seen_labels[label]} 组"
            group = _make_group(
                label,
                f"几何 {node.get('config')}、{node.get('npt')} 点、{span}\n"
                "整组勾上可去 [对比] / [热图]；右键删掉这一组。" + more)
            group.setData(0, GROUP_BATCH_ROLE, node["id"])   # 右键删这一组用
            for raw_item, meta, is_stale in kids:
                tail = "处理产物（旧算法，不可信）" if is_stale else "处理产物"
                leaf = add_leaf(group, raw_item, gui_sources.BG,
                                meta.get("key"), tail)
                if is_stale and leaf is not None:
                    leaf.setToolTip(
                        0, f"{leaf.toolTip(0)}\n这一份是按<b>旧的背景算法</b>"
                           "算的（曲线不可信）；要清掉就右键删这一条/这一组，"
                           "或[删除所有缓存…]")
            tree.addTopLevelItem(group)
        tree.expandAll()
        if stale:
            # 列出来、但标"不可信"：产物文件是用户的数据，删不删他说了算
            _log(window, f"文件栏里有 {stale} 条旧背景算法算的处理产物"
                         "（名字里标着“旧算法，不可信”）：扣背景的算法已"
                         "升级，那批曲线不再可信——要清掉就右键删那一组，"
                         "或[删除所有缓存…]，然后重新批量处理")
        # 冒出新产物（基线见上面那段）= 上一轮的工作结束了：勾选
        # （出图/批量处理的输入）清零重来（用户 2026-10-01 第 8 条，
        # 方案"甲"）。清掉之后不能再叫 keep 把旧对号放回来——滚动/
        # 收起/当前项照旧要还（那是 2026-09-27 那半条）。
        if newborn:
            _uncheck_everything(window)
        # 恢复展开/勾选/当前项/滚动位置：在信号屏蔽期间做（否则每个被恢复
        # 勾选的条目都要触发一次联动 + 一行日志，200 个条目就是 200 次）
        _restore_tree_state(window, keep, restore_checks=not newborn)
    finally:
        tree.blockSignals(False)
    if newborn:
        # 勾选"自己没了"必须说清楚去哪了，否则就是又一个"东西怎么会不见"
        _log(window, f"文件栏出现 {len(newborn)} 条新产物：上一轮的勾选已"
                     "清空（勾选集是出图/批量处理的输入，请重新勾）")
    # 恢复出来的勾选集合可能和进来时不一样（有产物的条目没了）→ 按钮上的
    # 数字与状态行跟着刷新（只改文字，不碰树）
    _refresh_file_label(window)
    _sync_select_label(window)
    _refresh_group_badges(window)   # 重建出新组了：折叠着的那些要带上数字
    refresh_open_marks(window)      # 重建出来的是新对象：标记要重新打一遍

