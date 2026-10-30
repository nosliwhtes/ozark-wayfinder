# Implemented plugin contract and verification

This file describes the working implementation. Other `docs/` pages include an earlier design/research pass; their proposed provider, hook, curation, and migration features are **not** implemented merely because they appear there.

## What ships

Ozark Wayfinder 0.1.0 is a **Hermes directory tool plugin**, not a replacement memory provider. Its manifest uses the installed Hermes v2 plugin contract. It registers exactly three tools, no lifecycle hooks:

- `wayfinder_recall(query, limit=8)`: bounded canonical Markdown keyword scan, optionally augmented by QMD BM25 and read-only Mnemosyne SQL retrieval. Results carry a relative `path:line` citation or a record citation (`mnemosyne:<id>`, or the QMD URI), plus provenance, a backend label and a curation tier; Markdown rows also carry an 800-character snippet. A matched empty note is cited by path alone (`notes/empty.md`, `line: 0`) because it has no line 1. Curated notes sort before unclassified notes, then unlinked episodic memory, then imported notes. Linked memory/QMD hits are resolved against the configured vault, not arbitrary source paths.

  Resolving a linked hit costs a file read, so resolution is capped at `min(2*limit, 100)` per backend. Where the entry budget truncates a directory listing before it is sorted, which notes fall inside the budget follows filesystem order; the retained set is stable for a given directory state but its membership can differ between machines. The cap bounds read attempts — a slot is spent even if the read then fails — and not the candidate pool: hits past it stay in the result set with their own citation and `canonical_note_status: not_resolved`, so a curated hit cannot be demoted out of ranking by an imported one. An unresolved hit still derives its tier from its target path, and the path is validated on both the read and the skipped branch, so an unread hit cannot smuggle a bad path past the guards. The applied cap and the number actually resolved are reported in `resolution`.
- `wayfinder_checkpoint(path, title, summary, confirm=false)`: preview by default; explicit `confirm=true` creates a new Markdown note, refuses overwrite, and verifies persisted bytes. No database writes or automatic indexing. Curation follows the explicitly chosen configured directory; there is no automatic promotion. Title is one line of at most 200 characters and the summary is limited to 4,000 characters; the resulting file must also fit `max_file_bytes`. Hidden paths, traversal, and symlink directories are refused. `confirm` is a mechanical tool argument, not an independent human-authorization system.
- `wayfinder_health()`: one bounded probe per component — vault presence, and a single fixed-term query against each enabled backend. It reports `disabled`, `ok` or a structured error, so an enabled-but-broken engine shows up before recall is attempted. It does **not** certify result quality, schema compatibility, or index freshness.

Canonical notes are read as untrusted data. The plugin neither executes note text nor installs backend code. Results remain visible to the caller/model; local storage is not a guarantee that the host model is offline.

## Configuration

The plugin reads the host's supported `plugins.entries.ozark-wayfinder.settings` object. There are **no discovered or machine-specific data paths** and no bundled personal material.

| Setting | Default / meaning |
| --- | --- |
| `vault_root` | Required existing absolute vault directory |
| `curated_roots` | `["curated"]`, relative directory prefixes |
| `imported_roots` | `["imports", "sources"]`, relative prefixes; cannot overlap curated roots |
| `max_files` | `5000`, visited directory entries (files, directories) before the walk stops |
| `max_file_bytes` | `1048576`, per-file read/write limit |
| `mnemosyne_db` | Omitted/disabled; explicit absolute existing SQLite path when enabled |
| `qmd_workspace` | Omitted/disabled; explicit absolute dedicated QMD workspace when enabled |
| `qmd_command` | `["qmd"]`, trusted executable/argument list, no shell invocation |
| `qmd_timeout` | `10` seconds, greater than zero and at most 60 |

Hermes enablement lives in `plugins.enabled: ["ozark-wayfinder"]`. Put the four runtime files (`__init__.py`, `plugin.yaml`, `wayfinder.py`, `backends.py`) inside the selected **isolated evaluation** home at `plugins/ozark-wayfinder/`, and configure its explicit test vault. This is the exact directory layout exercised by `tests/test_plugin.py`. Do not install/enable into a live profile just to run the verification below.

The Python wheel is independently installable for library use (`ozark_wayfinder.wayfinder`), but does **not** advertise an entry point or automatically activate a Hermes plugin. Runtime Python dependencies are empty; isolated wheel builds declare `setuptools>=68` as the build dependency. The Hermes host and optional external engines remain separately managed dependencies. Python >=3.11 and POSIX filesystem APIs are required; actual execution here used Linux/Python 3.14. Other Python/OS versions were not exercised.

## Optional backends

**QMD:** only the dedicated `wayfinder` index and `vault` collection are queried, using `search`, not embedding/model-backed `query`. The caller must separately build that index. `backends.qmd_environment(workspace)` defines the isolated HOME/HERMES_HOME/XDG/config/index/cache/temp directories; configure QMD with that exact environment. Both child streams are spooled to temp files and polled every 50 ms; a child seen past 2 MiB on either stream is killed and reported `output_limit`, and one still alive at the deadline — or finishing after it — is `timeout`. Reaping is bounded at 5 seconds so a kill that itself fails cannot hang the call. The bound is a poll, not a quota: a child can overshoot between checks and is killed rather than buffered. Commands exercised on synthetic notes were:

```text
qmd --index wayfinder collection add <synthetic-vault> --name vault --mask **/*.md
qmd --index wayfinder search solar -c vault --json -n 50
```

The command receives a minimal environment with no inherited provider credentials, `NODE_OPTIONS`, or ambient QMD overrides. It can still access resources permitted to that executable by the OS; it is not a subprocess/network sandbox. Configure only a trusted executable and dedicated workspace. No automatic reindex, model download, or embedding request is implemented.

**Mnemosyne:** SQLite URI `mode=ro`, `PRAGMA query_only`, no Beam construction during plugin use. The adapter supports the inspected `working_memory` / `episodic_memory` schema, filters expired/superseded entries, and includes only global entries. Session/private scoped rows are excluded; this tool does not implement session-sensitive Mnemosyne retrieval. Linked records must identify `vault_note`; arbitrary source paths do not become file reads. Rows are paged 200 at a time and the 500-row live cap applies after expiry filtering, so a normal backlog of expired rows cannot starve valid matches. Reads still stop at a 5000-row scan cap, plus a single-row probe to see whether anything remains beyond it; when that cap cuts the scan short the backend reports `degraded`/`scan_limit` with whatever it found rather than `ok`. A non-text `valid_until` or `metadata_json` is coerced, and an unusable one degrades to `invalid_expiry` / `invalid_metadata` rather than raising. This is lexical SQL retrieval, **not** Mnemosyne's full graph/vector/temporal recall pipeline. Explicitly pointing the plugin at a database authorizes it to read matching contents; database ownership/scope remains the operator's responsibility.

Disabled components report `disabled`. Enabled components that fail report structured error codes and mark recall degraded while returning usable canonical results. No synthetic success is substituted for a failed backend.

## Repeatable verification

Run in the project directory, with the host interpreter for the host test:

```sh
python -m unittest discover -s tests -v
python tools/verify_host.py
python tools/verify_bootstrap.py
```

- Unit/host suite: **40 tests**, including real PluginManager discovery, scoped registry dispatch, checkpoint preview/create/readback, and unload. Default run intentionally skips the two optional-engine tests.
- Official `hermes plugins doctor ... --ci`: **passed**, manifest `ozark-wayfinder 0.1.0`, **3 tools, 0 hooks**. The script stages only the four runtime files, places HOME/HERMES_HOME/TMPDIR in a disposable project directory, and never enables the live profile.
- Clean bootstrap: builds/installs the declared package in a fresh venv without system-site packages; installed code passes **37 tests**, with **3 intentional skips** (no Hermes, QMD, or Mnemosyne in that environment). An isolated build may fetch its declared setuptools build dependency; this is not an offline-build claim.
- Real optional-engine suite: **40/40 passed, no skips**, using a real installed QMD executable and real Mnemosyne `Beam` to create synthetic disposable data, then querying via this adapter. To repeat, set `RUN_QMD_INTEGRATION=1` and `MNEMOSYNE_PYTHON` to an explicitly selected interpreter containing Mnemosyne before running unittest. The interpreter path is intentionally not embedded here. No personal database or vault is used.

Eleven prior tests pin repaired behaviours; four more were added this round, and each was confirmed RED against the source before its fix and GREEN after: empty-note citation, non-text `valid_until`, 120-row and 501-row expired starvation, the scan budget ignoring directories, QMD stderr overflow on a fast exit, dropped ranking rows under the resolution cap, a QMD list starving memory resolution, and non-object metadata. The remaining additions are coverage only (four-tier ordering, the live row cap, title/summary caps, exact child environment isolation, the plugin-level `verification_failed` code) and passed on first run.

One initial doctor invocation placed TMPDIR below the directory doctor recursively copies and failed with recursive path growth. `tools/verify_host.py` fixes this by staging the runtime source beside, not above, temporary validation state. Its subsequent official CLI run passed.

## Explicit gaps and non-goals

No automatic capture, context injection, `memory` provider registration, prefetch, session/compression hooks, memory synchronization, Mnemosyne writes, semantic/vector/graph recall, QMD indexing, reviewed ingest/promotion queue, secret detector, forget/edit/delete API, encryption, backup/restore, migration, publication, scheduling, or network sandbox. There is no proof of crash/power-loss durability, hostile concurrent directory-renaming resistance, full multi-profile concurrency isolation, or exhaustive Mnemosyne schema/version compatibility. No user data migration, live activation, commit, or publication was performed.
