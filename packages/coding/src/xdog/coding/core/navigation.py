"""Read-only native navigation entries shared by Coding terminal clients."""
from __future__ import annotations

from xdog.agent.history import history_points
from xdog.coding.core.agent_session import AgentSession
from xdog.tui.navigation import NavigationEntry


def navigation_entries(session: AgentSession, scope: str) -> tuple[NavigationEntry, ...]:
    if scope == "sessions":
        return tuple(NavigationEntry(
            meta.session_id, meta.name or meta.summary or meta.session_id,
            f"{meta.model} · {meta.message_count} messages · {meta.working_dir}",
            session_id=meta.session_id, active=meta.session_id == session.session_id,
        ) for meta in session.session_manager.list_sessions(limit=None))
    parts = scope.split("|")
    if len(parts) != 3 or parts[0] != "tree":
        raise ValueError("Invalid navigation scope")
    session_id, branch_id = parts[1:]
    saved = session.session_manager.load_session(session_id)
    if saved is None:
        raise ValueError("Session unavailable")
    from xdog.coding.core.messages import dicts_to_messages
    messages = saved.messages
    if branch_id:
        branch = next((value for value in saved.branches if value.get("branch_id") == branch_id), None)
        if branch is None:
            raise ValueError("Branch unavailable")
        messages = dicts_to_messages(branch["messages"])
    entries = []
    if not branch_id:
        parents = {value["branch_id"]: value.get("parent_branch", "") for value in saved.branches}
        for value in saved.branches:
            key, parent, depth = value["branch_id"], value.get("parent_branch"), 0
            seen = {key}
            while parent and parent in parents and parent not in seen:
                depth += 1
                seen.add(parent)
                parent = parents[parent]
            entries.append(NavigationEntry(
                f"b:{key}", value.get("label") or key, f"{len(value.get('messages', []))} messages",
                "branch", session_id, key, depth=depth, active=key == saved.active_branch,
            ))
    entries.extend(NavigationEntry(
        f"p:{branch_id}:{point.index}", f"{point.index + 1}. {point.role}: {point.preview}",
        "Navigate here with a retained return checkpoint", "checkpoint", session_id, branch_id, point.index,
    ) for point in history_points(messages))
    return tuple(entries)

