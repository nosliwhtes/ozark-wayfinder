# Documentation

The product overview, install and configuration live in the [root README](../README.md).
The exact implemented contract, and every deliberate gap, is in
[IMPLEMENTATION.md](../IMPLEMENTATION.md). Read those two first.

This directory holds the reasoning that is easy to lose in a changelog:

| File | Answers |
| --- | --- |
| [STACK.md](STACK.md) | Which layer owns which kind of information, and how strongly any claim is evidenced. |
| [PRIVACY.md](PRIVACY.md) | Where local data still leaks, and what to check before sharing a vault or an export. |
| [CURATION.md](CURATION.md) | What belongs in durable memory, in Markdown, in a skill — and what belongs nowhere. |
| [SETUP.md](SETUP.md) | The evaluation gate that has to pass before this touches a lived-in profile. |
| [examples/](examples/README.md) | Synthetic record, page and scenario shapes. Not import files, not tool payloads. |

## Status of the documentation itself

`STACK.md`, `PRIVACY.md` and `CURATION.md` were written from a read-only audit of an existing
personal memory stack before this plugin had a working build. They are **policy and boundary
documents**: the shipped 0.1.0 satisfies part of what they argue for and satisfies other parts
trivially by not implementing the risky surface at all.

Where a section would be misread as a feature claim, it now says which of the two it is.
Anything still phrased as an intention is future work, and `IMPLEMENTATION.md` wins in a conflict.

## Architecture in one picture

```text
explicit recall ---> wayfinder_recall ---> cited Markdown (curated, then unclassified, then imported)
                                    \--> optional QMD BM25 (read)
                                    \--> optional Mnemosyne evidence (read-only, global)

explicit write  ---> wayfinder_checkpoint ---> new Markdown note, create-only, byte-verified
```

Nothing else runs. No turn hooks, no prefetch, no capture, no background writer, no index build.

## Why the plugin does not claim to be a memory provider

The audited stack does more: a `MemoryProvider` with prefetch, turn synchronization, provider
prompt context and a curated bootstrap store. Porting all of that into one release would have
meant guessing at a lifecycle boundary that needed its own capture-contamination and
cross-profile isolation testing.

Shipping a narrow tool plugin first keeps every claim checkable: three tools, real citations,
visible degradation. `STACK.md` keeps the provider contract written down so a later release can
add it against a spec instead of rediscovering it.

## Upstream references

- [Hermes memory system](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory)
- [Memory provider plugins](https://hermes-agent.nousresearch.com/docs/developer-guide/memory-provider-plugin)
- [Native plugin development](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins)

Contracts upstream can change. These are references, not a compatibility promise.
