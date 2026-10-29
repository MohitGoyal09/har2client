import importlib.util
from pathlib import Path

path = Path(__file__).resolve().parents[1] / 'scripts/audit_probe_run.py'
spec = importlib.util.spec_from_file_location('audit_probe_run', path)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def example():
    grade = dict(status='ok', total=2, passed=1, pass_rate=.5, strict_pass_rate=.5,
                 results=[dict(passed=True, strict_passed=True), dict(passed=False, strict_passed=False)])
    log = dict(status='ok', probe_budget=5, probes_used=1, probe_labels={'other': 1}, turns=[],
               usage=dict(completion_tokens=100, reasoning_tokens=50))
    return grade, log


def test_valid_model_failure_does_not_count_as_integrity_failure():
    grade, log = example()
    assert audit.check_cell(grade, log, 'P5') == []


def test_wrong_score_is_detected():
    grade, log = example()
    grade['pass_rate'] = 1
    assert any('pass score' in issue for issue in audit.check_cell(grade, log, 'P5'))


def test_probe_budget_and_labels_are_checked():
    grade, log = example()
    log['probes_used'] = 6
    issues = audit.check_cell(grade, log, 'P5')
    assert any('budget' in issue for issue in issues)
    assert any('label' in issue for issue in issues)


def test_placebo_http_attempt_is_detected_even_when_no_probe_was_sent():
    grade, log = example()
    log.update(probe_budget=0, probes_used=0, probe_labels={},
               turns=[dict(tool_calls=[dict(name='http_request')])])
    assert any('HTTP' in issue for issue in audit.check_cell(grade, log, 'placebo'))


def test_double_counted_reasoning_is_detected():
    grade, log = example()
    log['usage']['reasoning_tokens'] = 101
    assert any('token' in issue for issue in audit.check_cell(grade, log, 'P5'))
