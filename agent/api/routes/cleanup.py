"""Clean-up: what a server folder no longer needs (agent/cleanup.py).
Moving things to the clean-up bin, Undo and emptying the bin need the right
to delete backups, the closest right to "delete what the server made"."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request

from ... import cleanup
from ...security.auth import Principal
from ...security.permissions import BACKUPS_DELETE, SERVER_VIEW, require
from ..deps import get_server
from ..errors import audit_failure
from ..responses import CleanupDone, CleanupEmptied, CleanupList, CleanupUndone
from .models import CleanupBatchRequest, CleanupRequest

router = APIRouter()


@router.get("/cleanup", response_model=CleanupList)
async def cleanup_list(
    principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)
):
    """What could go, with sizes. Measured now in a worker thread."""
    return await asyncio.to_thread(cleanup.scan, ctx)


@router.post("/cleanup", response_model=CleanupDone)
async def cleanup_remove(
    payload: CleanupRequest,
    request: Request,
    principal: Principal = Depends(require(BACKUPS_DELETE)),
    ctx=Depends(get_server),
):
    """Move the picked items into the clean-up bin, as one batch Undo puts back."""
    with audit_failure(ctx, request, "cleanup"):
        result = await asyncio.to_thread(cleanup.remove, ctx, payload.paths, principal.user)
    request.state.audited = True
    return {"ok": True, **result}


@router.post("/cleanup/undo", response_model=CleanupUndone)
async def cleanup_undo(
    payload: CleanupBatchRequest,
    request: Request,
    principal: Principal = Depends(require(BACKUPS_DELETE)),
    ctx=Depends(get_server),
):
    with audit_failure(ctx, request, "cleanup_undo"):
        result = await asyncio.to_thread(cleanup.undo, ctx, payload.batch or "", principal.user)
    request.state.audited = True
    return {"ok": True, **result}


@router.post("/cleanup/empty", response_model=CleanupEmptied)
async def cleanup_empty(
    payload: CleanupBatchRequest,
    request: Request,
    principal: Principal = Depends(require(BACKUPS_DELETE)),
    ctx=Depends(get_server),
):
    """Empty one batch (or the whole bin, with no batch) for good."""
    with audit_failure(ctx, request, "cleanup_empty"):
        result = await asyncio.to_thread(cleanup.empty, ctx, payload.batch, principal.user)
    request.state.audited = True
    return {"ok": True, **result}
