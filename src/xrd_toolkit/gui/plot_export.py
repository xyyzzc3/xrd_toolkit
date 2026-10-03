"""导出：1D 数据（txt/chi + 全部数据总表）+ 图片（PNG/TIF，可选 DPI）。

从 app.py 与 plot_views.py 拆出来（纯搬迁）：这一块是"把算好的东西
写出去"——只读面板缓存（dock.last_tth / last_intensity）与处理设置
（背景扣除 / 平滑 / 裁剪），不改任何计算状态。数据源统一走
_checked_1d_results：勾选文件的 1D 曲线（没算过的会提示先出图），
处理链按需求叠加（导出原始还是处理后的由弹窗决定；设置开着但实际
没做成的照实写 Raw，见该函数）。

目录结构与文件格式（2026-10-02 用户定，规范见 docs/UI_COPY.zh-CN.md
「导出文件规范」）：单个数据集 = 光一个 txt；两个及以上 = 一个
`导出_时间戳_{内容标签}/` 文件夹（里面 全部数据.csv + txt/ 子目录；
标签 = 原始/1D产物/处理产物/混合）。写文件的
格式与写法全部在 services/export（唯一出处，CLI 共用）；本模块只管
交互（弹窗、勾选、网格不一致的三选）与目录布局。
"""
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QSpinBox,
    QVBoxLayout, QWidget)

from xrd_toolkit import paths
from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui.panel_state import (_content, _log, _proc_curve,
                                            _proc_settings)
from xrd_toolkit.services import export as data_export
from xrd_toolkit.services import process, stage_cache


def _ask_save_options(window: QMainWindow):
    """保存图片选项弹窗：分辨率 dpi + 格式（PNG/TIF）。

    返回 {"dpi": int, "fmt": "png"|"tif"} 或 None（取消 = 整个保存
    流程中止，不继续弹文件名框）。fmt 既是 currentData 也是扩展名，
    文件名框的过滤器与自动补后缀都从它来。默认 300 dpi：屏幕看 100
    dpi 够用，论文/报告印刷要求 300 起步，图大了再往上加。
    """
    dlg = QDialog(window)
    dlg.setWindowTitle("保存图片选项")
    lay = QFormLayout(dlg)
    dpi_spin = QSpinBox()
    dpi_spin.setObjectName("save_dpi_spin")
    dpi_spin.setRange(72, 1200)
    dpi_spin.setValue(300)
    lay.addRow("分辨率 (dpi)", dpi_spin)
    fmt_combo = QComboBox()
    fmt_combo.setObjectName("save_fmt_combo")
    fmt_combo.addItem("PNG（通用，文件小）", "png")
    fmt_combo.addItem("TIF（无损，论文常用）", "tif")
    lay.addRow("格式", fmt_combo)
    btn_row = QWidget()
    btn_lay = QHBoxLayout(btn_row)
    btn_lay.setContentsMargins(0, 0, 0, 0)
    ok = QPushButton("确定")
    ok.setObjectName("save_opt_ok_btn")
    cancel = QPushButton("取消")
    ok.clicked.connect(dlg.accept)
    cancel.clicked.connect(dlg.reject)
    btn_lay.addWidget(ok)
    btn_lay.addWidget(cancel)
    lay.addRow("", btn_row)
    if dlg.exec() != QDialog.Accepted:
        return None
    return {"dpi": dpi_spin.value(), "fmt": fmt_combo.currentData()}


def _save_figures(window: QMainWindow) -> bool:
    """[保存] 按钮与关窗询问共用：弹窗勾选要保存的图 → 选分辨率/格式
    （_ask_save_options，整批共用一份）→ 逐个选文件名存图。

    返回 False = 流程被取消（关窗时应留在程序里），True = 完成。
    有画布（figure）的面板才参与；未接线视图的占位面板（若有）不参与。
    """
    panels = [d for d in window.plot_docks.values()
              if getattr(_content(d), "figure", None) is not None]
    if not panels:
        _log(window, "没有可保存的图")
        return True
    chosen = _choose_panels(window, panels)
    if chosen is None:
        _log(window, "已取消保存")
        return False
    if not chosen:
        _log(window, "未勾选任何要保存的图")
        return False
    options = _ask_save_options(window)
    if options is None:
        _log(window, "已取消保存")
        return False
    ext = options["fmt"]
    saved, skipped = 0, 0
    for dock in chosen:
        default = str(paths.OUTPUTS_DIR / f"{dock.windowTitle()}.{ext}")
        name, _ = QFileDialog.getSaveFileName(
            window, f"保存 {dock.windowTitle()}", default,
            f"{ext.upper()} 图片 (*.{ext})")
        if not name:
            skipped += 1   # 这张图用户没存：不算"保存完成"
            _log(window, f"已跳过保存 {dock.windowTitle()}")
            continue
        if not name.lower().endswith(f".{ext}"):
            name += f".{ext}"
        try:
            Path(name).parent.mkdir(parents=True, exist_ok=True)
            _content(dock).figure.savefig(name, dpi=options["dpi"])
        except OSError as err:
            _log(window, f"保存失败 {dock.windowTitle()} → {name}（{err}）")
            skipped += 1
            continue
        dock.figure_saved = True
        saved += 1
        _log(window, f"已保存 {dock.windowTitle()} → {name}"
                     f"（{options['dpi']} dpi）")
    if saved:
        _log(window, f"保存完成：{saved} 张图（{options['dpi']} dpi）")
    return skipped == 0


def _choose_panels(window: QMainWindow, panels) -> list:
    """弹窗勾选要保存的面板（默认全勾）；确定 = 勾选列表（可为空），
    取消 = None（与"确定但一张没勾"区分开）。"""
    dlg = QDialog(window)
    dlg.setWindowTitle("保存哪些图")
    lay = QVBoxLayout(dlg)
    lay.addWidget(QLabel("勾选要保存的图："))
    lst = QListWidget()
    for d in panels:
        it = QListWidgetItem(d.windowTitle())
        it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
        it.setCheckState(Qt.Checked)   # 默认全勾
        lst.addItem(it)
    lay.addWidget(lst)
    row = QHBoxLayout()
    ok = QPushButton("确定")
    cancel = QPushButton("取消")
    row.addWidget(ok)
    row.addWidget(cancel)
    lay.addLayout(row)
    ok.clicked.connect(dlg.accept)
    cancel.clicked.connect(dlg.reject)
    if dlg.exec() != QDialog.Accepted:
        return None
    return [panels[i] for i in range(lst.count())
            if lst.item(i).checkState() == Qt.Checked]


def _checked_1d_results(window: QMainWindow, want_bg: bool = False,
                        quiet: bool = False, sources=None) -> list:
    """收集勾选文件的 1D 积分结果，每条 6 元：

        (曲线名, tth, intensity, 处理链, 数据类别, 几何配置, )

    只认已经算好的 1D 面板缓存（last_tth / last_intensity），按文件
    列表顺序返回；勾选里没算过的文件跳过并记日志（提示先点 [1D]
    出图）。重复文件改名加入的条目按显示名找各自面板。

    want_bg=True 时跑整条处理链（背景 → 平滑 → 裁剪，锚点按文件路径取，
    与画图共用同一个 _proc_curve——导出与屏幕同一个口径）；全关时原样
    返回。第 4 项是链的一句话描述、第 5/6 项是文件头要写的类别与几何
    配置名——文件自己说清它是怎么来的（规范见 docs/UI_COPY.zh-CN.md）。
    判定"处理过没有"按链**实际做了的事**：真做了才叫"处理产物"、名字才带
    `_处理产物` 尾缀；没做成的（三项全关，或背景开着但没生效——「手动
    锚点」没点锚点、「空扫相减」没选空扫图）照实写 Raw + chain: none，
    并在日志里说一声——标成 Processed 是谎报。
    quiet=True 不记日志：导出要拿数量去填弹窗标题，之后再正式收一遍，
    两遍都记就会把"跳过 X"打两次。
    sources 给定一组来源（右键"导出这一条/这一组"那条路）时只处理这一组，
    不看勾选状态——右键导出不该悄悄改动用户的对号。
    """
    checked = (gui_sources.checked_sources(window) if sources is None
               else list(sources))
    if not checked:
        if not quiet:
            _log(window, "未勾选任何项")
        return []
    out = []
    for src in checked:
        path = str(src.path)
        display = src.display
        if src.kind != gui_sources.RAW:
            # 产物条目：直接导出那一份产物（已经是算好/扣好的曲线）
            got = gui_sources.load_product(src)
            if got is None:
                if not quiet:
                    _log(window, f"跳过 {display}：产物读不到了（被删了？）")
                continue
            # 名字带阶段后缀：同一张图的原始结果与处理结果各存一份，
            # 不重名、不互相覆盖（导出文件名 = 这个名字）
            tail = gui_sources.KIND_TAIL.get(src.kind, src.kind)
            meta = stage_cache.meta_by_key(src.kind, src.key)
            category = (data_export.CATEGORY_ONED
                        if src.kind == gui_sources.ONED
                        else data_export.CATEGORY_PROCESSED)
            out.append((f"{Path(path).stem}_{tail}", got[0], got[1],
                        meta.get("chain", ""), category, meta.get("config")))
            continue
        for key in (f"1D|{path}", f"1D|{path}|{display}"):
            dock = window.plot_docks.get(key)
            if dock is not None and getattr(dock, "last_tth", None) is not None:
                tth, intensity = dock.last_tth, dock.last_intensity
                chain = ""
                category = data_export.CATEGORY_RAW
                stem = Path(path).stem
                # 几何配置名取这张图的参数快照（开图/重算时拍的），没有就省
                config = (getattr(dock, "params_snapshot", None) or {}).get(
                    "config")
                if want_bg:
                    settings = _proc_settings(window, dock, path)
                    mode = str(settings.get("mode") or "off")
                    tth, intensity, base = _proc_curve(window, dock, path, tth,
                                                       intensity)
                    # 判"真的处理了吗"看链**实际做了的事**，不看设置里写的
                    # 模式：「手动锚点」没点锚点、「空扫相减」没选空扫图时
                    # apply_chain 见 base=None 就原样返回——曲线一个点没动，
                    # 设置里模式却还开着（2026-10-03 实测：锚点 0 个时链描述
                    # 仍报 bg=anchor(n=0)）。拿设置当判据，没动过的数据会被
                    # 标成 Processed——谎报，用户会按那个名字当扣过的用。
                    # base=None = 背景那一步没发生 → 描述里按 off 写，
                    # 判定与文件头就都是照实的
                    bg_missing = base is None and mode != "off"
                    if base is None:
                        settings = {**settings, "mode": "off"}
                    chain = process.chain_desc(settings)
                    if not quiet and bg_missing:
                        why = {"anchor": "「手动锚点」模式但这条还没点锚点",
                               "blank": "「空扫相减」模式但还没选空扫图",
                               }.get(mode, "背景没有拟合出来")
                        _log(window, f"{display}：没扣背景（{why}），"
                                     + ("按原始值导出" if chain == "none"
                                        else "这条只做了平滑/裁剪"))
                    elif not quiet and chain == "none":
                        _log(window, f"{display}：三项都关着"
                                     f"（背景扣除/平滑/裁剪），按原始值导出")
                    if chain != "none":
                        # 链真的做了事才叫"处理产物"：上面的描述只写实际
                        # 发生的步骤，没做事就是 none——照实写 Raw + chain:
                        # none，名字也不加 _处理产物 尾缀
                        category = data_export.CATEGORY_PROCESSED
                        stem = (f"{stem}_"
                                f"{gui_sources.KIND_TAIL[gui_sources.BG]}")
                out.append((stem, tth, intensity, chain, category, config))
                break
        else:   # for-else：两个键都没命中 = 这个文件还没有 1D 结果
            if not quiet:
                _log(window, f"跳过 {display}：还没有 1D 曲线"
                             f"（先点 [1D] 出图）")
    if not out and not quiet:
        _log(window, "没有可导出的 1D 曲线")
    return out


def _build_export_dialog(window: QMainWindow, n_results: int):
    """导出设置弹窗：输出目录 + 后缀（.txt/.chi）+ 全部数据总表开关。

    输出目录默认是项目根 outputs 的**完整绝对路径**（2026-10-02 起；
    以前是字面的 "outputs"，跟着进程工作目录跑，会写进意想不到的地方
    ——用户机器上就这么攒出过第二个 outputs）。单个数据集只出曲线文件，
    总表复选框禁用并说明原因。

    返回 dict（"dir"=Path / "suffix" / "csv"）或 None（取消）。测试
    可以 mock 本函数直接给 dict，也可以 patch QDialog.exec 走真实
    控件。"""
    dlg = QDialog(window)
    dlg.setWindowTitle(f"导出 1D 数据（{n_results} 个文件）")
    lay = QFormLayout(dlg)
    dir_edit = QLineEdit(str(paths.OUTPUTS_DIR))
    dir_edit.setObjectName("export_dir_edit")
    browse = QPushButton("浏览…")
    browse.setObjectName("export_browse_btn")

    def pick_dir():
        folder = QFileDialog.getExistingDirectory(
            window, "选择输出目录", dir_edit.text())
        if folder:
            dir_edit.setText(folder)

    browse.clicked.connect(pick_dir)
    row = QWidget()
    row_lay = QHBoxLayout(row)
    row_lay.setContentsMargins(0, 0, 0, 0)
    row_lay.addWidget(dir_edit, 1)
    row_lay.addWidget(browse)
    lay.addRow("输出目录", row)
    suffix_combo = QComboBox()
    suffix_combo.setObjectName("export_suffix_combo")
    suffix_combo.addItem(".txt（两列文本）", ".txt")
    suffix_combo.addItem(".chi（与 txt 同格式）", ".chi")
    lay.addRow("文件后缀", suffix_combo)
    csv_check = QCheckBox(f"同时生成全部数据总表（{data_export.CSV_NAME}）")
    csv_check.setObjectName("export_csv_check")
    csv_check.setChecked(True)   # 默认顺手出一张大集合
    if n_results == 1:
        # 单个数据集就光一个曲线文件（用户 2026-10-02 定）；禁用而不是
        # 藏起来——看得见才知道"为什么没有总表"
        csv_check.setChecked(False)
        csv_check.setEnabled(False)
        csv_check.setToolTip("单个数据集只导出曲线文件（txt / chi）；"
                             "两个及以上才生成全部数据总表")
    lay.addRow("", csv_check)
    # 处理链的成果要能带走：默认关 = 导原始曲线（数据出口不该被显示
    # 参数悄悄改变——这是显示层的约定）。名字必须写全三项：链跑的是
    # 背景 + 平滑 + 裁剪，只写"扣背景"会让开着平滑的人把平滑过的文件
    # 当没处理过的原始分辨率用（2026-10-03 讨论定）
    bg_check = QCheckBox("导出处理后的曲线（背景/平滑/裁剪）")
    bg_check.setObjectName("export_bg_check")
    bg_check.setChecked(False)
    bg_check.setToolTip("按「处理」页当前设置（背景扣除/平滑/裁剪）导出"
                        "处理后的曲线；只作用于勾选的「原始数据」，"
                        "「1D 产物」「处理产物」条目原样导出。不勾选则"
                        "导出原始积分结果。扣完可能出现负值（噪声地板），"
                        "这是正常的")
    lay.addRow("", bg_check)
    btn_row = QWidget()
    btn_lay = QHBoxLayout(btn_row)
    btn_lay.setContentsMargins(0, 0, 0, 0)
    ok = QPushButton("导出")
    ok.setObjectName("export_ok_btn")
    cancel = QPushButton("取消")
    ok.clicked.connect(dlg.accept)
    cancel.clicked.connect(dlg.reject)
    btn_lay.addWidget(ok)
    btn_lay.addWidget(cancel)
    lay.addRow("", btn_row)
    if dlg.exec() != QDialog.Accepted:
        return None
    return {"dir": Path(dir_edit.text()), "suffix": suffix_combo.currentData(),
            "csv": csv_check.isChecked(), "bg": bg_check.isChecked()}


def _write_export(target: Path, tth, intensity, chain: str = "", *,
                  category: str = None, config: str = None,
                  now=None) -> int:
    """写一条两列 1D 曲线（窄口；格式与写法全在 services/export）。

    保留这个名字：GUI 内部、测试与探针都从这条口走。类别没给就按链
    推断（none → Raw，否则 Processed）——只传链的老调用方也能工作。
    裁剪过的点不写行（写出去就是字面 nan，别的软件读不了），头里用
    cut 行写明删了多少点（用户 2026-09-25 定的"都存文件"）。
    """
    if category is None:
        category = (data_export.CATEGORY_RAW
                    if not chain or chain == "none"
                    else data_export.CATEGORY_PROCESSED)
    return data_export.write_curve(target, tth, intensity, category=category,
                                   chain=chain or "none", config=config,
                                   now=now)


def _ask_csv_range(window: QMainWindow) -> str:
    """2θ 网格不一致时问用户 CSV 怎么出；返回 "intersect"/"skip"/"cancel"。

    窗口从未显示过（测试等非交互场景）不弹框，直接按"跳过范围不同
    的文件"处理，免得模态对话框把测试挂死；真实使用中窗口显示过，
    正常弹框。
    """
    if not window.isVisible():
        return "skip"
    box = QMessageBox(window)
    box.setWindowTitle("2θ 网格不一致")
    box.setText("这批文件的 2θ 网格不一致，全部数据总表怎么出？")
    b_common = box.addButton("取公共交集（重插值）", QMessageBox.AcceptRole)
    b_skip = box.addButton("跳过 2θ 网格不同的文件", QMessageBox.RejectRole)
    box.addButton("取消", QMessageBox.DestructiveRole)
    box.exec()
    clicked = box.clickedButton()
    if clicked is b_common:
        return "intersect"
    if clicked is b_skip:
        return "skip"
    return "cancel"


def _write_csv_summary(window: QMainWindow, results,
                       target_dir: Path) -> None:
    """把一批 1D 结果汇总成一张全部数据总表（第一列 2θ，其后每文件一列强度）。

    所有结果的 2θ 网格一致（同 npt 同范围）时直接按列拼；网格不一致时
    弹窗问用户（取公共交集重插值 / 跳过范围不同的文件 / 取消）。重插值
    = 公共区间内按最大点数均匀取样，原数据 np.interp 上去。

    写盘交给 services/export.write_csv（唯一格式出处）：每列一行来源
    明细、BOM、空单元格——裁剪过的点在表里**留空**，不写 0（0 会被
    当成真实强度参与后面的计算，空白才是"这里没有数据"的诚实表示）。
    """
    results = [data_export.normalize_row(r) for r in results]
    grids = [tth for _, tth, _, _, _, _ in results]
    ref = grids[0]
    same_grid = all(len(g) == len(ref) and np.allclose(g, ref, atol=1e-9)
                    for g in grids[1:])
    if not same_grid:
        choice = _ask_csv_range(window)
        if choice == "cancel":
            _log(window, "已取消全部数据总表")
            return
        if choice == "intersect":
            lo = max(g.min() for g in grids)
            hi = min(g.max() for g in grids)
            npt = max(len(g) for g in grids)
            common = np.linspace(lo, hi, npt)
            results = [(stem, common, np.interp(common, tth, intensity),
                        chain, category, config)
                       for stem, tth, intensity, chain, category, config
                       in results]
            _log(window, f"全部数据总表取公共交集 2θ {lo:.3f}–{hi:.3f}°"
                         f"（重插值到 {npt} 点）")
        else:   # "skip"：只保留与第一个文件同网格的
            kept = [r for r in results
                    if len(r[1]) == len(ref) and np.allclose(r[1], ref,
                                                             atol=1e-9)]
            if not kept:
                _log(window, "全部数据总表已取消：没有 2θ 网格一致的文件")
                return
            _log(window, f"全部数据总表跳过 {len(results) - len(kept)} 个"
                         f" 2θ 网格不同的文件")
            results = kept
    target = Path(target_dir) / data_export.CSV_NAME
    try:
        data_export.write_csv(target, results)
    except OSError as err:
        _log(window, f"全部数据总表写入失败（{err}）")
        return
    cut_cols = [stem for stem, _, intensity, _, _, _ in results
                if not np.isfinite(np.asarray(intensity, dtype=float)).all()]
    _log(window, f"已生成全部数据总表（{len(results)} 列） → {target}"
                 + (f"（{len(cut_cols)} 列有裁剪区，空单元格表示无数据）"
                    if cut_cols else ""))


def _run_export(window: QMainWindow, sources=None) -> None:
    """[导出数据]：勾选文件（或指定的一组来源）的 1D 结果批量写文件。

    目录结构（用户 2026-10-02 定，规范见 docs/UI_COPY.zh-CN.md）：
    单个数据集 = 光一个 txt（不包文件夹）；两个及以上 = 一个
    `导出_时间戳_{内容标签}/` 文件夹（里面 全部数据.csv + txt/ 子目录；
    标签 = 原始/1D产物/处理产物/混合，2026-10-03 加）。**任何一份已存在
    的曲线文件都不覆盖**——同名就顺延 _2 并把新名字记进日志（同一条
    曲线两套参数各导一次，两份都要在）。文件格式与 CLI 同源
    （services/export 唯一出处）；右键"导出这一条/这一组"走 sources
    那条路，同一套布局。单个文件写盘失败只记日志、不中断批处理；
    没算过 1D 的文件跳过并提示先点 [1D] 出图。
    """
    # 先数一遍（确定"扣不扣背景"要等弹窗，但弹窗标题要个数量）——这一遍
    # 静默：否则"跳过 X：还没有 1D 结果"会在下面第二遍里再打一次
    n = len(_checked_1d_results(window, quiet=True, sources=sources))
    if not n:
        # 让跳过/空结果的原因照常记进日志
        _checked_1d_results(window, sources=sources)
        return
    fields = _build_export_dialog(window, n)
    if fields is None:
        _log(window, "已取消导出")
        return
    want_bg = bool(fields.get("bg"))
    results = _checked_1d_results(window, want_bg=want_bg, sources=sources)
    if not results:
        return
    if want_bg:
        # 不报具体模式：处理是**按面板快照**算的（每张图各记各的），
        # 而此处读到的控件值只反映当前编辑对象
        _log(window, "导出：按各面板自己的处理设置（背景/平滑/裁剪）")
    outdir, suffix = fields["dir"], fields["suffix"]
    started = time.perf_counter()
    if len(results) == 1:
        # 单个数据集：光一个文件，不包文件夹
        txt_dir = target_dir = outdir
        landing = outdir / f"{results[0][0]}{suffix}"
    else:
        # 文件夹名带内容标签（原始 / 1D产物 / 处理产物 / 混合）：从外面
        # 一眼看出这袋子里装的是什么（用户 2026-10-03）
        target_dir = data_export.batch_dir(
            outdir, tag=data_export.category_tag(results))
        txt_dir = target_dir / data_export.TXT_DIR_NAME
        landing = target_dir
    _log(window, f"开始导出：{len(results)} 个文件 → {landing}")
    ok = dropped = 0
    for stem, tth, intensity, chain, category, config in results:
        target = data_export.unique_path(txt_dir / f"{stem}{suffix}")
        if target.name != f"{stem}{suffix}":
            # 不覆盖已有文件：同一条曲线用两套参数各导一次时，两份都要留下
            _log(window, f"{stem}{suffix} 已存在，这一份存为 {target.name}")
        if not np.isfinite(np.asarray(intensity, dtype=float)).all():
            dropped += 1
        try:
            _write_export(target, tth, intensity, chain, category=category,
                          config=config)
        except OSError as err:
            _log(window, f"导出失败 {stem}（{err}）")
            continue
        ok += 1
        _log(window, f"已导出 {stem} → {target}")
    if dropped:
        _log(window, f"提示：{dropped} 个文件的裁剪区间没有写进文件"
                     f"（文件头注明删了多少点），空值行不写入文件")
    if ok:
        _log(window, f"导出完成：{ok} 个文件，用时 "
                     f"{time.perf_counter() - started:.1f} 秒 → {landing}")
    # 单个数据集不出总表（用户 2026-10-02 定；弹窗里也禁用并说明了）
    if fields.get("csv") and len(results) > 1:
        _write_csv_summary(window, results, target_dir)
