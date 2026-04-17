"""
VapeNeon — веб-панель администратора
Запускается рядом с ботом, читает ту же data/bot_database.db
"""

import os
import sqlite3
import logging
import httpx
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ─── CONFIG ──────────────────────────────────────────────────────────────────

BOT_TOKEN  = os.getenv("BOT_TOKEN", "")
ADMIN_IDS  = list(map(int, os.getenv("ADMIN_IDS", "").split(","))) if os.getenv("ADMIN_IDS") else []
CHAT_ID    = int(os.getenv("CHAT_ID", "0"))
DB_PATH    = "data/bot_database.db"
WEB_SECRET = os.getenv("WEB_SECRET", "changeme")   # простой токен-доступ к API

logging.basicConfig(level=logging.INFO, format="%(asctime)s [WEB] %(levelname)s %(message)s")
log = logging.getLogger("web")

# ─── APP ─────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Веб-панель запущена на :8080")
    yield

app = FastAPI(title="VapeNeon Admin", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def rows(conn, sql, params=()):
    cur = conn.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]

def one(conn, sql, params=()):
    cur = conn.execute(sql, params)
    r = cur.fetchone()
    return dict(r) if r else None

async def tg_send(user_id: int, text: str):
    """Отправить сообщение через Telegram Bot API"""
    if not BOT_TOKEN:
        log.warning("BOT_TOKEN не задан — уведомление не отправлено")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.post(url, json={
                "chat_id": user_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
        except Exception as e:
            log.error(f"Ошибка отправки в Telegram: {e}")

async def notify_admins(complaint: dict):
    """Разослать уведомление о новой жалобе всем ADMIN_IDS"""
    type_map = {
        "abuse": "Злоупотребление полномочиями",
        "unfair_ban": "Несправедливый бан/мут",
        "inaction": "Бездействие",
        "rudeness": "Грубость / Неуважение",
        "other": "Другое",
    }
    text = (
        f"🔔 <b>Новая жалоба #{complaint['id']}</b>\n\n"
        f"👤 Заявитель: <code>{complaint['username']}</code>\n"
        f"⚡ На администратора: <code>{complaint['admin_username']}</code>\n"
        f"📌 Тип: {type_map.get(complaint['complaint_type'], complaint['complaint_type'])}\n"
        f"📝 Описание: {complaint['description']}\n\n"
        f"<i>Откройте веб-панель для рассмотрения.</i>"
    )
    for admin_id in ADMIN_IDS:
        await tg_send(admin_id, text)

def check_secret(request: Request):
    token = request.headers.get("X-Secret") or request.query_params.get("secret")
    if token != WEB_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

# ─── PYDANTIC MODELS ─────────────────────────────────────────────────────────

class ComplaintIn(BaseModel):
    username: str           # @username заявителя
    admin_username: str     # @username администратора
    complaint_type: str     # abuse | unfair_ban | inaction | rudeness | other
    description: str        # краткое описание
    complaint_text: str     # полный текст
    evidence: Optional[str] = None

class StatusIn(BaseModel):
    status: str             # resolved | rejected

# ─── API ROUTES ──────────────────────────────────────────────────────────────

# --- STATS ---

@app.get("/api/stats")
async def get_stats(request: Request):
    check_secret(request)
    conn = db()
    today = datetime.now().strftime("%Y-%m-%d")

    complaints_today = one(conn,
        "SELECT COUNT(*) as c FROM admin_complaints WHERE date(created_at)=?", (today,))["c"]
    pending = one(conn,
        "SELECT COUNT(*) as c FROM admin_complaints WHERE status='pending'")["c"]
    active_mutes = one(conn,
        "SELECT COUNT(*) as c FROM mutes WHERE is_active=1")["c"]
    active_bans = one(conn,
        "SELECT COUNT(*) as c FROM bans WHERE is_active=1")["c"]
    total_users = one(conn,
        "SELECT COUNT(*) as c FROM bot_users")["c"]

    # Жалобы по дням за последние 7 дней
    chart = rows(conn, """
        SELECT date(created_at) as day, COUNT(*) as count
        FROM admin_complaints
        WHERE created_at >= date('now','-6 days')
        GROUP BY date(created_at)
        ORDER BY day
    """)

    conn.close()
    return {
        "complaints_today": complaints_today,
        "pending": pending,
        "active_mutes": active_mutes,
        "active_bans": active_bans,
        "total_users": total_users,
        "chart": chart,
    }

# --- COMPLAINTS ---

@app.get("/api/complaints")
async def get_complaints(request: Request, status: str = "all", q: str = ""):
    check_secret(request)
    conn = db()
    sql = "SELECT * FROM admin_complaints"
    params = []
    conditions = []

    if status != "all":
        conditions.append("status=?")
        params.append(status)
    if q:
        conditions.append("(username LIKE ? OR admin_username LIKE ? OR description LIKE ?)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY created_at DESC LIMIT 200"

    data = rows(conn, sql, params)
    conn.close()
    return data

@app.post("/api/complaints")
async def create_complaint(request: Request, body: ComplaintIn):
    check_secret(request)
    conn = db()
    cur = conn.execute("""
        INSERT INTO admin_complaints
            (user_id, username, admin_username, description, complaint_text, evidence, status, created_at)
        VALUES (0, ?, ?, ?, ?, ?, 'pending', datetime('now'))
    """, (
        body.username, body.admin_username,
        body.description, body.complaint_text,
        body.evidence or "",
    ))
    conn.commit()
    complaint_id = cur.lastrowid
    complaint = one(conn, "SELECT * FROM admin_complaints WHERE id=?", (complaint_id,))
    conn.close()

    # Уведомляем всех админов через бота
    await notify_admins(complaint)
    log.info(f"Новая жалоба #{complaint_id} от {body.username} на {body.admin_username}")
    return complaint

@app.get("/api/complaints/{complaint_id}")
async def get_complaint(request: Request, complaint_id: int):
    check_secret(request)
    conn = db()
    c = one(conn, "SELECT * FROM admin_complaints WHERE id=?", (complaint_id,))
    conn.close()
    if not c:
        raise HTTPException(404, "Жалоба не найдена")
    return c

@app.patch("/api/complaints/{complaint_id}")
async def update_complaint(request: Request, complaint_id: int, body: StatusIn):
    check_secret(request)
    if body.status not in ("resolved", "rejected", "pending"):
        raise HTTPException(400, "Недопустимый статус")
    conn = db()
    conn.execute(
        "UPDATE admin_complaints SET status=?, handled_at=datetime('now') WHERE id=?",
        (body.status, complaint_id),
    )
    conn.commit()
    c = one(conn, "SELECT * FROM admin_complaints WHERE id=?", (complaint_id,))
    conn.close()
    return c

# --- USERS ---

@app.get("/api/users")
async def get_users(request: Request, q: str = ""):
    check_secret(request)
    conn = db()
    if q:
        data = rows(conn,
            "SELECT * FROM bot_users WHERE username LIKE ? OR first_name LIKE ? ORDER BY last_seen DESC LIMIT 100",
            (f"%{q}%", f"%{q}%"))
    else:
        data = rows(conn,
            "SELECT * FROM bot_users ORDER BY last_seen DESC LIMIT 100")

    # Для каждого пользователя добавим варны/муты/баны
    for u in data:
        uid = u["user_id"]
        u["warns"] = one(conn,
            "SELECT COUNT(*) as c FROM warns WHERE user_id=? AND expires_at > datetime('now')", (uid,))["c"]
        u["muted"] = one(conn,
            "SELECT COUNT(*) as c FROM mutes WHERE user_id=? AND is_active=1", (uid,))["c"] > 0
        u["banned"] = one(conn,
            "SELECT COUNT(*) as c FROM bans WHERE user_id=? AND is_active=1", (uid,))["c"] > 0

    conn.close()
    return data

# --- MODERATION LOGS ---

@app.get("/api/logs")
async def get_logs(request: Request, kind: str = "all"):
    check_secret(request)
    conn = db()
    result = []

    if kind in ("all", "warn"):
        warns = rows(conn, """
            SELECT 'warn' as kind, issued_at as ts, issued_by, user_id, reason, NULL as expires_at
            FROM warns ORDER BY issued_at DESC LIMIT 50
        """)
        result += warns

    if kind in ("all", "mute"):
        mutes = rows(conn, """
            SELECT 'mute' as kind, issued_at as ts, issued_by, user_id, reason, expires_at
            FROM mutes ORDER BY issued_at DESC LIMIT 50
        """)
        result += mutes

    if kind in ("all", "ban"):
        bans = rows(conn, """
            SELECT 'ban' as kind, issued_at as ts, issued_by, user_id, reason, expires_at
            FROM bans ORDER BY issued_at DESC LIMIT 50
        """)
        result += bans

    conn.close()
    result.sort(key=lambda x: x.get("ts") or "", reverse=True)
    return result[:200]

# --- BROADCAST PREVIEW (статика, актуальные тексты из БД не нужны) ---

@app.get("/api/broadcast/next")
async def broadcast_next(request: Request):
    check_secret(request)
    SEND_HOURS = sorted([7, 10, 13, 16, 19, 22, 1])
    now = datetime.now()
    msk_h = now.hour
    msk_m = now.minute
    next_h = next((h for h in SEND_HOURS if h > msk_h), SEND_HOURS[0])
    total_min = ((next_h - msk_h) % 24) * 60 - msk_m
    return {"next_hour": next_h, "minutes_left": max(0, total_min)}

# ─── FRONTEND ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    with open(html_path, encoding="utf-8") as f:
        return f.read()
