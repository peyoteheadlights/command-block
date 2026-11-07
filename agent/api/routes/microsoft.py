"""Sign in with Microsoft, for Diagnose server's joins with the owner's own
account (agent/microsoft.py). Owner only; no answer ever holds the sign-in
itself, only the names and the code to type."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...security.auth import Principal
from ...security.permissions import MICROSOFT_ACCOUNT, require
from ..deps import audit, get_core
from ..responses import MicrosoftStatus

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
