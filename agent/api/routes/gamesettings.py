"""One server's game settings: the common server.properties values, and
the server icon."""

from __future__ import annotations

import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException, Request

from ... import gamesettings
from ...security.auth import Principal
from ...security.permissions import SETTINGS_GAME, SETTINGS_VIEW, require
from ..deps import audit, get_server
from ..responses import GameSettings, GameSettingsSaved, ServerIcon, ServerIconSaved
from .models import GameSettingsRawRequest, GameSettingsRequest, ServerIconRequest

router = APIRouter()


@router.get("/game-settings", response_model=GameSettings)
async def get_game_settings(
    principal: Principal = Depends(require(SETTINGS_VIEW)), ctx=Depends(get_server)
):
    """What server.properties says for each known key, and the whole file."""
    return gamesettings.view(ctx)


@router.put("/game-settings", response_model=GameSettingsSaved)
async def save_game_settings(
    payload: GameSettingsRequest,
    request: Request,
    principal: Principal = Depends(require(SETTINGS_GAME)),
    ctx=Depends(get_server),
):
    """Change known keys. Every value is checked first; nothing is written
    unless all of them pass."""
    result = await gamesettings.save(ctx, updates=payload.values, user=principal.user)
    audit(ctx, request, "game_settings", detail=", ".join(result["keys"])[:300])
    return {"ok": True, **result}


@router.put("/game-settings/raw", response_model=GameSettingsSaved)
async def save_game_settings_raw(
    payload: GameSettingsRawRequest,
    request: Request,
    principal: Principal = Depends(require(SETTINGS_GAME)),
    ctx=Depends(get_server),
):
    """Replace the whole file with text typed in Technical mode. The known
    keys in it are still checked like the form's."""
    result = await gamesettings.save(ctx, raw_text=payload.text, user=principal.user)
    audit(ctx, request, "game_settings_raw", detail=", ".join(result["keys"])[:300])
    return {"ok": True, **result}


@router.get("/server-icon", response_model=ServerIcon)
async def get_server_icon(
    principal: Principal = Depends(require(SETTINGS_VIEW)), ctx=Depends(get_server)
):
    """The server's server-icon.png in base64, or null when it has none."""
    path = gamesettings.icon_path(ctx)
    return {"png": base64.b64encode(path.read_bytes()).decode() if path else None}


@router.put("/server-icon", response_model=ServerIconSaved)
async def save_server_icon(
    payload: ServerIconRequest,
    request: Request,
    principal: Principal = Depends(require(SETTINGS_GAME)),
    ctx=Depends(get_server),
):
    """A new icon: a 64 x 64 PNG, already resized by the app."""
    try:
        data = base64.b64decode(payload.png, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="The picture didn't arrive whole.") from None
    result = await gamesettings.save_icon(ctx, data, user=principal.user)
    audit(ctx, request, "server_icon", detail="changed")
    return result


@router.delete("/server-icon", response_model=ServerIconSaved)
async def remove_server_icon(
    request: Request,
    principal: Principal = Depends(require(SETTINGS_GAME)),
    ctx=Depends(get_server),
):
    result = await gamesettings.save_icon(ctx, None, user=principal.user)
    audit(ctx, request, "server_icon", detail="removed")
    return result
