import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from wayfinder import Settings, Wayfinder

class DisclosureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name)
        for name in ('approved', 'restricted', 'other'):
            (self.vault / name).mkdir()
            (self.vault / name / 'note.md').write_text('# solar\n' + name + '-SECRET\n')

    def test_default_restricted_collections_are_not_disclosed(self):
        service = Wayfinder(Settings(vault_root=self.vault))
        self.assertNotIn('restricted', json.dumps(service.recall('solar')))
        result = service.open_passage('restricted/note.md')
        self.assertEqual(result, {'status': 'denied'})

    def test_owner_scope_shared_by_recall_open_and_backend_fallback(self):
        service = Wayfinder(Settings(vault_root=self.vault, read_roots=('approved',)))
        records = [{'content':'other-SECRET','canonical_note':'other/note.md','citation':'record-secret',
                    'source_label':'other-SECRET','tier':'episodic','provenance':[{'backend':'mnemosyne','citation':'record-secret'}]}]
        with patch('wayfinder.mnemosyne_search', return_value=(records, {'status':'ok'})), patch('wayfinder.qmd_search',return_value=([{'file':'qmd://vault/other/note.md'}],{'status':'ok'})):
            reply = service.recall('solar')
        self.assertEqual([r['path'] for r in reply['results']], ['approved/note.md'])
        self.assertNotIn('other', json.dumps(reply))
        self.assertEqual(service.open_passage('other/note.md'), {'status':'denied'})

    def test_profiles_do_not_share_policy_and_exclusion_wins(self):
        first = Wayfinder(Settings(vault_root=self.vault, read_roots=('approved',), excluded_roots=('approved',)))
        second = Wayfinder(Settings(vault_root=self.vault, read_roots=('other',)))
        self.assertEqual(first.recall('solar')['results'], [])
        self.assertEqual(second.recall('solar')['results'][0]['path'], 'other/note.md')

    def test_unverified_canonical_link_does_not_authorize_record_text(self):
        service = Wayfinder(Settings(vault_root=self.vault, read_roots=('approved',)))
        for path in ('approved/missing.md', 'approved/image.txt'):
            records = [{'content':'DO-NOT-DISCLOSE', 'canonical_note':path, 'citation':'record',
                        'source_label':'DO-NOT-DISCLOSE','tier':'episodic',
                        'provenance':[{'backend':'mnemosyne','citation':'record'}]}]
            with patch('wayfinder.mnemosyne_search', return_value=(records, {'status':'ok'})):
                self.assertNotIn('DO-NOT-DISCLOSE', json.dumps(service.recall('solar')))

    def test_resolved_note_does_not_authorize_backend_source_label(self):
        service = Wayfinder(Settings(vault_root=self.vault))
        records = [{'content':'DO-NOT-DISCLOSE', 'canonical_note':'approved/note.md', 'citation':'record',
                    'source_label':'DO-NOT-DISCLOSE','tier':'episodic',
                    'provenance':[{'backend':'mnemosyne','citation':'record'}]}]
        with patch('wayfinder.mnemosyne_search', return_value=(records, {'status':'ok'})):
            self.assertNotIn('DO-NOT-DISCLOSE', json.dumps(service.recall('solar')))

    def test_metadata_filters_narrow_results_without_widening_scope(self):
        (self.vault / 'approved/note.md').write_text('---\nproject: alpha\nstatus: current\n---\n# Solar\n')
        (self.vault / 'other/note.md').write_text('---\nproject: beta\n---\n# Solar\n')
        service = Wayfinder(Settings(vault_root=self.vault, read_roots=('approved',)))
        self.assertEqual(len(service.recall('solar', filters={'project':'alpha'})['results']), 1)
        self.assertEqual(service.recall('solar', filters={'project':'beta'})['results'], [])
        with self.assertRaises(ValueError):
            service.recall('solar', filters={'read_roots':'other'})
