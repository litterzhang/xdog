"""Pure preflight using declared model facts. Unknown is never false."""
from __future__ import annotations

from xdog.ai.failures import ModelCapabilityError
from xdog.ai.types import Context, ImageContent, Model, StreamOptions


def validate_model_request(model: Model, context: Context, options: StreamOptions) -> None:
    """Reject explicit contradictions without auth, I/O, fallback or mutation.

    Capacity estimation stays with Agent/host budgets. An unknown capability may
    be attempted; this function never invents model support or adapter support.
    Streaming transport is checked by adapters that actually require streaming;
    e.g. Antigravity can satisfy the event API with a non-streaming response.
    """
    if context.tools and model.supports_tool_calls is False:
        raise ModelCapabilityError('tool_calls')
    if options.parallel_tool_calls is True and model.supports_parallel_tool_calls is False:
        raise ModelCapabilityError('parallel_tool_calls')
    if options.response_format is not None and model.supports_structured_outputs is False:
        raise ModelCapabilityError('structured_outputs')
    if options.web_search and model.supports_web_search is False:
        raise ModelCapabilityError('web_search')
    if model.supports_image_input is False and any(
        isinstance(part, ImageContent)
        for message in context.messages if not isinstance(message.content, str)
        for part in message.content
    ):
        raise ModelCapabilityError('image_input')
