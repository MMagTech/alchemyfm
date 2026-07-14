from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse

from app.auth import require_admin
from app.services.logs import (
    LOG_FILES,
    clear_log,
    create_logs_zip,
    list_logs,
    log_path,
    tail_log,
)

router = APIRouter(
    prefix="/api/admin/logs",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


def _require_known_log(name: str) -> None:
    if name not in LOG_FILES:
        raise HTTPException(status_code=404, detail=f"Unknown log: {name}")


@router.get("")
def read_logs():
    return {"logs": list_logs()}


@router.get("/download-all")
def download_all_logs():
    zip_path = create_logs_zip()
    return FileResponse(
        zip_path, filename="alchemyfm-logs.zip", media_type="application/zip"
    )


@router.get("/{name}/tail", response_class=PlainTextResponse)
def read_log_tail(name: str, lines: int = Query(default=200, ge=1, le=2000)):
    _require_known_log(name)
    return tail_log(name, lines=lines)


@router.get("/{name}/download")
def download_log(name: str):
    _require_known_log(name)
    path = log_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Log file does not exist yet")
    return FileResponse(path, filename=LOG_FILES[name], media_type="text/plain")


@router.post("/{name}/clear")
def clear_log_endpoint(name: str):
    _require_known_log(name)
    clear_log(name)
    return {"ok": True}
