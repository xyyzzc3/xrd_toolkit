# XRD Toolkit v0.1.1 试用版

不用装 Python、不用配环境：下载 → 解压 → 双击。

> **0.1.1（本次）**：修复四个 Windows 上的问题——中文显示方框、
> 参数栏「点数」被裁、显示桌面回来后三个面板消失、任务栏图标不对。
> 已在用 0.1.0 的用户可直接下载覆盖安装，标定、缓存与配方均会保留。

## 下载哪个？

| 你的电脑 | 下载这个 |
|---|---|
| Windows 10 / 11 | **[XRD-Toolkit-windows.zip](https://github.com/xyyzzc3/xrd_toolkit/releases/latest/download/XRD-Toolkit-windows.zip)** |
| Mac（苹果芯片：M1/M2/M3/M4…） | **[XRD-Toolkit-macos-apple-silicon.zip](https://github.com/xyyzzc3/xrd_toolkit/releases/latest/download/XRD-Toolkit-macos-apple-silicon.zip)** |
| Mac（Intel 处理器） | **[XRD-Toolkit-macos-intel.zip](https://github.com/xyyzzc3/xrd_toolkit/releases/latest/download/XRD-Toolkit-macos-intel.zip)** |

链接始终指向最新版本；需要旧版本到
[Releases 页面](https://github.com/xyyzzc3/xrd_toolkit/releases)选择。

如不确定 Mac 的芯片类型：点左上角苹果菜单 →「关于本机」，看「芯片」或
「处理器」一行——写着「Apple M…」就选苹果芯片，写着「Intel」就选 Intel。

> 安装包约 110 MB（包含整个计算环境：Python、pyFAI、Qt 界面框架）。
> 第一次启动会多花几秒到十几秒（系统安全检查 + 首次建立字体缓存），
> 之后恢复正常。

**包内还附有一份《使用说明.html》**（与程序放在一起：macOS 上与
`XRD Toolkit.app` 并列，Windows 下在程序文件夹中），双击用浏览器打开，
图文步骤与常见问题都在其中；程序内 **帮助 → 使用说明**（快捷键 F1）
打开的是同一份。

## 安装与第一次打开

三个平台的应用均**未做系统签名**，所以第一次打开时系统会拦截一次——
这属于预期现象，按下面步骤操作一次即可，之后不再提示。

**Windows**
1. 把 `XRD-Toolkit-windows.zip` 解压到任意文件夹（建议解压到桌面或 D 盘）
2. 进入解压出来的 `XRD Toolkit` 文件夹，双击 **`XRD Toolkit.exe`**
3. 如果出现蓝色窗口"Windows 已保护你的电脑" → 点 **更多信息** → 点 **仍要运行**
   （若没有"更多信息"按钮：右键 `XRD Toolkit.exe` → 属性 → 勾选底部的
   「解除锁定」→ 确定 → 再运行）

**Mac（Intel 或苹果芯片都一样）**
1. 把 zip 解压，得到 **XRD Toolkit.app**（可以拖进"应用程序"文件夹）
2. **第一次请勿直接双击**——要 **右键点它（或按住 Control 点）→ 选「打开」**
3. 弹窗里再点一次 **打开**。此后即可正常双击

## 三步上手

1. **打开数据**：左上「文件」区 → [打开…] → 选中仪器导出的 `.tif` 图像
   （支持 `.tif/.tiff/.edf/.cbf`，可多选）
2. **出图**：勾选文件 → 顶部 [1D] 入口 → [出图（勾选文件）]
   —— 2θ 范围、点数保持默认即可；如需修改，在右侧参数面板中调整
3. **导出**：文件区 [导出数据] → 选择输出目录 → 导出 txt / 全部数据总表

**几何配置**：内置本实验室第 1 批的标定（`lmfp1_lab6`）。自有批次的几何有
两种方式——用 LaB₆ 标样在 [校准] 页标定一套（[保存为配置] 保存下来），
或者导入他人用工具导出的 `.poni` 文件（参数面板 [加载几何…]）。

**文件位置**
- 导出的数据：导出对话框中显示的目录（Windows 默认 `文档\XRD_Toolkit`，
  Mac 默认 `个人文件夹/XRD_Toolkit`），路径可在对话框中自行修改
- 计算过的曲线缓存：上述目录中的 `_stage` 子文件夹

## 遇到问题

请提供以下两项：

1. **`XRD_Toolkit` 文件夹里的 `crash-watch.txt`**（程序记录崩溃现场的文件，
   Windows 在 `文档\XRD_Toolkit\`，Mac 在 `个人文件夹/XRD_Toolkit/`）
2. 出问题那一刻的**界面截图**（若程序右下角"日志"区有红色报错行，请一并截入）

## 数据与隐私

- **所有计算都在你本机完成**：程序不联网、不上传数据、没有使用统计。
- `crash-watch.txt` 是崩溃现场记录，可能含本机路径；对外分享前请先自行查看。
- 科研工具免责：结果取决于几何标定与参数选择，**发表/交付前请人工复核**关键数值。
- 许可：本程序采用 MIT 许可；随附第三方组件（Qt/PySide6、pyFAI、NumPy、
  SciPy、Matplotlib 等）的许可全文在包内 `THIRD_PARTY_NOTICES.txt`，
  程序内 **帮助 → 关于 → 第三方许可** 也可打开。

## 已知限制

- 未做系统签名：Windows 首次"仍要运行"、Mac 首次"右键→打开"（见上）
- 暂无自动更新：新版本发布后重新下载覆盖安装即可（缓存与配置不会丢失）
- 系统要求：macOS 13 起 / Windows 10 起
- 界面中文、图内文字英文（图片可直接用于论文）；导出数据为英文表头
