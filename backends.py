"""Optional adapters. Never open ambient QMD or Mnemosyne state."""
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time

OUTPUT_LIMIT = 2 * 1024 * 1024
ROW_LIMIT = 500
SCAN_ROWS = 5000
PAGE_ROWS = 200


def failure(code, **extra):
    return [], {'status': 'error', 'code': code, **extra}


def expired(value) -> bool:
    """True when a row is past its validity. Raises ValueError on an unparseable value.

    SQLite is loosely typed, so a non-text ``valid_until`` is coerced rather than trusted.
    """
    if value is None or value == '':
        return False
    if not isinstance(value, str):
        value = str(value)
    expiry = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry <= datetime.now(timezone.utc)


def _metadata(raw):
    if raw is None or raw == '':
        return {}
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8')
    if not isinstance(raw, str):
        # SQLite is loosely typed; a numeric metadata_json is still usable.
        raw = str(raw)
    metadata = json.loads(raw)
    if not isinstance(metadata, dict):
        raise ValueError('metadata must be an object')
    return metadata


def _live_rows(conn, sql, probe, params):
    """Page through matches so a heap of expired rows cannot hide the ones after it.

    Faces a hard read cap as well, so an archive of expired matches is still bounded.
    Returns the live rows and whether the cap cut the scan short.
    """
    live, scanned = [], 0
    offset = 0
    while len(live) <= ROW_LIMIT and scanned < SCAN_ROWS:
        page = list(conn.execute(sql, (*params, offset)))
        if not page:
            break
        scanned += len(page)
        offset += len(page)
        live.extend(row for row in page if not expired(row['valid_until']))
        if len(page) < PAGE_ROWS:
            break
    if scanned >= SCAN_ROWS:
        # One-row existence probe, built by the caller: a further page would overrun the cap.
        return live, bool(list(conn.execute(probe, (*params, scanned))))
    return live, False


def _stop(process, timeout):
    """Reap a child, bounded. Returns True once it is gone, False if it may still run."""
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=timeout)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def _run_bounded(command, cwd, env, timeout, limit):
    """Run without a shell, killing the child if it outruns either bound.

    Output goes to temp files, so disk use is capped by polling rather than by RAM.
    Returns (returncode | None, timed_out, over_limit, stdout_bytes, reaped).
    ``reaped`` is False only when the child could not be confirmed gone.
    """
    with tempfile.TemporaryFile(dir=cwd) as output, tempfile.TemporaryFile(dir=cwd) as errors:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=output, stderr=errors,
                                   stdin=subprocess.DEVNULL)
        stopped = False
        cleanup_deadline = None
        try:
            # Inside the try, so a failure here cannot strand the child we just launched.
            deadline = time.monotonic() + timeout
            def cleanup_budget():
                return max(0.001, (cleanup_deadline or 0.0) - time.monotonic())
            def settle():
                # Cleanup gets its own 5s window from the moment it starts, not from the
                # execution deadline, so an early overflow does not wait out the timeout too.
                nonlocal cleanup_deadline
                if cleanup_deadline is None:
                    try:
                        cleanup_deadline = time.monotonic() + 5
                    except RuntimeError:
                        return _stop(process, 5)
                try:
                    return _stop(process, cleanup_budget())
                except OSError:
                    return False
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    stopped = settle()
                    return None, True, False, b"", stopped
                try:
                    code = process.wait(timeout=min(0.05, remaining))
                except subprocess.TimeoutExpired:
                    if output.tell() > limit or errors.tell() > limit:
                        stopped = settle()
                        return None, False, True, b"", stopped
                    continue
                # A child that finished after the deadline still overran the bound.
                if time.monotonic() > deadline:
                    return None, True, False, b"", True
                break
            if output.tell() > limit or errors.tell() > limit:
                return code, False, True, b"", True
            output.seek(0)
            return code, False, False, output.read(), True
        finally:
            # Retry only if the earlier attempt could not confirm the child was gone. Both
            # attempts share one deadline, so they cannot stack into two timeouts, and a clock
            # failure here must not skip reaping altogether.
            if not stopped and process.poll() is None:
                try:
                    budget = 5.0
                    if cleanup_deadline is not None:
                        try:
                            budget = max(0.001, cleanup_deadline - time.monotonic())
                        except RuntimeError:
                            pass
                    _stop(process, budget)
                except (OSError, RuntimeError):
                    pass


def qmd_environment(workspace: Path) -> dict[str, str]:
    """Deliberately do not inherit provider credentials, NODE_OPTIONS or QMD overrides."""
    return {'PATH': os.environ.get('PATH', os.defpath), 'HOME': str(workspace / 'home'),
            'HERMES_HOME': str(workspace / 'hermes'), 'XDG_CONFIG_HOME': str(workspace / 'config'),
            'QMD_CONFIG_DIR': str(workspace / 'config'), 'INDEX_PATH': str(workspace / 'index.sqlite3'),
            'XDG_CACHE_HOME': str(workspace / 'cache'), 'XDG_DATA_HOME': str(workspace / 'data'),
            'TMPDIR': str(workspace), 'QMD_SOURCE_MODE': '0'}


def qmd_search(settings, query):
    root = settings.qmd_workspace
    if root is None:
        return [], {'status': 'disabled'}
    if not root.is_dir() or not (root / 'index.sqlite3').is_file() or not (root / 'config/wayfinder.yml').is_file():
        return failure('uninitialized_workspace')
    # Reject linked state: explicit opt-in to one workspace is not consent to ambient indexes.
    if any(p.is_symlink() for p in (root, root / 'index.sqlite3', root / 'config', root / 'config/wayfinder.yml')):
        return failure('symlink_state')
    command = list(settings.qmd_command) + ['--index', 'wayfinder', 'search', query, '-c', 'vault', '--json', '-n', '50']
    try:
        # QMD is a user-chosen trusted local executable; both streams are bounded while running.
        code, timed_out, over_limit, raw, reaped = _run_bounded(
            command, root, qmd_environment(root), settings.qmd_timeout, OUTPUT_LIMIT)
        child = 'reaped' if reaped else 'alive'
        if over_limit:
            return failure('output_limit', child=child)
        if timed_out:
            return failure('timeout', child=child)
        if code:
            return failure('process_failed')
        data = json.loads(raw)
        # Current QMD emits [] or an empty object for no matches; accept only documented list shape.
        if not isinstance(data, list) or len(data) > 1000:
            return failure('invalid_result')
        return data, {'status': 'ok', 'mode': 'bm25_local'}
    except (json.JSONDecodeError, UnicodeError):
        return failure('invalid_json')
    except OSError:
        return failure('executable_or_workspace_unavailable')


def mnemosyne_search(settings, terms):
    path = settings.mnemosyne_db
    if path is None:
        return [], {'status': 'disabled'}
    if not path.is_file():
        return failure('missing_database')
    if path.is_symlink():
        return failure('symlink_state')
    rows = []
    truncated = False
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            ticks = [0]
            def interrupt():
                ticks[0] += 1
                return ticks[0] > 1000
            conn.set_progress_handler(interrupt, 1000)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            usable = 0
            for table in ('working_memory', 'episodic_memory'):
                if table not in tables:
                    continue
                columns = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
                required = {'id', 'content', 'source', 'metadata_json', 'scope', 'session_id', 'superseded_by', 'valid_until'}
                if not required <= columns:
                    return failure('unsupported_schema')
                usable += 1
                clauses = ['LOWER(content) LIKE ? ESCAPE \'\\\'' for _ in terms]
                params = ['%' + t.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%' for t in terms]
                # Expiry is filtered in Python, so the row cap has to be applied after it.
                sql = f"SELECT id, content, source, metadata_json, valid_until FROM {table} WHERE scope = 'global' AND superseded_by IS NULL AND (" + ' OR '.join(clauses) + f') LIMIT {PAGE_ROWS} OFFSET ?'
                probe = f"SELECT 1 FROM {table} WHERE scope = 'global' AND superseded_by IS NULL AND (" + ' OR '.join(clauses) + ') LIMIT 1 OFFSET ?'
                try:
                    live, cut_short = _live_rows(conn, sql, probe, params)
                except ValueError:
                    return failure('invalid_expiry')
                if cut_short:
                    truncated = True
                if len(live) > ROW_LIMIT:
                    return failure('result_limit')
                for row in live:
                    try:
                        metadata = _metadata(row['metadata_json'])
                    except (ValueError, TypeError, json.JSONDecodeError, UnicodeError):
                        return failure('invalid_metadata')
                    citation = f"mnemosyne:{table}:{row['id']}"
                    rows.append({'content': str(row['content'])[:800], 'citation': citation,
                                 'backend': 'mnemosyne', 'tier': 'episodic', 'tier_rule': 'episodic_store',
                                 'source_label': row['source'], 'canonical_note': metadata.get('vault_note'),
                                 'provenance': [{'backend': 'mnemosyne', 'citation': citation}]})
            if not usable:
                return failure('unsupported_schema')
        if truncated:
            # Rows are still usable, but the scan stopped early: say so instead of "ok".
            return rows, {'status': 'degraded', 'code': 'scan_limit',
                          'mode': 'readonly_sqlite_global_literal'}
        return rows, {'status': 'ok', 'mode': 'readonly_sqlite_global_literal'}
    except sqlite3.OperationalError:
        return failure('database_busy_invalid_or_query_budget')
    except (sqlite3.DatabaseError, ValueError, TypeError, AttributeError, KeyError):
        return failure('invalid_database_or_metadata')
