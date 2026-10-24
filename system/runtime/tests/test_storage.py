import csv
import json
from datetime import date

from corporate_brain.storage import import_registries, initialize, list_records, portfolio_summary, update_task, portfolio_overview, create_task
from corporate_brain.scheduler import run_morning_cycle


def write_csv(path, headers, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def test_import_preserves_all_fields_and_summary_does_not_infer_zero_income(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    write_csv(source / "projects.csv", ["id", "проект", "статус"], [{"id": "P1", "проект": "Рекрутинг", "статус": "Активен"}])
    write_csv(source / "tasks.csv", ["id", "проект_id", "задача", "статус", "срок", "следующий_шаг"], [
        {"id": "T1", "проект_id": "P1", "задача": "Позвонить", "статус": "Открыта", "срок": "2026-10-22", "следующий_шаг": "Подготовить вопросы"},
        {"id": "T2", "проект_id": "P1", "задача": "Не согласована", "статус": "Открыта", "срок": "Не согласован", "следующий_шаг": "Уточнить"},
    ])
    db = tmp_path / "db.sqlite3"
    initialize(db)
    counts = import_registries(db, source)
    assert counts["projects"] == 1 and counts["tasks"] == 2
    imported = list_records(db, "tasks")
    assert imported[0]["data"]["следующий_шаг"] == "Подготовить вопросы"
    summary = portfolio_summary(db, date(2026, 10, 23))
    assert summary["tasks_overdue"] == 1
    assert summary["income_records"] == 0
    assert "нет записей" in summary["financial_history_status"]


def test_daily_cycle_is_idempotent_and_does_not_send_external_messages(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    write_csv(source / "tasks.csv", ["id", "проект_id", "задача", "статус", "срок"], [
        {"id": "T1", "проект_id": "P1", "задача": "Сделать", "статус": "Открыта", "срок": "2026-10-23"},
    ])
    db = tmp_path / "db.sqlite3"
    initialize(db)
    import_registries(db, source)
    from datetime import datetime
    from zoneinfo import ZoneInfo
    when = datetime(2026, 10, 23, 9, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    first = run_morning_cycle(db, when)
    second = run_morning_cycle(db, when)
    assert first["notifications_created"] == 3  # overdue, daily brief, Friday audit agenda
    assert second["notifications_created"] == 0
    assert first["external_delivery"] is False


def test_task_updates_validate_due_date(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    write_csv(source / "tasks.csv", ["id", "проект_id", "задача", "статус", "срок"], [
        {"id": "T1", "проект_id": "P1", "задача": "Сделать", "статус": "Открыта", "срок": ""},
    ])
    db = tmp_path / "db.sqlite3"
    initialize(db)
    import_registries(db, source)
    import pytest
    with pytest.raises(ValueError):
        update_task(db, "T1", {"срок": "завтра"})
    updated = update_task(db, "T1", {"срок": "2026-11-01", "статус": "В работе"})
    assert updated["due_date"] == "2026-11-01"
    assert json.loads(json.dumps(updated["data"], ensure_ascii=False))["статус"] == "В работе"


def test_owner_can_create_project_task_and_overview_shows_it(tmp_path):
    source=tmp_path/"source";source.mkdir()
    write_csv(source/"projects.csv",["id","проект","цель","статус","следующий_шаг","неизвестное"],[
        {"id":"P1","проект":"Агентство","цель":"Получать заявки","статус":"Активен","следующий_шаг":"Исследовать","неизвестное":"Источники спроса"}])
    write_csv(source/"tasks.csv",["id","проект_id","задача","статус","следующий_шаг","срок","ответственный","роль"],[])
    db=tmp_path/"db.sqlite3";initialize(db);import_registries(db,source)
    task=create_task(db,source,{"проект_id":"P1","задача":"Проверить канал","следующий_шаг":"Собрать обращения","срок":"2026-10-30","роль":"market"})
    assert task["record_id"]=="T01" and task["data"]["ответственный"]=="Анастасия / назначить"
    card=portfolio_overview(db,date(2026,10,30))[0]
    assert card["open_task_count"]==1 and card["due_today"]==1
    assert card["resource_gaps"]=="Источники спроса"


def test_weekly_review_agenda_is_internal_and_idempotent(tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    db=tmp_path/"db.sqlite3";initialize(db)
    monday=datetime(2026,10,26,9,0,tzinfo=ZoneInfo("Europe/Moscow"))
    result=run_morning_cycle(db,monday)
    assert result["weekly_review"]=="weekly_monday"
    assert result["external_delivery"] is False
    kinds=[n["kind"] for n in __import__("corporate_brain.storage",fromlist=["list_notifications"]).list_notifications(db)]
    assert "weekly_monday" in kinds
    assert run_morning_cycle(db,monday)["notifications_created"]==0


def test_telegram_notification_outbox_retries_and_only_sends_to_allowlisted_owner(tmp_path):
    from corporate_brain.storage import create_notification, initialize, pending_telegram_notifications
    from corporate_brain.telegram_notifications import deliver_pending_telegram
    db=tmp_path/"db.sqlite3";initialize(db)
    create_notification(db,"daily_brief:2026-10-23","daily_brief",{
        "as_of":"2026-10-23","counts":{"projects":3},"tasks_open":2,"tasks_overdue":1,
        "tasks_due_next_7_days":4,"pipeline_records":0,"income_records":0,
        "financial_history_status":"Нет внесённых записей"})
    config={"TELEGRAM_BOT_TOKEN":"test-secret","TELEGRAM_NOTIFY_CHAT_ID":"42","TELEGRAM_ALLOWED_USER_IDS":"42"}
    sent=[]
    result=deliver_pending_telegram(db,environ=config,sender=lambda token,chat,text: sent.append((token,chat,text)))
    assert result["sent"]==1 and len(sent)==1
    assert "пустой реестр" in sent[0][2]
    assert pending_telegram_notifications(db)==[]
    create_notification(db,"weekly:2026-10-23","weekly_friday",{"title":"Аудит","agenda":"Проверить итоги"})
    denied=deliver_pending_telegram(db,environ={**config,"TELEGRAM_NOTIFY_CHAT_ID":"99"},sender=lambda *_: sent.append("unexpected"))
    assert denied["error"]=="chat_not_allowlisted" and len(sent)==1

    def fail(*_): raise RuntimeError("private failure detail")
    failed=deliver_pending_telegram(db,environ=config,sender=fail)
    assert failed["sent"]==0 and failed["error"]=="RuntimeError"
    assert pending_telegram_notifications(db)[0]["attempts"]==1
    retried=deliver_pending_telegram(db,environ=config,sender=lambda *_: None)
    assert retried["sent"]==1 and pending_telegram_notifications(db)==[]


def test_sqlite_snapshot_includes_committed_data_and_passes_integrity_check(tmp_path):
    import sqlite3
    from corporate_brain.backup_sqlite_snapshot import create_snapshot
    source=tmp_path/"live.sqlite3"; snapshot=tmp_path/"safe-copy"/"snapshot.sqlite3"
    initialize(source)
    with sqlite3.connect(source) as db:
        db.execute("INSERT INTO records(entity_type,record_id,payload_json,updated_at) VALUES ('projects','P1','{}','now')")
    create_snapshot(source,snapshot)
    with sqlite3.connect(snapshot) as db:
        assert db.execute("SELECT record_id FROM records").fetchone()[0]=="P1"
        assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"


def test_cross_project_idea_inbox_can_classify_and_route_by_affected_roles(tmp_path):
    from corporate_brain.storage import create_idea, update_idea
    source=tmp_path/"source";source.mkdir()
    write_csv(source/"ideas.csv",["id","дата","источник_проект","источник_чат_GPT","тип","идея","решение_владелицы","факты","гипотезы","предложенный_проект","роль","монетизация","срок","основание_срока","статус","следующий_шаг","блокер","влияние_на_проекты","маршрутизация","обоснование_маршрута"],[])
    write_csv(source/"projects.csv",["id","проект"],[{"id":"P03","проект":"Контент"}])
    db=tmp_path/"db.sqlite3";initialize(db);import_registries(db,source)
    item=create_idea(db,source,{"идея":"Подготовить статью","источник_проект":"P03","источник_чат_GPT":"Контентный GPT"})
    assert item["record_id"]=="I01" and item["status"]=="На классификацию"
    assert portfolio_summary(db)["ideas_waiting_classification"]==1
    routed=update_idea(db,"I01",{"статус":"Передано в проект","влияние_на_проекты":"P03","маршрутизация":"content_editor; market","обоснование_маршрута":"Нужны текст и проверка аудитории"})
    assert routed["data"]["маршрутизация"]=="content_editor; market"
    assert routed["project_id"]=="P03"
    assert portfolio_summary(db)["ideas_waiting_classification"]==0


def test_shared_music_crm_creates_one_song_with_multiple_project_links_and_campaign(tmp_path):
    from corporate_brain.storage import create_music_entry, update_music_entry
    source = tmp_path / "source"
    source.mkdir()
    write_csv(source / "creative-catalog.csv", ["id", "название", "тип", "связанные_проекты", "площадка_создания"], [])
    write_csv(source / "song-campaigns.csv", ["campaign_id", "song_id", "project_id", "цель_кампании", "статус"], [])
    write_csv(source / "music-costs.csv", ["cost_id", "сервис_или_бот", "тип_платежа", "сумма", "валюта", "song_id"], [])
    db = tmp_path / "db.sqlite3"
    initialize(db)
    import_registries(db, source)
    song = create_music_entry(db, source, "music_songs", {
        "название": "Черновой трек", "тип": "Песня", "связанные_проекты": "P09;P10",
        "площадка_создания": "Платный Telegram-бот"})
    assert song["record_id"] == "C01"
    assert song["project_id"] == "P09;P10"
    campaign = create_music_entry(db, source, "song_campaigns", {
        "song_id": "C01", "project_id": "P10", "цель_кампании": "Проверить спрос", "статус": "Черновик"})
    assert campaign["data"]["song_id"] == song["record_id"]
    updated = update_music_entry(db, source, "music_songs", "C01", {"площадка_создания": "Бот в Telegram"})
    assert updated["data"]["связанные_проекты"] == "P09;P10"
    cost = create_music_entry(db, source, "music_costs", {
        "сервис_или_бот": "Платный Telegram-бот", "тип_платежа": "Подписка",
        "сумма": "990", "валюта": "RUB", "song_id": ""})
    assert cost["record_id"] == "MC01"


def test_music_asset_upload_and_download_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from corporate_brain import app as app_module
    source = tmp_path / "source"
    source.mkdir()
    write_csv(source / "creative-catalog.csv", ["id", "название", "тип", "связанные_проекты"], [
        {"id": "C01", "название": "Тестовая песня", "тип": "Песня", "связанные_проекты": "P09"},
    ])
    db_dir = tmp_path / "data"
    monkeypatch.setattr(app_module, "DB_PATH", db_dir / "portfolio.sqlite3")
    monkeypatch.setattr(app_module, "DATA_DIR", db_dir)
    monkeypatch.setattr(app_module, "SOURCE_DIR", source)
    with TestClient(app_module.app) as client:
        uploaded = client.post("/api/music/songs/C01/assets", data={"kind": "audio_master"},
                               files={"file": ("demo.wav", b"RIFF-demo-audio", "audio/wav")})
        assert uploaded.status_code == 200
        asset_id = uploaded.json()["id"]
        listing = client.get("/api/music/songs/C01/assets").json()
        assert listing[0]["original_name"] == "demo.wav"
        response = client.get(f"/api/music/assets/{asset_id}")
        assert response.status_code == 200 and response.content == b"RIFF-demo-audio"
        rejected = client.post("/api/music/songs/C01/assets", data={"kind": "audio_master"},
                              files={"file": ("bad.exe", b"nope", "application/octet-stream")})
        assert rejected.status_code == 415


def test_idea_inbox_api_accepts_and_routes_owner_discussion(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from corporate_brain import app as app_module
    source=tmp_path/"source";source.mkdir()
    write_csv(source/"ideas.csv",["id","дата","источник_проект","источник_чат_GPT","тип","идея","решение_владелицы","факты","гипотезы","предложенный_проект","роль","монетизация","срок","основание_срока","статус","следующий_шаг","блокер","влияние_на_проекты","маршрутизация","обоснование_маршрута"],[])
    write_csv(source/"projects.csv",["id","проект"],[{"id":"P03","проект":"Контент"}])
    dbdir=tmp_path/"data"
    monkeypatch.setattr(app_module,"DB_PATH",dbdir/"portfolio.sqlite3")
    monkeypatch.setattr(app_module,"DATA_DIR",dbdir)
    monkeypatch.setattr(app_module,"SOURCE_DIR",source)
    with TestClient(app_module.app) as client:
        created=client.post("/api/ideas",json={"fields":{"идея":"Написать статью","источник_проект":"P03","источник_чат_GPT":"GPT контента"}})
        assert created.status_code==200 and created.json()["record_id"]=="I01"
        routed=client.patch("/api/ideas/I01",json={"fields":{"статус":"Передано в проект","маршрутизация":"content_editor; market","влияние_на_проекты":"P03"}})
        assert routed.status_code==200
        assert client.get("/api/ideas").json()[0]["data"]["маршрутизация"]=="content_editor; market"


def test_telegram_intake_routes_owner_export_to_one_shared_song(tmp_path):
    from corporate_brain.storage import get_telegram_state, initialize
    from corporate_brain.telegram_intake import IntakeProcessor

    class FakeTelegram:
        def __init__(self): self.replies=[]
        def send_message(self, chat_id, text): self.replies.append(text)
        def get_file(self, file_id): return {"file_path":"documents/song.wav", "file_size":10}
        def download_file(self, file_path): return b"RIFF-audio"

    class FakeCRM:
        def __init__(self): self.song=None; self.assets=[]
        def request(self, path): return [{"record_id":"P09"},{"record_id":"P10"}]
        def create_song(self, fields):
            self.song={"record_id":"C77","data":fields}; return self.song
        def patch_song(self, song_id, fields): self.song["data"].update(fields); return self.song
        def upload_asset(self, song_id, filename, kind, content, content_type):
            self.assets.append((song_id,filename,kind,content)); return {"size_bytes":len(content)}

    db=tmp_path/"db.sqlite3";initialize(db)
    tg=FakeTelegram();crm=FakeCRM();receiver=IntakeProcessor(db,tg,crm,{"42"})
    def update(uid,text=None,**extra):
        message={"chat":{"id":42,"type":"private"},"from":{"id":uid}}
        if text is not None: message["text"]=text
        message.update(extra)
        return {"message":message}
    receiver.process(update(42,"/source Paid song bot"))
    receiver.process(update(42,"/projects P09;P10"))
    receiver.process(update(42,"/new Москва, новый припев"))
    receiver.process(update(42,None,document={"file_id":"file-1","file_name":"demo.wav","mime_type":"audio/wav"}))
    state=get_telegram_state(db,"42")
    assert state["active_song_id"]=="C77"
    assert state["project_ids"]=="P09;P10"
    assert crm.song["data"]["площадка_создания"]=="Paid song bot"
    assert crm.assets==[("C77","demo.wav","audio_master",b"RIFF-audio")]
    count=len(crm.assets)
    receiver.process(update(99,"/new чужая песня"))
    assert len(crm.assets)==count and len(tg.replies)==4


def test_telegram_intake_shows_menu_and_button_guidance(tmp_path):
    from corporate_brain.storage import initialize
    from corporate_brain.telegram_intake import IntakeProcessor

    class FakeTelegram:
        def __init__(self): self.messages=[]
        def send_message(self, chat_id, text, *, show_menu=False):
            self.messages.append((chat_id,text,show_menu))

    class FakeCRM:
        def request(self, path): return []

    db=tmp_path/"db.sqlite3";initialize(db)
    tg=FakeTelegram();receiver=IntakeProcessor(db,tg,FakeCRM(),{"42"})
    receiver.process({"message":{"chat":{"id":42,"type":"private"},"from":{"id":42},"text":"/start"}})
    receiver.process({"message":{"chat":{"id":42,"type":"private"},"from":{"id":42},"text":"➕ Новая песня"}})
    assert tg.messages[0][2] is True
    assert "общая CRM песен" in tg.messages[0][1]
    assert "название песни" in tg.messages[1][1]


def test_telegram_brain_menu_captures_ideas_tasks_and_summary(tmp_path):
    from corporate_brain.storage import get_telegram_state, initialize
    from corporate_brain.telegram_intake import IntakeProcessor

    class FakeTelegram:
        def __init__(self): self.messages=[]
        def send_message(self, chat_id, text, *, show_menu=False): self.messages.append((text,show_menu))

    class FakeCRM:
        def __init__(self): self.created_ideas=[];self.created_tasks=[]
        def summary(self): return {"as_of":"2026-10-24","counts":{"projects":13},"tasks_open":8,"tasks_overdue":1,"tasks_due_next_7_days":2,"ideas_waiting_classification":3,"pipeline_records":0,"income_records":0,"financial_history_status":"нет записей"}
        def request(self,path,payload=None,method="GET"):
            if path=="/api/records/projects": return [{"record_id":"P02","title":"HR","data":{"проект":"HR"}}]
            if path=="/api/records/tasks?limit=200": return []
            if path=="/api/music/songs?limit=100": return []
            if path=="/api/ideas" and method=="POST":
                self.created_ideas.append(payload["fields"]);return {"record_id":"I77"}
            if path=="/api/tasks" and method=="POST":
                self.created_tasks.append(payload);return {"record_id":"T77"}
            raise AssertionError(path)
        def create_idea(self,text):
            return self.request("/api/ideas",{"fields":{"идея":text}},"POST")
        def create_task(self,project_id,title,due_date="",role="secretary"):
            return self.request("/api/tasks",{"project_id":project_id,"title":title,"due_date":due_date,"role":role},"POST")
        def create_song(self,fields): return {"record_id":"C77","data":fields}
        def patch_song(self,*_): return {}
        def upload_asset(self,*_): return {"size_bytes":10}

    db=tmp_path/"db.sqlite3";initialize(db)
    tg=FakeTelegram();crm=FakeCRM();receiver=IntakeProcessor(db,tg,crm,{"42"})
    def update(text): return {"message":{"chat":{"id":42,"type":"private"},"from":{"id":42},"text":text}}
    receiver.process(update("💡 Новая идея"))
    assert get_telegram_state(db,"42")["pending_action"]=="new_idea"
    receiver.process(update("Проверить спрос на кадровый аудит"))
    assert crm.created_ideas[0]["идея"]=="Проверить спрос на кадровый аудит"
    assert "I77" in tg.messages[-1][0]
    receiver.process(update("/task P02 | Проверить отклики | 2026-11-01"))
    assert crm.created_tasks[0]["project_id"]=="P02" and crm.created_tasks[0]["due_date"]=="2026-11-01"
    receiver.process(update("🌅 Сводка"))
    assert "Просрочены: 1" in tg.messages[-1][0]
    receiver.process(update("➕ Новая песня"))
    assert get_telegram_state(db,"42")["pending_action"]=="new_song"
    receiver.process(update("Тестовая мелодия"))
    assert get_telegram_state(db,"42")["active_song_id"]=="C77"


def test_telegram_publishes_menu_keyboard_and_valid_bot_command_names():
    from corporate_brain.telegram_intake import BOT_COMMANDS, TelegramClient
    client=TelegramClient("placeholder-token")
    requests=[]
    client.call=lambda method,payload: requests.append((method,payload))
    client.send_message("42","menu",show_menu=True)
    assert requests[0][0]=="sendMessage"
    keyboard=requests[0][1]["reply_markup"]["keyboard"]
    labels={button["text"] for row in keyboard for button in row}
    assert {"🌅 Сводка","📁 Проекты","✅ Задачи","💡 Новая идея","🎵 Каталог песен","➕ Новая песня"} <= labels
    assert len(BOT_COMMANDS)==len({item["command"] for item in BOT_COMMANDS})
    assert all(item["command"].isascii() and item["command"].islower() and len(item["command"])<=32 for item in BOT_COMMANDS)


def test_owner_id_probe_only_selects_private_start_updates():
    from corporate_brain.telegram_owner_id import find_private_starters
    updates=[
        {"message":{"chat":{"id":42,"type":"private"},"from":{"id":42,"username":"anastasia"},"text":"/start"}},
        {"message":{"chat":{"id":77,"type":"private"},"from":{"id":77},"text":"hello"}},
        {"message":{"chat":{"id":-100,"type":"supergroup"},"from":{"id":42},"text":"/start"}},
    ]
    assert find_private_starters(updates)==[("42","anastasia")]


def test_initialize_adds_pending_action_to_existing_telegram_state(tmp_path):
    import sqlite3
    from corporate_brain.storage import get_telegram_state, initialize
    db=tmp_path/"old.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE telegram_intake_state (chat_id TEXT PRIMARY KEY, active_song_id TEXT, source_name TEXT NOT NULL DEFAULT '', project_ids TEXT NOT NULL DEFAULT 'P04', source_link TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL)")
        connection.execute("INSERT INTO telegram_intake_state(chat_id, active_song_id, updated_at) VALUES ('42', 'C09', 'now')")
    initialize(db)
    state=get_telegram_state(db,"42")
    assert state["active_song_id"]=="C09" and state["pending_action"]==""
