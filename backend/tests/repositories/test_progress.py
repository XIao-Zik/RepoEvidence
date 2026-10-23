from repo_evidence.repositories.progress import IndexProgressStore


def test_progress_store_tracks_terminal_index_statistics() -> None:
    store = IndexProgressStore()

    store.update("repo-1", "scanning", scanned_files=8, ignored_files=2)
    store.update("repo-1", "embedding", reused_files=5, new_chunks=12)
    progress = store.update("repo-1", "ready")

    assert progress.phase == "ready"
    assert progress.scanned_files == 8
    assert progress.ignored_files == 2
    assert progress.reused_files == 5
    assert progress.new_chunks == 12
