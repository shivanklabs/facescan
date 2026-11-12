# accuracy_test/
Accuracy benchmark for detection, matching and release rules.

Consent and labelling rules
- Only images from people who gave written, revocable consent for benchmarking. Never scraped
  images, never public face datasets with non-commercial licences.
- Datasets live in `datasets/` (git-ignored) and are deleted when consent is withdrawn.
- Labels use opaque person ids (e.g. `p001`), never names.
- Reports in `reports/` contain only aggregate metrics and person ids: no names, photos,
  photo ids or vectors. Per-photo debug output goes only to `work/` (git-ignored).

## Dataset layout (all git-ignored)
```
datasets/<event>/photos/                event photos
datasets/<event>/selfies/<person_id>.jpg  one face each
datasets/<event>/labels.json            ground truth (labels.schema.json)
```

## Workflow
```
uv run --env-file .env facescan-ai index ai/accuracy_test/datasets/<event>/photos --event <event>
uv run --env-file .env facescan-ai label <event>     # http://127.0.0.1:8765, Ctrl+C to stop
uv run --env-file .env facescan-ai bench <event>
```
The labeler binds to 127.0.0.1 only. It shows clusters, unclustered faces and a per-photo view,
so faces the clustering missed can be labelled too. Thumbnails are cropped in memory and never
written to disk. Keyboard shortcuts are listed on the page (`n` name, `x` remove, `u` unknown,
`j`/`k` next/previous, `h`/`l` faces, `c` photo fully labelled, `z` undo); the last 50 changes
can be undone.

When every face in a photo is labelled, mark the photo fully labelled (`c` in the Photos view).
`bench` counts only fully labelled photos and reports how many photos and releases it
excluded, so an unlabelled face never counts as a false release. The header shows progress.

`bench` reports precision, recall and false releases overall and per person, recall by face
size and quality, borderline counts, and the 95% upper bound on the false-release rate
(zero false releases in N released photos gives about 3/N; a 99.9% precision claim needs about
2995 released photos with no false release).

## Synthetic data
`facescan-ai bench <name> --synthetic` runs the engine on generated vectors (no images of
people), including look-alike pairs. `uv run --env-file .env python ai/accuracy_test/synthetic.py
<event>` writes a demo dataset with placeholder photos for trying the labeler.

## Files
- `bench_dataset.py`: layout, labels.json read/write
- `run_benchmark.py`: evaluation and reports
- `photo_labeler/`: local labelling page (FastAPI, plain HTML/JS; dev-only dependency)
- `synthetic.py`: synthetic events
