#!/usr/bin/env python3
"""Offline synthetic scanner benchmark. No private vault or optional backend access."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import platform
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wayfinder import Settings, Wayfinder


def ndcg(paths, relevance, k=5):
    def dcg(gains):
        return sum((2 ** gain - 1) / math.log2(i + 2) for i, gain in enumerate(gains))
    ideal = dcg(sorted(relevance.values(), reverse=True)[:k])
    return dcg([relevance.get(p, 0) for p in paths[:k]]) / ideal if ideal else None


def valid_citation(row, vault):
    """A citation is valid only if its content matches its stated file and line."""
    try:
        path = row['path']
        relative = Path(path)
        if relative.is_absolute() or any(p.startswith('.') for p in relative.parts):
            return False
        file = vault / relative
        if not file.resolve().is_relative_to(vault.resolve()):
            return False
        number = row['line']
        lines = file.read_text(encoding='utf-8').splitlines()
        return (type(number) is int and 1 <= number <= len(lines)
                and row['citation'] == f'{path}:{number}'
                and row['content'] == lines[number - 1][:800])
    except (KeyError, OSError, ValueError, TypeError):
        return False


def evaluate(fixture=ROOT / 'tests/evaluation/v1', split='all', repeats=3):
    fixture = Path(fixture)
    manifest = json.loads((fixture / 'queries.json').read_text(encoding='utf-8'))
    vault = fixture / 'vault'
    service = Wayfinder(Settings(vault_root=vault.resolve()))
    cases = [q for q in manifest['queries'] if split == 'all' or q['split'] == split]
    if not cases:
        raise ValueError('no cases selected')
    outcomes, latencies = [], []
    for case in cases:
        durations = []
        for _ in range(repeats):
            started = time.perf_counter()
            response = service.recall(case['query'], limit=5)
            durations.append((time.perf_counter() - started) * 1000)
        rows = response['results']
        paths = [r.get('path') for r in rows]
        relevance = case['relevance']
        recall = len(set(paths) & set(relevance)) / len(relevance) if relevance else None
        valid = [valid_citation(r, vault) for r in rows]
        leaked = any(marker in json.dumps(response) for marker in manifest.get('restricted_markers', []))
        latencies.extend(durations)
        outcomes.append({'id': case['id'], 'split': case['split'], 'tags': case['tags'],
                         'query': case['query'], 'expected_sources': relevance, 'returned_sources': paths,
                         'recall_at_5': recall, 'ndcg_at_5': ndcg(paths, relevance),
                         'citations': len(valid), 'invalid_citations': valid.count(False),
                         'no_answer_false_positive': bool(rows) if not relevance else None,
                         'restricted_marker_disclosed': leaked, 'degraded': response['degraded'],
                         'latency_ms': durations})
    corpus = sorted(vault.rglob('*.md'))
    digest = hashlib.sha256()
    for path in corpus:
        digest.update(path.relative_to(vault).as_posix().encode() + b'\0' + path.read_bytes() + b'\0')
    answered = [o for o in outcomes if o['recall_at_5'] is not None]
    controls = [o for o in outcomes if o['no_answer_false_positive'] is not None]
    citations = sum(o['citations'] for o in outcomes)
    invalid = sum(o['invalid_citations'] for o in outcomes)
    return {'fixture_version': manifest['version'], 'engine': 'dependency-free Markdown scanner',
            'optional_backends': 'disabled; QMD BM25/hybrid not measured', 'split': split,
            'machine': {'platform': platform.platform(), 'python': platform.python_version(),
                        'processor': platform.processor() or 'unknown'},
            'corpus': {'documents': len(corpus), 'bytes': sum(p.stat().st_size for p in corpus),
                       'sha256': digest.hexdigest()},
            'metrics': {'recall_at_5': statistics.mean(o['recall_at_5'] for o in answered) if answered else None,
                        'ndcg_at_5': statistics.mean(o['ndcg_at_5'] for o in answered) if answered else None,
                        'citation_validity': (citations-invalid)/citations if citations else None,
                        'invalid_citations': invalid,
                        'no_answer_false_positive_rate': sum(o['no_answer_false_positive'] for o in controls)/len(controls) if controls else None,
                        'restricted_marker_disclosures': sum(o['restricted_marker_disclosed'] for o in outcomes),
                        'latency_ms': {'p50': statistics.median(latencies),
                                       'p95': sorted(latencies)[math.ceil(.95*len(latencies))-1],
                                       'max': max(latencies)}}, 'queries': outcomes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', choices=('all', 'development', 'heldout'), default='all')
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 100:
        parser.error('--repeats must be between 1 and 100')
    report = evaluate(split=args.split, repeats=args.repeats)
    print(json.dumps(report, indent=2))
    # Quality is reported, not an invented target. Citation/disclosure are hard gates.
    return int(bool(report['metrics']['invalid_citations'] or report['metrics']['restricted_marker_disclosures']))


if __name__ == '__main__':
    raise SystemExit(main())
