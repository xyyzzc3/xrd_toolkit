"""导出：1D 数据（txt/chi + CSV 总表）+ 图片（PNG/TIF，可选 DPI）。

从 app.py 与 plot_views.py 拆出来（纯搬迁）：这一块是"把算好的东西
写出去"——只读面板缓存（dock.last_tth / last_intensity）与背景扣除
设置，不改任何计算状态。数据源统一走 _checked_1d_results：勾选文件
的 1D 曲线（没算过的会提示先出图），背景扣除按需求叠加（导出原始
还是扣过的由弹窗决定）。
"""
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QSpinBox,
    QVBoxLayout, QWidget)

from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui.panel_state import (_content, _log, _proc_curve,
                                            _proc_settings)
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
        _log(window, "没有已输出的图可保存")
        return True
    chosen = _choose_panels(window, panels)
    if chosen is None:
        _log(window, "已取消保存")
        return False
    if not chosen:
        _log(window, "没有勾选要保存的图")
        return False
    options = _ask_save_options(window)
    if options is None:
        _log(window, "已取消保存")
        return False
    ext = options["fmt"]
    saved, skipped = 0, 0
    for dock in chosen:
        default = str(Path("outputs") / f"{dock.windowTitle()}.{ext}")
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
    """收集勾选文件的 1D 积分结果：[(文件名, tth, intensity, 处理链), ...]。

    只认已经算好的 1D 面板缓存（last_tth / last_intensity），按文件
    列表顺序返回；勾选里没算过的文件跳过并记日志（提示先点 [1D]
    出图）。重复文件改名加入的条目按显示名找各自面板。

    want_bg=True 时跑整条处理链（背景 → 平滑 → 裁剪，锚点按文件路径取，
    与画图共用同一个 _proc_curve——导出与屏幕同一个口径）；全关时原样
    返回。第 4 项是链的一句话描述（空 = 没做处理），写进导出文件的头里
    ——文件自己说清它是怎么来的。
    quiet=True 不记日志：导出要拿数量去填弹窗标题，之后再正式收一遍，
    两遍都记就会把"跳过 X"打两次。
    sources 给定一组来源（右键"导出这一条/这一组"那条路）时只处理这一组，
    不看勾选状态——右键导出不该悄悄改动用户的对号。
    """
    checked = (gui_sources.checked_sources(window) if sources is None
               else list(sources))
    if not checked:
        if not quiet:
            _log(window, "没有选中的文件")
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
            out.append((f"{Path(path).stem}_{tail}", got[0], got[1],
                        stage_cache.meta_by_key(src.kind, src.key)
                        .get("chain", "")))
            continue
        for key in (f"1D|{path}", f"1D|{path}|{display}"):
            dock = window.plot_docks.get(key)
            if dock is not None and getattr(dock, "last_tth", None) is not None:
                tth, intensity = dock.last_tth, dock.last_intensity
                chain = ""
                if want_bg:
                    settings = _proc_settings(window, dock, path)
                    tth, intensity, _ = _proc_curve(window, dock, path, tth,
                                                    intensity)
                    chain = process.chain_desc(settings)
                out.append((Path(path).stem, tth, intensity, chain))
                break
        else:   # for-else：两个键都没命中 = 这个文件还没有 1D 结果
            if not quiet:
                _log(window, f"跳过 {display}：还没有 1D 结果"
                             f"（先点 [1D] 出图）")
    if not out and not quiet:
        _log(window, "没有可导出的 1D 结果")
    return out


def _build_export_dialog(window: QMainWindow, n_results: int):
    """导出设置弹窗：输出目录 + 后缀（.txt/.chi）+ CSV 总表开关。

    返回 dict（"dir"=Path / "suffix" / "csv"）或 None（取消）。测试
    可以 mock 本函数直接给 dict，也可以 patch QDialog.exec 走真实
    控件。"""
    dlg = QDialog(window)
    dlg.setWindowTitle(f"导出 1D 数据（{n_results} 个文件）")
    lay = QFormLayout(dlg)
    dir_edit = QLineEdit("outputs")
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
    csv_check = QCheckBox("同时生成 CSV 总表（1d_summary.csv）")
    csv_check.setObjectName("export_csv_check")
    csv_check.setChecked(True)   # 默认顺手出一张总表
    lay.addRow("", csv_check)
    # 扣背景的成果要能带走：默认关 = 导原始曲线（数据出口不该被显示
    # 参数悄悄改变——这是显示层的约定）
    bg_check = QCheckBox("导出扣除背景后的曲线")
    bg_check.setObjectName("export_bg_check")
    bg_check.setChecked(False)
    bg_check.setToolTip("按当前\"背景扣除\"设置（模式/窗口/锚点/空扫）"
                        "导出扣完背景的曲线；不勾 = 导出原始积分结果。"
                        "扣完可能出现负值（噪声地板），这是正常的")
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


def _write_export(target: Path, tth, intensity, chain: str = "") -> None:
    """写一个两列 1D 数据文件（头行与格式逐字镜像 CLI integrate_pattern）。

    **裁剪过的点不写行**：那些点在数据里是"空"（NaN），写出去就是字面
    "nan"，别的软件读不了；跳过它们并在头里写明处理链——文件自己说清它
    是怎么来的（用户 2026-09-25 定的"都存文件"）。
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tth = np.asarray(tth, dtype=float)
    intensity = np.asarray(intensity, dtype=float)
    keep = np.isfinite(intensity)
    header = "2theta(deg)  intensity"
    if chain:
        header += f"\nprocessed: {chain}"
    if not keep.all():
        header += f"\ncut: {int((~keep).sum())} points removed"
    np.savetxt(str(target), np.c_[tth[keep], intensity[keep]], fmt="%.6g",
               header=header)


def _ask_csv_range(window: QMainWindow) -> str:
    """2θ 网格不一致时问用户 CSV 怎么出；返回 "intersect"/"skip"/"cancel"。

    窗口从未显示过（测试等非交互场景）不弹框，直接按"跳过范围不同
    的文件"处理，免得模态对话框把测试挂死；真实使用中窗口显示过，
    正常弹框。
    """
    if not window.isVisible():
        return "skip"
    box = QMessageBox(window)
    box.setWindowTitle("2θ 范围不一致")
    box.setText("这批文件的 2θ 网格不一致，CSV 总表怎么出？")
    b_common = box.addButton("取公共交集（重插值）", QMessageBox.AcceptRole)
    b_skip = box.addButton("跳过范围不同的文件", QMessageBox.RejectRole)
    box.addButton("取消", QMessageBox.DestructiveRole)
    box.exec()
    clicked = box.clickedButton()
    if clicked is b_common:
        return "intersect"
    if clicked is b_skip:
        return "skip"
    return "cancel"


def _as4(row) -> tuple:
    """结果行统一成 4 元组 (名, tth, 强度, 处理链)。

    第 4 项是可选备注（老调用方与测试可能只给三元组）——写盘的两个函数
    都从这里进，缺了就补空串。
    """
    return tuple(row) if len(row) == 4 else (*row, "")


def _write_csv_summary(window: QMainWindow, results, outdir: Path) -> None:
    """把一批 1D 结果汇总成一张 CSV：第一列 2θ，其后每文件一列强度。

    所有结果的 2θ 网格一致（同 npt 同范围）时直接按列拼；网格不一
    致时弹窗问用户（取公共交集重插值 / 跳过范围不同的文件 / 取消）。
    重插值 = 公共区间内按最大点数均匀取样，原数据 np.interp 上去。

    裁剪过的点是"空"：CSV 里那几格**留空**，不写 0——0 会被当成真实
    强度参与后面的计算，空白才是"这里没有数据"的诚实表示。为此整张表
    按字符串写（np.savetxt 的 %.6g 会把空值写成字面 nan）。
    """
    results = [_as4(r) for r in results]
    grids = [tth for _, tth, _, _ in results]
    ref = grids[0]
    same_grid = all(len(g) == len(ref) and np.allclose(g, ref, atol=1e-9)
                    for g in grids[1:])
    if not same_grid:
        choice = _ask_csv_range(window)
        if choice == "cancel":
            _log(window, "已取消 CSV 总表")
            return
        if choice == "intersect":
            lo = max(g.min() for g in grids)
            hi = min(g.max() for g in grids)
            npt = max(len(g) for g in grids)
            common = np.linspace(lo, hi, npt)
            results = [(stem, common, np.interp(common, tth, intensity),
                        chain)
                       for stem, tth, intensity, chain in results]
            _log(window, f"CSV 总表取公共交集 2θ {lo:.3f}~{hi:.3f}°"
                         f"（重插值到 {npt} 点）")
        else:   # "skip"：只保留与第一个文件同网格的
            kept = [r for r in results
                    if len(r[1]) == len(ref) and np.allclose(r[1], ref,
                                                             atol=1e-9)]
            if not kept:
                _log(window, "CSV 总表已取消：没有 2θ 网格一致的文件")
                return
            _log(window, f"CSV 总表跳过 {len(results) - len(kept)} 个"
                         f" 2θ 范围不同的文件")
            results = kept
    grid = np.asarray(results[0][1], dtype=float)
    columns = [np.asarray(intensity, dtype=float)
               for _, _, intensity, _ in results]

    def fmt(v):
        return "" if not np.isfinite(v) else f"{v:.6g}"

    rows = [",".join([fmt(x)] + [fmt(c[i]) for c in columns])
            for i, x in enumerate(grid)]
    header = "2theta(deg)," + ",".join(stem for stem, _, _, _ in results)
    cut_cols = [stem for stem, _, inten, _ in results
                if not np.isfinite(np.asarray(inten, dtype=float)).all()]
    if cut_cols:
        header += ("\n# 空单元格 = 该 2θ 段被裁剪（"
                   + "、".join(cut_cols) + "）")
    target = outdir / "1d_summary.csv"
    try:
        # comments=""：头行不带 # 前缀，读回时第一行就是列名
        target.write_text(header + "\n" + "\n".join(rows) + "\n",
                          encoding="utf-8")
    except OSError as err:
        _log(window, f"CSV 总表写入失败（{err}）")
        return
    _log(window, f"已生成 CSV 总表 → {target}"
                 + (f"（{len(cut_cols)} 列有裁剪区，空格 = 无数据）"
                    if cut_cols else ""))


def _run_export(window: QMainWindow, sources=None) -> None:
    """[导出数据]：勾选文件（或指定的一组来源）的 1D 结果批量落盘。

    镜像 CLI 的 txt 格式；右键"导出这一条/这一组"走 sources 那条路。

    输出路径 = {目录}/{文件名}/integrated_2th{suffix}（与命令行
    integrate_pattern 同目录同格式）；可选 CSV 总表。单个文件写盘
    失败只记日志、不中断批处理；没算过 1D 的文件跳过并提示先点
    [1D] 出图。
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
    ok = dropped = 0
    for stem, tth, intensity, chain in map(_as4, results):
        target = outdir / stem / f"integrated_2th{suffix}"
        if np.isfinite(np.asarray(intensity, dtype=float)).all() is False:
            dropped += 1
        try:
            _write_export(target, tth, intensity, chain)
        except OSError as err:
            _log(window, f"导出失败 {stem}（{err}）")
            continue
        ok += 1
        _log(window, f"已导出 {stem} → {target}")
    if dropped:
        _log(window, f"提示：{dropped} 个文件的裁剪区间没有写进文件"
                     f"（文件头注明删了多少点），空值行不落盘")
    if ok:
        _log(window, f"导出完成：{ok} 个文件")
    if fields["csv"]:
        _write_csv_summary(window, results, outdir)
