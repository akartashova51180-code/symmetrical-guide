# Событийный шлюз ChatGPT ↔ GitHub ↔ Codex ↔ Supabase

## Статус и границы

Шлюз принимает структурированные события, а не историю чатов. `sync_core.py` реализует локальный append-only журнал, контроль версий и идемпотентность; `sync_api.py` — авторизованные HTTP-операции и проверку подписи GitHub webhook. Этот код не запускает фоновые процессы и не создаёт интеграции в аккаунтах ChatGPT/Codex. Клиенты должны быть отдельно настроены на API и подтверждать получение через `/ack`.

Схема `supabase-sync-schema.sql` подготовлена, но не применяется автоматически. Не направляйте шлюз на рабочий проект до отдельного тестового развёртывания, настройки Auth/network policy и проверки резервной копии. Секрет GitHub webhook защищает только endpoint webhook; он не заменяет bearer-токен API. Файлы не содержат секретов.

## Событие

Обязательные поля: `event_id`, `project_id`, `source`, `source_ref`, `created_at` (ISO-8601 с часовым поясом), `version` (начиная с 1), `type`, `approval`, `privacy`, `text`. Дополнительные поля не принимаются в канонический payload: полный исходный payload можно расширить отдельным изменением протокола.

Один `event_id` может иметь последовательные версии. Повтор тех же `event_id` + `version` + содержимого возвращает `duplicate`. То же сочетание с другим содержимым и пропуск версии дают конфликт. Событие не удаляется после подтверждения; ACK фиксируется отдельно по направлению.

## Локальный запуск

Требуется Python 3.12 и зависимости проекта. Скопируйте файлы модуля в `system/runtime/corporate_brain/`, тесты — в `system/runtime/tests/`. Включите роутер из `sync_api.py` в `corporate_brain.app` и задайте:

```text
CORPORATE_BRAIN_SYNC_DB=/absolute/private/path/sync-journal.sqlite3
CORPORATE_BRAIN_SYNC_TOKEN=<случайный длинный секрет>
CORPORATE_BRAIN_GITHUB_WEBHOOK_SECRET=<отдельный секрет webhook>
```

Не коммитьте `.env`. Сначала запускайте только локально на `127.0.0.1`. У API сейчас нет отдельного управления пользователями и ролями: bearer-токен даёт полное право создавать события и ACK, поэтому храните его как серверный секрет.

```bash
uv sync --locked --extra test
uv run --locked pytest -q
uv run --locked uvicorn corporate_brain.app:app --host 127.0.0.1 --port 8787
```

## API

- `POST /api/sync/events` — принять событие; заголовок `Authorization: Bearer ...`.
- `GET /api/sync/pending/{destination}` — получить ожидающие события (`github`, `supabase`, `codex`, `chatgpt`).
- `POST /api/sync/events/{event_id}/{version}/ack/{destination}` — подтвердить доставку JSON `{"status":"delivered"}`; при ошибке `{"status":"failed","error":"..."}`.
- `GET /api/sync/events/{event_id}/versions` — список версий и хешей.
- `POST /api/sync/github-webhook` — проверка HMAC SHA-256 подписи webhook. Webhook является сигналом, он не превращает произвольное тело GitHub в утверждённое событие.

Пример тела события Issue #2 (тестовые публичные данные):

```json
{"event_id":"SYNC-TEST-20261119-01","project_id":"P00","source":"ChatGPT","source_ref":"https://github.com/akartashova51180-code/symmetrical-guide/issues/2","created_at":"2026-11-19T20:03:58Z","version":1,"type":"test_task","approval":"yes, test only","privacy":"public_synthetic","text":"Подтвердить доставку синтетического тестового события"}
```

## Supabase и GitHub

Чтобы хранить журнал в Supabase, сначала создайте отдельный тестовый проект и вручную примените подготовленный SQL, затем добавьте отдельный адаптер с service-role ключом в серверном окружении и проверкой хеша/version conflict. Такая интеграция здесь намеренно не включена: применение миграции или запись в основную базу не выполнялись. Для GitHub настройте webhook только на тестовый репозиторий и ограниченный набор событий; сам callback пока лишь проверяет подпись и отвечает, не импортируя тела Issue.

Автоматической сквозной синхронизации пока нет: понадобится адаптер GitHub для получения/публикации структурированного события и комментария ACK, Supabase transport и способ вызова API из конкретных чатов ChatGPT и Codex. Проверка через доступ к GitHub или комментарий Issue доказывает только факт чтения/записи там, но не межчатовую доставку.

