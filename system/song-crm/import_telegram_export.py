#!/usr/bin/env python3
"""Index Telegram Desktop JSON exports and their media into a reviewable CSV.

This is a local, read-only staging step. It never uploads media or creates CRM
records; song matching and quality decisions remain for the owner to confirm.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


FIELDS = [
    "source_channel", "source_channel_url", "telegram_message_id", "message_date",
    "message_caption", "original_filename", "file_sha256", "archive_path",
    "proposed_song_id", "proposed_bundle", "asset_role", "version_status",
    "anastasia_quality", "quality_reason", "rights_status", "review_status", "notes",
]


def flatten_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(flatten_text(item) for item in value)
    if isinstance(value, dict):
        return str(value.get("text", ""))
    return ""


def find_exports(root: Path) -> list[Path]:
    candidates = [p for p in root.rglob("*.json") if p.name == "result.json" or p.name.startswith("messages")]
    valid = []
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("messages"), list):
            valid.append(path)
    return sorted(valid)


def message_media(message: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for key in ("file", "photo", "thumbnail"):
        value = message.get(key)
        if isinstance(value, str):
            found.append(value)
    for item in message.get("media", []) if isinstance(message.get("media"), list) else []:
        if isinstance(item, dict):
            path = item.get("file") or item.get("path")
            if isinstance(path, str):
                found.append(path)
    return list(dict.fromkeys(found))


def classify(path: Path, message: dict[str, Any]) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".pdf", ".doc", ".docx", ".rtf"}:
        return "lyrics_or_document_review"
    if suffix in {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".aiff", ".aif"}:
        mime = str(message.get("mime_type", "")).lower()
        return "audio_review" + ("; inspect possible instrumental/zero-version" if "audio" in mime else "")
    if suffix == ".zip":
        return "source_export_or_stems_review"
    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return "cover_or_image_review"
    return "other_review"


def build_rows(root: Path, channel_url: str = "") -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for export in find_exports(root):
        data = json.loads(export.read_text(encoding="utf-8-sig"))
        channel = str(data.get("name") or data.get("title") or export.parent.name)
        for message in data["messages"]:
            if not isinstance(message, dict):
                continue
            media = message_media(message)
            caption = flatten_text(message.get("text", ""))
            # Include text-only messages as contextual entries; never label them as a song lyric automatically.
            if not media and not caption.strip():
                continue
            base = {
                "source_channel": channel,
                "source_channel_url": channel_url,
                "telegram_message_id": str(message.get("id", "")),
                "message_date": str(message.get("date", "")),
                "message_caption": caption,
                "proposed_song_id": "",
                "proposed_bundle": "",
                "version_status": "Проверить",
                "anastasia_quality": "",
                "quality_reason": "",
                "rights_status": "Не проверено",
                "review_status": "Новая запись",
                "notes": "Текст сообщения — контекст источника, не подтверждённый текст песни.",
            }
            if not media:
                rows.append(base | {"original_filename": "", "file_sha256": "", "archive_path": "", "asset_role": "message_context_review"})
                continue
            for raw_path in media:
                rel = Path(raw_path)
                if rel.is_absolute() or ".." in rel.parts:
                    rows.append(base | {"original_filename": rel.name, "file_sha256": "", "archive_path": raw_path, "asset_role": "unsafe_path_review", "review_status": "Путь требует проверки"})
                    continue
                candidate = (export.parent / rel).resolve()
                try:
                    candidate.relative_to(root.resolve())
                except ValueError:
                    rows.append(base | {"original_filename": rel.name, "file_sha256": "", "archive_path": raw_path, "asset_role": "unsafe_path_review", "review_status": "Путь требует проверки"})
                    continue
                digest = hashlib.sha256()
                if candidate.is_file():
                    with candidate.open("rb") as source:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            digest.update(chunk)
                rows.append(base | {
                    "original_filename": candidate.name,
                    "file_sha256": digest.hexdigest() if candidate.is_file() else "",
                    "archive_path": str(candidate.relative_to(root.resolve())) if candidate.is_file() else raw_path,
                    "asset_role": classify(candidate, message),
                    "review_status": "Готово к сверке" if candidate.is_file() else "Файл не найден в экспорте",
                })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export_root", type=Path, help="Folder containing extracted Telegram Desktop export(s)")
    parser.add_argument("--output", type=Path, default=Path("telegram-intake-review.csv"))
    parser.add_argument("--channel-url", default="", help="Optional public channel URL, if known")
    args = parser.parse_args()
    root = args.export_root.resolve()
    if not root.is_dir():
        parser.error("export_root must be an existing directory")
    rows = build_rows(root, args.channel_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Indexed {len(rows)} messages/files into {args.output}; no files were uploaded or changed.")


if __name__ == "__main__":
    main()
