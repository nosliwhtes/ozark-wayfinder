"""Opt-in real engines, exclusively synthetic state in disposable project directories."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from backends import qmd_environment
from wayfinder import Settings, Wayfinder

PROJECT = Path(__file__).resolve().parents[1]


class OptionalIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('RUN_QMD_INTEGRATION') == '1', 'opt in with RUN_QMD_INTEGRATION=1; requires local QMD')
    def test_real_qmd_bm25_in_fresh_isolated_workspace(self):
        executable = shutil.which('qmd')
        self.assertIsNotNone(executable, 'QMD executable required for requested integration test')
        with tempfile.TemporaryDirectory(dir=PROJECT) as temp:
            root = Path(temp)
            vault = root / 'vault'
            (vault / 'curated').mkdir(parents=True)
            (vault / 'curated/fixture.md').write_text('# Fixture\nSynthetic solar decision for a blue meadow.', encoding='utf-8')
            workspace = root / 'qmd'
            workspace.mkdir()
            env = qmd_environment(workspace)
            result = subprocess.run([executable, '--index', 'wayfinder', 'collection', 'add', str(vault),
                                     '--name', 'vault', '--mask', '**/*.md'], env=env, cwd=workspace,
                                    capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            reply = Wayfinder(Settings(vault, qmd_workspace=workspace, qmd_command=(executable,), qmd_timeout=60)).recall('solar')
            self.assertEqual(reply['backends']['qmd']['status'], 'ok', reply)
            self.assertIn('qmd', [p['backend'] for p in reply['results'][0]['provenance']], reply)
            self.assertEqual(reply['results'][0]['tier'], 'curated')

    @unittest.skipUnless(os.environ.get('MNEMOSYNE_PYTHON'), 'set MNEMOSYNE_PYTHON to an interpreter with real Mnemosyne installed')
    def test_real_mnemosyne_beam_write_then_readonly_adapter(self):
        with tempfile.TemporaryDirectory(dir=PROJECT) as temp:
            root = Path(temp)
            (root / 'home').mkdir()
            (root / 'hermes').mkdir()
            (root / 'vault/curated').mkdir(parents=True)
            env = {'PATH': os.environ.get('PATH', os.defpath), 'HOME': str(root / 'home'),
                   'HERMES_HOME': str(root / 'hermes'), 'MNEMOSYNE_HOME': str(root / 'mnemosyne'),
                   'XDG_CONFIG_HOME': str(root / 'config'), 'XDG_CACHE_HOME': str(root / 'cache'),
                   'XDG_DATA_HOME': str(root / 'data'), 'TMPDIR': str(root), 'PYTHONDONTWRITEBYTECODE': '1',
                   'MNEMOSYNE_AUTO_MIGRATE': '0', 'PYTHONPATH': str(PROJECT)}
            script = r'''
import socket

def blocked(*args, **kwargs):
    raise AssertionError('network disabled in synthetic integration')
socket.socket.connect = blocked
socket.create_connection = blocked
from pathlib import Path
import hashlib, json
from mnemosyne.core.beam import BeamMemory
from wayfinder import Settings, Wayfinder
root = Path.cwd()
db = root / 'synthetic.db'
beam = BeamMemory(session_id='synthetic', db_path=db, use_cloud=False)
memory_id = beam.remember('Synthetic solar preference for a blue meadow.', scope='global',
                          source='synthetic-fixture', extract=False, extract_entities=False,
                          metadata={'vault_note': 'curated/fixture.md'})
assert memory_id is not None
beam.conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
beam.conn.close()
(root / 'vault/curated/fixture.md').write_text('Synthetic solar decision.', encoding='utf-8')
before = hashlib.sha256(db.read_bytes()).hexdigest()
reply = Wayfinder(Settings(root / 'vault', mnemosyne_db=db)).recall('solar')
assert reply['backends']['mnemosyne']['status'] == 'ok', reply
assert any(p['backend'] == 'mnemosyne' for p in reply['results'][0]['provenance']), reply
assert hashlib.sha256(db.read_bytes()).hexdigest() == before
print(json.dumps({'integration': 'real BeamMemory remember + readonly adapter', 'network': 'blocked', 'status': 'ok'}))
'''
            result = subprocess.run([os.environ['MNEMOSYNE_PYTHON'], '-c', script], env=env, cwd=root,
                                    capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1])['status'], 'ok')
