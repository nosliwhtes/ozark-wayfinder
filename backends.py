"""Optional adapters. Never open ambient QMD or Mnemosyne state."""
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile


def failure(code):
    return [], {'status': 'error', 'code': code}


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
        # Spool bounded output outside RAM; QMD is a user-chosen trusted local executable.
        with tempfile.TemporaryFile(dir=root) as output, tempfile.TemporaryFile(dir=root) as errors:
            reply = subprocess.run(command, cwd=root, env=qmd_environment(root), stdout=output,
                                   stderr=errors, timeout=settings.qmd_timeout, check=False)
            if reply.returncode:
                return failure('process_failed')
            if output.tell() > 2 * 1024 * 1024:
                return failure('output_limit')
            output.seek(0)
            data = json.loads(output.read())
        # Current QMD emits [] or an empty object for no matches; accept only documented list shape.
        if not isinstance(data, list) or len(data) > 1000:
            return failure('invalid_result')
        return data, {'status': 'ok', 'mode': 'bm25_local'}
    except subprocess.TimeoutExpired:
        return failure('timeout')
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
                sql = f"SELECT id, content, source, metadata_json, valid_until FROM {table} WHERE scope = 'global' AND superseded_by IS NULL AND (" + ' OR '.join(clauses) + ') LIMIT 101'
                matched = list(conn.execute(sql, params))
                if len(matched) > 100:
                    return failure('result_limit')
                for row in matched:
                    if row['valid_until']:
                        try:
                            expiry = datetime.fromisoformat(row['valid_until'].replace('Z', '+00:00'))
                            if expiry.tzinfo is None:
                                expiry = expiry.replace(tzinfo=timezone.utc)
                            if expiry <= datetime.now(timezone.utc):
                                continue
                        except (ValueError, TypeError):
                            return failure('invalid_expiry')
                    metadata = json.loads(row['metadata_json'] or '{}')
                    if not isinstance(metadata, dict):
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
    except (sqlite3.DatabaseError, ValueError, TypeError):
        return failure('invalid_database_or_metadata')
