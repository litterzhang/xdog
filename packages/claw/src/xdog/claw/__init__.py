"""claw — AI agent orchestration layer."""
from xdog.claw.channels.base import Channel
from xdog.claw.channels.tui.channel import TuiChannel
from xdog.claw.config import ClawConfig, GroupDef, load_config, save_config
from xdog.claw.core.chunker import BlockChunker
from xdog.claw.core.persistence.transcript_store import SessionManager, TranscriptStore
from xdog.claw.core.prompt import build_system_prompt, init_workspace, run_bootstrap, workspace_path
from xdog.claw.core.queue import MessageQueue
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.runtime.session import AgentSession, TurnResult
from xdog.claw.core.types import (
    Group,
    GroupConfig,
    GroupInput,
    QueueMode,
    SessionMeta,
    SessionState,
    SystemInput,
    SystemInputKind,
    UserInput,
)
