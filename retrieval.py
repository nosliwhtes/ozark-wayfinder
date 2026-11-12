"""Bounded, non-executing metadata and lexical relevance helpers.

This intentionally supports a small frontmatter subset, not YAML. Relevance is
not authority or confidence. Paths and disclosure policy remain the caller's job.
"""
from __future__ import annotations

from datetime import date
import json
import re

FIELDS = frozenset(('project', 'status', 'type', 'valid_from', 'valid_until', 'aliases'))
MAX_HEADER_BYTES = 8192
MAX_HEADER_LINES = 100


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('date must be YYYY-MM-DD')
    return date.fromisoformat(value)


def _scalar(value):
    value = value.strip()
    if value.startswith('"'):
        value = json.loads(value)
    elif value.startswith("'"):
        if not value.endswith("'") or len(value) < 2:
            raise ValueError('unterminated scalar')
        value = value[1:-1].replace("''", "'")
    elif not value or any(c in value for c in '{}[]!&*|>') or value.startswith(('#', '- ')):
        raise ValueError('unsupported scalar')
    if not isinstance(value, str) or not value.strip() or len(value) > 500 or any(ord(c) < 32 for c in value):
        raise ValueError('invalid scalar')
    return value


def extract_metadata(text):
    """Return metadata, status, and 1-based first body line; malformed is fail-closed."""
    if not isinstance(text, str):
        raise ValueError('text must be a string')
    lines = text.splitlines()
    absent = {'metadata': {}, 'status': 'absent', 'body_start_line': 1}
    if not lines or lines[0] != '---':
        return absent
    end = next((i for i, line in enumerate(lines[1:MAX_HEADER_LINES + 1], 1) if line == '---'), None)
    if end is None:
        return {**absent, 'status': 'limit' if len(lines) > MAX_HEADER_LINES else 'invalid'}
    if len('\n'.join(lines[:end + 1]).encode('utf-8')) > MAX_HEADER_BYTES:
        return {**absent, 'status': 'limit', 'body_start_line': end + 2}
    result = {'metadata': {}, 'status': 'ok', 'body_start_line': end + 2}
    metadata, seen = {}, set()
    try:
        i = 1
        while i < end:
            line = lines[i]
            i += 1
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            match = re.fullmatch(r'([a-z_]+):(?:[ \t]*(.*))?', line)
            if not match:
                raise ValueError('unsupported header syntax')
            key, value = match[1], match[2] or ''
            if key in seen:
                raise ValueError('duplicate metadata field')
            seen.add(key)
            # Ignore unknown scalar fields, but never interpret objects/tags.
            if key not in FIELDS:
                continue
            if key == 'aliases':
                if value.strip():
                    aliases = json.loads(value)
                    if not isinstance(aliases, list) or any(not isinstance(a, str) for a in aliases):
                        raise ValueError('aliases must be a list')
                    aliases = [_scalar(json.dumps(item)) for item in aliases]
                else:
                    aliases = []
                    while i < end and lines[i].startswith('  - '):
                        aliases.append(_scalar(lines[i][4:]))
                        i += 1
                if len(aliases) > 20:
                    raise ValueError('too many aliases')
                metadata[key] = aliases
            else:
                metadata[key] = _scalar(value)
                if key in ('valid_from', 'valid_until'):
                    _date(metadata[key])
        if 'valid_from' in metadata and 'valid_until' in metadata and metadata['valid_from'] > metadata['valid_until']:
            raise ValueError('inverted validity interval')
    except (ValueError, TypeError):
        return {**result, 'status': 'invalid'}
    return {**result, 'metadata': metadata}


def matches_filters(metadata, filters):
    """Exact metadata matches; valid_at requires at least one explicit validity bound."""
    if not isinstance(filters, dict) or any(k not in ('project', 'status', 'type', 'valid_at') for k in filters):
        raise ValueError('unsupported filters')
    for key, value in filters.items():
        if not isinstance(value, str) or not value or len(value) > 500:
            raise ValueError('filter must be nonempty text')
        if key == 'valid_at':
            _date(value)
    for key, value in filters.items():
        if key != 'valid_at':
            if metadata.get(key) != value:
                return False
        else:
            if not ('valid_from' in metadata or 'valid_until' in metadata):
                return False
            if metadata.get('valid_from', value) > value or metadata.get('valid_until', value) < value:
                return False
    return True


def _tokens(text):
    return re.findall(r'[^\W_]+(?:[-_./][^\W_]+)*', text.casefold())


def analyze(text, query):
    """Select a real source line with explainable, bounded lexical relevance."""
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        raise ValueError('query must contain text, at most 2000 characters')
    parsed = extract_metadata(text)
    terms = set(_tokens(query))
    phrase = ' '.join(query.casefold().split())
    identifiers = {t for t in terms if any(c in t for c in '-_./')}
    lines = text.splitlines()
    best_score, best_line, best_reasons = 0, 0, []
    alias_match = any(phrase == ' '.join(a.casefold().split()) for a in parsed['metadata'].get('aliases', []))
    structures = _headings(lines, parsed['body_start_line'])
    for number, (line, structure) in enumerate(zip(lines, structures), 1):
        tokens = set(_tokens(line))
        hits = terms & tokens
        if not hits:
            # Alias exact matching can include punctuation the tokenizer omits.
            continue
        reasons = ['terms']
        score = len(hits)
        normalized = ' '.join(line.casefold().split())
        if len(terms) > 1 and phrase in normalized:
            score += 6
            reasons.append('phrase')
        if identifiers & tokens:
            score += 10
            reasons.append('exact_identifier')
        if number < parsed['body_start_line']:
            if alias_match and ('aliases:' in line or line.startswith('  - ')) and phrase in normalized:
                score += 15
                reasons.append('alias')
            else:
                continue
        # Heading structure is handled below independently of matching lines.
        if structure['heading']:
            label = 'title' if structure['level'] == 1 else 'heading'
            score += 12 if label == 'title' else 8
            reasons.append(label)
        elif structure['terms'] & terms and terms <= tokens | structure['terms'] and len(terms) > len(hits):
            score += 14
            reasons.append('section')
        if score > best_score:
            best_score, best_line, best_reasons = score, number, reasons
    return {'score': best_score, 'line': best_line, 'explanations': best_reasons,
            'metadata': parsed['metadata'], 'metadata_status': parsed['status']}


def _headings(lines, body_start):
    fence, heading_terms = None, set()
    for number, line in enumerate(lines, 1):
        structure = {'heading': False, 'level': 0, 'terms': heading_terms}
        if number < body_start:
            yield structure
            continue
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
        elif marker:
            fence = marker[1]
        else:
            heading = re.match(r'^ {0,3}(#{1,6})(?:\s|$)(.*)', line)
            if heading:
                heading_terms = set(_tokens(heading[2]))
                structure = {'heading': True, 'level': len(heading[1]), 'terms': heading_terms}
        yield structure
