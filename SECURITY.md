# Security

## What this plugin can touch

- Reads `.md` files under `vault_root` until `max_files` directory entries have been visited, skipping hidden entries and symlinks, up to `max_file_bytes` each. Optional-backend hits are re-read under their own separate per-backend budgets, which run after the walk.
- Writes one new `.md` file inside `vault_root` when `confirm=true`.
- Runs the `qmd_command` argv as a subprocess, if `qmd_workspace` is set.
- Opens `mnemosyne_db` read-only, if set.

It makes no network call itself, installs nothing, and never executes note text.

## Boundaries to be clear about

- `qmd_command` is a trusted argv list, not a shell. A malicious or replaced `qmd` binary is
  outside the model — it runs with your OS permissions.
- Note text flows back to whichever model reads the session. Local storage is not confidentiality.
- Mnemosyne is read as SQLite evidence only. Pointing the plugin at a database lets it read
  matching global rows from it.
- Path guards reject `../` traversal and hidden components. Every sync-file open is
  descriptor-relative with `O_NOFOLLOW` (SQLite and the QMD temp files are opened by their own
  libraries). Symlinked directories are pruned from scans and symlinked *files* are skipped, so a
  note is only reachable through real directories and real files under `vault_root`.
- There is no encryption, secret scanning, sandbox, rate limit or audit log.

## Reporting

Open an issue with the affected version, a synthetic reproduction, and the observed output. Do
not paste real vault contents, memory records or credential values without checking them first;
redact paths and note bodies.

For an exploitable path escape, write/verify bypass, or command-injection style bypass, say so in
the title. Fixes land on `main` with a regression test.
