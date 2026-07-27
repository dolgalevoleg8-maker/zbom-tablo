#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ZBOM — сборщик дашборда для GitHub Actions.
Ходит в Битрикс (серверный запрос, без CORS-проблем), считает тишину
по трём воронкам и генерирует готовый index.html с уже вложенными данными.
Ключ берётся из переменной окружения BITRIX_HOOK (секрет GitHub).
"""
import os, sys, json, time, html
import datetime as dt
from urllib import request, parse, error

HOOK = os.environ.get("BITRIX_HOOK", "").strip()
if not HOOK:
    print("ОШИБКА: не задан секрет BITRIX_HOOK"); sys.exit(1)
if not HOOK.endswith("/"):
    HOOK += "/"

RATE = 80
TODAY = dt.datetime.now()

PIPES = [
    {"key": "sale",  "title": "Продажи мебель",   "entity": "deal", "cat": 0,  "yellow": 5,  "red": 10, "mode": "silence"},
    {"key": "defer", "title": "Отложенный спрос",  "entity": "deal", "cat": 14, "yellow": 45, "red": 90, "mode": "silence"},
    {"key": "leads", "title": "Лиды",              "entity": "lead",             "yellow": 2,  "red": 3,  "mode": "silence"},
]
STOP = ("S", "F")  # завершённые стадии


def call(method, params=""):
    url = HOOK + method + (("?" + params) if params else "")
    for attempt in range(3):
        try:
            with request.urlopen(url, timeout=30) as r:
                d = json.loads(r.read().decode("utf-8"))
            if "error" in d:
                raise RuntimeError(d.get("error_description") or d["error"])
            return d
        except Exception as e:
            print(f"  попытка {attempt+1} на {method}: {e}")
            if attempt == 2:
                raise
            time.sleep(2)


def list_all(method, params=""):
    out, start = [], 0
    while True:
        d = call(method, params + ("&" if params else "") + "start=" + str(start))
        if not d or "result" not in d:
            break
        out += d["result"]
        nxt = d.get("next")
        if nxt is None:
            break
        start = nxt
        if start > 20000:
            break
        time.sleep(0.2)
    return out


def days_since(s):
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s.replace("T", " ").split("+")[0].strip())
    except Exception:
        try:
            d = dt.datetime.strptime(s[:10], "%Y-%m-%d")
        except Exception:
            return None
    return (TODAY - d).days


def load_users():
    u = {}
    for x in list_all("user.get", "FILTER[ACTIVE]=true"):
        u[str(x["ID"])] = " ".join(v for v in [x.get("NAME"), x.get("LAST_NAME")] if v) or ("ID " + str(x["ID"]))
    return u


def collect():
    users = load_users()
    data = {}
    risk = 0.0
    for p in PIPES:
        rows = []
        if p["entity"] == "lead":
            leads = list_all("crm.lead.list",
                "filter[STATUS_SEMANTIC_ID]=P"
                "&select[]=ID&select[]=TITLE&select[]=OPPORTUNITY&select[]=ASSIGNED_BY_ID&select[]=DATE_CREATE")
            for l in leads:
                rows.append(mk(l["ID"], l.get("TITLE"), l.get("OPPORTUNITY"),
                               l.get("ASSIGNED_BY_ID"), l.get("DATE_CREATE"), p, users))
        else:
            deals = list_all("crm.deal.list",
                "filter[CATEGORY_ID]=" + str(p["cat"]) + "&filter[STAGE_SEMANTIC_ID]=P"
                "&select[]=ID&select[]=TITLE&select[]=OPPORTUNITY&select[]=ASSIGNED_BY_ID"
                "&select[]=LAST_ACTIVITY_TIME&select[]=DATE_CREATE")
            for d in deals:
                rows.append(mk(d["ID"], d.get("TITLE"), d.get("OPPORTUNITY"),
                               d.get("ASSIGNED_BY_ID"), d.get("LAST_ACTIVITY_TIME") or d.get("DATE_CREATE"), p, users))
        order = {"red": 0, "amber": 1, "green": 2}
        rows.sort(key=lambda r: (order[r["lvl"]], -r["days"]))
        if p["key"] == "sale":
            risk += sum(r["opp"] for r in rows if r["lvl"] == "red")
        data[p["key"]] = {"title": p["title"], "rows": rows}
    return data, risk


def mk(id, title, opp, mgr, date, p, users):
    d = days_since(date)
    d = 999 if d is None else d
    lvl = "red" if d >= p["red"] else ("amber" if d >= p["yellow"] else "green")
    try:
        opp = float(opp or 0)
    except Exception:
        opp = 0.0
    return {"id": str(id), "title": title or ("Сделка " + str(id)),
            "opp": opp, "mgr": users.get(str(mgr), "ID " + str(mgr)),
            "days": d, "lvl": lvl}


def money(v):
    v = float(v or 0)
    if v <= 0:
        return "—"
    return f"{int(round(v)):,}".replace(",", " ") + " ₽"


def esc(s):
    return html.escape(str(s))


def render(data, risk):
    now = TODAY.strftime("%d.%m.%Y %H:%M")
    tabs, bodies = "", ""
    first = True
    for key, blk in data.items():
        rows = blk["rows"]
        red = sum(1 for r in rows if r["lvl"] == "red")
        amber = sum(1 for r in rows if r["lvl"] == "amber")
        on = " on" if first else ""
        tabs += f'<button class="tab{on}" onclick="pick(\'{key}\')">{esc(blk["title"])} <b>{red+amber}</b></button>'
        cards = ""
        for r in [x for x in rows if x["lvl"] != "green"]:
            alert = (f'{r["days"]} дн. без движения' if key == "defer" else f'{r["days"]} дн. без касания')
            cards += (f'<div class="card {r["lvl"]}"><div class="top"><span class="num">#{esc(r["id"])}</span>'
                      f'<span class="days">{alert}</span></div><div class="title">{esc(r["title"])}</div>'
                      f'<div class="meta"><span>👤 {esc(r["mgr"])}</span>'
                      f'<span class="amt">{money(r["opp"])}</span></div></div>')
        if not cards:
            cards = '<div class="empty">Проблемных карточек нет</div>'
        bodies += (f'<div class="body{on}" id="b_{key}">'
                   f'<div class="sum"><div class="st r"><b>{red}</b><span>критично</span></div>'
                   f'<div class="st a"><b>{amber}</b><span>внимание</span></div>'
                   f'<div class="st t"><b>{len(rows)}</b><span>в работе</span></div></div>{cards}</div>')
        first = False

    return TEMPLATE.replace("{{NOW}}", now).replace("{{RISK}}", money(risk)) \
                   .replace("{{TABS}}", tabs).replace("{{BODIES}}", bodies)


TEMPLATE = r"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>ZBOM · Контроль CRM</title><style>
:root{--marsala:#6e1a2e;--marsala-deep:#54121f;--graphite:#33333a;--paper:#f5f3f0;
--card:#fff;--line:#e7e3de;--red:#c0392b;--amber:#d98324;--green:#4a8a5c;--muted:#8a857e}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
body{font-family:-apple-system,"Segoe UI",Roboto,sans-serif;background:var(--paper);
color:var(--graphite);max-width:680px;margin:0 auto;padding-bottom:48px}
header{background:linear-gradient(160deg,var(--marsala),var(--marsala-deep));color:#fff;
padding:20px 18px 16px;position:sticky;top:0;z-index:20}
header h1{font-size:17px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase}
header h1 span{opacity:.6;font-weight:400}.upd{font-size:12px;opacity:.75;margin-top:4px}
.risk{margin:14px 18px 0;background:var(--card);border:1px solid var(--line);
border-left:4px solid var(--marsala);border-radius:12px;padding:14px 16px}
.risk .lbl{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
.risk .val{font-size:26px;font-weight:800;color:var(--marsala);margin-top:2px}
.tabs{display:flex;gap:6px;padding:12px;overflow-x:auto;position:sticky;top:64px;
background:var(--paper);z-index:15}
.tab{flex:0 0 auto;border:1px solid var(--line);background:var(--card);color:var(--graphite);
padding:9px 14px;border-radius:22px;font-size:13px;font-weight:600;cursor:pointer;
display:flex;align-items:center;gap:7px}
.tab.on{background:var(--marsala);color:#fff;border-color:var(--marsala)}
.tab b{background:rgba(0,0,0,.12);border-radius:11px;padding:1px 8px;font-size:11px}
.tab.on b{background:rgba(255,255,255,.25)}
.body{padding:0 12px;display:none}.body.on{display:block}
.sum{display:flex;gap:8px;margin:4px 0 14px}
.st{flex:1;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 8px;text-align:center}
.st b{display:block;font-size:23px;line-height:1}.st span{font-size:11px;color:var(--muted)}
.st.r b{color:var(--red)}.st.a b{color:var(--amber)}.st.t b{color:var(--marsala)}
.card{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--line);
border-radius:12px;padding:13px 15px;margin-bottom:10px}
.card.red{border-left-color:var(--red)}.card.amber{border-left-color:var(--amber)}
.top{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:5px}
.days{font-size:13px;font-weight:800}.card.red .days{color:var(--red)}.card.amber .days{color:var(--amber)}
.num{font-size:11px;color:var(--muted)}
.title{font-size:15px;font-weight:600;margin-bottom:7px;line-height:1.3}
.meta{display:flex;flex-wrap:wrap;gap:8px 14px;font-size:12px;color:var(--muted)}
.amt{font-weight:700;color:var(--marsala)}
.empty{text-align:center;padding:40px 10px;color:var(--muted);font-size:15px}
footer{text-align:center;font-size:11px;color:var(--muted);padding:22px}
</style></head><body>
<header><h1>ZBOM <span>· контроль CRM</span></h1><div class="upd">Обновлено: {{NOW}}</div></header>
<div class="risk"><div class="lbl">Под угрозой · продажи в тишине 10+ дней</div><div class="val">{{RISK}}</div></div>
<div class="tabs">{{TABS}}</div>{{BODIES}}
<footer>Данные обезличены · обновляется раз в сутки</footer>
<script>
function pick(k){
 document.querySelectorAll('.tab').forEach(function(x){x.classList.remove('on')});
 document.querySelectorAll('.body').forEach(function(x){x.classList.remove('on')});
 event.target.closest('.tab').classList.add('on');
 document.getElementById('b_'+k).classList.add('on');
}
</script></body></html>"""


def main():
    print("Сбор данных из Битрикса…")
    data, risk = collect()
    for k, v in data.items():
        print(f"  {v['title']}: {len(v['rows'])} в работе")
    html_out = render(data, risk)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_out)
    print("index.html готов, сумма риска:", money(risk))


if __name__ == "__main__":
    main()
