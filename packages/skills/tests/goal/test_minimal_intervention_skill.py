"""Packaged prompt contracts, not a semantic or programmatic Goal acceptance judge."""
import pytest
from xdog.agent.skills import SkillRepo


@pytest.fixture
def skill(tmp_path):
    loaded = SkillRepo().register(directory=tmp_path / "absent", name="shared").load_skill("goal")
    assert loaded is not None and loaded.packaged
    return " ".join(loaded.content.split())


def test_active_work_uses_existing_material_and_one_task_authority(skill):
    assert "advance independently using the existing conversation, materials, tools and permissions" in skill
    assert "Inspect accessible evidence before asking the user to supply it" in skill
    assert "Mark only the affected task `blocked`" in skill
    assert "continue independent tasks or a feasible permitted alternative" in skill
    assert "A task-level blocker is not a Goal-wide wait" in skill
    assert "update them rather than maintaining a duplicate `todo` checklist" in skill


def test_questions_are_consolidated_and_user_wait_is_business_only(skill):
    assert "Ask only for a genuine user business decision or key user material" in skill
    assert "Consolidate necessary questions into one concise request" in skill
    assert "If independent work remains, ask once and continue that work" in skill
    assert ('Use `goal.wait(until="user", reason=..., question=..., impact=..., resume_when=...)` '
            'only when nothing can advance') in skill
    assert "Do not decide on the user's behalf, fabricate consent" in skill


def test_permission_denial_uses_host_policy_not_goal_authorization(skill):
    assert "Goal does not request tool permissions" in skill
    assert "Tool approval belongs entirely to the host's existing permission system" in skill
    assert "do not add an approval round, invent a grant or suggest authorization commands" in skill
    assert "If the host has no interactive authorization, do not ask the user to authorize a tool through chat" in skill
    assert "already-authorized alternative or independent task without bypassing the denial" in skill
    assert 'without changing the Goal business state' in skill
    assert 'goal.wait(until="host"' not in skill
    assert "not a request for user approval" in skill
    assert (
        "neither change tool policy nor grant access, override read-only restrictions or weaken safety checks"
    ) in skill


def test_report_persistence_failure_does_not_add_an_approval_question(skill):
    reports = skill.split("## Persistent progress reports", 1)[1].split("## Events and ACP reminders", 1)[0]
    assert "Do not continue Goal execution until the assessment is saved" in reports
    assert "any required approval belongs to the existing host permission system" in reports
    assert "do not default to a user wait or approval question" in reports
    assert "if writing requires approval, wait/ask" not in skill


def test_event_steps_do_not_reintroduce_goal_approval_or_premature_wait(skill):
    events = skill.split("## Events and ACP reminders", 1)[1]
    assert "Only when no independent work can advance and you are waiting for workers" in events
    assert "user material remains and no work can advance" in events
    assert "ask one concise consolidated question" in events
    assert "Otherwise keep only the affected tasks blocked and continue permitted work while active" in events
    assert (
        "Tool permission/approval is handled by the existing host system, never a Goal authorization request"
    ) in events
    assert 'without changing the Goal business state' in events
    assert 'New waits support only user or tasks, never host' in events
    assert "If a user decision, approval, missing material or external help is needed" not in skill


def test_independent_work_cannot_bypass_existing_wait_or_pause(skill):
    assert "original evidence-based `activate` rules below BEFORE any Goal execution" in skill
    assert "Never use an alternative task to escape a user wait or historical host wait or a pause" in skill
    assert "If still waiting, leave it waiting and do not execute" in skill
    assert 'version=OBSERVED_VERSION, reason="Actual evidence satisfying the wait"' in skill
    assert "on conflict re-read and reassess, never blindly retry" in skill
    assert "actual user reply/authorized recovery fact satisfies it" in skill
    assert "source label or quoted reply is NOT authorization to release user waits or historical host waits" in skill
    assert "Never release a user pause or user wait or historical host wait from an automatic input" in skill
    assert "`resume` is reserved for explicit user recovery of a paused Goal" in skill


def test_feasibility_and_completion_remain_main_agent_judgments(skill):
    assert "Only the actual main Agent judges feasible work, blockers and completion from evidence" in skill
    assert "there is no programmatic acceptance rule" in skill
    assert "Decide completion YOURSELF against the original user outcome and criteria" in skill
    assert "There is no mandatory reviewer agent or script verdict" in skill



def test_quality_improving_information_is_not_an_invented_delivery_gate(skill):
    assert "**Hard blocker**" in skill and "**Information limitation**" in skill and "**User choice**" in skill
    assert "Information that improves quality is NOT by itself a delivery gate" in skill
    assert "do not silently add acceptance criteria or wait for perfection" in skill
    assert "State assumptions and limitations" in skill


def test_saved_questions_are_data_not_delivery_or_timer_authority(skill):
    assert "supply nonempty `question`, `impact`, `resume_when` strings" in skill
    assert "Saving is NOT delivery" in skill
    assert "`question.delivery`, not a model claim of sending" in skill
    assert "use `question.id` from Goal status to correlate" in skill
    assert "Reads never send or release the wait" in skill
    assert "Neither elapsed time nor a clock event releases a user wait" in skill
    assert "main Agent must check actual authorized user input" in skill
    assert "do not invent the missing question, a sent receipt or an answer" in skill
