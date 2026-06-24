from pathlib import Path
from uuid import uuid4

from awesome_agent.artifacts.store import LocalArtifactStore


def test_artifact_store_writes_hashes_and_deletes_run(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    run_id = uuid4()

    metadata = store.write(
        run_id=run_id,
        artifact_type="logs",
        filename="../test.log",
        content=b"hello",
        mime_type="text/plain",
    )

    assert metadata.path.name.endswith("-test.log")
    assert metadata.sha256 == (
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )
    assert metadata.path.exists()

    store.delete_run(run_id)
    assert not metadata.path.exists()
