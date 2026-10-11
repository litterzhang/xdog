"""Command session-inspection/browser requests, never direct session mutation."""

from __future__ import annotations

import re
from typing import Any

from xdog.agent.extensions.catalog import validate_page, validate_request


def validate_session_request(value: dict[str, Any]) -> None:
    if value.get("operation") not in (
            "snapshot", "browse", "navigate", "catalog", "model", "usage", "name", "rename", "commands",
            "active_tools", "select_tools", "compact", "message", "queue_input", "pending_messages", "context_message"):
        raise ValueError("Unsupported command session operation")
    if value['operation'] == 'context_message':
        from xdog.agent.extensions.context_message import validate
        validate(value)
    if value['operation'] == 'pending_messages':
        from xdog.agent.extensions.pending_messages import request as pending_request
        pending_request(value)
    if value['operation'] == 'queue_input':
        from xdog.agent.extensions.queued_input import request as queue_request
        queue_request(value)
    if value['operation'] == 'message':
        from xdog.agent.extensions.message_request import request
        request(value)
    if value['operation'] == 'compact':
        if 'after_run' in value and type(value['after_run']) is not bool:
            raise ValueError('Compaction producer scope must be an exact boolean')
        from xdog.agent.summarization import summary_instructions
        instructions = value.get('instructions')
        if not isinstance(instructions, str) or instructions != summary_instructions(instructions):
            raise ValueError('Compaction instructions must be canonical text')
        if any(key in value for key in ('target', 'session_id', 'branch_id', 'scope', 'index', 'revision')):
            raise ValueError('Compaction cannot select another session/branch')
    if value['operation'] in {'active_tools', 'select_tools'}:
        from xdog.agent.extensions.active_tools import request
        request(value)
    if value['operation'] == 'commands':
        from xdog.agent.extensions.command_catalog import request
        request(value)
    if value['operation'] == 'rename':
        from xdog.agent.session_name import session_name
        for key in ('name', 'expected_name'):
            if value.get(key) != session_name(value.get(key)):
                raise ValueError('Session names must be canonical plain text')
        if any(key in value for key in ('target', 'session_id', 'branch_id', 'scope', 'index')):
            raise ValueError('Naming cannot select another session/branch')
    if value["operation"] == "browse" and value.get("scope") not in ("sessions", "tree"):
        raise ValueError("Command browser scope must be sessions or current tree")
    if value["operation"] == "catalog":
        validate_request(value)
    if value["operation"] == "navigate":
        target = value.get("target")
        if (
            not isinstance(target, dict)
            or set(target) != {"action", "session_id", "branch_id", "index"}
            or target["action"] not in ("new", "resume", "branch", "goto", "fork")
        ):
            raise ValueError("Invalid extension navigation target")
        for key in ("session_id", "branch_id"):
            if (
                not isinstance(target[key], str)
                or len(target[key]) > 128
                or target[key]
                and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", target[key])
            ):
                raise ValueError("Invalid extension navigation identity")
        if type(target["index"]) is not int or not -1 <= target["index"] <= 1000000:
            raise ValueError("Invalid extension checkpoint index")
        action = target["action"]
        if action == "resume" and not target["session_id"]:
            raise ValueError("Resume requires an explicit target session")
        if action == "branch" and not target["branch_id"]:
            raise ValueError("Branch activation requires an explicit branch")
        if action == "goto" and target["index"] < 0:
            raise ValueError("Checkpoint navigation requires a zero-based index")
        if action == "new" and (target["session_id"] or target["branch_id"] or target["index"] != -1):
            raise ValueError("New session cannot substitute a navigation target")
        if action == "resume" and (target["branch_id"] or target["index"] != -1):
            raise ValueError("Resume cannot silently include a branch/checkpoint")
        if action == "branch" and target["index"] != -1:
            raise ValueError("Branch activation cannot silently include a checkpoint")
        if action == "fork" and (target["session_id"] or target["branch_id"]):
            raise ValueError("Fork is scoped to the current session/head")


def validate_session_answer(request: dict[str, Any], value: Any) -> Any:
    if value is None:
        return None
    if request['operation'] == 'active_tools':
        from xdog.agent.extensions.active_tools import answer as active_answer
        return active_answer(value)
    if request['operation'] == 'name':
        from xdog.agent.session_name import session_name
        if (not isinstance(value, dict) or set(value) != {'session_id', 'name'}
                or not isinstance(value['session_id'], str) or len(value['session_id'].encode()) > 256
                or value['name'] != session_name(value['name'])):
            raise ValueError('Invalid session name inspection')
        return dict(value)
    if request['operation'] == 'commands':
        from xdog.agent.extensions.command_catalog import answer
        return answer(request, value)
    if request["operation"] in ("model", "usage"):
        from xdog.agent.extensions.inspection import validate
        return validate(request["operation"], value)
    if request["operation"] == "catalog":
        return validate_page(request, value)
    if request["operation"] == "snapshot":
        if not isinstance(value, dict) or set(value) != {"session_id", "workspace", "group_id"}:
            raise ValueError("Invalid command session snapshot")
        for key, maximum in (("session_id", 256), ("workspace", 8192), ("group_id", 256)):
            if not isinstance(value[key], str) or len(value[key].encode()) > maximum:
                raise ValueError("Invalid command session metadata")
        return dict(value)
    if type(value) is not bool:
        raise ValueError("Invalid command browser receipt")
    return value
