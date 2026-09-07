# XRD Toolkit

XRD 衍射图像处理工具箱：读取 .tif / .edf / .cbf 衍射数据，绘制二维衍射图与过圆心的强度剖面。

## 安装

```bash
conda activate XRD_Toolkit_Environment
pip install -e .
```

## 使用

```bash
python scripts/view_diffraction.py --file data/xxx.tif --center 1020,1024 --angle 0 --outdir outputs
```

参数说明：
- `--file`：衍射图像路径（.tif / .edf / .cbf）
- `--center`：环圆心坐标 cx,cy，不填则默认图像几何中心
- `--angle`：剖面线与水平方向的夹角（度），默认 0
- `--outdir`：PNG 输出目录，默认 outputs/

## 项目结构

```
.
├── data/                       # XRD 原始数据（.tif，不进 git）
├── outputs/                    # 生成的示例图（PNG）
├── scripts/
│   └── view_diffraction.py     # 衍射图查看器（画图 + 线剖面）
├── src/xrd_toolkit/
│   ├── main.py                 # 程序入口
│   ├── config.py               # 全局配置
│   ├── core/processor.py       # 图像计算（线剖面等）
│   ├── services/data_loader.py # 数据读取（fabio）
│   └── utils/                  # 工具函数
├── tests/                      # 测试
├── pyproject.toml              # 项目元信息与依赖
├── README.md                   # 项目说明
└── WEEKLY_REPORT.md            # 周报
```

## 输出示例

二维衍射图（对数色标，红色十字为环圆心）：

<img src="outputs/LMFP_1_atten0-00029_copy_image.png" width="60%">

过圆心的强度剖面（横轴为到圆心的距离，单位像素）：

<img src="outputs/LMFP_1_atten0-00029_copy_profile.png" width="60%">
