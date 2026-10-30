# Ozark Wayfinder

Local, cited recall over your own Markdown knowledge — with optional read-only evidence from
QMD and Mnemosyne — as a Hermes plugin.

Wayfinder keeps your notes as the source of truth. Every hit is either a real file and line or a
record citation, and curated notes always outrank imported ones. Nothing is captured
automatically, nothing is uploaded, and a failed optional backend is reported instead of hidden.

```
wayfinder_recall      cited Markdown scan, optionally + QMD BM25 + Mnemosyne evidence
wayfinder_checkpoint  preview-first, create-only Markdown note with byte readback
wayfinder_health      one bounded local probe per component, never a download
```

Requires Hermes with the v2 plugin contract, Python >= 3.11 and POSIX filesystem APIs.
Only Linux/Python 3.14 was exercised here.

> **Scope, plainly:** this is a **tool plugin**, not a `memory:` provider replacement.
> It does not hook turns, inject context, capture conversations, write Mnemosyne, or build
> indexes. See [docs/STACK.md](docs/STACK.md) for why that boundary is deliberate and
> [IMPLEMENTATION.md](IMPLEMENTATION.md) for the full contract and gaps.

## Install

Copy the four runtime files into an isolated profile's plugin directory:

```sh
mkdir -p "$HERMES_HOME/plugins/ozark-wayfinder"
cp __init__.py plugin.yaml wayfinder.py backends.py "$HERMES_HOME/plugins/ozark-wayfinder/"
```

Enable it and point it at a vault you own:

```sh
hermes plugins enable ozark-wayfinder
hermes config set plugins.entries.ozark-wayfinder.settings.vault_root /absolute/path/to/vault
```

Restart the session owner, then confirm the three tools appear. `vault_root` has no default —
Wayfinder never guesses a directory or reads an ambient index.

The wheel is independently installable for library use (`ozark_wayfinder.wayfinder`) but does
not declare an entry point or self-activate.

## Configure

Settings live under `plugins.entries.ozark-wayfinder.settings`.

| Setting | Default | Meaning |
| --- | --- | --- |
| `vault_root` | — | Required existing absolute vault directory |
| `curated_roots` | `["curated"]` | Relative prefixes sorted first |
| `imported_roots` | `["imports", "sources"]` | Relative prefixes sorted last; must not overlap curated |
| `max_files` | `5000` | Directory entries visited before the walk stops |
| `max_file_bytes` | `1048576` | Per-file read and write limit |
| `mnemosyne_db` | off | Absolute SQLite path, opened `mode=ro` with `query_only` |
| `qmd_workspace` | off | Absolute dedicated QMD workspace |
| `qmd_command` | `["qmd"]` | Trusted argv list, no shell |
| `qmd_timeout` | `10` | Seconds, `0 < t <= 60` |

Tiering is by prefix, so a vault laid out as `curated/…` and `imports/…` works out of the box;
any other layout just needs the two root lists adjusted. Notes outside both lists are still
recallable, ranked between curated and imported.

### Optional QMD

Wayfinder queries only the dedicated `wayfinder` index and `vault` collection via `search`
(lexical BM25, no embedding call). Build it yourself with the isolated environment that
`backends.qmd_environment(workspace)` defines:

```sh
qmd --index wayfinder collection add /absolute/path/to/vault --name vault --mask **/*.md
```

### Optional Mnemosyne

Point `mnemosyne_db` at a SQLite file to add lexical evidence from `working_memory` and
`episodic_memory`. Only global rows are read; expired, superseded and session-scoped rows are
excluded. A record that names an existing `vault_note` resolves to that note; a record with no
usable target stays a record citation, so a stale path can never force a foreign read.

## What verification has shown

Run from the project root, with the host interpreter for the host test:

```sh
python -m unittest discover -s tests -v   # 36 tests, 0 failures
python tools/verify_host.py               # official doctor: 3 tools, 0 hooks
python tools/verify_bootstrap.py          # clean-venv wheel install: 33 passed, 3 expected skips
```

- Default suite: **34 pass, 2 optional skips**. The suite includes a real `PluginManager`
  discovery, scoped dispatch, checkpoint readback and unload in an isolated `HOME`/`HERMES_HOME`
  — no mocked plugin context.
- `RUN_QMD_INTEGRATION=1 MNEMOSYNE_PYTHON=<interpreter-with-mnemosyne>` against a real QMD
  binary and a real Mnemosyne database: **36/36, no skips**, all on synthetic data.
- Clean bootstrap builds and installs the wheel in a fresh venv with declared dependencies only.

Both verification scripts stay inside the project and never enable the live profile.

## Guarantees and non-guarantees

- Note text is untrusted data. The plugin never executes it and never installs backend code.
- Recall is bounded; every result carries a citation, a source label and a tier. An empty matched
  note is cited by path alone rather than a line number it does not have.
- A disabled backend reports `disabled`; an enabled one that fails reports a structured error
  and marks the recall degraded while still returning canonical results. No synthetic success.
- `checkpoint` previews by default, refuses overwrite, and verifies the persisted bytes.
- `health` runs one fixed-term probe per enabled component and reports `disabled`, `ok` or a
  structured error. It does not grade result quality, schema compatibility or index freshness.
- No encryption, secret detection, capture, vector/graph recall, indexing, promotion queue,
  edit/forget API, backup, migration, or sandbox. QMD can reach whatever the OS permits.

Local storage is not a confidentiality guarantee: recall output still travels to whichever
model reads it. See [docs/PRIVACY.md](docs/PRIVACY.md).

## Documentation

- [docs/STACK.md](docs/STACK.md) — authority boundaries and the evidence ladder
- [docs/PRIVACY.md](docs/PRIVACY.md) — disclosure surfaces and review checks
- [docs/CURATION.md](docs/CURATION.md) — what belongs in memory vs. knowledge
- [docs/SETUP.md](docs/SETUP.md) — evaluation gate before live use
- [IMPLEMENTATION.md](IMPLEMENTATION.md) — exact implemented contract and gaps

## License

MIT. See [LICENSE](LICENSE).
