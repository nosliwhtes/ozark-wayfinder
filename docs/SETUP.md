# Setup and the evaluation gate

## Install (isolated profile first)

The [root README](../README.md) has the full contract. The short version, in a throwaway
`HERMES_HOME` rather than your lived-in profile:

```sh
mkdir -p "$HERMES_HOME/plugins/ozark-wayfinder"
cp __init__.py plugin.yaml wayfinder.py backends.py "$HERMES_HOME/plugins/ozark-wayfinder/"
hermes plugins enable ozark-wayfinder
hermes config set plugins.entries.ozark-wayfinder.settings.vault_root /absolute/path/to/vault
```

There is no entry point and no installer. The wheel exists for library use only.

## Prerequisites

1. **Host:** a Hermes build with the v2 plugin contract, Python >= 3.11, POSIX filesystem APIs. Only Linux/Python 3.14 was exercised; certify your own combination before trusting it.
2. **Vault:** an existing absolute directory you own, laid out so curated and imported material are separable by prefix. Nothing is discovered for you.
3. **Dependencies:** none for the plugin itself. QMD and Mnemosyne are optional and stay separately managed — the plugin never installs, updates or imports either.
4. **Storage:** write access only to the vault (for `checkpoint`) and read access to the Mnemosyne file if you enable it. Keep runtime state out of the source tree.

## Hermes preflight

These inspect the selected profile. Status output can reveal local metadata; review before
pasting it into a public issue.

```sh
hermes plugins list
hermes plugins doctor . --ci
hermes memory status
```

`doctor --ci` executes plugin registration. It uses an isolated Hermes home but is not a
security sandbox — read the code first. It is not proof of activation on the live owner.

## Evaluation gate before live use

Run against disposable state and synthetic notes only. Require, per point, actual output:

- `wayfinder_health` reports the vault present and every unconfigured backend `disabled`.
- `wayfinder_recall` returns synthetic hits with `path:line` citations and tiers, curated first.
- A query in a note outside both tier prefixes still returns, between curated and imported.
- `wayfinder_checkpoint` previews without writing; `confirm=true` writes and reports `verified`;
  the same path again returns `target_exists` and leaves the file untouched.
- Relative traversal (`../`), a hidden directory and a symlinked directory are all refused.
- With `mnemosyne_db` set to a file whose `working_memory` table is missing, recall still returns
  Markdown results and marks the memory backend degraded — no fabricated hits.
- With QMD enabled but unreachable, the same: results plus a visible error.
- Nothing appears outside the vault, no network call is made with QMD disabled, and no message,
  schedule or index is created.

Optional-engine coverage is opt-in:

```sh
RUN_QMD_INTEGRATION=1 MNEMOSYNE_PYTHON=/path/to/interpreter-with-mnemosyne \
  python -m unittest discover -s tests -v
```

## Before trusting it with a real vault

- Confirm the vault has a backup you have actually restored once. Wayfinder has no backup,
  encryption, migration or rollback.
- Decide whether notes returned by recall are safe for the model reading the session. They leave
  the machine.
- Agree who may call `confirm=true`. The flag is a mechanical argument, not a human approval
  system — a model can set it.
- Leave `vault_root` unwritten in a shared config until you have chosen a real directory;
  a missing setting fails closed with `configuration_error`.

## Known gaps to accept knowingly

No capture hooks, prefetch, provider registration, vector or graph recall, index building,
promotion queue, edit/forget API, secret detection, encryption, or scheduling. Mnemosyne is read
as SQL evidence only — its graph, vector and temporal ranking are not used. Session-scoped
Mnemosyne rows are excluded entirely. See [IMPLEMENTATION.md](../IMPLEMENTATION.md).

## Resolved and open decisions

Resolved for 0.1.0:

- Tool plugin, not a provider. The provider checklist lives in [STACK.md](STACK.md).
- Identifier `ozark-wayfinder`, directory layout `plugins/ozark-wayfinder/`, four runtime files.
- Empty runtime dependencies; QMD and Mnemosyne optional and externally managed.
- Explicit-only writes; all paths supplied by settings; no ambient discovery.

Still open:

- Provider release with prefetch and capture, plus the contamination and cross-profile tests.
- Secret detection before write; session-scoped memory retrieval; vector or graph ranking.
- Recovery semantics for interrupted checkpoints and concurrent writers.
- A tested Python/OS matrix beyond Linux/Python 3.14.
