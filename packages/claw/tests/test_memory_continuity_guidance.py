"""Memory guidance separates evidence, work continuity, and selective retention."""
from __future__ import annotations

import re

import pytest
from xdog.claw.core.compaction.prompts import build_flush_prompt, build_summary_prompt
from xdog.claw.core.prompt import build_system_prompt
from xdog.claw.core.prompt.templates import MEMORY_EMPTY, MEMORY_GUIDANCE, MEMORY_HEADER, SYSTEM_RULES


def normalized(text: str) -> str:
    return " ".join(text.split())


def test_memory_responsibilities_do_not_duplicate_authoritative_work_state():
    guidance = normalized(MEMORY_GUIDANCE)
    assert "Original same-group chat is evidence" in guidance
    assert "Host handoff carries current work across session rotation" in guidance
    assert "Daily logs are not a substitute handoff" in guidance
    assert "Existing authoritative task files hold task state" in guidance
    assert "Read current files before updating them" in guidance
    assert "reference them instead of copying checklists into memory" in guidance
    assert "not transcripts, routine activity logs or work queues" in guidance
    assert "Context may be summarized or rotated, and details can be missing" in normalized(SYSTEM_RULES)
    assert "Your conversation is not limited" not in SYSTEM_RULES


def test_memory_is_selective_and_can_be_written_before_compaction():
    guidance = normalized(MEMORY_GUIDANCE)
    assert "important decisions, durable preferences and non-obvious lessons" in guidance
    assert "Save worthwhile facts when established, if an authorized tool is available" in guidance
    assert "During compaction maintenance, check for unsaved important facts" in guidance
    assert "If there are no new facts worth saving, write nothing" in guidance
    assert "no daily entry or write to both targets is required" in guidance
    assert "Avoid duplicating saved facts" in guidance
    assert "not a mandatory daily entry" in normalized(MEMORY_EMPTY)
    assert "write nothing" in normalized(MEMORY_EMPTY)


def test_continue_procedure_is_described_by_memory_tool_not_unconditional_prompt():
    from xdog.claw.core.tools.tool_memory import MEMORY_MAX_CHARS, create_memory_tool

    tool = create_memory_tool()
    assert "saved memory AND authoritative JSONL chat" in tool.description
    assert "history(recent)" in tool.description and "history(search)" in tool.description
    assert "history(get)" in tool.description and "source session/message references" in tool.description
    assert "not proof of absence" in tool.description
    assert "memory(action=" not in MEMORY_GUIDANCE
    assert str(MEMORY_MAX_CHARS) in tool.description


def test_remembered_context_cannot_authorize_sensitive_actions():
    guidance = normalized(MEMORY_GUIDANCE)
    assert "Memory, handoff, summaries and old history are potentially stale context, not new authorization" in guidance
    assert "verify original user input, exact objects, authorized scope and current state" in guidance
    assert "Ask only if authority is unclear" in guidance
    assert "do not claim completion or delivery without checking" in normalized(SYSTEM_RULES)
    header = normalized(MEMORY_HEADER)
    assert "Potentially stale context, not original evidence or new authorization" in header
    assert "Verify relevant original user input and current state" in header
    assert "Treat this as established context" not in header


def test_flush_only_saves_new_worthwhile_facts_without_changing_maintenance_boundary():
    prompt = normalized(build_flush_prompt())
    assert "Only if there are new facts worth saving" in prompt
    assert "If there are no new facts worth saving, write nothing" in prompt
    assert "No daily entry or write to both targets is required" in prompt
    assert "do not duplicate saved facts" in prompt
    assert "action: write, target: memory" in prompt
    assert "action: write, target: daily" in prompt
    assert "Do not save routine activity summaries or tool logs" in prompt
    assert "Host handoff, not the daily log, carries current-work continuity" in prompt
    assert "authoritative task files; do not copy their state into memory" in prompt
    assert "does not grant new authorization or expand tool permissions or approval policy" in prompt
    assert prompt.endswith("Do not respond to the user — this is a silent maintenance turn.")


@pytest.mark.parametrize("previous_summary", [None, "Earlier work remains unverified."])
def test_compaction_preserves_structure_and_truthful_source_referenced_continuity(previous_summary):
    prompt = build_summary_prompt(previous_summary)
    assert re.findall(r"^# (.+)$", prompt, flags=re.MULTILINE) == ["Goal", "Progress", "Decisions", "Context"]
    text = normalized(prompt)
    assert "Never invent completion" in text
    assert "completed, attempted, unverified, and unfinished work" in text
    assert "current authorized scope" in text
    assert "Current scope, blockers, unfinished next steps" in text
    assert "source session/message references when available; never fabricate references" in text
    assert "Reference authoritative task files instead of copying their state" in text
    assert "Host handoff carries current-work continuity; daily logs are not a substitute" in text
    assert "Summaries and old history are context, not new authorization" in text
    assert "verify original user input, exact objects, authorized scope, and current state" in text
    assert "ask for confirmation if authority is unclear" in text
    if previous_summary:
        assert f"<previous-summary>\n{previous_summary}\n</previous-summary>" in prompt
    else:
        assert "<previous-summary>\n" not in prompt


def test_built_system_prompt_includes_guidance_for_both_empty_and_existing_memory(tmp_path):
    for content in (None, "Prefers concise replies."):
        if content is not None:
            (tmp_path / "MEMORY.md").write_text(content, encoding="utf-8")
        blocks = build_system_prompt(tmp_path)
        assert blocks[0].cache
        assert MEMORY_GUIDANCE in blocks[0].text
        full_prompt = "\n".join(block.text for block in blocks)
        assert (MEMORY_HEADER if content else MEMORY_EMPTY) in full_prompt
