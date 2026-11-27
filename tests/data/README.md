# Reference batches

Two in-situ reference batches are tracked here. Both are 81 frames of the
LMFP lab data (`~/Data/XRD/lmfpdata`) with the standard mask and
`--intensity-poly 0`; the live `outputs/` tree is gitignored.

| CSV | Produced by | Purpose |
| --- | --- | --- |
| `insitu_reference_v021_81.csv` | v0.2.1, 2026-11-24 (189 s, mean Rwp 2.29%) | Historical baseline before the targeted dual-start trigger. |
| `insitu_reference_v022_81.csv` | v0.2.2 code, 2026-11-27 (235.3 s, 81/81 ok, mean Rwp 2.282%) | Current baseline; includes `dual_start` / `start_used` / `rwp_seed` / `rwp_cif` provenance. Only frames 27-28 run a second start; 28 takes the CIF solution. |

Each CSV has a sidecar JSON with the version, generation command,
tolerances and frames whose differences are expected. The checker reads
it automatically.

## Checking a new batch

```bash
conda activate XRD_Toolkit_Environment
python scripts/check_insitu_reference.py \
    --candidate outputs/insitu_v022_81/insitu_fit.csv
```

The v0.2.1 manifest allows frames 28-30 by default: those are the only
frames changed by the 2026-11-24 `Rwp >= 2.45%` trigger. The v0.2.2
manifest allows none.

## Regenerating / accepting a reference

```bash
python scripts/fit_insitu.py --engine generic \
    --data ~/Data/XRD/lmfpdata \
    --mask outputs/masks/lmfp1_lab6_mask.npy \
    --outdir outputs/insitu_v022_81 \
    --intensity-poly 0

python scripts/accept_insitu_reference.py \
    --candidate outputs/insitu_v022_81/insitu_fit.csv \
    --reference tests/data/insitu_reference_v022_81.csv \
    --version 0.2.2 --engine generic \
    --command "python scripts/fit_insitu.py --engine generic ..."
```

`--engine generic` runs the same code path as the GUI [原位] workbench;
the legacy engine can still be checked with `--engine legacy`.
