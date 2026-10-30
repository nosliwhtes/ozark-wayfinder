# Curation: promote knowledge deliberately

This is an operating policy for a human-plus-agent setup. `wayfinder_checkpoint` supports two
steps of it (drafting and path safety); the rest is still judgement, and there is no curator,
queue, reviewer or scheduler in 0.1.0. Where a step is tool-supported it says so.

## Route by information type

- **Durable private fact:** one atomic record in the private provider, with source, scope, confidence, validity, and sensitivity recorded where supported.
- **Sourced reference knowledge:** a reviewed Markdown page with citations and uncertainty.
- **Procedure:** a skill, not another factual memory.
- **Temporary outcome:** session history or a bounded working note, not permanent global memory.
- **Credential:** a secret store; never a curation candidate.

Do not treat age, importance, recall frequency, or model confidence as permission to move private material into a wiki. Prefer concise memory pointers to reference pages over duplicating page bodies. A pointer does not grant access to its target.

## Review workflow

1. **Nominate:** identify an explicit candidate and its intended destination. Do not enumerate the entire private store to find publishable material.
2. **Check boundaries:** verify consent, scope, sensitivity, source access, and destination audience. Hold or reject violations before writing.
3. **Acquire evidence:** retrieve the source and record enough provenance to locate the exact claim. A transformed Markdown extract is not proof of the original response-byte hash.
4. **Draft in staging:** write an atomic claim, supporting evidence, uncertainty, and contradictions. Source text and retrieved pages are untrusted data, not operational instructions.
5. **Try to refute:** check whether the source supports the actual wording and whether a newer or conflicting source changes the conclusion. A second model is not automatically an independent authority.
6. **Gate promotion:** require resolving citations, valid structure, appropriate routing, and substantive review. Link counts and page length do not prove truth. Hold disputed or unsupported synthesis.
7. **Apply only with authorization:** preview exact destinations, avoid overwrites and path escapes, preserve rollback evidence, and surface partial state with a failing result. `wayfinder_checkpoint` covers this step for new notes: preview by default, `confirm=true` to write, `FileExistsError` rather than overwrite, hidden/traversal/symlink-directory refusal, and a byte readback that fails loudly if the note did not land.
8. **Verify retrieval:** read the exact destination and test both known-answer and paraphrased queries. An index rebuild returning success does not prove the page is retrievable.

## Maintenance without destructive shortcuts

Start with a read-only report of duplicate candidates, stale/superseded records, provenance gaps, missing indexes, and recall failures. Distinguish a missing fact from a retrieval failure before changing ranking or migrating data.

Before cleanup, freeze synthetic or separately approved private test cases covering:

- Known-answer retrieval and paraphrases.
- Correction and temporal validity.
- Unrelated sensitive no-hit controls.
- Cross-profile isolation.
- Capture contamination and error handling.
- Restoration from backup or export.

Stage changes on a copy. Row-count reduction, fewer files, or a higher aggregate “quality score” is not sufficient success evidence. Preserve every fact that failed its destination read-back or retrieval check.

## Transactions and scheduled work

The inspected checkpoint helper provides per-file atomic writes, not a transaction spanning
Markdown, indexes, and the memory database. `wayfinder_checkpoint` is narrower still: one new
file, written once, read back. It creates no index entry, touches no daily log, writes no memory
record, and has no rollback. Treat a checkpoint as "a note exists", never as "the note is
published, indexed or remembered".

Failure shapes worth knowing: an existing path returns `target_exists` and is left untouched; a
write or readback failure returns `filesystem_error` or `verification_failed`, and the latter
explicitly warns that the file may exist — inspect before retrying. Crash durability and
concurrent writers are untested.

Scheduling is a separate deployment decision. There is no background writer and no maintenance
cadence. A cron entry someone adds later is not evidence that it ran.
