# Retrieval evaluation (development set)

`python -m app.retrieval evaluate` scores 8 queries against 24 synthetic documents (`app/retrieval/eval_data.py`).

Ground truth is the listed `relevant` titles for those queries only. Metrics: Recall@5/10, Precision@5/10, citation coverage, empty rate, duplicate rate.

Embeddings in this eval are **fake lexical hashes**, not OpenAI. Scores measure fixture overlap, not production semantic quality. Do not publish them as a benchmark.
