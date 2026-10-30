# Contributing

Small, checkable changes are welcome. The bar is evidence, not enthusiasm.

## Before you open a PR

```sh
python -m unittest discover -s tests -v
python tools/verify_host.py
python tools/verify_bootstrap.py
```

All three must pass for your branch. If you touched a tier, path guard or backend, add or extend
a test that fails without your change — that red run is part of the review.

## Rules that are not negotiable

- **The plugin stays a tool plugin until a provider is designed and tested.** Do not add hooks,
  prefetch or automatic capture as a drive-by.
- **Every result keeps a citation and a tier.** A hit without a locator is not a hit.
- **Failures stay visible.** A broken optional backend degrades the result and reports a code.
  Never substitute synthetic success, an empty list, or a silent fallback.
- **No ambient discovery.** No default vault, no `~/.hermes` guesses, no reading an index that
  was not named in settings.
- **Notes are untrusted data.** The plugin never executes note text and never fetches a link
  found in one.
- **`checkpoint` never overwrites and never writes without `confirm=true`.**
- **Runtime dependencies stay empty** unless a change genuinely needs one. QMD and Mnemosyne are
  optional and externally managed; do not vendor them.
- **No personal data.** No real vault paths, no captured note contents in tests or docs, no
  credentials. Use `example.invalid` and invented facts.

## Scope to leave alone

Provider registration, vector or graph ranking, ingest/promotion queues, secret detection,
encryption, scheduling and an edit/forget API are all out of scope for 0.1.x. Discuss before
building.

## Documentation

If behaviour changes, `IMPLEMENTATION.md`, the root README and any affected doc change in the
same PR. A doc that describes an intention as if it shipped is a bug; mark each claim
implemented, policy or future.

## Commits

`type: subject` with `fix:`, `feat:`, `refactor:`, `docs:` or `chore:`. One concern per commit.
