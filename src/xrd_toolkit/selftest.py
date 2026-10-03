r"""打包冒烟自检：在**打包出来的**应用里验证"随包数据"是否完整（2026-10-03）。

为什么需要它：冻结环境坏得最隐蔽的不是 Python 代码，而是随包的数据
文件——fabio 的格式编解码器（importlib 按名单动态注册，静态分析收不全
就一个格式都读不了）、pyFAI 的 LaB₆ 理论环数据（缺了校准页直接报错）、
matplotlib 的 mpl-data（工具栏图标消失）。这些在开发机上永远测不出来，
到了客户手里就是"打不开文件"这种没法自查的坏法。

自检不依赖任何外部数据：现画一条合成衍射环 → 存成 TIFF → 用应用自己
的读图函数读回来 → pyFAI 积分 → 检查峰位与积分引擎。任何一条链路断了
都以非 0 退出，并把报告同时写进 <用户数据目录>/selftest.txt（Windows
的窗口版 exe 没有控制台，文件是唯一能看到的通道）。

用法（打包后；CI 的冒烟步骤就是它）：
    XRD_SELFTEST=1 "dist/XRD Toolkit.app/Contents/MacOS/XRD Toolkit"
    set XRD_SELFTEST=1 && dist\XRD Toolkit\XRD Toolkit.exe
"""
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

_LINES = []


def _report(ok: bool, name: str, detail: str = "") -> bool:
    line = f"[{'OK ' if ok else 'FAIL'}] {name}" + (f"（{detail}）" if detail
                                                    else "")
    _LINES.append(line)
    print(line, flush=True)
    return ok


def _ring_image(dist_m: float, pixel_m: float, tth_deg: list) -> np.ndarray:
    """合成一张带已知衍射环的图像（峰位按几何反推：r = dist·tan2θ）。"""
    size = 512
    y, x = np.mgrid[0:size, 0:size].astype(float)
    c = (size - 1) / 2.0
    r_px = np.hypot(y - c, x - c)
    img = np.full((size, size), 10.0)
    for tth in tth_deg:
        r0 = dist_m * np.tan(np.deg2rad(tth)) / pixel_m
        img += 800.0 * np.exp(-0.5 * ((r_px - r0) / 4.0) ** 2)
    return img


def run() -> int:
    """跑全部自检；返回退出码（0 = 全过）。"""
    from xrd_toolkit import paths
    from xrd_toolkit.services import integrator
    from xrd_toolkit.services.data_loader import load_diffraction_image

    _report(True, "启动", f"打包模式={paths.FROZEN}，数据目录={paths.OUTPUTS_DIR}")

    # ① fabio 读图链路：写一张 TIFF 再读回来（动态编解码器缺了就死在这）
    dist_m, pixel_m = 0.15, 100e-6
    tth = [5.0, 10.0]
    image = _ring_image(dist_m, pixel_m, tth)
    with tempfile.TemporaryDirectory() as tmp:
        tif = Path(tmp) / "smoke.tif"
        try:
            from fabio.tifimage import TifImage
            TifImage(data=image.astype(np.float32)).save(str(tif))
        except Exception as err:                        # noqa: BLE001
            _report(False, "fabio 写 TIFF", repr(err))
            return _finish()
        try:
            back = load_diffraction_image(str(tif))
        except Exception as err:                        # noqa: BLE001
            _report(False, "fabio 读 TIFF（格式编解码器）", repr(err))
            return _finish()
        _report(back.shape == image.shape and np.isfinite(back).all(),
                "fabio 读写 TIFF（往返一致）", f"{back.shape}")

    # ② pyFAI 积分链路：峰位该落在 5°（合成环按几何反推的）。
    # 像素→2θ 只走 r = L·tan2θ，与波长无关，所以波长随便给一个有效值
    curve = integrator.integrate_1d(
        back, pixel_m, 1.0e-10, dist_m,
        (image.shape[0] - 1) / 2 * pixel_m,
        (image.shape[1] - 1) / 2 * pixel_m, 0.0, 0.0,
        npt=1000, tth_min_deg=3.0, tth_max_deg=7.0)
    tth_axis = np.asarray(curve[0], dtype=float)
    intensity = np.asarray(curve[1], dtype=float)
    peak = float(tth_axis[int(np.nanargmax(intensity))])
    _report(abs(peak - 5.0) < 0.3, "pyFAI 积分（合成环峰位）",
            f"峰在 {peak:.2f}°（预期 5.00°）")

    backend = integrator.integration_backend()
    _report(backend != "numpy", "积分引擎（cython 加速在不在）",
            f"实际用：{backend}")

    # ③ 标样数据：LaB₆ 理论环（校准页的命根子）
    try:
        from pyFAI.calibrant import get_calibrant
        n_rings = len(get_calibrant("LaB6").dspacing)
        _report(n_rings >= 10, "pyFAI 标样数据（LaB₆ 理论环）",
                f"{n_rings} 个环")
    except Exception as err:                            # noqa: BLE001
        _report(False, "pyFAI 标样数据（LaB₆ 理论环）", repr(err))

    # ④ matplotlib 数据：自绘工具栏用的 4 个图标（少一个图标就白板）
    from matplotlib import get_data_path
    icons = Path(get_data_path()) / "images"
    have = [n for n in ("home.png", "zoom_to_rect.png",
                        "qt4_editor_options.png", "filesave.png")
            if (icons / n).exists()]
    _report(len(have) == 4, "matplotlib 图标数据", f"{len(have)}/4")

    # ⑤ Qt 中文翻译（标准按钮"确定/取消"靠它）
    from PySide6.QtCore import QLibraryInfo
    tr = Path(QLibraryInfo.path(QLibraryInfo.TranslationsPath)) \
        / "qtbase_zh_CN.qm"
    _report(tr.exists(), "Qt 中文翻译文件", str(tr))

    return _finish()


def _finish() -> int:
    """写报告文件并给退出码。"""
    from xrd_toolkit import paths
    failed = [ln for ln in _LINES if ln.startswith("[FAIL]")]
    tail = f"\n== {'全过' if not failed else '有失败'}（{len(_LINES)} 项）=="
    _LINES.append(tail)
    print(tail, flush=True)
    try:
        out = Path(paths.OUTPUTS_DIR)
        out.mkdir(parents=True, exist_ok=True)
        (out / "selftest.txt").write_text("\n".join(_LINES) + "\n",
                                          encoding="utf-8")
    except OSError as err:
        print(f"（报告写盘失败：{err}）", file=sys.stderr)
    return 1 if failed else 0
