from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, status

from app.api.dependencies import get_graph
from app.api.schemas.threads import ThreadStateResponse
from app.application import fetch_thread_state

threads_router = APIRouter()
GraphDep = Annotated[Any, Depends(get_graph)]


@threads_router.get("/threads/{thread_id}/state", response_model=ThreadStateResponse)
def thread_state(
    thread_id: Annotated[str, Path(min_length=1, max_length=255)],
    graph: GraphDep,
) -> ThreadStateResponse:
    result = fetch_thread_state(thread_id, graph=graph)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread '{thread_id}' was not found.",
        )

    return ThreadStateResponse(**asdict(result))
