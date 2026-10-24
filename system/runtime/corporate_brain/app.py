from __future__ import annotations

import os
import hashlib
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .scheduler import start_scheduler
from .storage import (REGISTRIES, import_registries, initialize, list_notifications,
                      connect, list_records, list_runs, portfolio_summary, portfolio_overview, update_task, create_task,
                      create_idea, update_idea,
                      create_music_entry, update_music_entry, get_record,
                      store_song_asset, list_song_assets, get_song_asset)

RUNTIME_DIR = Path(__file__).resolve().parents[1]
SYSTEM_DIR = RUNTIME_DIR.parent
DATA_DIR = Path(os.environ.get("CORPORATE_BRAIN_DATA_DIR", RUNTIME_DIR / "data"))
DB_PATH = DATA_DIR / "portfolio.sqlite3"
SOURCE_DIR = Path(os.environ.get("CORPORATE_BRAIN_SOURCE_DIR", SYSTEM_DIR))
WEB_DIR = RUNTIME_DIR / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize(DB_PATH)
    with connect(DB_PATH) as db:
        existing = db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    if existing == 0:
        import_registries(DB_PATH, SOURCE_DIR)
    scheduler = start_scheduler(DB_PATH)
    app.state.scheduler = scheduler
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Мозг-администратор · локальное ядро", version="0.1.0", lifespan=lifespan)


class TaskPatch(BaseModel):
    status: str | None = None
    next_step: str | None = None
    due_date: str | None = None
    responsible: str | None = None

    def as_source_fields(self) -> dict[str, str]:
        mapping = {"status": "статус", "next_step": "следующий_шаг", "due_date": "срок", "responsible": "ответственный"}
        return {mapping[key]: value for key, value in self.model_dump(exclude_unset=True).items() if value is not None}


class MusicEntryBody(BaseModel):
    fields: dict[str, str]


class TaskCreate(BaseModel):
    project_id: str
    title: str
    next_step: str = ""
    due_date: str = ""
    responsible: str = "Анастасия / назначить"
    role: str = "secretary"


class IdeaBody(BaseModel):
    fields: dict[str, str]


class IdeaPatch(BaseModel):
    fields: dict[str, str]


ALLOWED_ASSET_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".aiff", ".aif", ".mid", ".midi", ".txt", ".pdf", ".docx", ".zip", ".png", ".jpg", ".jpeg"}
MAX_ASSET_BYTES = 100 * 1024 * 1024


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "local-only", "timezone": "Europe/Moscow"}


@app.get("/api/summary")
def summary() -> dict[str, Any]:
    return portfolio_summary(DB_PATH)


@app.get("/api/overview")
def overview() -> list[dict[str, Any]]:
    return portfolio_overview(DB_PATH)


@app.get("/api/ideas")
def ideas(limit: int = 200) -> list[dict[str, Any]]:
    return list_records(DB_PATH, "ideas", max(1, min(limit, 500)))


@app.post("/api/ideas")
def add_idea(body: IdeaBody) -> dict[str, Any]:
    try:
        return create_idea(DB_PATH, SOURCE_DIR, body.fields)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.patch("/api/ideas/{record_id}")
def patch_idea(record_id: str, body: IdeaPatch) -> dict[str, Any]:
    try:
        result = update_idea(DB_PATH, record_id, body.fields)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if result is None:
        raise HTTPException(404, "Запись входящего потока не найдена")
    return result


@app.get("/api/music/songs")
def music_songs(limit: int = 500) -> list[dict[str, Any]]:
    return list_records(DB_PATH, "music_songs", max(1, min(limit, 1000)))


@app.get("/api/music/campaigns")
def music_campaigns(limit: int = 500) -> list[dict[str, Any]]:
    return list_records(DB_PATH, "song_campaigns", max(1, min(limit, 1000)))


@app.get("/api/music/costs")
def music_costs(limit: int = 500) -> list[dict[str, Any]]:
    return list_records(DB_PATH, "music_costs", max(1, min(limit, 1000)))


@app.post("/api/music/{entity_type}")
def create_music_record(entity_type: str, body: MusicEntryBody) -> dict[str, Any]:
    if entity_type not in {"music_songs", "song_campaigns", "music_costs"}:
        raise HTTPException(404, "Неизвестный реестр музыкальной CRM")
    try:
        return create_music_entry(DB_PATH, SOURCE_DIR, entity_type, body.fields)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.patch("/api/music/{entity_type}/{record_id}")
def patch_music_record(entity_type: str, record_id: str, body: MusicEntryBody) -> dict[str, Any]:
    if entity_type not in {"music_songs", "song_campaigns", "music_costs"}:
        raise HTTPException(404, "Неизвестный реестр музыкальной CRM")
    try:
        result = update_music_entry(DB_PATH, SOURCE_DIR, entity_type, record_id, body.fields)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if result is None:
        raise HTTPException(404, "Запись не найдена")
    return result


@app.post("/api/music/songs/{song_id}/assets")
async def upload_song_asset(song_id: str, file: UploadFile = File(...), kind: str = Form("audio_master")) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", song_id):
        raise HTTPException(422, "Некорректный ID песни")
    if not get_record(DB_PATH, "music_songs", song_id):
        raise HTTPException(404, "Песня не найдена в общей CRM")
    if kind not in {"audio_master", "instrumental", "stem", "lyrics", "cover", "source_export", "other"}:
        raise HTTPException(422, "Неизвестный тип файла")
    original_name = Path(file.filename or "file").name
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_ASSET_EXTENSIONS:
        raise HTTPException(415, "Тип файла не поддерживается")
    folder = DATA_DIR / "assets" / song_id
    folder.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    target = folder / stored_name
    temporary = folder / f".{stored_name}.part"
    size = 0
    digest = hashlib.sha256()
    try:
        with temporary.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_ASSET_BYTES:
                    raise HTTPException(413, "Максимальный размер файла — 100 МБ")
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise HTTPException(422, "Пустой файл сохранить нельзя")
        temporary.replace(target)
        prior = next((asset for asset in list_song_assets(DB_PATH, song_id) if asset["sha256"] == digest.hexdigest()), None)
        if prior:
            target.unlink(missing_ok=True)
            asset_id = int(prior["id"])
        else:
            asset_id = store_song_asset(DB_PATH, song_id, kind, original_name, str(target.relative_to(DATA_DIR)),
                                        file.content_type, size, digest.hexdigest())
    except Exception:
        temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    stored = get_song_asset(DB_PATH, asset_id)
    return {"id": asset_id, "song_id": song_id, "kind": kind, "original_name": stored["original_name"],
            "size_bytes": stored["size_bytes"], "sha256": stored["sha256"], "uploaded_at": stored["uploaded_at"]}


@app.get("/api/music/songs/{song_id}/assets")
def song_assets(song_id: str) -> list[dict[str, Any]]:
    if not get_record(DB_PATH, "music_songs", song_id):
        raise HTTPException(404, "Песня не найдена в общей CRM")
    return [{k: v for k, v in asset.items() if k != "stored_path"} | {"download_url": f"/api/music/assets/{asset['id']}"}
            for asset in list_song_assets(DB_PATH, song_id)]


@app.get("/api/music/assets/{asset_id}")
def download_song_asset(asset_id: int) -> FileResponse:
    asset = get_song_asset(DB_PATH, asset_id)
    if asset is None:
        raise HTTPException(404, "Файл не найден")
    root = (DATA_DIR / "assets").resolve()
    path = (DATA_DIR / asset["stored_path"]).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "Файл не найден в хранилище")
    return FileResponse(path, media_type=asset["media_type"] or "application/octet-stream", filename=asset["original_name"])


@app.get("/api/records/{entity_type}")
def records(entity_type: str, limit: int = 500) -> list[dict[str, Any]]:
    if entity_type not in REGISTRIES:
        raise HTTPException(404, "Неизвестный тип реестра")
    return list_records(DB_PATH, entity_type, max(1, min(limit, 1000)))


@app.get("/api/notifications")
def notifications(limit: int = 100) -> list[dict[str, Any]]:
    return list_notifications(DB_PATH, max(1, min(limit, 500)))


@app.get("/api/job-runs")
def job_runs(limit: int = 50) -> list[dict[str, Any]]:
    return list_runs(DB_PATH, max(1, min(limit, 200)))


@app.post("/api/tasks/{record_id}")
def patch_task(record_id: str, patch: TaskPatch) -> dict[str, Any]:
    changes = patch.as_source_fields()
    if not changes:
        raise HTTPException(422, "Передайте хотя бы одно поле")
    try:
        result = update_task(DB_PATH, record_id, changes)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if result is None:
        raise HTTPException(404, "Задача не найдена")
    return result


@app.post("/api/tasks")
def add_task(task: TaskCreate) -> dict[str, Any]:
    fields = {"проект_id": task.project_id, "задача": task.title, "следующий_шаг": task.next_step,
              "срок": task.due_date, "ответственный": task.responsible, "роль": task.role}
    try:
        return create_task(DB_PATH, SOURCE_DIR, fields)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/import")
def refresh_import() -> dict[str, int]:
    return import_registries(DB_PATH, SOURCE_DIR)
