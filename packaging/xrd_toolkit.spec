# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（0.1 试用版，2026-10-03）。

产物（onedir）：
    macOS   → dist/XRD Toolkit.app（未签名：首次打开要"右键 → 打开"，
              写进试用指南；macOS 13 起，PySide6 6.11 的下限）
    Windows → dist/XRD Toolkit/（整个文件夹压 zip 发，里面的
              "XRD Toolkit.exe" 双击即用）

为什么 onedir 不用 onefile：onefile 每次启动都要把 ~1 GB 解包到临时
目录——启动慢十几秒、杀毒软件也更容易误报。"打不开"是试用版最怕的
死法，体积换稳定，值。

必须随包的"非代码"物（少一样坏一样，全是打包前审计出来的）：
  * pyFAI/resources/calibration/*.D —— LaB₆ 理论环数据；缺了
    get_calibrant("LaB6") 抛 BadCalibrantName，校准功能整个不可用
  * matplotlib 的 mpl-data —— 除自绘工具栏图标用的 4 个 PNG 之外，
    还有 DejaVu Sans 字体（rcParams 的第一选择）
  * PySide6 的 qtbase_zh_CN.qm —— 标准按钮（确定/取消/是/否）的中文
    翻译；缺了静默退回英文
  * fabio 全部格式编解码器 —— 它是 importlib 按名单动态注册的，
    PyInstaller 的静态分析找不到（tif/edf/cbf 全靠它读）
  * pyFAI.ext.*（cython 扩展）—— 缺了积分会静默退回 numpy 实现
    （功能在、慢几倍，最难查的那种"坏"）

构建（在仓库根跑；dist/ 与 build/ 已 gitignore）：
    pip install -c packaging/constraints.txt . pyinstaller
    pyinstaller packaging/xrd_toolkit.spec --noconfirm
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import (collect_data_files,
                                     collect_submodules)

HERE = Path(SPECPATH)              # packaging/（PyInstaller 注入的全局）
ROOT = HERE.parent                 # 仓库根
MAC = sys.platform == "darwin"
VERSION = "0.1.0"

datas = []
# ① pyFAI 的整棵 resources/（≈1.8 MB，全收不亏）：
#    calibration/  LaB₆ 等标样理论环（校准页的命根子）
#    sensors/      传感器材料表——**第一次打包就栽在这**：detectors 的
#                  import 链在启动时就读它，缺了直接 FileNotFoundError
#                  （自检在冻结包里当场逮到，见 selftest.py）
#    elements/     散射因子表（后续功能会用）
#    gui/、openCL/ 前端与 GPU 内核（本应用不用，但小，留着免踩）
datas += collect_data_files("pyFAI",
                            includes=["resources/*/*", "resources/*"])
# ② matplotlib 数据面：自绘工具栏的 4 个图标 PNG + DejaVu 字体
datas += collect_data_files(
    "matplotlib",
    includes=["mpl-data/images/*.png",
              "mpl-data/fonts/ttf/DejaVuSans*.ttf"])
# ③ Qt 标准按钮的中文翻译
datas += collect_data_files("PySide6", includes=["translations/qtbase_zh_CN.qm"])

hiddenimports = []
hiddenimports += collect_submodules("fabio")        # 动态注册的格式表
hiddenimports += collect_submodules("pyFAI.ext")    # cython 加速模块
hiddenimports += ["silx.image.marchingsquares"]     # pyFAI.goniometer 的硬依赖

# 明确不要的：装不下也用不到的大件/开发件（tkinter 是 PyInstaller 经典
# 误收项；tkagg 后端我们不选——matplotlib.use("qtagg") 钉死了）
excludes = ["tkinter", "PyQt5", "PyQt6", "PySide2", "IPython",
            "matplotlib.backends._backend_tk"]

a = Analysis(
    [str(HERE / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

icon = str(HERE / ("icon.icns" if MAC else "icon.ico"))
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="XRD Toolkit",
    debug=False,
    strip=False,
    upx=False,               # UPX 压出来的 exe 是杀毒误报重灾区，别开
    console=False,           # 双击不弹黑窗口
    icon=icon,
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name="XRD Toolkit",
)

if MAC:
    app = BUNDLE(
        coll,
        name="XRD Toolkit.app",
        icon=str(HERE / "icon.icns"),
        bundle_identifier="io.github.xyyzzc3.xrd-toolkit",
        info_plist={
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            # PySide6 6.11 的轮子下限就是 macOS 13（见 constraints）
            "LSMinimumSystemVersion": "13.0",
            "NSHumanReadableCopyright":
                "MIT License · 2026 Chenze Bian",
        },
    )
