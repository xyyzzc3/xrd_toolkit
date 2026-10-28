# XRD 衍射图像读取：把仪器输出文件读成 numpy 数组。
import numpy as np
import fabio


def load_diffraction_image(path: str) -> np.ndarray:
    """
    读取衍射图像文件（.edf / .tif / .cbf 等），返回 2D numpy 强度数组。

    也认 **mask 后的 2D 产物**（`.mask2d.npz`）：读到 manifest 时返回
    `np.ma.MaskedArray`（原始像素 + 它自己的 mask），下游积分器自动
    排除被遮像素。原始像素不做任何改写。

    参数：
        path : str
            文件路径，比如 "data/xxx.tif"，或 masked 2D manifest

    返回：
        np.ndarray（原始图） / np.ma.MaskedArray（masked 2D）
            2D 数组，image[行][列] = 该像素的强度（浮点数）
            （行 = 探测器纵向，列 = 探测器横向）

    备注：
        fabio 是衍射领域标准读取库（pyFAI 生态），按文件内容自动
        识别格式（.edf / .tif / .cbf）。
    """
    if str(path).lower().endswith(".mask2d.npz"):
        from xrd_toolkit.services import masked2d
        raw_path, mask, _meta = masked2d.load_manifest(path)
        base = load_diffraction_image(raw_path)
        if base.shape != mask.shape:
            raise ValueError(
                f"masked 2D 的 mask 形状 {mask.shape} 与原始图 "
                f"{base.shape} 不一致：{path}")
        return np.ma.array(base, mask=mask)
    image = fabio.open(path)

    # 转成浮点数，防止后续整数除法截断小数
    return image.data.astype(np.float64)
