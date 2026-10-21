"""Unpublished application resource acquisition; never constructs or runs an Agent."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.orchestrator import Orchestrator


@dataclass(frozen=True)
class BuiltApplication:
    application: Orchestrator
    error: BaseException | None = None


def build(config: ClawConfig) -> BuiltApplication:
    from xdog.claw.core.runtime.gateway import _build_model_and_options, _groups_from_config
    model, stream = _build_model_and_options(config)
    app = Orchestrator(config, model=model, stream_fn=stream, data_dir=Path(config.data_dir),
                       defer_inputs=True)
    try:
        for group in _groups_from_config(config):
            app.register_group(group)
    except BaseException as exc:
        # Transfer partial, unpublished resources to the real owner for async
        # settlement; worker failure must not lose ownership of earlier groups.
        return BuiltApplication(app, exc)
    return BuiltApplication(app)
