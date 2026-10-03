#!/usr/bin/env python3
"""生成应用图标（0.1 试用版，2026-10-03）。

设计（用户 2026-10-03 定稿：融入字母 C、配色要科技感）：

  * **字母 C 即衍射环**——粗笔画的开口环，一眼是一个 C，同时就是那枚
    "衍射环"；环心再套三道细弧（环组 = 衍射，也像层层叠叠的产物）。
    C 的开口朝右，是束流进出的方向。
  * **科技感配色**——近黑深底 + 中央径向辉光；笔画走**青→蓝→紫**渐变，
    外加同色发光（暗场 + 光，是这类工具图标的通行语言）。

一次生成三份（全部提交进仓库，CI 直接用）：
    packaging/icon.png    512×512 母版（README / Linux 用）
    packaging/icon.icns   macOS 打包用（需要 iconutil，只有 macOS 上能出）
    packaging/icon.ico    Windows 打包用（Pillow 直接写）

超采样 2048 再缩（抗锯齿）；icns 靠 iconutil 从 iconset 拼装——非 macOS
机器会跳过 icns（仓库里那份照用）。

用法：python packaging/make_icon.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
S = 2048                        # 超采样画布
PAD = int(S * 0.075)            # macOS 图标标准边距
RADIUS = int(S * 0.185)

# 配色：青 → 蓝 → 紫（描边渐变）；暖色只留一颗"束心"备用
CYAN = (34, 211, 238)
BLUE = (59, 130, 246)
VIOLET = (139, 92, 246)
WHITE = (240, 250, 255)
BG = (9, 15, 28)                # 近黑深蓝（与界面深色主题同族）


def _lerp(a, b, t):
    return tuple(int(x + (y - x) * t) for x, y in zip(a, b))


def _ramp(t: float):
    """0→青，0.5→蓝，1→紫（沿弧长走一圈渐变）。"""
    return _lerp(CYAN, BLUE, t * 2) if t < 0.5 else _lerp(
        BLUE, VIOLET, (t - 0.5) * 2)


def _base() -> Image.Image:
    """近黑底 + 中央径向辉光；把圆角遮罩挂在 _mask 上供裁切。"""
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [PAD, PAD, S - PAD, S - PAD], radius=RADIUS, fill=255)
    img.paste(BG + (255,), (0, 0), mask)
    halo = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    for r, a in ((int(S * 0.42), 40), (int(S * 0.30), 40),
                 (int(S * 0.18), 40)):
        hd.ellipse([S / 2 - r, S / 2 - r, S / 2 + r, S / 2 + r],
                   fill=(40, 90, 200, a))
    halo = halo.filter(ImageFilter.GaussianBlur(80))
    img.alpha_composite(Image.composite(
        halo, Image.new("RGBA", (S, S), (0, 0, 0, 0)), mask))
    img._mask = mask
    return img


def _paste(img: Image.Image, layer: Image.Image) -> None:
    img.alpha_composite(Image.composite(
        layer, Image.new("RGBA", (S, S), (0, 0, 0, 0)), img._mask))


def _glow(layer: Image.Image, blur: int = 42) -> Image.Image:
    """外发光：模糊一份垫在清晰笔画下面。"""
    g = layer.copy().filter(ImageFilter.GaussianBlur(blur))
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.alpha_composite(g)
    out.alpha_composite(layer)
    out.alpha_composite(g.point(lambda v: int(v * 0.9)))
    return out


def _gradient_arc(layer: Image.Image, c, r, w, a0, a1, alpha=255, seg=96):
    """渐变圆弧：PIL 不能直接画渐变描边 → 分 96 段逐段取色。"""
    d = ImageDraw.Draw(layer)
    for i in range(seg):
        t0, t1 = i / seg, (i + 1) / seg
        col = _ramp((t0 + t1) / 2)
        d.arc([c[0] - r, c[1] - r, c[0] + r, c[1] + r],
              start=a0 + (a1 - a0) * t0 - 1, end=a0 + (a1 - a0) * t1 + 1,
              fill=col + (alpha,), width=w)


def draw_master() -> Image.Image:
    """母版：粗体 C（= 衍射环）+ 字腔里的三道细环。"""
    img = _base()
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    c = (int(S * 0.515), int(S * 0.5))
    R = int(S * 0.30)
    wall = int(S * 0.105)               # 厚壁 = 字形分量
    _gradient_arc(layer, c, R - wall // 2, wall, 40, 320)
    d = ImageDraw.Draw(layer)
    for i, rr in enumerate((int(S * 0.135), int(S * 0.185),
                            int(S * 0.235))):
        d.arc([c[0] - rr, c[1] - rr, c[0] + rr, c[1] + rr],
              start=55, end=305,
              fill=_ramp(0.15 + i * 0.3) + (200 - i * 35,),
              width=int(S * 0.013))
    _paste(img, _glow(layer, blur=48))
    return img


def main() -> int:
    master = draw_master()
    png512 = master.resize((512, 512), Image.LANCZOS)
    png512.save(HERE / "icon.png")
    print(f"✓ {HERE / 'icon.png'} (512×512)")

    sizes = [16, 24, 32, 48, 64, 128, 256]
    master.resize((256, 256), Image.LANCZOS).save(
        HERE / "icon.ico", sizes=[(s, s) for s in sizes])
    print(f"✓ {HERE / 'icon.ico'} ({'/'.join(map(str, sizes))})")

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
