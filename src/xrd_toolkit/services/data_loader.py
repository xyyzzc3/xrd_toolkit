# XRD 衍射图像读取：把仪器输出文件读成 numpy 数组。
import numpy as np
import fabio


def load_diffraction_image(path: str) -> np.ndarray:
    """
    读取衍射图像文件（.edf / .tif / .cbf 等），返回 2D numpy 强度数组。

    参数：
        path : str
            文件路径，比如 "data/week2_lab6.tif"

    返回：
        np.ndarray
            2D numpy 数组，image[行][列] = 该像素的强度（浮点数）
            （行 = 探测器纵向，列 = 探测器横向）

    备注：
        fabio 是衍射领域的标准读取库（pyFAI 生态），打开文件时
        自己判断格式，我们不用管文件是 .edf 还是 .tif。
    """
    image = fabio.open(path)

    # 把整数数组转成浮点数，防止后续整数除法截断小数
    return image.data.astype(np.float64)
