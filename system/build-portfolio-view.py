#!/usr/bin/env python3
"""Build a self-contained, read-only HTML snapshot of the portfolio CSVs."""
from __future__ import annotations

import csv
import json
from datetime import date
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read_rows(filename: str) -> list[dict[str, str]]:
    with (ROOT / filename).open(encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def main() -> None:
    projects = read_rows("projects.csv")
    tasks = read_rows("tasks.csv")
    roles = read_rows("roles.csv")
    decisions = read_rows("decisions.csv")
    evidence = read_rows("evidence.csv")
    data = {"projects": projects, "tasks": tasks, "roles": roles,
            "decisions": decisions, "evidence": evidence}
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    page = r'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Мозг-администратор · портфель</title>
<style>
:root{--ink:#29243d;--muted:#706b80;--purple:#7046bd;--lilac:#f0e9ff;--coral:#ff786f;--teal:#20a9a6;--paper:#fbf9ff;--card:#fff;--line:#ece7f3;--green:#208567}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#f8f3ff,#fff8f4 55%,#eefbfa);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
header{padding:30px max(22px,calc((100% - 1200px)/2));background:#fffC;border-bottom:1px solid var(--line)}h1{font-size:clamp(27px,4vw,42px);margin:0;color:#4b3480}.sub{color:var(--muted);margin:6px 0 0}.owner{display:inline-flex;gap:8px;align-items:center;margin-top:18px;padding:9px 14px;border-radius:999px;background:var(--lilac);font-weight:650}
main{max-width:1200px;margin:auto;padding:22px}.stats{display:grid;grid-template-columns:repeat(4,minmax(140px,1fr));gap:12px}.stat,.card,.panel{background:var(--card);border:1px solid var(--line);border-radius:18px;box-shadow:0 5px 22px #39235b0b}.stat{padding:17px}.stat b{font-size:26px;color:var(--purple);display:block}.stat span,.muted{color:var(--muted)}h2{margin:28px 0 12px;font-size:22px}h3{font-size:16px;margin:0 0 8px}.team,.projects{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}.card{padding:16px}.mascot{font-size:28px;float:right}.role{font-weight:700}.projects .card{border-top:4px solid var(--teal)}.id{font-size:12px;color:var(--purple);font-weight:750}.small{font-size:13px;color:var(--muted)}.pill{display:inline-block;background:#f2eef9;color:#5d4d79;border-radius:999px;padding:3px 9px;font-size:12px;margin:5px 4px 0 0}
.panel{padding:16px}.controls{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}input,select{border:1px solid var(--line);border-radius:10px;padding:10px 12px;background:#fff;color:var(--ink);font:inherit}input{flex:1;min-width:220px}select{min-width:180px}.task{padding:13px 4px;border-top:1px solid var(--line);display:grid;grid-template-columns:90px 1fr;gap:6px}.task:first-child{border-top:0}.task-title{font-weight:650}.task-meta{grid-column:2;color:var(--muted);font-size:13px}.notice{padding:12px 14px;border-radius:12px;background:#fff3e9;color:#7c4d36;margin:14px 0}.foot{margin-top:24px;color:var(--muted);font-size:13px}.foot a{color:var(--purple)}
.hq{position:relative;overflow:hidden;margin:18px 0;padding:22px;border:1px solid #e6dcff;border-radius:22px;background:linear-gradient(120deg,#efe8ff,#fff0e7 55%,#e7fbf6)}.hq-head{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.hq h2{margin:0;color:#553b91}.runway{display:flex;gap:12px;overflow:auto;padding:20px 5px 15px;border-bottom:3px dashed #d5c5f5}.runner{flex:0 0 150px;min-height:120px;padding:12px;border:1px solid #fff;border-radius:17px;background:#ffffffd9;text-align:center;box-shadow:0 5px 15px #47337412;animation:scurry 2.8s ease-in-out infinite alternate;animation-delay:var(--delay,0s)}.runner:nth-child(3n){animation-name:hop}.runner:nth-child(3n + 2){animation-name:wiggle}.runner .face{display:block;font-size:37px;line-height:1.2;filter:drop-shadow(0 3px 2px #503c6b18)}.runner b{display:block;font-size:13px;margin-top:5px}.runner small{display:block;color:var(--muted);font-size:11px;line-height:1.3;margin-top:3px}.hq-foot{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-top:12px;color:#62577a;font-size:12px}.pause{border:1px solid #d9c9f8;border-radius:999px;padding:7px 12px;background:white;color:#553b91;cursor:pointer;font:inherit}.paused .runner{animation-play-state:paused}@keyframes scurry{from{transform:translateY(1px) rotate(-2deg)}to{transform:translate(17px,-9px) rotate(2deg)}}@keyframes hop{0%,100%{transform:translateY(0)}50%{transform:translate(10px,-17px) rotate(5deg)}}@keyframes wiggle{from{transform:translateX(-3px) rotate(-3deg)}to{transform:translateX(12px) rotate(3deg)}}
@media(prefers-reduced-motion:reduce){.runner{animation:none!important}}
@media(max-width:650px){main{padding:14px}.stats{grid-template-columns:repeat(2,1fr)}.task{grid-template-columns:60px 1fr}.task-meta{grid-column:1/3}}
</style></head><body>
<header><h1>🧠 Мозг-администратор</h1><p class="sub">Единый портфель Анастасии Карташовой · снимок данных на <span id="date"></span></p><div class="owner">👑 Анастасия · владелица и последнее слово</div></header>
<main><div class="stats" id="stats"></div><section class="hq" id="hq"><div class="hq-head"><h2>🏃‍♀️ Штаб на сегодня</h2><button class="pause" id="pause" type="button" aria-pressed="false">⏸ Пауза маскотам</button></div><div class="runway" id="runway" aria-label="Анимированная команда маскотов"></div><div class="hq-foot"><span>Анимация — оформление интерфейса; реальные действия выполняются по поручению и отмечаются в базе.</span><span id="hqState"></span></div></section><div class="notice">Это визуальная сводка из файлов проекта. Панель не подключена к другим GPT-чатам или внешним аккаунтам; новые изменения появятся здесь после повторной сборки.</div>
<h2>Команда-кураторы</h2><div class="team" id="team"></div><h2>Проекты портфеля</h2><div class="projects" id="projects"></div>
<h2>Общий список задач</h2><section class="panel"><div class="controls"><input id="search" placeholder="Найти задачу или проект"><select id="projectFilter"><option value="">Все проекты</option></select></div><div id="tasks"></div></section>
<p class="foot">Источник: <code>system/projects.csv</code>, <code>system/tasks.csv</code> и реестры ролей. Обновить: <code>python system/build-portfolio-view.py</code>. Подробнее: <a href="integration-center.md">центр интеграций</a> · <a href="mascots.md">маскоты</a> · <a href="../README.md">корневая инструкция</a>.</p>
</main><script>
const D=DATA_PLACEHOLDER;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
document.querySelector('#date').textContent=new Date().toLocaleDateString('ru-RU');
const closed=s=>/готово|выполнено|закрыто|отменено|завершено/i.test(s||'');
document.querySelector('#stats').innerHTML=[['Проекты',D.projects.length],['Задачи',D.tasks.length],['Открытые задачи',D.tasks.filter(t=>!closed(t.статус)).length],['Фактические записи в воронке',0]].map(([a,b])=>`<div class="stat"><b>${esc(b)}</b><span>${esc(a)}</span></div>`).join('');
const mascots={secretary:['🦉','Штабная Сова'],market:['🐝','Маркетолог-исследователь'],analyst:['🐢','Аналитик проектов'],finance:['🐼','Финансовый аналитик'],monetization:['✨','Советник по монетизации'],product:['🐿️','Искра'],creative:['🦝','Креативный Енот'],integrator:['🐙','Интегратор'],executor:['🛠️','Исполнитель'],music_legal:['🦉','Юрист музыки'],home_office:['🐕','Home Office'],agency_coordinator:['🐧','Координатор агентства'],recruiter:['🐬','Рекрутер'],content_editor:['🦊','Рыжая Редакторка'],sales:['🦦','Выдра-Навигатор'],critic:['🐈‍⬛','Мисс Проверка']};
const roleCards=D.roles.map((r,i)=>{const [face,nickname]=mascots[r.id]||['✨',r.роль];return `<article class="runner" style="--delay:${(i%6)*-.27}s"><span class="face" aria-hidden="true">${face}</span><b>${esc(nickname)}</b><small>${esc(r.роль)}</small></article>`}).join('');
document.querySelector('#runway').innerHTML=roleCards;
document.querySelector('#hqState').textContent=`${D.roles.length} ролей · ${D.tasks.filter(t=>!closed(t.статус)).length} открытых задач в реестре`;
document.querySelector('#pause').addEventListener('click',e=>{const paused=document.querySelector('#hq').classList.toggle('paused');e.currentTarget.setAttribute('aria-pressed',String(paused));e.currentTarget.textContent=paused?'▶ Продолжить':'⏸ Пауза маскотам'});
document.querySelector('#team').innerHTML=D.roles.map(r=>{const [face,nickname]=mascots[r.id]||['✨',r.роль];return `<article class="card"><span class="mascot">${face}</span><div class="role">${esc(nickname)}</div><div class="small">${esc(r.текущий_статус||'Роль описана')}</div></article>`}).join('');
document.querySelector('#projects').innerHTML=D.projects.map(p=>`<article class="card"><span class="id">${esc(p.id)}</span><h3>${esc(p.проект)}</h3><div class="small"><b>Цель:</b> ${esc(p.цель)}</div><div class="pill">${esc(p.статус)}</div><p class="small"><b>Дальше:</b> ${esc(p.следующий_шаг)}</p><p class="small"><b>Неизвестно:</b> ${esc(p.неизвестное)}</p></article>`).join('');
const projectsById=Object.fromEntries(D.projects.map(p=>[p.id,p.проект]));
const select=document.querySelector('#projectFilter');D.projects.forEach(p=>select.insertAdjacentHTML('beforeend',`<option value="${esc(p.id)}">${esc(p.id+' · '+p.проект)}</option>`));
function renderTasks(){const q=document.querySelector('#search').value.toLowerCase(),f=select.value;const rows=D.tasks.filter(t=>(!f||(t.проект_id||'').split(';').includes(f))&&(!q||[t.задача,t.следующий_шаг,t.ответственный,t.проект_id].join(' ').toLowerCase().includes(q)));document.querySelector('#tasks').innerHTML=rows.map(t=>`<div class="task"><span class="id">${esc(t.id)}</span><div><div class="task-title">${esc(t.задача)}</div><div class="pill">${esc(t.статус)}</div></div><div class="task-meta">Проект: ${esc((t.проект_id||'').split(';').map(id=>projectsById[id]||id).join(', '))} · Роль: ${esc(t.роль)} · Срок: ${esc(t.срок||'не установлен')}<br>Следующий шаг: ${esc(t.следующий_шаг)}</div></div>`).join('')||'<p class="muted">Подходящих задач нет.</p>'}
document.querySelector('#search').addEventListener('input',renderTasks);select.addEventListener('change',renderTasks);renderTasks();
</script></body></html>'''.replace("DATA_PLACEHOLDER", payload)
    target = ROOT / "portfolio-overview.html"
    target.write_text(page, encoding="utf-8")
    print(f"Built {target} from {len(projects)} projects and {len(tasks)} tasks")


if __name__ == "__main__":
    main()
