"""Edit settings: the config files in a server's folder. Viewing them needs
"Change mods" too, because they can hold tokens and passwords."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request

from ... import configfiles
from ...security.auth import Principal
from ...security.permissions import MODS_MANAGE, require
from ..deps import audit, get_server
from ..responses import ConfigFile, ConfigFileList, ConfigFileSaved
from .models import ConfigFileRequest, ConfigFileSaveRequest

router = APIRouter()


@router.get("/config-files", response_model=ConfigFileList)
async def list_files(
    addon: str = "",
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    """One add-on's files (``addon`` = its jar's file name), or every other
    config file in the server folder."""
    if addon:
        return await asyncio.to_thread(configfiles.addon_files, ctx, addon)
    return await asyncio.to_thread(configfiles.server_files, ctx)


@router.get("/config-files/file", response_model=ConfigFile)
async def read_file(
    path: str, principal: Principal = Depends(require(MODS_MANAGE)), ctx=Depends(get_server)
):
    """The file's text with its hidden values as ••••••."""
    return await asyncio.to_thread(configfiles.read, ctx, path)


@router.post("/config-files/show", response_model=ConfigFile)
async def show_hidden(
    payload: ConfigFileRequest,
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    """The file with its hidden values in it. Each look is in the audit log."""
    result = await asyncio.to_thread(configfiles.read, ctx, payload.path, True)
    audit(ctx, request, "config_file_show", target=payload.path)
    return result


@router.post("/config-files/save", response_model=ConfigFileSaved)
async def save_file(
    payload: ConfigFileSaveRequest,
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    """Check the text as its format, back up the file, write it, read it
    back. One Undo."""
    result = await configfiles.save(
        ctx, payload.path, payload.text, payload.version, user=principal.user
    )
    audit(ctx, request, "config_file_save", target=payload.path)
    return {"ok": True, **result}
