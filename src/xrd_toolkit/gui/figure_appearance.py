"""工作台画布的轻量「图外观」对话框（2026-11-17）。

普通图面板已有 Customize；精修/原位这类嵌在中央工作台里的 Canvas
没有面板标题栏。这里提供一层最小的公共入口：标题、X/Y 标签、Y 轴
对数、网格、X/Y 范围。只改第一组坐标轴（工作台的主图），不碰
差值/布拉格自动布局。

返回 dict（空 dict = 全部恢复默认）；调用方把 dict 存在工作台上，
每次重画后 apply 一次即可。
"""
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit,
    QVBoxLayout)


def _num(text):
    text = str(text).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def edit_figure_appearance(parent, fig, opts=None):
    ax = fig.axes[0] if fig.axes else None
    if ax is None:
        return None
    opts = dict(opts or {})
    dlg = QDialog(parent)
    dlg.setWindowTitle("图外观")
    dlg.setMinimumWidth(360)
    lay = QVBoxLayout(dlg)
    form = QFormLayout()
    fields = {}
    for key, label, default in (
            ("title", "标题", opts.get("title", "")),
            ("xlabel", "X 轴标签", opts.get("xlabel", ax.get_xlabel())),
            ("ylabel", "Y 轴标签", opts.get("ylabel", ax.get_ylabel())),
            ("xmin", "X 最小", opts.get("xmin", "")),
            ("xmax", "X 最大", opts.get("xmax", "")),
            ("ymin", "Y 最小", opts.get("ymin", "")),
            ("ymax", "Y 最大", opts.get("ymax", ""))):
        edit = QLineEdit(str(default))
        if key == "title":
            edit.setPlaceholderText(
                ax.get_title() or "留空 = 保留自动标题（含 Rwp）")
        fields[key] = edit
        form.addRow(label, edit)
    log = QCheckBox("Y 轴对数刻度")
    log.setChecked(bool(opts.get("ylog", ax.get_yscale() == "log")))
    grid = QCheckBox("网格")
    grid.setChecked(bool(opts.get("grid", ax.get_xgridlines()
                                 and ax.get_xgridlines()[0].get_visible())))
    form.addRow("", log)
    form.addRow("", grid)
    lay.addLayout(form)
    buttons = QDialogButtonBox(
        QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    lay.addWidget(buttons)
    if dlg.exec() != QDialog.Accepted:
        return None
    out = {}
    for key, edit in fields.items():
        text = edit.text().strip()
        if text:
            out[key] = _num(text) if key.endswith(("min", "max")) else text
    out["ylog"] = bool(log.isChecked())
    out["grid"] = bool(grid.isChecked())
    return out


def apply_figure_appearance(fig, opts):
    """把 opts 套到第一组坐标轴；空/None = 保持 draw_fit 的默认样子。"""
    if not fig.axes or not opts:
        return
    ax = fig.axes[0]
    if opts.get("title"):
        ax.set_title(str(opts["title"]))
    if opts.get("xlabel"):
        ax.set_xlabel(str(opts["xlabel"]))
    if opts.get("ylabel"):
        ax.set_ylabel(str(opts["ylabel"]))
    xlo = opts.get("xmin")
    xhi = opts.get("xmax")
    if xlo is not None or xhi is not None:
        cur = ax.get_xlim()
        ax.set_xlim(cur[0] if xlo is None else xlo,
                    cur[1] if xhi is None else xhi)
    ylo = opts.get("ymin")
    yhi = opts.get("ymax")
    if ylo is not None or yhi is not None:
        cur = ax.get_ylim()
        ax.set_ylim(cur[0] if ylo is None else ylo,
                    cur[1] if yhi is None else yhi)
    try:
        ax.set_yscale("log" if opts.get("ylog") else "linear")
    except (ValueError, TypeError):
        pass
    ax.grid(bool(opts.get("grid")), alpha=0.25, linestyle=":")
