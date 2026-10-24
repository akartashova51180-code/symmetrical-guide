"""Opt-in polling receiver: the owner manually sends/forwards exports to this bot."""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from .storage import get_telegram_offset, get_telegram_state, set_telegram_offset, update_telegram_state

MAX_TELEGRAM_FILE_BYTES = 20 * 1024 * 1024
BOT_COMMANDS = [
    {"command": "start", "description": "Открыть меню"},
    {"command": "help", "description": "Команды и подсказки"},
    {"command": "summary", "description": "Сводка портфеля"},
    {"command": "projects", "description": "Список проектов или связи песни"},
    {"command": "tasks", "description": "Открытые задачи"},
    {"command": "idea", "description": "Записать идею во входящий поток"},
    {"command": "task", "description": "Добавить задачу проекту"},
    {"command": "songs", "description": "Общий каталог песен"},
    {"command": "new", "description": "Создать паспорт песни"},
    {"command": "source", "description": "Указать сервис генерации"},
    {"command": "link", "description": "Сохранить ссылку генерации"},
    {"command": "text", "description": "Добавить текст к песне"},
    {"command": "done", "description": "Закрыть активный паспорт"},
    {"command": "cancel", "description": "Отменить текущий ввод"},
]


class TelegramClient:
    def __init__(self, token: str) -> None:
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        self._token = token
        self._api = f"https://api.telegram.org/bot{token}/"
        self._file_api = f"https://api.telegram.org/file/bot{token}/"

    def call(self, method: str, payload: dict[str, Any], timeout: int = 35) -> Any:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(self._api + method, data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                result = json.loads(response.read())
        except Exception as exc:
            # Never echo request URLs: Bot API URLs contain the secret token.
            raise RuntimeError(f"Telegram {method} request failed ({type(exc).__name__})") from None
        if not result.get("ok"):
            raise RuntimeError(f"Telegram {method} returned an error")
        return result["result"]

    def get_file(self, file_id: str) -> dict[str, Any]:
        return self.call("getFile", {"file_id": file_id})

    def download_file(self, file_path: str) -> bytes:
        parts = Path(file_path).parts
        if not file_path or ".." in parts or file_path.startswith("/") or "\\" in file_path:
            raise ValueError("Telegram returned an invalid file path")
        url = self._file_api + urllib.parse.quote(file_path, safe="/")
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                length = response.headers.get("Content-Length")
                if length and int(length) > MAX_TELEGRAM_FILE_BYTES:
                    raise ValueError("Telegram cloud file-download limit is 20 MB; use CRM web upload for larger files")
                data = response.read(MAX_TELEGRAM_FILE_BYTES + 1)
        except ValueError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Telegram file download failed ({type(exc).__name__})") from None
        if len(data) > MAX_TELEGRAM_FILE_BYTES:
            raise ValueError("Telegram cloud file-download limit is 20 MB; use CRM web upload for larger files")
        return data

    def send_message(self, chat_id: str, text: str, *, show_menu: bool = False) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if show_menu:
            payload["reply_markup"] = {
                "keyboard": [
                    [{"text": "🌅 Сводка"}, {"text": "📁 Проекты"}],
                    [{"text": "✅ Задачи"}, {"text": "💡 Новая идея"}],
                    [{"text": "🎵 Каталог песен"}, {"text": "➕ Новая песня"}],
                    [{"text": "📎 Добавить материалы"}, {"text": "⏹ Завершить карточку"}],
                    [{"text": "❓ Помощь"}],
                ],
                "resize_keyboard": True,
                "is_persistent": True,
                "input_field_placeholder": "Выберите действие или отправьте файл",
            }
        self.call("sendMessage", payload)


class CRMClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8787") -> None:
        self.base_url = base_url.rstrip("/")

    def request(self, path: str, payload: dict[str, Any] | None = None, method: str = "GET") -> Any:
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        req = urllib.request.Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return json.loads(response.read())
        except Exception as exc:
            raise RuntimeError(f"Локальная CRM недоступна ({type(exc).__name__}); запустите основной сервис") from None

    def create_song(self, fields: dict[str, str]) -> dict[str, Any]:
        return self.request("/api/music/music_songs", {"fields": fields}, "POST")

    def create_idea(self, text: str) -> dict[str, Any]:
        return self.request("/api/ideas", {"fields": {"идея": text, "тип": "Идея из Telegram",
                           "источник_чат_GPT": "Telegram-приёмщик", "источник_проект": "P00"}}, "POST")

    def create_task(self, project_id: str, title: str, due_date: str = "", role: str = "secretary") -> dict[str, Any]:
        return self.request("/api/tasks", {"project_id": project_id, "title": title,
                           "due_date": due_date, "role": role}, "POST")

    def summary(self) -> dict[str, Any]:
        return self.request("/api/summary")

    def records(self, entity_type: str, limit: int = 100) -> list[dict[str, Any]]:
        return self.request(f"/api/records/{urllib.parse.quote(entity_type, safe='')}?limit={limit}")

    def patch_song(self, song_id: str, fields: dict[str, str]) -> dict[str, Any]:
        return self.request(f"/api/music/music_songs/{urllib.parse.quote(song_id, safe='')}", {"fields": fields}, "PATCH")

    def upload_asset(self, song_id: str, filename: str, kind: str, content: bytes, content_type: str) -> dict[str, Any]:
        boundary = uuid.uuid4().hex
        safe_name = Path(filename).name
        encoded_name = urllib.parse.quote(safe_name, safe="")
        chunks = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"kind\"\r\n\r\n{kind}\r\n".encode(),
            (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"upload{Path(safe_name).suffix}\"; filename*=UTF-8''{encoded_name}\r\n"
             f"Content-Type: {content_type}\r\n\r\n").encode(),
            content, f"\r\n--{boundary}--\r\n".encode(),
        ]
        req = urllib.request.Request(f"{self.base_url}/api/music/songs/{urllib.parse.quote(song_id, safe='')}/assets",
                                     data=b"".join(chunks), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read())
        except Exception as exc:
            raise RuntimeError(f"Не удалось сохранить файл в CRM ({type(exc).__name__})") from None


class IntakeProcessor:
    def __init__(self, db_path: Path, telegram: Any, crm: Any, allowed_user_ids: set[str]) -> None:
        self.db_path = db_path
        self.telegram = telegram
        self.crm = crm
        self.allowed_user_ids = allowed_user_ids

    def _reply(self, chat_id: str, text: str) -> None:
        try:
            self.telegram.send_message(chat_id, text)
        except Exception:
            # The underlying operation succeeded; do not replay the update and duplicate a song/file.
            pass

    def _reply_with_menu(self, chat_id: str, text: str) -> None:
        try:
            self.telegram.send_message(chat_id, text, show_menu=True)
        except TypeError:
            # Keep simple test doubles and alternative Telegram clients compatible.
            self._reply(chat_id, text)
        except Exception:
            pass

    def process(self, update: dict[str, Any]) -> None:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        chat_id = str(chat.get("id", ""))
        if chat.get("type") != "private" or str(sender.get("id", "")) not in self.allowed_user_ids:
            return
        state = get_telegram_state(self.db_path, chat_id)
        text = (message.get("text") or "").strip()
        button_actions = {
            "🌅 Сводка": "/summary",
            "📁 Проекты": "/projects",
            "✅ Задачи": "/tasks",
            "💡 Новая идея": "/idea",
            "🎵 Каталог песен": "/songs",
            "➕ Новая песня": "/new",
            "📎 Добавить материалы": "button_attach",
            "⏹ Завершить карточку": "/done",
            "❓ Помощь": "/help",
        }
        button_action = button_actions.get(text)
        if button_action == "button_attach":
            if state["active_song_id"]:
                self._reply(chat_id, f"Активна карточка {state['active_song_id']}. Отправьте сюда файл как документ или аудио; он прикрепится к этой песне.")
            else:
                self._reply(chat_id, "Сначала создайте паспорт кнопкой «➕ Новая песня» или командой /new Рабочее название.")
            return
        if button_action:
            text = button_action
        state = get_telegram_state(self.db_path, chat_id)
        if state["pending_action"] and text.startswith("/") and text not in {"/new", "/idea", "/cancel"}:
            update_telegram_state(self.db_path, chat_id, pending_action="")
            state = get_telegram_state(self.db_path, chat_id)
        if state["pending_action"] and text and not text.startswith("/"):
            pending_action = state["pending_action"]
            update_telegram_state(self.db_path, chat_id, pending_action="")
            if pending_action == "new_song":
                text = "/new " + text
            elif pending_action == "new_idea":
                idea = self.crm.create_idea(text)
                self._reply(chat_id, f"Идея сохранена во входящий поток: {idea['record_id']}. Она ожидает классификации и решения владелицы.")
                return
        if text.startswith("/start") or text.startswith("/help"):
            self._reply_with_menu(chat_id, "Корпоративный приёмщик. Через меню доступны сводка, проекты, задачи, входящий поток идей и общая CRM песен. Команды: /summary, /projects, /tasks, /idea, /task P02 | текст | YYYY-MM-DD, /songs, /new, /source Имя, /projects P09;P10, /link URL, /text текст, /done. Идеи из Telegram только записываются во входящий поток: их классифицирует и направляет владелица/мозг-администратор.")
            return
        if text.startswith("/summary"):
            report = self.crm.summary()
            counts = report.get("counts", {})
            self._reply(chat_id, (f"🌅 Сводка · {report.get('as_of', '')}\n"
                f"Проекты: {counts.get('projects', 0)} · открытые задачи: {report.get('tasks_open', 0)}\n"
                f"Просрочены: {report.get('tasks_overdue', 0)} · ближайшие 7 дней: {report.get('tasks_due_next_7_days', 0)}\n"
                f"Идеи на классификацию: {report.get('ideas_waiting_classification', 0)}\n"
                f"Воронка: {report.get('pipeline_records', 0)} записей · доход: {report.get('income_records', 0)} записей\n"
                f"{report.get('financial_history_status', '')}\n"
                "Пустой реестр не означает нулевой фактический доход."))
            return
        if text.startswith("/projects"):
            argument = text[len("/projects"):].strip()
            if argument:
                projects = argument.replace(" ", "")
                if not re.fullmatch(r"P\d{2}(;P\d{2})*", projects):
                    self._reply(chat_id, "Укажите ID проектов через ;, например /projects P09;P10")
                    return
                project_rows = self.crm.request("/api/records/projects")
                valid = {row["record_id"] for row in project_rows}
                if set(projects.split(";")) - valid:
                    self._reply(chat_id, "Один из ID проектов не найден в реестре. Проверьте ID и повторите команду.")
                    return
                update_telegram_state(self.db_path, chat_id, project_ids=projects)
                if state["active_song_id"]:
                    self.crm.patch_song(state["active_song_id"], {"связанные_проекты": projects})
                self._reply(chat_id, "Связи с проектами записаны. Карточка песни остаётся общей.")
                return
            rows = self.crm.request("/api/records/projects?limit=100")
            listing = "\n".join(f"{row['record_id']} · {row.get('title') or row['data'].get('проект', 'Без названия')} · {row.get('status') or row['data'].get('статус', '—')}" for row in rows[:20])
            self._reply(chat_id, f"📁 Проекты ({len(rows)}):\n{listing or 'Пока нет проектов'}")
            return
        if text.startswith("/tasks"):
            rows = self.crm.request("/api/records/tasks?limit=200")
            open_rows = [row for row in rows if not any(word in (row.get("status") or "").casefold() for word in ("готово", "выполнено", "закрыто", "отменено", "завершено"))]
            lines = [f"{row['record_id']} · {row.get('title') or 'Задача'} · {row.get('due_date') or 'срок не задан'} · {row.get('status') or '—'}" for row in open_rows[:15]]
            self._reply(chat_id, f"✅ Открытые задачи: {len(open_rows)}\n" + ("\n".join(lines) if lines else "Нет открытых задач."))
            return
        if text.startswith("/songs"):
            rows = self.crm.request("/api/music/songs?limit=100")
            lines = [f"{row['record_id']} · {row['data'].get('название', 'Без названия')} · {row.get('status') or row['data'].get('статус_сведений', '—')}" for row in rows[:15]]
            self._reply(chat_id, f"🎵 Общая CRM песен: {len(rows)}\n" + ("\n".join(lines) if lines else "Пока нет песен."))
            return
        if text == "/idea" or text.startswith("/idea "):
            idea_text = text[5:].strip()
            if not idea_text:
                update_telegram_state(self.db_path, chat_id, pending_action="new_idea")
                self._reply(chat_id, "Напишите идею одним сообщением. Я сохраню её во входящем потоке для последующей классификации; ничего не опубликую и не назначу автоматически. /cancel отменяет ввод.")
                return
            idea = self.crm.create_idea(idea_text)
            self._reply(chat_id, f"Идея сохранена во входящий поток: {idea['record_id']}. Она ожидает классификации и решения владелицы.")
            return
        if text.startswith("/task "):
            parts = [part.strip() for part in text[6:].split("|", 2)]
            if len(parts) < 2:
                self._reply(chat_id, "Формат: /task P02 | Что сделать | 2026-11-01. Срок можно не указывать.")
                return
            project_id, title = parts[:2]
            due_date = parts[2] if len(parts) == 3 else ""
            if not re.fullmatch(r"P\d{2}", project_id):
                self._reply(chat_id, "Первым укажите ID проекта, например P02.")
                return
            task = self.crm.create_task(project_id, title, due_date)
            self._reply(chat_id, f"Задача записана: {task['record_id']} · {project_id}. Проверьте назначение и срок в общей системе.")
            return
        if text == "/cancel":
            update_telegram_state(self.db_path, chat_id, pending_action="")
            self._reply(chat_id, "Текущий ввод отменён. Уже сохранённые карточки и файлы не удалены.")
            return
        if text == "/new" or text.startswith("/new "):
            title = text[4:].strip()
            if not title:
                update_telegram_state(self.db_path, chat_id, pending_action="new_song")
                self._reply(chat_id, "Напишите рабочее название песни одним сообщением. Для отмены нажмите /cancel.")
                return
            fields = {"название": title, "тип": "Песня", "связанные_проекты": state["project_ids"],
                      "площадка_создания": state["source_name"], "ссылка_на_проект_площадки": state["source_link"],
                      "статус_сведений": "Черновой паспорт через Telegram-приёмщик; проверить детали",
                      "материал_в_этой_среде": "Ожидает загрузки файла"}
            song = self.crm.create_song(fields)
            song_id = song["record_id"]
            update_telegram_state(self.db_path, chat_id, active_song_id=song_id, pending_action="")
            self._reply(chat_id, f"Создана карточка {song_id}. Отправьте сюда аудио/текст; файлы до 20 МБ будут добавлены в общий каталог.")
            return
        if text.startswith("/source "):
            source = text[8:].strip()[:200]
            if not source:
                self._reply(chat_id, "Укажите название стороннего AI-сервиса или Telegram-бота.")
                return
            update_telegram_state(self.db_path, chat_id, source_name=source)
            if state["active_song_id"]:
                self.crm.patch_song(state["active_song_id"], {"площадка_создания": source})
            self._reply(chat_id, "Источник сохранён для текущей и следующей карточки.")
            return
        if text.startswith("/link "):
            link = text[6:].strip()[:1000]
            update_telegram_state(self.db_path, chat_id, source_link=link)
            if state["active_song_id"]:
                self.crm.patch_song(state["active_song_id"], {"ссылка_на_проект_площадки": link})
            self._reply(chat_id, "Ссылка на исходную генерацию сохранена.")
            return
        if text.startswith("/text "):
            source_text = text[6:].strip()
            if not state["active_song_id"]:
                self._reply(chat_id, "Сначала создайте карточку командой /new Название.")
                return
            self.crm.patch_song(state["active_song_id"], {"промпт_или_источник_текста": source_text})
            self._reply(chat_id, f"Текст/заметка добавлены к {state['active_song_id']}.")
            return
        if text.startswith("/done"):
            update_telegram_state(self.db_path, chat_id, active_song_id="", pending_action="")
            self._reply(chat_id, "Текущая песня снята с приёма. Данные остаются в общем каталоге.")
            return
        media = message.get("audio") or message.get("document") or message.get("voice")
        if media:
            song_id = state["active_song_id"]
            if not song_id:
                self._reply(chat_id, "Сначала создайте карточку: /new Название. Так файл попадёт в нужный паспорт.")
                return
            filename = media.get("file_name") or ("telegram_voice.ogg" if message.get("voice") else "telegram_audio.mp3")
            suffix = Path(filename).suffix.lower()
            if suffix not in {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".aiff", ".aif", ".mid", ".midi", ".txt", ".pdf", ".docx", ".zip", ".png", ".jpg", ".jpeg"}:
                self._reply(chat_id, "Формат файла не поддерживается. Сохраните аудио в WAV/MP3/M4A/FLAC/OGG или добавьте его через веб-панель CRM.")
                return
            file_info = self.telegram.get_file(media["file_id"])
            if int(file_info.get("file_size", 0)) > MAX_TELEGRAM_FILE_BYTES:
                self._reply(chat_id, "Файл больше лимита Telegram Bot API для скачивания (20 МБ). Скачайте его на устройство и загрузите через веб-панель CRM (до 100 МБ).")
                return
            content = self.telegram.download_file(file_info["file_path"])
            kind = "lyrics" if suffix in {".txt", ".pdf", ".docx"} else "source_export" if suffix == ".zip" else "cover" if suffix in {".png", ".jpg", ".jpeg"} else "audio_master"
            saved = self.crm.upload_asset(song_id, filename, kind, content, media.get("mime_type") or "application/octet-stream")
            self.crm.patch_song(song_id, {"материал_в_этой_среде": f"Файл загружен в общую CRM: {filename}"})
            self._reply(chat_id, f"Файл добавлен к {song_id}: {filename} ({saved['size_bytes']} байт). Повторная загрузка того же файла в эту карточку не создаёт копию.")
            return
        if text:
            self._reply(chat_id, "Не распознала действие. Откройте меню /start. Для отмены ввода используйте /cancel; текст песни добавляется командой /text текст или файлом к активной карточке.")


class IntakeBot:
    def __init__(self, db_path: Path, telegram: TelegramClient, crm: CRMClient, allowed_user_ids: set[str]) -> None:
        self.db_path = db_path
        self.telegram = telegram
        self.processor = IntakeProcessor(db_path, telegram, crm, allowed_user_ids)

    def poll_once(self) -> int:
        offset = get_telegram_offset(self.db_path)
        updates = self.telegram.call("getUpdates", {"offset": offset, "timeout": 20, "allowed_updates": ["message"]}, timeout=35)
        processed = 0
        for update in updates:
            try:
                self.processor.process(update)
            except Exception as exc:
                print(f"Telegram update failed ({type(exc).__name__}); update will be acknowledged", file=sys.stderr)
                message = update.get("message") or {}
                chat = message.get("chat") or {}
                sender = message.get("from") or {}
                chat_id = str(chat.get("id", ""))
                if chat.get("type") == "private" and str(sender.get("id", "")) in self.processor.allowed_user_ids:
                    try:
                        self.telegram.send_message(chat_id, "Не получилось завершить действие. Проверьте введённые данные и попробуйте ещё раз; запись могла сохраниться частично, проверьте её в общей CRM.")
                    except Exception:
                        pass
            set_telegram_offset(self.db_path, int(update["update_id"]) + 1)
            processed += 1
        return processed

    def run_forever(self) -> None:
        while True:
            try:
                self.poll_once()
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"Telegram intake temporary error ({type(exc).__name__}); will retry", file=sys.stderr)
                time.sleep(5)


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    allowed = {item.strip() for item in os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").split(",") if item.strip()}
    if not allowed:
        raise SystemExit("Set TELEGRAM_ALLOWED_USER_IDS to your private numeric Telegram user ID; the receiver fails closed without it.")
    from .app import DB_PATH
    telegram = TelegramClient(token)
    telegram.call("setMyCommands", {"commands": BOT_COMMANDS})
    bot = IntakeBot(DB_PATH, telegram, CRMClient(os.environ.get("CORPORATE_BRAIN_API", "http://127.0.0.1:8787")), allowed)
    bot.run_forever()


if __name__ == "__main__":
    main()
