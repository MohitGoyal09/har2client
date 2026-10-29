"""Paired analysis must not turn missing or infrastructure-error cells into gains."""
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('analyze_probe', SCRIPTS / 'analyze_probe.py')
analysis = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = analysis  # dataclasses resolve the defining module through sys.modules
spec.loader.exec_module(analysis)


def rows():
    return [dict(task=task, run='1', har_mode='raw', arm=arm, effort=effort,
                 hidden_header='on', status='ok', pass_rate=str(score))
            for task in ('a', 'b') for effort in ('low', 'high')
            for arm, score in [('placebo', .5), ('P5', .75), ('P20', 1.0)]]


def test_missing_cell_excludes_only_that_effort_not_other_efforts():
    data = rows()
    coverage = analysis.complete_blocks(data[:-1])  # b/high/P20 is missing
    assert coverage.efforts == ['low', 'high']
    assert coverage.observed_blocks == 2
    assert coverage.complete_by_effort == {'low': 2, 'high': 1}
    assert coverage.common_blocks == 1
    assert len(coverage.rows) == 9 and {r['task'] for r in coverage.rows} == {'a', 'b'}
    assert {r['effort'] for r in coverage.rows if r['task'] == 'b'} == {'low'}
    assert {r['task'] for r in coverage.common_rows} == {'a'}


@pytest.mark.parametrize('status', ['api_error', 'grade_error'])
def test_infrastructure_error_is_excluded_only_for_that_effort(status):
    data = rows()
    data[-1]['status'] = status
    coverage = analysis.complete_blocks(data)
    assert coverage.complete_by_effort == {'low': 2, 'high': 1}
    assert coverage.common_blocks == 1


def test_no_submit_is_a_model_failure_and_remains_in_analysis():
    data = rows()
    data[-1].update(status='no_submit', pass_rate='0')
    coverage = analysis.complete_blocks(data)
    assert coverage.complete_by_effort == {'low': 2, 'high': 2}
    assert coverage.common_blocks == 2


def test_incomplete_exploratory_effort_does_not_drop_high_effort_comparison():
    data = rows()
    data.append(dict(task='a', run='1', har_mode='raw', arm='placebo', effort='xhigh',
                     hidden_header='on', status='ok', pass_rate='1'))
    coverage = analysis.complete_blocks(data)
    assert coverage.complete_by_effort == {'low': 2, 'high': 2, 'xhigh': 0}
    assert coverage.frontier_efforts == ['low', 'high']
    assert coverage.common_blocks == 2 and {r['task'] for r in coverage.common_rows} == {'a', 'b'}
    comparisons = analysis.compare(coverage.rows, coverage.efforts, resamples=100)
    high = [c for c in comparisons if c['effort'] == 'high' and c['hidden_header'] == 'all']
    assert high and all(c['tasks'] == 2 for c in high)


def test_frontier_intersects_tasks_at_every_effort_with_a_complete_block():
    data = rows()
    for arm, score in [('placebo', .5), ('P5', .75), ('P20', 1.0)]:
        data.append(dict(task='a', run='1', har_mode='raw', arm=arm, effort='xhigh',
                         hidden_header='on', status='ok', pass_rate=str(score)))
    coverage = analysis.complete_blocks(data)
    assert coverage.complete_by_effort == {'low': 2, 'high': 2, 'xhigh': 1}
    assert coverage.frontier_efforts == ['low', 'high', 'xhigh']
    assert coverage.common_blocks == 1
    assert {r['task'] for r in coverage.common_rows} == {'a'}
    assert {r['effort'] for r in coverage.common_rows} == {'low', 'high', 'xhigh'}


def test_paired_effect_and_bootstrap_are_in_percentage_points():
    result = analysis.compare(rows(), ['high'], resamples=100)
    c = next(c for c in result if c['arm'] == 'P20' and c['hidden_header'] == 'all')
    assert c['delta_pp'] == 50 and c['ci95_pp'] == [50, 50] and c['tasks'] == 2


def test_duplicate_cells_fail_instead_of_silently_overwriting():
    with pytest.raises(ValueError, match='duplicate'):
        analysis.complete_blocks(rows() + rows()[:1])


def test_dry_run_data_cannot_be_reported_as_beam_results(tmp_path):
    import csv
    import json

    source = tmp_path / 'results.csv'
    with source.open('w', newline='') as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows()[0]))
        writer.writeheader()
        writer.writerows(rows())
    (tmp_path / 'run_manifest.json').write_text(json.dumps({'dry_run': True}))
    with pytest.raises(ValueError, match='synthetic dry-run'):
        analysis.analyze(source, tmp_path / 'analysis')
