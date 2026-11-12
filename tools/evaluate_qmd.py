#!/usr/bin/env python3
"""Explicit opt-in BM25 comparison over the shipped synthetic fixture only.

Creates and removes a disposable QMD HOME/config/index. Never runs embed/query/pull.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import tempfile
import time
from urllib.parse import unquote, urlsplit

from evaluate_retrieval import ROOT, ndcg, valid_citation
from backends import qmd_environment
from wayfinder import Settings, Wayfinder, relative_path


def evaluate(executable, repeats=3):
    fixture = ROOT / 'tests/evaluation/v1'
    manifest = json.loads((fixture / 'queries.json').read_text())
    # Only copy non-hidden, approved synthetic files; no arbitrary vault option.
    source = fixture / 'vault'
    approved = [p for p in sorted(source.rglob('*.md')) if not any(s.startswith('.') for s in p.relative_to(source).parts)]
    digest = hashlib.sha256()
    with tempfile.TemporaryDirectory(prefix='wayfinder-qmd-eval-') as temp:
        root = Path(temp)
        vault = root / 'vault'
        workspace = root / 'qmd'
        workspace.mkdir()
        for path in approved:
            relative = path.relative_to(source)
            raw = path.read_bytes()
            digest.update(relative.as_posix().encode() + b'\0' + raw + b'\0')
            target = vault / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        env = qmd_environment(workspace)
        def run(*args):
            completed = subprocess.run([executable, '--index', 'wayfinder', *args], cwd=workspace,
                                       env=env, text=True, capture_output=True, timeout=90)
            if completed.returncode:
                raise RuntimeError(f'QMD {args[0]} failed (exit {completed.returncode}): {completed.stderr[-1000:]}')
            return completed.stdout
        version = run('--version').strip()
        run('collection', 'add', str(vault), '--name', 'vault', '--mask', '**/*.md')
        service = Wayfinder(Settings(vault_root=vault))
        output = {}
        for engine in ('scanner', 'qmd_bm25'):
            cases, times = [], []
            for query in manifest['queries']:
                latencies = []
                for _ in range(repeats):
                    start = time.perf_counter()
                    if engine == 'scanner':
                        reply = service.recall(query['query'], limit=5)
                        rows = reply['results']
                    else:
                        raw = run('search', query['query'], '-c', 'vault', '--json', '-n', '5')
                        hits = json.loads(raw) if raw.strip() else []
                        rows = []
                        seen = set()
                        for hit in hits:
                            parsed = urlsplit(hit['file'])
                            if parsed.scheme != 'qmd' or parsed.netloc != 'vault' or parsed.query not in ('', 'index=wayfinder') or parsed.fragment:
                                raise ValueError('unexpected QMD source identity')
                            relative = unquote(parsed.path.lstrip('/'))
                            relative_path(relative, markdown=True)
                            if relative in seen:
                                continue
                            seen.add(relative)
                            # Hydrate against fixture bytes; not an assertion of native QMD citation quality.
                            rows.append(service._canonical(relative, 'qmd', {'backend': 'qmd', 'citation': hit['file']}, [query['query'].casefold()], query['query']))
                    latencies.append((time.perf_counter()-start)*1000)
                paths = [r['path'] for r in rows]
                labels = query['relevance']
                cases.append({'id':query['id'], 'split':query['split'], 'expected_sources':labels,
                              'returned_sources':paths, 'recall_at_5':len(set(paths)&set(labels))/len(labels) if labels else None,
                              'ndcg_at_5':ndcg(paths, labels), 'invalid_hydrated_citations':sum(not valid_citation(r,vault) for r in rows),
                              'no_answer_false_positive':bool(rows) if not labels else None,
                              'restricted_marker_disclosed':any(m in json.dumps(rows) for m in manifest['restricted_markers']),
                              'latency_ms':latencies})
                times.extend(latencies)
            answered = [q for q in cases if q['recall_at_5'] is not None]
            controls = [q for q in cases if q['no_answer_false_positive'] is not None]
            output[engine] = {'recall_at_5':statistics.mean(q['recall_at_5'] for q in answered),
                              'ndcg_at_5':statistics.mean(q['ndcg_at_5'] for q in answered),
                              'invalid_hydrated_citations':sum(q['invalid_hydrated_citations'] for q in cases),
                              'no_answer_false_positive_rate':sum(q['no_answer_false_positive'] for q in controls)/len(controls),
                              'restricted_marker_disclosures':sum(q['restricted_marker_disclosed'] for q in cases),
                              'latency_ms':{'p50':statistics.median(times), 'p95':sorted(times)[math.ceil(.95*len(times))-1], 'max':max(times)},
                              'queries':cases}
        model_files = [p for p in workspace.rglob('*') if p.is_file() and p.suffix == '.gguf']
        if model_files:
            raise RuntimeError('unexpected model file in isolated evaluation')
        return {'fixture_version':manifest['version'], 'qmd_version':version,
                'machine':{'platform':platform.platform(),'python':platform.python_version(),'cpu':platform.processor()},
                'approved_corpus':{'documents':len(approved),'sha256':digest.hexdigest()},
                'disclosure':'Both engines see the same non-hidden synthetic files. Hidden marker is excluded before indexing.',
                'citation_scope':'BM25 candidates hydrated against current source; native QMD snippets are not graded.',
                'latency_scope':'QMD includes CLI startup and hydration; scanner runs in process. Tiny warm-cache corpus.',
                'semantic':'Not run; no model downloads or embedding commands.', 'results':output}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qmd', required=True, help='Explicit installed QMD executable')
    parser.add_argument('--repeats',type=int,default=3)
    args=parser.parse_args()
    if not 1 <= args.repeats <= 100:
        parser.error('repeats must be 1..100')
    executable=Path(args.qmd).expanduser().resolve(strict=True)
    print(json.dumps(evaluate(str(executable),args.repeats),indent=2))
