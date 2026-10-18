"""Synthetic-only regression coverage for descriptive statistical templates."""
from __future__ import annotations
import unittest
import tempfile
import os
from pathlib import Path
import copy
import csv
import hashlib
import json
import math
import numpy as np
from poi_audit.common import AuditError
from poi_audit.statistics import load_statistics_config, prepare_statistics, write_statistics_tables

def variable(field):
    return {'field': field, 'label': f'Synthetic {field}', 'unit': 'unitless'}

def scatter(**changes):
    return {'id': 'scatter', 'type': 'scatter', 'x': variable('x'), 'y': variable('y'), **changes}

def violin(**changes):
    return {'id': 'violin', 'type': 'half_violin', 'value': variable('x'), 'group_field': 'group', **changes}

def quadrant(**changes):
    return {'id': 'quadrant', 'type': 'quadrant', 'x': variable('x'), 'y': variable('y'), 'thresholds': {'x': {'method': 'median'}, 'y': {'method': 'median'}}, **changes}

def case(tmp_path, records=None, plots=None, *, headers=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    with (tmp_path / 'observations.csv').open('w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(headers or ['id', 'group', 'x', 'y', 'other'])
        writer.writerows(records if records is not None else [['a', 'A', 1, 2, ''], ['b', 'B', 3, 4, '']])
    config = {'schema_version': 1, 'kind': 'statistical_plots', 'title': 'Synthetic tests', 'synthetic': True, 'input': {'path': 'observations.csv', 'record_unit': 'one synthetic place per CSV row', 'id_field': 'id'}, 'plots': plots if plots is not None else [scatter()]}
    return (config, tmp_path)

class StatisticsTests(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(temp.name)
        original_cwd = Path.cwd()
        self.addCleanup(temp.cleanup)
        self.addCleanup(os.chdir, original_cwd)

    def test_separate_config_relative_paths_and_input_fingerprint(self):
        tmp_path = self.tmp_path
        config, base = case(tmp_path / 'nested')
        path = base / 'statistics.json'
        path.write_text(json.dumps(config), encoding='utf-8-sig')
        os.chdir(tmp_path)
        loaded, actual_base = load_statistics_config(path)
        assert loaded == config and actual_base == base.resolve()
        bundle = prepare_statistics(loaded, actual_base)
        assert bundle['input']['sha256'] == hashlib.sha256((base / 'observations.csv').read_bytes()).hexdigest()
        assert bundle['input']['rows'] == 2
        assert bundle['input']['record_unit'] == config['input']['record_unit']
        assert bundle['plots'][0]['transform']['mode'] == 'raw'
        json.dumps(bundle, allow_nan=False)

    def test_numeric_exclusions_have_exact_reasons_and_preserve_raw(self):
        tmp_path = self.tmp_path
        for raw, reason in [('', 'missing'), ('  ', 'missing'), ('True', 'boolean'), ('false', 'boolean'), ('2024-03-12', 'date'), ('2024/03/12', 'date'), ('03/12/2024', 'date'), ('2024-03-12T12:00:00Z', 'date'), ('bad', 'non_numeric'), ('1,000', 'non_numeric'), ('1_000', 'non_numeric'), ('NaN', 'nonfinite'), ('+infinity', 'nonfinite'), ('-Inf', 'nonfinite'), ('1e309', 'nonfinite'), ('1e101', 'magnitude_out_of_range'), ('-1e101', 'magnitude_out_of_range'), ('1e-101', 'magnitude_out_of_range'), ('1e-999', 'magnitude_out_of_range')]:
            with self.subTest(raw=raw, reason=reason):
                config, base = case(tmp_path, [['a', 'A', raw, 2, ''], ['b', 'B', 1, 3, '']])
                bundle = prepare_statistics(config, base)
                plot = bundle['plots'][0]
                assert (plot['n_raw'], plot['n_included'], plot['n_excluded']) == (2, 1, 1)
                assert plot['excluded'][0]['reasons'] == [f'x:{reason}']
                assert plot['excluded'][0]['row_number'] == 2
                assert json.loads(plot['excluded'][0]['raw_record_json'])['x'] == raw
                assert bundle['input_ledger'][0]['raw']['x'] == raw
                json.dumps(bundle, allow_nan=False)

    def test_all_numeric_reasons_recorded_and_plots_use_their_own_complete_cases(self):
        tmp_path = self.tmp_path
        records = [['a', 'A', 1, 2, ''], ['b', 'A', 3, '', ''], ['c', '', 5, 6, ''], ['d', 'B', 'false', '2025-01-01', ''], ['e', 'B', '', 'NaN', '']]
        config, base = case(tmp_path, records, [scatter(), violin(), scatter(id='grouped', group_field='group')])
        a, b, c = prepare_statistics(config, base)['plots']
        assert [r['observation_id'] for r in a['rows']] == ['a', 'c']
        assert [r['observation_id'] for r in b['rows']] == ['a', 'b']
        assert [r['observation_id'] for r in c['rows']] == ['a']
        assert a['excluded'][1]['reasons'] == ['x:boolean', 'y:date']
        assert a['excluded'][2]['reasons'] == ['x:missing', 'y:nonfinite']
        assert b['excluded'][0]['reasons'] == ['group:missing']

    def test_duplicate_missing_ids_and_identical_rows_are_flagged_not_deduplicated(self):
        tmp_path = self.tmp_path
        records = [['repeat', 'A', 1, 2, 'original'], ['repeat', 'A', 1, 2, 'original'], ['', 'A', 1, 2, ''], ['different', 'A', 1, 2, '']]
        config, base = case(tmp_path, records)
        bundle = prepare_statistics(config, base)
        rows = bundle['plots'][0]['rows']
        assert len(rows) == 4
        assert all(('duplicate_id' in r['flags'] for r in rows[:2]))
        assert rows[2]['observation_id'] == '' and rows[2]['flags'] == ['missing_id']
        assert bundle['input']['duplicate_ids'] == {'repeat': 2}
        assert bundle['input']['duplicate_id_rows'] == 2
        assert bundle['input']['missing_id_rows'] == 1

    def test_no_id_mapping_generates_physical_line_ids_and_preserves_multiline_text(self):
        tmp_path = self.tmp_path
        config, base = case(tmp_path, [['unused', 'A', 1, 2, 'first\nsecond'], ['unused', 'A', 2, 3, '']])
        del config['input']['id_field']
        bundle = prepare_statistics(config, base)
        rows = bundle['plots'][0]['rows']
        assert [r['row_number'] for r in rows] == [2, 4]
        assert [r['physical_line_end'] for r in rows] == [3, 4]
        assert [r['observation_id'] for r in rows] == ['row-2', 'row-4']
        assert bundle['input']['generated_ids'] is True
        assert bundle['input']['missing_id_rows'] == 0
        assert bundle['input_ledger'][0]['raw']['other'] == 'first\nsecond'

    def test_zscore_is_population_and_pairwise_complete_before_transform(self):
        tmp_path = self.tmp_path
        config, base = case(tmp_path, [['a', 'A', 1, 10, ''], ['b', 'A', 3, 30, ''], ['c', 'A', 9999, '', '']], [scatter(transform='zscore')])
        original = copy.deepcopy(config)
        plot = prepare_statistics(config, base)['plots'][0]
        assert config == original
        assert [r['x'] for r in plot['rows']] == [-1, 1]
        assert [r['y'] for r in plot['rows']] == [-1, 1]
        assert [r['x_raw'] for r in plot['rows']] == [1, 3]
        assert plot['transform']['ddof'] == 0
        assert plot['transform']['axes']['x']['mean'] == 2
        assert plot['transform']['axes']['x']['std'] == 1
        assert plot['transform']['axes']['x']['unit'] == 'z-score'

    def test_constant_zscore_zero_has_warning_and_no_undefined_correlation_or_fit(self):
        tmp_path = self.tmp_path
        config, base = case(tmp_path, [['a', 'A', 7, 1, ''], ['b', 'A', 7, 2, ''], ['c', 'A', 7, 3, '']], [scatter(transform='zscore', fit='ols', correlation='spearman')])
        plot = prepare_statistics(config, base)['plots'][0]
        assert [r['x'] for r in plot['rows']] == [0, 0, 0]
        assert plot['transform']['axes']['x']['constant'] is True
        assert any(('constant variable' in message for message in plot['warnings']))
        assert plot['statistics']['spearman'] is None
        assert plot['statistics']['ols']['status'] == 'unavailable'
        assert plot['statistics']['ols']['reason'] == 'constant_x'

    def test_spearman_uses_average_tie_ranks_and_ols_is_descriptive(self):
        tmp_path = self.tmp_path
        records = [[str(i), 'A', x, y, ''] for i, (x, y) in enumerate([(1, 1), (2, 3), (2, 2), (4, 4)])]
        config, base = case(tmp_path, records, [scatter(fit='ols', correlation='spearman')])
        statistics = prepare_statistics(config, base)['plots'][0]['statistics']
        expected = np.corrcoef([1, 2.5, 2.5, 4], [1, 3, 2, 4])[0, 1]
        self.assertTrue(math.isclose(statistics['spearman'], expected, rel_tol=1e-12, abs_tol=0.0))
        assert statistics['ols']['status'] == 'ready'
        self.assertTrue(math.isclose(statistics['ols']['slope'], np.polyfit([1, 2, 2, 4], [1, 3, 2, 4], 1)[0], rel_tol=1e-12, abs_tol=0.0))
        assert 'p_value' not in statistics and 'pvalue' not in statistics
        assert set(prepare_statistics({**config, 'plots': [scatter()]}, base)['plots'][0]['statistics']) == {'n'}

    def test_constant_y_has_valid_flat_ols_but_undefined_spearman(self):
        tmp_path = self.tmp_path
        config, base = case(tmp_path, [['a', 'A', 1, 5, ''], ['b', 'A', 2, 5, '']], [scatter(fit='ols', correlation='spearman')])
        stats = prepare_statistics(config, base)['plots'][0]['statistics']
        assert stats['ols'] == {'slope': 0, 'intercept': 5, 'status': 'ready'}
        assert stats['spearman'] is None

    def test_violin_type7_summary_full_range_and_small_group_fallbacks(self):
        tmp_path = self.tmp_path
        records = [[str(i), group, value, 0, ''] for i, (group, value) in enumerate([('B', 1), ('B', 2), ('B', 7), ('B', 10), ('A', 8), ('A', 8), ('C', 4), ('C', 4), ('C', 4), ('D', '')])]
        config, base = case(tmp_path, records, [violin(group_order=['A', 'B', 'C', 'D'])])
        plot = prepare_statistics(config, base)['plots'][0]
        assert plot['groups'] == ['A', 'B', 'C', 'D']
        a, b, c, d = plot['statistics']['groups']
        assert a['n'] == 2 and a['kde_reason'] == 'fewer_than_three_values'
        assert [b[k] for k in ('min', 'q1', 'median', 'q3', 'max')] == [1, 1.75, 4.5, 7.75, 10]
        assert b['kde_status'] == 'ready'
        assert c['kde_status'] == 'fallback' and c['kde_reason'] == 'constant_values'
        assert d['n'] == 0 and d['median'] is None and (d['kde_reason'] == 'no_included_values')

    def test_violin_zscore_uses_all_included_groups_not_per_group(self):
        tmp_path = self.tmp_path
        config, base = case(tmp_path, [['a', 'A', 1, 0, ''], ['b', 'B', 3, 0, ''], ['c', '', 99, 0, '']], [violin(transform='zscore')])
        plot = prepare_statistics(config, base)['plots'][0]
        assert [r['value'] for r in plot['rows']] == [-1, 1]
        assert plot['transform']['axes']['value']['mean'] == 2

    def test_quadrant_threshold_equality_is_high_and_orientation_is_explicit(self):
        tmp_path = self.tmp_path
        records = [[str(i), 'A', x, y, ''] for i, (x, y) in enumerate([(2, 2), (2, 0), (0, 2), (0, 0), (1, 1)])]
        config, base = case(tmp_path, records, [quadrant()])
        plot = prepare_statistics(config, base)['plots'][0]
        assert plot['statistics']['thresholds'] == {'x': 1, 'y': 1}
        assert [r['quadrant'] for r in plot['rows']] == ['Q1', 'Q2', 'Q3', 'Q4', 'Q1']
        assert plot['statistics']['counts'] == {'Q1': 2, 'Q2': 1, 'Q3': 1, 'Q4': 1}
        assert plot['statistics']['equality_rule'] == 'high >= threshold'

    def test_quadrant_mean_and_fixed_thresholds_in_displayed_units(self):
        tmp_path = self.tmp_path
        records = [['a', 'A', 1, 10, ''], ['b', 'A', 3, 30, ''], ['c', 'A', 10000, '', '']]
        config, base = case(tmp_path, records, [quadrant(transform='zscore', thresholds={'x': {'method': 'fixed', 'value': 0}, 'y': {'method': 'mean'}})])
        plot = prepare_statistics(config, base)['plots'][0]
        assert plot['statistics']['thresholds'] == {'x': 0, 'y': 0}
        assert [r['quadrant'] for r in plot['rows']] == ['Q4', 'Q1']
        assert plot['statistics']['threshold_methods'] == {'x': 'fixed', 'y': 'mean'}

    def test_empty_or_all_excluded_data_is_explicit_and_json_finite(self):
        tmp_path = self.tmp_path
        for records in [[], [['a', 'A', '', '', '']]]:
            with self.subTest(records=records):
                config, base = case(tmp_path, records, [scatter(fit='ols', correlation='spearman'), violin(), quadrant()])
                bundle = prepare_statistics(config, base)
                assert all((p['n_included'] == 0 and p['warnings'] for p in bundle['plots']))
                assert bundle['plots'][0]['statistics']['spearman'] is None
                assert bundle['plots'][2]['statistics']['thresholds'] == {'x': None, 'y': None}
                assert bundle['plots'][2]['statistics']['counts'] == dict.fromkeys(['Q1', 'Q2', 'Q3', 'Q4'], 0)
                json.dumps(bundle, allow_nan=False)

    def test_extreme_supported_values_remain_finite_without_clipping(self):
        tmp_path = self.tmp_path
        records = [['a', 'A', '-1e100', '-1e-100', ''], ['b', 'A', '0', '0', ''], ['c', 'A', '1e100', '1e-100', '']]
        config, base = case(tmp_path, records, [scatter(fit='ols', correlation='spearman'), scatter(id='z', transform='zscore')])
        bundle = prepare_statistics(config, base)
        assert bundle['plots'][0]['n_included'] == 3
        assert bundle['plots'][0]['rows'][0]['x'] == -1e+100
        self.assertTrue(math.isclose(bundle['plots'][0]['statistics']['ols']['slope'], 1e-200, rel_tol=1e-12, abs_tol=0.0))
        assert math.isclose(bundle['plots'][1]['rows'][2]['x'], math.sqrt(1.5))
        json.dumps(bundle, allow_nan=False)

    def test_tables_are_complete_reproducible_and_cross_reconcile(self):
        tmp_path = self.tmp_path
        config, base = case(tmp_path, [['a', 'A', 1, 1, '=FORMULA()'], ['b', 'A', 2, 3, ''], ['c', 'A', 'true', '', '']], [scatter(), violin(), quadrant()])
        bundle = prepare_statistics(config, base)
        out = tmp_path / 'out'
        names = write_statistics_tables(out, bundle)
        assert len(names) == 12 and len(set(names)) == len(names)
        assert {p.name for p in out.iterdir()} == set(names)
        snapshots = {name: (out / name).read_bytes() for name in names}
        assert write_statistics_tables(out, bundle) == names
        assert snapshots == {name: (out / name).read_bytes() for name in names}
        for p in bundle['plots']:
            with (out / f"{p['id']}_plot_data.csv").open(encoding='utf-8-sig', newline='') as handle:
                assert len(list(csv.DictReader(handle))) == p['n_included']
            with (out / f"{p['id']}_exclusions.csv").open(encoding='utf-8-sig', newline='') as handle:
                excluded = list(csv.DictReader(handle))
                assert len(excluded) == p['n_excluded']
                assert 'x:boolean' in excluded[0]['reasons'] or 'value:boolean' in excluded[0]['reasons']
            report = json.loads((out / f"{p['id']}_statistics.json").read_text())
            assert report['input']['sha256'] == bundle['input']['sha256']
            assert report['n_raw'] == report['n_included'] + report['n_excluded']
        ledger = list(csv.DictReader((out / 'statistics_input_ledger.csv').read_text(encoding='utf-8-sig').splitlines()))
        assert len(ledger) == 3
        assert json.loads(ledger[0]['raw_record_json'])['other'] == '=FORMULA()'

    def test_invalid_top_level_configs_are_actionable(self):
        tmp_path = self.tmp_path
        for change, match in [({'schema_version': True}, 'schema_version'), ({'schema_version': 2}, 'schema_version'), ({'kind': 'audit'}, 'kind'), ({'synthetic': 'yes'}, 'synthetic'), ({'style': []}, 'style'), ({'style': 'unknown'}, 'style'), ({'language': 'fr'}, 'language'), ({'font_family': 'Arial'}, 'font_family'), ({'font_path': 'missing.ttf'}, 'font_path'), ({'title': []}, 'title'), ({'plots': []}, 'plots'), ({'plots': [scatter()] * 13}, 'plots'), ({'unknown': True}, 'unknown keys'), ({'input': {'path': 'observations.csv'}}, 'record_unit')]:
            with self.subTest(change=change, match=match):
                config, base = case(tmp_path)
                config.update(change)
                with self.assertRaisesRegex(AuditError, match):
                    prepare_statistics(config, base)

    def test_invalid_plot_configs_are_actionable(self):
        tmp_path = self.tmp_path
        for spec, match in [(scatter(id='../escape'), 'safe'), (scatter(id='a/b'), 'safe'), (scatter(id='..'), 'safe'), (scatter(id=''), 'safe'), (scatter(id='x' * 65), 'safe'), (scatter(id='空'), 'safe'), (scatter(type=[]), 'type'), (scatter(transform=True), 'transform'), (scatter(fit=True), 'fit'), (scatter(correlation='pearson'), 'correlation'), (scatter(labels='all'), 'labels'), (scatter(labels=['a', 'a']), 'unique'), (scatter(group_order=['A']), 'requires group_field'), (scatter(x={'field': 'x', 'label': 'X'}), 'unit'), (scatter(y={'field': 'y', 'unit': 'm'}), 'label'), (scatter(x={'field': 'x', 'label': 'X', 'unit': 'm', 'scale': 'log'}), 'unknown keys'), (scatter(trim=0.1), 'unknown keys'), (violin(group_field=None), 'group_field'), (violin(jitter_seed=True), 'jitter_seed'), (violin(jitter_seed=-1), 'jitter_seed'), (violin(jitter_seed=4294967296), 'jitter_seed'), (violin(fit='ols'), 'unknown keys'), (quadrant(thresholds=None), 'thresholds'), (quadrant(thresholds={'x': {'method': 'median'}}), 'thresholds.y'), (quadrant(thresholds={'x': {'method': 'fixed', 'value': True}, 'y': {'method': 'mean'}}), 'finite'), (quadrant(thresholds={'x': {'method': 'fixed', 'value': float('nan')}, 'y': {'method': 'mean'}}), 'finite'), (quadrant(thresholds={'x': {'method': 'fixed'}, 'y': {'method': 'mean'}}), 'finite'), (quadrant(thresholds={'x': {'method': 'median', 'value': 2}, 'y': {'method': 'mean'}}), 'only allowed'), (quadrant(quadrant_labels={'Q5': 'bad'}), 'unknown keys'), (quadrant(group_field='group'), 'unknown keys')]:
            with self.subTest(spec=spec, match=match):
                config, base = case(tmp_path, plots=[spec])
                with self.assertRaisesRegex(AuditError, match):
                    prepare_statistics(config, base)

    def test_duplicate_plot_ids_are_case_insensitively_rejected(self):
        tmp_path = self.tmp_path
        config, base = case(tmp_path, plots=[scatter(id='A'), violin(id='a')])
        with self.assertRaisesRegex(AuditError, 'unique'):
            prepare_statistics(config, base)

    def test_group_order_never_silently_drops_groups(self):
        tmp_path = self.tmp_path
        for order in [['A'], ['A', 'B', 'typo'], ['A', 'A']]:
            with self.subTest(order=order):
                config, base = case(tmp_path, plots=[violin(group_order=order)])
                with self.assertRaisesRegex(AuditError, 'group_order'):
                    prepare_statistics(config, base)

    def test_unknown_label_id_rejected_excluded_label_reported(self):
        tmp_path = self.tmp_path
        config, base = case(tmp_path, [['a', 'A', 1, 1, ''], ['excluded', 'A', '', 1, '']], [scatter(labels=['typo'])])
        with self.assertRaisesRegex(AuditError, 'unknown observation IDs'):
            prepare_statistics(config, base)
        config['plots'][0]['labels'] = ['excluded']
        assert any(('no included rows' in w for w in prepare_statistics(config, base)['plots'][0]['warnings']))

    def test_invalid_or_missing_headers_rejected(self):
        tmp_path = self.tmp_path
        for headers in [['id', 'group', 'x', 'x', 'other'], ['id', 'group', 'x', '', 'other'], ['id', 'group', 'x', 'absent', 'other']]:
            with self.subTest(headers=headers):
                config, base = case(tmp_path, headers=headers)
                with self.assertRaisesRegex(AuditError, 'headers'):
                    prepare_statistics(config, base)

    def test_encoding_errors_are_actionable(self):
        tmp_path = self.tmp_path
        config, base = case(tmp_path)
        config['input']['encoding'] = 'not-an-encoding'
        with self.assertRaisesRegex(AuditError, 'Cannot read'):
            prepare_statistics(config, base)

    def test_malformed_csv_rows_preserve_extra_cells_and_exclude_only_missing_mapped_fields(self):
        tmp_path = self.tmp_path
        config, base = case(tmp_path, [['a', 'A', 1, 2, '', 'extra'], ['b', 'A', 3, 4], ['c', 'A']])
        bundle = prepare_statistics(config, base)
        plot = bundle['plots'][0]
        assert plot['n_included'] == 2 and plot['n_excluded'] == 1
        assert all(('schema_column_count' in r['flags'] for r in plot['rows']))
        assert bundle['input_ledger'][0]['extra_cells'] == ['extra']
        assert plot['excluded'][0]['reasons'] == ['x:missing', 'y:missing']

    def test_blank_physical_record_is_accounted_for(self):
        tmp_path = self.tmp_path
        config, base = case(tmp_path)
        (base / 'observations.csv').write_text('id,group,x,y,other\na,A,1,2,\n\nb,B,2,3,\n', encoding='utf-8')
        bundle = prepare_statistics(config, base)
        assert bundle['input']['rows'] == 3
        assert bundle['plots'][0]['excluded'][0]['row_number'] == 3
        assert bundle['plots'][0]['rows'][1]['row_number'] == 4

    def test_table_writer_rejects_unsafe_mutated_bundle_id(self):
        tmp_path = self.tmp_path
        config, base = case(tmp_path)
        bundle = prepare_statistics(config, base)
        bundle['plots'][0]['id'] = '../escape'
        with self.assertRaisesRegex(AuditError, 'Unsafe'):
            write_statistics_tables(tmp_path / 'out', bundle)

    def test_near_constant_violin_has_numerical_fallback_matching_renderer(self):
        from poi_audit.statistics_render import _kde
        epsilon = np.spacing(1.0)
        for values, expected in [([1, 1 + epsilon, 1 + 2 * epsilon], 'fallback'),
                                 ([1, 1 + 20 * epsilon, 1 + 40 * epsilon], 'ready')]:
            with self.subTest(expected=expected):
                records = [[str(i), 'A', value, 0, ''] for i, value in enumerate(values)]
                config, base = case(self.tmp_path, records, [violin()])
                plot = prepare_statistics(config, base)['plots'][0]
                summary = plot['statistics']['groups'][0]
                _, rendered = _kde([row['value'] for row in plot['rows']])
                self.assertEqual(summary['kde_status'], expected)
                self.assertEqual(summary['kde_status'], rendered['status'])
                if expected == 'fallback':
                    self.assertEqual(summary['kde_reason'], 'numerically_unresolved_variation')
                    self.assertTrue(summary['max'] > summary['min'])
                    self.assertEqual(plot['n_included'], 3)
                    names = write_statistics_tables(self.tmp_path / 'out',
                                                    prepare_statistics(config, base))
                    self.assertIn('violin_group_summary.csv', names)
                    exported = list(csv.DictReader((self.tmp_path / 'out' / 'violin_group_summary.csv').read_text(encoding='utf-8-sig').splitlines()))
                    self.assertEqual(exported[0]['kde_status'], 'fallback')
                    self.assertEqual(exported[0]['kde_reason'], 'numerically_unresolved_variation')

    def test_id_warning_does_not_claim_numerically_invalid_rows_are_included(self):
        records = [['repeat', 'A', 1, 2, ''], ['repeat', 'A', 'bad', 2, ''],
                   ['', 'A', '', 2, '']]
        config, base = case(self.tmp_path, records)
        bundle = prepare_statistics(config, base)
        self.assertEqual(bundle['plots'][0]['n_included'], 1)
        self.assertEqual(bundle['plots'][0]['n_excluded'], 2)
        self.assertTrue(all('none excluded solely for ID flags' in w for w in bundle['warnings']))
        self.assertFalse(any('all retained' in w for w in bundle['warnings']))

if __name__ == '__main__':
    unittest.main()
