# Week 9：Mask 集成 + LMFP 原位三相批量

本文对应 Week 9 任务（2026-10-27）：把工具从"单帧拟合"推进到"原位序列
批处理"。所有数字来自本机实测，复现命令在文末。

## 1. Mask（2D → 1D 之前）

### 1.1 为什么必须在积分前

方位角积分是对每个 2θ 环带内的像素求平均。被 beamstop、模块缝、坏点
污染的像素如果留在图里，会直接进入平均：坏点抬高整段背景/制造假峰，
beamstop 阴影压低低角强度。事后再在 1D 上扣背景已经晚了——1D 曲线
本身已经错了。

### 1.2 本批策略

约定：`mask[y, x] = True` 表示该像素被遮掉。多个来源取**并集**（任何
一方认定坏就不参与平均），不是交集。

| 来源 | 实现 | 本批结果 |
|---|---|---|
| 自动坏点（生产） | `services/hot_pixels.py`：15×15 局部中值、>8× 中值、连通域面积 ≤300 px、峰值 ≥20k | 24 团 / 691 px |
| 手动 beamstop | Mask 编辑器矩形（束心周围 90×90 px） | 8100 px |
| 探测器边缘 | 最外 2 px | ~16k px |
| 合计 | 并集 | 25159 px（0.60%） |

**径向 nσ 自动检测**（按径向箱中位数/MAD、>5σ）已经实现并放进 GUI 的
"实验性"按钮，但**没有用于生产**：在真实 LMFP 帧上它会沿真实衍射环
标出约 18k px（见 `services/mask.py` 的说明）。这正好回答思考题：
自动检测不能替代手动 mask——它分不清"坏像素"和"真实强环的方位起伏"。

### 1.3 界面与文件

- 工具栏 `[校准]` 旁边新增 `[Mask]`，打开**独立**编辑器（不塞进参数坞）：
  打开代表性图像、画笔/橡皮/矩形、清空/撤销、自动坏点、径向 nσ
  （实验性）、保存/加载、mask 前后 1D 预览。
- 文件格式 `.npy` / `.edf`（`fabio`）。默认路径
  `outputs/masks/<geometry>_mask.npy`，同一批原位数据共用。
- `integrate_1d` 和 `integrate_sectors` 都接受 `mask=`：pyFAI 路径直接
  传 `mask`，自研偏置束心路径在 `bincount` 前剔除。**有 mask 时 1D
  缓存读写都跳过**（缓存键不含 mask，不能把未遮罩曲线当遮罩结果）。
- 主窗口 2D 图会把已应用的 mask 画成半透明红色叠加。

### 1.4 前后对比

`outputs/masks/mask_before_after_00029.png`：灰色 = 无 mask，红色 = 有
mask。最明显的是 2.8–3.0° 的固定坏点团（假峰，max 差 ~2100 counts）；
束心矩形/边缘在 1–8° 积分范围外，对曲线无影响，只保护全范围积分。
GUI 截图：`outputs/masks/mask_editor.png`。

## 2. 三相全精修批量

### 2.1 数据与物相

- 数据：`~/Desktop/lmfpdata`，81 帧 TIFF（`00029`–`00189`，步长 2），
  2048×2048，同一几何 `lmfp1_lab6`（λ = 0.1223 Å）。
- `LMFP_rich`：LiMn₀.₆Fe₀.₄PO₄，橄榄石，主相，a/b/c 自由。
- `LMFP_poor`：Mn₀.₆Fe₀.₄PO₄ heterosite（COD 9000260 去掉 Li、按
  富锂轴序置换），额外相，a/b/c 自由。
- `Al`：集流体参考相，立方 a=4.061 Å **固定**，峰强逐反射自由
  （单轴 March–Dollase 在 (331) 上高估，2026-10-27 已改）。

### 2.2 精修参数

| 项 | 设置 |
|---|---|
| 仪器 | LaB₆ Le Bail 定 zero/U/V/W/X/Y，之后**固定**（逐帧不精修峰形） |
| 晶胞 | rich a/b/c、poor a/b/c 自由；Al 固定 4.061 Å（用户 2026-10-27 拍板 Al 作参考） |
| 晶胞边界 | rich 10.20–10.50 / 5.90–6.15 / 4.65–4.76；poor 9.50–10.35 / 5.50–6.05 / 4.65–4.90 Å |
| 背景 | 自动基线 + 0.25° 分段修正 + 曲率罚项 0.1，逐帧 |
| 强度 | rich/poor 结构 scale；Al 逐反射（修 (331) 纹理误差，见 §1/2026-10-27）；rich 加 2 阶平滑强度修正 |
| 首帧 | max_nfev=120；充分收敛后作为种子 |
| 后续帧 | max_nfev=40；继承**上一可用帧**（Rwp 合格、晶胞不跳）的 zero 与两相晶胞 |
| 异常 | 不收敛 / Rwp>20% / rich 晶胞跳 >0.12 Å / poor 跳 >0.25 Å / 无相信号 → 标记，不中断，下一帧仍用上一个好种子 |

### 2.3 相分数口径

```
A_p = scale_p × Σ(table.inten_p) × table.norm_p / 100
f_rich = A_rich / (A_rich + A_poor)
```

这是**相对散射强度分数**，不是 Rietveld 重量分数（没有乘 ZMV/密度，
也没有绝对标度校正）。Al 只作参考强度（其逐反射强度是为修 Al (331) 纹理误差而放开的，
Al 总和列 CSV 的 `al_ref`），不参与 LMFP 相分数。看趋势可以，
别当 wt% 写。

### 2.4 结果

- **81/81 帧跑完**。严格 `least_squares` 成功 32 帧；
  `not_converged` 45 帧、`cell_jump` 4 帧（严格收敛率 39.5%）。
  但 **81/81 帧都可用**：Rwp ≤ 20%、晶胞/相分数有限；Rwp 范围
  **2.10–2.62%**，最大在相变中点（第 34 帧，2.62%）。
  `not_converged` 集中在相变/放电段，原因是 `max_nfev=40` 撞上限，
  不是拟合发散；种子策略已让后续帧继续跟踪。
- **相分数 V 形**：f_rich = **0.917**（第 1 帧，00029）→
  **0.042**（第 45 帧，00117，充电末端）→ **0.707**（第 81 帧，
  00189，放电回升）。f_poor 互补。
- **Al 参考强度** 32.4–32.8 M（波动 ~1%），稳定。
- **晶胞**（首 → 充电末端 → 末）：
  - rich a 10.349 → 10.200 → 10.320 Å；b 6.019 → 5.969 → 6.011；
    c 4.700 → 4.696 → 4.706。
  - poor a 10.168 → 9.847 → 10.118 Å；b 5.820 → 5.807 → 5.948；
    c 4.780 → 4.792 → 4.769。
  - 注意：f_rich < ~5% 时 rich 相峰很弱，rich a 会贴到下限
    10.20 Å；这段的 rich 晶胞只能作趋势参考，不要当精确值。
- CSV 额外给出 `usable`（Rwp≤20%、晶胞/分数有限、非错误；跳变仍标在 status）和两相晶胞
  esd（`rich_a_esd` … `poor_c_esd`），可直接画误差棒/筛坏帧。
- 图：`outputs/insitu/insitu_fit_frame_001.png`（首）、
  `_041.png`（中）、`_081.png`（末）、`insitu_trends.png`（趋势）。
- 4-panel 展示图：`outputs/insitu/insitu_summary.png`（相分数+状态、
  两相晶胞+esd、Rwp/Rp+异常、充放电分区）。

### 2.5 效率评估

- 总耗时 **696.0 s（11.6 min）**，平均 **8.6 s/帧**。
- 分布（平均）：**fit 8.46 s（98.4%）**；integrate（含 mask）0.10 s；
  load 0.03 s；背景 0.003 s。**瓶颈非常明确：逐帧非线性精修**，
  不是 IO/积分/背景。
- 可扩展性：线性估计 2× 帧数约 **23.2 min**，1000 帧约 **2.6 h**。
  积分可以并行，但只占 1%；真正要提速必须动 fit。
- 收敛率：严格 39.5%；但 81/81 帧 Rwp 2.1–2.6%、晶胞/分数有限，
  属于"可用但优化器退出标志严格"。异常是**系统性**的（相变/放电段
  的 max_nfev），不是随机坏帧。
- 与手工流程对比：手工逐帧建三相模型、检查晶胞/相分数，约
  2–5 min/帧；81 帧约 **3–7 h 的操作时间**。批量 **11.6 min
  无人值守**，省的是操作时间，不是单帧计算时间（单帧计算仍 ~9 s）。
- 最值得做的 1–2 件事：
  1. **降 fit 成本**：稳定段继承种子后减少 `nfev`/固定更多参数；
     或按充/放电段分段，段间并行（现在顺序继承，fit 98% 时间）。
  2. **区分"严格收敛"与"可用"**：放宽 `ftol/xtol` 或把
     Rwp 合格的 max_nfev 帧标成 `ok_soft`，收敛率数字会更贴近实际；
     同时不掩盖真正发散的帧。

### 2.6 与市面标准对比

典型能力（版本不同会有差异）：

| 能力 | 本项目 | GSAS-II | FullProf | TOPAS | HighScore | pyFAI/DAWN |
|---|---|---|---|---|---|---|
| 2D→1D + mask | ✅ 独立 Mask 窗口 | 图像处理有 mask/limits | 不含 2D 积分 | 不含 | 主要吃 1D | ✅ pyFAI-drawmask/DAWN |
| 顺序批量精修 | ✅ 自写 81 帧 | ✅ Sequential Refine | ✅ sequential | ✅ | ✅ 批处理 | ❌ |
| 相分数 | **相对散射强度** | **重量分数** | **重量分数** | **重量分数** | **重量分数** | ❌ |
| 不确定度 | 晶胞有 esd；分数无 | ✅ | ✅ | ✅ | ✅ | ❌ |
| 标准导出 | CSV/PNG | `.gpx`/CSV | `.pcr/.sum` | Excel/CSV | 报告 | 1D/2D |
| 脚本/开源 | Python 开源 | Python 开源 | 学术免费 | 商业 | 商业 | 开源 |
| 本批速度 | 81 帧 11.6 min | 视模型 | 视模型 | 视模型 | 视模型 | 积分很快 |

**定位**：本项目的强项是一体化（2D mask → 1D → 三相批量 →
CSV/图）、脚本化、开源、快，适合快速筛趋势；不是 GSAS-II/FullProf/
TOPAS/HighScore 那种定量 Rietveld 的替代品——我们的 f_rich 是相对
散射强度，不是 wt%，没有分数 esd，且固定了 U/V/W/size/strain。
报告里应写“relative scattering fraction”，不要写“wt%”。

如果要做数值对照，下一步是用 GSAS-II **Sequential Refine** 跑 3 帧
（第 1/45/81 帧），相同 CIF/仪器/背景，比较晶胞（应在 esd 内一致）
和相分数趋势（GSAS-II 是 wt%，比较趋势/比值，不直接比绝对值）。
仓库的 `scripts/gsas2_crosscheck.py` 目前只做模拟，顺序精修需要
单独的 `gsas2` 环境。

## 3. 复现

```bash
# Mask（GUI）：工具栏 [Mask] → 打开图像 → 画/自动 → 保存 → 应用并预览
# 本批 mask 已生成：
ls outputs/masks/lmfp1_lab6_mask.npy

# 3 帧 pilot
python scripts/fit_insitu.py --data ~/Desktop/lmfpdata \
    --mask outputs/masks/lmfp1_lab6_mask.npy \
    --outdir outputs/insitu --limit 3

# 全量
python scripts/fit_insitu.py --data ~/Desktop/lmfpdata \
    --mask outputs/masks/lmfp1_lab6_mask.npy \
    --outdir outputs/insitu

# 4-panel 展示图（读 CSV，不重拟合）
python scripts/plot_insitu_summary.py \
    --csv outputs/insitu/insitu_fit.csv \
    --out outputs/insitu/insitu_summary.png
```

## 4. 交付物

- `outputs/masks/lmfp1_lab6_mask.npy` / `.edf`
- `outputs/masks/mask_before_after_00029.png`
- `outputs/masks/mask_editor.png`
- `outputs/insitu/insitu_fit.csv`
- `outputs/insitu/insitu_fit_frame_*.png`（首/中/末）
- `outputs/insitu/insitu_trends.png`
- `outputs/insitu/insitu_summary.png`（4-panel 展示图）
- `outputs/insitu/fit_summary.txt`
