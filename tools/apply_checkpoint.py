#!/usr/bin/env python3
"""Apply a Wayfinder checkpoint in a human terminal. No --yes or stdin JSON mode."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from checkpoints import apply_interactive
from wayfinder import Settings, Wayfinder


def read_json(path, limit):
    with Path(path).open('rb') as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('input exceeds size limit')
    return json.loads(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, help='Operator-owned JSON Settings file, including enable_checkpoint=true and checkpoint_profile_id')
    parser.add_argument('proposal', help='Saved JSON proposal returned by wayfinder_checkpoint')
    args = parser.parse_args()
    try:
        settings = read_json(args.config, 65536)
        if not isinstance(settings, dict):
            raise ValueError('configuration must be a JSON object')
        service = Wayfinder(Settings(**settings))
        proposal = read_json(args.proposal, 65536)
        print(json.dumps(apply_interactive(service, proposal)))
        return 0
    except RuntimeError:
        print('Checkpoint may have been published but verification failed. Inspect the target before retrying.', file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError):
        print('Checkpoint not applied. Check capability, proposal, expiry, scope, and destination; existing notes are never replaced.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
