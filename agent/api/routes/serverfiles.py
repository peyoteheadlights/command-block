"""A server's own files: look, download, upload. Owner only (files.browse),
everything checked by agent/serverfiles.py."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from ... import serverfiles
from ...security.auth import Principal
from ...security.paths import PathSafetyError
from ...security.permissions import FILES_BROWSE, require
from ..deps import audit, get_server
from ..responses import ServerFiles, ServerFileUploaded

router = APIRouter()


@router.get("/files", response_model=ServerFiles)
async def server_files(
    path: str = "", principal: Principal = Depends(require(FILES_BROWSE)), ctx=Depends(get_server)
):
    """What's in one folder of the server's folder."""
    try:
        return await asyncio.to_thread(serverfiles.listing, ctx, path)
    except (PathSafetyError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get("/files/download")
async def download_server_file(
    path: str,
    request: Request,
    principal: Principal = Depends(require(FILES_BROWSE)),
    ctx=Depends(get_server),
):
    """One file from the server's folder, as it is (no JSON answer)."""
    try:
        found = await asyncio.to_thread(serverfiles.for_download, ctx, path)
    except (PathSafetyError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    audit(ctx, request, "file_download", target=path)
    return FileResponse(found, media_type="application/octet-stream", filename=found.name)


@router.post("/files/upload", response_model=ServerFileUploaded)
async def upload_server_file(
    request: Request,
    path: str = "",
    file: UploadFile = File(...),
    principal: Principal = Depends(require(FILES_BROWSE)),
    ctx=Depends(get_server),
):
    """Puts a file into a folder of the server's folder. Never replaces one."""
    data = await file.read(serverfiles.MAX_UPLOAD_BYTES + 1)
    try:
        saved = await asyncio.to_thread(
            serverfiles.save_upload, ctx, path, file.filename or "", data
        )
    except (PathSafetyError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    audit(ctx, request, "file_upload", target=saved["path"], detail=f"{saved['size']} bytes")
    return saved
