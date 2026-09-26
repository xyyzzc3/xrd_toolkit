# XRD Toolkit — the long version

Everything that used to be in the README and made it unreadable: CLI flags, how the
calibration numbers are judged, how background subtraction was chosen, how batches and
product caching work, and the engineering notes behind the GUI. The short version is
[README.md](../README.md) · 中文：[NOTES.zh-CN.md](NOTES.zh-CN.md)

## CLI flags

All four scripts accept `--file` to pick one dataset, or — without it — an interactive
menu listing everything in `data/` (number selection, `1,2` multi-select, or `all`).
Outputs land in `outputs/{dataset}/`. The three consuming scripts take `--config NAME`
(default `lmfp1_lab6`); in interactive mode a second menu asks for the config after the
files are chosen.

<details>
<summary><b>View a diffraction image</b> — <code>scripts/view_diffraction.py</code></summary>

```bash
python scripts/view_diffraction.py --file data/xxx.tif --angle 0 --outdir outputs
```

- `--file`: diffraction image path (.tif / .edf / .cbf)
- `--center`: ring center `cx,cy` — defaults to the beam center of the selected config
- `--config`: geometry config name from `config.py` (default `lmfp1_lab6`)
- `--angle`: profile angle in degrees (default 0)
- `--outdir`: PNG output directory (default `outputs/`)

![Radial intensity profile through the beam centre](../showcase/lab6/radial_profile.png)

</details>

<details>
<summary><b>Geometric calibration + 1D pattern (LaB₆ standard)</b> — <code>scripts/calibrate_integrate.py</code></summary>

```bash
python scripts/calibrate_integrate.py --file data/xxx.tif --wavelength 0.1223 --pixel 200 --dist0 1600
```

- `--wavelength`: X-ray wavelength (Å)
- `--pixel`: detector pixel size (µm)
- `--dist0`: initial detector distance (mm) — refined automatically by pyFAI
- `--center`: initial ring center `cx,cy` in pixels (default: auto-localized, < 1 px accuracy). Pass it explicitly for off-center-beam (partial-ring) datasets: the auto-localizer needs full rings, and refinement is unreliable there — calibrate distance/tilt on a centered standard image instead
- `--max-rings`: number of LaB₆ rings used for calibration (default 16)
- `--range`: 2θ range — `full`, `auto` (default, per-material standard), or `lo,hi` degrees (e.g. 1.3,7.3); the full txt is always saved
- `--outdir`: output directory (default `outputs/`)

Pipeline: LaB₆ peak-position calibration (pyFAI `GeometryRefinement`) → azimuthal integration (2D → 1D) → writes `calibrated_2th.txt` + `calibrated.png` (red dashed lines = theoretical peak positions). Code in `xrd_toolkit/services/integrator.py`. After refinement, a copy-paste-ready `CONFIGS` entry is printed so the new batch can be registered in `src/xrd_toolkit/config.py` (the script never writes that file itself).

</details>

<details>
<summary><b>Full-angle integration (2D → 1D standard pattern)</b> — <code>scripts/integrate_pattern.py</code></summary>

```bash
python scripts/integrate_pattern.py --file data/xxx.tif
```

Integrates 0°–360° with the geometry of the selected config (default `lmfp1_lab6`, e.g. 1595.80 mm) and writes a two-column txt (2θ(deg), intensity) + PNG. Override with `--dist`, `--poni`, `--wavelength`; `--range full/auto/lo,hi` controls the 2θ interval of the plot and the `_auto` trimmed txt (full txt always saved).

</details>

<details>
<summary><b>Sector integration + waterfall plots</b> — <code>scripts/sector_waterfall.py</code></summary>

```bash
python scripts/sector_waterfall.py --file data/xxx.tif --n-sectors 36
```

Splits the 0°–360° azimuth into N sectors (default 36, one per 10°), integrates each sector separately (geometry from the selected `--config`, default `lmfp1_lab6`), and writes per-sector two-column txt files under `outputs/{dataset_name}/sectors/` plus a raw-intensity stacked waterfall plot (36 curves offset along the Y axis with **one row height for every row = the overall peak × 0.7**; each curve is drawn until its own intensity drops to zero, so the staircase right edge marks where each sector's ring is clipped by the detector). Large grains or preferred orientation show up as intensity concentrated in a few sectors. The row spacing deliberately does *not* follow each sector's own peak — that would rescale every row to its own height and kill the sector-to-sector comparison (the rule, set 2026-09-26: "don't normalize by each one's own peak, in any figure"). The GUI's waterfall follows the same rule and additionally runs the processing chain (background / smoothing / cut) on every sector, so cutting the giant peak that squashes the rest is what makes weak sectors readable. Supports `--range full/auto/lo,hi`.

</details>

## Calibration

The [校准] entrance opens the calibration page: its first block is the config-entry block — [编辑当前配置…] plus [加载参数] / [保存参数] / [删除] / [保存为配置] — sitting directly under the pinned geometry row, because those buttons act on the entry selected up there. Under it comes the three-column table: **current config** plus two comparison slots **A** and **B**, each selectable from everything produced so far, then two action blocks, 自动 (locate the ring centre and refine / refine again from the current geometry) and 手动 (pick rings by hand: ≥ 3 points, ≥ 2 rings, ±0.5° snapping).

The current config starts as the raw geometry borrowed from the selected entry; [编辑…] types values in or prefills from another entry, and any hand edit marks it 自定义 so nothing replaces it automatically. **Every result accumulates under its own name** (原始 / 自动1 / 手动1 / 精修1 …) and lands in a slot by rotation A → B → A — unless you picked that slot by hand, in which case it is pinned and new results stop overwriting it.

**Which geometry is in use is decided by the engine metric**: a result replaces the current config only when it wins by more than 0.05 px of ring-position deviation (repeating the same image moves the fitted geometry by PONI 1.6–2.2 px but the ring deviation by only 0.014–0.029 px, so anything smaller is run-to-run noise). Any two columns can be compared: choose a base (current config / A / B) and read the Δ rows — distance and ring-position deviation — plus a verdict line. PONI and tilt carry a ⚠ because they are degenerate with distance and wavelength, and the self-consistency residual is deliberately kept out of the table — it measures convergence, not accuracy.

The overlaid cyan rings are the *exact* rings the current geometry predicts: every point is reverse-solved from pyFAI's own 2θ map, so a tilted detector appears as the ellipse it really is — a plain "center + radius" circle would sit 8–23 px off, because the common center of the rings is the direct-beam spot, not the PONI. Point picking judges against that same geometry, so clicking a drawn ring cannot land on the neighbouring ring index, and if the geometry puts all 16 rings off the detector (distance / pixel-size / wavelength mismatch, or a wild refinement) the panel says so in red on the plot and in the log — ring radius range included, so the error factor is right there — rather than silently drawing nothing. **The view itself never moves**: it is always the image extent, so the detector image stays the one fixed frame of reference while the rings move around it. Seeing where the rings went is a deliberate act instead — the panel's [看环全貌] toggle (off by default, reset every time the panel opens) stretches the view to fit the whole ring set; the image then shrinks to a small square in the middle, which is exactly what that switch is for.

**Engine metrics.** Every run reports the median ring-position deviation in px (with the pre-refinement initial value beside it), how many of the 16 rings came out complete, and the dispersion of the LaB₆ lattice constant back-solved ring by ring. These measure what the fit residual cannot — a run can report a 0.0000° residual while the rings sit ~10 px off the real ones (measured on a deliberately mis-picked point set), because the residual is a self-consistency number, not an accuracy metric. Ring-position deviation catches uniform geometry error; the lattice-constant dispersion catches per-ring mismatch (wrong ring index, distortion, a peak pushed aside). Distance and wavelength are first-order degenerate on a standard, so a distance error barely moves the dispersion — do not use it to detect one.

**Pixel size is a gate, and the metrics cannot check it.** Calibration is blocked until the pixel size is confirmed — and the confirmation comes back only when the pixel value actually changes. Distance, wavelength and pixel size enter only as a *product*, so a wrong pixel size still lands the rings perfectly while the reported distance (and everything derived from it) is off by that factor. Saving is gated the same way: [保存为配置] refuses (with a log line) until the box is ticked, because a saved entry gets used verbatim by other batches and by the CLI scripts.

**Entry points.** New batches start from the current config: borrow another entry or type values in [编辑…], or import a .poni with [加载参数]. [保存为配置] stores the current config — borrowed, typed or adopted — together with its provenance (`derived_from`, `method`, `created`), as a named entry in a local user file outside git that instantly joins the geometry dropdown, is selected automatically, survives restarts, and is usable from the CLI via `--config`. The geometry dropdown itself sits in a row pinned to the top of the parameter dock, so it stays visible — and usable — on every entrance page; hover it for the pixel size / wavelength / distance of the entry in force, and the analysis pages are read-only where geometry is concerned: picking an entry is all they do. On the calibration page the dropdown keeps working (the tooltip adds that calibration follows the page's own "current config", not the entry picked here), because the buttons acting on that entry — [加载参数] / [保存参数] / [删除] — live on the calibration page. [保存参数] writes the selected config as a standard pyFAI `.poni` exchange file (distance, center as poni1/poni2 in meters, pixel size, wavelength, tilt rot1/rot2; the optional mask path is not written — the engine has no mask support and the format has no mask field), and [加载参数] reads one back into a named entry. Imported or saved user entries can be removed with [删除] (greyed out for builtin registry entries): a confirmation dialog guards the removal, and the selection falls back to the default entry.

## Automatic 2θ range selection

`--range auto` (the default) picks the interval with one standard per material: the lower bound comes from the material's standard (first known peak − 0.3° for powder samples; detected halo end − 0.6° ≈ 1.0° for the LaB₆ standard), the upper bound is detected where the data starts failing.

The failure criterion is **relative arc coverage** — the ring's fraction of azimuth inside the detector falling below 50 % of its own maximum — so one criterion adapts to any beam placement: ≈ 7.9° for a centered beam (the old 80 % absolute criterion stopped at ≈ 7.44°), and automatically later for off-center beams. Single-curve scripts use the exact geometric value; the waterfall's sector-based measured value lands ≈ 0.5° later (10° sector quantization) — the two are cross-checked and a warning fires only on a real mismatch. The full-range txt master copy is always saved.

## Off-center (partial-ring) beams

Datasets recorded with the beam deliberately placed at the detector edge or corner (each ring only partially captured, revealing higher-2θ rings) are handled end to end: a built-in numpy polar integration takes over automatically when pyFAI's radial binning becomes unreliable (pyFAI bins relative to the detector center, verified on synthetic rings), sector χ labels report the actually covered azimuth span instead of a fake 360°, and the waterfall statistics average only over sectors with real signal. Calibration on such datasets should be done on a centered standard image (distance/tilt are instrument properties); `--center` documents this.

## Processing (background / smoothing / cut)

The 处理 page carries three steps. **All three are optional** (unticked = not applied), all three redraw
live as you change them (milliseconds — no re-integration), and all three go to the whole batch through
[批量处理], landing in a 处理后 … group in the file bar.

**The order is fixed: background → smoothing → cut.** Two boundary reasons: anchors are sampled *before*
the cut (an anchor inside the cut window would sample a blank and take the whole baseline with it), and
smoothing never sees a hole (smooth the complete curve first, punch the hole last — the other order needs
"half the window is blank" edge handling, which quietly makes every image's edges slightly different).

| Step | What it does | Knob |
|:---|:---|:---|
| Background | removes the additive part carrying no structure (empty scan / auto baseline / manual anchors) | see the next section |
| Smoothing | two methods: **rolling average** (default) / **Savitzky–Golay** | window width in **degrees** (not points, so it survives a change of output point count); SG also takes a polynomial order (2–3 in practice) |
| Cut | punches chosen 2θ intervals out of the curve (the plot shows gaps there) | start/end 2θ plus [添加] — the list holds **as many intervals as you like** (e.g. 2–3° and 7–8° at once) |

**Choosing between the two smoothers** (measured on the LaB₆ standard, same 0.30° window): the rolling
average takes the strongest peak down by **90.6 %** and widens its FWHM from 0.18° to 0.31°;
Savitzky–Golay takes it down by only **82.3 %** and leaves the FWHM at 0.18°. So **use SG when peak
shape, height or width matter**; the rolling average is the faster, simpler choice when you only want
the curve to look clean. SG's price: against a steep edge (the low-angle hump) it can push slightly
below zero — worth watching when you read background levels.

The cut list is the authority (window-level). Ticking 「裁剪区间」 with an empty list adds the interval
sitting in the two spin boxes — filling them in and ticking the box is the natural gesture — [添加]
appends another, [清空] takes them all back. Duplicates collapse, and a start ≥ end is refused with a
message rather than silently doing nothing.

**The cut blanks values, it does not delete points**: samples inside the window become NaN, so the 2θ grid
and every array length stay put (compare / heatmap / CSV alignment is untouched). All three consequences
are intended:

- the plot **shows a gap** there (matplotlib breaks the line at NaN) and the automatic y-range **skips it** —
  which is the whole point when one giant peak squashes everything else;
- the processing product has the same gap, the exported txt/chi **omits those rows**, and the file header
  records `processed: …` and `cut: N points removed`;
- the CSV summary leaves those cells **blank** (not 0 — a zero would be read as real intensity) and names
  the affected columns.

**The product key = 1D product key + hash of the chain** (background, smoothing and cut together). With
smoothing and cut off, that hash is **bit-identical** to the old background-only product, so upgrading
never invalidates previously stored products. The group name states what the batch went through
(处理后 09-25 16:40（锚点 5 个、窗口 2°、平滑 0.15°、删 2–3°）), and the product metadata carries a
machine-readable chain (`bg=anchor(n=5)/win=2 → smooth=boxcar/0.15° → cut=2–3°`) — any product can
explain how it was made.

**The one-line model**: a 1D product is the integration's result, a processing product is the processing
result, and Compare / Heatmap / export read the processing product when there is one (saying so in the
log: "处理产物 N 条"). What you see on screen is produced by the same function that writes the file.

## Background subtraction

The parameter dock's 背景扣除 section removes what carries no structural information: air scatter, amorphous diffuse scattering, fluorescence, detector dark current, beam-stop halo. It matters here: both samples' background rises 3.8–5.2× toward low 2θ (LMFP 1429 counts at 1–2° vs 275 at 9–10°), so a constant offset cannot work.

Three modes, every one previewed live (parameters change the drawing only — the cached raw curve is never touched, so nothing re-integrates):

- **empty-scan subtraction** — subtract a blank measurement, with a normalization factor for differing exposure/beam current;
- **auto baseline** — rolling-window level estimate; set the window to 3–10× the widest peak's width;
- **manual anchors** — click points that are background only; the baseline joins them and is extrapolated linearly past the first/last anchor.

While subtracting, the 1D panel overlays the raw curve (dashed) and the baseline (dotted) on the subtracted one, so before/after is visible as you tune. Anchors are stored per file; Compare / Waterfall / Heatmap apply the same settings (the waterfall subtracts one common sector-mean baseline so sector-to-sector intensity differences survive), and [导出数据] can write the subtracted curves. Negative values after subtraction are kept by default — the noise floor is real, and clipping it at 0 biases the mean up by ~1σ — with an opt-in checkbox to clip.

SNIP was implemented and measured as well, but it over-subtracts by 43–98 % on these patterns (it clips the broad amorphous hump at ~2.2° as if it were a peak), so the GUI does not offer it.

![Background subtraction on a real LMFP pattern](../showcase/gui/gui_background.png)

## Batches, products and the file bar

**Batching.** [打开文件夹] imports a whole folder (every .tif/.tiff/.edf/.cbf inside, duplicates skipped automatically — or just drag a folder onto the window), and any view button processes all checked files at once, with a live progress counter in the log and in a status-bar progress bar (completion lines end in （k/n）; opening the panels themselves reports progress every 8 panels and keeps the window responsive). One batch draws at most the first **24** checked files (list order) — opening a panel is the one cost that grows with the count (136 ms for the first, 287 ms for the hundredth) and each costs ≈15 MB — and the overflow is never silent: **the rest are integrated in the background and stored anyway** (no panel), so opening any of them later is a cache hit, and the log says so. Large batches merge their log lines (one line for the openings, a progress line every 8, one summary with the elapsed time and how many came from the cache) — 81 files used to print 162 lines. [导出数据] saves each file's 1D result as a two-column `integrated_2th.txt` or `.chi` under `outputs/{file}/` — byte-identical format to the CLI — with an optional `1d_summary.csv` (one intensity column per file; when 2θ grids differ, a dialog offers the common intersection with re-interpolation, skipping the mismatched files, or cancelling). A single failed file never aborts the batch.

**Selecting what to process.** Imports start out unchecked (200 files should be your call, not the app's): [全选] / [全不选] / [按条件选…], where the last one takes a range (from the i-th to the j-th), a stride (every n-th, with a start offset) and a case-insensitive name filter (substring; it intersects with the other two), optionally appending to the current selection so you can build one across several ranges. The dialog previews the count live.

![Batch pipeline](../showcase/gui/gui_batch.png)

**Staged-product cache.** Integrated 1D curves are written to `outputs/_stage/` (local, not in git) and reused across sessions, so re-opening a batch the next day does not re-integrate everything (measured on three files: 1.4 s → 0.4 s; extrapolated to 81 files, 38 s → 10 s). The key is the file fingerprint (name + size + mtime + head hash) + geometry entry + 2θ range + point count + an **integration implementation version** (bump it and old products retire themselves). A hit is always logged ("复用缓存：…") — silent reuse is how you end up trusting the wrong numbers. The Draw page has [清空缓存].

**Background products.** [批量扣背景（勾选文件）] applies the current figure's anchors to the whole batch — the anchors carry **only their 2θ positions**, the intensities are re-sampled from each file's own curve (a batch shares the background *shape*, not its absolute level; copying the intensities would lift another file's baseline by a factor). Each result is stored, and the Compare / Heatmap views read those products first, saying so in the log ("扣背景产物 N 条"). Changing a background setting does not invalidate anything by hand: the product key contains the settings hash, so a change redraws live (milliseconds) and only an unchanged recipe reads the product.

**The file bar is a tree** — the 原始数据 group on top, then one group per stage: 1D 产物 (already integrated under the current settings) and 扣背景 09-25 14:03（锚点 5 个，窗口 2°）… (one group per [批量扣背景], named with its time and recipe, so you can tell which parameter set a result came from). **Checking a group checks everything in it** (a partial check shows as partially checked), so a whole batch of background-subtracted curves goes to [对比] / [热图] in two clicks. Right-clicking deletes: a product group (the whole batch, ledger and stored products together) or a single product entry — every kind of product can be removed from the file bar, and the 1D 产物 group is no exception. Groups are rebuilt only when something happens (import / delete, batch background, cache clear) — never on a timer.

**The two kinds of product are treated differently.** A **1D product** *is* the raw integrated curve, pinned to one cache entry, so it gets the same treatment as a raw file — anchors and auto-baseline work on it, and it can go through [批量扣背景] like anything else (the result hangs off *that* 1D key: whatever you checked is what gets subtracted). A **background product** is the finished article: its panel is forced to "no subtraction" and it is never subtracted again — subtracting twice is what that would be. Plotting or exporting a product reads that exact stored curve, with no re-integration.

## GUI engineering notes

- **Independent plot panels** — every plot is an MDI subwindow with free resizing (each drag remembers the panel's own aspect), pop-out into a separate OS window, cascade / tile arrangements, and a zoomable drawing area (Ctrl + wheel, Excel-style).
- **One-row panel chrome** — a single row per panel carries the buttons (Home — back to the panel's own view; zooming and panning do not move it, a recompute does / magnifier / Customize (title, axis labels, linear–log y scale, figure margins; Compare panels add one color per curve) / Save image (PNG or TIF at a chosen DPI), plus pop-out and close. Drag that row to move the panel, double-click to fill the plot area — the native title bar was replaced by this self-drawn row, which takes the shell from 83 px down to 26 px (57 px more plot in the same panel height). Popped-out panels get the system title bar back, with the row still carrying the buttons; the current edit target is shaded darker, the usual active/inactive convention.
- **Gestures** — left-drag pans; with the magnifier lit, the wheel zooms around the cursor (10 % per notch) and left-drag draws a box zoom (the rubber band is drawn by us and only ever changes the axis ranges, never the data); hover shows a data-point readout in the status bar; every panel edge has a resize grip.
- **Live parameter dock** — data and display parameters per panel (tooltips everywhere, Reset / Apply per group, per-panel snapshots); the 2θ range of the data group bounds the integration itself (npt samples within it), while zoom / pan writes the view range back in real time.

![2D / Profile / Waterfall panels](../showcase/gui/gui_views.png)
![Compare view](../showcase/gui/gui_compare.png)
![Customize dialog](../showcase/gui/gui_customize.png)

**Checks worth running.** `python scripts/check_env.py` answers "can this repo run right now?" in one command (interpreter, dependencies, editable-install target, GUI import, tests & data) — **after moving or renaming the project folder**, re-run `pip install -e .`, because the editable install stores an absolute path. `python scripts/check_gui.py` walks the interface with real sample data and real canvas events (hover, anchor picking, wheel zoom, compare, heatmap): an all-green unit suite can still hide a dead click path, and this one verifies at the canvas-callback layer. `python scripts/show_gui.py` opens the actual window on real data, walks three fixed steps, saves a screenshot of each under `outputs/gui_shots/`, and leaves the window up (`--headless --exit` for the automated variant).

**Two deadlocks, both fixed.** The offscreen GUI suite used to hang once enough panels had been built in one process: matplotlib's toolbar recursing inside its own constructor (the panel toolbar is ours now), and a PySide6 lock-order inversion — the main thread holding the GIL and waiting on a Qt internal mutex while a worker thread held that mutex and waited for the GIL, during per-task thread teardown (background tasks now run on long-lived worker threads). `scripts/stress_panels.py` is the regression probe that used to hang on them, and `scripts/run_tests.py` runs the suite behind a watchdog that dumps every thread's Python stack rather than stalling silently.
