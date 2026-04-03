import os
import re
import asyncio
import logging
import random
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Set, Tuple, Any
import pytz

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    ChatPermissions,
    Message,
    ChatMemberUpdated,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ChatAdministratorRights
)
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.utils.keyboard import InlineKeyboardBuilder
from logging.handlers import RotatingFileHandler
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

class AdminComplaintStates(StatesGroup):
    waiting_for_username = State()
    waiting_for_admin_username = State()
    waiting_for_description = State()
    waiting_for_complaint_text = State()
    waiting_for_evidence = State()
    # Новые состояния для обработки жалоб
    waiting_for_reject_reason = State()
    waiting_for_approve_actions = State()
    waiting_for_false_report_reason = State()
    waiting_for_incorrect_report_reason = State()

# Создаем необходимые директории
os.makedirs("data", exist_ok=True)
os.makedirs("logs", exist_ok=True)

# Настройка логирования для хостинга
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        RotatingFileHandler("logs/bot.log", maxBytes=10485760, backupCount=5),  # 10MB per file, 5 backups
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Конфигурация бота из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS").split(','))) if os.getenv("ADMIN_IDS") else []
CHAT_ID = int(os.getenv("CHAT_ID"))
WARN_EXPIRE_DAYS = int(os.getenv("WARN_EXPIRE_DAYS", "7"))

# Московский часовой пояс
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

def get_moscow_time():
    """Возвращает текущее время в московском часовом поясе"""
    return datetime.now(MOSCOW_TZ)

# Ограничения
MAX_ADS_PER_DAY = int(os.getenv("MAX_ADS_PER_DAY", "5"))
MIN_AD_INTERVAL_HOURS = float(os.getenv("MIN_AD_INTERVAL_HOURS", "1.5"))
MUTE_DURATION_DAYS = int(os.getenv("MUTE_DURATION_DAYS", "1"))

MIN_AD_INTERVAL = timedelta(hours=MIN_AD_INTERVAL_HOURS)
MUTE_DURATION = timedelta(days=MUTE_DURATION_DAYS)

# Инициализация базы данных
def init_db():
    db_path = "data/bot_database.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Таблица для варнов
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS warns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        chat_id INTEGER NOT NULL,
        reason TEXT,
        issued_by INTEGER NOT NULL,
        issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        expires_at TIMESTAMP
    )"""
    )

    # Таблица для мутов
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS mutes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        chat_id INTEGER NOT NULL,
        reason TEXT,
        issued_by INTEGER NOT NULL,
        issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        expires_at TIMESTAMP,
        is_active BOOLEAN DEFAULT TRUE
    )"""
    )

    # Таблица для банов
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS bans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        chat_id INTEGER NOT NULL,
        reason TEXT,
        issued_by INTEGER NOT NULL,
        issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        expires_at TIMESTAMP,
        is_active BOOLEAN DEFAULT TRUE
    )"""
    )

    # Таблица для предупреждений администраторов
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS admin_warns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        reason TEXT,
        issued_by INTEGER NOT NULL,
        issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_active BOOLEAN DEFAULT TRUE
    )"""
    )

    # Таблица для администраторов
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL UNIQUE,
        added_by INTEGER NOT NULL,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )"""
    )

    # Таблица для истории объявлений
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS user_ads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        message_text TEXT NOT NULL,
        sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )"""
    )

    # Таблица для нарушений лимита объявлений
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS ad_limit_violations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        violation_date DATE NOT NULL,
        violation_count INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )"""
    )

    # Таблица для донатов
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS donations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        amount REAL,
        currency TEXT DEFAULT 'RUB',
        message TEXT,
        donated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_anonymous BOOLEAN DEFAULT FALSE
    )"""
    )

    # Таблица для жалоб на администраторов
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS admin_complaints (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        username TEXT NOT NULL,
        admin_username TEXT NOT NULL,
        description TEXT NOT NULL,
        complaint_text TEXT NOT NULL,
        evidence TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status TEXT DEFAULT 'pending', -- pending, approved, rejected, false_report, incorrect_report
        handled_by INTEGER,
        handling_result TEXT,
        handled_at TIMESTAMP
    )"""
    )
    
    # Таблица для блокировок доступа к боту
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS bot_blocks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL UNIQUE,
        reason TEXT NOT NULL,
        blocked_by INTEGER NOT NULL,
        blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_active BOOLEAN DEFAULT TRUE
    )"""
    )

    # Таблица для предупреждений в боте (за некорректные жалобы)
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS bot_warns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        reason TEXT NOT NULL,
        issued_by INTEGER NOT NULL,
        issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_active BOOLEAN DEFAULT TRUE
    )"""
    )

    # Индексы для улучшения производительности
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_warns_user_chat ON warns(user_id, chat_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_warns_expires ON warns(expires_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_mutes_user_chat ON mutes(user_id, chat_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_mutes_expires ON mutes(expires_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bans_user_chat ON bans(user_id, chat_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bans_expires ON bans(expires_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_ads_user_date ON user_ads(user_id, sent_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ad_violations_user_date ON ad_limit_violations(user_id, violation_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_admin_complaints_status ON admin_complaints(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_admin_complaints_user ON admin_complaints(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_admin_complaints_created ON admin_complaints(created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bot_blocks_user ON bot_blocks(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bot_blocks_active ON bot_blocks(is_active)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bot_warns_user ON bot_warns(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bot_warns_active ON bot_warns(is_active)")

    conn.commit()
    conn.close()

init_db()

# Триггерные слова
TRIGGER_WORDS = {
    "кинг": ["кинг", "king", "кiнг", "к1нг", "кинг", "к!нг", "к@нг"],
    "техас": ["техас", "texas", "т3хас", "теха$", "техас", "тexас"],
    "чилл": ["чилл", "chill", "ч!лл", "чиll", "ч1лл", "чилl"],
    "космонавт": ["космонавт", "косmonaut", "к0смонавт", "космонавт", "космонавт"],
}

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Функции для работы с базой данных
def get_db_connection():
    """Создает соединение с базой данных"""
    return sqlite3.connect("data/bot_database.db")

def add_warn(user_id: int, chat_id: int, reason: str, issued_by: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    expires_at = datetime.now() + timedelta(days=WARN_EXPIRE_DAYS)
    cursor.execute(
        "INSERT INTO warns (user_id, chat_id, reason, issued_by, expires_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, chat_id, reason, issued_by, expires_at),
    )
    conn.commit()
    conn.close()

def get_user_warns(user_id: int, chat_id: int) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, reason, issued_at, expires_at FROM warns WHERE user_id = ? AND chat_id = ? AND expires_at > ?",
        (user_id, chat_id, datetime.now()),
    )
    warns = [
        {"id": row[0], "reason": row[1], "issued_at": row[2], "expires_at": row[3]}
        for row in cursor.fetchall()
    ]
    conn.close()
    return warns

def remove_warn(warn_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM warns WHERE id = ?", (warn_id,))
    conn.commit()
    conn.close()

def clear_user_warns(user_id: int, chat_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM warns WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)
    )
    conn.commit()
    conn.close()

def add_mute(user_id: int, chat_id: int, reason: str, issued_by: int, duration: timedelta = None):
    conn = get_db_connection()
    cursor = conn.cursor()
    expires_at = datetime.now() + duration if duration else None
    cursor.execute(
        "INSERT INTO mutes (user_id, chat_id, reason, issued_by, expires_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, chat_id, reason, issued_by, expires_at),
    )
    conn.commit()
    conn.close()

def add_ban(user_id: int, chat_id: int, reason: str, issued_by: int, duration: timedelta = None):
    conn = get_db_connection()
    cursor = conn.cursor()
    expires_at = datetime.now() + duration if duration else None
    cursor.execute(
        "INSERT INTO bans (user_id, chat_id, reason, issued_by, expires_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, chat_id, reason, issued_by, expires_at),
    )
    conn.commit()
    conn.close()

def add_admin_warn(user_id: int, reason: str, issued_by: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO admin_warns (user_id, reason, issued_by) VALUES (?, ?, ?)",
        (user_id, reason, issued_by),
    )
    conn.commit()
    conn.close()

def get_admin_warns(user_id: int) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, reason, issued_at, issued_by FROM admin_warns WHERE user_id = ? AND is_active = TRUE",
        (user_id,),
    )
    warns = [
        {"id": row[0], "reason": row[1], "issued_at": row[2], "issued_by": row[3]}
        for row in cursor.fetchall()
    ]
    conn.close()
    return warns

def remove_admin_warn(warn_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE admin_warns SET is_active = FALSE WHERE id = ?", (warn_id,))
    conn.commit()
    conn.close()

def remove_last_admin_warn(user_id: int):
    """Удаляет последнее предупреждение администратора"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM admin_warns WHERE user_id = ? AND is_active = TRUE ORDER BY id DESC LIMIT 1",
        (user_id,)
    )
    result = cursor.fetchone()
    if result:
        cursor.execute("UPDATE admin_warns SET is_active = FALSE WHERE id = ?", (result[0],))
    conn.commit()
    conn.close()
    return result[0] if result else None

def clear_admin_warns(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE admin_warns SET is_active = FALSE WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# Новые функции для работы с варнами в боте
def add_bot_warn(user_id: int, reason: str, issued_by: int):
    """Добавляет предупреждение в боте"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO bot_warns (user_id, reason, issued_by) VALUES (?, ?, ?)",
        (user_id, reason, issued_by),
    )
    conn.commit()
    conn.close()

def get_bot_warns(user_id: int) -> List[Dict[str, Any]]:
    """Получает активные предупреждения в боте"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, reason, issued_at, issued_by FROM bot_warns WHERE user_id = ? AND is_active = TRUE",
        (user_id,),
    )
    warns = [
        {"id": row[0], "reason": row[1], "issued_at": row[2], "issued_by": row[3]}
        for row in cursor.fetchall()
    ]
    conn.close()
    return warns

def remove_bot_warn(warn_id: int):
    """Удаляет предупреждение в боте"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE bot_warns SET is_active = FALSE WHERE id = ?", (warn_id,))
    conn.commit()
    conn.close()

def clear_bot_warns(user_id: int):
    """Очищает все предупреждения в боте"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE bot_warns SET is_active = FALSE WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def add_admin(user_id: int, added_by: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)",
        (user_id, added_by),
    )
    conn.commit()
    conn.close()

def remove_admin(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def is_admin(user_id: int) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM admins WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def get_all_admins() -> List[int]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM admins")
    admins = [row[0] for row in cursor.fetchall()]
    conn.close()
    return admins

def add_user_ad(user_id: int, message_text: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO user_ads (user_id, message_text) VALUES (?, ?)",
        (user_id, message_text),
    )
    conn.commit()
    conn.close()

def get_today_ads_count(user_id: int) -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM user_ads WHERE user_id = ? AND DATE(sent_at) = DATE('now')",
        (user_id,),
    )
    count = cursor.fetchone()[0]
    conn.close()
    return count

def get_last_ad_time(user_id: int) -> Optional[datetime]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT sent_at FROM user_ads WHERE user_id = ? ORDER BY sent_at DESC LIMIT 1",
        (user_id,),
    )
    result = cursor.fetchone()
    conn.close()
    return datetime.fromisoformat(result[0]) if result else None

def add_ad_violation(user_id: int):
    """Добавляет запись о нарушении лимита объявлений"""
    conn = get_db_connection()
    cursor = conn.cursor()
    today = datetime.now().date()
    
    # Проверяем, есть ли уже нарушение сегодня
    cursor.execute(
        "SELECT id, violation_count FROM ad_limit_violations WHERE user_id = ? AND violation_date = ?",
        (user_id, today)
    )
    result = cursor.fetchone()
    
    if result:
        # Увеличиваем счетчик нарушений
        violation_id, count = result
        cursor.execute(
            "UPDATE ad_limit_violations SET violation_count = ? WHERE id = ?",
            (count + 1, violation_id)
        )
    else:
        # Создаем новую запись
        cursor.execute(
            "INSERT INTO ad_limit_violations (user_id, violation_date) VALUES (?, ?)",
            (user_id, today)
        )
    
    conn.commit()
    conn.close()

def get_today_violations_count(user_id: int) -> int:
    """Получает количество нарушений лимита объявлений сегодня"""
    conn = get_db_connection()
    cursor = conn.cursor()
    today = datetime.now().date()
    
    cursor.execute(
        "SELECT violation_count FROM ad_limit_violations WHERE user_id = ? AND violation_date = ?",
        (user_id, today)
    )
    result = cursor.fetchone()
    conn.close()
    
    return result[0] if result else 0

def get_active_complaints() -> List[Dict[str, Any]]:
    """Получает активные жалобы из базы данных"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT id, user_id, username, admin_username, description, complaint_text, evidence, 
                  created_at, status, handled_by, handling_result
           FROM admin_complaints 
           WHERE status = 'pending'
           ORDER BY created_at DESC"""
    )
    complaints = [
        {
            "id": row[0],
            "user_id": row[1],
            "username": row[2] or "Неизвестный",  # Защита от NULL
            "admin_username": row[3] or "Неизвестный",  # Защита от NULL
            "description": row[4] or "Не указано",
            "complaint_text": row[5] or "Не указано",
            "evidence": row[6],
            "created_at": row[7],
            "status": row[8],
            "handled_by": row[9],
            "handling_result": row[10]
        }
        for row in cursor.fetchall()
    ]
    conn.close()
    return complaints

def get_complaint_by_id(complaint_id: int) -> Optional[Dict[str, Any]]:
    """Получает жалобу по ID"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT id, user_id, username, admin_username, description, complaint_text, evidence, 
                  created_at, status, handled_by, handling_result
           FROM admin_complaints 
           WHERE id = ?""",
        (complaint_id,)
    )
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return {
            "id": result[0],
            "user_id": result[1],
            "username": result[2] or "Неизвестный",  # Защита от NULL
            "admin_username": result[3] or "Неизвестный",  # Защита от NULL
            "description": result[4] or "Не указано",
            "complaint_text": result[5] or "Не указано",
            "evidence": result[6],
            "created_at": result[7],
            "status": result[8],
            "handled_by": result[9],
            "handling_result": result[10]
        }
    return None

def update_complaint_status(complaint_id: int, status: str, handled_by: int = None, handling_result: str = None):
    """Обновляет статус жалобы"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """UPDATE admin_complaints 
           SET status = ?, handled_by = ?, handling_result = ?, handled_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (status, handled_by, handling_result, complaint_id)
    )
    conn.commit()
    conn.close()

def save_admin_complaint(user_id: int, username: str, admin_username: str, description: str, complaint_text: str, evidence: str = None) -> int:
    """Сохраняет жалобу на администратора в базу данных и возвращает ID жалобы"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Обеспечиваем, что обязательные поля не NULL
    username = username or "Неизвестный"
    admin_username = admin_username or "Неизвестный"
    description = description or "Не указано"
    complaint_text = complaint_text or "Не указано"
    
    cursor.execute(
        """INSERT INTO admin_complaints 
           (user_id, username, admin_username, description, complaint_text, evidence) 
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, username, admin_username, description, complaint_text, evidence)
    )
    complaint_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return complaint_id

def is_user_blocked(user_id: int) -> bool:
    """Проверяет, заблокирован ли пользователь в боте"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM bot_blocks WHERE user_id = ? AND is_active = TRUE",
        (user_id,)
    )
    result = cursor.fetchone()
    conn.close()
    return result is not None

def block_user(user_id: int, reason: str, blocked_by: int):
    """Блокирует пользователя в боте"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO bot_blocks (user_id, reason, blocked_by) VALUES (?, ?, ?)",
        (user_id, reason, blocked_by)
    )
    conn.commit()
    conn.close()

def unblock_user(user_id: int):
    """Разблокирует пользователя в боте"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE bot_blocks SET is_active = FALSE WHERE user_id = ?",
        (user_id,)
    )
    conn.commit()
    conn.close()

def get_complaints_keyboard() -> InlineKeyboardMarkup:
    """Создает инлайн-клавиатуру со списком активных жалоб"""
    complaints = get_active_complaints()
    keyboard = InlineKeyboardBuilder()
    
    if not complaints:
        keyboard.row(
            InlineKeyboardButton(
                text="📭 Нет активных жалоб", 
                callback_data="no_complaints"
            )
        )
        return keyboard.as_markup()
    
    for complaint in complaints:
        complaint_id = complaint.get('id', 'N/A')
        username = complaint.get('username', 'Неизвестный')[:15]  # Обрезаем длинные имена
        admin_username = complaint.get('admin_username', 'Неизвестный')[:15]
        
        button_text = f"#{complaint_id} {username} → {admin_username}"
        # Обрезаем текст кнопки если слишком длинный
        if len(button_text) > 30:
            button_text = button_text[:27] + "..."
            
        keyboard.row(
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"view_complaint:{complaint_id}"
            )
        )
    
    keyboard.row(
        InlineKeyboardButton(
            text="🔄 Обновить список",
            callback_data="refresh_complaints"
        )
    )
    
    return keyboard.as_markup()

def get_complaint_actions_keyboard(complaint_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру с действиями для жалобы"""
    keyboard = InlineKeyboardBuilder()
    
    keyboard.row(
        InlineKeyboardButton(
            text="❌ Отклонить + причина",
            callback_data=f"reject_complaint:{complaint_id}"
        )
    )
    
    keyboard.row(
        InlineKeyboardButton(
            text="✅ Принять + действия",
            callback_data=f"approve_complaint:{complaint_id}"
        )
    )
    
    keyboard.row(
        InlineKeyboardButton(
            text="🚫 Бан в боте за ложную жалобу",
            callback_data=f"warn_false_report:{complaint_id}"
        )
    )
    
    keyboard.row(
        InlineKeyboardButton(
            text="⚠️ Предупреждение за некорректную жалобу",
            callback_data=f"warn_incorrect_report:{complaint_id}"
        )
    )
    
    keyboard.row(
        InlineKeyboardButton(
            text="📋 Назад к списку",
            callback_data="view_all_complaints"
        )
    )
    
    return keyboard.as_markup()

# Вспомогательные функции
async def get_user_mention(user_id: int) -> str:
    try:
        user = await bot.get_chat(user_id)
        name = user.first_name or user.username or str(user_id)
        return f'<a href="tg://user?id={user_id}">{name}</a>'
    except Exception:
        return str(user_id)

async def format_duration(duration: timedelta) -> str:
    if not duration:
        return "навсегда"

    days = duration.days
    hours, remainder = divmod(duration.seconds, 3600)
    minutes, _ = divmod(remainder, 60)

    parts = []
    if days > 0:
        parts.append(f"{days} д.")
    if hours > 0:
        parts.append(f"{hours} ч.")
    if minutes > 0 and days == 0:
        parts.append(f"{minutes} мин.")

    return " ".join(parts) if parts else "менее минуты"

async def is_owner(user_id: int) -> bool:
    return user_id in ADMIN_IDS

async def is_admin_user(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором бота (из ADMIN_IDS или базы данных)"""
    return user_id in ADMIN_IDS or is_admin(user_id)

async def is_chat_admin(user_id: int, chat_id: int = None) -> bool:
    """Проверяет, является ли пользователь администратором чата"""
    if not chat_id:
        return False
        
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]
    except Exception as e:
        logger.error(f"Ошибка проверки прав администратора чата: {e}")
        return False

async def is_bot_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором бота (не чата)"""
    return user_id in ADMIN_IDS or is_admin(user_id)

def parse_time(time_str: str) -> Optional[timedelta]:
    if not time_str:
        return None

    time_str = time_str.lower()
    match = re.match(r"^(\d+)([mhdw]?)$", time_str)
    if not match:
        return None

    num = int(match.group(1))
    unit = match.group(2) or "m"

    if unit == "m":
        return timedelta(minutes=num)
    elif unit == "h":
        return timedelta(hours=num)
    elif unit == "d":
        return timedelta(days=num)
    elif unit == "w":
        return timedelta(weeks=num)
    return None

async def get_user_id_from_message(text: str) -> Optional[int]:
    if not text:
        return None

    # Пытаемся извлечь ID или username из текста
    match = re.match(r"^(?:@?(\w+)|(\d+))$", text.strip())
    if not match:
        return None

    username_or_id = match.group(1) or match.group(2)
    if not username_or_id:
        return None

    if username_or_id.isdigit():
        return int(username_or_id)

    try:
        user = await bot.get_chat(f"@{username_or_id}")
        return user.id
    except Exception:
        return None

async def resolve_user_reference(message: Message, command_args: str = None) -> Optional[Tuple[int, str]]:
    """
    Разрешает ссылку на пользователя из различных источников:
    - Ответ на сообщение
    - Упоминание в аргументах команды (ID или @username)
    Возвращает (user_id, reason) или None
    """
    user_id = None
    reason = None
    
    # Сначала проверяем ответ на сообщение
    if message.reply_to_message:
        user_id = message.reply_to_message.from_user.id
        # Причина - все аргументы команды
        reason = command_args if command_args else "Не указана"
    # Затем проверяем аргументы команды
    elif command_args:
        # Разделяем аргументы на пользователя и причину
        args = command_args.split(maxsplit=1)
        if args:
            user_id = await get_user_id_from_message(args[0])
            reason = args[1] if len(args) > 1 else "Не указана"
    
    return (user_id, reason) if user_id else None

async def resolve_user_only(message: Message, command_args: str = None) -> Optional[int]:
    """
    Разрешает ссылку на пользователя без причины
    - Ответ на сообщение
    - Упоминание в аргументами команды (ID или @username)
    Возвращает user_id или None
    """
    # Сначала проверяем ответ на сообщение
    if message.reply_to_message:
        return message.reply_to_message.from_user.id
    
    # Затем проверяем аргументы команды
    elif command_args:
        return await get_user_id_from_message(command_args.split()[0])
    
    return None

def is_ad_message(text: str) -> bool:
    """Определяет, является ли сообщение объявлением"""
    ad_keywords = [
        'продам', 'продаю', 'куплю', 'покупаю', 'обмен', 'меняю', 
        'отдам', 'даром', 'бесплатно', 'цена', 'стоимость', '₽', 'руб',
        'тг', 'телеграм', 'доставка', 'забрать', 'самовывоз'
    ]
    
    text_lower = text.lower()
    # Если сообщение содержит несколько ключевых слов, считаем его объявлением
    keyword_count = sum(1 for keyword in ad_keywords if keyword in text_lower)
    return keyword_count >= 2 or len(text) > 100  # Длинные сообщения тоже считаем объявлениями

async def is_chat_admin_or_bot_admin(user_id: int, chat_id: int = None) -> bool:
    """Проверяет, является ли пользователь администратором чата или бота"""
    if not chat_id:
        return False
        
    # Проверяем права администратора в чате
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        is_chat_admin = member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]
        is_bot_admin = user_id in ADMIN_IDS or is_admin(user_id)
        
        return is_chat_admin or is_bot_admin
    except Exception as e:
        logger.error(f"Ошибка проверки прав администратора: {e}")
        return False

# Функции модерации
async def mute_user(chat_id: int, user_id: int, duration: timedelta = None, reason: str = None, is_auto: bool = False) -> bool:
    try:
        permissions = ChatPermissions(
            can_send_messages=False,
            can_send_media_messages=False,
            can_send_polls=False,
            can_send_other_messages=False,
            can_add_web_page_previews=False,
            can_change_info=False,
            can_invite_users=False,
            can_pin_messages=False,
        )
        until_date = datetime.now() + duration if duration else None
        await bot.restrict_chat_member(chat_id, user_id, permissions, until_date=until_date)

        duration_str = await format_duration(duration)
        reason_str = f"\n📝 <b>Причина:</b> {reason}" if reason else ""
        user_mention = await get_user_mention(user_id)

        if is_auto:
            message_text = (
                f"🤖 <b>Автоматическое наказание</b>\n\n"
                f"👤 <b>Пользователь:</b> {user_mention}\n"
                f"⏳ <b>Срок:</b> {duration_str}{reason_str}\n\n"
                f"❗ <i>Команды бота доступны только администраторам!</i>"
            )
        else:
            message_text = (
                f"🔇 <b>Мут пользователя</b>\n\n"
                f"👤 <b>Пользователь:</b> {user_mention}\n"
                f"⏳ <b>Срок:</b> {duration_str}{reason_str}"
            )

        await bot.send_message(chat_id, message_text, parse_mode="HTML")
        add_mute(user_id, chat_id, reason, chat_id, duration)
        return True
    except Exception as e:
        logger.error(f"Ошибка мута: {e}")
        return False

async def unmute_user(chat_id: int, user_id: int) -> bool:
    try:
        permissions = ChatPermissions(
            can_send_messages=True,
            can_send_media_messages=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
            can_change_info=False,
            can_invite_users=False,
            can_pin_messages=False,
        )
        await bot.restrict_chat_member(chat_id, user_id, permissions)

        # Убрано подтверждающее сообщение
        return True
    except Exception as e:
        logger.error(f"Ошибка размута: {e}")
        return False

async def ban_user(chat_id: int, user_id: int, duration: timedelta = None, reason: str = None) -> bool:
    try:
        until_date = datetime.now() + duration if duration else None
        await bot.ban_chat_member(chat_id, user_id, until_date=until_date)

        duration_str = await format_duration(duration)
        reason_str = f"\n📝 <b>Причина:</b> {reason}" if reason else ""
        user_mention = await get_user_mention(user_id)

        await bot.send_message(
            chat_id,
            f"🚫 <b>Бан пользователя</b>\n\n"
            f"👤 <b>Пользователь:</b> {user_mention}\n"
            f"⏳ <b>Срок:</b> {duration_str}{reason_str}",
            parse_mode="HTML",
        )

        add_ban(user_id, chat_id, reason, chat_id, duration)
        return True
    except Exception as e:
        logger.error(f"Ошибка бана: {e}")
        return False

async def unban_user(chat_id: int, user_id: int) -> bool:
    try:
        await bot.unban_chat_member(chat_id, user_id)

        # Убрано подтверждающее сообщение
        return True
    except Exception as e:
        logger.error(f"Ошибка разбана: {e}")
        return False

async def delete_message(chat_id: int, message_id: int) -> bool:
    try:
        await bot.delete_message(chat_id, message_id)
        return True
    except Exception as e:
        logger.error(f"Ошибка удаления сообщения: {e}")
        return False

async def warn_user(chat_id: int, user_id: int, reason: str = None) -> bool:
    try:
        add_warn(user_id, chat_id, reason, chat_id)
        warns = get_user_warns(user_id, chat_id)

        reason_str = f"\n📝 <b>Причина:</b> {reason}" if reason else ""
        user_mention = await get_user_mention(user_id)

        await bot.send_message(
            chat_id,
            f"⚠️ <b>Предупреждение выдано</b>\n\n"
            f"👤 <b>Пользователь:</b> {user_mention}\n"
            f"🔢 <b>Всего предупреждений:</b> {len(warns)}{reason_str}\n"
            f"📅 <b>Действует:</b> {WARN_EXPIRE_DAYS} дней",
            parse_mode="HTML",
        )

        if len(warns) >= 3:
            await ban_user(chat_id, user_id, reason="3 предупреждения")
            clear_user_warns(user_id, chat_id)

        return True
    except Exception as e:
        logger.error(f"Ошибка выдачи варна: {e}")
        return False

async def warn_admin(user_id: int, reason: str, issued_by: int) -> bool:
    try:
        add_admin_warn(user_id, reason, issued_by)
        warns = get_admin_warns(user_id)

        reason_str = f"\n📝 <b>Причина:</b> {reason}" if reason else ""
        user_mention = await get_user_mention(user_id)
        issued_mention = await get_user_mention(issued_by)

        # Отправляем сообщение в чат
        await bot.send_message(
            CHAT_ID,
            f"⚠️ <b>Предупреждение администратору</b>\n\n"
            f"👤 <b>Администратор:</b> {user_mention}\n"
            f"🔢 <b>Всего предупреждений:</b> {len(warns)}\n"
            f"👮 <b>Выдал:</b> {issued_mention}{reason_str}",
            parse_mode="HTML",
        )

        if len(warns) >= 3:
            # Снимаем права администратора
            await bot.send_message(
                CHAT_ID,
                f"🚫 <b>Снятие прав администратора</b>\n\n"
                f"👤 <b>Администратор:</b> {user_mention}\n"
                f"📝 <b>Причина:</b> 3 предупреждения",
                parse_mode="HTML",
            )
            remove_admin(user_id)
            clear_admin_warns(user_id)

        return True
    except Exception as e:
        logger.error(f"Ошибка выдачи варна администратору: {e}")
        return False

def get_main_keyboard():
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="📢 Оставить жалобу на админа", callback_data="complain_admin"))
    keyboard.row(InlineKeyboardButton(text="📋 Просмотреть жалобы (админы)", callback_data="view_all_complaints"))
    
    return keyboard.as_markup()

# Обработчики команд
@dp.message(Command("start"))
async def cmd_start(message: Message):
    if message.chat.type != ChatType.PRIVATE:
        return

    # Проверяем, не заблокирован ли пользователь
    if is_user_blocked(message.from_user.id):
        # Получаем причину блокировки
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT reason, blocked_at FROM bot_blocks WHERE user_id = ? AND is_active = TRUE ORDER BY blocked_at DESC LIMIT 1",
            (message.from_user.id,)
        )
        block_info = cursor.fetchone()
        conn.close()
        
        reason = block_info[0] if block_info else "Нарушение правил"
        blocked_at = block_info[1] if block_info else "неизвестно"
        
        await message.answer(
            f"🚫 <b>Вы заблокированы в боте</b>\n\n"
            f"📝 <b>Причина:</b> {reason}\n"
            f"⏰ <b>Дата блокировки:</b> {blocked_at}\n\n"
            f"Вы не можете использовать функции бота из-за нарушений правил.\n"
            f"Для разблокировки свяжитесь с администраторами.",
            parse_mode="HTML"
        )
        return

    welcome_text = """
    👋 <b>Привет! Я бот-модератор для чата.</b>

    🤖 <b>Мои возможности:</b>
    • Автоматическая модерация объявлений
    • Система предупреждений
    • Мут/бан пользователей
    • Управление администраторами
    • Система жалоб на администраторов

    📊 <b>Для администраторов доступны команды:</b>
    /warn - выдать предупреждение
    /mute - замутить пользователя
    /unmute - размутить пользователя
    /ban - забанить пользователя
    /unban - разбанить пользователя
    /warns - посмотреть предупреждения
    /clearwarns - очистить предупреждения

    👮 <b>Для владельцев:</b>
    /admin_add - добавить администратора
    /admin_remove - удалить администратора
    /admin_list - список администраторов
    /admin_warn - предупреждение администратору
    /admin_unwarn - снять предупреждение администратору

    📋 <b>Система жалоб:</b>
    • Подача жалоб на администраторов
    • Просмотр активных жалоб (для админов)
    • Управление жалобами
    """
    await message.answer(welcome_text, parse_mode="HTML", reply_markup=get_main_keyboard())

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if not await is_bot_admin(message.from_user.id):
        await message.answer("❌ У вас нет прав для этой команды.")
        return

    conn = get_db_connection()
    cursor = conn.cursor()

    # Статистика варнов
    cursor.execute("SELECT COUNT(*) FROM warns")
    total_warns = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM warns WHERE expires_at > ?", (datetime.now(),))
    active_warns = cursor.fetchone()[0]

    # Статистика мутов
    cursor.execute("SELECT COUNT(*) FROM mutes")
    total_mutes = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM mutes WHERE is_active = TRUE AND (expires_at IS NULL OR expires_at > ?)", (datetime.now(),))
    active_mutes = cursor.fetchone()[0]

    # Статистика банов
    cursor.execute("SELECT COUNT(*) FROM bans")
    total_bans = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM bans WHERE is_active = TRUE AND (expires_at IS NULL OR expires_at > ?)", (datetime.now(),))
    active_bans = cursor.fetchone()[0]

    # Статистика объявлений
    cursor.execute("SELECT COUNT(*) FROM user_ads")
    total_ads = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM user_ads WHERE DATE(sent_at) = DATE('now')")
    today_ads = cursor.fetchone()[0]

    # Статистика администраторов
    cursor.execute("SELECT COUNT(*) FROM admins")
    total_admins = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM admin_warns WHERE is_active = TRUE")
    active_admin_warns = cursor.fetchone()[0]

    stats_text = f"""
    📊 <b>Статистика бота</b>

    ⚠️ <b>Предупреждения:</b>
    • Всего: {total_warns}
    • Активных: {active_warns}

    🔇 <b>Муты:</b>
    • Всего: {total_mutes}
    • Активных: {active_mutes}

    🚫 <b>Баны:</b>
    • Всего: {total_bans}
    • Активных: {active_bans}

    📢 <b>Объявления:</b>
    • Всего: {total_ads}
    • Сегодня: {today_ads}

    👮 <b>Администраторы:</b>
    • Всего: {total_admins}
    • Предупреждений: {active_admin_warns}

    🤖 <b>Бот работает стабильно!</b>
    """
    conn.close()

    await message.answer(stats_text, parse_mode="HTML")

@dp.message(Command("warn"))
async def cmd_warn(message: Message, command: CommandObject):
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        # Отправляем анимированный смайл и не удаляем его
        clown_msg = await message.reply("🤡")
        await asyncio.sleep(2)  # Ждем 2 секунды для анимации
        
        await delete_message(message.chat.id, message.message_id)
        await mute_user(
            message.chat.id,
            message.from_user.id,
            MUTE_DURATION,
            "Использование команд бота",
            is_auto=True
        )
        return

    user_data = await resolve_user_reference(message, command.args)
    if not user_data:
        await message.answer("❌ Укажите пользователя (ответом на сообщение или @username/id).")
        return

    user_id, reason = user_data
    await warn_user(message.chat.id, user_id, reason)

@dp.message(Command("warns"))
async def cmd_warns(message: Message, command: CommandObject):
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        clown_msg = await message.reply("🤡")
        await asyncio.sleep(2)
        
        await delete_message(message.chat.id, message.message_id)
        await mute_user(
            message.chat.id,
            message.from_user.id,
            MUTE_DURATION,
            "Использование команд бота",
            is_auto=True
        )
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите пользователя (ответом на сообщение или @username/id).")
        return

    warns = get_user_warns(user_id, message.chat.id)
    user_mention = await get_user_mention(user_id)

    if not warns:
        await message.answer(f"✅ У пользователя {user_mention} нет активных предупреждений.")
        return

    warns_text = "\n".join(
        [
            f"• {warn['reason']} ({warn['issued_at']}, истекает: {warn['expires_at']})"
            for warn in warns
        ]
    )

    await message.answer(
        f"⚠️ <b>Предупреждения пользователя {user_mention}</b>\n\n{warns_text}",
        parse_mode="HTML",
    )

@dp.message(Command("clearwarns"))
async def cmd_clearwarns(message: Message, command: CommandObject):
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        clown_msg = await message.reply("🤡")
        await asyncio.sleep(2)
        
        await delete_message(message.chat.id, message.message_id)
        await mute_user(
            message.chat.id,
            message.from_user.id,
            MUTE_DURATION,
            "Использование команд бота",
            is_auto=True
        )
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите пользователя (ответом на сообщение или @username/id).")
        return

    clear_user_warns(user_id, message.chat.id)
    user_mention = await get_user_mention(user_id)

    await message.answer(
        f"✅ Предупреждения пользователя {user_mention} очищены.",
        parse_mode="HTML"
    )

@dp.message(Command("unwarn"))
async def cmd_unwarn(message: Message, command: CommandObject):
    """Снять одно предупреждение у пользователя"""
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        clown_msg = await message.reply("🤡")
        await asyncio.sleep(2)
        
        await delete_message(message.chat.id, message.message_id)
        await mute_user(
            message.chat.id,
            message.from_user.id,
            MUTE_DURATION,
            "Использование команд бота",
            is_auto=True
        )
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите пользователя (ответом на сообщение или @username/id).")
        return

    # Получаем все активные варны пользователя
    warns = get_user_warns(user_id, message.chat.id)
    
    if not warns:
        user_mention = await get_user_mention(user_id)
        await message.answer(f"✅ У пользователя {user_mention} нет активных предупреждений.")
        return
    
    # Удаляем последнее предупреждение (с наибольшим ID)
    last_warn = max(warns, key=lambda x: x['id'])
    remove_warn(last_warn['id'])
    
    user_mention = await get_user_mention(user_id)
    remaining_warns = len(warns) - 1
    
    await message.answer(
        f"✅ Снято последнее предупреждение у пользователя {user_mention}\n"
        f"📊 Осталось предупреждений: {remaining_warns}",
        parse_mode="HTML"
    )

@dp.message(Command("mute"))
async def cmd_mute(message: Message, command: CommandObject):
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        clown_msg = await message.reply("🤡")
        await asyncio.sleep(2)
        
        await delete_message(message.chat.id, message.message_id)
        await mute_user(
            message.chat.id,
            message.from_user.id,
            MUTE_DURATION,
            "Использование команд бота",
            is_auto=True
        )
        return

    user_data = await resolve_user_reference(message, command.args)
    if not user_data:
        await message.answer("❌ Укажите пользователя (ответом на сообщение или @username/id).")
        return

    user_id, reason = user_data
    await mute_user(message.chat.id, user_id, MUTE_DURATION, reason)

@dp.message(Command("tmute"))
async def cmd_tmute(message: Message, command: CommandObject):
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        clown_msg = await message.reply("🤡")
        await asyncio.sleep(2)
        
        await delete_message(message.chat.id, message.message_id)
        await mute_user(
            message.chat.id,
            message.from_user.id,
            MUTE_DURATION,
            "Использование команд бота",
            is_auto=True
        )
        return

    # Разбираем аргументы
    args = command.args
    
    # Если это ответ на сообщение
    if message.reply_to_message:
        # Получаем ID пользователя из ответа
        user_id = message.reply_to_message.from_user.id
        
        # Оставшиеся аргументы - это время и причина
        if args:
            # Разделяем на время и причину
            parts = args.split(maxsplit=1)
            time_str = parts[0]
            reason = parts[1] if len(parts) > 1 else "Не указана"
        else:
            await message.answer("❌ Укажите время: /tmute [время] [причина] или ответом на сообщение /tmute 1h [причина]")
            return
    else:
        # Если не ответ на сообщение, то парсим аргументы полностью
        if not args:
            await message.answer("❌ Укажите пользователя и время: /tmute @user 1h [причина]")
            return

        parts = args.split(maxsplit=2)
        
        # Проверяем минимальное количество аргументов
        if len(parts) < 2:
            await message.answer("❌ Формат: /tmute <пользователь> <время> [причина]")
            return

        user_identifier = parts[0]
        time_str = parts[1]
        reason = parts[2] if len(parts) > 2 else "Не указана"

        # Получаем ID пользователя
        user_id = await get_user_id_from_message(user_identifier)
        if not user_id:
            await message.answer("❌ Пользователь не найден.")
            return

    # Парсим время
    duration = parse_time(time_str)
    if not duration:
        await message.answer("❌ Неверный формат времени. Используйте: 30m, 2h, 1d, 1w")
        return

    await mute_user(message.chat.id, user_id, duration, reason)

@dp.message(Command("unmute"))
async def cmd_unmute(message: Message, command: CommandObject):
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        clown_msg = await message.reply("🤡")
        await asyncio.sleep(2)
        
        await delete_message(message.chat.id, message.message_id)
        await mute_user(
            message.chat.id,
            message.from_user.id,
            MUTE_DURATION,
            "Использование команд бота",
            is_auto=True
        )
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите пользователя (ответом на сообщение или @username/id).")
        return

    if await unmute_user(message.chat.id, user_id):
        user_mention = await get_user_mention(user_id)
        await message.answer(
            f"✅ Пользователь {user_mention} размучен.",
            parse_mode="HTML"
        )
    else:
        await message.answer("❌ Ошибка при размуте пользователя.")

@dp.message(Command("ban"))
async def cmd_ban(message: Message, command: CommandObject):
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        clown_msg = await message.reply("🤡")
        await asyncio.sleep(2)
        
        await delete_message(message.chat.id, message.message_id)
        await mute_user(
            message.chat.id,
            message.from_user.id,
            MUTE_DURATION,
            "Использование команд бота",
            is_auto=True
        )
        return

    user_data = await resolve_user_reference(message, command.args)
    if not user_data:
        await message.answer("❌ Укажите пользователя (ответом на сообщение или @username/id).")
        return

    user_id, reason = user_data
    await ban_user(message.chat.id, user_id, None, reason)

@dp.message(Command("tban"))
async def cmd_tban(message: Message, command: CommandObject):
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        clown_msg = await message.reply("🤡")
        await asyncio.sleep(2)
        
        await delete_message(message.chat.id, message.message_id)
        await mute_user(
            message.chat.id,
            message.from_user.id,
            MUTE_DURATION,
            "Использование команд бота",
            is_auto=True
        )
        return

    # Разбираем аргументы
    args = command.args
    
    # Если это ответ на сообщение
    if message.reply_to_message:
        # Получаем ID пользователя из ответа
        user_id = message.reply_to_message.from_user.id
        
        # Оставшиеся аргументы - это время и причина
        if args:
            # Разделяем на время и причину
            parts = args.split(maxsplit=1)
            time_str = parts[0]
            reason = parts[1] if len(parts) > 1 else "Не указана"
        else:
            await message.answer("❌ Укажите время: /tban [время] [причина] или ответом на сообщение /tban 1d [причина]")
            return
    else:
        # Если не ответ на сообщение, то парсим аргументы полностью
        if not args:
            await message.answer("❌ Укажите пользователя и время: /tban @user 1d [причина]")
            return

        parts = args.split(maxsplit=2)
        
        if len(parts) < 2:
            await message.answer("❌ Формат: /tban <пользователь> <время> [причина]")
            return

        user_identifier = parts[0]
        time_str = parts[1]
        reason = parts[2] if len(parts) > 2 else "Не указана"

        # Получаем ID пользователя
        user_id = await get_user_id_from_message(user_identifier)
        if not user_id:
            await message.answer("❌ Пользователь не найден.")
            return

    # Парсим время
    duration = parse_time(time_str)
    if not duration:
        await message.answer("❌ Неверный формат времени. Используйте: 30m, 2h, 1d, 1w")
        return

    await ban_user(message.chat.id, user_id, duration, reason)

@dp.message(Command("unban"))
async def cmd_unban(message: Message, command: CommandObject):
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        clown_msg = await message.reply("🤡")
        await asyncio.sleep(2)
        
        await delete_message(message.chat.id, message.message_id)
        await mute_user(
            message.chat.id,
            message.from_user.id,
            MUTE_DURATION,
            "Использование команд бота",
            is_auto=True
        )
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите пользователя (@username/id).")
        return

    if await unban_user(message.chat.id, user_id):
        user_mention = await get_user_mention(user_id)
        await message.answer(
            f"✅ Пользователь {user_mention} разбанен.",
            parse_mode="HTML"
        )
    else:
        await message.answer("❌ Ошибка при разбане пользователя.")

@dp.message(Command("cc"))
async def cmd_clear_chat(message: Message, command: CommandObject):
    """Очистка указанного количества сообщений в чате"""
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        clown_msg = await message.reply("🤡")
        await asyncio.sleep(2)
        
        await delete_message(message.chat.id, message.message_id)
        await mute_user(
            message.chat.id,
            message.from_user.id,
            MUTE_DURATION,
            "Использование команд бота",
            is_auto=True
        )
        return

    # Получаем количество сообщений из аргументов
    if not command.args:
        await message.answer(
            "❌ Укажите количество сообщений для удаления:\n"
            "Пример: <code>/cc 100</code> - удалит последние 100 сообщений",
            parse_mode="HTML"
        )
        return

    try:
        count = int(command.args.strip())
        if count <= 0:
            await message.answer("❌ Количество сообщений должно быть больше 0.")
            return
        if count > 200:  # Уменьшено ограничение для стабильности
            await message.answer("❌ Максимальное количество сообщений для удаления - 200.")
            return
    except ValueError:
        await message.answer("❌ Неверный формат числа. Укажите целое число.")
        return

    # Подтверждение начала очистки
    confirm_msg = await message.answer(
        f"🧹 <b>Начинаю очистку последних {count} сообщений...</b>\n\n"
        "⏳ Это может занять некоторое время...",
        parse_mode="HTML"
    )

    try:
        deleted_count = 0
        skipped_count = 0
        
        # Удаляем САМОЕ ПЕРВОЕ - команду /cc
        await delete_message(message.chat.id, message.message_id)
        
        # Начинаем с сообщения ПЕРЕД подтверждающим сообщением
        current_message_id = confirm_msg.message_id - 1
        
        # Удаляем сообщения ВПЕРЁД (от старых к новым)
        while (current_message_id > 0 and 
               deleted_count + skipped_count < count):
            
            try:
                # Пытаемся удалить сообщение
                success = await delete_message(message.chat.id, current_message_id)
                if success:
                    deleted_count += 1
                else:
                    skipped_count += 1
                
                # Небольшая задержка чтобы не превысить лимиты Telegram
                await asyncio.sleep(0.1)
                
            except Exception as e:
                skipped_count += 1
                # Продолжаем с следующим сообщением при любой ошибке
            
            current_message_id -= 1
            
            # Обновляем прогресс каждые 10 сообщений
            if (deleted_count + skipped_count) % 10 == 0:
                try:
                    await confirm_msg.edit_text(
                        f"🧹 <b>Очистка сообщений...</b>\n\n"
                        f"📊 <b>Прогресс:</b>\n"
                        f"• 🗑️ Удалено: {deleted_count}\n"
                        f"• ⏭️ Пропущено: {skipped_count}\n"
                        f"• ⏳ Осталось: {count - (deleted_count + skipped_count)}",
                        parse_mode="HTML"
                    )
                except:
                    pass
        
        # Обновляем сообщение с результатами
        result_text = (
            f"✅ <b>Очистка чата завершена!</b>\n\n"
            f"📊 <b>Результаты:</b>\n"
            f"• 🗑️ Удалено сообщений: {deleted_count}\n"
            f"• ⏭️ Пропущено сообщений: {skipped_count}\n"
            f"• 🧹 Всего обработано: {deleted_count + skipped_count}\n\n"
            f"<i>Пропущены недоступные или уже удаленные сообщения</i>"
        )
        
        await confirm_msg.edit_text(
            result_text,
            parse_mode="HTML"
        )
        
        # Удаляем сообщение с результатами через 15 секунд
        await asyncio.sleep(15)
        await delete_message(message.chat.id, confirm_msg.message_id)
        
    except Exception as e:
        logger.error(f"Ошибка при очистке чата: {e}")
        try:
            await confirm_msg.edit_text(
                f"❌ <b>Ошибка при очистке чата:</b>\n{str(e)}",
                parse_mode="HTML"
            )
        except:
            pass

@dp.message(Command("admin_add"))
async def cmd_admin_add(message: Message, command: CommandObject):
    if not await is_owner(message.from_user.id):
        await message.answer("❌ Только владелец может добавлять администраторов.")
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите пользователя (ответом на сообщение или @username/id).")
        return
    
    # Получаем информацию о пользователе
    try:
        user = await bot.get_chat(user_id)
        user_mention = await get_user_mention(user_id)
        
        # Создаем права администратора для группы
        admin_rights = ChatAdministratorRights(
            is_anonymous=False,
            can_manage_chat=False,
            can_delete_messages=True,
            can_manage_video_chats=False,
            can_restrict_members=True,
            can_promote_members=False,
            can_change_info=False,
            can_invite_users=True,
            can_post_messages=True,
            can_edit_messages=False,
            can_pin_messages=False,
            can_manage_topics=False
        )
        
        # Назначаем администратора в группе
        await bot.promote_chat_member(
            chat_id=message.chat.id,
            user_id=user_id,
            can_manage_chat=admin_rights.can_manage_chat,
            can_delete_messages=admin_rights.can_delete_messages,
            can_manage_video_chats=admin_rights.can_manage_video_chats,
            can_restrict_members=admin_rights.can_restrict_members,
            can_promote_members=admin_rights.can_promote_members,
            can_change_info=admin_rights.can_change_info,
            can_invite_users=admin_rights.can_invite_users,
            can_post_messages=admin_rights.can_post_messages,
            can_edit_messages=admin_rights.can_edit_messages,
            can_pin_messages=admin_rights.can_pin_messages,
            can_manage_topics=admin_rights.can_manage_topics,
            is_anonymous=admin_rights.is_anonymous
        )
        
        # Добавляем в базу данных бота
        add_admin(user_id, message.from_user.id)
        
        # КОРОТКОЕ подтверждение
        await message.answer(
            f"✅ Пользователь {user_mention} добавлен в администраторы бота и назначен администратором группы.",
            parse_mode="HTML"
        )
        
        # Уведомляем нового администратора
        try:
            await bot.send_message(
                chat_id=user_id,
                text=f"🎉 <b>Вам назначены права администратора</b>\n\n"
                     f"Вы были назначены администратором в группе и получили права администратора бота.\n\n"
                     f"<b>Доступные команды бота:</b>\n"
                     f"/warn - выдать предупреждение\n"
                     f"/mute - замутить пользователя\n"
                     f"/ban - забанить пользователя\n"
                     f"/cc - очистить сообщения\n"
                     f"/report - система жалоб\n\n"
                     f"<i>Используйте свои полномочия ответственно!</i>",
                parse_mode="HTML"
            )
        except:
            pass
        
    except Exception as e:
        logger.error(f"Ошибка при назначении администратора: {e}")
        await message.answer(f"❌ Ошибка при назначении администратора: {str(e)}")

@dp.message(Command("admin_remove"))
async def cmd_admin_remove(message: Message, command: CommandObject):
    if not await is_owner(message.from_user.id):
        await message.answer("❌ Только владелец может удалять администраторов.")
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите пользователя (ответом на сообщение или @username/id).")
        return
    
    user_mention = await get_user_mention(user_id)
    
    try:
        # Снимаем права администратора в группе
        await bot.promote_chat_member(
            chat_id=message.chat.id,
            user_id=user_id,
            can_manage_chat=False,
            can_delete_messages=False,
            can_manage_video_chats=False,
            can_restrict_members=False,
            can_promote_members=False,
            can_change_info=False,
            can_invite_users=False,
            can_post_messages=False,
            can_edit_messages=False,
            can_pin_messages=False,
            can_manage_topics=False,
            is_anonymous=False
        )
        
        # Удаляем из базы данных бота
        remove_admin(user_id)
        
        # Очищаем предупреждения администратора
        clear_admin_warns(user_id)
        
        # Отправляем подтверждение
        await message.answer(
            f"✅ Пользователь {user_mention} удален из администраторов бота и лишен прав администратора группы.",
            parse_mode="HTML"
        )
        
        # Уведомляем бывшего администратора
        try:
            await bot.send_message(
                chat_id=user_id,
                text="ℹ️ <b>Ваши права администратора были отозваны</b>\n\n"
                     "Вы больше не являетесь администратором группы и бота.",
                parse_mode="HTML"
            )
        except:
            pass  # Не удалось отправить уведомление
        
    except Exception as e:
        logger.error(f"Ошибка при снятии администратора: {e}")
        await message.answer(f"❌ Ошибка при снятии администратора: {str(e)}")

@dp.message(Command("admin_list"))
async def cmd_admin_list(message: Message):
    if not await is_bot_admin(message.from_user.id):
        await message.answer("❌ У вас нет прав для этой команды.")
        return

    admins = get_all_admins()
    if not admins:
        await message.answer("📋 Список администраторов пуст.")
        return

    admin_mentions = []
    for admin_id in admins:
        try:
            user = await bot.get_chat(admin_id)
            name = user.first_name or user.username or str(admin_id)
            admin_mentions.append(f"• <a href='tg://user?id={admin_id}'>{name}</a>")
        except:
            admin_mentions.append(f"• {admin_id}")

    await message.answer(
        f"👮 <b>Список администраторов бота:</b>\n\n" + "\n".join(admin_mentions),
        parse_mode="HTML",
    )

@dp.message(Command("admin_warn", "awarn"))
async def cmd_admin_warn(message: Message, command: CommandObject):
    if not await is_owner(message.from_user.id):
        await message.answer("❌ Только владелец может выдавать предупреждения администраторам.")
        return

    # Получаем аргументы команды
    args = command.args.split(maxsplit=1) if command.args else []
    
    # Если это ответ на сообщение
    if message.reply_to_message:
        user_id = message.reply_to_message.from_user.id
        reason = args[0] if args else "Не указана"
    else:
        # Если аргументы переданы напрямую
        if len(args) < 1:
            await message.answer("❌ Формат: /admin_warn @user [причина] или ответом на сообщение")
            return
        
        user_id = await get_user_id_from_message(args[0])
        reason = args[1] if len(args) > 1 else "Не указана"

    if not user_id:
        await message.answer("❌ Пользователь не найден.")
        return

    # Проверяем, является ли пользователь администратором
    is_admin_user = await is_chat_admin_or_bot_admin(user_id, message.chat.id)
    
    if not is_admin_user:
        await message.answer("❌ Указанный пользователь не является администратором.")
        return

    # Добавляем предупреждение администратору
    add_admin_warn(user_id, reason, message.from_user.id)
    
    # Получаем текущее количество предупреждений
    admin_warns = get_admin_warns(user_id)
    warn_count = len(admin_warns)
    
    user_mention = await get_user_mention(user_id)
    owner_mention = await get_user_mention(message.from_user.id)
    
    # Отправляем сообщение о выдаче предупреждения
    await message.answer(
        f"⚠️ <b>Администратору {user_mention} выдано предупреждение</b>\n\n"
        f"📝 <b>Причина:</b> {reason}\n"
        f"👮 <b>Выдал:</b> {owner_mention}\n"
        f"📊 <b>Текущее количество предупреждений:</b> {warn_count}/3\n\n"
        f"<i>При получении 3 предупреждений права администратора будут отозваны автоматически.</i>",
        parse_mode="HTML"
    )
    
    # Отправляем уведомление администратору
    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"⚠️ <b>Вам выдано предупреждение администратора</b>\n\n"
                 f"📝 <b>Причина:</b> {reason}\n"
                 f"👮 <b>Выдал:</b> {owner_mention}\n"
                 f"📊 <b>Текущее количество предупреждений:</b> {warn_count}/3\n\n"
                 f"<i>При получении 3 предупреждений вы будете автоматически сняты с должности администратора.</i>",
            parse_mode="HTML"
        )
    except:
        pass
    
    # Проверяем, достиг ли администратор 3 предупреждений
    if warn_count >= 3:
        # Автоматически снимаем права администратора
        try:
            # Снимаем права администратора в группе
            await bot.promote_chat_member(
                chat_id=message.chat.id,
                user_id=user_id,
                can_manage_chat=False,
                can_delete_messages=False,
                can_manage_video_chats=False,
                can_restrict_members=False,
                can_promote_members=False,
                can_change_info=False,
                can_invite_users=False,
                can_post_messages=False,
                can_edit_messages=False,
                can_pin_messages=False,
                can_manage_topics=False,
                is_anonymous=False
            )
            
            # Удаляем из базы данных бота
            remove_admin(user_id)
            
            # Очищаем предупреждения администратора
            clear_admin_warns(user_id)
            
            # Отправляем уведомление о снятии
            await message.answer(
                f"🚫 <b>Администратор {user_mention} снят с должности</b>\n\n"
                f"📝 <b>Причина:</b> 3 предупреждения администратора\n"
                f"📊 <b>Всего предупреждений:</b> {warn_count}\n\n"
                f"<i>Права администратора в группе и боте были отозваны автоматически.</i>",
                parse_mode="HTML"
            )
            
            # Уведомляем бывшего администратора
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text="🚫 <b>Вы сняты с должности администратора</b>\n\n"
                         f"📝 <b>Причина:</b> 3 предупреждения администратора\n"
                         f"📊 <b>Всего предупреждений:</b> {warn_count}\n\n"
                         "<i>Ваши права администратора в группе и боте были отозваны автоматически.</i>",
                    parse_mode="HTML"
                )
            except:
                pass
                
        except Exception as e:
            logger.error(f"Ошибка при автоматическом снятии администратора: {e}")
            await message.answer(f"❌ Ошибка при снятии администратора: {str(e)}")

@dp.message(Command("admin_unwarn", "unawarn"))
async def cmd_admin_unwarn(message: Message, command: CommandObject):
    if not await is_owner(message.from_user.id):
        await message.answer("❌ Только владелец может снимать предупреждения администраторам.")
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите администратора (ответом на сообщение или @username/id).")
        return

    # Проверяем, является ли пользователь администратором чата или бота
    is_chat_admin_user = await is_chat_admin(user_id, message.chat.id)
    is_bot_admin_user = is_admin(user_id)
    
    if not (is_chat_admin_user or is_bot_admin_user):
        await message.answer("❌ Указанный пользователь не является администратором.")
        return

    # Получаем текущие предупреждения
    warns_before = get_admin_warns(user_id)
    warn_count_before = len(warns_before)
    
    # Снимаем последнее предупреждение
    warn_id = remove_last_admin_warn(user_id)
    user_mention = await get_user_mention(user_id)
    owner_mention = await get_user_mention(message.from_user.id)

    if warn_id:
        # Получаем обновленное количество предупреждений
        warns_after = get_admin_warns(user_id)
        warn_count_after = len(warns_after)
        
        await message.answer(
            f"✅ <b>Снято последнее предупреждение у администратора {user_mention}</b>\n\n"
            f"👮 <b>Снял:</b> {owner_mention}\n"
            f"📊 <b>Предупреждений было:</b> {warn_count_before}\n"
            f"📊 <b>Предупреждений стало:</b> {warn_count_after}\n\n"
            f"<i>Одно предупреждение было успешно снято.</i>",
            parse_mode="HTML"
        )
        
        # Уведомляем администратора
        try:
            await bot.send_message(
                chat_id=user_id,
                text=f"✅ <b>Вам снято одно предупреждение</b>\n\n"
                     f"👮 <b>Снял:</b> {owner_mention}\n"
                     f"📊 <b>Текущее количество предупреждений:</b> {warn_count_after}/3\n\n"
                     f"<i>Одно предупреждение было снято владельцем.</i>",
                parse_mode="HTML"
            )
        except:
            pass
    else:
        await message.answer(f"✅ У администратора {user_mention} нет активных предупреждений.")

@dp.message(Command("admin_warns"))
async def cmd_admin_warns(message: Message, command: CommandObject):
    if not await is_bot_admin(message.from_user.id):
        await message.answer("❌ У вас нет прав для этой команды.")
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите администратора (ответом на сообщение или @username/id).")
        return

    # ЗАМЕНИТЕ ЭТУ ПРОВЕРКУ:
    # if not is_admin(user_id):
    # НА ЭТУ:
    if not await is_chat_admin_or_bot_admin(user_id, message.chat.id):
        await message.answer("❌ Указанный пользователь не является администратором.")
        return

    warns = get_admin_warns(user_id)
    user_mention = await get_user_mention(user_id)

    if not warns:
        await message.answer(f"✅ У администратора {user_mention} нет активных предупреждений.")
        return

    warns_text = "\n".join(
        [
            f"• {warn['reason']} (выдал: {await get_user_mention(warn['issued_by'])}, {warn['issued_at']})"
            for warn in warns
        ]
    )

    await message.answer(
        f"⚠️ <b>Предупреждения администратора {user_mention}</b>\n\n{warns_text}",
        parse_mode="HTML",
    )

@dp.message(Command("check_admin"))
async def cmd_check_admin(message: Message, command: CommandObject):
    """Команда для проверки прав администратора"""
    if not await is_bot_admin(message.from_user.id):  # Добавьте эту проверку
        await message.answer("❌ У вас нет прав для этой команды.")
        return
    
    if not command.args:
        await message.answer("❌ Укажите пользователя для проверки: /check_admin @username")
        return
    
    user_id = await get_user_id_from_message(command.args)
    if not user_id:
        await message.answer("❌ Пользователь не найден.")
        return
    
    is_chat_admin_user = await is_chat_admin(user_id, message.chat.id)
    is_bot_admin_user = is_admin(user_id)
    is_owner_user = user_id in ADMIN_IDS
    is_combined_admin = await is_chat_admin_or_bot_admin(user_id, message.chat.id)
    
    user_mention = await get_user_mention(user_id)
    
    status_text = f"👤 {user_mention}:\n"
    status_text += f"• Владелец бота: {'✅' if is_owner_user else '❌'}\n"
    status_text += f"• Админ бота (в базе): {'✅' if is_bot_admin_user else '❌'}\n"
    status_text += f"• Админ чата (Telegram): {'✅' if is_chat_admin_user else '❌'}\n"
    status_text += f"• Общий статус администратора: {'✅' if is_combined_admin else '❌'}"
    
    await message.answer(status_text, parse_mode="HTML")

@dp.message(Command("ban_info"))
async def cmd_ban_info(message: Message, command: CommandObject):
    """Полная информация о блокировках пользователя"""
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        await message.answer("❌ У вас нет прав для этой команды.")
        return
    
    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите пользователя: /ban_info @username")
        return
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Проверяем все типы наказаний
    cursor.execute("SELECT COUNT(*) FROM warns WHERE user_id = ? AND chat_id = ?", 
                  (user_id, message.chat.id))
    warn_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM mutes WHERE user_id = ? AND chat_id = ? AND is_active = TRUE", 
                  (user_id, message.chat.id))
    mute_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM bans WHERE user_id = ? AND chat_id = ? AND is_active = TRUE", 
                  (user_id, message.chat.id))
    ban_count = cursor.fetchone()[0]
    
    # Детальная информация о банах
    cursor.execute("""
        SELECT reason, issued_by, issued_at, expires_at 
        FROM bans 
        WHERE user_id = ? AND chat_id = ? 
        ORDER BY issued_at DESC
    """, (user_id, message.chat.id))
    
    bans = cursor.fetchall()
    conn.close()
    
    user_mention = await get_user_mention(user_id)
    
    response = f"📋 <b>Статус пользователя: {user_mention}</b>\n\n"
    response += f"⚠️ <b>Предупреждения:</b> {warn_count}\n"
    response += f"🔇 <b>Активные муты:</b> {mute_count}\n"
    response += f"🚫 <b>Активные баны:</b> {ban_count}\n\n"
    
    if bans:
        response += "<b>История банов:</b>\n"
        for i, ban in enumerate(bans[:5], 1):  # Показываем последние 5 банов
            reason, issued_by, issued_at, expires_at = ban
            issued_by_mention = await get_user_mention(issued_by)
            expires_text = f"до {expires_at}" if expires_at else "навсегда"
            
            status = "✅ Активен" if ban_count > 0 and i == 1 else "❌ Неактивен"
            
            response += f"\n{i}. {status}\n"
            response += f"   📝 <b>Причина:</b> {reason or 'Не указана'}\n"
            response += f"   👮 <b>Выдал:</b> {issued_by_mention}\n"
            response += f"   🕐 <b>Время:</b> {issued_at}\n"
            response += f"   ⏳ <b>Срок:</b> {expires_text}\n"
    else:
        response += "📭 <i>Записей о банах не найдено</i>"
    
    await message.answer(response, parse_mode="HTML")

@dp.message(Command("report"))
async def cmd_report(message: Message, command: CommandObject):
    """Обработчик команды /report для жалоб на пользователей"""
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("❌ Эта команда работает только в групповом чате.")
        return
        
    if not command.args and not message.reply_to_message:
        await message.answer(
            "📢 <b>Как отправить жалобу?</b>\n\n"
            "1. Ответьте на сообщение нарушителя командой <code>/report</code>\n"
            "2. Или укажите пользователя: <code>/report @username [причина]</code>\n\n"
            "⚠️ <i>Жалобы проверяются администраторами</i>",
            parse_mode="HTML"
        )
        return
    
    # Удаляем команду report
    await delete_message(message.chat.id, message.message_id)
    
    # Получаем данные о нарушителе
    if message.reply_to_message:
        reported_user_id = message.reply_to_message.from_user.id
        reason = command.args if command.args else "Нарушение правил чата"
        reported_message_id = message.reply_to_message.message_id
    else:
        args = command.args.split(maxsplit=1) if command.args else []
        if not args:
            await message.answer("❌ Укажите пользователя: /report @username [причина]")
            return
            
        reported_user_id = await get_user_id_from_message(args[0])
        if not reported_user_id:
            await message.answer("❌ Пользователь не найден.")
            return
            
        reason = args[1] if len(args) > 1 else "Нарушение правил чата"
        reported_message_id = None
    
    # Получаем информацию о пользователях
    reporter_mention = await get_user_mention(message.from_user.id)
    reported_mention = await get_user_mention(reported_user_id)
    
    # Формируем сообщение для администраторов (с указанием ID жалобщика)
    report_text = (
        f"🚨 <b>Новая жалоба</b>\n\n"
        f"👤 <b>Жалоба от:</b> {reporter_mention} (ID: {message.from_user.id})\n"
        f"⚠️ <b>На пользователя:</b> {reported_mention}\n"
        f"📝 <b>Причина:</b> {reason}\n"
    )
    
    if reported_message_id:
        chat_id_str = str(message.chat.id).replace('-100', '')
        report_text += f"📎 <b>Сообщение:</b> <a href='https://t.me/c/{chat_id_str}/{reported_message_id}'>ссылка</a>"
    
    # Создаем клавиатуру с кнопками для быстрых действий
    keyboard = InlineKeyboardBuilder()
    keyboard.row(
        InlineKeyboardButton(
            text="⚠️ Предупреждение", 
            callback_data=f"warn:{reported_user_id}:{message.chat.id}"
        ),
        InlineKeyboardButton(
            text="🔇 Мут 1д", 
            callback_data=f"mute1:{reported_user_id}:{message.chat.id}"
        )
    )
    keyboard.row(
        InlineKeyboardButton(
            text="🔇 Мут 2д", 
            callback_data=f"mute2:{reported_user_id}:{message.chat.id}"
        ),
        InlineKeyboardButton(
            text="🔇 Мут 3д", 
            callback_data=f"mute3:{reported_user_id}:{message.chat.id}"
        )
    )
    keyboard.row(
        InlineKeyboardButton(
            text="🚫 Бан", 
            callback_data=f"ban:{reported_user_id}:{message.chat.id}"
        ),
        InlineKeyboardButton(
            text="❌ Отклонить", 
            callback_data=f"dismiss:{reported_user_id}:{message.chat.id}"
        )
    )
    
    # СНАЧАЛА отправляем подтверждение пользователю
    confirm_msg = await message.answer(
        f"⏳ Ваша жалоба на {reported_mention} обрабатывается...",
        parse_mode="HTML"
    )
    
    # Получаем список администраторов бота
    admin_ids = list(set(get_all_admins() + ADMIN_IDS))
    
    # Функция для отправки жалобы одному администратору
    async def send_to_admin(admin_id):
        try:
            if admin_id == message.from_user.id or admin_id <= 0:
                return False
                
            report_msg = await bot.send_message(
                chat_id=admin_id,
                text=report_text,
                parse_mode="HTML",
                reply_markup=keyboard.as_markup()
            )
            
            # Запускаем задачу удаления сообщения через 10 минут
            async def delete_later():
                await asyncio.sleep(600)
                try:
                    await delete_message(admin_id, report_msg.message_id)
                except:
                    pass
            
            asyncio.create_task(delete_later())
            return True
            
        except Exception as e:
            logger.error(f"Не удалось отправить жалобу администратору {admin_id}: {e}")
            return False
    
    # Отправляем жалобы всем администраторам ПАРАЛЛЕЛЬНО
    tasks = [send_to_admin(admin_id) for admin_id in admin_ids]
    results = await asyncio.gather(*tasks)
    success_count = sum(results)
    
    # Обновляем сообщение с подтверждением
    if success_count > 0:
        await confirm_msg.edit_text(
            f"✅ Ваша жалоба на {reported_mention} отправлена {success_count} администраторам.",
            parse_mode="HTML"
        )
    else:
        await confirm_msg.edit_text(
            "❌ Не удалось отправить жалобу. Нет доступных администраторов.",
            parse_mode="HTML"
        )
    
    # Удаляем подтверждение через 10 секунд
    await asyncio.sleep(10)
    await delete_message(message.chat.id, confirm_msg.message_id)

@dp.callback_query(F.data.startswith(("warn:", "mute", "ban:", "dismiss:")))
async def handle_report_callback(callback: types.CallbackQuery):
    """Обработчик действий по жалобам"""
    try:
        data = callback.data
        admin_id = callback.from_user.id
        
        # Разбираем callback_data: action:user_id:chat_id
        if data.startswith("mute1:"):
            action = "mute1"
            parts = data.split(':', 2)
        elif data.startswith("mute2:"):
            action = "mute2"
            parts = data.split(':', 2)
        elif data.startswith("mute3:"):
            action = "mute3"
            parts = data.split(':', 2)
        else:
            parts = data.split(':', 2)
            action = parts[0]
        
        if len(parts) < 3:
            await callback.answer("❌ Неверный формат данных.")
            return
            
        user_id = int(parts[1])
        chat_id = int(parts[2])
        
        # Проверяем права администратора
        if not await is_chat_admin_or_bot_admin(admin_id, chat_id):
            await callback.answer("❌ У вас нет прав администратора.")
            return
        
        user_mention = await get_user_mention(user_id)
        admin_mention = await get_user_mention(admin_id)
        
        # Извлекаем информацию о жалобщике из текста сообщения
        reporter_id = None
        reporter_mention = "Неизвестный пользователь"
        
        # Ищем ID жалобщика в тексте сообщения
        message_text = callback.message.text or callback.message.caption or ""
        
        # Ищем ID жалобщика в формате: (ID: 123456789)
        id_match = re.search(r'\(ID:\s*(\d+)\)', message_text)
        if id_match:
            reporter_id = int(id_match.group(1))
            reporter_mention = await get_user_mention(reporter_id)
        else:
            # Альтернативный поиск: ищем упоминание пользователя
            mention_match = re.search(r'tg://user\?id=(\d+)', message_text)
            if mention_match:
                reporter_id = int(mention_match.group(1))
                reporter_mention = await get_user_mention(reporter_id)
        
        result_message = None
        
        if action == "warn":
            success = await warn_user(chat_id, user_id, f"Жалоба от пользователя {reporter_mention}")
            action_text = "выдано предупреждение" if success else "ошибка при выдаче предупреждения"
            
            if success:
                result_message = await bot.send_message(
                    chat_id,
                    f"⚠️ <b>По жалобе пользователя {reporter_mention}</b>\n\n"
                    f"👤 Пользователь {user_mention} получил предупреждение\n"
                    f"👮 Действие выполнено: {admin_mention}",
                    parse_mode="HTML"
                )
            
        elif action == "mute1":
            duration = timedelta(days=1)
            duration_str = "1 день"
            success = await mute_user(chat_id, user_id, duration, f"Жалоба от пользователя {reporter_mention}")
            action_text = f"мут на {duration_str}" if success else "ошибка при муте"
            
            if success:
                result_message = await bot.send_message(
                    chat_id,
                    f"🔇 <b>По жалобе пользователя {reporter_mention}</b>\n\n"
                    f"👤 Пользователь {user_mention} получил мут на {duration_str}\n"
                    f"👮 Действие выполнено: {admin_mention}",
                    parse_mode="HTML"
                )
                
        elif action == "mute2":
            duration = timedelta(days=2)
            duration_str = "2 дня"
            success = await mute_user(chat_id, user_id, duration, f"Жалоба от пользователя {reporter_mention}")
            action_text = f"мут на {duration_str}" if success else "ошибка при муте"
            
            if success:
                result_message = await bot.send_message(
                    chat_id,
                    f"🔇 <b>По жалобе пользователя {reporter_mention}</b>\n\n"
                    f"👤 Пользователь {user_mention} получил мут на {duration_str}\n"
                    f"👮 Действие выполнено: {admin_mention}",
                    parse_mode="HTML"
                )
                
        elif action == "mute3":
            duration = timedelta(days=3)
            duration_str = "3 дня"
            success = await mute_user(chat_id, user_id, duration, f"Жалоба от пользователя {reporter_mention}")
            action_text = f"мут на {duration_str}" if success else "ошибка при муте"
            
            if success:
                result_message = await bot.send_message(
                    chat_id,
                    f"🔇 <b>По жалобе пользователя {reporter_mention}</b>\n\n"
                    f"👤 Пользователь {user_mention} получил мут на {duration_str}\n"
                    f"👮 Действие выполнено: {admin_mention}",
                    parse_mode="HTML"
                )
            
        elif action == "ban":
            success = await ban_user(chat_id, user_id, None, f"Жалоба от пользователя {reporter_mention}")
            action_text = "бан" if success else "ошибка при бане"
            
            if success:
                result_message = await bot.send_message(
                    chat_id,
                    f"🚫 <b>По жалобе пользователя {reporter_mention}</b>\n\n"
                    f"👤 Пользователь {user_mention} забанен\n"
                    f"👮 Действие выполнено: {admin_mention}",
                    parse_mode="HTML"
                )
            
        elif action == "dismiss":
            action_text = "отклонено"
            if reporter_id:
                try:
                    await bot.send_message(
                        reporter_id,
                        f"❌ Ваша жалоба на {user_mention} была отклонена администратором {admin_mention}",
                        parse_mode="HTML"
                    )
                except:
                    pass  # Не удалось отправить сообщение жалобщику
            
            await callback.message.edit_text(
                f"❌ {callback.message.html_text}\n\n👮 Отклонено: {admin_mention}",
                reply_markup=None,
                parse_mode="HTML"
            )
            await callback.answer("Жалоба отклонена")
            return
        
        # Отправляем уведомление жалобщику
        if success and reporter_id:
            try:
                await bot.send_message(
                    reporter_id,
                    f"✅ Ваша жалоба на {user_mention} была рассмотрена\n"
                    f"👮 Администратор: {admin_mention}\n"
                    f"📝 Результат: {action_text}",
                    parse_mode="HTML"
                )
            except:
                pass  # Не удалось отправить сообщение жалобщику
        
        # Обновляем сообщение с жалобой
        if action != "dismiss":
            await callback.message.edit_text(
                f"✅ {callback.message.html_text}\n\n👮 Принято: {admin_mention} - {action_text}",
                reply_markup=None,
                parse_mode="HTML"
            )
        
        # Удаляем результат через 30 секунд
        if result_message:
            await asyncio.sleep(30)
            await delete_message(chat_id, result_message.message_id)
        
        await callback.answer(f"Действие выполнено: {action_text}")
        
    except Exception as e:
        logger.error(f"Ошибка обработки callback: {e}")
        try:
            await callback.answer("❌ Ошибка при обработке действия")
        except:
            pass

@dp.callback_query(F.data == "complain_admin")
async def start_complaint(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    
    await callback.message.answer(
        "📝 <b>Оставьте жалобу на администратора</b>\n\n"
        "Пожалуйста, укажите ваш юзернейм (например, @username):",
        parse_mode="HTML"
    )
    await state.set_state(AdminComplaintStates.waiting_for_username)

@dp.message(AdminComplaintStates.waiting_for_username)
async def process_username(message: Message, state: FSMContext):
    username = message.text.strip()
    
    # Проверяем формат юзернейма
    if not username.startswith('@'):
        await message.answer("❌ Юзернейм должен начинаться с @. Пожалуйста, укажите ваш юзернейм правильно:")
        return
    
    await state.update_data(username=username)
    await message.answer(
        "👮 Теперь укажите юзернейм администратора, на которого хотите пожаловаться (например, @admin):",
        parse_mode="HTML"
    )
    await state.set_state(AdminComplaintStates.waiting_for_admin_username)

@dp.message(AdminComplaintStates.waiting_for_admin_username)
async def process_admin_username(message: Message, state: FSMContext):
    admin_username = message.text.strip()
    
    # Проверяем формат юзернейма
    if not admin_username.startswith('@'):
        await message.answer("❌ Юзернейм администратора должен начинаться с @. Пожалуйста, укажите юзернейм правильно:")
        return
    
    await state.update_data(admin_username=admin_username)
    await message.answer(
        "📋 Теперь укажите краткое описание жалобы (например: 'Некорректное поведение', 'Злоупотребление полномочиями' и т.д.):",
        parse_mode="HTML"
    )
    await state.set_state(AdminComplaintStates.waiting_for_description)

@dp.message(AdminComplaintStates.waiting_for_description)
async def process_description(message: Message, state: FSMContext):
    description = message.text.strip()
    
    if len(description) < 5:
        await message.answer("❌ Описание слишком короткое. Пожалуйста, укажите более подробное описание:")
        return
    
    await state.update_data(description=description)
    await message.answer(
        "📝 Теперь опишите подробно суть жалобы. Расскажите, что именно произошло, когда и при каких обстоятельствах:",
        parse_mode="HTML"
    )
    await state.set_state(AdminComplaintStates.waiting_for_complaint_text)

@dp.message(AdminComplaintStates.waiting_for_complaint_text)
async def process_complaint_text(message: Message, state: FSMContext):
    complaint_text = message.text.strip()
    
    if len(complaint_text) < 20:
        await message.answer("❌ Текст жалобы слишком короткий. Пожалуйста, опишите ситуацию более подробно:")
        return
    
    await state.update_data(complaint_text=complaint_text)
    await message.answer(
        "📎 Теперь пришлите доказательства (скриншоты, фотографии, документы). "
        "Если доказательств нет, отправьте любое сообщение для продолжения:",
        parse_mode="HTML"
    )
    await state.set_state(AdminComplaintStates.waiting_for_evidence)

@dp.message(AdminComplaintStates.waiting_for_evidence)
async def process_evidence(message: Message, state: FSMContext):
    # Проверяем, не заблокирован ли пользователь
    if is_user_blocked(message.from_user.id):
        await message.answer(
            "❌ Вы заблокированы в боте и не можете подавать жалобы.",
            parse_mode="HTML",
            reply_markup=get_main_keyboard()
        )
        await state.clear()
        return
    
    # Получаем все данные из состояния
    data = await state.get_data()
    
    # Проверяем обязательные поля
    required_fields = ['username', 'admin_username', 'description', 'complaint_text']
    missing_fields = []
    
    for field in required_fields:
        if field not in data or not data[field]:
            missing_fields.append(field)
    
    if missing_fields:
        await message.answer(
            f"❌ Ошибка: отсутствуют обязательные данные ({', '.join(missing_fields)}). "
            f"Пожалуйста, начните процесс подачи жалобы заново.",
            parse_mode="HTML",
            reply_markup=get_main_keyboard()
        )
        await state.clear()
        return
    
    # Обеспечиваем, что обязательные поля не пустые
    username = data['username'] or "Неизвестный"
    admin_username = data['admin_username'] or "Неизвестный"
    description = data['description'] or "Не указано"
    complaint_text = data['complaint_text'] or "Не указано"
    
    # Сохраняем доказательства (фото, документы или текст)
    evidence = ""
    
    if message.photo:
        evidence = f"Фото: {message.photo[-1].file_id}"
    elif message.document:
        evidence = f"Документ: {message.document.file_name} ({message.document.file_id})"
    elif message.text:
        evidence = f"Текст: {message.text}"
    else:
        evidence = "Доказательства не предоставлены"
    
    # Сохраняем жалобу в базу данных
    complaint_id = save_admin_complaint(
        user_id=message.from_user.id,
        username=username,
        admin_username=admin_username,
        description=description,
        complaint_text=complaint_text,
        evidence=evidence
    )
    
    # Формируем жалобу
    complaint_message = (
        "🚨 <b>НОВАЯ ЖАЛОБА НА АДМИНИСТРАТОРА</b>\n\n"
        f"🆔 <b>ID жалобы:</b> #{complaint_id}\n"
        f"👤 <b>Жалобщик:</b> {username}\n"
        f"👮 <b>Администратор:</b> {admin_username}\n"
        f"📋 <b>Тип жалобы:</b> {description}\n"
        f"📝 <b>Описание:</b>\n{complaint_text}\n"
        f"📎 <b>Доказательства:</b> {evidence}\n\n"
        f"🆔 <b>ID жалобщика:</b> {message.from_user.id}\n"
        f"⏰ <b>Время подачи:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    
    # Отправляем жалобу владельцам бота
    sent_count = 0
    for admin_id in ADMIN_IDS:
        try:
            # Если есть фото, отправляем его с подписью
            if message.photo:
                await bot.send_photo(
                    chat_id=admin_id,
                    photo=message.photo[-1].file_id,
                    caption=complaint_message,
                    parse_mode="HTML"
                )
            # Если есть документ, отправляем его с подписью
            elif message.document:
                await bot.send_document(
                    chat_id=admin_id,
                    document=message.document.file_id,
                    caption=complaint_message,
                    parse_mode="HTML"
                )
            else:
                await bot.send_message(
                    chat_id=admin_id,
                    text=complaint_message,
                    parse_mode="HTML"
                )
            sent_count += 1
        except Exception as e:
            logger.error(f"Не удалось отправить жалобу администратору {admin_id}: {e}")
    
    # Создаем клавиатуру для ответа на жалобу (только для владельцев)
    if sent_count > 0:
        keyboard = InlineKeyboardBuilder()
        keyboard.row(
            InlineKeyboardButton(
                text="📞 Связаться с жалобщиком", 
                callback_data=f"contact_complainant:{message.from_user.id}"
            )
        )
        keyboard.row(
            InlineKeyboardButton(
                text="📋 Просмотреть все жалобы", 
                callback_data="view_all_complaints"
            )
        )
        
        # Отправляем клавиатуру владельцам
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=f"💬 <b>Действия по жалобе #{complaint_id} от {username}</b>",
                    reply_markup=keyboard.as_markup(),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Не удалось отправить клавиатуру администратору {admin_id}: {e}")
    
    # Отправляем подтверждение пользователю
    if sent_count > 0:
        await message.answer(
            f"✅ Ваша жалоба на администратора {admin_username} успешно отправлена!\n\n"
            f"<b>Краткая информация:</b>\n"
            f"• ID жалобы: #{complaint_id}\n"
            f"• Тип: {description}\n"
            f"• Адресат: {admin_username}\n"
            f"• Время: {datetime.now().strftime('%H:%M %d.%m.%Y')}\n\n"
            f"<i>Обратная связь будет предоставлена в ближайшее время.</i>",
            parse_mode="HTML",
            reply_markup=get_main_keyboard()
        )
    else:
        await message.answer(
            "❌ К сожалению, не удалось отправить вашу жалобу. "
            "Пожалуйста, попробуйте позже или свяжитесь с владельцами напрямую.",
            parse_mode="HTML",
            reply_markup=get_main_keyboard()
        )
    
    # Завершаем состояние
    await state.clear()

@dp.callback_query(F.data.startswith("contact_complainant:"))
async def contact_complainant(callback: types.CallbackQuery):
    try:
        parts = callback.data.split(":")
        if len(parts) < 2:
            await callback.answer("❌ Ошибка в данных")
            return
        
        complainant_id = int(parts[1])
        admin_mention = await get_user_mention(callback.from_user.id)
        
        # Создаем клавиатуру для ответа
        keyboard = InlineKeyboardBuilder()
        keyboard.row(
            InlineKeyboardButton(
                text="💬 Ответить на жалобу", 
                url=f"tg://user?id={complainant_id}"
            )
        )
        
        await callback.message.answer(
            f"👤 <b>Контакт с жалобщиком</b>\n\n"
            f"Для связи с жалобщиком нажмите кнопку ниже:\n"
            f"🆔 ID пользователя: {complainant_id}",
            reply_markup=keyboard.as_markup(),
            parse_mode="HTML"
        )
        
        await callback.answer("✅ Информация отправлена")
        
    except Exception as e:
        logger.error(f"Ошибка при обработке контакта с жалобщиком: {e}")
        await callback.answer("❌ Ошибка при обработке")

@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    if message.chat.type != ChatType.PRIVATE:
        return
        
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("❌ Нечего отменять.", reply_markup=get_main_keyboard())
        return
    
    await state.clear()
    await message.answer(
        "✅ Процесс подачи жалобы отменен.",
        reply_markup=get_main_keyboard()
    )

@dp.callback_query(F.data == "view_all_complaints")
async def view_all_complaints(callback: types.CallbackQuery):
    """Показывает список всех активных жалоб"""
    if not await is_bot_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет прав для просмотра жалоб.")
        return
    
    complaints = get_active_complaints()
    complaints_count = len(complaints)
    
    text = f"📋 <b>Активные жалобы на администраторов</b>\n\n"
    text += f"📊 <b>Всего активных жалоб:</b> {complaints_count}\n\n"
    
    if complaints_count > 0:
        text += "<b>Список жалоб:</b>\n"
        for complaint in complaints[:10]:  # Показываем первые 10 жалоб
            # Безопасное извлечение данных с значениями по умолчанию
            complaint_id = complaint.get('id', 'N/A')
            username = complaint.get('username', 'Неизвестный')
            admin_username = complaint.get('admin_username', 'Неизвестный')
            
            text += f"• #{complaint_id} {username} → {admin_username}\n"
        
        if complaints_count > 10:
            text += f"\n... и еще {complaints_count - 10} жалоб"
    else:
        text += "🎉 На данный момент активных жалоб нет!"
    
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=get_complaints_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("view_complaint:"))
async def view_complaint(callback: types.CallbackQuery):
    """Показывает детали конкретной жалобы"""
    if not await is_bot_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет прав для просмотра жалоб.")
        return
    
    complaint_id = int(callback.data.split(":")[1])
    complaint = get_complaint_by_id(complaint_id)
    
    if not complaint:
        await callback.answer("❌ Жалоба не найдена.")
        return
    
    # Форматируем текст жалобы
    complaint_text = (
        f"🚨 <b>Жалоба #${complaint['id']}</b>\n\n"
        f"👤 <b>Жалобщик:</b> {complaint['username']}\n"
        f"🆔 <b>ID жалобщика:</b> {complaint['user_id']}\n"
        f"👮 <b>Администратор:</b> {complaint['admin_username']}\n"
        f"📋 <b>Тип жалобы:</b> {complaint['description']}\n"
        f"📝 <b>Описание:</b>\n{complaint['complaint_text']}\n"
        f"📎 <b>Доказательства:</b> {complaint['evidence'] or 'Не предоставлены'}\n"
        f"⏰ <b>Время подачи:</b> {complaint['created_at']}\n"
        f"📊 <b>Статус:</b> {complaint['status']}"
    )
    
    await callback.message.edit_text(
        complaint_text,
        parse_mode="HTML",
        reply_markup=get_complaint_actions_keyboard(complaint_id)
    )
    await callback.answer()

@dp.callback_query(F.data == "refresh_complaints")
async def refresh_complaints(callback: types.CallbackQuery):
    """Обновляет список жалоб"""
    if not await is_bot_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет прав для просмотра жалоб.")
        return
    
    complaints = get_active_complaints()
    complaints_count = len(complaints)
    
    text = f"📋 <b>Активные жалобы на администраторов</b>\n\n"
    text += f"📊 <b>Всего активных жалоб:</b> {complaints_count}\n\n"
    
    if complaints_count > 0:
        text += "<b>Список жалоб:</b>\n"
        for complaint in complaints[:10]:
            text += f"• #{complaint['id']} {complaint['username']} → {complaint['admin_username']}\n"
        
        if complaints_count > 10:
            text += f"\n... и еще {complaints_count - 10} жалоб"
    else:
        text += "🎉 На данный момент активных жалоб нет!"
    
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=get_complaints_keyboard()
    )
    await callback.answer("✅ Список обновлен")

@dp.callback_query(F.data.startswith("reject_complaint:"))
async def start_reject_complaint(callback: types.CallbackQuery, state: FSMContext):
    """Начинает процесс отклонения жалобы"""
    if not await is_bot_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет прав для обработки жалоб.")
        return
    
    complaint_id = int(callback.data.split(":")[1])
    complaint = get_complaint_by_id(complaint_id)
    
    if not complaint:
        await callback.answer("❌ Жалоба не найдена.")
        return
    
    await state.update_data(complaint_id=complaint_id)
    await callback.message.answer(
        "📝 <b>Отклонение жалобы</b>\n\n"
        f"Жалоба #${complaint_id} от {complaint['username']}\n"
        "Пожалуйста, укажите причину отклонения:",
        parse_mode="HTML"
    )
    await state.set_state(AdminComplaintStates.waiting_for_reject_reason)
    await callback.answer()

@dp.callback_query(F.data.startswith("approve_complaint:"))
async def start_approve_complaint(callback: types.CallbackQuery, state: FSMContext):
    """Начинает процесс принятия жалобы"""
    if not await is_bot_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет прав для обработки жалоб.")
        return
    
    complaint_id = int(callback.data.split(":")[1])
    complaint = get_complaint_by_id(complaint_id)
    
    if not complaint:
        await callback.answer("❌ Жалоба не найдена.")
        return
    
    await state.update_data(complaint_id=complaint_id)
    await callback.message.answer(
        "✅ <b>Принятие жалобы</b>\n\n"
        f"Жалоба #${complaint_id} от {complaint['username']}\n"
        "Опишите действия, которые будут выполнены по этой жалобе:",
        parse_mode="HTML"
    )
    await state.set_state(AdminComplaintStates.waiting_for_approve_actions)
    await callback.answer()

@dp.callback_query(F.data.startswith("warn_false_report:"))
async def start_warn_false_report(callback: types.CallbackQuery, state: FSMContext):
    """Начинает процесс выдачи бана за ложную жалобу"""
    if not await is_bot_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет прав для обработки жалоб.")
        return
    
    complaint_id = int(callback.data.split(":")[1])
    complaint = get_complaint_by_id(complaint_id)
    
    if not complaint:
        await callback.answer("❌ Жалоба не найдена.")
        return
    
    await state.update_data(complaint_id=complaint_id)
    await callback.message.answer(
        "🚫 <b>Блокировка в боте за ложную жалобу</b>\n\n"
        f"Жалоба #{complaint_id} от {complaint['username']}\n"
        "Укажите причину блокировки в боте за ложную жалобу:",
        parse_mode="HTML"
    )
    await state.set_state(AdminComplaintStates.waiting_for_false_report_reason)
    await callback.answer()

@dp.callback_query(F.data.startswith("warn_incorrect_report:"))
async def start_warn_incorrect_report(callback: types.CallbackQuery, state: FSMContext):
    """Начинает процесс выдачи предупреждения за некорректную жалобу"""
    if not await is_bot_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет прав для обработки жалоб.")
        return
    
    complaint_id = int(callback.data.split(":")[1])
    complaint = get_complaint_by_id(complaint_id)
    
    if not complaint:
        await callback.answer("❌ Жалоба не найдена.")
        return
    
    await state.update_data(complaint_id=complaint_id)
    await callback.message.answer(
        "⚠️ <b>Предупреждение за некорректную жалобу</b>\n\n"
        f"Жалоба #{complaint_id} от {complaint['username']}\n"
        "Укажите причину предупреждения за некорректную жалобу:",
        parse_mode="HTML"
    )
    await state.set_state(AdminComplaintStates.waiting_for_incorrect_report_reason)
    await callback.answer()

@dp.message(AdminComplaintStates.waiting_for_reject_reason)
async def process_reject_reason(message: Message, state: FSMContext):
    """Обрабатывает причину отклонения жалобы"""
    data = await state.get_data()
    complaint_id = data['complaint_id']
    complaint = get_complaint_by_id(complaint_id)
    
    if not complaint:
        await message.answer("❌ Жалоба не найдена.")
        await state.clear()
        return
    
    reason = message.text.strip()
    if len(reason) < 5:
        await message.answer("❌ Причина слишком короткая. Укажите более подробную причину:")
        return
    
    # Обновляем статус жалобы
    update_complaint_status(complaint_id, "rejected", message.from_user.id, reason)
    
    # Уведомляем жалобщика
    try:
        await bot.send_message(
            chat_id=complaint['user_id'],
            text=f"❌ <b>Ваша жалоба отклонена</b>\n\n"
                 f"🆔 <b>ID жалобы:</b> #{complaint_id}\n"
                 f"?? <b>Администратор:</b> {complaint['admin_username']}\n"
                 f"📝 <b>Причина отклонения:</b> {reason}\n\n"
                 f"<i>Если вы не согласны с решением, свяжитесь с владельцами.</i>",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить жалобщика {complaint['user_id']}: {e}")
    
    # Уведомляем администратора
    await message.answer(
        f"✅ <b>Жалоба отклонена</b>\n\n"
        f"🆔 <b>ID жалобы:</b> #{complaint_id}\n"
        f"👤 <b>Жалобщик:</b> {complaint['username']}\n"
        f"📝 <b>Причина:</b> {reason}",
        parse_mode="HTML",
        reply_markup=get_complaints_keyboard()
    )
    
    await state.clear()

@dp.message(AdminComplaintStates.waiting_for_approve_actions)
async def process_approve_actions(message: Message, state: FSMContext):
    """Обрабатывает действия по принятой жалобе"""
    data = await state.get_data()
    complaint_id = data['complaint_id']
    complaint = get_complaint_by_id(complaint_id)
    
    if not complaint:
        await message.answer("❌ Жалоба не найдена.")
        await state.clear()
        return
    
    actions = message.text.strip()
    if len(actions) < 5:
        await message.answer("❌ Описание действий слишком короткое. Укажите более подробные действия:")
        return
    
    # Обновляем статус жалобы
    update_complaint_status(complaint_id, "approved", message.from_user.id, actions)
    
    # Уведомляем жалобщика
    try:
        await bot.send_message(
            chat_id=complaint['user_id'],
            text=f"✅ <b>Ваша жалоба принята</b>\n\n"
                 f"🆔 <b>ID жалобы:</b> #{complaint_id}\n"
                 f"👮 <b>Администратор:</b> {complaint['admin_username']}\n"
                 f"📋 <b>Будут выполнены действия:</b> {actions}\n\n"
                 f"<i>Спасибо за вашу бдительность!</i>",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить жалобщика {complaint['user_id']}: {e}")
    
    # Уведомляем администратора
    await message.answer(
        f"✅ <b>Жалоба принята</b>\n\n"
        f"🆔 <b>ID жалобы:</b> #{complaint_id}\n"
        f"👤 <b>Жалобщик:</b> {complaint['username']}\n"
        f"📋 <b>Действия:</b> {actions}",
        parse_mode="HTML",
        reply_markup=get_complaints_keyboard()
    )
    
    await state.clear()

@dp.message(AdminComplaintStates.waiting_for_false_report_reason)
async def process_false_report_reason(message: Message, state: FSMContext):
    """Обрабатывает причину бана за ложную жалобу - БАН в ЛС с ботом"""
    data = await state.get_data()
    complaint_id = data['complaint_id']
    complaint = get_complaint_by_id(complaint_id)
    
    if not complaint:
        await message.answer("❌ Жалоба не найдена.")
        await state.clear()
        return
    
    reason = message.text.strip()
    if len(reason) < 5:
        await message.answer("❌ Причина слишком короткая. Укажите более подробную причину:")
        return
    
    # Обновляем статус жалобы
    update_complaint_status(complaint_id, "false_report", message.from_user.id, reason)
    
    # БЛОКИРУЕМ пользователя в боте за ложную жалобу
    block_user(complaint['user_id'], f"Ложная жалоба: {reason}", message.from_user.id)
    
    # Уведомляем жалобщика
    try:
        await bot.send_message(
            chat_id=complaint['user_id'],
            text=f"🚫 <b>Вы заблокированы в боте за ложную жалобу</b>\n\n"
                 f"🆔 <b>ID жалобы:</b> #{complaint_id}\n"
                 f"👮 <b>Администратор:</b> {complaint['admin_username']}\n"
                 f"📝 <b>Причина:</b> {reason}\n\n"
                 f"<i>Вы не можете использовать функции бота из-за ложной жалобы.</i>",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить жалобщика {complaint['user_id']}: {e}")
    
    # Уведомляем администратора
    await message.answer(
        f"🚫 <b>Пользователь забанен в боте за ложную жалобу</b>\n\n"
        f"🆔 <b>ID жалобы:</b> #{complaint_id}\n"
        f"👤 <b>Жалобщик:</b> {complaint['username']}\n"
        f"📝 <b>Причина:</b> {reason}\n"
        f"📊 <b>Статус:</b> ✅ Пользователь заблокирован в боте",
        parse_mode="HTML",
        reply_markup=get_complaints_keyboard()
    )
    
    await state.clear()

@dp.message(AdminComplaintStates.waiting_for_incorrect_report_reason)
async def process_incorrect_report_reason(message: Message, state: FSMContext):
    """Обрабатывает причину предупреждения за некорректную жалобу - ПРЕДУПРЕЖДЕНИЕ в боте"""
    data = await state.get_data()
    complaint_id = data['complaint_id']
    complaint = get_complaint_by_id(complaint_id)
    
    if not complaint:
        await message.answer("❌ Жалоба не найдена.")
        await state.clear()
        return
    
    reason = message.text.strip()
    if len(reason) < 5:
        await message.answer("❌ Причина слишком короткая. Укажите более подробную причину:")
        return
    
    # Обновляем статус жалобы
    update_complaint_status(complaint_id, "incorrect_report", message.from_user.id, reason)
    
    # Выдаем предупреждение в БОТЕ за некорректную жалобу
    add_bot_warn(complaint['user_id'], f"Некорректная жалоба: {reason}", message.from_user.id)
    
    # Получаем текущие предупреждения в боте
    bot_warns = get_bot_warns(complaint['user_id'])
    warn_count = len(bot_warns)
    
    # Проверяем, нужно ли блокировать пользователя в боте (3 или более предупреждений в боте)
    if warn_count >= 3:
        block_user(complaint['user_id'], "3 предупреждения в боте за некорректные жалобы", message.from_user.id)
        block_message = "\n\n🚫 <b>Пользователь заблокирован в боте за 3 предупреждения!</b>"
    else:
        block_message = f"\n\n📊 <b>Текущее количество предупреждений в боте:</b> {warn_count}/3"
    
    # Уведомляем жалобщика
    try:
        block_notice = "\n\n🚫 <b>Вы заблокированы в боте за 3 предупреждения!</b>" if warn_count >= 3 else ""
        await bot.send_message(
            chat_id=complaint['user_id'],
            text=f"⚠️ <b>Вам выдано предупреждение в боте за некорректную жалобу</b>\n\n"
                 f"?? <b>ID жалобы:</b> #{complaint_id}\n"
                 f"👮 <b>Администратор:</b> {complaint['admin_username']}\n"
                 f"📝 <b>Причина:</b> {reason}\n"
                 f"📊 <b>Всего предупреждений в боте:</b> {warn_count}/3{block_notice}\n\n"
                 f"<i>При получении 3 предупреждений в боте вы будете заблокированы.</i>",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить жалобщика {complaint['user_id']}: {e}")
    
    # Уведомляем администратора
    await message.answer(
        f"⚠️ <b>Предупреждение в боте за некорректную жалобу выдано</b>\n\n"
        f"🆔 <b>ID жалобы:</b> #{complaint_id}\n"
        f"👤 <b>Жалобщик:</b> {complaint['username']}\n"
        f"📝 <b>Причина:</b> {reason}\n"
        f"📊 <b>Статус:</b> ✅ Предупреждение выдано{block_message}",
        parse_mode="HTML",
        reply_markup=get_complaints_keyboard()
    )
    
    await state.clear()

@dp.callback_query(F.data == "no_complaints")
async def no_complaints(callback: types.CallbackQuery):
    """Обработчик для случая, когда жалоб нет"""
    await callback.answer("🎉 На данный момент активных жалоб нет!")

@dp.message(Command("complaints"))
async def cmd_complaints(message: Message):
    """Команда для просмотра жалоб (только для администраторов)"""
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("❌ Эта команда работает только в личных сообщениях с ботом.")
        return
    
    if not await is_bot_admin(message.from_user.id):
        await message.answer("❌ У вас нет прав для просмотра жалоб.")
        return
    
    complaints = get_active_complaints()
    complaints_count = len(complaints)
    
    text = f"📋 <b>Активные жалобы на администраторов</b>\n\n"
    text += f"📊 <b>Всего активных жалоб:</b> {complaints_count}\n\n"
    
    if complaints_count > 0:
        text += "<b>Список жалоб:</b>\n"
        for complaint in complaints[:10]:  # Показываем первые 10 жалоб
            text += f"• #{complaint['id']} {complaint['username']} → {complaint['admin_username']}\n"
        
        if complaints_count > 10:
            text += f"\n... и еще {complaints_count - 10} жалоб"
    else:
        text += "🎉 На данный момент активных жалоб нет!"
    
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=get_complaints_keyboard()
    )

@dp.message(Command("my_bot_warns"))
async def cmd_my_bot_warns(message: Message):
    """Команда для просмотра своих предупреждений в боте"""
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("❌ Эта команда работает только в личных сообщениях с ботом.")
        return
    
    bot_warns = get_bot_warns(message.from_user.id)
    
    if not bot_warns:
        await message.answer("✅ У вас нет активных предупреждений в боте.")
        return
    
    warns_text = "\n".join(
        [
            f"• {warn['reason']} ({warn['issued_at']})"
            for warn in bot_warns
        ]
    )

    await message.answer(
        f"⚠️ <b>Ваши предупреждения в боте</b>\n\n"
        f"📊 <b>Всего предупреждений:</b> {len(bot_warns)}/3\n\n"
        f"{warns_text}\n\n"
        f"<i>При получении 3 предупреждений вы будете заблокированы в боте.</i>",
        parse_mode="HTML",
    )

@dp.message(Command("unblock"))
async def cmd_unblock(message: Message, command: CommandObject):
    """Команда для разблокировки пользователя в боте"""
    if not await is_owner(message.from_user.id):
        await message.answer("❌ Только владелец может разблокировать пользователей.")
        return
    
    if not command.args:
        await message.answer("❌ Укажите пользователя: /unblock @username")
        return
    
    user_id = await get_user_id_from_message(command.args)
    if not user_id:
        await message.answer("❌ Пользователь не найден.")
        return
    
    unblock_user(user_id)
    user_mention = await get_user_mention(user_id)
    
    await message.answer(
        f"✅ Пользователь {user_mention} разблокирован в боте.",
        parse_mode="HTML"
    )

# Обработчики сообщений
@dp.message(F.text)
async def handle_message(message: Message):
    # Пропускаем сообщения от ботов и в личных чатах
    if message.from_user.is_bot or message.chat.type == ChatType.PRIVATE:
        return

    # Проверяем, что сообщение из нужного чата
    if message.chat.id != CHAT_ID:
        return

    user_id = message.from_user.id
    text = message.text or message.caption or ""
    text_lower = text.lower()

    # Проверка на запрещенные слова (УСВ) - исправленная логика
    has_trigger_word = False
    found_word = None
    
    for trigger_word, variants in TRIGGER_WORDS.items():
        for variant in variants:
            # Ищем вхождение варианта как отдельного слова или части слова
            # Используем поиск подстроки с учетом границ слов
            pattern = r'\b' + re.escape(variant) + r'\w*'
            if re.search(pattern, text_lower):
                has_trigger_word = True
                found_word = variant
                logger.info(f"Найдено триггерное слово '{variant}' в сообщении: {text}")
                break
        if has_trigger_word:
            break
    
    if has_trigger_word:
        # Удаляем сообщение с запрещенным словом
        await delete_message(message.chat.id, message.message_id)
        
        # Выдаем предупреждение пользователю за УСВ
        await warn_user(message.chat.id, user_id, "УСВ (запрещенное слово)")
        
        # Отправляем предупреждающее сообщение
        warning_msg = await message.answer(
            f"⚠️ <b>Пользователь {await get_user_mention(user_id)} получил предупреждение за использование запрещенного слова (УСВ)</b>\n\n"
            f"<i>Использование запрещенных слов не допускается!</i>",
            parse_mode="HTML"
        )
        
        # Удаляем предупреждение через 10 секунд
        await asyncio.sleep(10)
        await delete_message(message.chat.id, warning_msg.message_id)
        return

    # Автоматическое удаление команд бота от обычных пользователей
    if text.startswith('/'):
        # Список команд этого бота для которых нужно наказывать
        bot_commands = [
            '/warn', '/warns', '/clearwarns', '/mute', '/tmute', '/unmute', 
            '/ban', '/tban', '/unban', '/cc', '/admin_add', '/admin_remove',
            '/admin_list', '/admin_warn', '/awarn', '/admin_unwarn', 
            '/admin_warns', '/check_admin', '/ban_info', '/stats',
            '/complaints', '/unblock'
        ]
        
        # Проверяем, является ли команда командой этого бота
        command_parts = text.split()
        command_name = command_parts[0].lower()  # Получаем имя команды
        
        if command_name in bot_commands and not await is_chat_admin(user_id, message.chat.id):
            await delete_message(message.chat.id, message.message_id)
            
            # Мут за использование команд бота
            await mute_user(
                message.chat.id,
                user_id,
                MUTE_DURATION,
                "Использование команд бота",
                is_auto=True
            )

# Обработчик новых участников
@dp.chat_member()
async def handle_chat_member_update(update: ChatMemberUpdated):
    if update.chat.id != CHAT_ID:
        return

    old_status = update.old_chat_member.status
    new_status = update.new_chat_member.status

    # Новый участник присоединился
    if (old_status == ChatMemberStatus.LEFT and 
        new_status in [ChatMemberStatus.MEMBER, ChatMemberStatus.RESTRICTED]):
        
        user_id = update.new_chat_member.user.id
        
        # Отправляем приветственное сообщение
        welcome_text = f"""
        👋 <b>Добро пожаловать в чат, {await get_user_mention(user_id)}!</b>

        📜 <b>Обязательно ознакомьтесь с правилами!</b>
        🎉 <b>Приятного общения!</b>
        """
        
        welcome_msg = await update.chat.send_message(welcome_text, parse_mode="HTML")
        
        # Удаляем приветствие через минуту
        await asyncio.sleep(60)
        await delete_message(update.chat.id, welcome_msg.message_id)

# Функция для очистки устаревших данных
async def cleanup_expired_data():
    while True:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            # Используем московское время для очистки
            current_time = get_moscow_time()
            
            # Очищаем истекшие варны
            cursor.execute("DELETE FROM warns WHERE expires_at <= ?", (current_time,))
            
            # Деактивируем истекшие муты
            cursor.execute(
                "UPDATE mutes SET is_active = FALSE WHERE expires_at <= ? AND is_active = TRUE",
                (current_time,)
            )
            
            # Деактивируем истекшие баны
            cursor.execute(
                "UPDATE bans SET is_active = FALSE WHERE expires_at <= ? AND is_active = TRUE",
                (current_time,)
            )
            
            conn.commit()
            conn.close()
            
            logger.info("Очистка устаревших данных выполнена")
            await asyncio.sleep(3600)  # Каждый час
        except Exception as e:
            logger.error(f"Ошибка при очистке данных: {e}")
            await asyncio.sleep(300)

# Основная функция
async def main():
    logger.info("Запуск бота...")
    
    # Запускаем фоновые задачи
    asyncio.create_task(cleanup_expired_data())
    
    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())