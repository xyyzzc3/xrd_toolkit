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

from xrd_toolkit.gui.panel_state import _bg_curve, _content, _log


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
                        quiet: bool = False) -> list:
    """收集勾选文件的 1D 积分结果：[(文件名, tth, intensity), ...]。

    只认已经算好的 1D 面板缓存（last_tth / last_intensity），按文件
    列表顺序返回；勾选里没算过的文件跳过并记日志（提示先点 [1D]
    出图）。重复文件改名加入的条目按显示名找各自面板。

    want_bg=True 时扣掉背景（锚点按文件路径取，与画图走同一个
    _bg_curve——导出与屏幕同一个口径）；模式关闭时原样返回。
    quiet=True 不记日志：导出要拿数量去填弹窗标题，之后再正式收一遍，
    两遍都记就会把"跳过 X"打两次。
    """
    checked = [window.file_list.item(i)
               for i in range(window.file_list.count())
               if window.file_list.item(i).checkState() == Qt.Checked]
    if not checked:
        if not quiet:
            _log(window, "没有选中的文件")
        return []
    out = []
    for item in checked:
        path = str(Path(item.data(Qt.UserRole)))
        display = item.text()
        for key in (f"1D|{path}", f"1D|{path}|{display}"):
            dock = window.plot_docks.get(key)
            if dock is not None and getattr(dock, "last_tth", None) is not None:
                tth, intensity = dock.last_tth, dock.last_intensity
                if want_bg:
                    _, intensity, _ = _bg_curve(window, dock, path, tth,
                                                intensity)
                out.append((Path(path).stem, tth, intensity))
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


def _write_export(target: Path, tth, intensity) -> None:
    """写一个两列 1D 数据文件（头行与格式逐字镜像 CLI integrate_pattern）。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    np.savetxt(str(target), np.c_[tth, intensity], fmt="%.6g",
               header="2theta(deg)  intensity")


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


def _write_csv_summary(window: QMainWindow, results, outdir: Path) -> None:
    """把一批 1D 结果汇总成一张 CSV：第一列 2θ，其后每文件一列强度。

    所有结果的 2θ 网格一致（同 npt 同范围）时直接按列拼；网格不一
    致时弹窗问用户（取公共交集重插值 / 跳过范围不同的文件 / 取消）。
    重插值 = 公共区间内按最大点数均匀取样，原数据 np.interp 上去。
    """
    grids = [tth for _, tth, _ in results]
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
            results = [(stem, common, np.interp(common, tth, intensity))
                       for stem, tth, intensity in results]
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
    grid = results[0][1]
    data = np.column_stack([grid] + [intensity for _, _, intensity in results])
    header = "2theta(deg)," + ",".join(stem for stem, _, _ in results)
    target = outdir / "1d_summary.csv"
    try:
        # comments=""：头行不带 # 前缀，读回时第一行就是列名
        np.savetxt(str(target), data, fmt="%.6g", delimiter=",",
                   header=header, comments="")
    except OSError as err:
        _log(window, f"CSV 总表写入失败（{err}）")
        return
    _log(window, f"已生成 CSV 总表 → {target}")


def _run_export(window: QMainWindow) -> None:
    """[导出数据]：勾选文件的 1D 结果批量落盘（镜像 CLI 的 txt 格式）。

    输出路径 = {目录}/{文件名}/integrated_2th{suffix}（与命令行
    integrate_pattern 同目录同格式）；可选 CSV 总表。单个文件写盘
    失败只记日志、不中断批处理；没算过 1D 的文件跳过并提示先点
    [1D] 出图。
    """
    # 先数一遍（确定"扣不扣背景"要等弹窗，但弹窗标题要个数量）——这一遍
    # 静默：否则"跳过 X：还没有 1D 结果"会在下面第二遍里再打一次
    n = len(_checked_1d_results(window, quiet=True))
    if not n:
        _checked_1d_results(window)   # 让跳过/空结果的原因照常记进日志
        return
    fields = _build_export_dialog(window, n)
    if fields is None:
        _log(window, "已取消导出")
        return
    want_bg = bool(fields.get("bg"))
    results = _checked_1d_results(window, want_bg=want_bg)
    if not results:
        return
    if want_bg:
        # 不报具体模式：扣除是**按面板快照**算的（每张图各记各的），
        # 而此处读到的控件值只反映当前编辑对象
        _log(window, "导出：按各面板自己的背景扣除设置扣背景")
    outdir, suffix = fields["dir"], fields["suffix"]
    ok = 0
    for stem, tth, intensity in results:
        target = outdir / stem / f"integrated_2th{suffix}"
        try:
            _write_export(target, tth, intensity)
        except OSError as err:
            _log(window, f"导出失败 {stem}（{err}）")
            continue
        ok += 1
        _log(window, f"已导出 {stem} → {target}")
    if ok:
        _log(window, f"导出完成：{ok} 个文件")
    if fields["csv"]:
        _write_csv_summary(window, results, outdir)


