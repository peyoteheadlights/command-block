"""Scheduled tasks."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...scheduler.scheduler import steps_of
from ...security.auth import Principal
from ...security.permissions import (
    CHAT_SEND,
    CONSOLE_SEND,
    SCHEDULES_MANAGE,
    SERVER_VIEW,
    check,
    require,
)
from ..deps import audit, get_server
from ..errors import respond_as
from ..responses import Ok, ScheduleList, ScheduleRan, ScheduleSaved
from .models import ScheduleRequest

router = APIRouter()


def may_save(principal: Principal, ctx, payload: ScheduleRequest) -> None:
    """A schedule that types in the console or chat needs that permission
    too, so a schedule is never a way around it."""
    for task, _step in steps_of(payload.task, payload.payload):
        if task == "command":
            check(principal, CONSOLE_SEND, ctx.server_id)
        elif task == "message":
            check(principal, CHAT_SEND, ctx.server_id)


@router.get("/schedules", response_model=ScheduleList)
async def list_schedules(
    principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)
):
    return {"schedules": ctx.scheduler.list_schedules(), "tasks": list(ctx.scheduler.handlers)}


@router.post("/schedules", response_model=ScheduleSaved)
async def create_schedule(
    payload: ScheduleRequest,
    request: Request,
    principal: Principal = Depends(require(SCHEDULES_MANAGE)),
    ctx=Depends(get_server),
):
    may_save(principal, ctx, payload)
    row = ctx.scheduler.add(
        payload.name, payload.task, payload.kind, payload.expr, payload.payload, payload.enabled
    )
    audit(ctx, request, "schedule_create", target=payload.name)
    return {"ok": True, "schedule": row}


@router.put("/schedules/{schedule_id}", response_model=ScheduleSaved)
async def update_schedule(
    schedule_id: int,
    payload: ScheduleRequest,
    request: Request,
    principal: Principal = Depends(require(SCHEDULES_MANAGE)),
    ctx=Depends(get_server),
):
    may_save(principal, ctx, payload)
    row = ctx.scheduler.update(
        schedule_id,
        name=payload.name,
        task=payload.task,
        kind=payload.kind,
        expr=payload.expr,
        payload=payload.payload,
        enabled=payload.enabled,
    )
    audit(ctx, request, "schedule_update", target=str(schedule_id))
    return {"ok": True, "schedule": row}


@router.delete("/schedules/{schedule_id}", response_model=Ok)
async def delete_schedule(
    schedule_id: int,
    request: Request,
    principal: Principal = Depends(require(SCHEDULES_MANAGE)),
    ctx=Depends(get_server),
):
    with respond_as(404):
        ctx.scheduler.delete(schedule_id)
    audit(ctx, request, "schedule_delete", target=str(schedule_id))
    return {"ok": True}


@router.post("/schedules/{schedule_id}/run", response_model=ScheduleRan)
async def run_schedule(
    schedule_id: int,
    request: Request,
    principal: Principal = Depends(require(SCHEDULES_MANAGE)),
    ctx=Depends(get_server),
):
    with respond_as(404):
        row = ctx.scheduler.get(schedule_id)
        result = await ctx.scheduler.run_task(row)
    audit(ctx, request, "schedule_run_now", target=row["name"])
    return {"ok": True, "result": result}
