"""Ozark Wayfinder: local-only, bounded, canonical Markdown recall."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import hashlib
import os
import re
import stat
import uuid

if __package__:
    from .backends import mnemosyne_search, qmd_search
else:
    from backends import mnemosyne_search, qmd_search

MAX_ERRORS = 20


def relative_path(value: str, markdown: bool = False) -> PurePosixPath:
    if not isinstance(value, str) or not value or '\\' in value or any(ord(c) < 32 for c in value):
        raise ValueError('invalid relative path')
    parts = value.split('/')
    if any(not p or p.startswith('.') for p in parts) or PurePosixPath(value).is_absolute():
        raise ValueError('path must be relative, non-hidden, with no traversal')
    path = PurePosixPath(value)
    if markdown and path.suffix != '.md':
        raise ValueError('canonical note must end with .md')
    return path


def absolute_path(value: Path, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError(f'{label} must be an explicit absolute, non-symlink path')
    return path.resolve()


@dataclass(frozen=True)
class Settings:
    vault_root: Path
    curated_roots: tuple[str, ...] = ('curated',)
    imported_roots: tuple[str, ...] = ('imports', 'sources')
    max_files: int = 5000
    max_file_bytes: int = 1024 * 1024
    mnemosyne_db: Path | None = None
    qmd_workspace: Path | None = None
    qmd_command: tuple[str, ...] = ('qmd',)
    qmd_timeout: float = 10


class Wayfinder:
    def __init__(self, settings: Settings):
        from dataclasses import replace
        optional = {}
        for name in ('mnemosyne_db', 'qmd_workspace'):
            value = getattr(settings, name)
            if value is not None:
                optional[name] = absolute_path(value, name)
        settings = replace(settings, **optional)
        if not isinstance(settings.qmd_command, (list, tuple)) or not settings.qmd_command or any(not isinstance(p, str) or not p or '\x00' in p for p in settings.qmd_command):
            raise ValueError('qmd_command must be a nonempty argument list')
        if type(settings.qmd_timeout) not in (int, float) or not 0 < settings.qmd_timeout <= 60:
            raise ValueError('qmd_timeout must be >0 and <=60 seconds')
        self.settings = settings
        self.vault = absolute_path(settings.vault_root, 'vault_root')
        if not self.vault.is_dir():
            raise ValueError('vault_root must be an existing directory')
        for group in (settings.curated_roots, settings.imported_roots):
            if not isinstance(group, (list, tuple)):
                raise ValueError('tier roots must be lists of relative paths')
            for root in group:
                relative_path(root)
        for a in settings.curated_roots:
            for b in settings.imported_roots:
                if a == b or a.startswith(b + '/') or b.startswith(a + '/'):
                    raise ValueError('curated and imported roots must not overlap')
        if type(settings.max_files) is not int or type(settings.max_file_bytes) is not int or not 1 <= settings.max_files <= 100000 or not 1 <= settings.max_file_bytes <= 10000000:
            raise ValueError('invalid scan limits')

    @contextmanager
    def _parent_fd(self, relative: str, create: bool = False):
        path = relative_path(relative, markdown=True)
        fd = os.open(self.vault, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in path.parts[:-1]:
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            yield fd, path.name
        finally:
            os.close(fd)

    def _read(self, relative: str) -> str:
        with self._parent_fd(relative) as (parent, name):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > self.settings.max_file_bytes:
                    raise ValueError('file is not regular or exceeds byte limit')
                raw = stream.read(self.settings.max_file_bytes + 1)
                if len(raw) > self.settings.max_file_bytes:
                    raise ValueError('file exceeds byte limit')
                return raw.decode('utf-8')

    def tier(self, relative: str) -> str:
        parts = PurePosixPath(relative).parts
        for tier, roots in [('curated', self.settings.curated_roots),
                            ('imported', self.settings.imported_roots)]:
            if any(parts[:len(PurePosixPath(root).parts)] == PurePosixPath(root).parts for root in roots):
                return tier
        return 'unclassified'

    def _markdown(self, terms: list[str]) -> tuple[list[dict], dict]:
        rows, errors, count = [], [], 0
        limited = False
        def note(entry):
            if len(errors) < MAX_ERRORS:
                errors.append(entry)
        # A manual stack over scandir that also caps how many entries are collected per
        # directory: os.walk materialises a whole directory before any budget check, so a wide
        # tree could still be walked for free. Ordering is directory order, not name order,
        # wherever the budget truncates a listing.
        stack = [self.vault]
        while stack and not limited:
            root = stack.pop()
            if count >= self.settings.max_files:
                limited = True
                break
            room = self.settings.max_files - count
            entries = []
            try:
                with os.scandir(root) as listing:
                    for entry in listing:
                        entries.append(entry)
                        if len(entries) > room:
                            break
            except OSError as exc:
                note({'code': 'walk_error', 'detail': type(exc).__name__})
                continue
            if len(entries) > room:
                limited = True
                del entries[room:]
            entries.sort(key=lambda entry: entry.name)
            descend = []
            for entry in entries:
                count += 1
                if entry.name.startswith('.'):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    descend.append(Path(entry.path))
                    continue
                if not entry.name.endswith('.md') or entry.is_symlink():
                    continue
                rel = (Path(root) / entry.name).relative_to(self.vault).as_posix()
                try:
                    text = self._read(rel)
                except (OSError, ValueError, UnicodeError) as exc:
                    note({'path': rel, 'code': type(exc).__name__})
                    continue
                matched = [(n, line) for n, line in enumerate(text.splitlines(), 1)
                           if any(t in line.casefold() for t in terms)]
                if matched:
                    line, snippet = matched[0]
                    citation = f'{rel}:{line}'
                    rows.append({'content': snippet[:800], 'path': rel, 'citation': citation, 'line': line,
                                 'backend': 'markdown', 'tier': self.tier(rel), 'tier_rule': 'configured_roots',
                                 'score': sum(text.casefold().count(t) for t in terms),
                                 'provenance': [{'backend': 'markdown', 'citation': citation}]})
            stack.extend(reversed(descend))
        status = {'status': 'degraded' if errors or limited else 'ok', 'errors': errors}
        if limited:
            status['code'] = 'scan_limit'
        if len(errors) >= MAX_ERRORS:
            status['errors_truncated'] = True
        return rows, status

    def health(self) -> dict:
        _, memory = mnemosyne_search(self.settings, ['wayfinder_health_probe'])
        _, qmd = qmd_search(self.settings, 'wayfinder_health_probe')
        return {'markdown': {'status': 'ok' if self.vault.is_dir() else 'error', 'mode': 'bounded_literal'},
                'qmd': qmd, 'mnemosyne': memory}

    def _canonical(self, relative: str, backend: str, provenance: dict, terms: list[str]) -> dict:
        relative_path(relative, markdown=True)
        text = self._read(relative)
        lines = text.splitlines()
        # An empty note has no line 1, so it is cited by path alone rather than falsely.
        number = next((n for n, line in enumerate(lines, 1) if any(t in line.casefold() for t in terms)), 1 if lines else 0)
        citation = f'{relative}:{number}' if number else relative
        return {'content': lines[number - 1][:800] if number else '', 'path': relative, 'line': number,
                'citation': citation, 'backend': backend, 'tier': self.tier(relative), 'tier_rule': 'configured_roots',
                'score': 0, 'provenance': [provenance]}

    def recall(self, query: str, limit: int = 8) -> dict:
        if not isinstance(query, str) or not isinstance(limit, int) or isinstance(limit, bool):
            raise ValueError('query must be text and limit must be an integer')
        terms = re.findall(r'\w+', query.casefold())
        if not terms or len(query) > 2000 or not 1 <= limit <= 50:
            raise ValueError('query must contain words (max 2000 characters); limit must be 1..50')
        rows, status = self._markdown(terms)
        qmd, qmd_status = qmd_search(self.settings, query)
        # Canonical re-reads are separate from the scan budget, so cap them — but keep every
        # backend hit in the ranking rather than dropping the tail of the list.
        budget = min(limit * 2, 100)
        resolved = 0
        qmd_resolved = 0
        for row in qmd:
            try:
                uri = row['file']
                if not isinstance(uri, str) or not uri.startswith('qmd://vault/'):
                    raise ValueError('only the explicit vault collection is permitted')
                from urllib.parse import unquote, urlsplit
                parsed = urlsplit(uri)
                if parsed.query not in ('', 'index=wayfinder') or parsed.fragment:
                    raise ValueError('unexpected QMD index or fragment')
                relative = unquote(parsed.path.removeprefix('/'))
                # Validate on both branches: an unread hit still needs a trustworthy path.
                relative_path(relative, markdown=True)
                if qmd_resolved < budget:
                    # A slot is consumed even if the read then fails, so failures are not free.
                    qmd_resolved += 1
                    rows.append(self._canonical(relative, 'qmd', {'backend': 'qmd', 'citation': uri}, terms))
                    resolved += 1
                else:
                    # Unread, but still a real hit with real provenance and its own tier.
                    rows.append({'content': '', 'path': relative, 'line': 0, 'citation': uri,
                                 'backend': 'qmd', 'tier': self.tier(relative), 'tier_rule': 'configured_roots',
                                 'score': 0, 'provenance': [{'backend': 'qmd', 'citation': uri}],
                                 'canonical_note_status': 'not_resolved'})
            except (ValueError, TypeError, KeyError, OSError, UnicodeError):
                qmd_status = {'status': 'degraded', 'code': 'invalid_result'}
        memories, memory_status = mnemosyne_search(self.settings, terms)
        # Each backend gets its own budget, so a long QMD list cannot starve memory resolution.
        memory_resolved = 0
        for row in memories:
            canonical = row.pop('canonical_note')
            if canonical:
                try:
                    relative_path(canonical, markdown=True)
                    # The tier comes from the canonical path even when the read is skipped.
                    row['tier'] = self.tier(canonical)
                    if memory_resolved < budget:
                        memory_resolved += 1
                        label = row.get('source_label')
                        row = self._canonical(canonical, 'mnemosyne', row['provenance'][0], terms)
                        if label is not None:
                            row['source_label'] = label
                        resolved += 1
                    else:
                        row['canonical_note_status'] = 'not_resolved'
                except (ValueError, TypeError, OSError, UnicodeError):
                    row['canonical_note_status'] = 'invalid_or_missing'
                    row['tier'] = 'episodic'
            rows.append(row)
        merged = {}
        for row in rows:
            key = row.get('path') or row['citation']
            if key in merged:
                merged[key]['provenance'].extend(row['provenance'])
                # Keep a label from any surviving backend record; the first row may be Markdown.
                if row.get('source_label') and not merged[key].get('source_label'):
                    merged[key]['source_label'] = row['source_label']
            else:
                merged[key] = row
        rows = list(merged.values())
        rows.sort(key=lambda r: ({'curated': 0, 'unclassified': 1, 'episodic': 2, 'imported': 3}[r['tier']],
                                 -len(r['provenance']), -r.get('score', 0), r['citation']))
        backends = {'markdown': status, 'qmd': qmd_status, 'mnemosyne': memory_status}
        return {'results': rows[:limit], 'backends': backends,
                'resolution': {'budget': budget, 'resolved': resolved},
                'degraded': any(s['status'] not in ('ok', 'disabled') for s in backends.values())}

    def checkpoint(self, path: str, title: str, summary: str, confirm: bool = False) -> dict:
        relative_path(path, markdown=True)
        if not isinstance(title, str) or not title.strip() or len(title) > 200 or '\n' in title or '\r' in title:
            raise ValueError('title must be a single nonblank line, max 200 characters')
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 4000:
            raise ValueError('summary must be nonblank, max 4000 characters')
        if type(confirm) is not bool:
            raise ValueError('confirm must be a boolean')
        # Existing symlink directories must also be rejected in preview mode.
        current = self.vault
        for part in PurePosixPath(path).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError('symlinks are not permitted')
        result = {'status': 'preview', 'path': path, 'tier': self.tier(path),
                  'mnemosyne': 'not_written', 'qmd': 'not_reindexed'}
        if not confirm:
            return result
        now = datetime.now(timezone.utc).isoformat()
        content = f'---\ncreated: {now}\nsource: wayfinder-checkpoint\n---\n\n# {title}\n\n{summary}\n'
        raw = content.encode('utf-8')
        if len(raw) > self.settings.max_file_bytes:
            raise ValueError('checkpoint exceeds configured canonical file byte limit')
        with self._parent_fd(path, create=True) as (parent, name):
            staging = '.wayfinder-' + uuid.uuid4().hex
            try:
                fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Atomic no-clobber publication; never replace an existing note.
                os.link(staging, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                os.fsync(parent)
            finally:
                try:
                    os.unlink(staging, dir_fd=parent)
                except FileNotFoundError:
                    pass
        try:
            persisted = self._read(path)
        except (OSError, ValueError, UnicodeError) as exc:
            # The note may well exist; do not let this read as a plain filesystem error.
            raise RuntimeError('checkpoint may exist but readback failed (%s); inspect target before retrying'
                               % type(exc).__name__) from exc
        if persisted != content:
            raise RuntimeError('checkpoint published but bytes differ on readback; inspect target before retrying')
        return {**result, 'status': 'created', 'verified': True, 'sha256': hashlib.sha256(raw).hexdigest()}