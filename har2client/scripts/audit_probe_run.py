"""Read-only integrity checks for a Case 2 run, including partial checkpoints.

Writes only to --out. Never calls a model, grades a client, or edits run files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ARMS = {'placebo': 0, 'P5': 5, 'P20': 20}
EFFORTS = ('low', 'medium', 'high')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def runtime_hash(runtime):
    digest = hashlib.sha256()
    files = sorted(p for directory in ('src', 'scripts', 'tasks') for p in (runtime / directory).rglob('*')
                   if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
    for path in files:
        digest.update(str(path.relative_to(runtime)).replace('\\', '/').encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def check_cell(grade, log, arm):
    issues = []
    budget = ARMS[arm]
    used = log.get('probes_used', 0)
    if log.get('probe_budget') != budget or not 0 <= used <= budget:
        issues.append('probe budget mismatch or exceeded')
    labels = log.get('probe_labels') or {}
    if sum(labels.values()) != used:
        issues.append('probe-label counts differ from probes used')
    if arm == 'placebo' and any(call.get('name') == 'http_request'
                                for turn in log.get('turns', []) for call in turn.get('tool_calls', [])):
        issues.append('HTTP tool call attempted in placebo')
    if grade.get('status') == 'ok':
        results = grade.get('results', [])
        if not results or len(results) != grade.get('total'):
            issues.append('hidden-test count mismatch')
        else:
            passed = sum(bool(r.get('passed')) for r in results)
            strict = sum(bool(r.get('strict_passed')) for r in results)
            if passed != grade.get('passed') or abs(passed / len(results) - grade.get('pass_rate', -1)) > 1e-9:
                issues.append('pass score inconsistent with per-test outcomes')
            if abs(strict / len(results) - grade.get('strict_pass_rate', -1)) > 1e-9:
                issues.append('strict score inconsistent with per-test outcomes')
        if log.get('status') != 'ok':
            issues.append('grade exists for unsuccessful submission')
    usage = log.get('usage') or {}
    output, reasoning = usage.get('completion_tokens'), usage.get('reasoning_tokens')
    if output is not None and reasoning is not None and not 0 <= reasoning <= output:
        issues.append('invalid output/reasoning token accounting')
    return issues


def audit(run_dir, out_dir):
    run_dir, out_dir = Path(run_dir), Path(out_dir)
    protocol = read_json(run_dir / 'protocol.json')
    runtime = Path(protocol['runtime'])
    tasks = sorted(d.name for d in (runtime / 'tasks/tierb50').iterdir() if (d / 'task.json').exists())
    expected = {f'{task}/{arm}-{effort}-raw-r1' for task in tasks for arm in ARMS for effort in EFFORTS}
    observed, issues, unreadable = set(), [], []
    statuses, failures = Counter(), Counter()
    checked_probes = 0
    latest, latest_time = {}, -1
    for grade_path in sorted(run_dir.glob('*/*/grade.json')):
        cell = grade_path.parent.relative_to(run_dir).as_posix()
        try:
            grade = read_json(grade_path)
            log_path = grade_path.parent / 'solve_log.json'
            log = read_json(log_path)
        except (OSError, ValueError) as exc:
            unreadable.append({'cell': cell, 'error_type': type(exc).__name__})
            continue  # a file may be in the middle of a write; never call it a score
        observed.add(cell)
        arm = grade_path.parent.name.split('-')[0]
        if arm not in ARMS:
            issues.append({'cell': cell, 'issues': ['unexpected arm']})
            continue
        errors = check_cell(grade, log, arm)
        if log.get('protocol') != protocol['protocol']:
            errors.append('submission protocol mismatch')
        if errors:
            issues.append({'cell': cell, 'issues': errors})
        statuses[grade.get('status', 'unknown')] += 1
        failures.update(grade.get('failure_counts') or {})
        checked_probes += log.get('probes_used', 0)
        mtime = log_path.stat().st_mtime
        if mtime > latest_time:
            latest_time, latest = mtime, log.get('rate_limit_headers') or {}
    frozen_hash_matches = runtime_hash(runtime) == protocol['runtime_sha256']
    if not frozen_hash_matches:
        issues.append({'cell': 'runtime', 'issues': ['frozen source/task hash mismatch']})
    missing, unexpected = sorted(expected - observed), sorted(observed - expected)
    infrastructure = statuses.get('api_error', 0) + statuses.get('grade_error', 0)
    report = dict(checked_utc=datetime.now(timezone.utc).isoformat(), expected_cells=len(expected),
                  observed_cells=len(observed), complete=not missing and not unexpected and not unreadable,
                  frozen_hash_matches=frozen_hash_matches, statuses=dict(statuses),
                  infrastructure_errors=infrastructure, integrity_issues=issues,
                  unreadable_cells=unreadable, missing_cells=missing, unexpected_cells=unexpected,
                  probes_checked=checked_probes, test_failure_counts=dict(failures), latest_quota=latest)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'audit.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    lines = ['# Case 2 integrity audit', '',
             f"Coverage: {len(observed)}/{len(expected)} cells. Complete: {report['complete']}.",
             f'Frozen source/task hash matches: {frozen_hash_matches}.',
             f'Integrity issues: {len(issues)}. Infrastructure errors: {infrastructure}.',
             f'Probes checked: {checked_probes}. Unreadable/in-progress cells: {len(unreadable)}.', '',
             'Checks: probe budgets and labels, no HTTP calls in placebo, per-test score consistency,',
             'submission protocol, token accounting, expected cell coverage, and frozen runtime hash.', '',
             'Model test failures (including client timeouts) are distinct from infrastructure errors.',
             'This is an integrity check, not statistical verification or a final benchmark claim.']
    for issue in issues:
        lines.append(f"- {issue['cell']}: {', '.join(issue['issues'])}")
    (out_dir / 'AUDIT.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    report = audit(args.run, args.out)
    print(f"Checked {report['observed_cells']}/{report['expected_cells']} cells; "
          f"integrity issues={len(report['integrity_issues'])}; infrastructure errors={report['infrastructure_errors']}")
    return 1 if report['integrity_issues'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
