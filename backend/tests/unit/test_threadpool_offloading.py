"""Unit tests verifying that slow blocking operations are offloaded to run_in_threadpool."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from app.core.auth import CurrentUser
from app.modules.ingest import router as ingest_router
from app.modules.ingest.service import UploadResult
from app.modules.matching import router as matching_router
from app.modules.matching.service import SearchResult
from app.modules.scene_search import service as scene_search_service
from app.modules.scene_search.schemas import SceneSearchRequest, SceneSearchResponse
from starlette.requests import Request


@pytest.mark.asyncio
async def test_upload_photos_offloads_to_threadpool() -> None:
    """
    Purpose: verify that ingest upload_photos calls service.upload_photos via run_in_threadpool.
    Inputs: mocked Request, PasskeyUser, Ctx.
    Outputs: None.
    Side effects: verifies run_in_threadpool invocation.
    """
    event_id = uuid4()
    req = MagicMock(spec=Request)
    user = MagicMock(spec=CurrentUser)
    ctx = MagicMock()
    ctx.settings.upload_max_files = 10
    ctx.settings.upload_max_bytes = 1024 * 1024
    ctx.settings.upload_spool_dir = "/tmp"

    dummy_result = UploadResult(
        collection_id=uuid4(), photo_ids=[uuid4()], already_uploaded=set(), message="uploaded"
    )

    with (
        patch.object(ingest_router, "_read_files", new_callable=AsyncMock) as mock_read,
        patch("app.modules.ingest.router.run_in_threadpool", new_callable=AsyncMock) as mock_pool,
    ):
        mock_read.return_value = ([b"photo_bytes"], None)
        mock_pool.return_value = dummy_result

        res = await ingest_router.upload_photos(event_id, req, user, ctx)
        assert mock_pool.called
        assert mock_pool.call_args[0][0] == ingest_router.service.upload_photos
        assert res.collection_id == dummy_result.collection_id


@pytest.mark.asyncio
async def test_upload_chunk_part_offloads_to_threadpool() -> None:
    """
    Purpose: verify that ingest upload_chunk_part calls service functions via run_in_threadpool.
    Inputs: mocked Request, PasskeyUser, Ctx.
    Outputs: None.
    Side effects: verifies run_in_threadpool invocation.
    """
    event_id = uuid4()
    upload_id = uuid4()
    req = MagicMock(spec=Request)

    async def mock_stream():
        yield b"chunk bytes"

    req.stream = mock_stream
    req.headers = {}
    user = MagicMock(spec=CurrentUser)
    ctx = MagicMock()

    dummy_session = MagicMock()
    dummy_session.total_chunks = 2

    with patch("app.modules.ingest.router.run_in_threadpool", new_callable=AsyncMock) as mock_pool:
        mock_pool.side_effect = [[0], dummy_session]

        res = await ingest_router.upload_chunk_part(
            event_id, upload_id, req, user, ctx, chunk_index=0
        )
        assert mock_pool.call_count == 2
        assert mock_pool.call_args_list[0][0][0] == ingest_router.service.upload_chunk
        assert mock_pool.call_args_list[1][0][0] == ingest_router.service.get_upload_session
        assert res.received_chunks == [0]
        assert res.complete is False


@pytest.mark.asyncio
async def test_search_offloads_to_threadpool() -> None:
    """
    Purpose: verify that matching search calls service.guest_search via run_in_threadpool.
    Inputs: mocked Request, User, Ctx.
    Outputs: None.
    Side effects: verifies run_in_threadpool invocation.
    """
    event_id = uuid4()
    req = MagicMock(spec=Request)
    user = MagicMock(spec=CurrentUser)
    ctx = MagicMock()
    ctx.settings.search_max_frames = 3
    ctx.settings.selfie_max_bytes = 1024

    dummy_result = SearchResult(release=[], some_photos_missing=False)

    with (
        patch.object(matching_router, "read_frames", new_callable=AsyncMock) as mock_read,
        patch("app.modules.matching.router.run_in_threadpool", new_callable=AsyncMock) as mock_pool,
    ):
        mock_read.return_value = [b"frame1", b"frame2"]
        mock_pool.return_value = dummy_result

        res = await matching_router.search(event_id, req, user, ctx)
        assert mock_pool.called
        assert mock_pool.call_args[0][0] == matching_router.service.guest_search
        assert res.release == []


@pytest.mark.asyncio
async def test_scene_search_offloads_db_and_embeddings_to_threadpool() -> None:
    """
    Purpose: verify that scene search offloads DB prep and ranking/embedding to run_in_threadpool.
    Inputs: mocked Ctx, CurrentUser, SceneSearchRequest.
    Outputs: None.
    Side effects: verifies run_in_threadpool invocations.
    """
    event_id = uuid4()
    user = MagicMock(spec=CurrentUser)
    ctx = MagicMock()
    ctx.rewriter = MagicMock()
    ctx.rewriter.rewrite_query = AsyncMock(return_value=["rewritten query"])
    req = SceneSearchRequest(query="bride dancing")

    dummy_response = SceneSearchResponse(candidates=[], query_rewritten=True)

    with patch(
        "app.modules.scene_search.service.run_in_threadpool", new_callable=AsyncMock
    ) as mock_pool:
        mock_pool.side_effect = [
            ({uuid4()}, {uuid4(): MagicMock()}),
            dummy_response,
        ]

        res = await scene_search_service.search_scene(ctx, user, event_id, req)
        assert mock_pool.call_count == 2
        assert mock_pool.call_args_list[0][0][0] == scene_search_service._prepare_scene_search
        assert mock_pool.call_args_list[1][0][0] == scene_search_service._rank_and_save_candidates
        assert res.query_rewritten is True
