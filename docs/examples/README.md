# Synthetic examples

Every fact, identifier and source here is invented. The `example.invalid` domain intentionally
does not resolve. Nothing here is a real export, a citation, an import schema, a tool payload,
or evidence that a run succeeded.

| File | What it illustrates |
| --- | --- |
| [`memory-record.example.json`](memory-record.example.json) | A conceptual durable fact with provenance. Field names show what a review cares about; no shipped adapter maps them yet, because 0.1.0 has no provider. |
| [`knowledge-page.example.md`](knowledge-page.example.md) | A staged knowledge-page shape. Deliberately not publishable: its citation cannot be retrieved. |

Do not feed these to a production importer. They are harness inputs for an isolated test, and
synthetic records should never be promoted into a real store.

## Scenario coverage

`Implemented` means a real test in this repository exercises it. `Policy` means the scenario
belongs to the provider release, which does not exist yet.

| Scenario | Status in 0.1.0 |
| --- | --- |
| Explicit remember, exact-ID read-back | Policy — no provider |
| Known-answer recall with provenance | Implemented (`wayfinder_recall` returns file/line and tier) |
| Paraphrase recall | Not implemented — the Markdown scan is lexical, no vectors |
| Correction / supersession | Not implemented — no edit or supersede API |
| No automatic capture | Implemented by absence — no hooks exist |
| Cross-profile isolation | Not tested — only single-profile isolated runs |
| Unrelated sensitive no-hit | Not implemented — no sensitivity metadata or filtering |
| Unverified source held in staging | Policy — `checkpoint` writes straight to a tier root |
| Index unavailable → visible degradation | Implemented for both optional backends |
| Partial persistence accounting | Implemented for readback — `verification_failed` warns the file may exist; no rollback |
