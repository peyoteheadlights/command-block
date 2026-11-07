"""Your PCs (agent/yourpcs.py): the owner's PCs that share one account.

The /pcs routes are for the apps. The /sync routes are for the owner's
other PCs only: a PC proves itself with its sync key (or, when joining, with
a join code and the owner's password), never with a sign-in.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ...discord.botpc import LINK_HEADER
from ...security.auth import AccountError, AuthError, Principal
from ...security.permissions import ACCOUNT, SECURITY_MANAGE, check_bot_link, require
from ...yourpcs import KEY_HEADER, PC_HEADER, PcError, Refused
from ..deps import audit, client_ip, get_core
from ..responses import (
    BotLinkAnswer,
    JoinAnswer,
    JoinLink,
    Ok,
    PcList,
    SyncChanges,
    SyncHello,
    SyncTaken,
)
from .models import BotLinkRequest, PcJoinRequest, SyncChangesRequest, SyncJoinRequest

router = APIRouter()


def _said(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/pcs", response_model=PcList)
async def list_pcs(principal: Principal = Depends(require(ACCOUNT)), core=Depends(get_core)):
    return core.pcs.listing(principal)


@router.post("/pcs/link", response_model=JoinLink)
async def join_link(
    request: Request,
    principal: Principal = Depends(require(SECURITY_MANAGE)),
    core=Depends(get_core),
):
    """A join link for another PC: works once, for 10 minutes."""
    try:
        made = core.pcs.join_link()
    except PcError as exc:
        raise _said(exc) from exc
    audit(core, request, "pc_join_link")
    return made


@router.post("/pcs/join", response_model=JoinAnswer)
async def join(
    payload: PcJoinRequest,
    request: Request,
    principal: Principal = Depends(require(SECURITY_MANAGE)),
    core=Depends(get_core),
):
    """On the new PC: ask the PC the link is from to take this one in."""
    try:
        answer = await core.pcs.join(payload.link, payload.username, payload.password)
    except PcError as exc:
        raise _said(exc) from exc
    audit(core, request, "pc_join")
    return answer


@router.post("/pcs/joins/{join_id}/{answer}", response_model=JoinAnswer)
async def answer_join(
    join_id: str,
    answer: str,
    request: Request,
    principal: Principal = Depends(require(SECURITY_MANAGE)),
    core=Depends(get_core),
):
    """ "Allow <PC> to join your account?": allow or refuse."""
    if answer not in ("allow", "refuse"):
        raise HTTPException(status_code=404, detail="Answer allow or refuse.")
    try:
        return core.pcs.answer_join(principal, join_id, answer == "allow")
    except PcError as exc:
        raise _said(exc) from exc


@router.post("/pcs/resets/{pc}/confirm", response_model=Ok)
async def confirm_reset(
    pc: str,
    request: Request,
    principal: Principal = Depends(require(SECURITY_MANAGE)),
    core=Depends(get_core),
):
    """A password reset made on another PC becomes the owner's password on
    every PC."""
    try:
        core.auth.confirm_reset(principal, pc)
    except AccountError as exc:
        raise _said(exc) from exc
    audit(core, request, "owner_reset_confirmed", target=pc)
    return {"ok": True}


@router.delete("/pcs/{pc}", response_model=Ok)
async def remove_pc(
    pc: str,
    request: Request,
    principal: Principal = Depends(require(SECURITY_MANAGE)),
    core=Depends(get_core),
):
    """Remove a PC: its sync key stops working at once."""
    try:
        return core.pcs.remove(principal, pc)
    except PcError as exc:
        raise _said(exc) from exc


# -- from the owner's other PCs --------------------------------------------
def _peer(request: Request, core) -> str:
    core.auth.api_rate.check(f"api:{client_ip(request)}")
    pc = request.headers.get(PC_HEADER, "")
    try:
        core.pcs.check_peer(pc, request.headers.get(KEY_HEADER, ""))
    except Refused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    return pc


@router.post("/sync/join", response_model=JoinAnswer)
async def sync_join(payload: SyncJoinRequest, request: Request, core=Depends(get_core)):
    try:
        return core.pcs.request_join(payload.model_dump(), client_ip(request))
    except PcError as exc:
        raise _said(exc) from exc


@router.get("/sync/hello", response_model=SyncHello)
async def sync_hello(request: Request, core=Depends(get_core)):
    return core.pcs.hello(_peer(request, core))


@router.get("/sync/changes", response_model=SyncChanges)
async def sync_changes(request: Request, since: int = 0, core=Depends(get_core)):
    """What changed here since ``since``, for one of the owner's PCs."""
    return core.pcs.changes_for(_peer(request, core), max(since, 0))


@router.post("/sync/changes", response_model=SyncTaken)
async def sync_receive(payload: SyncChangesRequest, request: Request, core=Depends(get_core)):
    """Changes made on (or passed on by) one of the owner's PCs."""
    pc = _peer(request, core)
    return {"ok": True, "taken": core.pcs.receive(pc, payload.records)}


@router.post("/sync/bot/{what}", response_model=BotLinkAnswer)
async def bot_link_call(
    what: str, payload: BotLinkRequest, request: Request, core=Depends(get_core)
):
    """What Discord asked about one of this PC's servers, passed on by the
    Bot PC over its Bot link: only what Discord can do (BOT_LINK_CALLS)."""
    core.auth.api_rate.check(f"api:{client_ip(request)}")
    try:
        core.botpc.check_link(
            request.headers.get(PC_HEADER, ""), request.headers.get(LINK_HEADER, "")
        )
        if what == "interaction":
            from ...discord.bot import on_the_list

            check_bot_link(what, on_the_list(payload.interaction or {}))
        else:
            check_bot_link(what)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    try:
        return core.botpc.answer(what, payload.model_dump())
    except PcError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
