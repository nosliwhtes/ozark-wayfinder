"""Run the official Hermes plugin doctor without touching a live profile."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
RUNTIME_FILES = ('__init__.py', 'wayfinder.py', 'backends.py', 'plugin.yaml')


def main():
    executable = shutil.which('hermes')
    if not executable:
        raise SystemExit('Hermes host CLI is required; no host will be installed automatically')
    with tempfile.TemporaryDirectory(prefix='.host-check-', dir=PROJECT) as temp:
        root = Path(temp)
        source = root / 'ozark-wayfinder'
        for directory in (source, root / 'home', root / 'hermes', root / 'tmp'):
            directory.mkdir()
        for name in RUNTIME_FILES:
            shutil.copyfile(PROJECT / name, source / name)
        env = {'PATH': os.environ.get('PATH', os.defpath), 'HOME': str(root / 'home'),
               'HERMES_HOME': str(root / 'hermes'), 'TMPDIR': str(root / 'tmp'),
               'PYTHONDONTWRITEBYTECODE': '1', 'HERMES_ENABLE_PROJECT_PLUGINS': '0'}
        subprocess.run([executable, 'plugins', 'doctor', str(source), '--ci'],
                       cwd=root, env=env, check=True, timeout=90)


if __name__ == '__main__':
    main()
