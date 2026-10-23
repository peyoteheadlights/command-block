"""The old browser alerts, now gone. The routes stay,
deprecated, until a release has said so (scripts/api_contract.py). The
desktop app and the phone apps get alerts from /api/alerts instead."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ...security.permissions import PC_SETTINGS, SETTINGS_VIEW, require

router = APIRouter()

GONE = "Browser alerts were removed. Use the desktop app or a phone app."


@router.get("/push", deprecated=True, dependencies=[Depends(require(SETTINGS_VIEW))])
async def push_status():
    raise HTTPException(status_code=410, detail=GONE)


@router.post("/push/subscribe", deprecated=True, dependencies=[Depends(require(PC_SETTINGS))])
async def subscribe():
    raise HTTPException(status_code=410, detail=GONE)


@router.post("/push/unsubscribe", deprecated=True, dependencies=[Depends(require(PC_SETTINGS))])
async def unsubscribe():
    raise HTTPException(status_code=410, detail=GONE)


@router.post("/push/test", deprecated=True, dependencies=[Depends(require(PC_SETTINGS))])
async def test_push():
    raise HTTPException(status_code=410, detail=GONE)
