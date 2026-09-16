"""python -m xrd_toolkit.gui 的入口：转发给 app.main()。

`python -m 包名` 会执行该包的 __main__.py，这样启动 GUI 就
不需要写清楚文件路径，PyCharm 里也方便配置 Run。
"""
import sys

from xrd_toolkit.gui.app import main

sys.exit(main())
