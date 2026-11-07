"""Sign in with Microsoft, for Diagnose server's joins with the owner's own
account (agent/microsoft.py). Owner only; no answer ever holds the sign-in
itself, only the names and the code to type.

HeadlessMC's own sign-in, for the join with all mods (agent/headlessmc.py),
is here too: same rules."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...security.auth import Principal
from ...security.permissions import MICROSOFT_ACCOUNT, require
from ..deps import audit, get_core
from ..responses import HeadlessMcStatus, MicrosoftStatus

router = APIRouter()


@router.get("/microsoft", response_model=MicrosoftStatus)
async def microsoft_status(
    principal: Principal = Depends(require(MICROSOFT_ACCOUNT)), core=Depends(get_core)
):
    """Signed in or not, the names, and the code while a sign-in waits for it."""
    return core.microsoft.status()


@router.post("/microsoft/sign-in", response_model=MicrosoftStatus)
async def microsoft_sign_in(
    request: Request,
    principal: Principal = Depends(require(MICROSOFT_ACCOUNT)),
    core=Depends(get_core),
):
    """Start a sign-in: answers with the code to type on Microsoft's page.
    Ask GET /microsoft until signed_in (or error)."""
    status = await core.microsoft.sign_in()
    audit(core, request, "microsoft_sign_in", result="ok" if not status["error"] else "failed")
    return status


@router.post("/microsoft/sign-out", response_model=MicrosoftStatus)
async def microsoft_sign_out(
    request: Request,
    principal: Principal = Depends(require(MICROSOFT_ACCOUNT)),
    core=Depends(get_core),
):
    """Forget the sign-in on this PC."""
    core.microsoft.sign_out()
    audit(core, request, "microsoft_sign_out")
    return core.microsoft.status()


@router.get("/headlessmc", response_model=HeadlessMcStatus)
async def headlessmc_status(
    principal: Principal = Depends(require(MICROSOFT_ACCOUNT)), core=Depends(get_core)
):
    """HeadlessMC signed in or not, its name, and the page while a sign-in waits."""
    return core.headlessmc.status()


@router.post("/headlessmc/sign-in", response_model=HeadlessMcStatus)
async def headlessmc_sign_in(
    request: Request,
    principal: Principal = Depends(require(MICROSOFT_ACCOUNT)),
    core=Depends(get_core),
):
    """Start HeadlessMC's sign-in (it is downloaded first, the first time):
    answers with the page to open. Ask GET /headlessmc until signed_in."""
    status = await core.headlessmc.sign_in()
    audit(core, request, "headlessmc_sign_in", result="ok" if not status["error"] else "failed")
    return status


@router.post("/headlessmc/sign-out", response_model=HeadlessMcStatus)
async def headlessmc_sign_out(
    request: Request,
    principal: Principal = Depends(require(MICROSOFT_ACCOUNT)),
    core=Depends(get_core),
):
    """Forget HeadlessMC's sign-in on this PC."""
    core.headlessmc.sign_out()
    audit(core, request, "headlessmc_sign_out")
    return core.headlessmc.status()
