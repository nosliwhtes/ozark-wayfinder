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


def _run_bounded(command, cwd, env, timeout, limit, outcome):
    """Run without a shell, killing the child if it outruns either bound.

    Output goes to temp files, so disk use is capped by polling rather than by RAM.
    Returns (returncode | None, timed_out, over_limit, stdout_bytes) and records the fate of
    the child in ``outcome['reaped']`` (True / False / None while it is still running), so a
    retry performed during unwinding is visible to the caller.
    """
    outcome['reaped'] = None
    outcome['spawned'] = False
    with tempfile.TemporaryFile(dir=cwd) as output, tempfile.TemporaryFile(dir=cwd) as errors:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=output, stderr=errors,
                                   stdin=subprocess.DEVNULL)
        outcome['spawned'] = True
        window = {'start': None, 'done': False}

        def stop_child():
            """Up to two kill+wait attempts inside one 5s window. Idempotent."""
            if window['done']:
                return outcome['reaped']
            window['done'] = True

            def budget(spent):
                floor = 5.0 if spent == 0 else 0.001
                # A clock that fails here must still yield a number, or _stop never runs.
                try:
                    if window['start'] is None:
                        return floor
                    return max(0.001, (window['start'] + 5) - time.monotonic())
                except (RuntimeError, OSError):
                    return floor

            def attempt(spent):
                try:
                    return _stop(process, budget(spent))
                except (OSError, RuntimeError):
                    return False

            try:
                window['start'] = time.monotonic()
            except (RuntimeError, OSError):
                window['start'] = None
            reaped = attempt(0)
            if not reaped:
                try:
                    alive = process.poll() is None
                except OSError:
                    alive = True
                if alive:
                    reaped = attempt(1)
            outcome['reaped'] = reaped
            return reaped

        try:
            # Inside the try, so a failure here cannot strand the child we just launched.
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    stop_child()
                    return None, True, False, b""
                try:
                    code = process.wait(timeout=min(0.05, remaining))
                except subprocess.TimeoutExpired:
                    if output.tell() > limit or errors.tell() > limit:
                        stop_child()
                        return None, False, True, b""
                    continue
                # It is gone: record that before any further clock read can fail.
                outcome['reaped'] = True
                # A child that finished after the deadline still overran the bound.
                if time.monotonic() > deadline:
                    return None, True, False, b""
                break
            if output.tell() > limit or errors.tell() > limit:
                outcome['reaped'] = True
                return code, False, True, b""
            output.seek(0)
            outcome['reaped'] = True
            return code, False, False, output.read()
        finally:
            # Only if we never got as far as stopping it (an exception while unwinding), so the
            # two windows can never add up.
            if not window['done']:
                try:
                    if process.poll() is None:
                        stop_child()
                except OSError:
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
    outcome = {}
    try:
        # QMD is a user-chosen trusted local executable; both streams are bounded while running.
        try:
            code, timed_out, over_limit, raw = _run_bounded(
                command, root, qmd_environment(root), settings.qmd_timeout, OUTPUT_LIMIT, outcome)
        except (OSError, RuntimeError) as exc:
            # Launch or read failure: only claim a child state if one was actually started.
            if not outcome.get('spawned'):
                return failure('executable_or_workspace_unavailable', child='not_launched')
            code = 'internal_error' if isinstance(exc, RuntimeError) else 'executable_or_workspace_unavailable'
            return failure(code, child='reaped' if outcome.get('reaped') else 'alive')
        child = 'reaped' if outcome.get('reaped') else 'alive'
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
    except RuntimeError:
        # e.g. a clock that stopped answering; still report the child rather than crashing.
        return failure('internal_error',
                       child='reaped' if outcome.get('reaped') else
                       ('alive' if outcome.get('spawned') else 'not_launched'))


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
