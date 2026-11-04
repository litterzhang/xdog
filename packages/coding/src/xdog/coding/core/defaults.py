"""Default constants used throughout the coding agent."""

from __future__ import annotations

# --- Model defaults ---
# No hardcoded default model. The first model in the ai catalog is used
# when no model is configured. Set via GlobalConfig.default_model or CLI --model.
DEFAULT_MODEL: str | None = None
DEFAULT_THINKING_LEVEL = "medium"

# --- Token / context limits ---
MAX_CONTEXT_TOKENS = 200_000
COMPACTION_THRESHOLD_RATIO = 0.80  # compact when context hits 80%
COMPACTION_TARGET_RATIO = 0.50     # target 50% after compaction

# --- Bash defaults ---
DEFAULT_BASH_TIMEOUT_MS = 120_000  # 2 minutes
MAX_BASH_TIMEOUT_MS = 600_000      # 10 minutes

# --- Read defaults ---
DEFAULT_READ_LIMIT = 2000          # max lines per read
MAX_LINE_LENGTH = 2000             # truncate lines longer than this

# --- Tool output ---
MAX_TOOL_OUTPUT_CHARS = 30_000     # truncate tool output beyond this

# --- Session ---
SESSION_FILE_PREFIX = "session_"
SESSION_FILE_SUFFIX = ".json"

# --- Extensions ---
EXTENSION_MANIFEST = "extension.yaml"

# --- Timing ---
TIMING_ENABLED = False
