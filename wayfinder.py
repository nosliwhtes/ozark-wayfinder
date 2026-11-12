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
import time

if __package__:
    from .backends import mnemosyne_search, qmd_search
    from .retrieval import analyze, matches_filters
else:
    from backends import mnemosyne_search, qmd_search
    from retrieval import analyze, matches_filters

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
    read_roots: tuple[str, ...] = ()
    excluded_roots: tuple[str, ...] = ('restricted', 'private')
    allow_unlinked_records: bool = False
    enable_checkpoint: bool = False
    checkpoint_profile_id: str = ""
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
        if not isinstance(settings.checkpoint_profile_id, str) or len(settings.checkpoint_profile_id) > 100:
            raise ValueError("invalid checkpoint profile identity")
        self.settings = settings
        self.vault = absolute_path(settings.vault_root, 'vault_root')
        if not self.vault.is_dir():
            raise ValueError('vault_root must be an existing directory')
        for field in (settings.allow_unlinked_records, settings.enable_checkpoint):
            if type(field) is not bool:
                raise ValueError("capabilities must be booleans")
        for group in (settings.curated_roots, settings.imported_roots, settings.read_roots, settings.excluded_roots):
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

    def permitted(self, relative: str, directory: bool = False) -> bool:
        """Only operator Settings define scope; callers cannot widen it."""
        relative_path(relative)
        def under(path, root):
            return path == root or path.startswith(root + '/')
        if any(under(relative, root) for root in self.settings.excluded_roots):
            return False
        return not self.settings.read_roots or any(
            under(relative, root) or (directory and under(root, relative))
            for root in self.settings.read_roots)

    @contextmanager
    def _parent_fd(self, relative: str, create: bool = False, expected_vault_id: str | None = None):
        path = relative_path(relative, markdown=True)
        fd = os.open(self.vault, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if expected_vault_id is not None:
                if __package__:
                    from .checkpoints import _digest
                else:
                    from checkpoints import _digest
                info = os.fstat(fd)
                if _digest([str(self.vault), info.st_dev, info.st_ino]) != expected_vault_id:
                    raise ValueError('checkpoint vault changed before publication')
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

    def _read_bytes(self, relative: str) -> bytes:
        with self._parent_fd(relative) as (parent, name):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > self.settings.max_file_bytes:
                    raise ValueError('file is not regular or exceeds byte limit')
                raw = stream.read(self.settings.max_file_bytes + 1)
                if len(raw) > self.settings.max_file_bytes:
                    raise ValueError('file exceeds byte limit')
                after = os.fstat(stream.fileno())
                if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise OSError('source changed during read')
                return raw

    def _read(self, relative: str) -> str:
        return self._read_bytes(relative).decode('utf-8')

    def _snapshot(self, relative: str) -> tuple[str, str]:
        raw = self._read_bytes(relative)
        return raw.decode('utf-8'), hashlib.sha256(raw).hexdigest()

    def open_passage(self, path: str, start_line: int = 1, line_count: int = 20,
                     expected_revision: str | None = None) -> dict:
        """Read bounded current bytes; a changed revision never reuses old line coordinates."""
        relative_path(path, markdown=True)
        if type(start_line) is not int or start_line < 1 or type(line_count) is not int or not 1 <= line_count <= 100:
            raise ValueError('start_line must be positive and line_count must be 1..100')
        if expected_revision is not None and (not isinstance(expected_revision, str) or
                not re.fullmatch(r'[0-9a-f]{64}', expected_revision)):
            raise ValueError('expected_revision must be a SHA-256 digest')
        if not self.permitted(path):
            return {'status': 'denied'}
        result = {'path': path, 'tier': self.tier(path), 'backend': 'markdown'}
        try:
            text, revision = self._snapshot(path)
        except FileNotFoundError:
            return {**result, 'status': 'missing'}
        except (OSError, ValueError, UnicodeError):
            return {**result, 'status': 'unavailable'}
        result['revision'] = revision
        if expected_revision is not None and revision != expected_revision:
            return {**result, 'status': 'stale'}
        lines = text.splitlines()
        if not lines:
            return {**result, 'status': 'empty'}
        if start_line > len(lines):
            return {**result, 'status': 'out_of_range'}
        end = min(len(lines), start_line + line_count - 1)
        content = '\n'.join(lines[start_line - 1:end])
        # Do not truncate a line and then claim that the full line was shown.
        heading, fence = None, None
        for number, line in enumerate(lines[:start_line], 1):
            marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
            if fence:
                if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                    fence = None
                continue
            if marker:
                fence = marker[1]
                continue
            if re.match(r'^ {0,3}#{1,6}(?:\s|$)', line):
                heading = {'line': number, 'text': line}
        if len(content.encode('utf-8')) + len((heading or {}).get('text', '').encode('utf-8')) > 16000:
            return {**result, 'status': 'passage_too_large'}
        return {**result, 'status': 'verified', 'content': content, 'heading': heading,
                'start_line': start_line, 'end_line': end,
                'citation': f'{path}:{start_line}-{end}',
                'source_identity': path, 'revision_algorithm': 'sha256'}

    def tier(self, relative: str) -> str:
        parts = PurePosixPath(relative).parts
        for tier, roots in [('curated', self.settings.curated_roots),
                            ('imported', self.settings.imported_roots)]:
            if any(parts[:len(PurePosixPath(root).parts)] == PurePosixPath(root).parts for root in roots):
                return tier
        return 'unclassified'

    def _markdown(self, terms: list[str], query: str) -> tuple[list[dict], dict]:
        rows, errors, count = [], [], 0
        opened, skipped = 0, 0
        started = time.perf_counter()
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
                directory = root.relative_to(self.vault).as_posix()
                probe = 'placeholder.md' if directory == '.' else directory + '/placeholder.md'
                with self._parent_fd(probe) as (directory_fd, _), os.scandir(directory_fd) as listing:
                    for entry in listing:
                        entries.append((entry.name, entry.is_dir(follow_symlinks=False), entry.is_symlink()))
                        if len(entries) > room:
                            break
            except (OSError, ValueError) as exc:
                note({'code': 'walk_error', 'detail': type(exc).__name__})
                continue
            if len(entries) > room:
                limited = True
                del entries[room:]
            entries.sort(key=lambda entry: entry[0])
            descend = []
            for name, is_directory, is_link in entries:
                count += 1
                if name.startswith('.'):
                    continue
                rel = (root / name).relative_to(self.vault).as_posix()
                try:
                    if not self.permitted(rel, directory=is_directory):
                        continue
                except ValueError:
                    note({'code': 'invalid_entry'})
                    continue
                if is_directory:
                    descend.append(root / name)
                    continue
                if not name.endswith('.md') or is_link:
                    continue
                rel = (Path(root) / name).relative_to(self.vault).as_posix()
                try:
                    text, revision = self._snapshot(rel)
                except (OSError, ValueError, UnicodeError) as exc:
                    note({'path': rel, 'code': type(exc).__name__})
                    continue
                opened += 1
                match = analyze(text, query)
                # Preserve literal fallback recall; richer exact matching reorders,
                # but does not discard existing substring candidates.
                fallback = next((n for n, line in enumerate(text.splitlines(), 1)
                                 if any(term in line.casefold() for term in terms)), 0)
                if match['line'] or fallback:
                    line = match['line'] or fallback
                    snippet = text.splitlines()[line - 1]
                    citation = f'{rel}:{line}'
                    rows.append({'content': snippet[:800], 'path': rel, 'citation': citation, 'line': line,
                                 'revision': revision, 'source_status': 'opened', 'backend': 'markdown',
                                 'tier': self.tier(rel), 'tier_rule': 'configured_roots',
                                 'score': match['score'], 'relevance_reasons': match['explanations'],
                                 'metadata': match['metadata'], 'metadata_status': match['metadata_status'],
                                 'provenance': [{'backend': 'markdown', 'citation': citation}]})
            stack.extend(reversed(descend))
        status = {'status': 'degraded' if errors or limited else 'ok', 'errors': errors,
                  'entries_visited': count, 'files_opened': opened, 'unreadable_count': len(errors),
                  'coverage': 'partial' if errors or limited else 'complete',
                  'elapsed_ms': round((time.perf_counter() - started) * 1000, 3)}
        if limited:
            status['code'] = 'scan_limit'
        if len(errors) >= MAX_ERRORS:
            status['errors_truncated'] = True
        return rows, status

    def health(self) -> dict:
        """Inspect bounded directory availability, never run backends or index notes.

        Availability is a point-in-time descriptor check, not completeness, schema
        compatibility, freshness of an index, or permission to disclose every note.
        """
        budget = 64
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        scope = dict(checked=0, available=0, unavailable=0, excluded=0, unchecked=0)
        markdown = {'status': 'ok', 'mode': 'bounded_lexical', 'availability': 'checked',
                    'coverage': 'unknown', 'freshness': 'read_at_retrieval',
                    'scope_roots': scope}
        root_fd = None
        try:
            root_fd = os.open(self.vault, flags)
            for index, relative in enumerate(self.settings.read_roots):
                if index >= budget:
                    scope['unchecked'] += len(self.settings.read_roots) - index
                    break
                if not self.permitted(relative):
                    scope['excluded'] += 1
                    continue
                parts = relative_path(relative).parts
                if len(parts) > budget:
                    scope['unchecked'] += 1
                    continue
                scope['checked'] += 1
                fd = None
                try:
                    fd = os.dup(root_fd)
                    for part in parts:
                        child = os.open(part, flags, dir_fd=fd)
                        os.close(fd)
                        fd = child
                    scope['available'] += 1
                except OSError:
                    scope['unavailable'] += 1
                finally:
                    if fd is not None:
                        os.close(fd)
            if scope['unavailable'] or scope['unchecked']:
                markdown.update(status='degraded', code='scope_not_fully_available')
        except OSError:
            markdown.update(status='error', code='root_unavailable')
            scope['unchecked'] = len(self.settings.read_roots)
        finally:
            if root_fd is not None:
                os.close(root_fd)

        def backend(configured):
            return {'status': 'unknown' if configured else 'disabled',
                    'configured': configured, 'compatibility': 'not_checked',
                    'availability': 'not_checked', 'coverage': 'unknown',
                    'index_freshness': 'unknown'}

        return {'markdown': markdown,
                'qmd': backend(self.settings.qmd_workspace is not None),
                'mnemosyne': backend(self.settings.mnemosyne_db is not None),
                'policy': {'read_scope': 'explicit_roots' if self.settings.read_roots else 'vault',
                           'read_root_count': len(self.settings.read_roots),
                           'excluded_root_count': len(self.settings.excluded_roots),
                           'exclusions_take_precedence': True,
                           'allow_unlinked_records': self.settings.allow_unlinked_records},
                'capabilities': {'recall': True, 'open_passage': True,
                                 'checkpoint_proposal': self.settings.enable_checkpoint and bool(self.settings.checkpoint_profile_id.strip()),
                                 'tool_checkpoint_apply': False},
                'limits': {'max_files': self.settings.max_files,
                           'max_file_bytes': self.settings.max_file_bytes,
                           'health_root_checks': budget, 'health_root_depth': budget,
                           'qmd_timeout_seconds': self.settings.qmd_timeout}}

    def _canonical(self, relative: str, backend: str, provenance: dict, terms: list[str], query: str) -> dict:
        relative_path(relative, markdown=True)
        text, revision = self._snapshot(relative)
        lines = text.splitlines()
        # An empty note has no line 1, so it is cited by path alone rather than falsely.
        match = analyze(text, query)
        number = match['line'] or (1 if lines else 0)
        citation = f'{relative}:{number}' if number else relative
        return {'content': lines[number - 1][:800] if number else '', 'path': relative, 'line': number,
                'citation': citation, 'revision': revision, 'source_status': 'opened', 'backend': backend, 'tier': self.tier(relative), 'tier_rule': 'configured_roots',
                'score': match['score'], 'metadata': match['metadata'], 'metadata_status': match['metadata_status'],
                'relevance_reasons': match['explanations'], 'provenance': [provenance]}

    def recall(self, query: str, limit: int = 8, filters: dict | None = None) -> dict:
        if not isinstance(query, str) or not isinstance(limit, int) or isinstance(limit, bool):
            raise ValueError('query must be text and limit must be an integer')
        terms = re.findall(r'\w+', query.casefold())
        if not terms or len(query) > 2000 or not 1 <= limit <= 50:
            raise ValueError('query must contain words (max 2000 characters); limit must be 1..50')
        filters = {} if filters is None else filters
        matches_filters({}, filters)  # validate before reading any evidence
        rows, status = self._markdown(terms, query)
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
                if not self.permitted(relative):
                    continue
                if qmd_resolved < budget:
                    # A slot is consumed even if the read then fails, so failures are not free.
                    qmd_resolved += 1
                    rows.append(self._canonical(relative, 'qmd', {'backend': 'qmd', 'citation': uri}, terms, query))
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
                    if not self.permitted(canonical):
                        continue
                except (ValueError, TypeError):
                    continue
            elif not self.settings.allow_unlinked_records:
                continue
            if canonical:
                try:
                    relative_path(canonical, markdown=True)
                    # The tier comes from the canonical path even when the read is skipped.
                    row['tier'] = self.tier(canonical)
                    if memory_resolved < budget:
                        memory_resolved += 1
                        label = row.get('source_label')
                        row = self._canonical(canonical, 'mnemosyne', row['provenance'][0], terms, query)
                        if label is not None and self.settings.allow_unlinked_records:
                            row['source_label'] = label
                        resolved += 1
                    else:
                        if not self.settings.allow_unlinked_records:
                            continue
                        row['canonical_note'] = canonical
                        row['canonical_note_status'] = 'not_resolved'
                except (ValueError, TypeError, OSError, UnicodeError):
                    memory_status = {'status': 'degraded', 'code': 'canonical_unavailable'}
                    if not self.settings.allow_unlinked_records:
                        continue
                    row['canonical_note'] = canonical
                    row['canonical_note_status'] = 'invalid_or_missing'
                    row['tier'] = 'episodic'
            rows.append(row)
        merged = {}
        for row in rows:
            # Namespaced, so a note literally named like a record citation cannot collide.
            key = ('path', row['path']) if row.get('path') else ('citation', row['citation'])
            if key in merged:
                merged[key]['provenance'].extend(row['provenance'])
                # Keep a label from any surviving backend record; the first row may be Markdown.
                if row.get('source_label') and not merged[key].get('source_label'):
                    merged[key]['source_label'] = row['source_label']
                if row.get('source_label'):
                    labels = merged[key].setdefault('source_labels', [])
                    if row['source_label'] not in labels:
                        labels.append(row['source_label'])
            else:
                if row.get('source_label'):
                    row['source_labels'] = [row['source_label']]
                merged[key] = row
        rows = [row for row in merged.values() if matches_filters(row.get('metadata', {}), filters)]
        rows.sort(key=lambda r: ({'curated': 0, 'unclassified': 1, 'episodic': 2, 'imported': 3}[r['tier']],
                                 -len(r['provenance']), -r.get('score', 0), r['citation']))
        backends = {'markdown': status, 'qmd': qmd_status, 'mnemosyne': memory_status}
        return {'results': rows[:limit], 'backends': backends,
                'resolution': {'budget': budget, 'resolved': resolved},
                'degraded': any(s['status'] not in ('ok', 'disabled') for s in backends.values())}

    def checkpoint(self, path: str, title: str, summary: str, confirm: bool = False) -> dict:
        if type(confirm) is not bool:
            raise ValueError('confirm must be a boolean')
        if __package__:
            from .checkpoints import propose
        else:
            from checkpoints import propose
        proposal = propose(self, path, title, summary)
        if confirm:
            raise PermissionError('use the human-operated checkpoint apply command')
        return proposal

    def _publish_checkpoint(self, path: str, content: str, expected_vault_id: str | None = None) -> dict:
        """Private publication primitive; only the human-operated apply flow calls this."""
        relative_path(path, markdown=True)
        if not self.settings.enable_checkpoint or not self.permitted(path):
            raise PermissionError('checkpoint disabled or outside owner scope')
        result = {'path': path, 'tier': self.tier(path), 'mnemosyne': 'not_written', 'qmd': 'not_reindexed'}
        raw = content.encode('utf-8')
        if len(raw) > self.settings.max_file_bytes:
            raise ValueError('checkpoint exceeds configured canonical file byte limit')
        with self._parent_fd(path, create=True, expected_vault_id=expected_vault_id) as (parent, name):
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