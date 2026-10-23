"""Wake-on-LAN (agent/wake.py). The owner sets it up from PC settings
(Technical); devices that may wake the PC (the owner, moderators with "Start
the agent") read what they keep for when it sleeps, and their own wake key;
the helping PC reads the valid key hashes."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request

from ...security.auth import Principal
from ...security.permissions import AGENT_START, PC_SETTINGS, require
from ...wake import WakeRefused
from ..deps import audit, get_core
from ..responses import WakeDevice, WakeKey, WakeKeys, WakeSetup
from .models import WakeHelperRequest, WakeSetupRequest

router = APIRouter()


def _refused(exc: WakeRefused) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/wake", response_model=WakeDevice)
async def wake_info(principal: Principal = Depends(require(AGENT_START)), core=Depends(get_core)):
    """What this device keeps to wake the PC once it sleeps."""
    return core.wake.for_device(principal.token_hash)


@router.post("/wake/key", response_model=WakeKey)
async def wake_key(
    request: Request, principal: Principal = Depends(require(AGENT_START)), core=Depends(get_core)
):
    """A new wake key for this device (the old one stops working). Shown
    once: only its hash is kept."""
    try:
        key = core.wake.new_key(principal.token_hash)
    except WakeRefused as exc:
        raise _refused(exc) from exc
    audit(core, request, "wake_key_made")
    return {"key": key}


@router.get("/wake/setup", response_model=WakeSetup)
async def wake_setup(principal: Principal = Depends(require(PC_SETTINGS)), core=Depends(get_core)):
    return await asyncio.to_thread(core.wake.setup)


@router.put("/wake/setup", response_model=WakeSetup)
async def change_wake_setup(
    payload: WakeSetupRequest,
    request: Request,
    principal: Principal = Depends(require(PC_SETTINGS)),
    core=Depends(get_core),
):
    """Turn it on (after the cable check) or off, or save the router's wake page."""
    try:
        if payload.on is True:
            state = await asyncio.to_thread(core.wake.turn_on)
        elif payload.on is False:
            state = core.wake.turn_off()
        else:
            state = core.wake.setup()
        if "router_url" in payload.model_fields_set:
            state = core.wake.set_router(payload.router_url)
    except WakeRefused as exc:
        raise _refused(exc) from exc
    audit(core, request, "wake_setup", detail=f"on={state['on']}")
    return state


@router.post("/wake/try", response_model=WakeSetup)
async def wake_try(principal: Principal = Depends(require(PC_SETTINGS)), core=Depends(get_core)):
    """Ends the setup: the agent marks "Tested, worked" once it wakes."""
    try:
        return core.wake.try_it()
    except WakeRefused as exc:
        raise _refused(exc) from exc


@router.put("/wake/helper", response_model=WakeSetup)
async def wake_helper(
    payload: WakeHelperRequest,
    request: Request,
    principal: Principal = Depends(require(PC_SETTINGS)),
    core=Depends(get_core),
):
    """This sign-in's PC becomes the helping PC ("Help wake <PC>")."""
    try:
        state = core.wake.set_helper(principal.token_hash, payload.address, payload.port)
    except WakeRefused as exc:
        raise _refused(exc) from exc
    audit(core, request, "wake_helper_set", detail=payload.address)
    return state


@router.delete("/wake/helper", response_model=WakeSetup)
async def wake_helper_off(
    request: Request, principal: Principal = Depends(require(PC_SETTINGS)), core=Depends(get_core)
):
    audit(core, request, "wake_helper_removed")
    return core.wake.remove_helper()


@router.get("/wake/keys", response_model=WakeKeys)
async def wake_keys(principal: Principal = Depends(require(PC_SETTINGS)), core=Depends(get_core)):
    """The helping PC's list: the recorded PC and the valid key hashes. Only
    the helping PC's own sign-in gets it."""
    helper = core.wake.helper()
    if not helper or helper["session"] != principal.token_hash:
        raise HTTPException(status_code=403, detail="This PC isn't the helping PC.")
    saved = core.wake.saved()
    return {
        "mac": saved.get("mac"),
        "broadcast": saved.get("broadcast"),
        "keys": core.wake.key_hashes(),
    }
