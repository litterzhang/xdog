from __future__ import annotations

import pytest
from xdog.agent.supervision_config import ChildConfig, parse_child_start


@pytest.mark.parametrize(
    "bad",
    [
        {"extra": True},
        {"label": "bad\nlabel"},
        {"label": "x" * 121},
        {"instructions": "x" * 16001},
        {"max_model_calls": True},
        {"max_model_calls": 17},
        {"max_tokens": 0},
        {"max_tokens": 32001},
        {"inherit_context": 1},
        {"timeout": float("nan")},
        {"timeout": 601},
        {"timeout": False},
        {"tools": "bash"},
        {"tools": ["bash", "bash"]},
        {"tools": ["../bash"]},
        {"tools": ["agent_spawn"]},
        {"tools": ["agent"]},
    ],
)
def test_configuration_rejects_malformed_or_broader_authority(bad):
    with pytest.raises(ValueError):
        ChildConfig.from_dict(bad)


def test_flags_preserve_task_suffix_and_fresh_subset():
    task, access, config = parse_child_start(
        '--inherit --label "code review" --instructions "Inspect only" '
        "--tools filesystem,current_time --fresh -- "
        'Check "literal quotes"\nand files'
    )
    assert task == 'Check "literal quotes"\nand files' and access == "inherit"
    assert config.label == "code review" and config.instructions == "Inspect only"
    assert config.tools == ("filesystem", "current_time") and not config.inherit_context
    assert parse_child_start('A "quoted" plain task')[0] == 'A "quoted" plain task'
    assert parse_child_start("--tools none task")[2].tools == ()
    assert parse_child_start("-- --unknown literal task")[0] == "--unknown literal task"


def test_invalid_flags_never_admit_child() -> None:

    def _check_case(value):
        with pytest.raises(ValueError):
            parse_child_start(value)

    for value in [
        "--unknown task",
        "--calls bad task",
        "--inherit",
        "--label",
        "--label 'broken",
        "--fresh --fresh task",
        "--tokens 0 task",
        "--tools agent_control task",
        "--tools agent task",
    ]:
        _check_case(value)


def test_historical_preset_snapshot_budgets_are_inert_and_unknown_fields_still_rejected():
    from xdog.agent.supervision_config import ChildPresets

    snapshot = [
        {
            "name": "old",
            "description": "",
            "config": {"timeout": 1, "max_model_calls": 1, "max_tokens": 1, "instructions": "Preserved"},
        }
    ]
    restored = ChildPresets.from_snapshot(snapshot).resolve({"preset": "old"})
    assert restored.instructions == "Preserved" and "max_tokens" not in restored.to_dict()
    with pytest.raises(ValueError):
        ChildConfig.from_record({"timeout": 1, "permission_mode": "allow-all"})
