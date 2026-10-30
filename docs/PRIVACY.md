# Privacy and data boundaries

## Local storage is not an offline or confidentiality guarantee

A local database or Markdown file can still reach a remote model through recall injection, a hosted embedding service, a curation model, a tool result, a log, a backup, or a published site. No claim is made here that Ozark Wayfinder is fully offline, encrypted at rest, an access-control boundary, or a complete secret detector.

Before use, identify every reader and writer, the selected model/embedding providers, their retention policies, and the audience of any generated files. An export is a separate disclosure decision.

## Policy, and how much of it 0.1.0 meets

Each rule below is marked **met by absence** (0.1.0 never performs the risky action),
**met by design** (the code enforces it and a test covers it), or **future** (needs work).

- Prefer explicit durable writes. Leave automatic transcript capture off until separately approved and tested. — **met by absence**: the manifest declares no hooks, so no turn, session-end or compression callback exists to capture anything.
- Do not persist raw conversation, assistant replies, tool outputs, or pre-compression material merely because a lifecycle callback receives them. — **met by absence**, there are no callbacks.
- Reject credentials and unauthorized sensitive material before persistence, indexing, logging, and publication. Pattern matching alone cannot guarantee detection. — **not implemented**; only path rules and size limits are enforced.
- Bind private state to the active profile. Do not silently fall back to another profile, a guessed chat identity, or a global shared store. — **met by design**: every path is an explicit setting; nothing is discovered and no default profile is used.
- Give retrieval only the scope it needs. A fact being important does not authorize its appearance in an unrelated context. — **partly met**: `limit` defaults to 8 and is capped at 50, and only global Mnemosyne rows are read. Session-scoped rows are excluded outright, so there is no per-session narrowing yet.
- Return bounded content with provenance and honest degradation signals. Avoid raw content in operational error messages. — **met by design**: results carry a `path:line`, a `mnemosyne:<id>` or a QMD URI citation plus a tier (the URI stands in when a hit was not opened, and a bare path when the note is empty); failures return codes, not note text.
- Require an explicit candidate boundary before private memory can feed a knowledge-ingest queue. Staging is not consent to publish. — **future**: there is no ingest queue yet; `wayfinder_checkpoint` writes straight to the configured vault.

The inspected backend supports turn synchronization as well as explicit writes, so “using local
memory” alone would not establish explicit-only capture — in Wayfinder's case the marker above
does, because there is no callback to capture from.

Two leak surfaces do exist and are easy to miss: `wayfinder_recall` returns note excerpts to
whatever model is reading the session, and QMD runs as an ordinary subprocess that inherits OS
permissions. Neither is mitigated by the plugin.

## Keep out of shared source and examples

Do not commit live databases, database sidecars, exports, transcripts, private notes, retrieval caches, embeddings of private text, profile settings, environment files, authentication state, backup bundles, screenshots of private sessions, or logs containing private content. File names and source identifiers can themselves disclose information.

All examples in this directory are independently invented. Reserved `example.invalid` URLs are intentionally non-resolving; they are not citations or evidence. Do not replace them with live private material while preparing a shareable artifact.

## Correction and forgetting

A correction should retain enough provenance to identify the superseded claim and prevent stale recall. Whether the underlying backend updates, invalidates, or supersedes records must be documented and tested rather than assumed.

“Forgetting” must name its scope: active records, graph relations, embeddings, search indexes, generated context, cached results, Markdown copies, session history, logs, and backups are different surfaces. Deleting one record does not prove erasure everywhere. Preserve backups only under a documented retention/access policy; report residual copies and expiry instead of promising universal deletion.

Bulk removal, merging, reindexing, import, or store replacement needs a reviewed exact-target manifest, a usable rollback plan where appropriate, isolated test evidence, and explicit approval. Never weaken privacy tests to make a release pass.

## Release privacy checks

- Exercise capture hooks with synthetic sentinel text and verify unintended stores remain unchanged. — **n/a in 0.1.0**, no hooks.
- Test unrelated sensitive no-hit queries and cross-profile isolation, including concurrent use. — **not run**. Only single-profile isolated tests exist.
- Verify secret rejection before a write, using runtime-generated synthetic markers rather than actual credentials. — **not implemented**; nothing inspects content for credentials.
- Inspect generated files, error output, archives, and documentation for accidental personal content. — **done for the source tree**; the whole project was scanned for host-specific absolute paths and none remain.
- Review exact packaged bytes, not only source files or a clean status report. — **partly done**: the wheel is built and installed in a clean venv and the installed code is what the bootstrap tests execute.

The first public release of this source is a separate gate; nothing above authorises publishing
a vault, an export or a memory database.
