"""Read-only estimates of a prepared request, with no IO or context rewriting."""
from __future__ import annotations

from dataclasses import dataclass, replace

from xdog.agent.compaction import CompactionBudget, estimate_input_message_tokens, estimate_input_tokens
from xdog.ai.types import Context, ImageContent, StreamOptions, system_prompt_text


@dataclass(frozen=True, slots=True)
class ContextReport:
    """Finite counts only: no prompt, credentials, tool callbacks or mutable request."""

    model: str
    estimated_tokens: int
    system_tokens: int
    tool_schema_tokens: int
    history_tokens: int
    image_tokens: int
    request_overhead_tokens: int
    message_count: int
    tool_count: int
    input_limit: int | None = None
    output_reserve: int | None = None
    estimate_method: str = 'utf8-size heuristic; not a provider tokenizer'

    @property
    def remaining_tokens(self) -> int | None:
        return None if self.input_limit is None else self.input_limit - self.estimated_tokens

    @property
    def fits(self) -> bool | None:
        return None if self.input_limit is None else self.estimated_tokens <= self.input_limit

    def with_budget(self, budget: CompactionBudget) -> ContextReport:
        return replace(self, input_limit=budget.input_limit, output_reserve=budget.output_reserve)


def inspect_context(context: Context, options: StreamOptions = StreamOptions(), *, model: str = '',
                    budget: CompactionBudget | None = None) -> ContextReport:
    """Reuse compaction's estimator on the actual prepared request.

    System includes any placed skills. Arbitrary host prompt strings cannot be
    reliably split into skills versus instructions; no attribution is invented.
    Unknown capacity stays unknown. No model discovery or tokenization service runs.
    """
    def text_tokens(value: str) -> int:
        return (len(value.encode('utf-8')) + 3) // 4
    system = text_tokens(system_prompt_text(context.system_prompt) or '')
    if isinstance(context.system_prompt, tuple):
        system += 4 * len(context.system_prompt)
    empty = estimate_input_tokens(Context(messages=(), system_prompt=context.system_prompt), options)
    schemas = estimate_input_tokens(Context(messages=(), system_prompt=context.system_prompt, tools=context.tools),
                                    options) - empty
    messages = sum(estimate_input_message_tokens(message) for message in context.messages)
    images = sum(max(200, text_tokens(part.data)) + text_tokens(part.mime_type)
                 for message in context.messages if not isinstance(message.content, str)
                 for part in message.content if isinstance(part, ImageContent))
    report = ContextReport(model, estimate_input_tokens(context, options), system, schemas, messages - images,
                           images, empty - system, len(context.messages), len(context.tools or ()))
    return report.with_budget(budget) if budget is not None else report
