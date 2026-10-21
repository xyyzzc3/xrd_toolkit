"""单文件视图：runner 分发、四个视图的出图、后台任务回调。

模块图见 panel_state 模块 docstring（本模块在 app.py / plot_compare
之下、plot_panels 之上）。

视图注册表：runner 表 _VIEW_RUNNERS 在本模块（视图名 → 跑计算），
builder 表 _VIEW_BUILDERS 在 plot_panels（视图名 → 建面板内容）——
两边都往表里加条目就能加新视图，_run_view / _build_view_widget 的
分发骨架不用再动。四个单文件视图（2D / 剖面 / 1D / 瀑布）在这里跑
计算；对比（[对比] 按钮）与热图（[热图] 按钮）是多文件视图，整个
流程在 plot_compare。

其余内容：
  - 出图：_draw_1d（曲线 + 背景叠加）/ _draw_2d（图像 + 对比度 +
    束心十字）/ _draw_profile（过束心剖面）/ _draw_waterfall（36
    扇区堆叠，对齐 CLI 画法）——都读该面板自己的参数快照；程序重
    画不覆盖 Customize 用户改动（plot_panels 的 _snapshot_canvas
    先拍现状，_settle_scale / _apply_text_guards /
    _restore_line_styles 保护记账）。对比 / 热图的绘图入口在
    plot_compare（_redraw_compare / _draw_heatmap），按需延迟导入；
  - 后台任务：各视图 worker（_compute_integration/_compute_image/
    _compute_profile/_compute_waterfall，纯计算，后台线程跑）/
    _spawn（1D 特化）/ _spawn_task（通用版）/ 各 _on_*_done（过期
    结果丢弃，面板关了静默）/ _on_integration_error /
    _on_view_error；_apply_params（数据 [应用]；显示参数 2026-10-08 起
    全部即改即画，图像 [应用] 按钮已删——2D 走 _refresh_image_display、
    剖面角度走 _refresh_profile_angle）；
  - 背景扣除：_draw_bg_overlay（原始曲线 / 基线 / 锚点标记的辅助
    线，统一带 _AUX_GID_PREFIX）+ _bg_path_of + _refresh_proc（按各
    面板快照重画全部曲线面板）；
  - 文件 → 视图闭环：_plot_view（作图按钮的动作：对每个对号文件开
    面板并计算）+ _batch_step（批量进度记账：状态栏进度条 + 大批量
    合并日志）+ _pending_products / _spawn_headless（超出画面板上限
    的文件"只算不画"：照样算完入库，之后点开是复用缓存）。

面板壳（画布容器、手势、悬停取点、每面板工具栏）在 plot_panels；
多文件视图（对比 / 热图 / 锚点拾取）在 plot_compare。
"""

# 绘制 / 曲线配方 / 刷新 / workers（2026-10-21 起经本模块再导出）
from xrd_toolkit.gui.plot.drawing import (
    _apply_plain_view, _bg_path_of, _chi_tick_labels, _draw_1d, _draw_2d,
    _draw_bg_overlay, _draw_profile, _draw_waterfall, _update_smooth_points)
from xrd_toolkit.gui.plot.curves import (
    _anchors_on_curve, _consume_data_params, _curve_for, _curve_source,
    _file_data_params, _panel_1d_key, _proc_recipe, _ready_curve,
    _refresh_proc, _retarget_product_panel_to_source, _set_recipe_controls,
    apply_recipe, chain_label_of, note_proc_recipe, recipe_text)
from xrd_toolkit.gui.plot.refresh import (
    _apply_params, _redraw_panel, _refresh_1d_display, _refresh_heatmap_names,
    _refresh_image_display, _refresh_profile_angle, _refresh_waterfalls)
from xrd_toolkit.gui.plot.workers import (
    _compute_image, _compute_integration, _compute_profile, _compute_waterfall)
# runner / 后台任务 / 处理链 / 文件→视图闭环（2026-10-22 从本文件原样搬出）
from xrd_toolkit.gui.plot.runners import (
    _VIEW_RUNNERS, _apply_range_memory, _run_1d, _run_2d, _run_profile,
    _run_view, _run_waterfall)
from xrd_toolkit.gui.plot.spawn import (
    BATCH_LOG_MERGE_AFTER, MAX_PANELS_PER_BATCH, _batch_step, _cache_image,
    _on_image_done, _on_integration_done, _on_integration_error,
    _on_profile_done, _on_view_error, _on_waterfall_done, _progress_hide,
    _progress_show, _spawn, _spawn_task, show_placeholder)
from xrd_toolkit.gui.plot.processing import (
    _proc_batch_apply, _proc_batch_label, _proc_batch_run, _proc_ensure_curves,
    _proc_keep_this, _proc_save, _process_source)
from xrd_toolkit.gui.plot.view_flow import (
    _on_headless_done, _open_product_panel, _open_source_group,
    _open_source_view, _pending_products, _plot_view, _reload_deferred,
    _resolve_dock, _spawn_headless)
