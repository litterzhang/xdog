"""Every host test owns and settles its private Eyes daemon."""
import pytest

# These helpers remain importable for the cleanup/isolation regression tests.
from test_support.eyes_daemon import _lease_free  # noqa: F401


@pytest.fixture(autouse=True)
async def isolated_eyes_daemon():
    from test_support.eyes_daemon import isolated_eyes_daemon as isolate

    async with isolate(prefix='claw-eyes-') as directory:
        yield directory
