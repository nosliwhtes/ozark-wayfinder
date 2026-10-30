# Stack and evidence boundaries

**Read this as a boundary document, not a feature list.** Sections below describe the audited
personal stack that motivated the project. Where 0.1.0 does not implement a layer, it says so
explicitly. [IMPLEMENTATION.md](../IMPLEMENTATION.md) is authoritative on what ships.

| Layer | In 0.1.0 |
| --- | --- |
| Markdown knowledge | Implemented, cited, curated-first |
| Retrieval index | Optional QMD read only; never built or refreshed |
| Private memory | Optional read-only Mnemosyne SQL evidence |
| Provider lifecycle | **Not implemented** — no hooks, prefetch or capture |
| Session history, skills, secret store | Out of scope; referenced only |

## Authority map

| Plane | Intended authority | What belongs here | What does not |
| --- | --- | --- | --- |
| Built-in `MEMORY.md` / `USER.md` | Small bootstrap and stable preferences | Minimal, intentionally curated context | A full knowledge base, secrets, long task histories |
| External memory provider | Approved durable facts and relationships | Atomic facts with provenance, scope, validity, correction links | Unreviewed assistant assertions or blanket transcript capture |
| Markdown knowledge | Inspectable, source-bound reference knowledge | Reviewed claims, citations, explicit uncertainty | Automatic copies of private records |
| Retrieval index | Rebuildable search representation | Indexes and embeddings of approved material | Independent authority over factual truth |
| Session history | Historical conversation evidence | Original messages and task outcomes under retention policy | Automatically trusted durable facts |
| Skills | Reusable procedures | Verified workflows and operational guidance | Credentials and personal-data archives |
| Secret store | Authentication material | Credentials under an appropriate access policy | Public configuration or documentation examples |

Bootstrap injection and provider prefetch are distinct paths. Enabling an external provider does not prove built-in memory or session storage has been disabled.

## What was inspected

The audited stack contains the following. These are findings about sources on disk, not
shipped features unless marked otherwise:

- A Mnemosyne Hermes adapter implementing initialization, provider prompt context, prefetch, explicit tools, turn synchronization, and shutdown. Its turn-capture behavior depends on configuration; the adapter supports excluding conversation roles without removing explicit remember operations.
- A unified recall helper that queries Mnemosyne and QMD, merges and deduplicates results, and can use a text-search fallback when QMD is unavailable or degraded. Curated and bulk material have different ranking treatment. This helper is separate from native provider prefetch; its existence does not establish that every conversation uses it. Wayfinder's `wayfinder_recall` reproduces the citation and curation-tier part of this, not the merge/fallback policy.
- Explicit persistence helpers for Markdown notes, daily summaries, indexes, and concise memory pointers. The checkpoint helper describes per-file atomic replacement and exact-ID memory read-back, **not** a cross-file/database transaction. A partial failure can leave completed local writes in place.
- Criteria-controlled promotion code with dry-run and apply modes, source/link validation, dispute holds, backup handling, and nonzero partial-failure reporting. A separate legacy promoter has weaker structural checks. Having both scripts on disk does not establish which one runs or whether all ingest routes use the stronger policy.
- A retained bridge entrypoint for another backend. Retained scripts are not evidence of a running or authoritative backend and are not portable dependencies by default.

The portability work replaces deployment-specific routing assumptions with explicit inputs.
Existing source was not an installation template to copy wholesale: no helper script is
reused verbatim, and every path is now a setting.

## What this inspection did not prove

No private records were used as documentation fixtures. No live remember/recall round trip, gateway reload, capture-contamination test, scheduler execution, or migration was performed for this documentation pass. Configuration selection and plugin enablement were checked separately, but do not establish initialization, callability, current writing, or cross-session correctness.

Use these evidence labels in release reports:

1. **Documented:** upstream describes the contract.
2. **Source-inspected:** a particular implementation contains the path.
3. **Configured / discovered / enabled:** the relevant control-plane state exists.
4. **Loaded / initialized / callable:** a fresh runtime actually exercised it.
5. **Verified integration:** isolated end-to-end tests establish the promised behavior.

Do not shorten the first three to “working.”

## Native Hermes contract to preserve

Official Hermes documentation describes directory providers with `plugin.yaml`, a Python implementation, and `register(ctx)` calling `ctx.register_memory_provider(...)`. It also documents package entry points. These are host-supported options, not a statement that this project already ships either layout.

None of the following is implemented in 0.1.0. They are the checklist for the provider release,
kept here because the audited stack's adapter already had to solve each one:

- Create a fresh provider instance per load.
- Derive state from the `hermes_home` passed to initialization; do not bind one process-global store to multiple profiles.
- Accept additive lifecycle context through `**kwargs`.
- Keep `is_available()` free of network calls.
- Keep turn synchronization non-blocking and preserve the owning profile context in background work; use the documented `spawn_context_thread` helper.
- Bound recall before injection and report unavailable/degraded states honestly.
- Treat prefetch, turn sync, session-end processing, and pre-compression processing as separate privacy boundaries.

Reference: [Memory Provider Plugins](https://hermes-agent.nousresearch.com/docs/developer-guide/memory-provider-plugin). Exact version support and project acceptance results remain to be recorded.
