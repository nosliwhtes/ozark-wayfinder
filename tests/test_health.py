import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wayfinder import Settings, Wayfinder


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vault = self.root / 'vault'
        self.vault.mkdir()

    def test_root_check_does_not_claim_coverage_or_freshness(self):
        result = Wayfinder(Settings(self.vault)).health()
        self.assertEqual(result['markdown']['status'], 'ok')
        self.assertEqual(result['markdown']['availability'], 'checked')
        self.assertEqual(result['markdown']['coverage'], 'unknown')
        self.assertEqual(result['markdown']['freshness'], 'read_at_retrieval')
        self.assertEqual(result['qmd']['status'], 'disabled')
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_backend_health_does_not_search_or_execute(self):
        service = Wayfinder(Settings(self.vault, mnemosyne_db=self.root/'db', qmd_workspace=self.root/'qmd'))
        with patch('wayfinder.mnemosyne_search', side_effect=AssertionError), patch('wayfinder.qmd_search', side_effect=AssertionError):
            result = service.health()
        for name in ('qmd', 'mnemosyne'):
            self.assertEqual(result[name]['status'], 'unknown')
            self.assertEqual(result[name]['compatibility'], 'not_checked')
            self.assertEqual(result[name]['index_freshness'], 'unknown')
        self.assertEqual(list(self.root.iterdir()), [self.vault])

    def test_replaced_symlink_root_not_available(self):
        service = Wayfinder(Settings(self.vault))
        self.vault.rmdir()
        self.vault.symlink_to(self.root, target_is_directory=True)
        result = service.health()
        self.assertEqual(result['markdown']['status'], 'error')
        self.assertEqual(result['markdown']['code'], 'root_unavailable')

    def test_scoped_roots_are_bounded_and_nofollow(self):
        (self.vault/'allowed').mkdir()
        (self.vault/'linked').symlink_to(self.root, target_is_directory=True)
        service = Wayfinder(Settings(self.vault, read_roots=('allowed', 'missing', 'linked', 'private'), max_files=9, max_file_bytes=100))
        result = service.health()
        self.assertEqual(result['markdown']['scope_roots'], {'checked': 3, 'available': 1, 'unavailable': 2, 'excluded': 1, 'unchecked': 0})
        self.assertEqual(result['markdown']['status'], 'degraded')
        self.assertEqual(result['policy']['read_scope'], 'explicit_roots')
        self.assertFalse(result['capabilities']['checkpoint_proposal'])
        self.assertEqual(result['limits']['max_files'], 9)
        self.assertNotIn('allowed', json.dumps(result))

    def test_root_probe_budget_is_explicit(self):
        service = Wayfinder(Settings(self.vault, read_roots=tuple('r'+str(i) for i in range(70))))
        result = service.health()
        self.assertEqual(result['markdown']['scope_roots']['checked'], 64)
        self.assertEqual(result['markdown']['scope_roots']['unchecked'], 6)
        self.assertEqual(result['limits']['health_root_checks'], 64)

    def test_deep_roots_remain_unchecked_without_unbounded_traversal(self):
        roots = ('/'.join(['deep'] * 65),) + tuple('r'+str(i) for i in range(65))
        result = Wayfinder(Settings(self.vault, read_roots=roots)).health()
        self.assertEqual(result['markdown']['scope_roots']['checked'], 63)
        self.assertEqual(result['markdown']['scope_roots']['unchecked'], 3)
