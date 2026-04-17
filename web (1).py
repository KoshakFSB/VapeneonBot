"""
VapeNeon — публичный сайт + панель администратора
"""

import os
import sqlite3
import logging
import hashlib
import secrets
import httpx
from datetime import datetime, timedelta
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ─── CONFIG ──────────────────────────────────────────────────────────────────

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(","))) if os.getenv("ADMIN_IDS") else []
CHAT_ID   = int(os.getenv("CHAT_ID", "0"))
DB_PATH   = "data/bot_database.db"

# Логины и пароли — только на сервере, в env переменной ADMIN_ACCOUNTS
# Формат: username1:password1,username2:password2
_raw = os.getenv(
    "ADMIN_ACCOUNTS",
    "KoshakFSB:JBoNViF5,rinya08:C386gh781,darknesss43:Ha9mapvz"
)
ADMIN_ACCOUNTS: dict = {}
for pair in _raw.split(","):
    if ":" in pair:
        u, p = pair.strip().split(":", 1)
        ADMIN_ACCOUNTS[u.lower()] = hashlib.sha256(p.encode()).hexdigest()

# Сессии в памяти
SESSIONS: dict = {}
SESSION_TTL = timedelta(hours=12)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [WEB] %(levelname)s %(message)s")
log = logging.getLogger("web")

# ─── APP ─────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_db()
    log.info("Сайт VapeNeon запущен на :8080")
    yield

app = FastAPI(title="VapeNeon", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ─── DB ──────────────────────────────────────────────────────────────────────

def _init_db():
    conn = sqlite3.connect(DB_PATH)
    for col, typ in [
        ("complaint_type",    "TEXT DEFAULT 'other'"),
        ("admin_comment",     "TEXT"),
        ("submitter_tg_id",   "INTEGER DEFAULT 0"),
        ("submitter_username","TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE admin_complaints ADD COLUMN {col} {typ}")
            conn.commit()
        except Exception:
            pass
    conn.close()

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]

def one(conn, sql, params=()):
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None

# ─── TELEGRAM ────────────────────────────────────────────────────────────────

async def tg_send(user_id: int, text: str):
    if not BOT_TOKEN or not user_id:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": user_id, "text": text,
                      "parse_mode": "HTML", "disable_web_page_preview": True}
            )
        except Exception as e:
            log.error(f"Telegram error: {e}")

async def notify_admins_new(c: dict):
    type_map = {"abuse":"Злоупотребление","unfair_ban":"Несправ. наказание",
                "inaction":"Бездействие","rudeness":"Грубость","other":"Другое"}
    text = (
        f"🔔 <b>Новая жалоба #{c['id']}</b>\n\n"
        f"👤 Заявитель: <code>{c['username']}</code>\n"
        f"⚡ На администратора: <code>{c['admin_username']}</code>\n"
        f"📌 Тип: {type_map.get(c.get('complaint_type','other'),'Другое')}\n"
        f"📝 {c['description']}\n\n"
        f"<i>Рассмотрите на сайте.</i>"
    )
    for aid in ADMIN_IDS:
        await tg_send(aid, text)

async def notify_user_reply(c: dict, comment: str):
    uid = c.get("submitter_tg_id") or 0
    if not uid:
        return
    status_map = {"resolved":"✅ Принята","rejected":"❌ Отклонена","pending":"⏳ На рассмотрении"}
    text = (
        f"📬 <b>Ответ на вашу жалобу #{c['id']}</b>\n\n"
        f"Статус: {status_map.get(c.get('status','pending'),'')}\n\n"
        f"💬 Комментарий администратора:\n{comment}"
    )
    await tg_send(uid, text)

# ─── AUTH ────────────────────────────────────────────────────────────────────

def get_session(request: Request):
    token = request.cookies.get("vn_session")
    if not token or token not in SESSIONS:
        return None
    s = SESSIONS[token]
    if datetime.now() > s["expires"]:
        del SESSIONS[token]
        return None
    return s

def require_admin(request: Request):
    s = get_session(request)
    if not s:
        raise HTTPException(401, "Требуется авторизация")
    return s

# ─── MODELS ──────────────────────────────────────────────────────────────────

class LoginIn(BaseModel):
    username: str
    password: str

class ComplaintIn(BaseModel):
    username: str
    tg_id: Optional[int] = None
    admin_username: str
    complaint_type: str
    description: str
    complaint_text: str
    evidence: Optional[str] = None

class ReviewIn(BaseModel):
    status: str
    comment: str

# ─── AUTH ENDPOINTS ──────────────────────────────────────────────────────────

@app.post("/api/auth/login")
async def login(body: LoginIn, response: Response):
    username = body.username.lower().lstrip("@")
    pw_hash  = hashlib.sha256(body.password.encode()).hexdigest()
    if ADMIN_ACCOUNTS.get(username) != pw_hash:
        raise HTTPException(401, "Неверный логин или пароль")
    token = secrets.token_urlsafe(32)
    SESSIONS[token] = {"username": username, "expires": datetime.now() + SESSION_TTL}
    response.set_cookie("vn_session", token, httponly=True, samesite="lax", max_age=43200)
    log.info(f"Вход: {username}")
    return {"ok": True, "username": username}

@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get("vn_session")
    if token and token in SESSIONS:
        del SESSIONS[token]
    response.delete_cookie("vn_session")
    return {"ok": True}

@app.get("/api/auth/me")
async def me(request: Request):
    s = get_session(request)
    if not s:
        return {"admin": False}
    return {"admin": True, "username": s["username"]}

# ─── PUBLIC ENDPOINTS ────────────────────────────────────────────────────────

@app.post("/api/complaints/submit")
async def submit_complaint(body: ComplaintIn):
    conn = db()
    cur = conn.execute("""
        INSERT INTO admin_complaints
            (user_id, username, admin_username, description, complaint_text,
             evidence, status, complaint_type, submitter_tg_id, submitter_username, created_at)
        VALUES (0,?,?,?,?,?,'pending',?,?,?,datetime('now'))
    """, (
        body.username, body.admin_username, body.description, body.complaint_text,
        body.evidence or "", body.complaint_type,
        body.tg_id or 0, body.username,
    ))
    conn.commit()
    cid = cur.lastrowid
    c = one(conn, "SELECT * FROM admin_complaints WHERE id=?", (cid,))
    conn.close()
    await notify_admins_new(c)
    return {"id": cid, "ok": True}

@app.get("/api/my-complaints")
async def my_complaints(username: str):
    if not username:
        raise HTTPException(400, "username обязателен")
    uname = username.lower().lstrip("@")
    conn = db()
    data = rows(conn, """
        SELECT id, username, admin_username, complaint_type, description,
               complaint_text, status, admin_comment, created_at, handled_at
        FROM admin_complaints
        WHERE lower(ltrim(username,'@'))=?
        ORDER BY created_at DESC
    """, (uname,))
    conn.close()
    return data

# ─── ADMIN ENDPOINTS ─────────────────────────────────────────────────────────

@app.get("/api/stats")
async def get_stats(request: Request):
    require_admin(request)
    conn = db()
    today = datetime.now().strftime("%Y-%m-%d")
    result = {
        "complaints_today": one(conn,"SELECT COUNT(*) as c FROM admin_complaints WHERE date(created_at)=?",(today,))["c"],
        "pending":          one(conn,"SELECT COUNT(*) as c FROM admin_complaints WHERE status='pending'")["c"],
        "active_mutes":     one(conn,"SELECT COUNT(*) as c FROM mutes WHERE is_active=1")["c"],
        "active_bans":      one(conn,"SELECT COUNT(*) as c FROM bans WHERE is_active=1")["c"],
        "total_users":      one(conn,"SELECT COUNT(*) as c FROM bot_users")["c"],
        "chart": rows(conn,"""
            SELECT date(created_at) as day, COUNT(*) as count
            FROM admin_complaints WHERE created_at>=date('now','-6 days')
            GROUP BY date(created_at) ORDER BY day
        """),
    }
    conn.close()
    return result

@app.get("/api/complaints")
async def get_complaints(request: Request, status: str = "all", q: str = ""):
    require_admin(request)
    conn = db()
    sql = "SELECT * FROM admin_complaints"
    params, conds = [], []
    if status != "all":
        conds.append("status=?"); params.append(status)
    if q:
        conds.append("(username LIKE ? OR admin_username LIKE ? OR description LIKE ?)")
        params += [f"%{q}%",f"%{q}%",f"%{q}%"]
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY created_at DESC LIMIT 200"
    data = rows(conn, sql, params)
    conn.close()
    return data

@app.get("/api/complaints/{cid}")
async def get_complaint(request: Request, cid: int):
    require_admin(request)
    conn = db()
    c = one(conn, "SELECT * FROM admin_complaints WHERE id=?", (cid,))
    conn.close()
    if not c: raise HTTPException(404, "Не найдено")
    return c

@app.patch("/api/complaints/{cid}")
async def review_complaint(request: Request, cid: int, body: ReviewIn):
    s = require_admin(request)
    if body.status not in ("resolved","rejected","pending"):
        raise HTTPException(400, "Недопустимый статус")
    conn = db()
    conn.execute("""
        UPDATE admin_complaints SET status=?,admin_comment=?,handled_at=datetime('now') WHERE id=?
    """, (body.status, body.comment, cid))
    conn.commit()
    c = one(conn, "SELECT * FROM admin_complaints WHERE id=?", (cid,))
    conn.close()
    await notify_user_reply(c, body.comment)
    log.info(f"Жалоба #{cid} → {body.status} от {s['username']}")
    return c

@app.get("/api/users")
async def get_users(request: Request, q: str = ""):
    require_admin(request)
    conn = db()
    if q:
        data = rows(conn,
            "SELECT * FROM bot_users WHERE username LIKE ? OR first_name LIKE ? ORDER BY last_seen DESC LIMIT 100",
            (f"%{q}%",f"%{q}%"))
    else:
        data = rows(conn,"SELECT * FROM bot_users ORDER BY last_seen DESC LIMIT 100")
    for u in data:
        uid = u["user_id"]
        u["warns"]  = one(conn,"SELECT COUNT(*) as c FROM warns WHERE user_id=? AND expires_at>datetime('now')",(uid,))["c"]
        u["muted"]  = one(conn,"SELECT COUNT(*) as c FROM mutes WHERE user_id=? AND is_active=1",(uid,))["c"] > 0
        u["banned"] = one(conn,"SELECT COUNT(*) as c FROM bans WHERE user_id=? AND is_active=1",(uid,))["c"] > 0
    conn.close()
    return data

@app.get("/api/logs")
async def get_logs(request: Request, kind: str = "all"):
    require_admin(request)
    conn = db()
    result = []
    if kind in ("all","warn"):
        result += rows(conn,"SELECT 'warn' as kind,issued_at as ts,issued_by,user_id,reason,NULL as expires_at FROM warns ORDER BY issued_at DESC LIMIT 50")
    if kind in ("all","mute"):
        result += rows(conn,"SELECT 'mute' as kind,issued_at as ts,issued_by,user_id,reason,expires_at FROM mutes ORDER BY issued_at DESC LIMIT 50")
    if kind in ("all","ban"):
        result += rows(conn,"SELECT 'ban' as kind,issued_at as ts,issued_by,user_id,reason,expires_at FROM bans ORDER BY issued_at DESC LIMIT 50")
    conn.close()
    result.sort(key=lambda x: x.get("ts") or "", reverse=True)
    return result[:200]

@app.get("/api/broadcast/next")
async def broadcast_next():
    SEND_HOURS = sorted([7,10,13,16,19,22,1])
    now = datetime.now()
    h, m = now.hour, now.minute
    next_h = next((x for x in SEND_HOURS if x > h), SEND_HOURS[0])
    total_min = ((next_h - h) % 24) * 60 - m
    return {"next_hour": next_h, "minutes_left": max(0, total_min)}

# ─── FRONTEND ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    with open(path, encoding="utf-8") as f:
        return f.read()
