"""独立 Mask 编辑器（2026-10-27）。

从工具栏 [校准] 旁边的 [Mask] 打开，**不是**参数坞的一页：它要同时看
2D 图、画遮罩、跑自动检测、预览 mask 前后的 1D，塞进参数页会太挤。

流程（对应 Week 9 任务一）：
  1. 打开一张代表性图像（也可以直接用文件栏勾选的第一张）；
  2. 手动画：画笔遮、橡皮擦、矩形遮；或点 [自动坏点（局部）]；
     径向 nσ 是实验性按钮（真实环可能被误标，见 services/mask.py）；
  3. [保存 Mask…] 写 .npy/.edf；[加载 Mask…] 读回来；
  4. [应用并预览 1D]：把当前 mask 设为窗口级 mask（后续 1D 积分生效），
     并在下方画 mask 前/后的 1D 对比。

像素约定：mask[y, x] = True 表示该像素被遮掉；与
`services.mask`、`services.integrator` 一致。mask 必须与图像同形。
"""
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.colors import LogNorm
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QDialog, QDoubleSpinBox, QFileDialog,
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QSpinBox, QVBoxLayout,
    QWidget)

from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui.panel_state import _collect_geometry, _log
from xrd_toolkit.paths import OUTPUTS_DIR
from xrd_toolkit.services.data_loader import load_diffraction_image
from xrd_toolkit.services.hot_pixels import mask_hot_pixels
from xrd_toolkit.services.integrator import integrate_1d
from xrd_toolkit.services.mask import (
    combine_masks, load_mask, mask_stats, radial_outlier_mask, save_mask)


class MaskEditorWindow(QDialog):
    """独立 Mask 编辑器；主窗口持有一个实例，关闭只是隐藏。"""

    TOOL_BRUSH = "brush"
    TOOL_ERASER = "eraser"
    TOOL_RECT = "rect"

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main = main_window
        self.setWindowTitle("Mask 编辑器：2D → 1D 积分前遮罩")
        self.setModal(False)
        self.resize(1180, 860)

        self.image = None
        self.image_path = None
        self.mask = None
        self.mask_path = None
        self._undo = []
        self._tool = self.TOOL_BRUSH
        self._drawing = False
        self._last = None
        self._rect_start = None
        self._rect_patch = None
        self._overlay_artist = None
        self._overlay_ds = None
        self._last_dir = str(Path.home())

        self._build()
        self._update_status()

    # ── 界面 ────────────────────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)
        row1 = QHBoxLayout()
        self.btn_open = QPushButton("打开图像…")
        self.btn_open.clicked.connect(self._open_image)
        row1.addWidget(self.btn_open)
        self.btn_checked = QPushButton("用勾选第一张")
        self.btn_checked.setToolTip("用文件栏里当前勾选的第一张原始数据当底图")
        self.btn_checked.clicked.connect(self._use_checked)
        row1.addWidget(self.btn_checked)
        self.btn_load = QPushButton("加载 Mask…")
        self.btn_load.clicked.connect(self._load_mask)
        row1.addWidget(self.btn_load)
        self.btn_save = QPushButton("保存 Mask…")
        self.btn_save.clicked.connect(self._save_mask)
        row1.addWidget(self.btn_save)
        self.btn_clear = QPushButton("清空")
        self.btn_clear.clicked.connect(self._clear)
        row1.addWidget(self.btn_clear)
        self.btn_undo = QPushButton("撤销")
        self.btn_undo.clicked.connect(self._undo_last)
        row1.addWidget(self.btn_undo)
        row1.addStretch(1)
        root.addLayout(row1)

        row2 = QHBoxLayout()
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_buttons = {}
        for text, tool in (("画笔", self.TOOL_BRUSH),
                           ("橡皮", self.TOOL_ERASER),
                           ("矩形", self.TOOL_RECT)):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked=False, t=tool:
                                self._set_tool(t))
            self.tool_group.addButton(btn)
            self.tool_buttons[tool] = btn
            row2.addWidget(btn)
        self.tool_buttons[self.TOOL_BRUSH].setChecked(True)
        row2.addWidget(QLabel("笔刷"))
        self.brush = QSpinBox()
        self.brush.setRange(1, 201)
        self.brush.setValue(11)
        self.brush.setSuffix(" px")
        row2.addWidget(self.brush)
        self.btn_hot = QPushButton("自动坏点（局部）")
        self.btn_hot.setToolTip("现有 hot_pixels 检测：孤立亮团/坏点簇，"
                                "不碰真实衍射环（生产路径）")
        self.btn_hot.clicked.connect(self._auto_hot)
        row2.addWidget(self.btn_hot)
        self.btn_radial = QPushButton("径向 nσ（实验性）")
        self.btn_radial.setToolTip("按径向环带的中位数/MAD 找离群像素。"
                                   "真实环可能被误标，先预览再决定")
        self.btn_radial.clicked.connect(self._auto_radial)
        row2.addWidget(self.btn_radial)
        row2.addWidget(QLabel("nσ"))
        self.n_sigma = QDoubleSpinBox()
        self.n_sigma.setRange(1.0, 20.0)
        self.n_sigma.setSingleStep(0.5)
        self.n_sigma.setValue(5.0)
        row2.addWidget(self.n_sigma)
        row2.addStretch(1)
        root.addLayout(row2)

        self.fig = Figure(figsize=(10, 7.4))
        gs = self.fig.add_gridspec(2, 1, height_ratios=[3.4, 1.25],
                                   hspace=0.28)
        self.ax_img = self.fig.add_subplot(gs[0])
        self.ax_prev = self.fig.add_subplot(gs[1])
        self.ax_img.set_axis_off()
        self.ax_prev.set_xlabel("2θ (°)", fontsize=8)
        self.ax_prev.set_ylabel("counts", fontsize=8)
        self.ax_prev.tick_params(labelsize=7)
        self.ax_prev.grid(alpha=0.25, linestyle=":")
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.setFocusPolicy(Qt.StrongFocus)
        self.canvas.mpl_connect("button_press_event", self._on_press)
        self.canvas.mpl_connect("motion_notify_event", self._on_motion)
        self.canvas.mpl_connect("button_release_event", self._on_release)
        root.addWidget(self.canvas, stretch=1)

        row3 = QHBoxLayout()
        self.lbl_status = QLabel("未载入图像")
        self.lbl_status.setWordWrap(True)
        row3.addWidget(self.lbl_status, stretch=1)
        self.btn_preview = QPushButton("应用并预览 1D")
        self.btn_preview.setToolTip("把当前 mask 应用到后续 1D 积分，"
                                    "并在这里画 mask 前后的曲线")
        self.btn_preview.clicked.connect(self._apply_and_preview)
        row3.addWidget(self.btn_preview)
        self.btn_apply = QPushButton("应用到当前窗口")
        self.btn_apply.clicked.connect(self._apply_to_main)
        row3.addWidget(self.btn_apply)
        root.addLayout(row3)

    # ── 工具/状态 ───────────────────────────────────────────────────
    def _set_tool(self, tool):
        self._tool = tool
        self._update_status()

    def _update_status(self):
        if self.image is None or self.mask is None:
            text = "未载入图像：先 [打开图像…] 或 [用勾选第一张]"
        else:
            st = mask_stats(self.mask)
            h, w = self.image.shape
            text = (f"{Path(self.image_path).name}  {h}×{w} px  |  "
                    f"mask {st['masked_pixels']} px "
                    f"({st['fraction'] * 100:.2f}%)  |  {self._tool}")
            if self.mask_path is not None:
                text += f"  |  {Path(self.mask_path).name}"
        self.lbl_status.setText(text)

    def _push_undo(self):
        if self.mask is not None:
            self._undo.append(self.mask.copy())
            if len(self._undo) > 12:
                self._undo.pop(0)

    def _undo_last(self):
        if not self._undo:
            _log(self.main, "Mask：没有可撤销的操作")
            return
        self.mask = self._undo.pop()
        self._update_overlay()
        self._update_status()

    # ── 图像与 mask 显示 ────────────────────────────────────────────
    def _open_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择代表性图像", self._last_dir,
            "衍射图像 (*.tif *.tiff *.edf *.cbf);;所有文件 (*)")
        if path:
            self.load_image(Path(path))

    def _use_checked(self):
        raw = [s for s in gui_sources.checked_sources(self.main)
               if s.kind == gui_sources.RAW]
        if not raw:
            _log(self.main, "Mask：文件栏没有勾选的原始数据")
            return
        self.load_image(Path(raw[0].path))

    def load_image(self, path):
        try:
            img = load_diffraction_image(str(path))
        except Exception as err:                       # noqa: BLE001
            QMessageBox.critical(self, "读取失败", str(err))
            return
        if img.ndim != 2:
            QMessageBox.critical(self, "读取失败", "不是 2D 图像")
            return
        self.image = img
        self.image_path = Path(path)
        self._last_dir = str(self.image_path.parent)
        if self.mask is None or self.mask.shape != img.shape:
            self.mask = np.zeros(img.shape, dtype=bool)
            self.mask_path = None
        self._undo.clear()
        self._draw_image()
        self._update_status()
        _log(self.main, f"Mask 编辑器：载入 {self.image_path.name}"
                       f"（{img.shape[0]}×{img.shape[1]} px）")

    def _draw_image(self):
        self.ax_img.clear()
        self.ax_img.set_axis_off()
        img = self.image
        lo, hi = np.percentile(img, [1.0, 99.9])
        vmin = max(1.0, float(lo))
        vmax = max(float(hi), vmin * 10.0)
        self.ax_img.imshow(
            img, cmap="magma", norm=LogNorm(vmin=vmin, vmax=vmax),
            origin="lower", interpolation="nearest", aspect="equal")
        h, w = img.shape
        self.ax_img.set_xlim(-0.5, w - 0.5)
        self.ax_img.set_ylim(-0.5, h - 0.5)
        self._overlay_artist = None
        self._overlay_ds = None
        self._update_overlay()
        self.ax_prev.clear()
        self.ax_prev.set_xlabel("2θ (°)", fontsize=8)
        self.ax_prev.set_ylabel("counts", fontsize=8)
        self.ax_prev.tick_params(labelsize=7)
        self.ax_prev.grid(alpha=0.25, linestyle=":")
        self.ax_prev.text(0.5, 0.5, "点 [应用并预览 1D] 看 mask 前后",
                          transform=self.ax_prev.transAxes, ha="center",
                          va="center", color="0.45", fontsize=9)
        self.canvas.draw_idle()

    def _overlay_rgba(self):
        # 大图降采样只影响显示；真正的 mask 仍是全分辨率
        ds = 1 if self.mask.size <= 1_500_000 else 2
        small = self.mask[::ds, ::ds]
        rgba = np.zeros((*small.shape, 4), dtype=np.float32)
        rgba[..., 0] = 1.0
        rgba[..., 3] = 0.45 * small
        return rgba, ds

    def _update_overlay(self):
        if self.mask is None or self.image is None:
            return
        rgba, ds = self._overlay_rgba()
        h, w = self.image.shape
        if self._overlay_artist is None or self._overlay_ds != ds:
            if self._overlay_artist is not None:
                try:
                    self._overlay_artist.remove()
                except Exception:                      # noqa: BLE001
                    pass
            self._overlay_artist = self.ax_img.imshow(
                rgba, extent=(-0.5, w - 0.5, -0.5, h - 0.5),
                origin="lower", interpolation="nearest", zorder=5)
            self._overlay_ds = ds
        else:
            self._overlay_artist.set_data(rgba)
        self.canvas.draw_idle()

    # ── 鼠标绘制 ────────────────────────────────────────────────────
    def _pixel(self, event):
        if event.xdata is None or event.ydata is None:
            return None
        col = int(round(float(event.xdata)))
        row = int(round(float(event.ydata)))
        h, w = self.image.shape
        if 0 <= row < h and 0 <= col < w:
            return row, col
        return None

    def _stamp(self, row, col, value):
        hw = max(0, int(self.brush.value()) // 2)
        h, w = self.mask.shape
        r0, r1 = max(0, row - hw), min(h, row + hw + 1)
        c0, c1 = max(0, col - hw), min(w, col + hw + 1)
        if r0 < r1 and c0 < c1:
            self.mask[r0:r1, c0:c1] = value

    def _paint_segment(self, a, b, value):
        dr, dc = b[0] - a[0], b[1] - a[1]
        n = max(abs(dr), abs(dc), 1)
        for t in np.linspace(0.0, 1.0, n + 1):
            self._stamp(int(round(a[0] + dr * t)),
                        int(round(a[1] + dc * t)), value)

    def _fill_rect(self, a, b, value):
        r0, r1 = sorted((a[0], b[0]))
        c0, c1 = sorted((a[1], b[1]))
        h, w = self.mask.shape
        self.mask[max(0, r0):min(h, r1 + 1),
                  max(0, c0):min(w, c1 + 1)] = value

    def _on_press(self, event):
        if (self.image is None or self.mask is None
                or event.button != 1 or event.inaxes is not self.ax_img):
            return
        rc = self._pixel(event)
        if rc is None:
            return
        self._push_undo()
        self._drawing = True
        self._last = rc
        if self._tool in (self.TOOL_BRUSH, self.TOOL_ERASER):
            self._paint_segment(rc, rc, self._tool == self.TOOL_BRUSH)
            self._update_overlay()
            self._update_status()
        else:
            self._rect_start = rc
            r0, r1 = sorted((rc[0], rc[0]))
            c0, c1 = sorted((rc[1], rc[1]))
            self._rect_patch = Rectangle(
                (c0 - 0.5, r0 - 0.5), max(c1 - c0 + 1, 1),
                max(r1 - r0 + 1, 1), fill=False, edgecolor="yellow",
                lw=1.2, zorder=6)
            self.ax_img.add_patch(self._rect_patch)
            self.canvas.draw_idle()

    def _on_motion(self, event):
        if not self._drawing or self.image is None:
            return
        rc = self._pixel(event)
        if rc is None:
            return
        if self._tool in (self.TOOL_BRUSH, self.TOOL_ERASER):
            self._paint_segment(self._last, rc,
                                self._tool == self.TOOL_BRUSH)
            self._last = rc
            self._update_overlay()
            self._update_status()
        elif self._rect_patch is not None and self._rect_start is not None:
            r0, r1 = sorted((self._rect_start[0], rc[0]))
            c0, c1 = sorted((self._rect_start[1], rc[1]))
            self._rect_patch.set_xy((c0 - 0.5, r0 - 0.5))
            self._rect_patch.set_width(max(c1 - c0 + 1, 1))
            self._rect_patch.set_height(max(r1 - r0 + 1, 1))
            self.canvas.draw_idle()

    def _on_release(self, event):
        if not self._drawing:
            return
        if self._tool == self.TOOL_RECT and self._rect_start is not None:
            rc = self._pixel(event) or self._rect_start
            self._fill_rect(self._rect_start, rc, True)
            if self._rect_patch is not None:
                try:
                    self._rect_patch.remove()
                except Exception:                      # noqa: BLE001
                    pass
                self._rect_patch = None
            self._update_overlay()
            self._update_status()
        self._drawing = False
        self._last = None
        self._rect_start = None

    # ── 自动检测 ────────────────────────────────────────────────────
    def _auto_hot(self):
        if self.image is None:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            _clean, hot, info = mask_hot_pixels(self.image)
        except Exception as err:                       # noqa: BLE001
            QMessageBox.critical(self, "自动坏点失败", str(err))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self._push_undo()
        self.mask = combine_masks(self.mask, hot)
        self._update_overlay()
        self._update_status()
        _log(self.main, f"Mask 自动坏点：{info.get('masked_components', '?')} 团 / "
                       f"{info.get('masked_pixels', int(hot.sum()))} px")

    def _auto_radial(self):
        if self.image is None:
            return
        ans = QMessageBox.question(
            self, "径向 nσ（实验性）",
            "这个方法按径向环带找离群像素；真实衍射环的强弧段可能被\n"
            "误标（实测 LMFP 上会沿环标出约 18k px）。\n\n"
            "建议只作参考，跑完用 [撤销] 或橡皮擦掉误标。继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans != QMessageBox.Yes:
            return
        center = tuple(getattr(self.main, "config", {}).get(
            "beam_center", (self.image.shape[0] / 2.0,
                            self.image.shape[1] / 2.0)))
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            radial = radial_outlier_mask(
                self.image, center, n_sigma=float(self.n_sigma.value()))
        except Exception as err:                       # noqa: BLE001
            QMessageBox.critical(self, "径向 nσ 失败", str(err))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self._push_undo()
        self.mask = combine_masks(self.mask, radial)
        self._update_overlay()
        self._update_status()
        _log(self.main, f"Mask 径向 nσ（实验性）：{int(radial.sum())} px"
                       f"（n_sigma={self.n_sigma.value():g}）")

    # ── 保存/加载/应用 ──────────────────────────────────────────────
    def _default_mask_path(self):
        d = Path(OUTPUTS_DIR) / "masks"
        d.mkdir(parents=True, exist_ok=True)
        name = getattr(self.main, "config_name", "mask") or "mask"
        return d / f"{name}_mask.npy"

    def _save_mask(self):
        if self.mask is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 Mask", str(self._default_mask_path()),
            "Mask (*.npy *.edf)")
        if not path:
            return
        p = Path(path)
        if p.suffix.lower() not in (".npy", ".edf"):
            p = p.with_suffix(".npy")
        try:
            save_mask(p, self.mask)
        except Exception as err:                       # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(err))
            return
        self.mask_path = p
        self._apply_to_main()
        _log(self.main, f"Mask 已保存：{p}")

    def _load_mask(self):
        if self.image is None:
            QMessageBox.information(self, "先打开图像",
                                    "先打开一张代表性图像，mask 的形状要"
                                    "与积分图像一致。")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "加载 Mask", str(self._default_mask_path().parent),
            "Mask (*.npy *.edf);;所有文件 (*)")
        if not path:
            return
        try:
            mask = load_mask(path)
        except Exception as err:                       # noqa: BLE001
            QMessageBox.critical(self, "加载失败", str(err))
            return
        if mask.shape != self.image.shape:
            QMessageBox.critical(
                self, "形状不一致",
                f"mask {mask.shape} 与图像 {self.image.shape} 不一致")
            return
        self.mask = mask
        self.mask_path = Path(path)
        self._undo.clear()
        self._update_overlay()
        self._update_status()
        self._apply_to_main()
        _log(self.main, f"Mask 已加载：{path}")

    def _apply_to_main(self):
        if self.mask is None:
            return
        self.main.current_mask = self.mask.copy()
        self.main.mask_path = self.mask_path
        st = mask_stats(self.mask)
        src = self.mask_path.name if self.mask_path else "未保存"
        self.main.status_text.setText(
            f"Mask 已应用：{st['masked_pixels']} px")
        _log(self.main, f"Mask 已应用：{st['masked_pixels']} px"
                       f"（{st['fraction'] * 100:.2f}%），{src}")

    def _clear(self):
        if self.mask is None:
            return
        self._push_undo()
        self.mask[:] = False
        self.mask_path = None
        self._update_overlay()
        self._update_status()

    def _apply_and_preview(self):
        if self.image is None or self.mask is None:
            return
        self._apply_to_main()
        geom = _collect_geometry(self.main)
        npt = int(self.main.params["输出点数"].value())
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            t0, y0 = integrate_1d(self.image, npt=npt, **geom)
            _t1, y1 = integrate_1d(self.image, npt=npt, mask=self.mask, **geom)
        except Exception as err:                       # noqa: BLE001
            QMessageBox.critical(self, "预览失败", str(err))
            return
        finally:
            QApplication.restoreOverrideCursor()
        ax = self.ax_prev
        ax.clear()
        ax.plot(t0, y0, lw=0.8, color="0.45", label="no mask")
        ax.plot(t0, y1, lw=0.9, color="tab:red", label="with mask")
        ax.set_xlabel("2θ (°)", fontsize=8)
        ax.set_ylabel("counts", fontsize=8)
        ax.tick_params(labelsize=7)
        ax.grid(alpha=0.25, linestyle=":")
        ax.legend(fontsize=8, loc="best")
        self.canvas.draw_idle()
