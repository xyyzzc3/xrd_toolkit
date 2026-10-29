"""独立 Mask 编辑器（2026-10-27）。

从工具栏 [校准] 旁边的 [Mask] 打开，**不是**参数坞的一页：它要同时看
2D 图、画遮罩、跑自动检测、预览 mask 前后的 1D，塞进参数页会太挤。

流程（对应 Week 9 任务一）：
  1. 打开一张代表性图像（也可以直接用文件栏勾选的第一张）；
  2. 手动画：画笔遮、橡皮擦、矩形遮；或点 [自动坏点（局部）]；
     径向 nσ 是实验性按钮（真实环可能被误标，见 services/mask.py）；
  3. [保存 Mask…] 写 .npy/.edf；[加载 Mask…] 读回来；
  4. [生成 Mask 2D]：在 stage cache 写一条 mask manifest（原始图 +
     mask），文件栏出现「Mask 后的 2D」条目；勾着「同时预览 1D」时
     下方再画 mask 前/后的 1D 对比。[应用到勾选文件] 给整批各生成一条。

视图：滚轮缩放；中键（或 ⌥/Shift + 左键）拖动平移；[全图] 复位。
mask 永远是全分辨率；只有**显示**按视图降采样（块内取最大，来源数组按
优先级取），所以缩到全图时孤立坏点也不会被平均掉。底图与 overlay 只画
视野内的那块，overlay 每 16 ms 合并一次并 blit，绘制时不会整屏重画。

像素约定：mask[y, x] = True 表示该像素被遮掉；与
`services.mask`、`services.integrator` 一致。mask 必须与图像同形。
"""
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.colors import LogNorm
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QDialog, QDoubleSpinBox,
    QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSpinBox,
    QVBoxLayout, QWidget)

from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui.image_utils import downsample_max
from xrd_toolkit.gui.panel_state import _collect_geometry, _log
from xrd_toolkit.gui.tasks import BackgroundTask
from xrd_toolkit.paths import OUTPUTS_DIR
from xrd_toolkit.services.data_loader import load_diffraction_image
from xrd_toolkit.services.hot_pixels import mask_hot_pixels
from xrd_toolkit.services.integrator import integrate_1d
from xrd_toolkit.services import masked2d
from xrd_toolkit.services.mask import (
    combine_masks, load_mask, mask_stats, radial_outlier_mask, save_mask)


# overlay 三种来源的配色：都避开 magma 的橙黄主色带，叠在亮斑上也能分辨。
COLOR_MANUAL = (229, 57, 53)    # 红：画笔/矩形
COLOR_HOT = (0, 229, 255)       # 青：自动坏点（局部）
COLOR_RADIAL = (118, 255, 3)    # 绿：径向 nσ（实验性）
MASK_ALPHA = 115                # ≈ 0.45

SRC_MANUAL = 3                  # mask 来源标记（只影响显示，不参与产物）
SRC_HOT = 2                     # 数值越大越优先：降采样取 max 时手动盖住自动
SRC_RADIAL = 1

MIN_VIEW_SPAN = 8.0             # 最多放大到 8×8 图像像素
MAX_DS = 8                      # 显示降采样上限

# 来源值 → RGBA 的查表（0 = 没遮，全透明）；降采样后一次 gather 上色
_MASK_LUT = np.zeros((256, 4), dtype=np.uint8)
_MASK_LUT[SRC_MANUAL] = (*COLOR_MANUAL, MASK_ALPHA)
_MASK_LUT[SRC_HOT] = (*COLOR_HOT, MASK_ALPHA)
_MASK_LUT[SRC_RADIAL] = (*COLOR_RADIAL, MASK_ALPHA)


def _radial_worker(image, center, n_sigma):
    """后台线程跑径向 nσ；放在模块级，避免闭包在 QThread 里的生命周期坑。"""
    return radial_outlier_mask(image, center, n_sigma=n_sigma)


class MaskEditorWindow(QDialog):
    """独立 Mask 编辑器；主窗口持有一个实例，关闭只是隐藏。"""

    TOOL_BRUSH = "brush"
    TOOL_ERASER = "eraser"
    TOOL_RECT = "rect"
    TOOL_NAMES = {TOOL_BRUSH: "画笔", TOOL_ERASER: "橡皮", TOOL_RECT: "矩形"}

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main = main_window
        self.setWindowTitle("Mask 编辑器：2D → 1D 积分前遮罩")
        self.setModal(False)
        self.setWindowFlag(Qt.Tool, True)   # macOS：浮在父窗口之上，不抢主界面
        self.setWindowModality(Qt.NonModal)
        self.resize(1180, 860)

        self.image = None
        self.image_path = None
        self.mask = None
        self._source = None          # uint8：每个 mask 像素的来源（只影响配色）
        self.mask_path = None
        self._undo = []
        self._tool = self.TOOL_BRUSH
        self._drawing = False
        self._last = None
        self._rect_start = None
        self._rect_patch = None
        self._base_artist = None
        self._overlay_artist = None
        self._overlay_ds = None
        self._norm = None
        self._last_dir = str(Path.home())
        self._auto_task = None
        # 显示缓存：底图/overlay 降采样 + blitting；mask 仍是全分辨率
        self._disp_ds = 0
        self._disp_box = None        # 显示窗口 (r0, r1, c0, c1)，按 ds 对齐
        self._bg = None
        self._panning = False
        self._pan_start = None
        self._layers = {}
        self._overlay_timer = QTimer(self)
        self._overlay_timer.setSingleShot(True)
        self._overlay_timer.setInterval(16)
        self._overlay_timer.timeout.connect(self._flush_overlay)
        self._view_timer = QTimer(self)
        self._view_timer.setSingleShot(True)
        self._view_timer.setInterval(33)
        self._view_timer.timeout.connect(self._redraw_all)

        self._build()
        self._wire_checked_count()
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
        self.btn_clear.setToolTip("清空编辑器里的当前 mask（已生成的 "
                                  "Mask 2D 产物不受影响）")
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
                                   "真实环可能被误标，跑完看绿色区域，"
                                   "不对就用橡皮/撤销修")
        self.btn_radial.clicked.connect(self._auto_radial)
        row2.addWidget(self.btn_radial)
        row2.addWidget(QLabel("nσ"))
        self.n_sigma = QDoubleSpinBox()
        self.n_sigma.setRange(1.0, 20.0)
        self.n_sigma.setSingleStep(0.5)
        self.n_sigma.setValue(5.0)
        row2.addWidget(self.n_sigma)
        self.btn_home = QPushButton("全图")
        self.btn_home.setToolTip("复位 2D 视图（滚轮缩放；中键或 ⌥/Shift + "
                                 "左键拖动平移）")
        self.btn_home.clicked.connect(self._reset_view)
        row2.addWidget(self.btn_home)
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
        self.canvas.mpl_connect("scroll_event", self._on_scroll)
        self.canvas.mpl_connect("resize_event", self._on_resize)
        self.canvas.mpl_connect("draw_event", self._on_draw)
        root.addWidget(self.canvas, stretch=1)

        row3 = QHBoxLayout()
        self.lbl_status = QLabel("未载入图像")
        self.lbl_status.setWordWrap(True)
        row3.addWidget(self.lbl_status, stretch=1)
        self.lbl_legend = QLabel(
            '<span style="color:#e53935">■</span> 手动 '
            '<span style="color:#00e5ff">■</span> 坏点 '
            '<span style="color:#76ff03">■</span> 径向')
        self.lbl_legend.setToolTip(
            "红色 = 画笔/矩形；青色 = 自动坏点（局部）；"
            "绿色 = 径向 nσ（实验性）。橡皮擦掉的就是没遮。")
        row3.addWidget(self.lbl_legend)
        self.btn_apply = QPushButton("生成 Mask 2D")
        self.btn_apply.setToolTip(
            "为当前图像生成一条 Mask 后的 2D 数据（原始像素 + mask）")
        self.btn_apply.clicked.connect(self._generate_current)
        row3.addWidget(self.btn_apply)
        self.chk_preview = QCheckBox("同时预览 1D")
        self.chk_preview.setChecked(True)
        self.chk_preview.setToolTip("生成后在这里画 mask 前/后两条 1D 曲线")
        row3.addWidget(self.chk_preview)
        self.btn_apply_checked = QPushButton("应用到勾选文件")
        self.btn_apply_checked.setToolTip(
            "把这张 mask 生成到文件栏里勾选的每个原始数据上"
            "（同一探测器、同形状）")
        self.btn_apply_checked.clicked.connect(self._apply_to_checked)
        row3.addWidget(self.btn_apply_checked)
        for btn in self.findChildren(QPushButton):
            btn.setAutoDefault(False)
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
                    f"({st['fraction'] * 100:.2f}%)  |  "
                    f"{self.TOOL_NAMES.get(self._tool, self._tool)}")
            if self.mask_path is not None:
                text += f"  |  {Path(self.mask_path).name}"
        self.lbl_status.setText(text)

    def _ensure_source(self):
        """保证来源数组和 mask 同形；老状态/外部直接写 mask 时兜底成"手动"。"""
        if self.mask is None:
            return None
        if self._source is None or self._source.shape != self.mask.shape:
            self._source = np.where(self.mask, SRC_MANUAL, 0).astype(np.uint8)
        return self._source

    def _push_undo(self):
        if self.mask is not None:
            src = self._ensure_source()
            self._undo.append((self.mask.copy(), src.copy()))
            if len(self._undo) > 12:
                self._undo.pop(0)

    def _undo_last(self):
        if not self._undo:
            _log(self.main, "Mask：没有可撤销的操作")
            return
        self.mask, self._source = self._undo.pop()
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
               if gui_sources.is_rawlike(s.kind)]
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
        if np.ma.isMaskedArray(img):
            # 从 Mask 2D 产物打开：解析到原始图，并把它的 mask 载入编辑器
            raw_path, mask0, _meta = masked2d.load_manifest(path)
            self.image = np.asarray(np.ma.getdata(img))
            self.image_path = Path(raw_path)
            self.mask = np.array(mask0, dtype=bool, copy=True)
            self.mask_path = None
            # 整张 mask 被换掉：来源无从得知，统一按“手动”（红）显示
            self._source = np.where(self.mask, SRC_MANUAL, 0).astype(np.uint8)
        else:
            self.image = img
            self.image_path = Path(path)
            if self.mask is None or self.mask.shape != img.shape:
                self.mask = np.zeros(img.shape, dtype=bool)
                self.mask_path = None
        self._ensure_source()
        self._last_dir = str(self.image_path.parent)
        self._undo.clear()
        self._draw_image()
        self._update_status()
        _log(self.main, f"Mask 编辑器：载入 {self.image_path.name}"
                       f"（{img.shape[0]}×{img.shape[1]} px）")

    def _display_ds(self):
        """显示降采样倍数：屏幕上 1 px 不必对应多于 ~1 个图像块。"""
        if self.image is None:
            return 1
        try:
            ax_px = float(self.ax_img.get_window_extent().width)
        except Exception:                              # noqa: BLE001
            ax_px = 0.0
        if not np.isfinite(ax_px) or ax_px < 50.0:
            ax_px = max(1.0, float(self.fig.get_size_inches()[0]
                                   * self.fig.dpi))
        x0, x1 = self.ax_img.get_xlim()
        vis = abs(float(x1) - float(x0))
        ds = int(np.ceil(vis / (ax_px * 1.25)))
        return int(min(max(ds, 1), MAX_DS))

    def _display_box(self, ds):
        """当前视图对应的显示窗口 (r0, r1, c0, c1)：按 ds 对齐、外扩 10%。

        只画看得见的那块——matplotlib 的绘制成本按**数据像素数**算，
        整张 2048² 每帧要 600 ms，裁到视野后几十毫秒。
        """
        h, w = self.image.shape
        x0, x1 = self.ax_img.get_xlim()
        y0, y1 = self.ax_img.get_ylim()

        def snap(lo, hi, n):
            pad = max(0.0, (hi - lo) * 0.1)
            a = int(np.floor((lo - pad + 0.5) / ds) * ds)
            b = int(np.ceil((hi + pad + 0.5) / ds) * ds)
            a = max(0, min(a, n - 1))
            b = max(a + 1, min(b, n))
            return a, b

        c0, c1 = snap(x0, x1, w)
        r0, r1 = snap(y0, y1, h)
        return r0, r1, c0, c1

    def _rebuild_base(self):
        """按当前视图重建底图；降采样倍数和显示窗口都没变就不动 artist。"""
        if self.image is None:
            return
        ds = self._display_ds()
        box = self._display_box(ds)
        if (self._base_artist is not None and ds == self._disp_ds
                and box == self._disp_box):
            return
        r0, r1, c0, c1 = box
        disp = downsample_max(self.image[r0:r1, c0:c1], ds)
        hh, ww = disp.shape
        # 显示块 != 图像像素：extent 要乘回 ds，块心才落在原像素中心
        extent = (-0.5 + c0, -0.5 + c0 + ww * ds,
                  -0.5 + r0, -0.5 + r0 + hh * ds)
        if self._base_artist is None:
            self._base_artist = self.ax_img.imshow(
                disp, cmap="magma", norm=self._norm, extent=extent,
                origin="lower", interpolation="nearest", aspect="equal",
                zorder=1)
        else:
            self._base_artist.set_data(disp)
            self._base_artist.set_extent(extent)
        self._disp_ds = ds
        self._disp_box = box
        self._overlay_ds = None          # 块大小变了，overlay 要跟着重建

    def _draw_image(self):
        self.ax_img.clear()
        self.ax_img.set_axis_off()
        img = self.image
        lo, hi = np.percentile(img, [1.0, 99.9])
        vmin = max(1.0, float(lo))
        vmax = max(float(hi), vmin * 10.0)
        self._norm = LogNorm(vmin=vmin, vmax=vmax)
        h, w = img.shape
        self.ax_img.set_xlim(-0.5, w - 0.5)
        self.ax_img.set_ylim(-0.5, h - 0.5)
        self.ax_img.set_aspect("equal")
        self._base_artist = None
        self._overlay_artist = None
        self._overlay_ds = None
        self._layers = {}
        self._disp_ds = 0
        self._disp_box = None
        self._bg = None
        self._rebuild_base()
        self._sync_overlay()
        self.ax_prev.clear()
        self.ax_prev.set_xlabel("2θ (°)", fontsize=8)
        self.ax_prev.set_ylabel("counts", fontsize=8)
        self.ax_prev.tick_params(labelsize=7)
        self.ax_prev.grid(alpha=0.25, linestyle=":")
        self.ax_prev.text(
            0.5, 0.5, "点 [生成 Mask 2D]（勾着「同时预览 1D」）看 mask 前后",
            transform=self.ax_prev.transAxes, ha="center", va="center",
            color="0.45", fontsize=9)
        self.canvas.draw_idle()

    def _overlay_rgba(self, ds):
        """mask → 显示尺寸 RGBA（按来源上色，只做看得见的那块）。"""
        src = self._ensure_source()
        box = self._disp_box
        if box is None:                       # 还没画过图：整张
            r0, r1, c0, c1 = 0, self.image.shape[0], 0, self.image.shape[1]
        else:
            r0, r1, c0, c1 = box
        sub = src[r0:r1, c0:c1]
        bad = self.mask[r0:r1, c0:c1] & (sub == 0)
        if bad.any():                         # 外部直接写 mask 的兜底：按手动算
            sub = sub.copy()
            sub[bad] = SRC_MANUAL
        small = downsample_max(sub, ds)
        # np.take 比 LUT[small] 快一倍（2048²：10 ms vs 22 ms）
        rgba = np.take(_MASK_LUT, small, axis=0)
        hh, ww = rgba.shape[:2]
        return rgba, (-0.5 + c0, -0.5 + c0 + ww * ds,
                      -0.5 + r0, -0.5 + r0 + hh * ds)

    def _sync_overlay(self):
        """把当前 mask 压成 RGBA 挂到图上（不调度重绘）。"""
        if (self.image is None or self.mask is None
                or self._base_artist is None):
            return
        rgba, extent = self._overlay_rgba(self._disp_ds)
        if self._overlay_artist is None or self._overlay_ds != self._disp_ds:
            if self._overlay_artist is not None:
                try:
                    self._overlay_artist.remove()
                except Exception:                      # noqa: BLE001
                    pass
            self._overlay_artist = self.ax_img.imshow(
                rgba, extent=extent, origin="lower",
                interpolation="nearest", aspect="equal", zorder=5)
            self._overlay_ds = self._disp_ds
            self._layers["overlay"] = self._overlay_artist
            self._bg = None          # 新 artist 必须先进入一次完整绘制
        else:
            self._overlay_artist.set_data(rgba)
            self._overlay_artist.set_extent(extent)

    def _update_overlay(self):
        if self.mask is None or self.image is None:
            return
        self._sync_overlay()
        self._overlay_timer.start()

    def _on_draw(self, event):
        # 任何完整绘制之后，缓冲区里就带上了 overlay；背景要重新抓
        self._bg = None

    def _ensure_bg(self):
        """抓一帧**不含 overlay / 矩形预览**的背景。

        blit 必须基于它：restore_region 把旧 overlay 也还原了，只画新的
        会留下擦不掉的旧色块（橡皮擦过的区域尤其明显）。
        """
        if self._bg is not None or self._overlay_artist is None:
            return
        art = self._overlay_artist
        rect = self._rect_patch
        art.set_visible(False)
        if rect is not None:
            rect.set_visible(False)
        try:
            self.canvas.draw()
            self._bg = self.canvas.copy_from_bbox(self.ax_img.bbox)
        except Exception:                              # noqa: BLE001
            self._bg = None
        finally:
            art.set_visible(True)
            if rect is not None:
                rect.set_visible(True)

    def _flush_overlay(self):
        """16 ms 合并一次：只重画 overlay（+ 矩形预览），不整屏重画。"""
        if self.image is None or self._overlay_artist is None:
            return
        self._ensure_bg()
        if self._bg is None:
            self.canvas.draw_idle()
            return
        try:
            self.canvas.restore_region(self._bg)
            for artist in self._layers.values():
                if artist is not None and artist.axes is not None:
                    self.ax_img.draw_artist(artist)
            if (self._rect_patch is not None
                    and self._rect_patch.axes is not None):
                self.ax_img.draw_artist(self._rect_patch)
            self.canvas.blit(self.ax_img.bbox)
        except Exception:                              # noqa: BLE001
            # blit 失败（窗口最小化/后端不支持）：退回整帧重画，别让异常
            # 落进 Qt 槽里
            self._bg = None
            self.canvas.draw_idle()

    def _redraw_all(self):
        """视图变了（缩放/平移/窗口大小）：重建底图 + 完整重画。"""
        if self.image is None:
            return
        self._rebuild_base()
        self._sync_overlay()
        self.canvas.draw_idle()

    # ── 缩放/平移 ───────────────────────────────────────────────────
    @staticmethod
    def _centered(center, span, lo, hi):
        """把跨度为 span 的窗口放在 center 上，并夹在 [lo, hi] 里。"""
        if span >= (hi - lo):
            mid = (lo + hi) / 2.0
            return mid - span / 2.0, mid + span / 2.0
        center = min(max(center, lo + span / 2.0), hi - span / 2.0)
        return center - span / 2.0, center + span / 2.0

    def _zoom_about(self, cx, cy, factor):
        """以 (cx, cy) 为中心缩放 factor 倍；x/y 同一比例，像素保持正方。"""
        x0, x1 = self.ax_img.get_xlim()
        y0, y1 = self.ax_img.get_ylim()
        h, w = self.image.shape
        sx0, sy0 = float(x1 - x0), float(y1 - y0)
        sx = min(max(sx0 * factor, MIN_VIEW_SPAN), float(w))
        sy = min(max(sy0 * factor, MIN_VIEW_SPAN), float(h))
        f = min(sx / sx0, sy / sy0)
        sx, sy = sx0 * f, sy0 * f
        x0, x1 = self._centered(cx, sx, -0.5, w - 0.5)
        y0, y1 = self._centered(cy, sy, -0.5, h - 0.5)
        self.ax_img.set_xlim(x0, x1)
        self.ax_img.set_ylim(y0, y1)
        self._bg = None
        self._view_timer.start()

    def _reset_view(self):
        if self.image is None:
            return
        h, w = self.image.shape
        self.ax_img.set_xlim(-0.5, w - 0.5)
        self.ax_img.set_ylim(-0.5, h - 0.5)
        self._bg = None
        self._redraw_all()

    def _on_scroll(self, event):
        if (self.image is None or event.inaxes is not self.ax_img
                or event.xdata is None or event.ydata is None):
            return
        button = event.button
        if button is None:                 # 有的后端只给 step 不给 button
            step = float(getattr(event, "step", 0) or 0)
            if not step:
                return
            button = "up" if step > 0 else "down"
        if button not in ("up", "down"):
            return
        # 每格 10%——与 plot_panels._wheel_zoom / 校准页同一口径：
        # 触摸板两指一滑是连续好多小事件，大百分比连乘几下就飞了
        self._zoom_about(float(event.xdata), float(event.ydata),
                         1.0 / 1.1 if button == "up" else 1.1)

    def _is_pan_gesture(self, event):
        """中键；或 ⌥/Shift + 左键（Mac 触控板没有中键）。"""
        if event.button == 2:
            return True
        if event.button != 1:
            return False
        if event.key in ("alt", "shift"):
            return True
        gui = getattr(event, "guiEvent", None)
        mods = getattr(gui, "modifiers", None)
        if callable(mods):
            try:
                return bool(mods() & (Qt.AltModifier | Qt.ShiftModifier))
            except Exception:                          # noqa: BLE001
                return False
        return False

    def _pan_move(self, event):
        x0p, y0p, xlim0, ylim0 = self._pan_start
        bw = max(1.0, float(self.ax_img.bbox.width))
        bh = max(1.0, float(self.ax_img.bbox.height))
        span_x = float(xlim0[1] - xlim0[0])
        span_y = float(ylim0[1] - ylim0[0])
        cx = (xlim0[0] + xlim0[1]) / 2.0 - (event.x - x0p) * span_x / bw
        cy = (ylim0[0] + ylim0[1]) / 2.0 - (event.y - y0p) * span_y / bh
        h, w = self.image.shape
        x0, x1 = self._centered(cx, span_x, -0.5, w - 0.5)
        y0, y1 = self._centered(cy, span_y, -0.5, h - 0.5)
        self.ax_img.set_xlim(x0, x1)
        self.ax_img.set_ylim(y0, y1)
        self._bg = None
        self._view_timer.start()

    def _on_resize(self, event):
        self._bg = None
        if self.image is not None:
            self._view_timer.start()

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
            self._ensure_source()[r0:r1, c0:c1] = (SRC_MANUAL if value else 0)

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
        r0, r1 = max(0, r0), min(h, r1 + 1)
        c0, c1 = max(0, c0), min(w, c1 + 1)
        self.mask[r0:r1, c0:c1] = value
        self._ensure_source()[r0:r1, c0:c1] = (SRC_MANUAL if value else 0)

    def _on_press(self, event):
        if (self.image is None or self.mask is None
                or event.inaxes is not self.ax_img):
            return
        if self._is_pan_gesture(event):
            self._panning = True
            self._pan_start = (event.x, event.y,
                               self.ax_img.get_xlim(),
                               self.ax_img.get_ylim())
            self.canvas.setCursor(Qt.ClosedHandCursor)
            return
        if event.button != 1:
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
            self._overlay_timer.start()

    def _on_motion(self, event):
        if self._panning and self._pan_start is not None:
            self._pan_move(event)
            return
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
            self._overlay_timer.start()

    def _on_release(self, event):
        if self._panning:
            self._panning = False
            self._pan_start = None
            self.canvas.setCursor(Qt.ArrowCursor)
            self._redraw_all()
            return
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
    def _auto_busy(self, busy: bool) -> None:
        self.btn_hot.setEnabled(not busy)
        self.btn_radial.setEnabled(not busy)

    def _start_auto(self, label, fn, args, on_ok) -> None:
        """自动检测放后台（2048² 上实测 0.7–2.3 s，不能卡界面）。"""
        if self._auto_task is not None or self.image is None:
            return
        self._auto_busy(True)
        self.lbl_status.setText(f"{label}检测中…")

        def done(result):
            self._auto_task = None
            self._auto_busy(False)
            self._update_status()
            on_ok(result)

        def error(msg):
            self._auto_task = None
            self._auto_busy(False)
            self._update_status()
            QMessageBox.critical(self, f"{label}失败", msg)
            _log(self.main, f"Mask {label}失败：{msg}")

        self._auto_task = BackgroundTask(
            fn, *args, on_done=done, on_error=error)
        self._auto_task.start()

    def _auto_hot(self):
        if self.image is None:
            return
        image = self.image.copy()      # 后台只读快照，避免边跑边改

        def apply(result):
            _clean, hot, info = result
            if hot.shape != self.mask.shape:
                _log(self.main, "Mask 自动坏点：图像已换，结果丢弃")
                return
            self._push_undo()
            new = hot & ~self.mask          # 只给新标出的上色，别覆盖手动
            self.mask = combine_masks(self.mask, hot)
            self._ensure_source()[new] = SRC_HOT
            self._update_overlay()
            self._update_status()
            _log(self.main, f"Mask 自动坏点："
                           f"{info.get('masked_components', '?')} 团 / "
                           f"{info.get('masked_pixels', int(hot.sum()))} px")

        self._start_auto("自动坏点", mask_hot_pixels, (image,), apply)

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
        image = self.image.copy()
        nsig = float(self.n_sigma.value())

        def apply(radial):
            if radial.shape != self.mask.shape:
                _log(self.main, "Mask 径向 nσ：图像已换，结果丢弃")
                return
            self._push_undo()
            new = radial & ~self.mask
            self.mask = combine_masks(self.mask, radial)
            self._ensure_source()[new] = SRC_RADIAL
            self._update_overlay()
            self._update_status()
            _log(self.main, f"Mask 径向 nσ（实验性）：{int(radial.sum())} px"
                           f"（n_sigma={nsig:g}）")

        self._start_auto("径向 nσ", _radial_worker, (image, center, nsig),
                         apply)

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
        self._update_status()
        _log(self.main, f"Mask 已保存：{p}（点 [生成 Mask 2D] 才会建产物）")

    def _load_mask(self):
        if self.image is None:
            QMessageBox.information(self, "先打开图像",
                                    "先打开一张代表性图像，mask 的形状要"
                                    "与积分图像一致。")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "加载 Mask", str(self._default_mask_path().parent),
            "Mask (*.npy *.edf)")
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
        # 读回来的 mask 没有来源信息，统一按“手动”（红）显示
        self._source = np.where(mask, SRC_MANUAL, 0).astype(np.uint8)
        self.mask_path = Path(path)
        self._undo.clear()
        self._update_overlay()
        self._update_status()
        _log(self.main, f"Mask 已加载：{path}（点 [生成 Mask 2D] 才会建产物）")

    def _refresh_groups(self) -> None:
        cb = getattr(self.main, "refresh_groups", None)
        if callable(cb):
            cb()

    def _wire_checked_count(self) -> None:
        """文件栏勾选一变，就更新 [应用到勾选文件（N）] 里的数字。"""
        fl = getattr(self.main, "file_list", None)
        if fl is not None:
            fl.itemChanged.connect(self._refresh_checked_count)
        self._refresh_checked_count()

    def _refresh_checked_count(self, *_args) -> None:
        try:
            n = len([s for s in gui_sources.checked_sources(self.main)
                     if s.kind == gui_sources.RAW])
        except Exception:                              # noqa: BLE001
            n = 0
        self.btn_apply_checked.setText(
            "应用到勾选文件" if n == 0 else f"应用到勾选文件（{n}）")

    def _create_masked2d(self, paths, *, label):
        made = []
        for p in paths:
            made.append(masked2d.create(p, self.mask, label=label,
                                        mask_path=self.mask_path))
        return made

    def _apply_to_main(self):
        """为当前图像生成一条 Mask 后的 2D（原图 + mask manifest）。"""
        if self.mask is None or self.image_path is None:
            return
        if not np.any(self.mask):
            _log(self.main, "Mask 为空：没有生成 Mask 后的 2D")
            self.main.status_text.setText("Mask 为空：未生成产物")
            return
        label = self.mask_path.name if self.mask_path else "未保存"
        try:
            self._create_masked2d([self.image_path], label=label)
        except Exception as err:                       # noqa: BLE001
            QMessageBox.critical(self, "生成失败", str(err))
            _log(self.main, f"Mask 2D 生成失败：{err}")
            return
        st = mask_stats(self.mask)
        self.main.status_text.setText(
            f"Mask 后的 2D 已生成：{st['masked_pixels']} px")
        _log(self.main, f"Mask 后的 2D 已生成：{Path(self.image_path).name}"
                       f"（{st['masked_pixels']} px，"
                       f"{st['fraction'] * 100:.2f}%，mask {label}）"
                       "——在文件栏「Mask 后的 2D」里选它，再点 [1D]")
        self._refresh_groups()

    def _apply_to_checked(self):
        """把当前 mask 生成到文件栏里勾选的每个原始数据上。"""
        if self.mask is None or self.image_path is None:
            return
        if not np.any(self.mask):
            _log(self.main, "Mask 为空：没有生成 Mask 后的 2D")
            return
        raw = [s for s in gui_sources.checked_sources(self.main)
               if s.kind == gui_sources.RAW]
        if not raw:
            _log(self.main, "没有勾选的原始数据：先在文件栏勾上要应用 mask 的文件")
            return
        label = self.mask_path.name if self.mask_path else "未保存"
        made = 0
        for source in raw:
            try:
                masked2d.create(source.path, self.mask, label=label,
                                mask_path=self.mask_path)
                made += 1
            except Exception as err:                   # noqa: BLE001
                _log(self.main, f"Mask 2D 生成失败：{source.display}——{err}")
        st = mask_stats(self.mask)
        self.main.status_text.setText(
            f"Mask 后的 2D 已生成 {made} 条")
        _log(self.main, f"Mask 后的 2D：{made}/{len(raw)} 个文件"
                       f"（{st['masked_pixels']} px，mask {label}）"
                       "——在文件栏「Mask 后的 2D」里选它们再点 [1D]")
        self._refresh_groups()

    def _clear(self):
        if self.mask is None:
            return
        self._push_undo()
        self.mask[:] = False
        self._ensure_source()[:] = 0
        self.mask_path = None
        self._update_overlay()
        self._update_status()

    def _generate_current(self):
        """[生成 Mask 2D]：建产物；勾着「同时预览 1D」就再画前后曲线。"""
        if self.image is None or self.mask is None:
            _log(self.main, "Mask：先载入一张图像")
            return
        if not np.any(self.mask):
            _log(self.main, "Mask 为空：没有生成 Mask 后的 2D")
            if hasattr(self.main, "status_text"):
                self.main.status_text.setText("Mask 为空：未生成产物")
            return
        self._apply_to_main()
        if self.chk_preview.isChecked():
            self._preview_1d()

    def _preview_1d(self):
        """在下方画 mask 前/后两条曲线（同步积分，大图约 1–2 s）。"""
        if self.image is None or self.mask is None:
            return
        geom = _collect_geometry(self.main)
        npt = int(self.main.params["输出点数"].value())
        self.lbl_status.setText("积分预览中…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            t0, y0 = integrate_1d(self.image, npt=npt, **geom)
            _t1, y1 = integrate_1d(self.image, npt=npt, mask=self.mask,
                                   **geom)
        except Exception as err:                       # noqa: BLE001
            QMessageBox.critical(self, "预览失败", str(err))
            return
        finally:
            QApplication.restoreOverrideCursor()
            self._update_status()
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

    def _apply_and_preview(self):
        """旧入口（测试/脚本用）：生成 + 预览，等价于勾着复选框点 [生成]。"""
        if self.image is None or self.mask is None:
            return
        self._apply_to_main()
        self._preview_1d()
