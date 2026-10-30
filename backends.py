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


def failure(code):
    return [], {'status': 'error', 'code': code}


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


def _live_rows(conn, sql, params):
    """Page through matches so a heap of expired rows cannot hide the ones after it.

    Faces a hard read cap as well, so an archive of expired matches is still bounded.
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
    return live


def _run_bounded(command, cwd, env, timeout, limit):
    """Run without a shell, killing the child if it outruns either bound.

    Output goes to temp files, so disk use is capped by polling rather than by RAM.
    Returns (returncode | None, timed_out, over_limit, stdout_bytes).
    """
    with tempfile.TemporaryFile(dir=cwd) as output, tempfile.TemporaryFile(dir=cwd) as errors:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=output, stderr=errors,
                                   stdin=subprocess.DEVNULL)
        deadline = time.monotonic() + timeout
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    process.kill()
                    process.wait()
                    return None, True, False, b""
                try:
                    code = process.wait(timeout=min(0.05, remaining))
                except subprocess.TimeoutExpired:
                    if output.tell() > limit or errors.tell() > limit:
                        process.kill()
                        process.wait()
                        return None, False, True, b""
                    continue
                # A child that finished after the deadline still overran the bound.
                if time.monotonic() > deadline:
                    return None, True, False, b""
                break
            if output.tell() > limit or errors.tell() > limit:
                return code, False, True, b""
            output.seek(0)
            return code, False, False, output.read()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


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
        code, timed_out, over_limit, raw = _run_bounded(command, root, qmd_environment(root),
                                                        settings.qmd_timeout, OUTPUT_LIMIT)
        if over_limit:
            return failure('output_limit')
        if timed_out:
            return failure('timeout')
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
                try:
                    live = _live_rows(conn, sql, params)
                except ValueError:
                    return failure('invalid_expiry')
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
        return rows, {'status': 'ok', 'mode': 'readonly_sqlite_global_literal'}
    except sqlite3.OperationalError:
        return failure('database_busy_invalid_or_query_budget')
    except (sqlite3.DatabaseError, ValueError, TypeError, AttributeError, KeyError):
        return failure('invalid_database_or_metadata')
