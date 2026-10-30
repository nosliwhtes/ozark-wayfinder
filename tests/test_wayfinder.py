import importlib
import tempfile
import unittest
from pathlib import Path


class RecallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.vault = self.root / 'vault'
        self.vault.mkdir()

    def service(self, **kw):
        module = importlib.import_module('wayfinder')
        return module.Wayfinder(module.Settings(vault_root=self.vault, **kw))

    def test_markdown_recall_is_cited_and_curated_first(self):
        for folder in ('curated', 'imports'):
            (self.vault / folder).mkdir()
        (self.vault / 'curated/a.md').write_text('# Plan\nSolar battery decision.\n')
        (self.vault / 'imports/a.md').write_text('Solar battery battery battery\n')
        reply = self.service().recall('solar battery')
        self.assertEqual(reply['results'][0]['tier'], 'curated')
        self.assertEqual(reply['results'][0]['citation'], 'curated/a.md:2')
        self.assertEqual(reply['results'][1]['tier'], 'imported')
        self.assertEqual(reply['backends']['markdown']['status'], 'ok')
        self.assertEqual(reply['backends']['qmd']['status'], 'disabled')
        self.assertEqual(reply['backends']['mnemosyne']['status'], 'disabled')

    def test_symlink_escape_and_hidden_files_never_read(self):
        outside = self.root / 'private.md'
        outside.write_text('outside sentinel')
        (self.vault / 'escape.md').symlink_to(outside)
        (self.vault / '.secret.md').write_text('outside sentinel')
        self.assertEqual(self.service().recall('sentinel')['results'], [])

    def test_validation_and_scan_errors_are_visible(self):
        service = self.service(max_files=1)
        for name in ('a.md', 'b.md'):
            (self.vault / name).write_text('alpha')
        reply = service.recall('alpha')
        self.assertTrue(reply['degraded'])
        self.assertEqual(reply['backends']['markdown']['code'], 'scan_limit')
        for query, limit in [('', 2), ('alpha', 0), ('a' * 2001, 2)]:
            with self.assertRaises(ValueError):
                service.recall(query, limit)

    def test_explicit_absolute_paths_and_relative_tiers(self):
        from wayfinder import Settings, Wayfinder
        for kwargs in [{'vault_root': Path('.')}, {'vault_root': self.vault, 'curated_roots': ('../',)},
                       {'vault_root': self.vault, 'curated_roots': ('imports',)}]:
            with self.assertRaises(ValueError):
                Wayfinder(Settings(**kwargs))

    def test_checkpoint_is_explicit_create_only_and_readback_verified(self):
        service = self.service()
        plan = service.checkpoint('curated/decision.md', 'Battery decision', 'Choose solar.', confirm=False)
        self.assertEqual(plan['status'], 'preview')
        self.assertFalse((self.vault / 'curated').exists())
        result = service.checkpoint('curated/decision.md', 'Battery decision', 'Choose solar.', confirm=True)
        self.assertEqual(result['status'], 'created')
        self.assertTrue(result['verified'])
        text = (self.vault / 'curated/decision.md').read_text()
        self.assertIn('Choose solar.', text)
        self.assertEqual(service.recall('solar')['results'][0]['tier'], 'curated')
        with self.assertRaises(FileExistsError):
            service.checkpoint('curated/decision.md', 'Other', 'Changed.', confirm=True)
        for path in ('../escape.md', '/escape.md', '.hidden/x.md'):
            with self.assertRaises(ValueError):
                service.checkpoint(path, 'Title', 'Summary', confirm=True)
        with self.assertRaises(ValueError):
            service.checkpoint('x.md', 'Title', '', confirm=True)

    def test_checkpoint_rejects_symlink_directory(self):
        (self.vault / 'curated').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.service().checkpoint('curated/escape.md', 'Title', 'Summary', confirm=True)
        self.assertFalse((self.root / 'escape.md').exists())

    def test_health_reports_optional_components_without_faking_success(self):
        health = self.service().health()
        self.assertEqual(health['markdown']['status'], 'ok')
        self.assertEqual(health['qmd']['status'], 'disabled')
        self.assertEqual(health['mnemosyne']['status'], 'disabled')

    def test_mnemosyne_sqlite_is_readonly_scoped_and_provenance_aware(self):
        import sqlite3
        from hashlib import sha256
        database = self.root / 'memory.sqlite3'
        with __import__('contextlib').closing(sqlite3.connect(database)) as conn:
            conn.execute('CREATE TABLE working_memory (id TEXT, content TEXT, source TEXT, metadata_json TEXT, scope TEXT, session_id TEXT, superseded_by TEXT, valid_until TEXT)')
            rows = [('global-id', 'solar decision', 'checkpoint', '{"vault_note":"curated/a.md"}', 'global', 'other', None, None),
                    ('private-id', 'solar private', 'conversation', '{}', 'session', 'other', None, None),
                    ('expired-id', 'solar old', 'conversation', '{}', 'global', 'other', None, '2000-01-01'),
                    ('superseded-id', 'solar old', 'conversation', '{}', 'global', 'other', 'new', None)]
            conn.executemany('INSERT INTO working_memory VALUES (?,?,?,?,?,?,?,?)', rows)
            conn.commit()
        (self.vault / 'curated').mkdir()
        (self.vault / 'curated/a.md').write_text('solar canonical')
        before = sha256(database.read_bytes()).hexdigest()
        reply = self.service(mnemosyne_db=database).recall('solar')
        self.assertEqual(reply['backends']['mnemosyne']['status'], 'ok')
        self.assertEqual(len(reply['results']), 1)
        self.assertEqual({p['backend'] for p in reply['results'][0]['provenance']}, {'markdown', 'mnemosyne'})
        self.assertEqual(before, sha256(database.read_bytes()).hexdigest())
        self.assertEqual(reply['results'][0]['tier'], 'curated')
        self.assertEqual(self.service(mnemosyne_db=database).health()['mnemosyne']['status'], 'ok')

    def test_missing_optional_backends_and_bad_schema_degrade_visibly(self):
        import sqlite3
        database = self.root / 'empty.sqlite3'
        with __import__('contextlib').closing(sqlite3.connect(database)):
            pass
        (self.vault / 'a.md').write_text('solar')
        for database_path, expected in [(database, 'unsupported_schema'), (self.root / 'missing.sqlite3', 'missing_database')]:
            reply = self.service(mnemosyne_db=database_path, qmd_workspace=self.root / 'qmd').recall('solar')
            self.assertTrue(reply['degraded'])
            self.assertEqual(reply['backends']['mnemosyne']['code'], expected)
            self.assertEqual(reply['backends']['qmd']['code'], 'uninitialized_workspace')
            self.assertEqual(reply['results'][0]['path'], 'a.md')
        self.assertFalse((self.root / 'qmd').exists())

    def test_mnemosyne_unlinked_memory_keeps_episodic_tier(self):
        import sqlite3
        database = self.root / 'memory.sqlite3'
        with __import__('contextlib').closing(sqlite3.connect(database)) as conn:
            conn.execute('CREATE TABLE episodic_memory (id TEXT, content TEXT, source TEXT, metadata_json TEXT, scope TEXT, session_id TEXT, superseded_by TEXT, valid_until TEXT)')
            conn.execute("INSERT INTO episodic_memory VALUES ('memory-id', 'solar episode', 'conversation', '{}', 'global', 'x', NULL, NULL)")
            conn.commit()
        reply = self.service(mnemosyne_db=database).recall('solar')
        self.assertEqual(reply['results'][0]['tier'], 'episodic')
        self.assertEqual(reply['results'][0]['citation'], 'mnemosyne:episodic_memory:memory-id')
        self.assertEqual(reply['results'][0]['source_label'], 'conversation')

    def test_disappearing_vault_reports_walk_error(self):
        service = self.service()
        self.vault.rmdir()
        reply = service.recall('solar')
        self.assertTrue(reply['degraded'])
        self.assertEqual(reply['backends']['markdown']['errors'][0]['code'], 'walk_error')

    def test_tier_order_is_total_and_curated_wins(self):
        """All four tiers must be strictly ordered, not merely curated-first."""
        import sqlite3
        for name in ('curated', 'imports', 'other'):
            (self.vault / name).mkdir()
        for name in ('curated/a.md', 'other/b.md', 'imports/c.md'):
            (self.vault / name).write_text('solar\n')
        database = self.root / 'memory.sqlite3'
        with __import__('contextlib').closing(sqlite3.connect(database)) as conn:
            conn.execute('CREATE TABLE working_memory (id TEXT, content TEXT, source TEXT, metadata_json TEXT, scope TEXT, session_id TEXT, superseded_by TEXT, valid_until TEXT)')
            conn.execute("INSERT INTO working_memory VALUES ('m', 'solar', 'conversation', '{}', 'global', 's', NULL, NULL)")
            conn.commit()
        reply = self.service(mnemosyne_db=database).recall('solar')
        self.assertEqual([r['tier'] for r in reply['results']],
                         ['curated', 'unclassified', 'episodic', 'imported'])

    def test_empty_canonical_note_is_cited_by_path_only(self):
        import sqlite3
        database = self.root / 'memory.sqlite3'
        with __import__('contextlib').closing(sqlite3.connect(database)) as conn:
            conn.execute('CREATE TABLE working_memory (id TEXT, content TEXT, source TEXT, metadata_json TEXT, scope TEXT, session_id TEXT, superseded_by TEXT, valid_until TEXT)')
            conn.execute("INSERT INTO working_memory VALUES ('e', 'solar', 'x', '{\"vault_note\":\"empty.md\"}', 'global', 's', NULL, NULL)")
            conn.commit()
        (self.vault / 'empty.md').write_text('')
        row = self.service(mnemosyne_db=database).recall('solar')['results'][0]
        self.assertEqual(row['citation'], 'empty.md')
        self.assertEqual(row['line'], 0)
        self.assertEqual(row['content'], '')

    def test_malformed_valid_until_degrades_instead_of_raising(self):
        import sqlite3
        # Untyped column, so SQLite keeps the storage class: SQLite is loosely typed in practice.
        cases = ((20260101, 'ok'), (b'2030-01-01', 'invalid_expiry'), ('whenever', 'invalid_expiry'))
        for index, (value, expected) in enumerate(cases):
            database = self.root / f'memory-{index}.sqlite3'
            with __import__('contextlib').closing(sqlite3.connect(database)) as conn:
                conn.execute('CREATE TABLE working_memory (id, content, source, metadata_json, scope, session_id, superseded_by, valid_until)')
                conn.execute("INSERT INTO working_memory VALUES ('m', 'solar', 'x', '{}', 'global', 's', NULL, ?)", (value,))
                conn.commit()
            reply = self.service(mnemosyne_db=database).recall('solar')
            self.assertEqual(reply['backends']['mnemosyne'].get('code', 'ok'), expected)

    def test_expired_rows_do_not_consume_the_row_cap(self):
        import sqlite3
        database = self.root / 'memory.sqlite3'
        with __import__('contextlib').closing(sqlite3.connect(database)) as conn:
            conn.execute('CREATE TABLE working_memory (id TEXT, content TEXT, source TEXT, metadata_json TEXT, scope TEXT, session_id TEXT, superseded_by TEXT, valid_until TEXT)')
            conn.executemany("INSERT INTO working_memory VALUES (?, 'solar', 'x', '{}', 'global', 's', NULL, ?)",
                             [(f'old-{i}', '2000-01-01') for i in range(120)] + [('live', None)])
            conn.commit()
        reply = self.service(mnemosyne_db=database).recall('solar')
        self.assertNotIn('code', reply['backends']['mnemosyne'])
        self.assertEqual([r['citation'] for r in reply['results']], ['mnemosyne:working_memory:live'])

    def test_expired_backlog_beyond_one_page_does_not_hide_live_rows(self):
        """The page cap must not sit in front of the expiry filter."""
        import sqlite3
        database = self.root / 'memory.sqlite3'
        with __import__('contextlib').closing(sqlite3.connect(database)) as conn:
            conn.execute('CREATE TABLE working_memory (id, content, source, metadata_json, scope, session_id, superseded_by, valid_until)')
            conn.executemany("INSERT INTO working_memory VALUES (?, 'solar', 'x', '{}', 'global', 's', NULL, '2000-01-01')",
                             [(f'old-{i}',) for i in range(501)])
            conn.execute("INSERT INTO working_memory VALUES ('live', 'solar', 'x', '{}', 'global', 's', NULL, NULL)")
            conn.commit()
        reply = self.service(mnemosyne_db=database).recall('solar')
        self.assertNotIn('code', reply['backends']['mnemosyne'])
        self.assertIn('mnemosyne:working_memory:live', [r['citation'] for r in reply['results']])

    def test_numeric_metadata_is_accepted_and_non_object_is_classified(self):
        import sqlite3
        # A bare scalar and a list are both non-objects, and both are metadata errors, not
        # generic database errors.
        cases = (('{"a": 1}', 'ok'), (7, 'invalid_metadata'), ('[1, 2]', 'invalid_metadata'))
        for index, (value, expected) in enumerate(cases):
            database = self.root / f'meta-{index}.sqlite3'
            with __import__('contextlib').closing(sqlite3.connect(database)) as conn:
                conn.execute('CREATE TABLE working_memory (id, content, source, metadata_json, scope, session_id, superseded_by, valid_until)')
                conn.execute("INSERT INTO working_memory VALUES ('m', 'solar', 'x', ?, 'global', 's', NULL, NULL)", (value,))
                conn.commit()
            reply = self.service(mnemosyne_db=database).recall('solar')
            self.assertEqual(reply['backends']['mnemosyne'].get('code', 'ok'), expected)

    def test_resolution_budget_keeps_unresolved_hits_in_the_ranking(self):
        import sys
        (self.vault / 'curated').mkdir()
        for index in range(6):
            (self.vault / f'curated/n{index}.md').write_text('solar note')
        workspace = self.root / 'qmd'
        (workspace / 'config').mkdir(parents=True)
        (workspace / 'index.sqlite3').touch()
        (workspace / 'config/wayfinder.yml').write_text('collections: {}')
        script = self.root / 'fake_qmd.py'
        hits = [{'file': f'qmd://vault/curated/n{i}.md'} for i in range(6)]
        script.write_text('import json\nprint(json.dumps(%r))\n' % (hits,))
        service = self.service(qmd_workspace=workspace, qmd_command=(sys.executable, str(script)))
        reply = service.recall('solar', 1)
        # Truncation is reported, and the row that survived is fully resolved rather than gutted.
        self.assertEqual(reply['resolution'], {'budget': 2, 'resolved': 2})
        self.assertEqual(len(reply['results']), 1)
        self.assertEqual(reply['results'][0]['citation'], 'curated/n0.md:1')
        # A wider limit resolves everything, so no hit is lost from the candidate pool.
        self.assertEqual(service.recall('solar', 8)['resolution'], {'budget': 16, 'resolved': 6})

    def test_fast_stderr_overflow_is_reported(self):
        import sys
        (self.vault / 'a.md').write_text('solar')
        workspace = self.root / 'qmd'
        (workspace / 'config').mkdir(parents=True)
        (workspace / 'index.sqlite3').touch()
        (workspace / 'config/wayfinder.yml').write_text('collections: {}')
        script = self.root / 'fake_qmd.py'
        script.write_text('import sys\nsys.stderr.write("x" * 2200000)\nprint("[]")\n')
        reply = self.service(qmd_workspace=workspace, qmd_command=(sys.executable, str(script))).recall('solar')
        self.assertEqual(reply['backends']['qmd']['code'], 'output_limit')

    def test_each_backend_resolves_within_its_own_budget(self):
        """A long QMD list must not consume the budget memory needs."""
        import sys, sqlite3
        (self.vault / 'curated').mkdir()
        (self.vault / 'curated/linked.md').write_text('solar note')
        for index in range(20):
            (self.vault / f'curated/q{index}.md').write_text('solar note')
        workspace = self.root / 'qmd'
        (workspace / 'config').mkdir(parents=True)
        (workspace / 'index.sqlite3').touch()
        (workspace / 'config/wayfinder.yml').write_text('collections: {}')
        hits = [{'file': f'qmd://vault/curated/q{i}.md'} for i in range(20)]
        script = self.root / 'fake_qmd.py'
        script.write_text('import json\nprint(json.dumps(%r))\n' % (hits,))
        database = self.root / 'memory.sqlite3'
        with __import__('contextlib').closing(sqlite3.connect(database)) as conn:
            conn.execute('CREATE TABLE working_memory (id, content, source, metadata_json, scope, session_id, superseded_by, valid_until)')
            conn.execute("INSERT INTO working_memory VALUES ('m', 'solar', 'x', '{\"vault_note\":\"curated/linked.md\"}', 'global', 's', NULL, NULL)")
            conn.commit()
        reply = self.service(mnemosyne_db=database, qmd_workspace=workspace,
                             qmd_command=(sys.executable, str(script))).recall('solar', 8)
        # The memory row resolves to its canonical note instead of staying a bare msy citation.
        memory = [r for r in reply['results'] if r['path'] == 'curated/linked.md']
        self.assertEqual(len(memory), 1)
        self.assertIn('mnemosyne', {p['backend'] for p in memory[0]['provenance']})
        self.assertNotIn('canonical_note_status', memory[0])
        self.assertEqual(reply['resolution'], {'budget': 16, 'resolved': 17})

    def test_unresolved_memory_hit_keeps_its_canonical_tier(self):
        """A skipped read must not demote a linked note to the generic episodic tier."""
        import sqlite3
        for name in ('imports', 'notes'):
            (self.vault / name).mkdir()
        for index in range(8):
            (self.vault / f'imports/i{index}.md').write_text('solar note')
        (self.vault / 'notes/n.md').write_text('solar note')
        database = self.root / 'memory.sqlite3'
        with __import__('contextlib').closing(sqlite3.connect(database)) as conn:
            conn.execute('CREATE TABLE working_memory (id, content, source, metadata_json, scope, session_id, superseded_by, valid_until)')
            rows = [(f'i{index}', '{"vault_note": "imports/i%d.md"}' % index) for index in range(8)]
            rows.append(('c1', '{"vault_note": "notes/n.md"}'))
            conn.executemany("INSERT INTO working_memory VALUES (?, 'solar', 'x', ?, 'global', 's', NULL, NULL)", rows)
            conn.commit()
        # limit=4 gives a budget of 8, so the ninth link is over budget and never read.
        results = self.service(mnemosyne_db=database).recall('solar', 4)['results']
        row = next(r for r in results if r['citation'] == 'mnemosyne:working_memory:c1')
        self.assertEqual(row['tier'], 'unclassified')
        self.assertEqual(row['canonical_note_status'], 'not_resolved')

    def test_resolved_memory_hit_keeps_its_source_label(self):
        import sqlite3
        (self.vault / 'curated').mkdir()
        (self.vault / 'curated/c.md').write_text('solar note')
        database = self.root / 'memory.sqlite3'
        with __import__('contextlib').closing(sqlite3.connect(database)) as conn:
            conn.execute('CREATE TABLE working_memory (id, content, source, metadata_json, scope, session_id, superseded_by, valid_until)')
            conn.execute("INSERT INTO working_memory VALUES ('c1', 'solar', 'checkpoint', '{\"vault_note\":\"curated/c.md\"}', 'global', 's', NULL, NULL)")
            conn.commit()
        results = self.service(mnemosyne_db=database).recall('solar')['results']
        row = next(r for r in results if r['citation'] == 'curated/c.md:1')
        self.assertEqual(row['source_label'], 'checkpoint')

    def test_child_is_reaped_when_the_deadline_setup_raises(self):
        import sys, importlib, time
        from unittest.mock import patch
        backends = importlib.import_module('backends')
        (self.vault / 'a.md').write_text('solar')
        workspace = self.root / 'qmd'
        (workspace / 'config').mkdir(parents=True)
        (workspace / 'index.sqlite3').touch()
        (workspace / 'config/wayfinder.yml').write_text('collections: {}')
        script = self.root / 'fake_qmd.py'
        script.write_text('import time\ntime.sleep(1)\nprint("[]")\n')
        spawned = []
        real_popen = backends.subprocess.Popen
        real_monotonic = time.monotonic
        calls = {'n': 0}

        def recording(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            spawned.append(process)
            return process

        def flaky():
            calls['n'] += 1
            if calls['n'] == 1:
                raise RuntimeError('clock unavailable')
            return real_monotonic()

        service = self.service(qmd_workspace=workspace, qmd_command=(sys.executable, str(script)))
        with patch.object(backends.subprocess, 'Popen', recording), patch.object(backends.time, 'monotonic', flaky):
            with self.assertRaises(RuntimeError):
                service.recall('solar')
        self.assertEqual(len(spawned), 1)
        self.assertIsNotNone(spawned[0].poll(), 'child was left running')

    def test_unresolved_qmd_path_is_still_validated(self):
        import sys
        (self.vault / 'curated').mkdir()
        (self.vault / 'curated/a.md').write_text('solar note')
        workspace = self.root / 'qmd'
        (workspace / 'config').mkdir(parents=True)
        (workspace / 'index.sqlite3').touch()
        (workspace / 'config/wayfinder.yml').write_text('collections: {}')
        script = self.root / 'fake_qmd.py'
        hits = [{'file': 'qmd://vault/curated/a.md'}, {'file': 'qmd://vault/curated/b.md'},
                {'file': 'qmd://vault/curated/../../escape.md'}]
        script.write_text('import json\nprint(json.dumps(%r))\n' % (hits,))
        (self.vault / 'curated/b.md').write_text('solar note')
        reply = self.service(qmd_workspace=workspace, qmd_command=(sys.executable, str(script))).recall('solar', 1)
        # Budget is 2, so the bad third hit is never read — rejection must still happen.
        self.assertEqual(reply['backends']['qmd']['code'], 'invalid_result')

    def test_scan_cap_reports_truncation_instead_of_ok(self):
        import sqlite3
        database = self.root / 'memory.sqlite3'
        with __import__('contextlib').closing(sqlite3.connect(database)) as conn:
            conn.execute('CREATE TABLE working_memory (id, content, source, metadata_json, scope, session_id, superseded_by, valid_until)')
            conn.executemany("INSERT INTO working_memory VALUES (?, 'solar', 'x', '{}', 'global', 's', NULL, '2000-01-01')",
                             [(f'old-{i}',) for i in range(5001)])
            conn.commit()
        reply = self.service(mnemosyne_db=database).recall('solar')
        self.assertEqual(reply['backends']['mnemosyne']['code'], 'scan_limit')
        self.assertTrue(reply['degraded'])

    def test_stop_survives_a_kill_that_raises(self):
        import importlib
        backends = importlib.import_module('backends')

        class Fixture:
            def kill(self):
                raise OSError('no such process')

            def wait(self, timeout=None):
                self.seen = timeout
                raise OSError('no such process')

        fixture = Fixture()
        self.assertFalse(backends._stop(fixture, 0.01))
        # Reaping must stay bounded even when the kill itself failed.
        self.assertEqual(fixture.seen, 0.01)

    def test_wide_directory_is_not_enumerated_past_the_budget(self):
        """Collecting a whole huge directory would defeat the entry budget."""
        import os, types
        from unittest.mock import patch
        import wayfinder
        consumed = [0]
        real_vault = str(self.vault)

        class FakeScandir:
            def __init__(self):
                self.closed = False

            def __enter__(self):
                return self._entries()

            def __exit__(self, *exc):
                return False

            def _entries(self):
                for index in range(1000):
                    consumed[0] += 1
                    name = f'f{index}.txt'
                    yield types.SimpleNamespace(name=name, path=f'{real_vault}/{name}',
                                                is_dir=lambda follow_symlinks=True: False,
                                                is_symlink=lambda: False)

        def fake_scandir(path):
            return FakeScandir() if str(path) == real_vault else os.scandir(path)

        with patch.object(wayfinder.os, 'scandir', fake_scandir):
            reply = self.service(max_files=4).recall('solar')
        self.assertEqual(reply['backends']['markdown']['code'], 'scan_limit')
        # One entry past the budget is peeked at, not the whole 1000-entry listing.
        self.assertLessEqual(consumed[0], 5)

    def test_scan_cap_probe_does_not_overrun_on_an_exact_boundary(self):
        import sqlite3
        database = self.root / 'memory.sqlite3'
        with __import__('contextlib').closing(sqlite3.connect(database)) as conn:
            conn.execute('CREATE TABLE working_memory (id, content, source, metadata_json, scope, session_id, superseded_by, valid_until)')
            conn.executemany("INSERT INTO working_memory VALUES (?, 'solar', 'x', '{}', 'global', 's', NULL, '2000-01-01')",
                             [(f'old-{i}',) for i in range(5000)])
            conn.commit()
        reply = self.service(mnemosyne_db=database).recall('solar')
        # Exactly the cap and nothing after it: not truncated.
        self.assertNotIn('code', reply['backends']['mnemosyne'])

    def test_scan_cap_probe_is_a_single_row_statement(self):
        import sqlite3, importlib
        from unittest.mock import patch
        backends = importlib.import_module('backends')
        database = self.root / 'memory.sqlite3'
        with __import__('contextlib').closing(sqlite3.connect(database)) as conn:
            conn.execute('CREATE TABLE working_memory (id, content, source, metadata_json, scope, session_id, superseded_by, valid_until)')
            conn.executemany("INSERT INTO working_memory VALUES (?, 'solar', 'x', '{}', 'global', 's', NULL, '2000-01-01')",
                             [(f'old-{i}',) for i in range(5001)])
            conn.commit()
        seen = []
        real_connect = sqlite3.connect

        def traced(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            connection.set_trace_callback(seen.append)
            return connection

        with patch.object(backends.sqlite3, 'connect', traced):
            self.service(mnemosyne_db=database).recall('solar')
        probes = [statement for statement in seen if statement.startswith('SELECT 1')]
        self.assertEqual(len(probes), 1)
        # sqlite expands bound parameters in the trace, so match the executed shape.
        self.assertIn('LIMIT 1', probes[0])
        self.assertIn('OFFSET 5000', probes[0])

    def test_reaping_is_attempted_once_per_call(self):
        import sys, importlib
        from unittest.mock import patch
        backends = importlib.import_module('backends')
        (self.vault / 'a.md').write_text('solar')
        workspace = self.root / 'qmd'
        (workspace / 'config').mkdir(parents=True)
        (workspace / 'index.sqlite3').touch()
        (workspace / 'config/wayfinder.yml').write_text('collections: {}')
        script = self.root / 'fake_qmd.py'
        script.write_text('import time\ntime.sleep(1)\nprint("[]")\n')
        calls = []

        def failing(process, *args):
            calls.append(process)
            raise OSError('kill refused')

        with patch.object(backends, '_stop', failing):
            try:
                reply = self.service(qmd_workspace=workspace, qmd_command=(sys.executable, str(script)),
                                     qmd_timeout=0.05).recall('solar')
            finally:
                # The patched _stop never reaps, so clean the child up here.
                child = calls[0] if calls else None
                if child is not None and child.poll() is None:
                    child.kill()
                    child.wait()
        # The reason we stopped survives a failed kill, and only one 5s wait is attempted.
        self.assertEqual(reply['backends']['qmd']['code'], 'timeout')
        self.assertLessEqual(len(calls), 2)

    def test_row_cap_still_applies_to_live_rows(self):
        import sqlite3
        database = self.root / 'memory.sqlite3'
        with __import__('contextlib').closing(sqlite3.connect(database)) as conn:
            conn.execute('CREATE TABLE working_memory (id TEXT, content TEXT, source TEXT, metadata_json TEXT, scope TEXT, session_id TEXT, superseded_by TEXT, valid_until TEXT)')
            conn.executemany("INSERT INTO working_memory VALUES (?, 'solar', 'x', '{}', 'global', 's', NULL, NULL)",
                             [(f'id-{i}',) for i in range(501)])
            conn.commit()
        reply = self.service(mnemosyne_db=database).recall('solar')
        self.assertEqual(reply['backends']['mnemosyne']['code'], 'result_limit')

    def test_title_and_summary_maximums(self):
        service = self.service()
        self.assertEqual(service.checkpoint('a.md', 't' * 200, 's' * 4000)['status'], 'preview')
        for title, summary in (('t' * 201, 'ok'), ('t', 's' * 4001), ('two\nlines', 'ok'), ('   ', 'ok')):
            with self.assertRaises(ValueError):
                service.checkpoint('a.md', title, summary)

    def test_readback_failure_reports_verification_not_plain_filesystem(self):
        service = self.service()
        from unittest.mock import patch
        from wayfinder import Wayfinder
        with patch.object(Wayfinder, '_read', side_effect=OSError('gone')):
            with self.assertRaises(RuntimeError) as caught:
                service.checkpoint('a.md', 'Title', 'Summary', confirm=True)
        self.assertIn('may exist', str(caught.exception))
        self.assertIn('readback failed', str(caught.exception))

    def test_scan_budget_counts_directories_and_candidate_notes(self):
        for i in range(30):
            (self.vault / f'dir{i}').mkdir()
        (self.vault / 'a.md').write_text('solar')
        # 30 directories alone overrun a budget of 20, so the walk is genuinely bounded.
        self.assertEqual(self.service(max_files=20).recall('solar')['backends']['markdown']['code'], 'scan_limit')
        self.assertNotIn('code', self.service(max_files=40).recall('solar')['backends']['markdown'])
        (self.vault / 'b.md').write_text('solar')
        self.assertEqual(self.service(max_files=31).recall('solar')['backends']['markdown']['code'], 'scan_limit')

    def test_plugin_reports_readback_mismatch_as_verification_failed(self):
        import json, sys, shutil, importlib
        from unittest.mock import patch
        # Load the plugin the way the loader does: as a package, so relative imports work.
        package = self.root / 'wf_plugin'
        package.mkdir()
        for name in ('__init__.py', 'wayfinder.py', 'backends.py', 'plugin.yaml'):
            shutil.copyfile(Path(__file__).resolve().parents[1] / name, package / name)
        sys.path.insert(0, str(self.root))
        self.addCleanup(sys.path.remove, str(self.root))
        plugin = importlib.import_module('wf_plugin')
        plugin_wayfinder = importlib.import_module('wf_plugin.wayfinder')

        class Context:
            def get_config(self, name, default=None):
                return str(self.vault) if name == 'vault_root' else default

        context = Context()
        context.vault = self.vault
        with patch.object(plugin_wayfinder.Wayfinder, '_read', return_value='wrong bytes'):
            result = json.loads(plugin._handle(context, 'checkpoint',
                                               {'path': 'a.md', 'title': 'T', 'summary': 'S', 'confirm': True}))
        self.assertEqual(result['error']['code'], 'verification_failed')

    def test_scan_settings_are_integers_and_small_checkpoint_is_rejected_before_write(self):
        for config in ({'max_files': True}, {'max_file_bytes': 1.5}, {'qmd_timeout': True}):
            with self.assertRaises(ValueError):
                self.service(**config)
        service = self.service(max_file_bytes=20)
        with self.assertRaises(ValueError):
            service.checkpoint('curated/a.md', 'Title', 'A synthetic summary.', confirm=True)
        self.assertFalse((self.vault / 'curated/a.md').exists())

    def test_qmd_reads_only_isolated_workspace_and_merges_citations(self):
        import sys
        import json
        (self.vault / 'curated').mkdir()
        (self.vault / 'curated/a.md').write_text('solar note')
        workspace = self.root / 'qmd'
        (workspace / 'config').mkdir(parents=True)
        (workspace / 'index.sqlite3').touch()
        (workspace / 'config/wayfinder.yml').write_text('collections: {}')
        script = self.root / 'fake_qmd.py'
        script.write_text(
            'import os,sys,json\n'
            'workspace = sys.argv[1]\n'
            # HOME and HERMES_HOME must be the isolated workspace, not the invoking profile.
            'assert os.environ["HOME"] == workspace + "/home", os.environ["HOME"]\n'
            'assert os.environ["HERMES_HOME"] == workspace + "/hermes", os.environ["HERMES_HOME"]\n'
            'assert os.environ["INDEX_PATH"] == workspace + "/index.sqlite3", os.environ["INDEX_PATH"]\n'
            'assert "TEST_SECRET" not in os.environ\n'
            'assert "search" in sys.argv and "-c" in sys.argv\n'
            'print(json.dumps([{"file":"qmd://vault/curated/a.md","score":0.7,"snippet":"solar note"}]))\n')
        command = (sys.executable, str(script), str(workspace))
        service = self.service(qmd_workspace=workspace, qmd_command=command)
        from unittest.mock import patch
        with patch.dict('os.environ', {'TEST_SECRET': 'should-not-be-inherited', 'INDEX_PATH': '/not-used'}):
            reply = service.recall('solar')
        self.assertEqual(reply['backends']['qmd']['status'], 'ok')
        self.assertEqual(len(reply['results']), 1)
        self.assertEqual({p['backend'] for p in reply['results'][0]['provenance']}, {'markdown', 'qmd'})
        for code, body in [('invalid_json', 'print("broken")'), ('process_failed', 'raise SystemExit(2)'),
                           ('timeout', 'import time; time.sleep(1)'),
                           ('output_limit', 'print("x" * 2200000)'),
                           ('invalid_result', 'print(\'[{"file":"qmd://vault/../outside.md"}]\')')]:
            script.write_text(body)
            reply = self.service(qmd_workspace=workspace, qmd_command=(sys.executable, str(script)), qmd_timeout=0.05 if code == 'timeout' else 3).recall('solar')
            self.assertEqual(reply['backends']['qmd']['code'], code)
            self.assertTrue(reply['degraded'])
            self.assertEqual(reply['results'][0]['path'], 'curated/a.md')


if __name__ == '__main__':
    unittest.main()
