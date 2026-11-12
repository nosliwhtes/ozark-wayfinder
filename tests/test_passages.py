import hashlib
from pathlib import Path
import tempfile
import unittest
from wayfinder import Settings, Wayfinder


class PassageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.file = self.root / 'note.md'
        self.raw = '# Plan\r\n\r\n## Power\r\nSolar choice.\r\nMore detail.\r\n'.encode()
        self.file.write_bytes(self.raw)
        self.service = Wayfinder(Settings(vault_root=self.root))

    def test_recall_revision_and_verified_passage(self):
        hit = self.service.recall('solar')['results'][0]
        self.assertIn('revision', hit)
        self.assertEqual(hit['revision'], hashlib.sha256(self.raw).hexdigest())
        opened = self.service.open_passage(hit['path'], hit['line'], 2, hit['revision'])
        self.assertEqual(opened['status'], 'verified')
        self.assertEqual(opened['content'], 'Solar choice.\nMore detail.')
        self.assertEqual((opened['start_line'], opened['end_line']), (4, 5))
        self.assertEqual(opened['heading'], {'line': 3, 'text': '## Power'})
        self.assertEqual(opened['revision'], hit['revision'])

    def test_changed_source_never_returns_old_line_numbers(self):
        digest = hashlib.sha256(self.raw).hexdigest()
        self.file.write_text('replacement\n')
        reply = self.service.open_passage('note.md', 4, 2, digest)
        self.assertEqual(reply['status'], 'stale')
        self.assertNotIn('content', reply)
        self.assertNotIn('start_line', reply)

    def test_missing_renamed_and_empty(self):
        self.file.rename(self.root / 'renamed.md')
        self.assertEqual(self.service.open_passage('note.md')['status'], 'missing')
        self.file.write_text('')
        self.assertEqual(self.service.open_passage('note.md')['status'], 'empty')
        self.assertEqual(self.service.open_passage('renamed.md', 99)['status'], 'out_of_range')

    def test_limits_and_paths(self):
        for path in ('../note.md', '.hidden.md', '/note.md'):
            with self.assertRaises(ValueError):
                self.service.open_passage(path)
        for start, count in ((0, 1), (1, 101), (True, 1), (1, False)):
            with self.assertRaises(ValueError):
                self.service.open_passage('note.md', start, count)
        with self.assertRaises(ValueError):
            self.service.open_passage('note.md', expected_revision='bogus')
        self.file.unlink()
        self.file.symlink_to('/etc/passwd')
        self.assertEqual(self.service.open_passage('note.md')['status'], 'unavailable')

    def test_long_lines_do_not_create_false_full_passage(self):
        self.file.write_text('x' * 20000)
        reply = self.service.open_passage('note.md')
        self.assertEqual(reply['status'], 'passage_too_large')
        self.assertNotIn('content', reply)

    def test_directory_swap_does_not_emit_external_filenames(self):
        from unittest.mock import patch
        import os
        outside = self.root.parent / (self.root.name + '-outside')
        outside.mkdir()
        self.addCleanup(outside.rmdir)
        self.addCleanup(lambda: (outside / 'secret-marker.md').unlink())
        (outside / 'secret-marker.md').write_text('solar secret')
        folder = self.root / 'queued'
        folder.mkdir()
        original = os.scandir
        def switched(path):
            listing = original(path)
            if isinstance(path, int):
                entries = list(listing)
                listing.close()
                if any(entry.name == 'queued' for entry in entries):
                    folder.rmdir()
                    folder.symlink_to(outside, target_is_directory=True)
                from contextlib import nullcontext
                return nullcontext(iter(entries))
            if isinstance(path, Path) and path == folder:
                folder.rmdir()
                folder.symlink_to(outside, target_is_directory=True)
            return listing
        with patch('wayfinder.os.scandir', side_effect=switched):
            reply = self.service.recall('solar')
        self.assertNotIn('secret-marker', str(reply))
        # If descriptor-based scanning prevents the path callback, no swap occurs.
        if folder.is_symlink():
            folder.unlink()

    def test_invalid_directory_name_does_not_abort_scan(self):
        (self.root / 'bad\\name').mkdir()
        reply = self.service.recall('solar')
        self.assertTrue(reply['results'])
        self.assertTrue(reply['degraded'])

    def test_heading_ignores_fenced_code(self):
        self.file.write_text('# Real\n```python\n# Not a heading\n```\nEvidence.\n')
        result = self.service.open_passage('note.md', 5)
        self.assertEqual(result['heading'], {'line': 1, 'text': '# Real'})
