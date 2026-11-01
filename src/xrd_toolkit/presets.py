"""项目预设（2026-11-02）。

v0.2.0 的定位是"实验室项目工具"：默认几何、第二相、原位三相模型和默认
目录都是 LMFP 这一套数据的配置。以前这些字面量散落在 GUI / simulation
里，看起来像"软件只会做 LMFP"；现在集中到这里，文案和默认值都能一眼
看出"这是项目预设，不是通用默认"。

换数据（或 v0.3 的预设层）时：改这里的值，或按同名结构加一份新预设。
"""

PROJECT_PRESET = {
    "name": "LMFP 三相",
    "geometry": "lmfp1_lab6",
    # 物相筛选时视为"第二相"、不参与主相候选的 CIF 文件名
    "second_phase_cif": "Al_metal.cif",
    # 原位工作台的默认帧目录与默认 mask 文件名
    "insitu_dir": "~/Data/XRD/lmfpdata",
    "insitu_mask_name": "lmfp1_lab6_mask.npy",
}
