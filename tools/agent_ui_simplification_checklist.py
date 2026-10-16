#!/usr/bin/env python3
"""Check the current simplification ledger's unique scope, exact counts and Markdown status view."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / 'docs/agent-ui-simplification-requirements.json'
VIEW = ROOT / 'docs/agent-ui-simplification-plan.md'
STATUSES = ('待实现', '待验证', '缺外部环境', '已完成', '延期')
EXPECTED = {f'UI-{number:03}' for number in (12, 18, 19, 20, 21, 22, 23, 24, 26, 28, 29,
                                          31, 32, 34, 35, 36, 37, 38, 39, 40, 41, 42, 45, 47, 48)}
EXPECTED.add('PLATFORM-LINUX')


def check() -> dict[str, int]:
    data = json.loads(LEDGER.read_text())
    rows = data['requirements']
    ids = [row['id'] for row in rows]
    if len(ids) != len(set(ids)) or set(ids) != EXPECTED:
        raise ValueError('Scope/ID mismatch: exactly25 approved UI IDs plus PLATFORM-LINUX required')
    view = VIEW.read_text()
    counts: Counter[str] = Counter()
    for row in rows:
        if row['status'] not in STATUSES:
            raise ValueError(f"Unknown status: {row['id']}")
        for field in ('scope', 'acceptance', 'title', 'decision'):
            if not isinstance(row[field], str) or not row[field].strip():
                raise ValueError(f"Missing {field}: {row['id']}")
        if not isinstance(row['evidence'], list):
            raise ValueError(f"Evidence must be a list: {row['id']}")
        if row['status'] == '已完成' and not row['evidence']:
            raise ValueError(f"Completion needs evidence: {row['id']}")
        lines = [line for line in view.splitlines() if line.startswith(f"| {row['id']} |")]
        if len(lines) != 1 or not lines[0].endswith(f"| {row['status']} |"):
            raise ValueError(f"Markdown status missing/stale/duplicated: {row['id']}")
        counts[row['status']] += 1
    result = {'top_level': len(rows), **{status: counts[status] for status in STATUSES}}
    if data['counts'] != result:
        raise ValueError('Declared counts do not match unique requirement states')
    summary = (f"Current categories: **{counts['待实现']} pending implementation, "
               f"{counts['待验证']} pending verification, {counts['缺外部环境']} missing "
               f"external environment, {counts['已完成']} complete, {counts['延期']} deferred**")
    if summary not in ' '.join(view.split()):
        raise ValueError('Markdown current count summary is stale')
    if data['status'] == 'complete' and any(counts[status] for status in STATUSES[:3]):
        raise ValueError('Cannot mark goal complete with unfinished requirements')
    return result


if __name__ == '__main__':
    counts = check()
    print(json.dumps({'remaining': sum(counts[status] for status in STATUSES[:3]), **counts}, ensure_ascii=False))
