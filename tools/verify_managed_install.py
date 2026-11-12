"""Verify a reviewed immutable revision via Hermes in a disposable disabled profile."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('revision', help='Reviewed full 40-character Git commit SHA')
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{40}', args.revision):
        parser.error('a full lowercase commit SHA is required')
    executable = shutil.which('hermes')
    if not executable:
        raise SystemExit('Hermes must already be installed')
    project = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='.managed-check-', dir=project) as temporary:
        root = Path(temporary)
        for name in ('home', 'hermes', 'tmp', 'cache'):
            (root / name).mkdir()
        env = {'PATH': os.environ.get('PATH', os.defpath), 'HOME': str(root / 'home'),
               'HERMES_HOME': str(root / 'hermes'), 'TMPDIR': str(root / 'tmp'),
               'XDG_CACHE_HOME': str(root / 'cache'), 'HERMES_ENABLE_PROJECT_PLUGINS': '0',
               'PYTHONDONTWRITEBYTECODE': '1'}
        # Some editable host installs need their repository root on sys.path.
        if os.environ.get('PYTHONPATH'):
            env['PYTHONPATH'] = os.environ['PYTHONPATH']
        subprocess.run([executable, 'plugins', 'install', 'nosliwhtes/ozark-wayfinder',
                        '--ref', args.revision, '--no-deps', '--no-enable'],
                       cwd=root, env=env, check=True, timeout=180)
        subprocess.run([executable, 'plugins', 'list'], cwd=root, env=env, check=True, timeout=60)
        plugins = root / 'hermes/plugins'
        metadata = json.loads((plugins / '.install-metadata.json').read_text())
        record = metadata['ozark-wayfinder']
        assert record['revision'] == args.revision and record['pinned'] is True, record
        installed = plugins / 'ozark-wayfinder'
        for name in ('__init__.py', 'wayfinder.py', 'backends.py', 'checkpoints.py',
                     'retrieval.py', 'plugin.yaml', 'skills/wayfinder/SKILL.md'):
            assert (installed / name).is_file(), name
        check = """
import os
from pathlib import Path
from hermes_yaml import safe_load
path = Path(os.environ['HERMES_HOME']) / 'config.yaml'
config = safe_load(path.read_text()) if path.exists() else {}
plugins = (config or {}).get('plugins', {})
assert 'ozark-wayfinder' not in plugins.get('enabled', []), plugins
assert not plugins.get('entries', {}).get('ozark-wayfinder', {}).get('enabled', False), plugins
"""
        subprocess.run([sys.executable, '-c', check], cwd=root, env=env, check=True, timeout=30)
        print(json.dumps({'managed_install': 'verified', 'requested_revision': args.revision,
                          'profile': 'disposable', 'enablement_requested': False}))


if __name__ == '__main__':
    main()
