# Synthetic evaluation record

Measured on 2026-11-12 on Linux x86_64 / Python 3.14.7, QMD 2.8.3
(facd35e). Run `python tools/evaluate_retrieval.py` for the dependency-free
baseline. `python tools/evaluate_qmd.py --help` describes the explicit optional
BM25 comparison. It accepts only the shipped synthetic corpus, not a live vault,
and creates its own disposable QMD configuration/index. No model downloads,
embedding or hybrid queries were performed.

The BM25 comparison used the same 10 approved synthetic documents and 16 queries
for both engines (the hidden fixture is excluded before copying to QMD):

| Engine | Recall@5 | nDCG@5 | Median end-to-end ms |
| --- | ---: | ---: | ---: |
| Markdown scanner | 0.9231 | 0.8780 | 0.79 |
| QMD BM25 | 0.7949 | 0.8210 | 61.69 |

Both had zero invalid hydrated citations, no-answer false positives, and
restricted-marker disclosures in this small fixture. The latency comparison
includes QMD CLI startup and passage hydration; it is not an algorithm-speed
comparison. These tests do not prove broad privacy isolation or real-vault quality.
Development/held-out labels are fixed in the fixture, but this small corpus has
already informed implementation; a fresh larger held-out corpus is needed before
claiming generalization.

Decision: retain the simple lexical default. This fixture does not justify
adding QMD fusion or default semantic retrieval. Semantic/hybrid quality remains
unmeasured, and no model download is required for ordinary Wayfinder use.
