# Evaluation datasets

Versioned historical packs. `v1` is loaded by `python -m evals.run --dataset v1`.

`v1` contains **25** curated public-market events plus planted-flaw / memory / routing cases.
Ground truth is **not** in these files at runtime; it lives on `PackedCase.truth` inside
`catalog.py` and is never passed to production agents.

Selection criteria: multiple event types (earnings, guidance, regulatory, macro, sector,
competitor/short-report, idiosyncratic, unusual). Mix of easy / medium / hard / ambiguous.
Quality over an arbitrary 100-case count; the loader is designed to scale.
