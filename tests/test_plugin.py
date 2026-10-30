"""Real Hermes integration: isolated subprocess, no mock PluginContext."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]


def _host_root():
    """Directory that must be on sys.path for a real Hermes import."""
    spec = importlib.util.find_spec('hermes_cli')
    if spec is None or not spec.origin:
        return PROJECT
    return Path(spec.origin).resolve().parents[1]


HOST_ROOT = _host_root()


@unittest.skipUnless(importlib.util.find_spec('hermes_cli'), 'requires a Hermes host environment')
class HermesIntegrationTests(unittest.TestCase):
    def test_real_discovery_dispatch_readback_and_unload(self):
        with tempfile.TemporaryDirectory(dir=PROJECT) as temporary:
            root = Path(temporary)
            home = root / 'home'
            hermes = root / 'hermes'
            vault = root / 'vault'
            for folder in (home, hermes, vault):
                folder.mkdir()
            (vault / 'curated').mkdir()
            (vault / 'curated/fixture.md').write_text('Synthetic solar decision.\n')
            plugin = hermes / 'plugins/ozark-wayfinder'
            plugin.mkdir(parents=True)
            import shutil
            for name in ('__init__.py', 'wayfinder.py', 'backends.py', 'plugin.yaml'):
                shutil.copyfile(PROJECT / name, plugin / name)
            # JSON is a YAML subset; avoids undeclared test-only YAML deps.
            config = {'plugins': {'enabled': ['ozark-wayfinder'], 'entries': {
                'ozark-wayfinder': {'settings': {'vault_root': str(vault)}}}}}
            (hermes / 'config.yaml').write_text(json.dumps(config))
            env = {'PATH': os.environ.get('PATH', os.defpath), 'HOME': str(home),
                   'HERMES_HOME': str(hermes), 'TMPDIR': str(root),
                   'PYTHONDONTWRITEBYTECODE': '1', 'HERMES_ENABLE_PROJECT_PLUGINS': '0',
                   # The editable install resolves hermes_cli but not the repo-root
                   # top-level modules (hermes_yaml); a real launch inserts the root.
                   'PYTHONPATH': str(HOST_ROOT)}
            code = '''
import json, os
from pathlib import Path
from hermes_cli.plugins import PluginManager
from tools.registry import registry
home = Path(os.environ['HERMES_HOME'])
manager = PluginManager()
manager.discover_and_load()
loaded = manager._plugins['ozark-wayfinder']
assert loaded.enabled, loaded.error
assert {n for n in manager._plugin_tool_names if n.startswith('wayfinder_')} == {'wayfinder_recall', 'wayfinder_health', 'wayfinder_checkpoint'}, manager._plugin_tool_names
recall = json.loads(registry.dispatch('wayfinder_recall', {'query': 'solar'}, scope=manager.scope_key, session_id='synthetic'))
assert recall['results'][0]['citation'] == 'curated/fixture.md:1', recall
health = json.loads(registry.dispatch('wayfinder_health', {}, scope=manager.scope_key))
assert health['mnemosyne']['status'] == 'disabled', health
args = {'path': 'curated/new.md', 'title': 'Synthetic checkpoint', 'summary': 'Synthetic battery choice.'}
preview = json.loads(registry.dispatch('wayfinder_checkpoint', args, scope=manager.scope_key))
assert preview['status'] == 'preview', preview
args['confirm'] = True
created = json.loads(registry.dispatch('wayfinder_checkpoint', args, scope=manager.scope_key))
assert created['status'] == 'created' and created['verified'], created
assert 'Synthetic battery choice.' in (home.parent / 'vault/curated/new.md').read_text()
invalid = json.loads(registry.dispatch('wayfinder_recall', {'query': ''}, scope=manager.scope_key))
assert invalid['error']['code'] == 'invalid_input', invalid
manager.unload('ozark-wayfinder')
assert registry.get_entry('wayfinder_recall', scope=manager.scope_key) is None
print(json.dumps({'discovery': 'ok', 'tools': 3, 'dispatch': 'ok', 'checkpoint_readback': 'ok', 'unload': 'ok'}))
'''
            run = subprocess.run([sys.executable, '-c', code], env=env, cwd=root,
                                 capture_output=True, text=True, timeout=90)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            result = json.loads(run.stdout.splitlines()[-1])
            self.assertEqual(result['checkpoint_readback'], 'ok')


if __name__ == '__main__':
    unittest.main()
