import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from wayfinder import Settings, Wayfinder


class Terminal(io.StringIO):
    def isatty(self):
        return True


class CheckpointProposalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name)

    def service(self, enabled=True):
        return Wayfinder(Settings(vault_root=self.vault, enable_checkpoint=enabled, checkpoint_profile_id='synthetic-profile'))

    def proposal(self, service=None):
        from checkpoints import propose
        return propose(service or self.service(), 'curated/test.md', 'Synthetic choice', 'Use the blue sample.')

    def test_preview_is_content_bound_and_does_not_write(self):
        import hashlib
        result = self.proposal()
        self.assertEqual(result['status'], 'preview')
        self.assertEqual(result['sha256'], hashlib.sha256(result['content'].encode()).hexdigest())
        self.assertFalse((self.vault / 'curated').exists())

    def test_disabled_operator_capability_rejects_preview(self):
        with self.assertRaises(PermissionError):
            self.proposal(self.service(False))

    def test_pipe_cannot_apply_even_with_approval_text(self):
        from checkpoints import apply_interactive
        proposal = self.proposal()
        with patch('sys.stdin', io.StringIO('APPLY ' + proposal['proposal_sha256'] + '\n')), patch('sys.stdout', Terminal()):
            with self.assertRaises(PermissionError):
                apply_interactive(self.service(), proposal)
        self.assertFalse((self.vault / 'curated').exists())

    def test_interactive_exact_apply_and_replay_rejection(self):
        from checkpoints import apply_interactive
        proposal = self.proposal()
        with patch('sys.stdin', Terminal('APPLY ' + proposal['proposal_sha256'] + '\n')), patch('sys.stdout', Terminal()) as out:
            result = apply_interactive(self.service(), proposal)
        self.assertEqual(result['status'], 'created')
        self.assertEqual((self.vault / 'curated/test.md').read_text(), proposal['content'])
        self.assertIn(json.dumps(proposal['content']), out.getvalue())
        with patch('sys.stdin', Terminal('APPLY ' + proposal['proposal_sha256'] + '\n')), patch('sys.stdout', Terminal()):
            with self.assertRaises(FileExistsError):
                apply_interactive(self.service(), proposal)

    def test_tampered_and_expired_and_wrong_vault_rejected(self):
        from checkpoints import validate
        proposal = self.proposal()
        for key, value in [('path', 'curated/other.md'), ('content', 'changed'), ('expires_at', '2000-01-01T00:00:00+00:00')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate(self.service(), {**proposal, key: value})
        other = self.vault / 'other'
        other.mkdir()
        with self.assertRaises(ValueError):
            validate(Wayfinder(Settings(vault_root=other, enable_checkpoint=True, checkpoint_profile_id='synthetic-profile')), proposal)
        # Validate real expiration independently of digest corruption.
        with patch('checkpoints._now', return_value=datetime.now(timezone.utc) + timedelta(hours=1)):
            with self.assertRaises(ValueError):
                validate(self.service(), proposal)

    def test_cancel_is_no_write(self):
        from checkpoints import apply_interactive
        proposal = self.proposal()
        with patch('sys.stdin', Terminal('no\n')), patch('sys.stdout', Terminal()):
            result = apply_interactive(self.service(), proposal)
        self.assertEqual(result['status'], 'cancelled')
        self.assertFalse((self.vault / 'curated').exists())

    def test_scope_and_expiry_rechecked_after_terminal_review(self):
        from checkpoints import apply_interactive, propose
        service = self.service()
        with self.assertRaises(PermissionError):
            propose(service, 'restricted/no.md', 'Title', 'Text')
        proposal = self.proposal(service)
        class DelayedTerminal(Terminal):
            def readline(self, *args):
                # Simulate a long review without sleeping in the suite.
                clock.return_value = datetime.now(timezone.utc) + timedelta(hours=1)
                return 'APPLY ' + proposal['proposal_sha256'] + '\n'
        with patch('checkpoints._now', return_value=datetime.now(timezone.utc)) as clock:
            with patch('sys.stdin', DelayedTerminal()), patch('sys.stdout', Terminal()):
                with self.assertRaises(ValueError):
                    apply_interactive(service, proposal)
        self.assertFalse((self.vault / 'curated').exists())

    def test_real_cli_pty_roundtrip(self):
        import os
        import pty
        import select
        import subprocess
        import sys
        import time
        proposal = self.proposal()
        config = self.vault / 'operator.json'
        config.write_text(json.dumps({'vault_root': str(self.vault), 'enable_checkpoint': True, 'checkpoint_profile_id': 'synthetic-profile'}))
        saved = self.vault / 'proposal.json'
        saved.write_text(json.dumps(proposal))
        master, slave = pty.openpty()
        command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'tools/apply_checkpoint.py'),
                   '--config', str(config), str(saved)]
        process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave)
        os.close(slave)
        output = b''
        try:
            deadline = time.monotonic() + 10
            while b'anything else to cancel:' not in output and time.monotonic() < deadline:
                if select.select([master], [], [], .1)[0]:
                    try:
                        output += os.read(master, 65536)
                    except OSError:
                        break
            self.assertIn(b'anything else to cancel:', output)
            os.write(master, ('APPLY ' + proposal['proposal_sha256'] + '\n').encode())
            self.assertEqual(process.wait(timeout=10), 0)
            self.assertEqual((self.vault / 'curated/test.md').read_text(), proposal['content'])
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)

    def test_operator_profile_binding_rejects_cross_profile_proposal(self):
        from checkpoints import validate
        proposal = self.proposal()
        other = Wayfinder(Settings(vault_root=self.vault, enable_checkpoint=True, checkpoint_profile_id='different-profile'))
        with self.assertRaises(ValueError):
            validate(other, proposal)

    def test_vault_swap_between_validation_and_publish_is_rejected(self):
        from checkpoints import apply_interactive
        service = self.service()
        proposal = self.proposal(service)
        publish = service._publish_checkpoint
        original = self.vault.with_name(self.vault.name + '-original')

        def swapped_publish(*args, **kwargs):
            self.vault.rename(original)
            self.vault.mkdir()
            return publish(*args, **kwargs)

        try:
            with patch.object(service, '_publish_checkpoint', side_effect=swapped_publish):
                with patch('sys.stdin', Terminal('APPLY ' + proposal['proposal_sha256'] + '\n')), patch('sys.stdout', Terminal()):
                    with self.assertRaises(ValueError):
                        apply_interactive(service, proposal)
            self.assertEqual(list(self.vault.iterdir()), [])
            self.assertEqual(list(original.iterdir()), [])
        finally:
            if original.exists():
                import shutil
                shutil.rmtree(self.vault)
                original.rename(self.vault)
