"""Exercise both the host runtime bridge and generic host-loop dispatch."""

import asyncio
import threading
from types import SimpleNamespace

import pytest
from xdog.agent.cross_loop import settle_on


@pytest.fixture(params=["runtime", "host"])
async def harness(request, runtime_factory, execution_owner):
    owner = runtime_factory(lambda *args: pytest.fail("Dispatch must not construct an Agent"))

    async def identity():
        return asyncio.get_running_loop(), threading.get_ident()

    # Bootstrap through the actual bridge: Claw binds an existing application
    # loop here, whereas Coding lazily creates its owned service loop.
    loop, identifier = await owner._bridge(identity())
    host = request.param == "host"
    dispatch = (lambda work: settle_on(loop, work)) if host else owner._bridge
    try:
        yield SimpleNamespace(
            owner=owner,
            loop=loop,
            dispatch=dispatch,
            host=host,
            thread_id=identifier,
            ownership=execution_owner,
            closed_error=RuntimeError,
        )
    finally:
        # Admission tests deliberately close the flag without closing the actual owner.
        if not loop.is_closed():
            owner._closed = False
        await owner.aclose()
        execution_owner.assert_closed(owner)
