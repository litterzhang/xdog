# Claw/Eyes ACP verification — 2026-10-16

Implementation is in the original checkout on `feat/claw-eyes-acp`, based on
`e9f27f66f87808c8f9cd25f452f9cab3a4a3f8d5`. Verification below was performed
before the feature commit/push. Independent review remains deferred at the user's
request, not reported as passed. Design, usage and limits: [Claw/Eyes ACP](claw-eyes-acp.md).

## Final source gates

`uv run python scripts/test.py` completed all eight configured test paths in
86.81s on Python 3.13.9. Final log: `/tmp/claw-eyes-verified-tests.log`.

| Package | Passed | Skipped |
|---|---:|---:|
| flow | 813 | 0 |
| agent | 1785 | 48 |
| claw | 612 | 0 |
| ai | 688 | 7 |
| eyes | 186 | 0 |
| site | 42 | 0 |
| tui | 919 | 0 |
| coding | 1279 | 12 |
| **Total** | **6324** | **67** |

The 67 skips belong to existing optional MCP SDK/external-CLI coverage. None of
the 29 new Claw ACP tests is skipped. `uv run pytest packages/claw/tests/test_acp*
-q -n 0` independently passed all29 in8.45s. Ruff over all packages, strict mypy
over all eight namespaces (632 source files), `git diff --check`, and all17 shipped
Flow example cases passed.

Permanent regressions cover missing intake implementation, canonical persistence
and actual Agent execution, busy-group result isolation, transport cancellation,
background concurrency/human priority, pending recovery/interrupted non-replay,
ID conflict/deduplication, capacity/lease/private paths, terminal-commit shutdown
races, exact group/workspace validation, disabled tools, bounded response chunks,
optional SDK import isolation, config defaults/round-trip, endpoint alias rejection,
gateway startup/shutdown, TUI input/result deduplication and preserved drafts/history.
A real file edit also traveled through the unmodified Eyes source/detector/router/
ACP handler into Claw's actual Orchestrator/AgentSession/transcript.

Initial full-gate failures exposed an undeclared direct SDK dependency and empty
ACP paths incorrectly expanded to `.` on config reload. Both were fixed, with no
deselection or test suppression, before the final full green run. The cancellation
regression also caught terminal commits being mistaken for interrupted work; the
final implementation reads the settled state rather than overwriting it.

## Built/installed verification

Four wheels and four sdists (ai, agent, tui, claw) passed Twine validation. Claw was
rebuilt after the final queue-policy change. Distribution directory:
`/tmp/xdog-claw-acp-dist.wYPK2H`; source parity manifest: `source-parity.json` there.
Every Python member in all8 archives was byte-compared to the final checkout.

A wheel-only venv outside the checkout installed the four distributions and their
declared dependencies. Running `/tmp/claw_acp_installed_probe.py` with `python -I`
from `/tmp` proved:

- Eyes, coding and flow are not installed/imported.
- Official ACP SDK0.12.1 initializes over a real private Unix endpoint.
- Enqueue → durable group inbox → existing AgentSession executes once and succeeds.
- Canonical message ID/provenance persists in the JSONL transcript.
- Repeating the original ID/content returns duplicate without another model call.
- Enqueue does not broadcast into an unrelated ACP peer's prompt stream.

| Artifact | Python members matched | SHA-256 |
|---|---:|---|
| `xdog_agent-2.4.1-py3-none-any.whl` | 138 | `fec36f4ad8dd9b6b4ff4d0697d46a19f53a58128bf4ea6fda61cfdfa80c2efb5` |
| `xdog_agent-2.4.1.tar.gz` | 349 | `9d79bc57318c5d005f0068d2b443f90914e109ce0dd7098c4a6814a50ab2a25e` |
| `xdog_ai-2.4.1-py3-none-any.whl` | 50 | `a4c9e41315478352974674d5c0003b73da592e9f30a8d6a76f58180b92725c43` |
| `xdog_ai-2.4.1.tar.gz` | 91 | `291ca06afa9c3735bdc130d106598a78714295baabcf800e1890728b36a78b33` |
| `xdog_claw-2.4.1-py3-none-any.whl` | 90 | `1fa3d459f1dba58b681908d42e1cae78804372b48acbc9fb2278fa86d6e868f6` |
| `xdog_claw-2.4.1.tar.gz` | 173 | `0498be7fa44be559d40fa49979f67d3b1f75d48b3034552f8638fc397f9cbc7e` |
| `xdog_tui-2.4.1-py3-none-any.whl` | 116 | `3fdb127c9577f121ca2d15592317bdedd083cee2b5dde5119b656f08379d7b4c` |
| `xdog_tui-2.4.1.tar.gz` | 203 | `a06a6495b8241bee9f59cb1cc60056f97c7206850263a12548d864cacfd0f8a7` |

## Explicit limits

No paid model/provider, external IDE, TCP/Windows transport, stdio gateway adapter,
installed service, separate offline receiver, Claw batched-turn policy or automatic
interrupted retry is claimed. Gateway must be running for new intake; TUI need
not be. Group addressing intentionally follows its active conversation rather
than pinning a coding-style exact session/branch. Live TUI notices are completion-
level; replay uses the normal persisted transcript. No exactly-once tool-side-effect
guarantee or independent-review verdict is claimed.
