"""
VapeNeon — публичный сайт + панель администратора
"""

import os
import json
import logging
import hashlib
import secrets
import httpx
import string
import random
import psycopg2
import psycopg2.pool
import psycopg2.extras
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

# ─── AI-ПОМОЩНИК (Groq) ───────────────────────────────────────────────────────
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"

# Username, куда AI отправляет пользователя, если в базе знаний нет ответа.
# ВАЖНО: сюда всегда подставляется только этот контакт, никакие другие админы.
AI_FALLBACK_CONTACT = "@KoshakFSB"

AI_KNOWLEDGE_BASE = """
ПРАВИЛА ЧАТА VapeNeon:
01. Нарушение правил отправки объявлений («НПОО») — максимум 1 объявление в 1.5 часа, максимум 5 в день, редактирование только в тексте самого объявления, минимум 30 сообщений от других участников перед повтором. Наказание: мут на 1–3 дня.
02. Упоминание сторонних вейп-шопов — 1 предупреждение за каждое упоминание, 3 предупреждения = бан.
03. Использование бота участниками чата запрещено — автоматический мут на 1 день.
04. Реклама запрещённых веществ категорически запрещена — перманентный бан.
05. Конкуренция и перепродажа нового товара запрещена — мут на 7 дней.
06. Беспричинное упоминание администраторов запрещено (вопросы — в общий чат, жалобы — команда /report; администраторы могут упоминать друг друга) — 1 предупреждение.
07. Слив личных данных (фото, видео, деанон без согласия) запрещён — бан на 30 дней.
08. Флуд и спам (повторяющиеся сообщения, символы, цепочки эмодзи) — предупреждение, затем мут на 1–7 дней.
09. Оскорбления и токсичность (прямые оскорбления, унижения, призывы к конфликтам) — предупреждение, затем мут до 3 дней.
10. Запрещённый медиаконтент (шокирующий/непристойный контент, 18+) — мут до 7 дней, бан при повторе.
11. Технические нарушения (взлом, обход ограничений, мультиаккаунты) — бан от 30 дней до перманентного.
12. Политика и религия (острые дискуссии, провокации, экстремистская пропаганда) — мут на 7 дней, затем бан.
13. Продажа аккаунтов социальных сетей запрещена (игровые аккаунты продавать можно) — мут на 7 дней, бан при повторе.
14. Продажа сим-карт, банковских карт и доступов к финансовым счетам запрещена — перманентный бан.

СИСТЕМА НАКАЗАНИЙ: Варн — предупреждение, сгорает через 7 дней. Мут — временный запрет писать в чат. Бан — исключение из чата. 3 варна = автоматический бан. Решение владельцев чата окончательное.

КАК СДЕЛАТЬ ЗАКАЗ: Написать владельцу @darknesss43 (Вика) или администратору @vavapipo. 1) указать что хотите заказать, 2) оплатить, 3) доставка в конце недели. Актуальный прайс: t.me/vapeneonVP/3

ЖАЛОБЫ: На администратора — через форму «Подать жалобу» на этом сайте, рассмотрение до 48 часов. На обычного участника чата — команда /report [причина] в ответ на сообщение в чате, рассмотрение до 24 часов. Ложные жалобы наказуемы.

ПОЛЕЗНЫЕ ССЫЛКИ:
- Барахолка ВП | Сосновка | Крп: t.me/+-pfuJP__fQM2NDcy
- Бот: @TheVapeNeonBot (участникам чата использовать бота запрещено правилом 03)
- Владелец: @darknesss43
- ГА/ТА: @KoshakFSB
- Мод: @Crazy_Anasha
- Заказы: @vavapipo
"""

AI_SYSTEM_PROMPT = f"""Ты — помощник сайта чат-сообщества VapeNeon. Отвечай ТОЛЬКО на основе базы знаний ниже. Никогда не придумывай факты, цены, сроки, правила или контакты, которых нет в базе.

СТИЛЬ ОТВЕТА (строго):
- Только простой текст, БЕЗ какого-либо форматирования: никогда не используй **жирный**, *курсив*, markdown-заголовки, разметку списков через "-" или "*". Если нужен список — пиши через эмодзи или цифры с точкой, обычным текстом.
- Кратко: 2-5 предложений или короткий список.
- Дружелюбный тон, можно по одному эмодзи в начале смысловых строк.

ЕСЛИ ОТВЕТА НЕТ В БАЗЕ ЗНАНИЙ:
Честно скажи, что не знаешь, и предложи написать {AI_FALLBACK_CONTACT}. Никогда не предлагай писать другим администраторам или контактам в качестве запасного варианта — только {AI_FALLBACK_CONTACT}.

ЗАПРЕЩЁННЫЕ ТЕМЫ:
Никогда не давай советов и информации о запрещённых веществах, обходе правил чата, взломах — только модерация, заказы вейпов, правила чата и контакты.

ЛОГ НАКАЗАНИЙ ПОЛЬЗОВАТЕЛЯ:
Если пользователь спрашивает, за что ему дали мут / бан / варн (в любой формулировке, например «за что мне мут?», «почему забанили», «за что варн»), НИКОГДА не вызывай функцию get_punishment_history, пока пользователь явно не указал свой числовой Telegram ID в этом диалоге. Сначала вежливо попроси его прислать свой Telegram ID (объясни, что это нужно для уточнения). Как только пользователь прислал числовой ID — вызови get_punishment_history с этим ID. Когда получишь результат функции — объясни причину наказания простыми словами и, если это соответствует одному из правил в базе знаний, укажи номер и суть этого правила. Если наказаний не найдено — так и скажи.

БАЗА ЗНАНИЙ:
{AI_KNOWLEDGE_BASE}"""

AI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_punishment_history",
            "description": "Возвращает последние наказания (варны, муты, баны) пользователя чата по его Telegram ID, включая причину каждого наказания.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tg_id": {
                        "type": "integer",
                        "description": "Числовой Telegram ID пользователя, который явно назвал его в диалоге."
                    }
                },
                "required": ["tg_id"]
            }
        }
    }
]

PG_CONF = dict(
    host=os.getenv("POSTGRES_HOST", "localhost"),
    port=os.getenv("POSTGRES_PORT", "5432"),
    user=os.getenv("POSTGRES_USER", "vapeneon"),
    password=os.getenv("POSTGRES_PASSWORD", ""),
    dbname=os.getenv("POSTGRES_DB", "vapeneon"),
)

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

# ─── 2FA (профиль) ───────────────────────────────────────────────────────────
# Токены привязки 2FA через бота (тот же механизм, что и вход обычных пользователей)
TFA_BIND_TOKENS: dict = {}   # token -> {confirmed, subject_type, subject_username, tg_id, tg_username}
# Коды подтверждения при входе (после ввода пароля / телеграм-логина, если 2FA включена)
TFA_LOGIN_CODES: dict = {}   # login_token -> {code, subject_type, username, ip, expires, extra}
TFA_CODE_TTL = timedelta(minutes=5)

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
# База — PostgreSQL. Схема создаётся один раз через migrate_to_postgres.py,
# здесь только держим пул соединений и подчищаем протухшие сессии на старте.

_POOL: "psycopg2.pool.ThreadedConnectionPool" = None

def _init_db():
    global _POOL
    _POOL = psycopg2.pool.ThreadedConnectionPool(minconn=1, maxconn=10, **PG_CONF)

    conn = _POOL.getconn()
    try:
        cur = conn.cursor()

        # На случай, если миграция ещё не накатывала новые колонки —
        # безопасно (IF NOT EXISTS поддерживается Postgres нативно).
        for col, typ in [
            ("complaint_type",    "TEXT DEFAULT 'other'"),
            ("admin_comment",     "TEXT"),
            ("submitter_tg_id",   "BIGINT DEFAULT 0"),
            ("submitter_username","TEXT"),
        ]:
            cur.execute(f"ALTER TABLE admin_complaints ADD COLUMN IF NOT EXISTS {col} {typ}")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS bug_reports (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                reporter_username TEXT,
                reporter_tg_id BIGINT DEFAULT 0,
                status TEXT DEFAULT 'new',
                created_at TIMESTAMP DEFAULT now()
            )
        """)

        cur.execute("ALTER TABLE admin_sessions ADD COLUMN IF NOT EXISTS ip TEXT")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS two_factor_auth (
                id SERIAL PRIMARY KEY,
                subject_type TEXT NOT NULL,      -- 'admin' | 'user'
                username TEXT NOT NULL,
                tg_id BIGINT,
                tg_username TEXT,
                enabled INT DEFAULT 0,
                created_at TIMESTAMP DEFAULT now(),
                UNIQUE(subject_type, username)
            )
        """)

        cur.execute("DELETE FROM admin_sessions WHERE expires_at < now()")
        cur.execute("DELETE FROM user_sessions WHERE expires_at < now()")
        conn.commit()
    finally:
        _POOL.putconn(conn)

class _PooledConn:
    """Тонкая обёртка над соединением из пула: весь остальной код вызывает
    conn.execute(...)/conn.commit()/conn.close() точно так же, как раньше
    для sqlite3.Connection — но close() возвращает соединение в пул,
    а не рвёт его."""

    def __init__(self, raw):
        self._raw = raw
        raw.cursor_factory = psycopg2.extras.RealDictCursor

    def execute(self, sql, params=()):
        cur = self._raw.cursor()
        cur.execute(sql, params)
        return cur

    def cursor(self):
        return self._raw.cursor()

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        _POOL.putconn(self._raw)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self._raw.rollback()
        else:
            self._raw.commit()
        self.close()


def db():
    """Возвращает соединение из пула (обёрнутое), совместимое по интерфейсу
    с тем, как раньше использовался sqlite3.Connection в этом файле."""
    raw = _POOL.getconn()
    return _PooledConn(raw)

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
    rec = one(conn, "SELECT * FROM site_admins WHERE lower(username)=%s AND is_active=1", (uname,))
    conn.close()
    return rec

def check_admin_password(username: str, password: str) -> Optional[dict]:
    """Проверить логин/пароль. Возвращает dict с правами или None"""
    uname = username.lower().lstrip("@")
    pw_hash = hashlib.sha256(password.encode()).hexdigest()

    # Сначала проверяем БД
    conn = db()
    rec = one(conn, "SELECT * FROM site_admins WHERE lower(username)=%s AND is_active=1", (uname,))
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

def get_client_ip(request: Request) -> str:
    """IP клиента с учётом обратного прокси."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

def get_tfa_record(subject_type: str, username: str) -> Optional[dict]:
    conn = db()
    rec = one(conn, "SELECT * FROM two_factor_auth WHERE subject_type=%s AND lower(username)=%s",
              (subject_type, username.lower().lstrip("@")))
    conn.close()
    return rec

def save_tfa_binding(subject_type: str, username: str, tg_id, tg_username):
    conn = db()
    conn.execute("""
        INSERT INTO two_factor_auth (subject_type, username, tg_id, tg_username, enabled)
        VALUES (%s,%s,%s,%s,1)
        ON CONFLICT (subject_type, username) DO UPDATE SET
            tg_id=excluded.tg_id, tg_username=excluded.tg_username, enabled=1
    """, (subject_type, username.lower().lstrip("@"), tg_id, tg_username))
    conn.commit(); conn.close()

def gen_2fa_code() -> str:
    return "".join(random.choices(string.digits, k=6))

async def send_2fa_code(tg_id: int, code: str, ip: str):
    text = (
        "🔐 <b>Код входа на сайт VapeNeon</b>\n\n"
        f"Код: <code>{code}</code>\n"
        f"IP: <code>{ip}</code>\n\n"
        "Если это были не вы — никому не сообщайте этот код."
    )
    await tg_send(tg_id, text)

def is_site_banned(username: str) -> Optional[dict]:
    """Проверить бан на сайте"""
    uname = username.lower().lstrip("@")
    conn = db()
    ban = one(conn, """
        SELECT * FROM site_bans
        WHERE lower(username)=%s AND is_active=1
        AND (expires_at IS NULL OR expires_at > now())
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
        c = db(); c.execute("DELETE FROM admin_sessions WHERE token=%s", (token,)); c.commit(); c.close()
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
    row = one(c, "SELECT * FROM user_sessions WHERE token=%s AND expires_at > now()", (token,))
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
            """INSERT INTO user_sessions (token, username, tg_id, appeal_reason, appeal_type, expires_at)
               VALUES (%s,%s,%s,%s,%s,%s)
               ON CONFLICT (token) DO UPDATE SET
                   username=excluded.username, tg_id=excluded.tg_id,
                   appeal_reason=excluded.appeal_reason, appeal_type=excluded.appeal_type,
                   expires_at=excluded.expires_at""",
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
        c = db(); c.execute("DELETE FROM user_sessions WHERE token=%s", (token,)); c.commit(); c.close()
    except Exception: pass

def get_session(request: Request):
    token = request.cookies.get("vn_session")
    if not token:
        log.warning("get_session: cookie vn_session отсутствует")
        return None
    log.info(f"get_session: token={token[:12]}...")
    cur_ip = get_client_ip(request)
    if token in SESSIONS:
        s = SESSIONS[token]
        if datetime.now() > s["expires"]:
            log.warning("get_session: сессия в памяти просрочена")
            _del_session(token)
            return None
        if s.get("ip") and s["ip"] != cur_ip:
            log.warning(f"get_session: смена IP ({s['ip']} -> {cur_ip}), сессия сброшена, требуется повторный вход с 2FA")
            _del_session(token)
            return None
        log.info(f"get_session: найдена в памяти, user={s['username']}")
        return s
    # Не в кеше — восстанавливаем из БД (после перезапуска сервера)
    c = db()
    row = one(c, "SELECT * FROM admin_sessions WHERE token=%s AND expires_at > now()", (token,))
    c.close()
    if not row:
        log.warning(f"get_session: токен не найден в БД (token={token[:12]}...)")
        return None
    if row.get("ip") and row["ip"] != cur_ip:
        log.warning(f"get_session: смена IP при восстановлении сессии ({row['ip']} -> {cur_ip})")
        _del_session(token)
        return None
    s = {
        "username": row["username"],
        "can_review_admin_complaints": bool(row["can_review_admin_complaints"]),
        "expires": row["expires_at"],  # psycopg2 уже отдаёт datetime, парсить не нужно
        "ip": row.get("ip"),
    }
    SESSIONS[token] = s
    log.info(f"get_session: восстановлена из БД, user={s['username']}")
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

class AiChatMsg(BaseModel):
    role: str            # "user" | "assistant"
    content: str

class AiAskIn(BaseModel):
    message: str
    history: Optional[list[AiChatMsg]] = None

class BugReportIn(BaseModel):
    title: str
    description: Optional[str] = ""

# ─── AUTH ENDPOINTS ──────────────────────────────────────────────────────────

def _create_admin_session(response: Response, admin: dict, ip: str) -> dict:
    token = secrets.token_urlsafe(32)
    expires_dt = datetime.now() + SESSION_TTL
    SESSIONS[token] = {
        "username": admin["username"],
        "can_review_admin_complaints": admin["can_review_admin_complaints"],
        "expires": expires_dt,
        "ip": ip,
    }
    try:
        c = db()
        c.execute(
            """INSERT INTO admin_sessions (token, username, can_review_admin_complaints, expires_at, ip)
               VALUES (%s,%s,%s,%s,%s)
               ON CONFLICT (token) DO UPDATE SET
                   username=excluded.username,
                   can_review_admin_complaints=excluded.can_review_admin_complaints,
                   expires_at=excluded.expires_at,
                   ip=excluded.ip""",
            (token, admin["username"], int(admin["can_review_admin_complaints"]), expires_dt.isoformat(), ip)
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
    log.info(f"Вход: {admin['username']} (can_review_complaints={admin['can_review_admin_complaints']}, ip={ip})")
    return {"ok": True, "username": admin["username"], "can_review_admin_complaints": admin["can_review_admin_complaints"]}


@app.post("/api/auth/login")
async def login(body: LoginIn, request: Request, response: Response):
    username = body.username.lower().lstrip("@")

    # Проверяем бан на сайте
    ban = is_site_banned(username)
    if ban:
        expires = f" до {ban['expires_at']}" if ban.get('expires_at') else " (бессрочно)"
        raise HTTPException(403, f"Вы заблокированы на сайте{expires}. Причина: {ban.get('reason','')}")

    admin = check_admin_password(username, body.password)
    if not admin:
        raise HTTPException(401, "Неверный логин или пароль")

    ip = get_client_ip(request)
    tfa = get_tfa_record("admin", admin["username"])

    # 2FA обязательна для администраторов. Если ещё не привязана — пускаем в
    # аккаунт, но помечаем это в ответе, чтобы фронтенд сразу открыл вкладку
    # «Профиль» и потребовал привязку.
    if not tfa or not tfa.get("enabled"):
        result = _create_admin_session(response, admin, ip)
        result["tfa_setup_required"] = True
        return result

    # 2FA привязана — на каждый вход (и на смену IP, см. get_session) требуем код
    login_token = secrets.token_urlsafe(24)
    code = gen_2fa_code()
    TFA_LOGIN_CODES[login_token] = {
        "code": code,
        "subject_type": "admin",
        "username": admin["username"],
        "ip": ip,
        "expires": datetime.now() + TFA_CODE_TTL,
        "extra": {"can_review_admin_complaints": admin["can_review_admin_complaints"]},
    }
    await send_2fa_code(tfa["tg_id"], code, ip)
    return {"ok": True, "tfa_required": True, "login_token": login_token}


@app.post("/api/auth/verify-2fa")
async def verify_2fa(body: dict, request: Request, response: Response):
    """Подтверждение 6-значного кода из Telegram — общий эндпоинт и для
    администраторов, и для обычных пользователей с включённой 2FA."""
    login_token = body.get("login_token")
    code = (body.get("code") or "").strip()
    d = TFA_LOGIN_CODES.get(login_token)
    if not d:
        raise HTTPException(404, "Токен входа не найден или истёк")
    if datetime.now() > d["expires"]:
        del TFA_LOGIN_CODES[login_token]
        raise HTTPException(400, "Код истёк, войдите заново")
    if code != d["code"]:
        raise HTTPException(401, "Неверный код")

    del TFA_LOGIN_CODES[login_token]
    ip = get_client_ip(request)

    if d["subject_type"] == "admin":
        admin = {"username": d["username"], **d["extra"]}
        return _create_admin_session(response, admin, ip)
    else:
        session_token = secrets.token_urlsafe(32)
        session_data = {"username": d["username"], "tg_id": d["extra"].get("tg_id")}
        save_user_session(session_token, session_data)
        response.set_cookie("vn_user_session", session_token, httponly=True, samesite="lax", max_age=86400)
        return {"ok": True, "confirmed": True, "username": d["username"], "tg_id": d["extra"].get("tg_id")}

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
    tfa = get_tfa_record("admin", s["username"])
    return {
        "admin": True,
        "username": s["username"],
        "can_review_admin_complaints": s.get("can_review_admin_complaints", False),
        "tfa_enabled": bool(tfa and tfa.get("enabled")),
        "tfa_setup_required": not (tfa and tfa.get("enabled")),
    }

@app.post("/api/auth/user-token")
async def create_user_token(body: dict):
    token = body.get("token")
    if not token:
        raise HTTPException(400, "token required")
    USER_TOKENS[token] = {"confirmed": False, "username": None, "tg_id": None}
    return {"ok": True}

@app.get("/api/auth/user-poll")
async def poll_user_token(token: str, request: Request, response: Response):
    if token not in USER_TOKENS:
        raise HTTPException(404, "Token not found")
    data = USER_TOKENS[token]
    if data["confirmed"]:
        del USER_TOKENS[token]
        tfa = get_tfa_record("user", data["username"])
        if tfa and tfa.get("enabled"):
            ip = get_client_ip(request)
            login_token = secrets.token_urlsafe(24)
            code = gen_2fa_code()
            TFA_LOGIN_CODES[login_token] = {
                "code": code,
                "subject_type": "user",
                "username": data["username"],
                "ip": ip,
                "expires": datetime.now() + TFA_CODE_TTL,
                "extra": {"tg_id": data["tg_id"]},
            }
            await send_2fa_code(tfa["tg_id"], code, ip)
            return {"confirmed": True, "tfa_required": True, "login_token": login_token}
        session_token = secrets.token_urlsafe(32)
        session_data = {"username": data["username"], "tg_id": data["tg_id"]}
        save_user_session(session_token, session_data)
        response.set_cookie("vn_user_session", session_token, httponly=True, samesite="lax", max_age=86400)
        return {"confirmed": True, "username": data["username"], "tg_id": data["tg_id"]}
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
        VALUES (0,%s,%s,%s,%s,%s,'pending',%s,%s,%s,now())
        RETURNING id
    """, (
        username, body.admin_username, body.description, body.complaint_text,
        body.evidence or "", body.complaint_type, tg_id, username,
    ))
    cid = cur.fetchone()["id"]
    conn.commit()
    c = one(conn, "SELECT * FROM admin_complaints WHERE id=%s", (cid,))
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
        FROM admin_complaints WHERE lower(ltrim(username,'@'))=%s
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
        "complaints_today":    one(conn,"SELECT COUNT(*) as c FROM admin_complaints WHERE date(created_at)=%s",(today,))["c"],
        "pending":             one(conn,"SELECT COUNT(*) as c FROM admin_complaints WHERE status='pending'")["c"],
        "active_mutes":        one(conn,"SELECT COUNT(*) as c FROM mutes WHERE is_active=TRUE")["c"],
        "active_bans":         one(conn,"SELECT COUNT(*) as c FROM bans WHERE is_active=TRUE")["c"],
        "total_users":         one(conn,"SELECT COUNT(*) as c FROM bot_users")["c"],
        "pending_user_reports":one(conn,"SELECT COUNT(*) as c FROM user_reports WHERE status='pending'")["c"],
        "chart": rows(conn,"""
            SELECT date(created_at) as day, COUNT(*) as count
            FROM admin_complaints WHERE created_at >= CURRENT_DATE - INTERVAL '6 days'
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
        conds.append("status=%s"); params.append(status)
    if q:
        conds.append("(username LIKE %s OR admin_username LIKE %s OR description LIKE %s)")
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
    c = one(conn, "SELECT * FROM admin_complaints WHERE id=%s", (cid,))
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
        UPDATE admin_complaints SET status=%s,admin_comment=%s,handled_at=now() WHERE id=%s
    """, (body.status, body.comment, cid))
    conn.commit()
    c = one(conn, "SELECT * FROM admin_complaints WHERE id=%s", (cid,))
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
        VALUES (%s,%s,%s,%s,%s)
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
        VALUES (%s,%s,%s,%s,%s)
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
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
        RETURNING id
    """, (
        body.get("reporter_id",0), body.get("reporter_username",""),
        body.get("reported_id",0), body.get("reported_username",""),
        body.get("reason",""), body.get("message_text",""),
        body.get("message_photo",""), body.get("message_link",""),
        body.get("chat_id",0),
    ))
    rid = cur.fetchone()["id"]
    conn.commit()
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
        data = rows(conn, "SELECT * FROM user_reports WHERE status=%s ORDER BY created_at DESC LIMIT 200", (status,))
    conn.close()
    return data

@app.patch("/api/user-reports/{rid}")
async def handle_user_report(request: Request, rid: int, body: UserReportActionIn):
    """Администратор принимает решение по /report жалобе"""
    s = require_admin(request)
    conn = db()
    r = one(conn, "SELECT * FROM user_reports WHERE id=%s", (rid,))
    if not r:
        conn.close()
        raise HTTPException(404, "Не найдено")

    new_status = 'rejected' if body.action == 'dismiss' else 'resolved'
    conn.execute("""
        UPDATE user_reports SET status=%s, handled_by=%s, handled_action=%s, handled_at=now()
        WHERE id=%s
    """, (new_status, s["username"], body.action, rid))
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

    # Отправить в чат ОДНО итоговое сообщение с решением по жалобе.
    # Оно не удаляется автоматически, чтобы всегда было видно, кто и за что выдал наказание.
    if chat_id and action != "dismiss":
        await tg_send_chat(chat_id,
            f"{result_text}\n"
            f"👤 Пользователь: {r.get('reported_username','')}\n"
            f"👮 Администратор: @{admin_name}\n"
            f"📝 Причина: {reason}"
        )

    return {"ok": True, "result": result_text}

async def _bot_action(action: str, user_id: int, chat_id: int, reason: str, admin: str, days: int = 0):
    """Выполнить действие через бота используя его API.
    Само сообщение в чат о результате отправляется один раз из handle_user_report,
    поэтому здесь мы только применяем наказание (restrict/ban) и НЕ шлём сообщений в чат,
    чтобы не дублировать уведомление."""
    if not BOT_TOKEN or not user_id or not chat_id:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            if action == "warn":
                # Варн — предупреждение фиксируется решением жалобы,
                # сообщение в чат отправит handle_user_report одним блоком
                pass
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
            VALUES (%s,%s,%s,%s,%s)
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
    username = body.get("username", "").lower().lstrip("@")
    tg_id    = body.get("tg_id", 0)
    conn = db()
    # Ищем сначала по username, потом по tg_id (если username не задан или не найден)
    existing = one(conn, "SELECT * FROM site_admins WHERE lower(username)=%s AND is_active=1", (username,)) if username else None
    if not existing and tg_id:
        existing = one(conn, "SELECT * FROM site_admins WHERE tg_id=%s AND is_active=1", (tg_id,))
    if not existing:
        conn.close()
        raise HTTPException(404, "Admin not found")
    conn.execute(
        "UPDATE site_admins SET can_review_admin_complaints=1 WHERE id=%s",
        (existing["id"],)
    )
    conn.commit()
    conn.close()
    log.info(f"Granted admin complaint review to {existing['username']} (tg_id={existing.get('tg_id')})")
    return {"ok": True}

@app.post("/api/site-admins/revoke-complaints")
async def revoke_complaint_review(body: dict):
    """Снять право рассматривать жалобы на администраторов (только /removejbadm)"""
    secret = body.get("secret")
    if secret != BOT_TOKEN:
        raise HTTPException(403, "Forbidden")
    username = body.get("username", "").lower().lstrip("@")
    tg_id    = body.get("tg_id", 0)
    conn = db()
    existing = one(conn, "SELECT * FROM site_admins WHERE lower(username)=%s AND is_active=1", (username,)) if username else None
    if not existing and tg_id:
        existing = one(conn, "SELECT * FROM site_admins WHERE tg_id=%s AND is_active=1", (tg_id,))
    if not existing:
        conn.close()
        raise HTTPException(404, "Admin not found")
    conn.execute(
        "UPDATE site_admins SET can_review_admin_complaints=0 WHERE id=%s",
        (existing["id"],)
    )
    conn.commit()
    conn.close()
    log.info(f"Revoked admin complaint review from {existing['username']} (tg_id={existing.get('tg_id')})")
    return {"ok": True}

@app.post("/api/site-admins/deactivate")
async def deactivate_site_admin(body: dict):
    """Полностью отключить доступ администратора к сайту (вызывается из /admin_remove)"""
    secret = body.get("secret")
    if secret != BOT_TOKEN:
        raise HTTPException(403, "Forbidden")
    username = body.get("username", "").lower().lstrip("@")
    tg_id    = body.get("tg_id", 0)
    conn = db()
    existing = one(conn, "SELECT * FROM site_admins WHERE lower(username)=%s AND is_active=1", (username,)) if username else None
    if not existing and tg_id:
        existing = one(conn, "SELECT * FROM site_admins WHERE tg_id=%s AND is_active=1", (tg_id,))
    if not existing:
        conn.close()
        # Не ошибка: у пользователя могло не быть аккаунта на сайте вовсе
        return {"ok": True, "found": False}
    conn.execute(
        "UPDATE site_admins SET is_active=0, password_hash='' WHERE id=%s",
        (existing["id"],)
    )
    conn.commit()
    conn.close()
    log.info(f"Deactivated site account for {existing['username']} (tg_id={existing.get('tg_id')})")
    return {"ok": True, "found": True}

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
            "SELECT * FROM bot_users WHERE username LIKE %s OR first_name LIKE %s ORDER BY last_seen DESC LIMIT 100",
            (f"%{q}%",f"%{q}%"))
    else:
        data = rows(conn,"SELECT * FROM bot_users ORDER BY last_seen DESC LIMIT 100")
    for u in data:
        uid = u["user_id"]
        try:
            u["warns"]     = (one(conn,"SELECT COUNT(*) as c FROM warns WHERE user_id=%s AND expires_at>now()",(uid,)) or {}).get("c", 0)
            u["muted"]     = ((one(conn,"SELECT COUNT(*) as c FROM mutes WHERE user_id=%s AND is_active=TRUE",(uid,)) or {}).get("c", 0)) > 0
            u["banned"]    = ((one(conn,"SELECT COUNT(*) as c FROM bans WHERE user_id=%s AND is_active=TRUE",(uid,)) or {}).get("c", 0)) > 0
            u["site_banned"] = ((one(conn,"SELECT COUNT(*) as c FROM site_bans WHERE lower(username)=%s AND is_active=1 AND (expires_at IS NULL OR expires_at>now())",(u.get("username","").lower(),)) or {}).get("c", 0)) > 0
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


# ─── AI-ПОМОЩНИК ──────────────────────────────────────────────────────────────

def get_punishment_history_sync(tg_id: int) -> list:
    """Последние наказания пользователя по Telegram ID — для tool-вызова AI."""
    conn = db()
    data = rows(conn, """
        SELECT 'warn' AS kind, reason, issued_at, NULL AS expires_at FROM warns WHERE user_id=%s
        UNION ALL
        SELECT 'mute' AS kind, reason, issued_at, expires_at FROM mutes WHERE user_id=%s
        UNION ALL
        SELECT 'ban' AS kind, reason, issued_at, expires_at FROM bans WHERE user_id=%s
        ORDER BY issued_at DESC
        LIMIT 10
    """, (tg_id, tg_id, tg_id))
    conn.close()
    for r in data:
        if r.get("issued_at"):
            r["issued_at"] = str(r["issued_at"])
        if r.get("expires_at"):
            r["expires_at"] = str(r["expires_at"])
    return data

async def call_groq(messages: list) -> dict:
    """Один запрос к Groq chat completions с включённым tool use.
    Возвращает сырой JSON-ответ API."""
    if not GROQ_API_KEY:
        raise HTTPException(500, "AI не настроен: отсутствует GROQ_API_KEY на сервере")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "messages": messages,
                "tools": AI_TOOLS,
                "max_tokens": 500,
                "temperature": 0.3,
            },
        )
        resp.raise_for_status()
        return resp.json()

@app.post("/api/ai/ask")
async def ai_ask(body: AiAskIn):
    """Публичный эндпоинт чат-виджета. Отвечает строго по базе знаний,
    с доступом к логу наказаний через function calling."""
    user_message = (body.message or "").strip()
    if not user_message:
        raise HTTPException(400, "Пустое сообщение")
    if len(user_message) > 1000:
        raise HTTPException(400, "Слишком длинное сообщение")

    messages = [{"role": "system", "content": AI_SYSTEM_PROMPT}]
    for m in (body.history or [])[-10:]:
        if m.role in ("user", "assistant"):
            messages.append({"role": m.role, "content": m.content})
    messages.append({"role": "user", "content": user_message})

    try:
        # До двух шагов: обычный ответ, либо вызов инструмента и затем финальный ответ
        for _ in range(2):
            data = await call_groq(messages)
            choice = data["choices"][0]["message"]
            tool_calls = choice.get("tool_calls")

            if not tool_calls:
                return {"reply": (choice.get("content") or "").strip()}

            messages.append({
                "role": "assistant",
                "content": choice.get("content") or "",
                "tool_calls": tool_calls,
            })

            for tc in tool_calls:
                if tc["function"]["name"] == "get_punishment_history":
                    try:
                        args = json.loads(tc["function"]["arguments"])
                        tg_id = int(args.get("tg_id"))
                        result = get_punishment_history_sync(tg_id)
                    except Exception as e:
                        result = {"error": str(e)}
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(result, ensure_ascii=False),
                    })

        return {"reply": "Не получилось сформировать ответ, попробуй переформулировать вопрос."}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"AI ask error: {e}")
        raise HTTPException(502, "AI-помощник временно недоступен")

@app.post("/api/bugs/submit")
async def submit_bug(request: Request, body: BugReportIn):
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(400, "Укажите заголовок бага")

    token = request.cookies.get("vn_user_session")
    u = get_user_session(token) if token else None
    username = u["username"] if u else "аноним"
    tg_id = (u.get("tg_id") if u else 0) or 0

    conn = db()
    cur = conn.execute("""
        INSERT INTO bug_reports (title, description, reporter_username, reporter_tg_id, status, created_at)
        VALUES (%s,%s,%s,%s,'new',now())
        RETURNING id
    """, (title, body.description or "", username, tg_id))
    bid = cur.fetchone()["id"]
    conn.commit()
    conn.close()

    text = (
        f"🐞 <b>Новый баг-репорт #{bid}</b>\n\n"
        f"👤 От: <code>{username}</code>\n"
        f"📝 {title}\n"
        + (f"\nℹ️ {body.description}" if body.description else "")
    )
    for aid in ADMIN_IDS:
        await tg_send(aid, text)

    return {"id": bid, "ok": True}

@app.get("/api/bugs")
async def get_bugs(request: Request, status: str = "all"):
    require_admin(request)
    conn = db()
    if status == "all":
        data = rows(conn, "SELECT * FROM bug_reports ORDER BY created_at DESC LIMIT 200")
    else:
        data = rows(conn, "SELECT * FROM bug_reports WHERE status=%s ORDER BY created_at DESC LIMIT 200", (status,))
    conn.close()
    return data

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
    u = one(conn,"SELECT * FROM bot_users WHERE lower(ltrim(username,'@'))=%s",(username.lower().lstrip('@'),))
    conn.close()
    if not u:
        return {"ok": False, "reason": "not_found"}
    return {"ok": True, "username": u["username"], "first_name": u.get("first_name","")}

# ─── ПРОФИЛЬ / 2FA ────────────────────────────────────────────────────────────

def _current_subject(request: Request):
    """Возвращает (subject_type, username, tg_id) для админа или обычного
    пользователя по текущей сессии, либо None."""
    s = get_session(request)
    if s:
        return "admin", s["username"], None
    token = request.cookies.get("vn_user_session")
    u = get_user_session(token) if token else None
    if u:
        return "user", u["username"], u.get("tg_id")
    return None

@app.get("/api/profile/me")
async def profile_me(request: Request):
    subj = _current_subject(request)
    if not subj:
        raise HTTPException(401, "Требуется авторизация")
    subject_type, username, tg_id = subj
    tfa = get_tfa_record(subject_type, username)
    if not tg_id and tfa:
        tg_id = tfa.get("tg_id")
    history = get_punishment_history_sync(tg_id) if tg_id else []
    return {
        "subject_type": subject_type,
        "username": username,
        "tg_id": tg_id,
        "tfa_enabled": bool(tfa and tfa.get("enabled")),
        "tfa_required": subject_type == "admin",
        "history": history,
    }

@app.post("/api/profile/tfa/start")
async def tfa_start(request: Request):
    """Начать привязку 2FA — генерируем токен для входа в бота, как при
    обычном логине пользователей."""
    subj = _current_subject(request)
    if not subj:
        raise HTTPException(401, "Требуется авторизация")
    subject_type, username, _ = subj
    token = secrets.token_urlsafe(16)
    TFA_BIND_TOKENS[token] = {
        "confirmed": False,
        "subject_type": subject_type,
        "subject_username": username,
        "tg_id": None,
        "tg_username": None,
    }
    return {"ok": True, "token": token}

@app.get("/api/profile/tfa/poll")
async def tfa_poll(token: str):
    d = TFA_BIND_TOKENS.get(token)
    if not d:
        raise HTTPException(404, "Token not found")
    if d["confirmed"]:
        save_tfa_binding(d["subject_type"], d["subject_username"], d["tg_id"], d["tg_username"])
        del TFA_BIND_TOKENS[token]
        return {"confirmed": True, "tg_id": d["tg_id"], "tg_username": d["tg_username"]}
    return {"confirmed": False}

@app.post("/api/profile/tfa/confirm")
async def tfa_confirm(body: dict):
    """Бот подтверждает привязку 2FA (аналог /api/auth/user-confirm)."""
    token = body.get("token")
    secret = body.get("secret")
    if secret != BOT_TOKEN:
        raise HTTPException(403, "Forbidden")
    if token not in TFA_BIND_TOKENS:
        raise HTTPException(404, "Token not found")
    TFA_BIND_TOKENS[token]["confirmed"] = True
    TFA_BIND_TOKENS[token]["tg_id"] = body.get("tg_id")
    TFA_BIND_TOKENS[token]["tg_username"] = body.get("username")
    return {"ok": True}

@app.post("/api/profile/tfa/disable")
async def tfa_disable(request: Request):
    subj = _current_subject(request)
    if not subj:
        raise HTTPException(401, "Требуется авторизация")
    subject_type, username, _ = subj
    if subject_type == "admin":
        raise HTTPException(403, "2FA обязательна для администраторов и не может быть отключена")
    conn = db()
    conn.execute("UPDATE two_factor_auth SET enabled=0 WHERE subject_type='user' AND lower(username)=%s",
                 (username.lower().lstrip("@"),))
    conn.commit(); conn.close()
    return {"ok": True}

# ─── PRODUCTS API ─────────────────────────────────────────────────────────────

PRODUCT_CATEGORIES = [
    "Жидкости 20 мг Strong",
    "Жидкости 20 мг",
    "Под системы",
    "Одноразовые ЭС",
    "Испарители и картриджи",
    "Никотиновые пакетики",
]


def _ensure_products_table():
    """Таблицы товаров создаются миграцией (migrate_to_postgres.py).
    Здесь только подстраховка на случай, если её ещё не прогнали."""
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shop_products (
                id SERIAL PRIMARY KEY,
                category TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                photo_file_id TEXT,
                added_by BIGINT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shop_product_photos (
                id SERIAL PRIMARY KEY,
                product_id INTEGER NOT NULL REFERENCES shop_products(id),
                file_id TEXT NOT NULL,
                sort_order INTEGER DEFAULT 0
            )
        """)


@app.get("/api/products")
async def get_products(category: str = "", response: Response = None):
    """Получить товары (все или по категории)"""
    if response is not None:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    _ensure_products_table()
    with db() as conn:
        if category:
            result = rows(conn,
                "SELECT id, category, name, description, photo_file_id, added_at "
                "FROM shop_products WHERE is_active=1 AND category=%s ORDER BY id DESC",
                (category,)
            )
        else:
            result = rows(conn,
                "SELECT id, category, name, description, photo_file_id, added_at "
                "FROM shop_products WHERE is_active=1 ORDER BY id DESC"
            )
    # Подгружаем фото из shop_product_photos для каждого товара
    with db() as conn:
        for p in result:
            photos = conn.execute(
                "SELECT file_id FROM shop_product_photos WHERE product_id=%s ORDER BY sort_order",
                (p["id"],)
            ).fetchall()
            p["photos"] = [row["file_id"] for row in photos]
            # Категории с количеством
        cat_counts = rows(conn,
            "SELECT category, COUNT(*) as count FROM shop_products WHERE is_active=1 GROUP BY category"
        )
    counts = {r["category"]: r["count"] for r in cat_counts}
    categories = [{"name": cat, "count": counts.get(cat, 0)} for cat in PRODUCT_CATEGORIES]
    return {"products": result, "categories": categories}


@app.get("/api/product-photo/{product_id}")
async def get_product_photo(product_id: int):
    """Прокси для фото товара из Telegram"""
    _ensure_products_table()
    with db() as conn:
        row = one(conn, "SELECT photo_file_id FROM shop_products WHERE id=%s AND is_active=1", (product_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    file_id = row["photo_file_id"]
    # Получаем путь файла через Telegram API
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}")
            data = resp.json()
            if not data.get("ok"):
                raise HTTPException(status_code=404, detail="TG error")
            file_path = data["result"]["file_path"]
            photo_resp = await client.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}")
            return Response(content=photo_resp.content, media_type="image/jpeg")
    except Exception as e:
        log.error(f"Photo proxy error: {e}")
        raise HTTPException(status_code=500, detail="Photo unavailable")


@app.get("/api/product-photo/{product_id}/{photo_index}")
async def get_product_photo_by_index(product_id: int, photo_index: int):
    """Прокси для фото товара по индексу из shop_product_photos"""
    _ensure_products_table()
    with db() as conn:
        row = conn.execute(
            "SELECT file_id FROM shop_product_photos WHERE product_id=%s ORDER BY sort_order LIMIT 1 OFFSET %s",
            (product_id, photo_index)
        ).fetchone()
        if not row:
            # Фолбэк: старое поле photo_file_id (для товаров без записей в shop_product_photos)
            fallback = one(conn, "SELECT photo_file_id FROM shop_products WHERE id=%s AND is_active=1", (product_id,))
            if not fallback or not fallback["photo_file_id"]:
                raise HTTPException(status_code=404, detail="Not found")
            file_id = fallback["photo_file_id"]
        else:
            file_id = row["file_id"]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}")
            data = resp.json()
            if not data.get("ok"):
                raise HTTPException(status_code=404, detail="TG error")
            file_path = data["result"]["file_path"]
            photo_resp = await client.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}")
            return Response(content=photo_resp.content, media_type="image/jpeg")
    except Exception as e:
        log.error(f"Photo proxy error: {e}")
        raise HTTPException(status_code=500, detail="Photo unavailable")


@app.get("/api/products/categories")
async def get_categories(response: Response):
    """Получить список категорий"""
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    _ensure_products_table()
    with db() as conn:
        result = rows(conn,
            "SELECT category, COUNT(*) as count FROM shop_products WHERE is_active=1 GROUP BY category"
        )
    counts = {r["category"]: r["count"] for r in result}
    return {
        "categories": [
            {"name": cat, "count": counts.get(cat, 0)}
            for cat in PRODUCT_CATEGORIES
        ]
    }


# ─── FRONTEND ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    with open(path, encoding="utf-8") as f:
        return f.read()