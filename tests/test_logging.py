"""Link URLs must not reach the logs: query strings can carry tokens."""

import logging

import pytest
import respx

from gcpbasicdeployment.core.logging import setup_logging
from gcpbasicdeployment.services.checker import check_many


@respx.mock
async def test_checked_url_is_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    setup_logging("DEBUG")
    # setup_logging replaces the root handlers, so attach pytest's capture again.
    logging.getLogger().addHandler(caplog.handler)
    respx.get("https://93.184.216.34/p?token=SECRET123").respond(200)

    try:
        [outcome] = await check_many(["https://example.com/p?token=SECRET123"], 5.0, 1)
    finally:
        setup_logging("INFO")

    assert outcome.status == "alive"
    assert "SECRET123" not in caplog.text
