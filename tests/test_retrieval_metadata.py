import unittest
from retrieval import analyze, extract_metadata, matches_filters


class MetadataTests(unittest.TestCase):
    def test_simple_frontmatter_is_data_with_line_coordinates(self):
        text = '---\nproject: atlas\nstatus: current\ntype: decision\nvalid_from: 2026-01-01\nvalid_until: 2026-12-31\naliases: ["power store", "A-42"]\n---\n# Battery\nBody\n'
        result = extract_metadata(text)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['body_start_line'], 9)
        self.assertEqual(result['metadata']['aliases'], ['power store', 'A-42'])
        self.assertTrue(matches_filters(result['metadata'], {'project':'atlas', 'valid_at':'2026-06-01'}))
        self.assertFalse(matches_filters(result['metadata'], {'valid_at':'2027-01-01'}))

    def test_invalid_or_unbounded_metadata_is_not_partially_trusted(self):
        for text in ('---\nproject: x\nproject: y\n---\nbody', '---\nvalid_from: 2026-02-31\n---', '---\naliases: !!python/object:x\n---', '---\nproject: x\nbody'):
            result = extract_metadata(text)
            self.assertEqual(result['status'], 'invalid')
            self.assertEqual(result['metadata'], {})
        result = extract_metadata('---\nproject: ' + 'x' * 9000 + '\n---')
        self.assertEqual(result['status'], 'limit')
        self.assertEqual(result['metadata'], {})

    def test_no_metadata_and_filter_validation(self):
        self.assertEqual(extract_metadata('# Plain')['status'], 'absent')
        self.assertFalse(matches_filters({}, {'status':'current'}))
        self.assertFalse(matches_filters({}, {'valid_at':'2026-01-01'}))
        for filters in ({'unknown':'x'}, {'status':True}, {'valid_at':'not-a-date'}):
            with self.assertRaises(ValueError):
                matches_filters({}, filters)

    def test_aliases_block_and_quoted_scalars(self):
        result = extract_metadata('---\nproject: "Atlas"\naliases:\n  - "power store"\n  - battery pack\n---\n# Title')
        self.assertEqual(result['metadata'], {'project':'Atlas','aliases':['power store','battery pack']})

    def test_identifier_is_whole_token_and_phrase_title_are_explained(self):
        exact = analyze('# Widget A-42\nSerial A-42 is available.', 'A-42')
        other = analyze('# Widget A-420\nA-420 is available.', 'A-42')
        self.assertGreater(exact['score'], 0)
        self.assertEqual(other['score'], 0)
        self.assertIn('exact_identifier', exact['explanations'])
        self.assertIn('title', exact['explanations'])
        self.assertEqual(exact['line'], 1)
        self.assertIn('phrase', analyze('# Solar battery\nChosen.', 'solar battery')['explanations'])

    def test_alias_and_heading_matches_keep_real_source_lines(self):
        text = '---\naliases: ["power store"]\n---\n# Decision\n## Solar\nBattery selected.\n'
        alias = analyze(text, 'power store')
        self.assertIn('alias', alias['explanations'])
        self.assertEqual(alias['line'], 2)
        heading = analyze(text, 'solar battery')
        self.assertIn('section', heading['explanations'])
        self.assertEqual(heading['line'], 6)

    def test_code_comment_is_not_heading_and_no_match_stays_empty(self):
        result = analyze('```python\n# solar\n```\nPlain.', 'solar')
        self.assertNotIn('title', result['explanations'])
        self.assertNotIn('heading', result['explanations'])
        self.assertEqual(analyze('# Plan\nNothing relevant', 'quasar')['line'], 0)
