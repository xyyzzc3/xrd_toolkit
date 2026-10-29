"""显示用的位图小工具：块内取最大的降采样。

matplotlib 的绘制成本按**数据像素数**算（2048² 一帧约 0.6 s），所以 2D
面板、Mask 编辑器这类要显示大图的地方，先降采样再交给 imshow。块内取
最大而不是平均：孤立亮斑/坏点才不会被摊平；对 bool 掩膜来说 max 就是
「块里有任一像素被标记」，漏不掉小笔画。

实现用 ds×ds 个错位切片取 maximum：2048² 上约 2 ms；reshape 后
`max(axis=(1, 3))` 要 60 ms（跨步归约的坑），别换回去。
"""
import numpy as np


def downsample_max(img, ds):
    """块内取最大降采样（ds <= 1 原样返回）。

    边缘不足一块时按 edge 补齐，所以返回形状是 ceil(h/ds) × ceil(w/ds)；
    显示端用 extent = (…, ww * ds, …, hh * ds) 把块心对回原像素中心。
    """
    if ds <= 1:
        return img
    h, w = img.shape
    ph, pw = (-h) % ds, (-w) % ds
    a = np.pad(img, ((0, ph), (0, pw)), mode="edge") if (ph or pw) else img
    out = None
    for r in range(ds):
        for c in range(ds):
            blk = a[r::ds, c::ds]
            if out is None:
                out = blk.copy()
            else:
                np.fmax(out, blk, out=out)   # fmax：NaN 不扩散到整块
    return out
