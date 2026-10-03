"""PyInstaller 打包的入口脚本（双击 .app / .exe 跑的就是它）。

和 `python -m xrd_toolkit.gui` 完全等价；单独一个文件是因为
PyInstaller 的 Analysis 要一个**脚本路径**当入口，而包里的
`gui/__main__.py` 是"模块入口"语义（import 时执行），不适合直接当
打包入口用。

`XRD_SELFTEST=1` 时不开界面、只跑打包冒烟自检（验证随包的数据文件
齐不齐；CI 的冒烟步骤与排障都用它，报告另存一份到用户数据目录），
见 xrd_toolkit/selftest.py。
"""
import os
import sys


def _selftest() -> int:
    from xrd_toolkit.selftest import run
    return run()


if __name__ == "__main__":
    if os.environ.get("XRD_SELFTEST") == "1":
        sys.exit(_selftest())
    from xrd_toolkit.gui.app import main
    sys.exit(main())
