"""项目路径的唯一出处（2026-10-02；打包适配 2026-10-03）。

以前 GUI 的导出 / 存图 / .poni / 看门狗用**相对路径** `Path("outputs")`
——相对谁？看进程的工作目录。用户机器上把 PyCharm 运行配置的工作目录设成
`src/xrd_toolkit/gui` 跑了一次批量导出，文件就落进了源码树里，磁盘上真的
出现了两个 outputs（2026-10-02 用户报告）。现在一律从这里取：路径由代码
位置推算，"从哪启动都对"；测试/脚本也能用 XRD_OUTPUTS 整体换到临时目录
（与 stage_cache 的 XRD_STAGE_CACHE、recipes 的 XRD_RECIPES 同一套规矩）。

打包（PyInstaller，2026-10-03）后又多一层：`__file__` 落在 .app / 安装
目录**内部**——只读、升级即换，绝不能当用户数据位置。所以分两支：
没打包 = 现状（仓库根/outputs，测试与脚本全靠它）；打包后 = "用户找得到、
写得进"的地方（见 default_outputs_dir 里的选择理由）。
"""
import os
import sys
from pathlib import Path

# 是否跑在 PyInstaller 打出来的包里（源码直跑 / 测试 / 脚本都是 False）
FROZEN = bool(getattr(sys, "frozen", False))

# src/xrd_toolkit/paths.py → 仓库根（stage_cache 的 parents[3] 是同一处，
# 它现在也改从这里取，别再多算一份）。**只有开发模式拿它当数据位置**。
ROOT = Path(__file__).resolve().parents[2]

# 打包后的用户数据目录名（导出、产物缓存、配方、崩溃现场都住里面）
APP_DIR_NAME = "XRD_Toolkit"


def default_outputs_dir(frozen: bool = None, platform: str = None,
                        home: Path = None) -> Path:
    """产物根目录的默认值（纯函数——三个分支可直接单测）。

    开发模式：仓库根/outputs——现状一个字不改（测试全按它写的）。
    打包后：
      macOS   → ~/XRD_Toolkit——**故意不进"文档"**：一是 Documents 属于
                macOS 的 TCC 保护目录，未签名应用会多弹一次系统授权；
               二是用户若开了"桌面与文档"iCloud 同步，实验数据会被传上
               云——数据不出本机是这个项目的底线。个人文件夹是访达
               第一屏，告诉客户"打开你的个人文件夹"就够了。
      Windows → ~/Documents/XRD_Toolkit——没有 TCC 这回事，"文档"就是
                大家找文件的地方。
      其它    → ~/XRD_Toolkit（照 macOS）。
    导出弹窗里显示的是**完整绝对路径**（plot_export 的目录框），客户
    一眼能看到文件会去哪。
    """
    frozen = FROZEN if frozen is None else frozen
    platform = sys.platform if platform is None else platform
    home = Path.home() if home is None else home
    if not frozen:
        return ROOT / "outputs"
    if platform == "win32":
        return home / "Documents" / APP_DIR_NAME
    return home / APP_DIR_NAME


# 数据导出、图片保存、看门狗现场、.poni 默认都落这里
OUTPUTS_DIR = Path(os.environ.get("XRD_OUTPUTS", default_outputs_dir()))
