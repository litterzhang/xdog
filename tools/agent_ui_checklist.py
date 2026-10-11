#!/usr/bin/env python3
"""Validate/count the authoritative requirement ledger and render its Markdown view."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / 'docs/agent-ui-requirements.json'
VIEW = ROOT / 'docs/agent-ui-requirements.md'
STATUSES = ('待实现', '待验证', '缺外部环境', '已完成', '延期')


def render() -> tuple[str, Counter[str]]:
    data = json.loads(LEDGER.read_text())
    rows = data['requirements']
    seen: set[str] = set()
    for row in rows:
        identifier = row['id']
        if not re.fullmatch(r'UI-\d{3}', identifier) or identifier in seen:
            raise ValueError(f'Invalid/duplicate requirement ID: {identifier}')
        seen.add(identifier)
        if row['status'] not in STATUSES:
            raise ValueError(f'Invalid status: {identifier}')
        for field in ('title', 'scope', 'acceptance', 'evidence', 'history'):
            if not isinstance(row[field], str) or not row[field].strip():
                raise ValueError(f'Missing {field}: {identifier}')
        for item in row['evidence'].split('; '):
            if item.startswith('packages/') and not (ROOT / item).exists():
                raise ValueError(f'Missing source evidence path: {identifier}: {item}')
    counts = Counter(row['status'] for row in rows)
    remaining = sum(counts[status] for status in STATUSES[:3])
    lines = [
        '# Agent UI authoritative requirement checklist', '',
        f"Updated: **{data['updated']}**. JSON is authoritative; this Markdown is generated.", '',
        '## Scope and completion conditions', '',
        '- 保留原目标全部已审计适用 UI/能力要求；仅 OS-enforced sandboxing 不在目标内。',
        '- 用户本轮只延期非当前 Linux 环境的**原生验证**；不声称通过、不作为本轮阻塞、不为这些验证继续扩展实现。',
        '- 非 Linux 凭据后端的历史**实现**缺口仍是 UI-045，不能偷换为原生验证延期。',
        '- 当前 Linux 功能、权限安全、生命周期、传输、终端与最终完整回归全部保留。',
        '- 完成条件：除延期项外全部 ID 已完成；缺外部环境仍未完成，不能静默免除。',
        '- 已完成仅指该项命名行为有实现/证据，不代表旧完整回归覆盖当前源码；最终门槛 UI-049/051/053–056/064 独立保留。',
        '- 不承诺无限未知厂商 API/全协议 conformance；较宽历史 OPEN 在对应 ID 下先细化合同，不因未知而悄悄删除。',
        '- 后续发现真实新需求必须追加新 ID；已有范围细化留在原 ID，不重复计数。不回收或重新编号。',
        ('- 不处理 Claw memory search/scheduler 另案；不发布、不重启用户服务、不打断正常推进的测试、不覆盖无关改动。'
         '经身份/根因确认的自有挂起测试及其 peer 可按用户2026-10-10授权优先正常退出、'
         '必要时定向 SIGTERM；不触碰 Codex/无关进程。'), '',
        '## Exact requirement counts', '',
        f'**总计 {len(rows)}；本轮未完成 {remaining}；延期 {counts["延期"]}（另报、非阻塞）。**', '',
        '| 状态 | 需求数 |', '| --- | ---: |',
    ]
    lines.extend(f'| {status} | {counts[status]} |' for status in STATUSES)
    lines += ['', '计数单位是唯一需求 ID，不是实现轮数、测试用例数或历史 OPEN 出现次数。', '',
              '复算/一致性检查：`python tools/agent_ui_checklist.py --check`；更新 JSON 后用 `--write` 生成本文。', '',
              '## Requirements', '',
              '| ID | 状态 | 行为范围 | 可验证验收标准 | 实现/证据入口 | 历史归并 |',
              '| --- | --- | --- | --- | --- | --- |']
    for row in rows:
        values = [row['id'], row['status'], f"**{row['title']}**：{row['scope']}",
                  row['acceptance'], row['evidence'], row['history']]
        lines.append('| ' + ' | '.join(value.replace('|', '\\|').replace('\n', '<br>') for value in values) + ' |')
    lines += ['', '## Historical OPEN reconciliation', '',
              '以下是旧文档到唯一清单的归并规则；历史日期的 OPEN 不是额外需求或当前状态。', '',
              '| 历史条目族 | 当前 ID / 处理 |', '| --- | --- |',
              ('| file/path、keys/editor、queue、session/tree/name、images、transcript/output/git、planning | '
               'UI-001–013；实现已完成，非Linux证据归UI-060/061/063，最终安全/终端归UI-049/064 |'),
              ('| child queue edit/remove/reorder、stopped/initial recovery、image return/Claw '
               'wiring/backup | UI-014–018；passes147–172逐项补齐，不再沿用早期 OPEN |'),
              ('| durable local editor drafts/native wiring/catalog/load/lock/save/discard/RPC child '
               'snapshots | UI-019–020；passes173–180补齐 |'),
              ('| autosave/general parent drafts/save shortcut/preview/modal ownership/deferred quit | '
               'UI-021–024；passes181–193补齐；未结write窗口只计UI-047，remote parent只计UI-040 |'),
              ('| exact consumption/handled journaling | 已有flush后行为UI-017已完成；flush前atomic '
               'boundary只计UI-046，physical loss只计UI-058 |'),
              ('| MCP transport/schema/content/completion/catalog/resource watch/OAuth/storage | '
               '已实现UI-025–029；剩余client capability UI-048、当前SDK验证UI-051、'
               '外部部署UI-057、其他Linux vault UI-050 |'),
              ('| worker/hooks/live tools/commands/dialogs/native '
               'containers/overlay/provider/editor/name/navigation/menu/inspection | 已实现UI-030–034；suffix '
               'UI-035、raw UI-036、renderer UI-037、host UI-043、provider/auth UI-044 |'),
              ('| full RPC source/dialog/editor/navigation/child/queues/recovery | '
               '已实现UI-034/UI-020；canonical/binary UI-038、transient UI-039、parent drafts UI-040、host '
               'scopes UI-041、custom/child-terminal UI-042 |'),
              ('| native credential storage UNIMPLEMENTED before151 | Linux Secret '
               'Service已由UI-029闭合；非Linux实现UI-045保留，原生验证UI-062延期 |'),
              ('| general provider/decoder/credential/clipboard/MCP interoperability | '
               '不无限重复：UI-044/048实现，UI-050/052/057/059缺环境，UI-051当前独立SDK验证 |'),
              ('| Windows/macOS/AppKit/JXA/iTerm/native desktop/native transport/manual platforms | '
               '原生验证UI-060–063延期；Linux UI-049/050/059/064仍保留，不能合并延期 |'),
              ('| pass139/149/169/171 green/red/segmented checkpoints、29 '
               'skips、Ruff/mypy/artifacts/examples | 历史证据非功能数量；当前最终门槛只计UI-054–056及UI-064 |'), '',
              '历史全文仍保留在 `agent-ui-gap-plan.md` / `agent-ui-completion-audit.md`；',
              'README 和 reference-baseline 中早期“remain OPEN”也使用上表解释，不参与额外计数。',
              '基线为已保存的 2026-10-09 资料。本轮网页工具未返回可用内容，因此没有宣称重新核实最新 Codex/Pi。', '']
    return '\n'.join(lines), counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--check', action='store_true')
    action.add_argument('--write', action='store_true')
    args = parser.parse_args()
    text, counts = render()
    if args.write:
        VIEW.write_text(text)
    elif not VIEW.exists() or VIEW.read_text() != text:
        raise SystemExit('Checklist Markdown/counts are stale: run --write')
    print(json.dumps({'total': sum(counts.values()),
                      'remaining': sum(counts[status] for status in STATUSES[:3]),
                      'counts': {status: counts[status] for status in STATUSES}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
