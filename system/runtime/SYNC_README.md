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
- `POST /api/sync/github-webhook` — проверка HMAC SHA-256 подписи webhook. Webhook принимает только явно размеченные синтетические тестовые события из доверенного репозитория и от разрешённого автора (см. ниже). Другие события игнорируются; webhook не утверждает бизнес-решения.

Пример тела события Issue #2 (тестовые публичные данные):

```json
{"event_id":"SYNC-TEST-20261119-01","project_id":"P00","source":"ChatGPT","source_ref":"https://github.com/akartashova51180-code/symmetrical-guide/issues/2","created_at":"2026-11-19T20:03:58Z","version":1,"type":"test_task","approval":"yes, test only","privacy":"public_synthetic","text":"Подтвердить доставку синтетического тестового события"}
```

## Supabase и GitHub

Чтобы хранить журнал в Supabase, сначала создайте отдельный тестовый проект и вручную примените подготовленный SQL, затем добавьте отдельный адаптер с service-role ключом в серверном окружении и проверкой хеша/version conflict. Такая интеграция здесь намеренно не включена: применение миграции или запись в основную базу не выполнялись. Для GitHub добавлен только opt-in приём синтетических событий; настройку webhook и доступ из интернета пока не выполнять. Для локального теста требуются переменные CORPORATE_BRAIN_GITHUB_REPO и CORPORATE_BRAIN_GITHUB_ACTORS. Входящий обработчик проверяет подпись GitHub, repo/sender/author и принимает один структурированный блок sync-event. Неизвестные или обычные Issue игнорируются.

Автоматической сквозной синхронизации пока нет: понадобится адаптер GitHub для получения/публикации структурированного события и комментария ACK, Supabase transport и способ вызова API из конкретных чатов ChatGPT и Codex. Проверка через доступ к GitHub или комментарий Issue доказывает только факт чтения/записи там, но не межчатовую доставку.


## Безопасный вход GitHub Issues (только тестовые события)

**По умолчанию выключен**: если не настроены `CORPORATE_BRAIN_GITHUB_REPO` и `CORPORATE_BRAIN_GITHUB_ACTORS`, webhook не создаёт событий. Для изолированной локальной проверки (НЕ открывать порт наружу):

```text
CORPORATE_BRAIN_GITHUB_REPO=akartashova51180-code/symmetrical-guide
CORPORATE_BRAIN_GITHUB_ACTORS=akartashova51180-code
```

Обработчик принимает только события `issues` с действием `opened` или `edited`, с проверенной HMAC-подписью, указанным репозиторием, доверенным отправителем и доверенным автором Issue. В тексте Issue должен быть **ровно один** блок:

```sync-event
{"event_id":"SYNC-TEST-EXAMPLE-01","project_id":"P00","source":"GitHub","source_ref":"https://github.com/akartashova51180-code/symmetrical-guide/issues/9","created_at":"2026-11-19T20:03:58Z","version":1,"type":"test_task","approval":"yes, test only","privacy":"public_synthetic","text":"Synthetic transport check"}
```

`source_ref` обязан совпадать с реальным URL Issue из подписанного GitHub payload. Реальные решения, резюме, договоры и другие закрытые данные таким путём не принимаются. `ACK` всё ещё является отметкой клиента, а не криптографическим доказательством получения: до отдельного адаптера и фактического подтверждения автоматическая доставка в Codex или ChatGPT **не работает**.

GitHub Actions проверяет алгоритмы при коммитах в разработческую ветку. Для тестов не нужны ключи Supabase, GitHub или сервер Timeweb.

## Подготовленный адаптер Supabase (не развёрнут)

`corporate_brain/supabase_transport.py` формирует строки событий для двух таблиц из `supabase-sync-schema.sql` и проверяет удалённый `payload_sha256` после записи. Ключ `(event_id, version)` обрабатывается как неизменяемый: совпадающее содержимое считается повтором, отличающееся — конфликтом, который не перезаписывается.

`corporate_brain/sync_worker.py` — **ручной одноразовый обработчик** очереди `supabase`. Подтверждение ACK устанавливается только после совпадения контрольной суммы события с данными удалённой базы. Ошибки сохраняют запись в локальной очереди для повторной попытки. Это не фоновый планировщик: запущенный вручную процесс обрабатывает одну порцию событий и завершает работу.

Обе части **по умолчанию выключены**. Для изолированного тестового проекта, после отдельного согласования и применения подготовленной схемы, потребуются переменные окружения:

```text
CORPORATE_BRAIN_SUPABASE_SYNC_ENABLED=true
CORPORATE_BRAIN_SUPABASE_SYNC_URL=https://TEST_PROJECT_REF.supabase.co
CORPORATE_BRAIN_SUPABASE_SYNC_SECRET_KEY=<server-only-secret>
CORPORATE_BRAIN_SYNC_DB=/private/existing/path/sync-journal.sqlite3
```

Не подставлять URL основной `kartashova-brain` или рабочие ключи в тестовую конфигурацию. Не коммитить значения переменных окружения и не помещать их в браузер/публичные GitHub Actions. Вторая копия проекта и настройка секретов ещё **не создавались**. На этом этапе не запускайте обработчик против существующей `kartashova-brain-test` без проверки схемы, RLS и изоляции доступа.

Когда тестовая база будет отдельно разрешена и защищена, однократный запуск из каталога `system/runtime`:
```bash
python -m corporate_brain.sync_worker
```

Это не подключение ChatGPT/Codex к Supabase и не автоматический запуск Codex. Без утверждённого оператора/адаптеров синхронизация между чатами остаётся частично ручной. В GitHub Actions используются только фальшивые ответы Supabase REST, без сетевых запросов и секретов.

## Интеграционные тесты SQL без доступа к Supabase

При коммите в ветку разработки GitHub Actions поднимает **одноразовый контейнер PostgreSQL 16**, применяет точно тот SQL-файл `supabase-sync-schema.sql` и проверяет:

- обе таблицы созданы и RLS включена;
- публичных политик доступа нет;
- повторная запись того же `event_id + version` не создаёт дубль;
- конфликтующая повторная запись не изменяет исходное событие;
- ссылка `sync_deliveries` на `sync_events` поддерживается.

Тесты находятся в `tests/test_sync_postgres_integration.py`, запуск включён в `.github/workflows/sync-tests.yml`.
Это **настоящая проверка схемы PostgreSQL, но не тест соединения с Supabase REST API**. В облачную Supabase не записывается ничего. Для следующей проверки с Data API нужен отдельно согласованный тестовый проект, ограниченные ключи, проверка RLS и сетевых настроек.
