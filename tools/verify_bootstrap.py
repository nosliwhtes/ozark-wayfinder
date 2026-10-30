"""Exercise the distributable in a fresh venv using only declared dependencies.

All writable HOME, HERMES_HOME, caches, build temporaries, and the venv are
inside a disposable project directory. Does not install or enable a live plugin.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='.bootstrap-', dir=PROJECT) as temp:
        root = Path(temp)
        for name in ('home', 'hermes', 'cache', 'tmp'):
            (root / name).mkdir()
        env = {'PATH': os.environ.get('PATH', os.defpath), 'HOME': str(root / 'home'),
               'HERMES_HOME': str(root / 'hermes'), 'TMPDIR': str(root / 'tmp'),
               'XDG_CACHE_HOME': str(root / 'cache'), 'PIP_CACHE_DIR': str(root / 'cache'),
               'PYTHONDONTWRITEBYTECODE': '1'}
        subprocess.run([sys.executable, '-m', 'venv', str(root / 'venv')], env=env, check=True)
        python = root / 'venv/bin/python'
        staged = root / 'source'
        staged.mkdir()
        for name in ('__init__.py', 'wayfinder.py', 'backends.py', 'plugin.yaml', 'pyproject.toml'):
            shutil.copyfile(PROJECT / name, staged / name)
        subprocess.run([str(python), '-m', 'pip', '--isolated', '--disable-pip-version-check',
                        'install', '--no-cache-dir', str(staged)], cwd=root, env=env, check=True)
        script = '''
from pathlib import Path
import importlib.util, sys, unittest
import ozark_wayfinder
from ozark_wayfinder.wayfinder import Settings, Wayfinder
assert importlib.util.find_spec('hermes_cli') is None, 'must not leak host Hermes into bootstrap'
assert importlib.util.find_spec('mnemosyne') is None, 'must not leak host Mnemosyne into bootstrap'
sys.path.insert(0, str(Path(ozark_wayfinder.__file__).parent))
suite = unittest.defaultTestLoader.discover(sys.argv[1])
result = unittest.TextTestRunner(verbosity=2).run(suite)
assert len(result.skipped) == 3, result.skipped
raise SystemExit(not result.wasSuccessful())
'''
        subprocess.run([str(python), '-I', '-c', script, str(PROJECT / 'tests')],
                       cwd=root, env=env, check=True)
    print('CLEAN_BOOTSTRAP_OK (installed package, no runtime dependencies; real-engine tests intentionally skipped)')


if __name__ == '__main__':
    main()
