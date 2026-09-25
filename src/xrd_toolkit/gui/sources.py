"""文件栏条目的"数据来源"：一个条目是原始文件，还是某个阶段的产物。

为什么单独一个模块（2026-09-25 加"阶段文件夹"）：文件栏从"只有原始文件"
变成"原始文件 + 各组产物"，而读勾选集合的地方有六处（出图 / 批量扣背景 /
对比 / 热图 / 导出 / 校准取标样）。这六处如果各写一遍"这个条目是文件还是
产物、该去哪儿取数"，必然漏一处——而且漏了**不报错**：拿产物当原始文件去
重新积分，屏幕上照样有曲线，只是悄悄换了口径。所以只留这一份定义：

    Source(path, kind, key, display, item)
      kind = "raw" 原始文件：按**当前设置**走老路（积分、产物缓存、扣背景）
             "1d"  1D 产物：明确用那一份（不重算、不扣背景）
             "bg"  扣背景产物：明确用那一批的那一份

产物条目带的是**产物键**（建分组时记下的），所以取数不重算键、不比对
设置——用户在界面上勾的就是他要的那一份。

条目的 Qt 数据槽：UserRole = 全路径（老约定，一切按路径找面板/锚点的
代码都还认它），另外两个新槽见 SOURCE_KIND_ROLE / SOURCE_KEY_ROLE。
"""
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import Qt

from xrd_toolkit.services import stage_cache

RAW = "raw"        # 原始数据
ONED = "1d"        # 1D 产物
BG = "bg"          # 扣背景产物

SOURCE_KIND_ROLE = Qt.UserRole + 1     # 条目属于哪个阶段
SOURCE_KEY_ROLE = Qt.UserRole + 2      # 产物键（raw 条目为 None）

KIND_TAIL = {ONED: "1D", BG: "扣背景"}   # 产物条目的名字后缀（图例/标题里
                                        # 分得清"这份是哪一阶段的结果"）


def set_item_source(item, path, kind: str = RAW, key: str = None) -> None:
    """给条目写上来源三件套（路径 / 阶段 / 产物键）。"""
    item.setData(0, Qt.UserRole, str(path))
    item.setData(0, SOURCE_KIND_ROLE, kind)
    item.setData(0, SOURCE_KEY_ROLE, key)


def make_source(path, display: str = None, kind: str = RAW, key: str = None,
                item=None) -> SimpleNamespace:
    """手工造一个 Source（测试/脚本直接给面板绑一组来源时用）。

    界面上真正的条目一律走 source_of（从 Qt 数据槽里读）；这个入口只给
    "不经过文件栏也要让面板动起来"的场合，比如单元测试手工置
    dock.heat_files / dock.compare_files。
    """
    p = Path(path) if not isinstance(path, Path) else path
    return SimpleNamespace(item=item, path=str(p), kind=kind, key=key,
                           display=display or p.name)


def source_of(item) -> SimpleNamespace:
    """条目 → Source（纯读，改不了东西）。"""
    if item is None:
        return None
    return SimpleNamespace(
        item=item,
        path=item.data(0, Qt.UserRole),
        kind=item.data(0, SOURCE_KIND_ROLE) or RAW,
        key=item.data(0, SOURCE_KEY_ROLE),
        display=item.text(0),
    )


def all_sources(window) -> list:
    """文件栏里全部**叶子**条目（不论勾没勾），按树上的先后顺序。

    组节点不是条目（它没有来源三件套）：勾组 = 勾组里全部子项，由文件坞
    负责展开，所以这里往下走一层就只会看到叶子。
    """
    out = []

    def walk(node):
        for i in range(node.childCount()):
            child = node.child(i)
            if child.data(0, SOURCE_KIND_ROLE) is None:
                walk(child)          # 组节点：往下走
            else:
                out.append(source_of(child))

    walk(window.file_list.invisibleRootItem())
    return out


def checked_sources(window) -> list:
    """文件栏里所有打对号的条目 → [Source, ...]（按树上的先后顺序）。

    对号仍然是唯一的选择表达。要跨组读勾选集合的地方（出图/批量扣背景/
    对比/热图/导出/校准取标样）都走它，别自己去遍历列表。
    """
    return [s for s in all_sources(window)
            if s.item.checkState(0) == Qt.Checked]


def source_id(source) -> str:
    """条目的稳定身份 = 面板键的后半段。

    原始文件用路径（老约定：面板 "1D|<路径>" 全世界都认）；
    产物用 "阶段#键"——同一张图的原始曲线与扣背景曲线是两个面板，
    互不覆盖（这正是要能并排看的原因）。
    """
    if source.kind == RAW:
        return str(source.path)
    return f"{source.kind}#{source.key}"


def path_of(source):
    """产物条目对应的**源文件**路径（锚点、日志、默认文件名都用它）。"""
    return source.path


def load_product(source):
    """产物条目的曲线 (tth, intensity)；读不到（被删/坏了）→ None。

    raw 条目没有"现成的一份"——它要走各自的老路（积分 / 当前设置的
    扣背景产物），这里只负责产物条目。
    """
    if source.kind == RAW:
        return None
    return stage_cache.load_by_key(source.kind, source.key)


def describe_source(source) -> str:
    """日志/标题里说清"这一条是从哪儿来的"（用户 2026-09-25：照旧用，
    但日志要说一句——用哪一份得看得见）。"""
    if source.kind == RAW:
        return "原始数据"
    return f"{KIND_TAIL.get(source.kind, source.kind)}产物"
