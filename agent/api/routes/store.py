"""The Store tab (agent/store.py). Owner only: the key and everything to
do with money stay away from moderators. No answer ever holds the key."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ... import store
from ...security.auth import Principal
from ...security.permissions import SERVERS_MANAGE, require
from ..deps import audit, get_server
from ..responses import StoreChecked, StoreView

router = APIRouter()


class StoreKey(BaseModel):
    key: str = Field(max_length=200)


@router.get("/store", response_model=StoreView)
async def store_tab(
    principal: Principal = Depends(require(SERVERS_MANAGE)), ctx=Depends(get_server)
):
    return store.view(ctx)


@router.post("/store/connect", response_model=StoreView)
async def connect_store(
    payload: StoreKey,
    request: Request,
    principal: Principal = Depends(require(SERVERS_MANAGE)),
    ctx=Depends(get_server),
):
    """Check the secret key with Tebex, then install the plugin and
    LuckPerms and make the Store ranks (backup first, one Undo)."""
    result = await store.connect(ctx, payload.key, principal.user)
    audit(ctx, request, "store_connect")  # never the key
    return result


@router.post("/store/check", response_model=StoreChecked)
async def check_store(
    request: Request,
    principal: Principal = Depends(require(SERVERS_MANAGE)),
    ctx=Depends(get_server),
):
    result = await store.check_store(ctx)
    audit(ctx, request, "store_check")
    return result
