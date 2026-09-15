"""四个命令行脚本共用的交互菜单。

view_diffraction / calibrate_integrate / integrate_pattern / sector_waterfall
均支持两种文件选择方式：
    方式一：--file data/xxx.tif 指定单个文件
    方式二：不带 --file 运行，列出 data/ 中的文件按编号选择

菜单逻辑集中在本文件，四个脚本共用；修改菜单行为只需改这里。
"""
from pathlib import Path


# 支持的衍射数据扩展名（交互菜单只列出这些格式的文件；集合保证不重复）
SUPPORTED_EXTS = {".tif", ".tiff", ".edf", ".cbf"}


def interactive_pick_files(datadir: Path) -> list[Path]:
    """交互式选择要跑的文件：列出 datadir 里的数据文件，让用户按编号选.

    返回选中的文件路径列表。用户可输入：
        2       → 只跑第 2 个
        1,3     → 跑第 1 和第 3 个
        all     → 全部跑
    """
    # 目录不存在时给出友好提示（而不是 FileNotFoundError 的 traceback）
    if not datadir.is_dir():
        print(f"Data directory not found: {datadir}/ "
              f"(create it and put .tif/.edf/.cbf files inside)")
        raise SystemExit(1)

    # 遍历目录并筛选支持扩展名的文件：suffix 为扩展名（含点），
    # .lower() 兼容大写后缀；sorted 保证编号顺序稳定
    files = sorted(p for p in datadir.iterdir() if p.suffix.lower() in SUPPORTED_EXTS)

    if not files:
        print(f"No data files found in {datadir}/ (supported: {', '.join(sorted(SUPPORTED_EXTS))})")
        raise SystemExit(1)  # 无可处理的文件，退出（退出码 1）

    print(f"Found {len(files)} data file(s) in {datadir}/:")
    # enumerate(..., start=1)：编号从 1 开始（用户习惯），程序内部下标从 0
    for i, p in enumerate(files, start=1):
        print(f"  [{i}] {p.name}")

    # 循环直到输入合法
    while True:
        raw = input("Enter number(s) to run (e.g. 1,2 or all): ").strip()
        if raw.lower() == "all":
            return files
        try:
            picks = []
            # 按英文逗号切分编号
            for part in raw.split(","):
                part = part.strip()
                if not part:
                    continue  # 跳过空段（如 "1,,"）
                n = int(part)
                if not 1 <= n <= len(files):
                    raise ValueError  # 编号超出范围，由下方 except 处理
                picks.append(files[n - 1])  # 用户编号 1 → 下标 0
            if picks:
                return picks
        except ValueError:
            pass  # 非法输入：落入下方提示后重试
        print(f"Invalid input; enter 1~{len(files)} (comma-separated for several) or all")


def pick_config(configs: dict, default: str) -> str:
    """交互式选择一个几何配置条目：列出 CONFIGS 中的所有条目，按编号选.

    在交互选完数据文件之后调用。用户可输入：
        直接回车   → 使用默认条目（DEFAULT_CONFIG）
        2          → 选择第 2 个
        lmfp2_lab6 → 直接输入条目 key 名
    """
    names = list(configs)
    print(f"\nAvailable geometry configs ({len(names)}):")
    # enumerate(..., start=1)：编号从 1 开始（同 interactive_pick_files）
    for i, name in enumerate(names, start=1):
        mark = " (default)" if name == default else ""
        print(f"  [{i}] {name}{mark} — {configs[name]['label']}")

    # 循环直到输入合法
    while True:
        raw = input(f"Enter config number or name (default: {default}): ").strip()
        if not raw:
            return default                        # 直接回车 = 默认条目
        if raw in configs:
            return raw                            # 直接输入 key 名
        try:
            n = int(raw)
            if 1 <= n <= len(names):
                return names[n - 1]               # 用户编号 1 → 下标 0
        except ValueError:
            pass
        print(f"Invalid input; enter 1~{len(names)}, a config name, "
              f"or press Enter for the default")


def parse_range_arg(raw):
    """解析 --range 参数（三个脚本共用）：full / auto / lo,hi（度）。

    返回三种形式：
        "full"      → 完整数据（txt 母版永远存这一版）
        "auto"      → 自动选区（默认）：下界=材料专属标准（lmfp 第一峰
                      −0.3°；lab6 光环结束点−0.6°，同一材料所有数据相同；
                      未知则缓坡检测兜底）；
                      上界=数据失效点自动检测（找不到才退回完整环极限）
        (lo, hi)    → 手动指定区间，如 --range 1.3,7.3
    """
    if raw is None or raw == "auto":
        return "auto"
    if raw == "full":
        return "full"
    try:
        lo, hi = (float(v) for v in raw.split(","))
        if lo >= hi:
            raise ValueError  # 区间左端必须小于右端
        return (lo, hi)
    except ValueError:
        raise SystemExit(
            f"Invalid --range '{raw}'; use full, auto, or lo,hi (e.g. 1.3,7.3)")
