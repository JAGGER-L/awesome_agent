from uuid import uuid4

import pytest
from tests.fakes import FakeModelProvider

from awesome_agent.memory.compression import ContextCompressor
from awesome_agent.memory.models import ContextItem


@pytest.mark.asyncio
async def test_context_compression_preserves_source_lineage() -> None:
    event_id = uuid4()
    compressor = ContextCompressor(FakeModelProvider(["summary"]))

    summary = await compressor.compress(
        [ContextItem(event_id=event_id, content="Tests failed once.")]
    )

    assert summary.text == "summary"
    assert summary.source_event_ids == [event_id]
