#!/usr/bin/env python3
"""生成应用图标（0.1 试用版，2026-10-03）。

图案 = 一张"衍射环"：深色圆角底 + 同心环 + 亮斑（束心/最强峰）。
纯 Pillow 画，无外部素材；一次生成三份：

    packaging/icon.png    512×512 母版（进 git，给 README / Linux 用）
    packaging/icon.icns   macOS 打包用（需要 iconutil，只有 macOS 上能出）
    packaging/icon.ico    Windows 打包用（Pillow 直接写）

超采样画 2048 再缩到目标尺寸（抗锯齿）；icns 靠 iconutil 从 iconset
拼装——非 macOS 机器会跳过 icns（已生成的那份留在仓库里，CI 直接用）。

用法：python packaging/make_icon.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
SS = 2048                       # 超采样画布
BG = (14, 26, 46, 255)          # 深海军蓝（和界面深色主题一个色系）
RING = (168, 205, 255)          # 冷白蓝（衍射环）
SPOT = (255, 214, 140)          # 亮斑（暖色，和冷底对比）


def _rounded_mask(size: int, pad: int, radius: int) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle(
        [pad, pad, size - pad, size - pad], radius=radius, fill=255)
    return m


def draw_master() -> Image.Image:
    """画母版（SS×SS，RGBA）。"""
    img = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    pad = int(SS * 0.075)       # 留出 macOS 图标的标准边距
    radius = int(SS * 0.185)
    mask = _rounded_mask(SS, pad, radius)

    # 底层：深色圆角块（带一点点纵向渐变，免得太死板）
    base = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    bd = ImageDraw.Draw(base)
    for y in range(SS):
        t = y / SS
        col = (int(14 + 10 * t), int(26 + 14 * t), int(46 + 22 * t), 255)
        bd.line([(0, y), (SS, y)], fill=col)
    img.paste(base, (0, 0), mask)

    # 衍射环：圆心略偏（像真实的束心），半径、粗细、亮度各不同
    cx, cy = SS * 0.47, SS * 0.50
    rings = [                    # (半径系数, 线宽系数, 透明度)
        (0.130, 0.016, 150),
        (0.235, 0.022, 235),
        (0.330, 0.014, 120),
        (0.435, 0.018, 165),
        (0.545, 0.012, 95),
        (0.660, 0.015, 125),
        (0.780, 0.010, 70),
    ]
    layer = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    for rf, wf, alpha in rings:
        r = SS * rf
        w = max(2, int(SS * wf))
        ld.ellipse([cx - r, cy - r, cx + r, cy + r],
                   outline=RING + (alpha,), width=w)
    # 环只画在圆角块里
    img.paste(layer, (0, 0), Image.composite(layer.split()[3], Image.new(
        "L", (SS, SS), 0), mask))

    # 束心：小暗圆（挡掉正中心，像打了 beam stop）
    r0 = SS * 0.055
    ImageDraw.Draw(img).ellipse(
        [cx - r0, cy - r0, cx + r0, cy + r0], fill=(10, 18, 32, 255))

    # 亮斑：落在第二条环上（最强峰），带一点光晕
    r_spot = SS * 0.235
    sx, sy = cx + r_spot * 0.71, cy - r_spot * 0.71
    halo = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    ImageDraw.Draw(halo).ellipse(
        [sx - 60, sy - 60, sx + 60, sy + 60], fill=SPOT + (120,))
    halo = halo.filter(ImageFilter.GaussianBlur(28))
    img.alpha_composite(halo)
    d = ImageDraw.Draw(img)
    d.ellipse([sx - 26, sy - 26, sx + 26, sy + 26], fill=SPOT + (255,))

    return img


def main() -> int:
    master = draw_master()
    png512 = master.resize((512, 512), Image.LANCZOS)
    png512.save(HERE / "icon.png")
    print(f"✓ {HERE / 'icon.png'} (512×512)")

    # .ico：Pillow 直接写多尺寸
    sizes = [16, 24, 32, 48, 64, 128, 256]
    master.resize((256, 256), Image.LANCZOS).save(
        HERE / "icon.ico", sizes=[(s, s) for s in sizes])
    print(f"✓ {HERE / 'icon.ico'} ({'/'.join(map(str, sizes))})")

    # .icns：iconutil 只认 macOS；其它平台跳过（仓库里已有的那份照用）
    if sys.platform != "darwin" or shutil.which("iconutil") is None:
        print("· 非 macOS（或没有 iconutil）：跳过 icon.icns")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "icon.iconset"
        iconset.mkdir()
        for s in (16, 32, 128, 256, 512):
            master.resize((s, s), Image.LANCZOS).save(
                iconset / f"icon_{s}x{s}.png")
            master.resize((s * 2, s * 2), Image.LANCZOS).save(
                iconset / f"icon_{s}x{s}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset),
                        "-o", str(HERE / "icon.icns")], check=True)
    print(f"✓ {HERE / 'icon.icns'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
