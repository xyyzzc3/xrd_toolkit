# Reference batches

`insitu_reference_v021_81.csv` is the 81-frame in-situ batch produced by
v0.2.1 on 2026-11-24 (189 s, 81/81 ok, mean Rwp 2.29%). It is the
baseline for `scripts/check_insitu_reference.py`. The file is tracked
here because the live `outputs/` tree is gitignored.

To regenerate it:

```bash
conda activate XRD_Toolkit_Environment
python scripts/fit_insitu.py \
    --data ~/Data/XRD/lmfpdata \
    --mask outputs/masks/lmfp1_lab6_mask.npy \
    --outdir outputs/insitu_v021_81 \
    --intensity-poly 0
cp outputs/insitu_v021_81/insitu_fit.csv tests/data/insitu_reference_v021_81.csv
```

The 2026-11-24 targeted trigger changes frames 28-30 only (frame 75 is
unchanged); compare a new batch with `--allow-frames 28,29,30` until a
new reference is accepted.
