"""
VapeNeon — публичный сайт + панель администратора
"""

import os
import sqlite3
import logging
import hashlib
import secrets
import httpx
import string
import random
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

# Статические аккаунты из env (fallback если нет в БД)
_raw = os.getenv("ADMIN_ACCOUNTS", "KoshakFSB:JBoNViF5,rinya08:C386gh781,darknesss43:Ha9mapvz")
STATIC_ADMIN_ACCOUNTS: dict = {}
for pair in _raw.split(","):
    if ":" in pair:
        u, p = pair.strip().split(":", 1)
        STATIC_ADMIN_ACCOUNTS[u.lower()] = hashlib.sha256(p.encode()).hexdigest()

# Права на рассмотрение жалоб на администраторов (из env, fallback)
_adm_raw = os.getenv("ADMIN_COMPLAINT_REVIEWERS", "KoshakFSB")
STATIC_ADMIN_COMPLAINT_REVIEWERS = {x.strip().lower() for x in _adm_raw.split(",")}

# Сессии в памяти
SESSIONS: dict = {}
SESSION_TTL = timedelta(days=7)

# Токены авторизации пользователей
USER_TOKENS: dict = {}
USER_SESSIONS: dict = {}

# Appeal токены (автоматическая авторизация через кнопку «Обжаловать»)
APPEAL_TOKENS: dict = {}  # token -> {tg_id, username, reason, punishment_type}

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



    # Существующие колонки admin_complaints
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

    # Таблица /report жалоб из чата
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reporter_id INTEGER NOT NULL,
            reporter_username TEXT,
            reported_id INTEGER NOT NULL,
            reported_username TEXT,
            reason TEXT,
            message_text TEXT,
            message_photo TEXT,
            message_link TEXT,
            chat_id INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            handled_by TEXT,
            handled_action TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            handled_at TIMESTAMP
        )
    """)

    # Таблица администраторов сайта (выданные через /addjbadm)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS site_admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER NOT NULL UNIQUE,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            added_by INTEGER NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            can_review_admin_complaints INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1
        )
    """)

    # Баны на сайте
    conn.execute("""
        CREATE TABLE IF NOT EXISTS site_bans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            tg_id INTEGER DEFAULT 0,
            reason TEXT,
            issued_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            is_active INTEGER DEFAULT 1
        )
    """)

    # Варны на сайте
    conn.execute("""
        CREATE TABLE IF NOT EXISTS site_warns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            tg_id INTEGER DEFAULT 0,
            reason TEXT,
            issued_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            is_active INTEGER DEFAULT 1
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS admin_sessions (
            token TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            can_review_admin_complaints INTEGER DEFAULT 0,
            expires_at TIMESTAMP NOT NULL
        )
    """)
    conn.execute("DELETE FROM admin_sessions WHERE expires_at < datetime('now')")

    # Пользовательские сессии (авторизация через Telegram)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_sessions (
            token TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            tg_id INTEGER DEFAULT 0,
            appeal_reason TEXT DEFAULT '',
            appeal_type TEXT DEFAULT '',
            expires_at TIMESTAMP NOT NULL
        )
    """)
    conn.execute("DELETE FROM user_sessions WHERE expires_at < datetime('now')")

    conn.commit()
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

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def gen_password(length=12):
    chars = string.ascii_letters + string.digits + "!@#$%"
    return "".join(random.choices(chars, k=length))

def get_admin_record(username: str) -> Optional[dict]:
    """Получить запись администратора из БД или статического списка"""
    uname = username.lower().lstrip("@")
    conn = db()
    rec = one(conn, "SELECT * FROM site_admins WHERE lower(username)=? AND is_active=1", (uname,))
    conn.close()
    return rec

def check_admin_password(username: str, password: str) -> Optional[dict]:
    """Проверить логин/пароль. Возвращает dict с правами или None"""
    uname = username.lower().lstrip("@")
    pw_hash = hashlib.sha256(password.encode()).hexdigest()

    # Сначала проверяем БД
    conn = db()
    rec = one(conn, "SELECT * FROM site_admins WHERE lower(username)=? AND is_active=1", (uname,))
    conn.close()
    if rec and rec["password_hash"] == pw_hash:
        return {
            "username": rec["username"],
            "can_review_admin_complaints": bool(rec["can_review_admin_complaints"]),
            "source": "db"
        }

    # Fallback — статический список
    if STATIC_ADMIN_ACCOUNTS.get(uname) == pw_hash:
        return {
            "username": uname,
            "can_review_admin_complaints": uname in STATIC_ADMIN_COMPLAINT_REVIEWERS,
            "source": "static"
        }
    return None

def is_site_banned(username: str) -> Optional[dict]:
    """Проверить бан на сайте"""
    uname = username.lower().lstrip("@")
    conn = db()
    ban = one(conn, """
        SELECT * FROM site_bans
        WHERE lower(username)=? AND is_active=1
        AND (expires_at IS NULL OR expires_at > datetime('now'))
        ORDER BY created_at DESC LIMIT 1
    """, (uname,))
    conn.close()
    return ban

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
        f"🔔 <b>Новая жалоба на администратора #{c['id']}</b>\n\n"
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

async def tg_ban_user(tg_id: int, reason: str, issued_by: str):
    """Уведомить пользователя о бане на сайте"""
    text = (
        f"🚫 <b>Вы заблокированы на сайте VapeNeon</b>\n\n"
        f"📝 Причина: {reason}\n"
        f"👮 Администратор: {issued_by}\n\n"
        "Обжалование через: <a href='https://t.me/" + os.getenv("BOT_USERNAME", "TheVapeNeonBot") + "'>бота</a>"
    )
    await tg_send(tg_id, text)

async def tg_warn_user(tg_id: int, reason: str, issued_by: str):
    """Уведомить пользователя о варне на сайте"""
    text = (
        f"⚠️ <b>Вы получили предупреждение на сайте VapeNeon</b>\n\n"
        f"📝 Причина: {reason}\n"
        f"👮 Администратор: {issued_by}\n"
        f"⏰ Срок: 7 дней"
    )
    await tg_send(tg_id, text)

# ─── AUTH ────────────────────────────────────────────────────────────────────

def _del_session(token: str):
    SESSIONS.pop(token, None)
    try:
        c = db(); c.execute("DELETE FROM admin_sessions WHERE token=?", (token,)); c.commit(); c.close()
    except Exception: pass

USER_SESSION_TTL = timedelta(days=1)

def get_user_session(token: str) -> Optional[dict]:
    """Получить пользовательскую сессию из памяти или БД"""
    if not token:
        return None
    if token in USER_SESSIONS:
        return USER_SESSIONS[token]
    # Восстанавливаем из БД после перезапуска
    c = db()
    row = one(c, "SELECT * FROM user_sessions WHERE token=? AND expires_at > datetime('now')", (token,))
    c.close()
    if not row:
        return None
    s = {
        "username": row["username"],
        "tg_id": row["tg_id"],
        "appeal_reason": row.get("appeal_reason", ""),
        "appeal_type": row.get("appeal_type", ""),
    }
    USER_SESSIONS[token] = s
    return s

def save_user_session(token: str, data: dict):
    """Сохранить пользовательскую сессию в памяти и БД"""
    USER_SESSIONS[token] = data
    expires_dt = datetime.now() + USER_SESSION_TTL
    try:
        c = db()
        c.execute(
            "INSERT OR REPLACE INTO user_sessions (token, username, tg_id, appeal_reason, appeal_type, expires_at) VALUES (?,?,?,?,?,?)",
            (token, data.get("username", ""), data.get("tg_id", 0),
             data.get("appeal_reason", ""), data.get("appeal_type", ""), expires_dt.isoformat())
        )
        c.commit(); c.close()
    except Exception as e:
        log.error(f"Ошибка сохранения user_session: {e}")

def del_user_session(token: str):
    """Удалить пользовательскую сессию"""
    USER_SESSIONS.pop(token, None)
    try:
        c = db(); c.execute("DELETE FROM user_sessions WHERE token=?", (token,)); c.commit(); c.close()
    except Exception: pass

def get_session(request: Request):
    token = request.cookies.get("vn_session")
    if not token:
        return None
    if token in SESSIONS:
        s = SESSIONS[token]
        if datetime.now() > s["expires"]:
            _del_session(token)
            return None
        return s
    # Не в кеше — восстанавливаем из БД (после перезапуска сервера)
    c = db()
    row = one(c, "SELECT * FROM admin_sessions WHERE token=? AND expires_at > datetime('now')", (token,))
    c.close()
    if not row:
        return None
    s = {
        "username": row["username"],
        "can_review_admin_complaints": bool(row["can_review_admin_complaints"]),
        "expires": datetime.fromisoformat(row["expires_at"]),
    }
    SESSIONS[token] = s
    return s

def require_admin(request: Request):
    s = get_session(request)
    if not s:
        raise HTTPException(401, "Требуется авторизация")
    return s

def require_complaint_reviewer(request: Request):
    """Требует права на рассмотрение жалоб на администраторов"""
    s = require_admin(request)
    if not s.get("can_review_admin_complaints"):
        raise HTTPException(403, "Нет прав на рассмотрение жалоб на администраторов")
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

class PunishIn(BaseModel):
    username: str
    tg_id: Optional[int] = 0
    reason: str
    expires_hours: Optional[int] = None  # None = бессрочно

class UserReportActionIn(BaseModel):
    action: str   # warn / mute1 / mute2 / mute3 / ban / dismiss
    reason: Optional[str] = None
    comment: Optional[str] = None

# ─── AUTH ENDPOINTS ──────────────────────────────────────────────────────────

@app.post("/api/auth/login")
async def login(body: LoginIn, response: Response):
    username = body.username.lower().lstrip("@")

    # Проверяем бан на сайте
    ban = is_site_banned(username)
    if ban:
        expires = f" до {ban['expires_at']}" if ban.get('expires_at') else " (бессрочно)"
        raise HTTPException(403, f"Вы заблокированы на сайте{expires}. Причина: {ban.get('reason','')}")

    admin = check_admin_password(username, body.password)
    if not admin:
        raise HTTPException(401, "Неверный логин или пароль")

    token = secrets.token_urlsafe(32)
    expires_dt = datetime.now() + SESSION_TTL
    SESSIONS[token] = {
        "username": admin["username"],
        "can_review_admin_complaints": admin["can_review_admin_complaints"],
        "expires": expires_dt
    }
    try:
        c = db()
        c.execute(
            "INSERT OR REPLACE INTO admin_sessions (token, username, can_review_admin_complaints, expires_at) VALUES (?,?,?,?)",
            (token, admin["username"], int(admin["can_review_admin_complaints"]), expires_dt.isoformat())
        )
        c.commit(); c.close()
    except Exception as e:
        log.error(f"Ошибка сохранения сессии: {e}")
    response.set_cookie(
        "vn_session", token,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
        max_age=int(SESSION_TTL.total_seconds())
    )
    log.info(f"Вход: {username} (can_review_complaints={admin['can_review_admin_complaints']})")
    return {"ok": True, "username": admin["username"], "can_review_admin_complaints": admin["can_review_admin_complaints"]}

@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get("vn_session")
    if token:
        _del_session(token)
    response.delete_cookie("vn_session", path="/", samesite="lax", secure=False)
    return {"ok": True}

@app.get("/api/auth/me")
async def me(request: Request):
    s = get_session(request)
    if not s:
        # Check user session
        token = request.cookies.get("vn_user_session")
        u = get_user_session(token) if token else None
        if u:
            return {"admin": False, "user": True, "username": u["username"], "tg_id": u.get("tg_id")}
        # Check appeal token in cookie
        appeal = request.cookies.get("vn_appeal")
        if appeal and appeal in APPEAL_TOKENS:
            a = APPEAL_TOKENS[appeal]
            return {"admin": False, "user": True, "username": a["username"],
                    "tg_id": a["tg_id"], "appeal_reason": a.get("reason",""),
                    "appeal_type": a.get("punishment_type","")}
        return {"admin": False, "user": False}
    return {
        "admin": True,
        "username": s["username"],
        "can_review_admin_complaints": s.get("can_review_admin_complaints", False)
    }

@app.post("/api/auth/user-token")
async def create_user_token(body: dict):
    token = body.get("token")
    if not token:
        raise HTTPException(400, "token required")
    USER_TOKENS[token] = {"confirmed": False, "username": None, "tg_id": None}
    return {"ok": True}

@app.get("/api/auth/user-poll")
async def poll_user_token(token: str, response: Response):
    if token not in USER_TOKENS:
        raise HTTPException(404, "Token not found")
    data = USER_TOKENS[token]
    if data["confirmed"]:
        session_token = secrets.token_urlsafe(32)
        session_data = {"username": data["username"], "tg_id": data["tg_id"]}
        save_user_session(session_token, session_data)
        response.set_cookie("vn_user_session", session_token, httponly=True, samesite="lax", max_age=86400)
        del USER_TOKENS[token]
        return {"confirmed": True, "username": data["username"]}
    return {"confirmed": False}

@app.post("/api/auth/user-confirm")
async def confirm_user_token(body: dict):
    """Бот подтверждает токен"""
    token   = body.get("token")
    username= body.get("username")
    tg_id   = body.get("tg_id")
    secret  = body.get("secret")
    if secret != BOT_TOKEN:
        raise HTTPException(403, "Forbidden")
    # Appeal token
    if token and token in APPEAL_TOKENS:
        APPEAL_TOKENS[token]["confirmed"] = True
        APPEAL_TOKENS[token]["username"] = username
        APPEAL_TOKENS[token]["tg_id"] = tg_id
        return {"ok": True, "type": "appeal"}
    # Regular token
    if token not in USER_TOKENS:
        raise HTTPException(404, "Token not found")
    USER_TOKENS[token] = {"confirmed": True, "username": username, "tg_id": tg_id}
    return {"ok": True, "type": "user"}

@app.post("/api/auth/user-logout")
async def user_logout(request: Request, response: Response):
    token = request.cookies.get("vn_user_session")
    if token:
        del_user_session(token)
    response.delete_cookie("vn_user_session")
    return {"ok": True}

# Appeal token endpoints
@app.post("/api/auth/appeal-token")
async def create_appeal_token(body: dict):
    """Бот создаёт appeal-токен для конкретного пользователя"""
    secret = body.get("secret")
    if secret != BOT_TOKEN:
        raise HTTPException(403, "Forbidden")
    token = body.get("token")
    tg_id = body.get("tg_id")
    username = body.get("username", "")
    reason = body.get("reason", "")
    punishment_type = body.get("punishment_type", "")
    if not token:
        raise HTTPException(400, "token required")
    APPEAL_TOKENS[token] = {
        "tg_id": tg_id, "username": username, "reason": reason,
        "punishment_type": punishment_type, "confirmed": False,
        "created_at": datetime.now().isoformat()
    }
    return {"ok": True}

@app.get("/api/auth/appeal-poll")
async def poll_appeal_token(token: str, response: Response):
    """Сайт опрашивает: подтверждён ли appeal-токен"""
    if token not in APPEAL_TOKENS:
        raise HTTPException(404, "Token not found")
    data = APPEAL_TOKENS[token]
    if data.get("confirmed"):
        session_token = secrets.token_urlsafe(32)
        session_data = {
            "username": data["username"], "tg_id": data["tg_id"],
            "appeal_reason": data.get("reason", ""),
            "appeal_type": data.get("punishment_type", "")
        }
        save_user_session(session_token, session_data)
        response.set_cookie("vn_user_session", session_token, httponly=True, samesite="lax", max_age=86400)
        del APPEAL_TOKENS[token]
        return {"confirmed": True, "username": data["username"],
                "appeal_reason": data.get("reason",""), "appeal_type": data.get("punishment_type","")}
    return {"confirmed": False}

# ─── PUBLIC ENDPOINTS ────────────────────────────────────────────────────────

@app.post("/api/complaints/submit")
async def submit_complaint(request: Request, body: ComplaintIn):
    # Проверяем авторизацию пользователя
    token = request.cookies.get("vn_user_session")
    tg_id = body.tg_id or 0
    username = body.username
    u = get_user_session(token) if token else None
    if u:
        username = u["username"]
        tg_id = u.get("tg_id") or tg_id

    conn = db()
    cur = conn.execute("""
        INSERT INTO admin_complaints
            (user_id, username, admin_username, description, complaint_text,
             evidence, status, complaint_type, submitter_tg_id, submitter_username, created_at)
        VALUES (0,?,?,?,?,?,'pending',?,?,?,datetime('now'))
    """, (
        username, body.admin_username, body.description, body.complaint_text,
        body.evidence or "", body.complaint_type, tg_id, username,
    ))
    conn.commit()
    cid = cur.lastrowid
    c = one(conn, "SELECT * FROM admin_complaints WHERE id=?", (cid,))
    conn.close()
    await notify_admins_new(c)
    return {"id": cid, "ok": True}

@app.get("/api/my-complaints")
async def my_complaints(request: Request):
    token = request.cookies.get("vn_user_session")
    u = get_user_session(token) if token else None
    if not u:
        raise HTTPException(401, "Требуется авторизация")
    uname = u["username"].lower().lstrip("@")
    conn = db()
    data = rows(conn, """
        SELECT id, username, admin_username, complaint_type, description,
               complaint_text, status, admin_comment, created_at, handled_at
        FROM admin_complaints WHERE lower(ltrim(username,'@'))=?
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
        "complaints_today":    one(conn,"SELECT COUNT(*) as c FROM admin_complaints WHERE date(created_at)=?",(today,))["c"],
        "pending":             one(conn,"SELECT COUNT(*) as c FROM admin_complaints WHERE status='pending'")["c"],
        "active_mutes":        one(conn,"SELECT COUNT(*) as c FROM mutes WHERE is_active=1")["c"],
        "active_bans":         one(conn,"SELECT COUNT(*) as c FROM bans WHERE is_active=1")["c"],
        "total_users":         one(conn,"SELECT COUNT(*) as c FROM bot_users")["c"],
        "pending_user_reports":one(conn,"SELECT COUNT(*) as c FROM user_reports WHERE status='pending'")["c"],
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
    s = require_admin(request)
    if not s.get("can_review_admin_complaints"):
        raise HTTPException(403, "Нет прав на просмотр жалоб на администраторов")
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
    s = require_admin(request)
    if not s.get("can_review_admin_complaints"):
        raise HTTPException(403, "Нет прав")
    conn = db()
    c = one(conn, "SELECT * FROM admin_complaints WHERE id=?", (cid,))
    conn.close()
    if not c: raise HTTPException(404, "Не найдено")
    return c

@app.patch("/api/complaints/{cid}")
async def review_complaint(request: Request, cid: int, body: ReviewIn):
    s = require_admin(request)
    if not s.get("can_review_admin_complaints"):
        raise HTTPException(403, "Нет прав")
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
    return c

# Наказания из жалоб на администраторов
@app.post("/api/complaints/{cid}/punish")
async def punish_from_complaint(request: Request, cid: int, body: PunishIn):
    """Выдать бан или варн пользователю из жалобы на администратора"""
    s = require_admin(request)
    if not s.get("can_review_admin_complaints"):
        raise HTTPException(403, "Нет прав")

    conn = db()
    # Site ban
    expires_at = None
    if body.expires_hours:
        expires_at = (datetime.now() + timedelta(hours=body.expires_hours)).isoformat()

    conn.execute("""
        INSERT INTO site_bans (username, tg_id, reason, issued_by, expires_at)
        VALUES (?,?,?,?,?)
    """, (body.username.lower().lstrip("@"), body.tg_id or 0,
          body.reason, s["username"], expires_at))
    conn.commit()
    conn.close()

    # Выгнать из активных сессий
    for tok, sess in list(USER_SESSIONS.items()):
        if sess.get("username","").lower() == body.username.lower().lstrip("@"):
            del_user_session(tok)
    for tok, sess in list(SESSIONS.items()):
        if sess.get("username","").lower() == body.username.lower().lstrip("@"):
            del SESSIONS[tok]

    # Уведомить через бота
    if body.tg_id:
        await tg_ban_user(body.tg_id, body.reason, s["username"])

    return {"ok": True}

@app.post("/api/complaints/{cid}/warn")
async def warn_from_complaint(request: Request, cid: int, body: PunishIn):
    """Выдать варн пользователю из жалобы"""
    s = require_admin(request)
    if not s.get("can_review_admin_complaints"):
        raise HTTPException(403, "Нет прав")

    conn = db()
    expires_at = (datetime.now() + timedelta(days=7)).isoformat()
    conn.execute("""
        INSERT INTO site_warns (username, tg_id, reason, issued_by, expires_at)
        VALUES (?,?,?,?,?)
    """, (body.username.lower().lstrip("@"), body.tg_id or 0,
          body.reason, s["username"], expires_at))
    conn.commit()
    conn.close()

    if body.tg_id:
        await tg_warn_user(body.tg_id, body.reason, s["username"])

    return {"ok": True}

# ─── USER REPORTS (/report из чата) ──────────────────────────────────────────

@app.post("/api/user-reports/submit")
async def submit_user_report(body: dict):
    """Бот отправляет /report жалобу на сайт"""
    secret = body.get("secret")
    if secret != BOT_TOKEN:
        raise HTTPException(403, "Forbidden")
    conn = db()
    cur = conn.execute("""
        INSERT INTO user_reports
            (reporter_id, reporter_username, reported_id, reported_username,
             reason, message_text, message_photo, message_link, chat_id, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))
    """, (
        body.get("reporter_id",0), body.get("reporter_username",""),
        body.get("reported_id",0), body.get("reported_username",""),
        body.get("reason",""), body.get("message_text",""),
        body.get("message_photo",""), body.get("message_link",""),
        body.get("chat_id",0),
    ))
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    log.info(f"User report #{rid} от {body.get('reporter_username')} на {body.get('reported_username')}")
    return {"id": rid, "ok": True}

@app.get("/api/user-reports")
async def get_user_reports(request: Request, status: str = "all"):
    require_admin(request)
    conn = db()
    if status == "all":
        data = rows(conn, "SELECT * FROM user_reports ORDER BY created_at DESC LIMIT 200")
    else:
        data = rows(conn, "SELECT * FROM user_reports WHERE status=? ORDER BY created_at DESC LIMIT 200", (status,))
    conn.close()
    return data

@app.patch("/api/user-reports/{rid}")
async def handle_user_report(request: Request, rid: int, body: UserReportActionIn):
    """Администратор принимает решение по /report жалобе"""
    s = require_admin(request)
    conn = db()
    r = one(conn, "SELECT * FROM user_reports WHERE id=?", (rid,))
    if not r:
        conn.close()
        raise HTTPException(404, "Не найдено")

    conn.execute("""
        UPDATE user_reports SET status='resolved', handled_by=?, handled_action=?, handled_at=datetime('now')
        WHERE id=?
    """, (s["username"], body.action, rid))
    conn.commit()
    conn.close()

    # Применяем наказание через бота
    reported_id  = r.get("reported_id", 0)
    reporter_id  = r.get("reporter_id", 0)
    chat_id      = r.get("chat_id", 0)
    action       = body.action
    reason       = body.reason or r.get("reason", "Нарушение правил")
    admin_name   = s["username"]

    result_text  = ""
    if action == "warn":
        result_text = "⚠️ Предупреждение выдано"
        await _bot_action("warn", reported_id, chat_id, reason, admin_name)
    elif action == "mute1":
        result_text = "🔇 Мут на 1 день выдан"
        await _bot_action("mute", reported_id, chat_id, reason, admin_name, days=1)
    elif action == "mute2":
        result_text = "🔇 Мут на 2 дня выдан"
        await _bot_action("mute", reported_id, chat_id, reason, admin_name, days=2)
    elif action == "mute3":
        result_text = "🔇 Мут на 3 дня выдан"
        await _bot_action("mute", reported_id, chat_id, reason, admin_name, days=3)
    elif action == "ban":
        result_text = "🚫 Бан выдан"
        await _bot_action("ban", reported_id, chat_id, reason, admin_name)
    elif action == "dismiss":
        result_text = "❌ Жалоба отклонена"

    # Уведомить жалобщика
    if reporter_id:
        await tg_send(reporter_id,
            f"{'✅' if action != 'dismiss' else '❌'} <b>Ваша жалоба рассмотрена</b>\n\n"
            f"👮 Администратор: @{admin_name}\n"
            f"📝 Результат: {result_text}\n"
            f"💬 {body.comment or ''}"
        )

    # Отправить в чат результат
    if chat_id and action != "dismiss":
        await tg_send_chat(chat_id,
            f"{result_text}\n"
            f"👤 Пользователь: {r.get('reported_username','')}\n"
            f"👮 Администратор: @{admin_name}\n"
            f"📝 Причина: {reason}"
        )

    return {"ok": True, "result": result_text}

async def _bot_action(action: str, user_id: int, chat_id: int, reason: str, admin: str, days: int = 0):
    """Выполнить действие через бота используя его API"""
    if not BOT_TOKEN or not user_id or not chat_id:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            if action == "warn":
                # Варн — через бота командой
                await client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": f"⚠️ Пользователю выдано предупреждение\nПричина: {reason}\n👮 @{admin}",
                    "parse_mode": "HTML"
                })
            elif action == "mute":
                until = int((datetime.now() + timedelta(days=days)).timestamp())
                await client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/restrictChatMember", json={
                    "chat_id": chat_id, "user_id": user_id,
                    "until_date": until,
                    "permissions": {"can_send_messages": False, "can_send_media_messages": False,
                                    "can_send_polls": False, "can_send_other_messages": False}
                })
            elif action == "ban":
                await client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/banChatMember", json={
                    "chat_id": chat_id, "user_id": user_id
                })
        except Exception as e:
            log.error(f"Bot action error: {e}")

async def tg_send_chat(chat_id: int, text: str):
    if not BOT_TOKEN:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
        except Exception as e:
            log.error(f"Chat send error: {e}")

# ─── SITE ADMIN MANAGEMENT ───────────────────────────────────────────────────

@app.post("/api/site-admins/add")
async def add_site_admin(body: dict):
    """Бот добавляет нового администратора сайта"""
    secret = body.get("secret")
    if secret != BOT_TOKEN:
        raise HTTPException(403, "Forbidden")

    tg_id    = body.get("tg_id")
    username = body.get("username", "").lower().lstrip("@")
    added_by = body.get("added_by", 0)
    can_review = int(body.get("can_review_admin_complaints", 0))
    password = body.get("password", gen_password())
    pw_hash  = hashlib.sha256(password.encode()).hexdigest()

    conn = db()
    try:
        conn.execute("""
            INSERT INTO site_admins (tg_id, username, password_hash, added_by, can_review_admin_complaints)
            VALUES (?,?,?,?,?)
            ON CONFLICT(tg_id) DO UPDATE SET
                username=excluded.username, password_hash=excluded.password_hash,
                can_review_admin_complaints=excluded.can_review_admin_complaints,
                is_active=1
        """, (tg_id, username, pw_hash, added_by, can_review))
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(500, str(e))
    conn.close()

    return {"ok": True, "username": username, "password": password, "can_review_admin_complaints": bool(can_review)}

@app.post("/api/site-admins/grant-complaints")
async def grant_complaint_review(body: dict):
    """Выдать право рассматривать жалобы на администраторов (только /addjbadm)"""
    secret = body.get("secret")
    if secret != BOT_TOKEN:
        raise HTTPException(403, "Forbidden")
    username = body.get("username","").lower().lstrip("@")
    conn = db()
    existing = one(conn, "SELECT * FROM site_admins WHERE lower(username)=? AND is_active=1", (username,))
    if not existing:
        conn.close()
        raise HTTPException(404, "Admin not found")
    conn.execute("UPDATE site_admins SET can_review_admin_complaints=1 WHERE lower(username)=?", (username,))
    conn.commit()
    conn.close()
    log.info(f"Granted admin complaint review to {username}")
    return {"ok": True}

@app.get("/api/site-admins")
async def get_site_admins(request: Request):
    require_admin(request)
    conn = db()
    data = rows(conn, "SELECT id, tg_id, username, added_by, added_at, can_review_admin_complaints, is_active FROM site_admins ORDER BY added_at DESC")
    conn.close()
    return data

# ─── OTHER ADMIN ENDPOINTS ───────────────────────────────────────────────────

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
        try:
            u["warns"]     = (one(conn,"SELECT COUNT(*) as c FROM warns WHERE user_id=? AND expires_at>datetime('now')",(uid,)) or {}).get("c", 0)
            u["muted"]     = ((one(conn,"SELECT COUNT(*) as c FROM mutes WHERE user_id=? AND is_active=1",(uid,)) or {}).get("c", 0)) > 0
            u["banned"]    = ((one(conn,"SELECT COUNT(*) as c FROM bans WHERE user_id=? AND is_active=1",(uid,)) or {}).get("c", 0)) > 0
            u["site_banned"] = ((one(conn,"SELECT COUNT(*) as c FROM site_bans WHERE lower(username)=? AND is_active=1 AND (expires_at IS NULL OR expires_at>datetime('now'))",(u.get("username","").lower(),)) or {}).get("c", 0)) > 0
        except Exception:
            u["warns"] = 0; u["muted"] = False; u["banned"] = False; u["site_banned"] = False
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

@app.get("/api/auth/user-check")
async def user_auth_check(username: str):
    if not username:
        raise HTTPException(400, "username required")
    conn = db()
    u = one(conn,"SELECT * FROM bot_users WHERE lower(ltrim(username,'@'))=?",(username.lower().lstrip('@'),))
    conn.close()
    if not u:
        return {"ok": False, "reason": "not_found"}
    return {"ok": True, "username": u["username"], "first_name": u.get("first_name","")}

# ─── FRONTEND ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    with open(path, encoding="utf-8") as f:
        return f.read()
