# Synthetic retrieval evaluation v1

Run from the repository root:

```sh
python tools/evaluate_retrieval.py --split development
python tools/evaluate_retrieval.py --split heldout
python -m unittest discover -s tests -p test_evaluation.py -v
```

`v1/queries.json` freezes separate development and held-out release cases. These labels are public and tiny, not a statistically representative private test set. Do not tune retrieval to the held-out cases; add a new fixture version when changing the evaluation design. All names, identifiers, and facts are invented. Expected sources have graded relevance (3 primary, 2 supporting, 1 obsolete historical context). No-answer controls have no expected source.

Coverage: exact identifiers, aliases, headings, tables, phrases, paraphrases, ambiguous queries, multiple sources, superseded notes, and curated/raw-source disagreements. The hidden `.restricted` marker is a regression check for the existing hidden-directory exclusion, **not proof of general disclosure authorization**. Conflicting documents test source retrieval, not automatic truth or contradiction detection.

The evaluator calls the production recall operation with the same fixed synthetic vault and limit 5, optional backends disabled. Recall@5 is source-level recall; nDCG@5 uses graded labels. Citation validity checks every returned snippet against its claimed file, exact line and text. It reports no-answer false positives, degraded responses, machine/platform, corpus digest/bytes, per-query results, and all latency samples with p50/p95/max aggregates. A read-only evaluation never writes notes or indexes. The command exits nonzero for invalid citations or restricted marker disclosure; retrieval quality is measured rather than assigned a made-up passing threshold. Exact-identifier recall is regression-gated by the unit test.

`v1/scanner-baseline.json` records an executed scanner baseline on the reported Linux/Python environment on 2026-11-12, before retrieval changes. Timing includes warm filesystem cache and only this small corpus, so it is not a scaling claim. It is not a QMD comparison: dedicated QMD BM25 and optional hybrid must be provisioned explicitly and measured against this same fixture and budget before claiming improvement. No dependency installation or model download is performed by this evaluator.
