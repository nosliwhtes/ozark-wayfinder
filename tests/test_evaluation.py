import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('evaluation', ROOT / 'tools/evaluate_retrieval.py')
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


class EvaluationTests(unittest.TestCase):
    def test_ndcg_rewards_order_and_penalizes_irrelevant(self):
        labels = {'a': 3, 'b': 1}
        self.assertEqual(evaluation.ndcg(['a', 'b'], labels), 1)
        self.assertLess(evaluation.ndcg(['b', 'a'], labels), 1)
        self.assertEqual(evaluation.ndcg(['z'], labels), 0)
        self.assertIsNone(evaluation.ndcg([], {}))

    def test_citations_reject_wrong_file_line_and_text(self):
        vault = ROOT / 'tests/evaluation/v1/vault'
        row = {'path': 'curated/atlas.md', 'line': 1, 'citation': 'curated/atlas.md:1', 'content': '# Atlas shipping'}
        self.assertTrue(evaluation.valid_citation(row, vault))
        for update in ({'path': 'curated/orchid.md'}, {'line': 999}, {'content': 'invented'}, {'path': '../queries.json'}):
            self.assertFalse(evaluation.valid_citation(dict(row, **update), vault))

    def test_fixture_splits_and_safety(self):
        dev = evaluation.evaluate(split='development', repeats=1)
        heldout = evaluation.evaluate(split='heldout', repeats=1)
        self.assertFalse({q['id'] for q in dev['queries']} & {q['id'] for q in heldout['queries']})
        for report in (dev, heldout):
            self.assertEqual(report['metrics']['invalid_citations'], 0)
            self.assertEqual(report['metrics']['restricted_marker_disclosures'], 0)
            self.assertEqual(len(report['corpus']['sha256']), 64)
            for case in report['queries']:
                if 'exact_identifier' in case['tags']:
                    self.assertEqual(case['recall_at_5'], 1, case['id'])


if __name__ == '__main__':
    unittest.main()
