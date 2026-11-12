"""Explicit checkpoint proposals and human-operated apply, not model approval.

The digest detects changed proposals; it is not an authentication credential.
Only the terminal apply flow publishes. This is not an OS/process sandbox.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys

LIFETIME_SECONDS = 900
_FIELDS = {'version', 'status', 'vault_id', 'profile_id', 'path', 'content', 'sha256',
           'created_at', 'expires_at', 'proposal_sha256'}


def _now():
    return datetime.now(timezone.utc)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True).encode('utf-8')).hexdigest()


def _vault_id(service):
    # Opening the configured root without following its final component preserves
    # the same root replacement boundary as the publication helper.
    fd = os.open(service.vault, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        return _digest([str(service.vault), info.st_dev, info.st_ino])
    finally:
        os.close(fd)


def _enabled(service):
    if getattr(service.settings, 'enable_checkpoint', False) is not True:
        raise PermissionError('Checkpoint capability is disabled by operator configuration.')
    profile = getattr(service.settings, 'checkpoint_profile_id', None)
    if not isinstance(profile, str) or not profile.strip() or len(profile) > 100:
        raise PermissionError('Checkpoint capability requires an operator-configured profile identifier.')


def _path(service, path):
    if __package__:
        from .wayfinder import relative_path
    else:
        from wayfinder import relative_path
    relative_path(path, markdown=True)
    if not service.permitted(path):
        raise PermissionError('Checkpoint destination is outside operator-approved scope.')
    # Existing symlinks are rejected before a proposal is displayed. Publication
    # repeats nofollow checks with descriptors to handle later replacements.
    current = service.vault
    for part in Path(path).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('symlinks are not permitted')
    if current.exists():
        raise FileExistsError('Existing notes are never replaced.')


def propose(service, path, title, summary):
    _enabled(service)
    _path(service, path)
    if not isinstance(title, str) or not title.strip() or len(title) > 200 or '\n' in title or '\r' in title:
        raise ValueError('title must be a single nonblank line, max 200 characters')
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 4000:
        raise ValueError('summary must be nonblank, max 4000 characters')
    now = _now()
    content = f'---\ncreated: {now.isoformat()}\nsource: wayfinder-checkpoint\n---\n\n# {title}\n\n{summary}\n'
    if len(content.encode('utf-8')) > service.settings.max_file_bytes:
        raise ValueError('checkpoint exceeds configured byte limit')
    proposal = {'version': 1, 'status': 'preview', 'vault_id': _vault_id(service),
                'profile_id': service.settings.checkpoint_profile_id, 'path': path, 'content': content,
                'sha256': hashlib.sha256(content.encode('utf-8')).hexdigest(),
                'created_at': now.isoformat(),
                'expires_at': (now + timedelta(seconds=LIFETIME_SECONDS)).isoformat()}
    return {**proposal, 'proposal_sha256': _digest(proposal)}


def validate(service, proposal):
    """Validate without writing; callers must still obtain interactive approval."""
    _enabled(service)
    if not isinstance(proposal, dict) or set(proposal) != _FIELDS:
        raise ValueError('invalid checkpoint proposal fields')
    if type(proposal['version']) is not int or proposal['version'] != 1 or proposal['status'] != 'preview':
        raise ValueError('unsupported checkpoint proposal')
    if any(not isinstance(proposal[key], str) for key in _FIELDS - {'version'}):
        raise ValueError('invalid checkpoint proposal types')
    payload = {key: value for key, value in proposal.items() if key != 'proposal_sha256'}
    if not hmac.compare_digest(_digest(payload), proposal['proposal_sha256']):
        raise ValueError('checkpoint proposal changed; generate a fresh preview')
    try:
        created = datetime.fromisoformat(proposal['created_at'])
        expires = datetime.fromisoformat(proposal['expires_at'])
        now = _now()
        if created.tzinfo is None or expires.tzinfo is None or not created <= now < expires:
            raise ValueError('checkpoint proposal expired or not yet valid')
        if (expires - created).total_seconds() > LIFETIME_SECONDS:
            raise ValueError('checkpoint proposal lifetime exceeds limit')
    except (TypeError, OverflowError) as exc:
        raise ValueError('invalid checkpoint proposal timestamps') from exc
    if not hmac.compare_digest(_vault_id(service), proposal['vault_id']):
        raise ValueError('checkpoint proposal belongs to a different vault')
    if proposal['profile_id'] != service.settings.checkpoint_profile_id:
        raise ValueError('checkpoint proposal belongs to a different operator profile')
    raw = proposal['content'].encode('utf-8')
    if not raw or len(raw) > service.settings.max_file_bytes or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), proposal['sha256']):
        raise ValueError('checkpoint content changed or exceeds limit')
    _path(service, proposal['path'])
    return payload


def apply_interactive(service, proposal):
    """Human CLI boundary. Never expose this as a model-callable plugin operation."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise PermissionError('Checkpoint apply requires an interactive terminal; piping approval is not supported.')
    # Freeze a deep copy before displaying so mutable input cannot switch after
    # the person sees the exact bytes and destination.
    proposal = json.loads(json.dumps(proposal))
    validate(service, proposal)
    print('Create-only checkpoint. Review the exact JSON-escaped destination and content:')
    print('Vault: ' + json.dumps(str(service.vault)))
    print('Operator profile: ' + json.dumps(proposal['profile_id']))
    print('Path: ' + json.dumps(proposal['path']))
    print('Content: ' + json.dumps(proposal['content']))
    print('SHA-256: ' + proposal['sha256'])
    expected = 'APPLY ' + proposal['proposal_sha256']
    print('Type exactly ' + expected + ' to publish, anything else to cancel:', flush=True)
    answer = sys.stdin.readline(256).rstrip('\r\n')
    if answer != expected:
        return {'status': 'cancelled'}
    validate(service, proposal)  # expiry, destination and policy may change while reviewing
    return service._publish_checkpoint(proposal['path'], proposal['content'],
                                       expected_vault_id=proposal['vault_id'])
