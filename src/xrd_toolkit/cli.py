"""所有命令行脚本共用的交互菜单。

view_diffraction / calibrate_integrate / integrate_pattern / sector_waterfall
四个脚本都支持两种方式选文件：
    方式一：--file data/xxx.tif 指定一个文件（原用法，兼容不变）
    方式二：不带 --file 运行 → 列出 data/ 里的文件，按编号选一个或多个

菜单逻辑只有这一份：四个脚本都从这里 import，
以后要改菜单行为（比如加过滤规则）只改这一个文件。
"""
from pathlib import Path


# 支持的衍射数据扩展名（交互菜单只列出这些格式的文件）。
# 集合 set：花括号 {} 包起来的一堆值，特点是"查找快、不重复"。
SUPPORTED_EXTS = {".tif", ".tiff", ".edf", ".cbf"}


def interactive_pick_files(datadir: Path) -> list[Path]:
    """交互式选择要跑的文件：列出 datadir 里的数据文件，让用户按编号选.

    返回选中的文件路径列表。用户可输入：
        2       → 只跑第 2 个
        1,3     → 跑第 1 和第 3 个
        all     → 全部跑
    """
    # iterdir() 逐个列出文件夹里的条目；suffix 是扩展名（含点），.lower() 转小写
    # （防止 .TIF 大写被漏掉）；sorted 按名字排序，让编号顺序稳定
    files = sorted(p for p in datadir.iterdir() if p.suffix.lower() in SUPPORTED_EXTS)

    if not files:
        print(f"在 {datadir}/ 里没有找到数据文件（支持 {', '.join(sorted(SUPPORTED_EXTS))}）")
        raise SystemExit(1)  # 没有可跑的文件，直接退出程序（退出码 1 = 出错）

    print(f"{datadir}/ 里找到 {len(files)} 个数据文件：")
    # enumerate(..., start=1)：给文件编号，从 1 开始
    # （人习惯从 1 数，程序内部习惯从 0 数，这里在"给人看"的环节用 1）
    for i, p in enumerate(files, start=1):
        print(f"  [{i}] {p.name}")

    # while True = 无限循环，直到拿到合法输入才 return 跳出
    while True:
        raw = input("输入要跑的编号（如 1,2 或 all）: ").strip()
        if raw.lower() == "all":
            return files
        try:
            picks = []
            # replace("，", ",")：把中文逗号换成英文逗号，两种写法都能认
            for part in raw.replace("，", ",").split(","):
                part = part.strip()
                if not part:
                    continue  # 跳过空段（比如输入 "1,," 里的空）
                n = int(part)
                if not 1 <= n <= len(files):
                    raise ValueError  # 编号超出范围 → 抛错，跳到 except
                picks.append(files[n - 1])  # 人的编号 1 → 列表下标 0
            if picks:
                return picks
        except ValueError:
            pass  # 非法输入：走不到 return，落到下面的提示，再问一遍
        print(f"输入不合法，请输入 1~{len(files)} 的编号（多个用逗号隔开）或 all")
