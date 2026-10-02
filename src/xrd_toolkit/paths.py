"""项目路径的唯一出处（2026-10-02）。

以前 GUI 的导出 / 存图 / .poni / 看门狗用**相对路径** `Path("outputs")`
——相对谁？看进程的工作目录。用户机器上把 PyCharm 运行配置的工作目录设成
`src/xrd_toolkit/gui` 跑了一次批量导出，文件就落进了源码树里，磁盘上真的
出现了两个 outputs（2026-10-02 用户报告）。现在一律从这里取：路径由代码
位置推算，"从哪启动都对"；测试/脚本也能用 XRD_OUTPUTS 整体换到临时目录
（与 stage_cache 的 XRD_STAGE_CACHE、recipes 的 XRD_RECIPES 同一套规矩）。
"""
import os
from pathlib import Path

# src/xrd_toolkit/paths.py → 仓库根（stage_cache 的 parents[3] 是同一处，
# 它现在也改从这里取，别再多算一份）
ROOT = Path(__file__).resolve().parents[2]

# 数据导出、图片保存、看门狗现场、.poni 默认都落这里
OUTPUTS_DIR = Path(os.environ.get("XRD_OUTPUTS", ROOT / "outputs"))
