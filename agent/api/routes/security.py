"""Sessions, TLS certificate facts and the audit log."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request

from ... import tailscale_share
from ...security.auth import Principal
from ...security.permissions import PC_SETTINGS, SECURITY_MANAGE, SECURITY_VIEW, require
from ...tailscale import tailscale_status
from ...tailscale_watch import MoveRefused
from ..deps import audit, get_core
from ..responses import (
    AuditLog,
    SecurityOverview,
    SessionsRevoked,
    ShareKey,
    ShareLink,
    TailscaleState,
)
from .models import ShareKeyRequest

router = APIRouter()


@router.get("/security", response_model=SecurityOverview)
async def security_overview(
    principal: Principal = Depends(require(SECURITY_VIEW)), core=Depends(get_core)
):
    return {
        "agent_connected": True,
        "tls": core.metrics.certificate_status(),
        "https_enabled": core.config.tls_enabled,
        "hostname": core.config.tls_hostname or None,
        "tailscale": await asyncio.to_thread(core.metrics.tailscale_status),
        "authentication": {
            "enabled": core.auth.configured,
            "session_hours": core.config.security.session_hours,
            "api_token_configured": bool(core.config.api_token),
        },
        "active_sessions": core.auth.active_sessions(),
        "failed_logins_24h": core.auth.failed_login_count(24),
        "bind_address": f"{core.config.network.host}:{core.config.network.port}",
    }


@router.get("/security/audit", response_model=AuditLog)
async def audit_log(
    limit: int = 100, principal: Principal = Depends(require(SECURITY_VIEW)), core=Depends(get_core)
):
    limit = max(1, min(int(limit), 500))
    return {"entries": core.db.audit_entries(limit)}


@router.post("/security/revoke-sessions", response_model=SessionsRevoked)
async def revoke_sessions(
    request: Request,
    principal: Principal = Depends(require(SECURITY_MANAGE)),
    core=Depends(get_core),
):
    count = core.auth.revoke_all()
    audit(core, request, "revoke_all_sessions", detail=str(count))
    return {"ok": True, "revoked": count}


@router.get("/security/tls")
async def tls_status(
    principal: Principal = Depends(require(SECURITY_VIEW)), core=Depends(get_core)
):
    """Certificate facts read from disk.

    Only metadata is returned. The private key is never read into a response,
    never logged, and has no endpoint of its own - the only thing reported
    about it is whether its public half matches the certificate.
    """
    return {
        "enabled": core.config.tls_enabled,
        "certificate": core.metrics.certificate_status(),
        "hostname": core.config.tls_hostname or None,
        "hsts": core.config.tls.hsts,
        "http_redirect": core.config.tls.http_redirect,
        "renewal": "Run 'python -m installer.make_certs --renew' on the server PC. "
        "Tailscale-issued certificates renew automatically.",
    }


# The desktop app's Tailscale step. Installing and signing in to Tailscale is
# done by the desktop app; the agent only moves once Tailscale says connected.
@router.get("/tailscale", response_model=TailscaleState)
async def tailscale_state(
    principal: Principal = Depends(require(PC_SETTINGS)), core=Depends(get_core)
):
    return await core.tailscale.status()


@router.post("/tailscale/move", response_model=TailscaleState)
async def tailscale_move(
    request: Request,
    principal: Principal = Depends(require(PC_SETTINGS)),
    core=Depends(get_core),
):
    try:
        state = await core.tailscale.start_move()
    except MoveRefused as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(core, request, "tailscale_move", detail=state["address"] or "waiting")
    return state


@router.post("/tailscale/skip", response_model=TailscaleState)
async def tailscale_skip(
    request: Request,
    principal: Principal = Depends(require(PC_SETTINGS)),
    core=Depends(get_core),
):
    audit(core, request, "tailscale_skip")
    return core.tailscale.skip()


# Share links for moderator invites, made through Tailscale's API with a key
# the owner saved once (Technical). agent/tailscale_share.py.
async def _tailscale_address(core) -> str:
    if core.tailscale.moved:
        return core.config.network.host
    report = await asyncio.to_thread(tailscale_status)
    ipv4 = next((a for a in report.get("addresses") or [] if ":" not in a), None)
    if not report.get("connected") or not ipv4:
        raise HTTPException(409, "Tailscale isn't connected on this PC.")
    return ipv4


@router.get("/tailscale/share-key", response_model=ShareKey)
async def share_key(principal: Principal = Depends(require(PC_SETTINGS)), core=Depends(get_core)):
    return tailscale_share.state(core.config)


@router.put("/tailscale/share-key", response_model=ShareKey)
async def save_share_key(
    payload: ShareKeyRequest,
    request: Request,
    principal: Principal = Depends(require(PC_SETTINGS)),
    core=Depends(get_core),
):
    """Checked with Tailscale first: it must find this PC with the key."""
    address = await _tailscale_address(core)
    try:
        state = await tailscale_share.save(
            core.config, address, payload.kind, payload.secret, payload.client_id
        )
    except tailscale_share.ShareError as exc:
        raise HTTPException(400, str(exc)) from exc
    audit(core, request, "tailscale_key_saved", detail=payload.kind)
    return state


@router.delete("/tailscale/share-key", response_model=ShareKey)
async def remove_share_key(
    request: Request, principal: Principal = Depends(require(PC_SETTINGS)), core=Depends(get_core)
):
    audit(core, request, "tailscale_key_removed")
    return tailscale_share.remove(core.config)


@router.post("/tailscale/share-link", response_model=ShareLink)
async def make_share_link(
    request: Request, principal: Principal = Depends(require(PC_SETTINGS)), core=Depends(get_core)
):
    address = await _tailscale_address(core)
    try:
        url = await tailscale_share.make_link(core.config, address)
    except tailscale_share.ShareError as exc:
        raise HTTPException(400, str(exc)) from exc
    audit(core, request, "tailscale_share_link")
    return {"url": url}
