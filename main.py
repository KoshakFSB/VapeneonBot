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
    CallbackQuery,
    ChatAdministratorRights,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
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

# Новые состояния для отзывов
class ReviewStates(StatesGroup):
    waiting_for_rating = State()
    waiting_for_review_text = State()
    review_target_user = State()

# Состояния для онбординга новых участников
class OnboardingStates(StatesGroup):
    step_1_welcome = State()      # Приветствие
    step_2_rules = State()        # Правила
    step_3_rules_quiz = State()   # Проверка знания правил
    step_4_order = State()        # Как сделать заказ
    step_6_complaints = State()   # Как подавать жалобы
    step_7_final_quiz = State()   # Финальный тест


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

def format_moscow_time(db_time_str):
    """Конвертирует время из БД (UTC) в московское время и форматирует"""
    if not db_time_str:
        return "не указано"
    
    try:
        # Парсим время из БД (оно в UTC)
        utc_time = datetime.fromisoformat(db_time_str.replace('Z', '+00:00'))
        
        # Если время без таймзоны, добавляем UTC
        if utc_time.tzinfo is None:
            utc_time = pytz.UTC.localize(utc_time)
        
        # Конвертируем в московское время
        moscow_time = utc_time.astimezone(MOSCOW_TZ)
        
        # Форматируем
        return moscow_time.strftime('%d.%m.%Y %H:%M:%S')
    except Exception as e:
        logger.error(f"Ошибка конвертации времени {db_time_str}: {e}")
        return db_time_str

# Ограничения
MAX_ADS_PER_DAY = int(os.getenv("MAX_ADS_PER_DAY", "5"))
MIN_AD_INTERVAL_HOURS = float(os.getenv("MIN_AD_INTERVAL_HOURS", "1.5"))
MUTE_DURATION_DAYS = int(os.getenv("MUTE_DURATION_DAYS", "1"))

MIN_AD_INTERVAL = timedelta(hours=MIN_AD_INTERVAL_HOURS)
MUTE_DURATION = timedelta(days=MUTE_DURATION_DAYS)

# ID чата для журнала модерации
MOD_LOG_CHAT_ID = -1003838979861

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
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        role TEXT DEFAULT 'moderator',
        display_name TEXT
    )"""
    )
    
    # Миграция: добавляем колонки role и display_name если их нет
    try:
        cursor.execute("ALTER TABLE admins ADD COLUMN role TEXT DEFAULT 'moderator'")
    except sqlite3.OperationalError:
        pass  # Колонка уже существует
    try:
        cursor.execute("ALTER TABLE admins ADD COLUMN display_name TEXT")
    except sqlite3.OperationalError:
        pass  # Колонка уже существует

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





    # Таблица для отзывов о пользователях
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS user_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_user_id INTEGER NOT NULL,
        to_user_id INTEGER NOT NULL,
        rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
        review_text TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )"""
    )

    # Таблица для онбординга новых участников
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS onboarding (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL UNIQUE,
        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        completed_at TIMESTAMP,
        is_completed BOOLEAN DEFAULT FALSE,
        current_step INTEGER DEFAULT 1,
        failed_at TIMESTAMP,
        fail_count INTEGER DEFAULT 0
    )"""
    )
    # Миграция: добавляем колонки если их ещё нет (для существующих БД)
    try:
        cursor.execute("ALTER TABLE onboarding ADD COLUMN failed_at TIMESTAMP")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE onboarding ADD COLUMN fail_count INTEGER DEFAULT 0")
    except Exception:
        pass

    # Индексы для улучшения производительности
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_warns_user_chat ON warns(user_id, chat_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_warns_expires ON warns(expires_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_mutes_user_chat ON mutes(user_id, chat_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_mutes_expires ON mutes(expires_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bans_user_chat ON bans(user_id, chat_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bans_expires ON bans(expires_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_ads_user_date ON user_ads(user_id, sent_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_admin_complaints_status ON admin_complaints(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_admin_complaints_user ON admin_complaints(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_admin_complaints_created ON admin_complaints(created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bot_blocks_user ON bot_blocks(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bot_blocks_active ON bot_blocks(is_active)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bot_warns_user ON bot_warns(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bot_warns_active ON bot_warns(is_active)")
    
    
    # Новые индексы
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_reviews_to_user ON user_reviews(to_user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_reviews_from_user ON user_reviews(from_user_id)")

    conn.commit()
    conn.close()

init_db()

async def notify_incomplete_onboarding():
    """
    При запуске бота рассылает напоминание об обучении всем пользователям,
    у которых онбординг начат, но не завершён.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id FROM onboarding WHERE is_completed = FALSE AND failed_at IS NULL"
        )
        users = cursor.fetchall()
        conn.close()

        if not users:
            logger.info("Напоминания об обучении: нет пользователей, требующих напоминания.")
            return

        logger.info(f"Напоминания об обучении: рассылаем {len(users)} пользователям...")
        sent = 0
        failed = 0
        bot_info = await bot.get_me()

        for (user_id,) in users:
            try:
                remaining = get_onboarding_cooldown_remaining(user_id)
                if remaining:
                    # Пользователь на кулдауне — пропускаем
                    continue

                builder = InlineKeyboardBuilder()
                builder.row(InlineKeyboardButton(text="✅ Пройти обучение сейчас", callback_data="onb_start"))
                builder.row(InlineKeyboardButton(text="⏳ Позже", callback_data="onb_later"))

                await bot.send_message(
                    user_id,
                    "👋 <b>Напоминание об обучении!</b>\n\n"
                    "Ты ещё не прошёл обязательное обучение для участников чата. "
                    "Без него ты не сможешь писать сообщения.\n\n"
                    "Это займёт всего <b>2-3 минуты</b> — пройди сейчас, чтобы начать общаться! 🚀",
                    parse_mode="HTML",
                    reply_markup=builder.as_markup()
                )
                sent += 1
                await asyncio.sleep(0.1)  # небольшая пауза чтобы не превысить лимиты
            except Exception as e:
                logger.warning(f"Не удалось отправить напоминание пользователю {user_id}: {e}")
                failed += 1

        logger.info(f"Напоминания об обучении: отправлено {sent}, ошибок {failed}.")
    except Exception as e:
        logger.error(f"Ошибка в notify_incomplete_onboarding: {e}")


async def mark_existing_members_as_onboarded():
    """
    При первом запуске бота помечает всех текущих участников как прошедших онбординг.
    Это гарантирует, что люди, которые уже были в чате, не получат обучение при следующем входе.
    Запускается только один раз — если таблица onboarding пустая.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM onboarding")
        count = cursor.fetchone()[0]
        conn.close()

        # Если записи уже есть — инициализация уже была, пропускаем
        if count > 0:
            logger.info("Онбординг: записи уже существуют, инициализация не нужна.")
            return

        logger.info("Онбординг: первый запуск, помечаем администраторов как прошедших обучение...")

        conn = get_db_connection()
        cursor = conn.cursor()
        marked = 0
        now = datetime.now()

        try:
            admins = await bot.get_chat_administrators(CHAT_ID)
            for admin in admins:
                if not admin.user.is_bot:
                    cursor.execute(
                        """INSERT OR IGNORE INTO onboarding
                        (user_id, started_at, completed_at, is_completed, current_step)
                        VALUES (?, ?, ?, TRUE, 7)""",
                        (admin.user.id, now, now)
                    )
                    marked += 1
        except Exception as e:
            logger.error(f"Ошибка получения администраторов для онбординга: {e}")

        # Также помечаем всех ADMIN_IDS как прошедших
        for admin_id in ADMIN_IDS:
            cursor.execute(
                """INSERT OR IGNORE INTO onboarding
                (user_id, started_at, completed_at, is_completed, current_step)
                VALUES (?, ?, ?, TRUE, 7)""",
                (admin_id, now, now)
            )
            marked += 1

        conn.commit()
        conn.close()
        logger.info(f"Онбординг: помечено {marked} пользователей как прошедших обучение.")

    except Exception as e:
        logger.error(f"Ошибка в mark_existing_members_as_onboarded: {e}")


async def restore_active_punishments():
    """Восстанавливает активные наказания при запуске бота"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        current_time = datetime.now()
        
        # Восстанавливаем активные муты
        cursor.execute("""
            SELECT user_id, chat_id, expires_at, reason 
            FROM mutes 
            WHERE is_active = TRUE AND (expires_at IS NULL OR expires_at > ?)
        """, (current_time,))
        
        active_mutes = cursor.fetchall()
        restored_count = 0
        
        for user_id, chat_id, expires_at, reason in active_mutes:
            try:
                # Проверяем, не является ли пользователь администратором
                member = await bot.get_chat_member(chat_id, user_id)
                if member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                    # Если администратор - деактивируем мут
                    cursor.execute(
                        "UPDATE mutes SET is_active = FALSE WHERE user_id = ? AND chat_id = ?",
                        (user_id, chat_id)
                    )
                    logger.info(f"Мут администратора {user_id} деактивирован")
                    continue
                
                until_date = datetime.fromisoformat(expires_at) if expires_at else None
                
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
                
                await bot.restrict_chat_member(chat_id, user_id, permissions, until_date=until_date)
                restored_count += 1
                logger.info(f"Восстановлен мут для пользователя {user_id} в чате {chat_id}")
                
            except Exception as e:
                logger.error(f"Ошибка восстановления мута для {user_id}: {e}")
                # Если не удалось восстановить - возможно пользователь уже не в чате
                cursor.execute(
                    "UPDATE mutes SET is_active = FALSE WHERE user_id = ? AND chat_id = ?",
                    (user_id, chat_id)
                )
        
        # Восстанавливаем активные баны
        cursor.execute("""
            SELECT user_id, chat_id, expires_at, reason 
            FROM bans 
            WHERE is_active = TRUE AND (expires_at IS NULL OR expires_at > ?)
        """, (current_time,))
        
        active_bans = cursor.fetchall()
        restored_bans = 0
        
        for user_id, chat_id, expires_at, reason in active_bans:
            try:
                until_date = datetime.fromisoformat(expires_at) if expires_at else None
                await bot.ban_chat_member(chat_id, user_id, until_date=until_date)
                restored_bans += 1
                logger.info(f"Восстановлен бан для пользователя {user_id} в чате {chat_id}")
            except Exception as e:
                logger.error(f"Ошибка восстановления бана для {user_id}: {e}")
                cursor.execute(
                    "UPDATE bans SET is_active = FALSE WHERE user_id = ? AND chat_id = ?",
                    (user_id, chat_id)
                )
        
        conn.commit()
        conn.close()
        
        logger.info(f"Восстановлено наказаний: {restored_count} мутов, {restored_bans} банов")
        
    except Exception as e:
        logger.error(f"Ошибка восстановления наказаний: {e}")

async def monitor_expired_punishments():
    """Мониторит и автоматически снимает истекшие наказания"""
    while True:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            current_time = datetime.now()
            
            # Находим истекшие муты
            cursor.execute("""
                SELECT user_id, chat_id, id 
                FROM mutes 
                WHERE is_active = TRUE AND expires_at <= ?
            """, (current_time,))
            
            expired_mutes = cursor.fetchall()
            
            for user_id, chat_id, mute_id in expired_mutes:
                try:
                    # Снимаем мут
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
                    
                    # Деактивируем в БД
                    cursor.execute(
                        "UPDATE mutes SET is_active = FALSE WHERE id = ?",
                        (mute_id,)
                    )
                    
                    logger.info(f"Автоматически снят мут с пользователя {user_id}")
                    
                except Exception as e:
                    logger.error(f"Ошибка снятия мута {mute_id}: {e}")
            
            # Находим истекшие баны
            cursor.execute("""
                SELECT user_id, chat_id, id 
                FROM bans 
                WHERE is_active = TRUE AND expires_at <= ?
            """, (current_time,))
            
            expired_bans = cursor.fetchall()
            
            for user_id, chat_id, ban_id in expired_bans:
                try:
                    # Разбаниваем
                    await bot.unban_chat_member(chat_id, user_id)
                    
                    # Деактивируем в БД
                    cursor.execute(
                        "UPDATE bans SET is_active = FALSE WHERE id = ?",
                        (ban_id,)
                    )
                    
                    logger.info(f"Автоматически снят бан с пользователя {user_id}")
                    
                except Exception as e:
                    logger.error(f"Ошибка снятия бана {ban_id}: {e}")
            
            # Находим истекшие блокировки объявлений
            cursor.execute("""
                UPDATE ad_blocks 
                SET is_active = FALSE 
                WHERE is_active = TRUE AND expires_at <= ?
            """, (current_time,))
            
            conn.commit()
            conn.close()
            
            await asyncio.sleep(60)  # Проверяем каждую минуту
            
        except Exception as e:
            logger.error(f"Ошибка в мониторинге наказаний: {e}")
            await asyncio.sleep(60)

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

# Режим технических работ
MAINTENANCE_MODE = False

MAINTENANCE_MESSAGE = (
    "🔧 <b>Бот на технических работах</b>\n\n"
    "Приносим извинения за неудобства. Пожалуйста, попробуйте позже."
)

from aiogram import BaseMiddleware
from typing import Callable, Awaitable
from aiogram.types import TelegramObject, Update

class MaintenanceMiddleware(BaseMiddleware):
    """Middleware: блокирует все ЛС-обращения в режиме тех.работ для не-владельцев."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict], Awaitable[Any]],
        event: TelegramObject,
        data: dict,
    ) -> Any:
        if not MAINTENANCE_MODE:
            return await handler(event, data)

        # Определяем пользователя и сообщение из события
        from aiogram.types import CallbackQuery
        user_id = None
        reply_message: Optional[Message] = None

        if isinstance(event, Message):
            if event.chat.type != ChatType.PRIVATE:
                return await handler(event, data)
            user_id = event.from_user.id if event.from_user else None
            reply_message = event
        elif isinstance(event, CallbackQuery):
            if not event.message or event.message.chat.type != ChatType.PRIVATE:
                return await handler(event, data)
            user_id = event.from_user.id if event.from_user else None
            reply_message = event.message
        else:
            return await handler(event, data)

        # Владельцы проходят без ограничений
        if user_id and user_id in ADMIN_IDS:
            return await handler(event, data)

        # Для всех остальных — сообщение о тех.работах
        if reply_message:
            await reply_message.answer(MAINTENANCE_MESSAGE, parse_mode="HTML")
        return  # Не передаём управление дальше

# Добавьте эту строку для получения username бота
bot_username = os.getenv("BOT_USERNAME", "TheVapeNeonBot")

# После инициализации бота
async def set_bot_username():
    bot_info = await bot.get_me()
    bot.username = bot_info.username

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
        "INSERT INTO mutes (user_id, chat_id, reason, issued_by, expires_at, is_active) VALUES (?, ?, ?, ?, ?, 1)",
        (user_id, chat_id, reason, issued_by, expires_at),
    )
    conn.commit()
    conn.close()

def add_ban(user_id: int, chat_id: int, reason: str, issued_by: int, duration: timedelta = None):
    conn = get_db_connection()
    cursor = conn.cursor()
    expires_at = datetime.now() + duration if duration else None
    cursor.execute(
        "INSERT INTO bans (user_id, chat_id, reason, issued_by, expires_at, is_active) VALUES (?, ?, ?, ?, ?, 1)",
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

# Функции для работы с блокировками объявлений
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


# ============================================================
#         РОЛИ АДМИНИСТРАТОРОВ
# Уровни:
#   junior_moderator  — мл. модератор   : только муты
#   moderator         — модератор       : муты + варны
#   senior_moderator  — ст. модератор   : муты + варны + баны
#   admin             — администратор   : муты + варны + баны + adblock
# ============================================================

ADMIN_ROLES = {
    "junior_moderator": {
        "label": "Мл. модератор",
        "emoji": "🟢",
        "can_mute": True,
        "can_warn": False,
        "can_ban": False,
        "can_adblock": False,
        # Права в группе Telegram
        "tg_rights": {
            "can_manage_chat": False,
            "can_delete_messages": True,
            "can_manage_video_chats": False,
            "can_restrict_members": True,   # нужно для мута
            "can_promote_members": False,
            "can_change_info": False,
            "can_invite_users": True,
            "can_post_messages": True,
            "can_edit_messages": False,
            "can_pin_messages": False,
            "can_manage_topics": False,
            "is_anonymous": False,
        }
    },
    "moderator": {
        "label": "Модератор",
        "emoji": "🔵",
        "can_mute": True,
        "can_warn": True,
        "can_ban": False,
        "can_adblock": False,
        "tg_rights": {
            "can_manage_chat": False,
            "can_delete_messages": True,
            "can_manage_video_chats": False,
            "can_restrict_members": True,
            "can_promote_members": False,
            "can_change_info": False,
            "can_invite_users": True,
            "can_post_messages": True,
            "can_edit_messages": False,
            "can_pin_messages": False,
            "can_manage_topics": False,
            "is_anonymous": False,
        }
    },
    "senior_moderator": {
        "label": "Ст. модератор",
        "emoji": "🟠",
        "can_mute": True,
        "can_warn": True,
        "can_ban": True,
        "can_adblock": False,
        "tg_rights": {
            "can_manage_chat": True,
            "can_delete_messages": True,
            "can_manage_video_chats": False,
            "can_restrict_members": True,
            "can_promote_members": False,
            "can_change_info": False,
            "can_invite_users": True,
            "can_post_messages": True,
            "can_edit_messages": True,
            "can_pin_messages": True,
            "can_manage_topics": False,
            "is_anonymous": False,
        }
    },
    "admin": {
        "label": "Администратор",
        "emoji": "🔴",
        "can_mute": True,
        "can_warn": True,
        "can_ban": True,
        "can_adblock": True,
        "tg_rights": {
            "can_manage_chat": True,
            "can_delete_messages": True,
            "can_manage_video_chats": True,
            "can_restrict_members": True,
            "can_promote_members": False,
            "can_change_info": True,
            "can_invite_users": True,
            "can_post_messages": True,
            "can_edit_messages": True,
            "can_pin_messages": True,
            "can_manage_topics": True,
            "is_anonymous": False,
        }
    },
}

def add_admin(user_id: int, added_by: int, role: str = "moderator", display_name: str = None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO admins (user_id, added_by, role, display_name) VALUES (?, ?, ?, ?)",
        (user_id, added_by, role, display_name),
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

def get_admin_role(user_id: int) -> Optional[str]:
    """Возвращает роль администратора или None если не администратор БД"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT role FROM admins WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def get_admin_info(user_id: int) -> Optional[Dict[str, Any]]:
    """Возвращает полную информацию об администраторе"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, role, display_name, added_at FROM admins WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    if result:
        return {"user_id": result[0], "role": result[1], "display_name": result[2], "added_at": result[3]}
    return None

def get_all_admins() -> List[int]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM admins")
    admins = [row[0] for row in cursor.fetchall()]
    conn.close()
    return admins

def get_all_admins_with_info() -> List[Dict[str, Any]]:
    """Возвращает список всех администраторов с их ролями"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, role, display_name, added_at FROM admins ORDER BY added_at")
    rows = cursor.fetchall()
    conn.close()
    return [{"user_id": r[0], "role": r[1] or "moderator", "display_name": r[2], "added_at": r[3]} for r in rows]

def admin_can(user_id: int, permission: str) -> bool:
    """Проверяет, есть ли у администратора конкретное право по роли.
    Если user_id в ADMIN_IDS — разрешено всё.
    permission: 'can_mute', 'can_warn', 'can_ban', 'can_adblock'
    """
    if user_id in ADMIN_IDS:
        return True
    role = get_admin_role(user_id)
    if not role:
        return False
    role_data = ADMIN_ROLES.get(role, ADMIN_ROLES["moderator"])
    return role_data.get(permission, False)

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

# Функции для работы с отзывами о пользователях
def add_user_review(from_user_id: int, to_user_id: int, rating: int, review_text: str) -> int:
    """Добавляет отзыв о пользователе"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute(
        "INSERT INTO user_reviews (from_user_id, to_user_id, rating, review_text) VALUES (?, ?, ?, ?)",
        (from_user_id, to_user_id, rating, review_text)
    )
    review_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return review_id

def get_user_reviews(user_id: int) -> List[Dict[str, Any]]:
    """Получает все отзывы о пользователе"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute(
        """SELECT id, from_user_id, rating, review_text, created_at 
           FROM user_reviews 
           WHERE to_user_id = ? 
           ORDER BY created_at DESC""",
        (user_id,)
    )
    
    reviews = []
    for row in cursor.fetchall():
        reviews.append({
            "id": row[0],
            "from_user_id": row[1],
            "rating": row[2],
            "review_text": row[3],
            "created_at": row[4]
        })
    
    conn.close()
    return reviews

def get_user_rating_stats(user_id: int) -> Tuple[float, int]:
    """Получает средний рейтинг и количество отзывов пользователя"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT AVG(rating), COUNT(*) FROM user_reviews WHERE to_user_id = ?",
        (user_id,)
    )
    row = cursor.fetchone()
    conn.close()
    
    avg_rating = row[0] if row[0] else 0
    review_count = row[1] if row[1] else 0
    
    return round(avg_rating, 1), review_count

def get_user_review_from_user(from_user_id: int, to_user_id: int) -> Optional[Dict[str, Any]]:
    """Проверяет, оставлял ли пользователь отзыв о другом пользователе"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT id, rating, review_text, created_at FROM user_reviews WHERE from_user_id = ? AND to_user_id = ?",
        (from_user_id, to_user_id)
    )
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            "id": row[0],
            "rating": row[1],
            "review_text": row[2],
            "created_at": row[3]
        }
    return None

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

# Клавиатуры для объявлений
def get_public_menu_keyboard():
    """Главное меню публикаций"""
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="👤 Мой профиль", callback_data="my_profile"))
    return keyboard.as_markup()

def get_user_reviews_keyboard(user_id: int, viewer_id: int):
    """Клавиатура для отзывов пользователя"""
    keyboard = InlineKeyboardBuilder()
    
    # Проверяем, оставлял ли уже пользователь отзыв
    existing_review = get_user_review_from_user(viewer_id, user_id)
    
    if not existing_review and viewer_id != user_id:
        keyboard.row(InlineKeyboardButton(text="✏️ Оставить отзыв", callback_data=f"leave_review:{user_id}"))
    
    # Кнопка "Назад" ведет в зависимости от контекста
    keyboard.row(InlineKeyboardButton(text="◀️ Назад", callback_data=f"back_from_reviews:{user_id}"))
    
    return keyboard.as_markup()

def get_rating_keyboard():
    """Клавиатура для выбора оценки"""
    keyboard = InlineKeyboardBuilder()
    keyboard.row(
        InlineKeyboardButton(text="1⭐", callback_data="rating:1"),
        InlineKeyboardButton(text="2⭐", callback_data="rating:2"),
        InlineKeyboardButton(text="3⭐", callback_data="rating:3")
    )
    keyboard.row(
        InlineKeyboardButton(text="4⭐", callback_data="rating:4"),
        InlineKeyboardButton(text="5⭐", callback_data="rating:5")
    )
    keyboard.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_review"))
    return keyboard.as_markup()

# Вспомогательные функции
async def get_user_mention(user_id: int) -> str:
    try:
        user = await bot.get_chat(user_id)
        name = user.first_name or user.username or str(user_id)
        # Экранируем HTML-спецсимволы в имени, чтобы не ломать parse_mode="HTML"
        name = name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
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
    """
    Парсит строку времени в timedelta
    Поддерживает форматы: 30m, 2h, 1d, 1w
    """
    if not time_str:
        return None

    time_str = time_str.lower().strip()
    # Убираем возможные пробелы между числом и единицей
    time_str = re.sub(r'\s+', '', time_str)
    
    # Более гибкая проверка формата
    match = re.match(r"^(\d+)([mhdw])$", time_str)
    if not match:
        # Пробуем другой формат: число и единица через пробел
        match = re.match(r"^(\d+)\s+([mhdw])$", time_str)
        if not match:
            return None

    num = int(match.group(1))
    unit = match.group(2)

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
    """
    Получает ID пользователя из текста (ID или @username)
    """
    if not text:
        return None

    # Убираем @ если есть
    text = text.strip().lstrip('@')
    
    # Проверяем, является ли это числом (ID)
    if text.isdigit():
        return int(text)
    
    # Иначе пробуем получить по юзернейму
    try:
        # Добавляем @ обратно для поиска
        username = f"@{text}"
        user = await bot.get_chat(username)
        return user.id
    except Exception as e:
        logger.error(f"Ошибка получения пользователя по юзернейму @{text}: {e}")
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
async def mute_user(chat_id: int, user_id: int, duration: timedelta = None, reason: str = None, is_auto: bool = False, message_thread_id: int = None) -> bool:
    try:
        # restrict_chat_member доступен только для супергрупп
        try:
            chat = await bot.get_chat(chat_id)
            if chat.type not in ("supergroup", "channel"):
                logger.error(f"Ошибка мута: чат {chat_id} не является супергруппой (тип: {chat.type})")
                return False
        except Exception as e:
            logger.error(f"Ошибка получения информации о чате {chat_id}: {e}")
            return False

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

        # Деактивируем предыдущие активные муты
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE mutes SET is_active = FALSE WHERE user_id = ? AND chat_id = ? AND is_active = TRUE",
            (user_id, chat_id)
        )
        conn.commit()
        conn.close()

        # Добавляем новый мут
        add_mute(user_id, chat_id, reason, 0 if is_auto else chat_id, duration)

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

        mute_appeal_builder = InlineKeyboardBuilder()
        if not is_auto:
            mute_appeal_builder.row(InlineKeyboardButton(
                text="⚖️ Обжаловать",
                url=f"https://t.me/{bot_username}?start=appeal_mute_{user_id}"
            ))
        await bot.send_message(
            chat_id,
            message_text,
            parse_mode="HTML",
            message_thread_id=message_thread_id,
            reply_markup=mute_appeal_builder.as_markup() if not is_auto else None
        )

        # Журнал модерации
        log_action = "🤖 Авто-мут" if is_auto else "🔇 Мут"
        await send_to_mod_log(
            f"{log_action}\n\n"
            f"👤 <b>Пользователь:</b> {user_mention}\n"
            f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
            f"⏳ <b>Срок:</b> {duration_str}"
            + (f"\n📝 <b>Причина:</b> {reason}" if reason else "") +
            f"\n🕐 <b>Время:</b> {get_moscow_time().strftime('%d.%m.%Y %H:%M:%S')}"
        )

        return True
    except Exception as e:
        logger.error(f"Ошибка мута: {e}")
        return False

async def unmute_user(chat_id: int, user_id: int, message_thread_id: int = None) -> bool:
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

        user_mention = await get_user_mention(user_id)
        await bot.send_message(
            chat_id,
            f"✅ <b>Размут пользователя</b>\n\n"
            f"👤 <b>Пользователь:</b> {user_mention}\n"
            f"🔊 <b>Статус:</b> Мут снят, пользователь может писать",
            parse_mode="HTML",
            message_thread_id=message_thread_id
        )

        # Журнал модерации
        await send_to_mod_log(
            f"🔊 Размут\n\n"
            f"👤 <b>Пользователь:</b> {user_mention}\n"
            f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
            f"🕐 <b>Время:</b> {get_moscow_time().strftime('%d.%m.%Y %H:%M:%S')}"
        )

        return True
    except Exception as e:
        logger.error(f"Ошибка размута: {e}")
        return False

async def ban_user(chat_id: int, user_id: int, duration: timedelta = None, reason: str = None, message_thread_id: int = None) -> bool:
    try:
        until_date = datetime.now() + duration if duration else None
        await bot.ban_chat_member(chat_id, user_id, until_date=until_date)

        duration_str = await format_duration(duration)
        reason_str = f"\n📝 <b>Причина:</b> {reason}" if reason else ""
        user_mention = await get_user_mention(user_id)

        # Деактивируем предыдущие активные баны
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE bans SET is_active = FALSE WHERE user_id = ? AND chat_id = ? AND is_active = TRUE",
            (user_id, chat_id)
        )
        conn.commit()
        conn.close()

        # Добавляем новый бан
        add_ban(user_id, chat_id, reason, chat_id, duration)

        ban_appeal_builder = InlineKeyboardBuilder()
        ban_appeal_builder.row(InlineKeyboardButton(
            text="⚖️ Обжаловать",
            url=f"https://t.me/{bot_username}?start=appeal_ban_{user_id}"
        ))
        await bot.send_message(
            chat_id,
            f"🚫 <b>Бан пользователя</b>\n\n"
            f"👤 <b>Пользователь:</b> {user_mention}\n"
            f"⏳ <b>Срок:</b> {duration_str}{reason_str}",
            parse_mode="HTML",
            message_thread_id=message_thread_id,
            reply_markup=ban_appeal_builder.as_markup()
        )

        # Журнал модерации
        await send_to_mod_log(
            f"🚫 Бан\n\n"
            f"👤 <b>Пользователь:</b> {user_mention}\n"
            f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
            f"⏳ <b>Срок:</b> {duration_str}"
            + (f"\n📝 <b>Причина:</b> {reason}" if reason else "") +
            f"\n🕐 <b>Время:</b> {get_moscow_time().strftime('%d.%m.%Y %H:%M:%S')}"
        )

        return True
    except Exception as e:
        logger.error(f"Ошибка бана: {e}")
        return False

async def unban_user(chat_id: int, user_id: int, message_thread_id: int = None) -> bool:
    try:
        # only_if_banned=True — не кикает пользователя, если он уже в чате
        await bot.unban_chat_member(chat_id, user_id, only_if_banned=True)

        user_mention = await get_user_mention(user_id)
        await bot.send_message(
            chat_id,
            f"✅ <b>Разбан пользователя</b>\n\n"
            f"👤 <b>Пользователь:</b> {user_mention}\n"
            f"🚪 <b>Статус:</b> Бан снят, пользователь может вернуться",
            parse_mode="HTML",
            message_thread_id=message_thread_id
        )

        # Журнал модерации
        await send_to_mod_log(
            f"✅ Разбан\n\n"
            f"👤 <b>Пользователь:</b> {user_mention}\n"
            f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
            f"🕐 <b>Время:</b> {get_moscow_time().strftime('%d.%m.%Y %H:%M:%S')}"
        )

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

async def warn_user(chat_id: int, user_id: int, reason: str = None, message_thread_id: int = None) -> bool:
    try:
        add_warn(user_id, chat_id, reason, chat_id)
        warns = get_user_warns(user_id, chat_id)

        reason_str = f"\n📝 <b>Причина:</b> {reason}" if reason else ""
        user_mention = await get_user_mention(user_id)

        appeal_builder = InlineKeyboardBuilder()
        appeal_builder.row(InlineKeyboardButton(
            text="⚖️ Обжаловать",
            url=f"https://t.me/{bot_username}?start=appeal_warn_{user_id}"
        ))
        await bot.send_message(
            chat_id,
            f"⚠️ <b>Предупреждение выдано</b>\n\n"
            f"👤 <b>Пользователь:</b> {user_mention}\n"
            f"🔢 <b>Всего предупреждений:</b> {len(warns)}{reason_str}\n"
            f"📅 <b>Действует:</b> {WARN_EXPIRE_DAYS} дней",
            parse_mode="HTML",
            message_thread_id=message_thread_id,
            reply_markup=appeal_builder.as_markup()
        )

        # Журнал модерации
        await send_to_mod_log(
            f"⚠️ Предупреждение\n\n"
            f"👤 <b>Пользователь:</b> {user_mention}\n"
            f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
            f"🔢 <b>Предупреждений:</b> {len(warns)}"
            + (f"\n📝 <b>Причина:</b> {reason}" if reason else "") +
            f"\n🕐 <b>Время:</b> {get_moscow_time().strftime('%d.%m.%Y %H:%M:%S')}"
        )

        if len(warns) >= 3:
            await ban_user(chat_id, user_id, reason="3 предупреждения", message_thread_id=message_thread_id)
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

        # Журнал модерации
        await send_to_mod_log(
            f"⚠️ Предупреждение администратору\n\n"
            f"👤 <b>Администратор:</b> {user_mention}\n"
            f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
            f"🔢 <b>Предупреждений:</b> {len(warns)}\n"
            f"👮 <b>Выдал:</b> {issued_mention}"
            + (f"\n📝 <b>Причина:</b> {reason}" if reason else "") +
            f"\n🕐 <b>Время:</b> {get_moscow_time().strftime('%d.%m.%Y %H:%M:%S')}"
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

async def send_to_mod_log(text: str):
    """Отправляет сообщение в журнал модерации"""
    try:
        await bot.send_message(MOD_LOG_CHAT_ID, text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка отправки в журнал модерации: {e}")


# ============================================================
#              КНОПКА «ОБЖАЛОВАТЬ» — ОБРАБОТКА DEEP LINK
# ============================================================

async def start_appeal_flow(message: Message, state: FSMContext, punishment_type: str, punished_user_id: int):
    """
    Запускает процесс обжалования в ЛС бота.
    Проверяет, что обжалование открыл именно наказанный пользователь.
    """
    user = message.from_user

    # Проверяем: именно ли наказанный пишет
    if user.id != punished_user_id:
        await message.answer(
            "❌ <b>Это обжалование предназначено не вам.</b>\n\n"
            "Кнопка «Обжаловать» работает только для того пользователя, которому было выдано наказание.",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(user.id)
        )
        return

    if is_user_blocked(user.id):
        await message.answer(
            "🚫 Вы заблокированы в боте и не можете подавать жалобы.",
            reply_markup=get_main_keyboard(user.id)
        )
        return

    if not user.username:
        await message.answer(
            "⚠️ <b>У вас не установлен username в Telegram!</b>\n\n"
            "Для подачи обжалования необходим username.\n"
            "Пожалуйста, установите его в настройках Telegram и попробуйте снова.",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(user.id)
        )
        return

    type_map = {
        "warn": "предупреждение",
        "mute": "мут",
        "ban": "бан",
    }
    punishment_name = type_map.get(punishment_type, "наказание")

    await state.update_data(
        username=f"@{user.username}",
        appeal_punishment_type=punishment_type
    )

    await message.answer(
        f"⚖️ <b>Обжалование: {punishment_name}</b>\n\n"
        f"Вы начали процесс обжалования наказания.\n\n"
        f"👮 Укажите юзернейм администратора, который выдал вам наказание (например, <code>@admin</code>):",
        parse_mode="HTML",
        reply_markup=get_main_keyboard(user.id)
    )
    await state.set_state(AdminComplaintStates.waiting_for_admin_username)


def get_main_keyboard(user_id: int = None):
    """Стандартная Reply-клавиатура для ЛС с ботом.
    Кнопка «Просмотреть жалобы» показывается только администраторам.
    """
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="📢 Оставить жалобу на админа"))
    # Кнопка управления жалобами — только для владельцев и администраторов
    if user_id is not None and (user_id in ADMIN_IDS or is_admin(user_id)):
        builder.row(KeyboardButton(text="📋 Просмотреть жалобы"))
    builder.row(KeyboardButton(text="📚 Пройти обучение"))
    builder.row(KeyboardButton(text="🔐 Открыть систему безопасных сделок"))
    return builder.as_markup(resize_keyboard=True)

def get_admin_main_keyboard():
    """Reply-клавиатура для администраторов (все кнопки)"""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="📢 Оставить жалобу на админа"))
    builder.row(KeyboardButton(text="📋 Просмотреть жалобы"))
    builder.row(KeyboardButton(text="📚 Пройти обучение"))
    builder.row(KeyboardButton(text="🔐 Открыть систему безопасных сделок"))
    return builder.as_markup(resize_keyboard=True)

# ============================================================
#              КОМАНДА /tw — ТЕХНИЧЕСКИЕ РАБОТЫ
# ============================================================

@dp.message(Command("tw"), F.chat.type == ChatType.PRIVATE)
async def cmd_maintenance(message: Message):
    """Команда /tw — включает/выключает режим тех.работ. Только для владельцев."""
    global MAINTENANCE_MODE

    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав для использования этой команды.")
        return

    MAINTENANCE_MODE = not MAINTENANCE_MODE

    if MAINTENANCE_MODE:
        await message.answer(
            "🔧 <b>Режим технических работ ВКЛЮЧЁН</b>\n\n"
            "Все пользователи теперь получают сообщение о тех.работах вместо обычных ответов бота.\n\n"
            "Чтобы выключить — отправьте /tw ещё раз.",
            parse_mode="HTML"
        )
        logger.info(f"Режим тех.работ включён владельцем {message.from_user.id}")
    else:
        await message.answer(
            "✅ <b>Режим технических работ ВЫКЛЮЧЕН</b>\n\n"
            "Бот работает в штатном режиме.",
            parse_mode="HTML"
        )
        logger.info(f"Режим тех.работ выключен владельцем {message.from_user.id}")


# ============================================================
#   КНОПКА «ОТКРЫТЬ СИСТЕМУ БЕЗОПАСНЫХ СДЕЛОК»
# ============================================================

@dp.message(F.chat.type == ChatType.PRIVATE, F.text == "🔐 Открыть систему безопасных сделок")
async def btn_open_safe_deal(message: Message):
    """Показывает меню системы безопасных сделок (гарант-бот)"""
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="📝 Создать сделку", callback_data="safe_deal_create"))
    keyboard.row(InlineKeyboardButton(text="📋 Мои сделки", callback_data="safe_deal_my_deals"))
    keyboard.row(InlineKeyboardButton(text="💰 Мой баланс", callback_data="safe_deal_balance"))
    keyboard.row(InlineKeyboardButton(text="⭐ Мои отзывы", callback_data="safe_deal_reviews"))
    keyboard.row(InlineKeyboardButton(text="💬 Отзыв о сервисе", callback_data="safe_deal_service_review"))
    keyboard.row(InlineKeyboardButton(text="ℹ️ Информация", callback_data="safe_deal_about"))

    await message.answer(
        "🔐 <b>Система безопасных сделок</b>\n\n"
        "Добро пожаловать в систему гарантированных сделок! Здесь вы можете безопасно "
        "провести сделку с любым пользователем — ваши средства будут под защитой гаранта "
        "до тех пор, пока обе стороны не выполнят свои обязательства.\n\n"
        "💡 <b>Как это работает?</b>\n"
        "1️⃣ Создайте сделку и укажите условия\n"
        "2️⃣ Покупатель вносит оплату (средства у гаранта)\n"
        "3️⃣ Продавец выполняет свои обязательства\n"
        "4️⃣ Покупатель подтверждает получение\n"
        "5️⃣ Гарант переводит средства продавцу\n\n"
        "🛡️ <b>Преимущества:</b>\n"
        "• Защита от мошенничества для обеих сторон\n"
        "• Разрешение споров через администратора\n"
        "• Система отзывов для репутации\n"
        "• Комиссия всего <b>8%</b> от суммы сделки\n\n"
        "Выберите действие 👇",
        parse_mode="HTML",
        reply_markup=keyboard.as_markup()
    )


@dp.callback_query(F.data == "safe_deal_about")
async def safe_deal_about(callback: CallbackQuery):
    """Информация о системе безопасных сделок"""
    await callback.answer()
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="📝 Создать сделку", callback_data="safe_deal_create"))
    keyboard.row(InlineKeyboardButton(text="📋 Мои сделки", callback_data="safe_deal_my_deals"))
    keyboard.row(InlineKeyboardButton(text="💰 Мой баланс", callback_data="safe_deal_balance"))
    keyboard.row(InlineKeyboardButton(text="⭐ Мои отзывы", callback_data="safe_deal_reviews"))
    keyboard.row(InlineKeyboardButton(text="💬 Отзыв о сервисе", callback_data="safe_deal_service_review"))

    about_text = (
        "ℹ️ <b>Информация о системе безопасных сделок</b>\n\n"
        "Система безопасных сделок — это встроенный гарант, который защищает обе стороны "
        "при проведении финансовых операций.\n\n"
        "✅ <b>Гарантия безопасности</b> — средства хранятся у гаранта до выполнения условий сделки\n"
        "✅ <b>Защита от мошенников</b> — покупатель и продавец под надёжной защитой\n"
        "✅ <b>Разрешение споров</b> — администратор поможет урегулировать конфликтные ситуации\n"
        "✅ <b>Система отзывов</b> — формируйте репутацию надёжного партнёра\n"
        "✅ <b>Прозрачность</b> — все этапы сделки отслеживаются в реальном времени\n\n"
        "💳 <b>Оплата через ЮMoney</b>\n"
        "💼 <b>Комиссия сервиса: 8%</b> от суммы сделки\n\n"
        "📞 <b>Поддержка:</b> обратитесь к администраторам через /report\n\n"
        "Начните безопасную сделку прямо сейчас!"
    )
    await callback.message.edit_text(about_text, reply_markup=keyboard.as_markup(), parse_mode="HTML")


@dp.callback_query(F.data == "safe_deal_create")
async def safe_deal_create_redirect(callback: CallbackQuery, state: FSMContext):
    """Создание новой сделки"""
    await callback.answer()
    await state.set_state(DealStates.DEAL_ROLE)
    
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="👤 Я покупатель", callback_data="role_buyer"))
    keyboard.row(InlineKeyboardButton(text="👨‍💼 Я продавец", callback_data="role_seller"))
    keyboard.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_safe_deal_menu"))
    
    await callback.message.edit_text(
        "🤝 <b>Создание новой сделки</b>\n\n"
        "Выберите вашу роль в сделке:",
        reply_markup=keyboard.as_markup(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "safe_deal_my_deals")
async def safe_deal_my_deals_redirect(callback: CallbackQuery):
    """Мои сделки"""
    await callback.answer()
    user_id = callback.from_user.id
    
    conn = sqlite3.connect("data/bot_database.db")
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM safe_deals 
        WHERE buyer_id = ? OR seller_id = ? 
        ORDER BY created_at DESC
    ''', (user_id, user_id))
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        keyboard = InlineKeyboardBuilder()
        keyboard.row(InlineKeyboardButton(text="📝 Создать сделку", callback_data="safe_deal_create"))
        keyboard.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_safe_deal_menu"))
        await callback.message.edit_text(
            "📋 <b>Мои сделки</b>\n\nУ вас пока нет сделок.",
            reply_markup=keyboard.as_markup(),
            parse_mode="HTML"
        )
        return
    
    keyboard = InlineKeyboardBuilder()
    for row in rows[:10]:
        deal_id = row[0]
        amount = row[8]
        status = row[11]
        status_emoji = {'created': '🟡', 'active': '🟢', 'completed': '✅', 'cancelled': '❌'}.get(status, '⚪')
        keyboard.row(InlineKeyboardButton(text=f"{status_emoji} Сделка #{deal_id} - {amount} руб.", callback_data=f"view_safe_deal_{deal_id}"))
    
    keyboard.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_safe_deal_menu"))
    await callback.message.edit_text(
        f"📋 <b>Мои сделки</b>\n\nНайдено сделок: {len(rows)}",
        reply_markup=keyboard.as_markup(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "safe_deal_balance")
async def safe_deal_balance_redirect(callback: CallbackQuery):
    """Мой баланс в системе гаранта"""
    await callback.answer()
    user_id = callback.from_user.id
    
    conn = sqlite3.connect("data/bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM safe_deal_balances WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    
    balance = row[0] if row else 0.0
    
    keyboard = InlineKeyboardBuilder()
    if balance >= 50:
        keyboard.row(InlineKeyboardButton(text="💰 Подать заявку на вывод", callback_data="withdraw_from_balance"))
    keyboard.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_safe_deal_menu"))
    
    await callback.message.edit_text(
        f"💰 <b>Ваш баланс в системе гаранта:</b> {balance:.2f} руб.\n\n"
        f"Минимальная сумма для вывода: 50 руб.",
        reply_markup=keyboard.as_markup(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "safe_deal_reviews")
async def safe_deal_reviews_redirect(callback: CallbackQuery):
    """Мои отзывы о сделках"""
    await callback.answer()
    user_id = callback.from_user.id
    
    conn = sqlite3.connect("data/bot_database.db")
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM safe_deal_reviews 
        WHERE reviewed_user_id = ? 
        ORDER BY created_at DESC
    ''', (user_id,))
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        keyboard = InlineKeyboardBuilder()
        keyboard.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_safe_deal_menu"))
        await callback.message.edit_text(
            "⭐ <b>Мои отзывы</b>\n\nУ вас пока нет отзывов.",
            reply_markup=keyboard.as_markup(),
            parse_mode="HTML"
        )
        return
    
    text = "⭐ <b>Отзывы о вас</b>\n\n"
    for row in rows[:10]:
        rating = row[3]
        review_text = row[4]
        text += f"⭐ {rating}/5: {review_text}\n\n"
    
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_safe_deal_menu"))
    await callback.message.edit_text(text, reply_markup=keyboard.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data == "safe_deal_service_review")
async def safe_deal_service_review_redirect(callback: CallbackQuery, state: FSMContext):
    """Отзыв о сервисе"""
    await callback.answer()
    await state.set_state("waiting_for_service_review")
    
    rating_keyboard = InlineKeyboardBuilder()
    for i in range(1, 6):
        rating_keyboard.row(InlineKeyboardButton(text="⭐" * i, callback_data=f"service_rating_{i}"))
    rating_keyboard.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_safe_deal_menu"))
    
    await callback.message.edit_text(
        "💬 <b>Отзыв о сервисе SafeDeal</b>\n\n"
        "Пожалуйста, оцените нашу работу:",
        reply_markup=rating_keyboard.as_markup(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("service_rating_"))
async def process_service_rating_safe(callback: CallbackQuery, state: FSMContext):
    """Обработка рейтинга отзыва о сервисе"""
    await callback.answer()
    rating = int(callback.data.split("_")[2])
    await state.update_data(service_rating=rating)
    await state.set_state("waiting_for_service_review_text")
    
    await callback.message.edit_text(
        f"⭐ <b>Оценка: {rating}/5</b>\n\n"
        f"Теперь напишите текстовый отзыв:",
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "back_to_safe_deal_menu")
async def back_to_safe_deal_menu(callback: CallbackQuery):
    """Возврат в меню безопасных сделок"""
    await callback.answer()
    
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="📝 Создать сделку", callback_data="safe_deal_create"))
    keyboard.row(InlineKeyboardButton(text="📋 Мои сделки", callback_data="safe_deal_my_deals"))
    keyboard.row(InlineKeyboardButton(text="💰 Мой баланс", callback_data="safe_deal_balance"))
    keyboard.row(InlineKeyboardButton(text="⭐ Мои отзывы", callback_data="safe_deal_reviews"))
    keyboard.row(InlineKeyboardButton(text="💬 Отзыв о сервисе", callback_data="safe_deal_service_review"))
    keyboard.row(InlineKeyboardButton(text="ℹ️ Информация", callback_data="safe_deal_about"))
    
    await callback.message.edit_text(
        "🔐 <b>Система безопасных сделок</b>\n\n"
        "Добро пожаловать в систему гарантированных сделок!\n\n"
        "✅ Гарантия безопасности\n"
        "✅ Защита от мошенников\n"
        "✅ Разрешение споров\n"
        f"✅ Комиссия: {GUARANTOR_FEE*100:.0f}%\n\n"
        "Выберите действие 👇",
        reply_markup=keyboard.as_markup(),
        parse_mode="HTML"
    )

# Состояния для FSM
class SafeDealStates(StatesGroup):
    DEAL_ROLE = State()
    DEAL_AMOUNT = State()
    DEAL_DESCRIPTION = State()
    DEAL_DEADLINE = State()
    DEAL_PARTNER = State()
    DEAL_GROUP_LINK = State()

# Обработчики создания сделки
@dp.callback_query(F.data == "role_buyer")
async def process_role_buyer(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(creator_role="buyer")
    await state.set_state(SafeDealStates.DEAL_AMOUNT)
    await callback.message.edit_text("💰 <b>Введите сумму сделки в рублях:</b>", parse_mode="HTML")

@dp.callback_query(F.data == "role_seller")
async def process_role_seller(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(creator_role="seller")
    await state.set_state(SafeDealStates.DEAL_AMOUNT)
    await callback.message.edit_text("💰 <b>Введите сумму сделки в рублях:</b>", parse_mode="HTML")

@dp.message(SafeDealStates.DEAL_AMOUNT)
async def process_safe_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
        if amount <= 0:
            await message.answer("❌ Сумма должна быть больше 0:")
            return
        await state.update_data(amount=amount)
        await state.set_state(SafeDealStates.DEAL_DESCRIPTION)
        await message.answer("📝 <b>Опишите предмет сделки:</b>", parse_mode="HTML")
    except ValueError:
        await message.answer("❌ Введите число:")

@dp.message(SafeDealStates.DEAL_DESCRIPTION)
async def process_safe_description(message: Message, state: FSMContext):
    if len(message.text.strip()) < 5:
        await message.answer("❌ Описание слишком короткое:")
        return
    await state.update_data(description=message.text.strip())
    await state.set_state(SafeDealStates.DEAL_DEADLINE)
    
    keyboard = InlineKeyboardBuilder()
    for days in [1, 3, 7, 14]:
        keyboard.row(InlineKeyboardButton(text=f"{days} дней", callback_data=f"deadline_{days}"))
    await message.answer("⏰ <b>Выберите срок выполнения:</b>", reply_markup=keyboard.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("deadline_"))
async def process_safe_deadline(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    days = int(callback.data.split("_")[1])
    await state.update_data(deadline_days=days)
    await state.set_state(SafeDealStates.DEAL_PARTNER)
    
    data = await state.get_data()
    role = data.get("creator_role", "buyer")
    partner = "продавца" if role == "buyer" else "покупателя"
    await callback.message.edit_text(f"👤 <b>Введите username {partner}:</b>\n\nПример: @username", parse_mode="HTML")

@dp.message(SafeDealStates.DEAL_PARTNER)
async def process_safe_partner(message: Message, state: FSMContext):
    partner_username = message.text.strip().lstrip('@')
    
    try:
        user = await bot.get_chat(f"@{partner_username}")
        partner_id = user.id
    except Exception:
        await message.answer(f"❌ Пользователь @{partner_username} не найден. Попробуйте снова:")
        return
    
    if partner_id == message.from_user.id:
        await message.answer("❌ Нельзя создать сделку с самим собой:")
        return
    
    data = await state.get_data()
    creator_role = data.get("creator_role")
    
    if creator_role == "buyer":
        await state.update_data(buyer_id=message.from_user.id, buyer_username=message.from_user.username or "user",
                                seller_id=partner_id, seller_username=partner_username)
    else:
        await state.update_data(seller_id=message.from_user.id, seller_username=message.from_user.username or "user",
                                buyer_id=partner_id, buyer_username=partner_username)
    
    await state.set_state(SafeDealStates.DEAL_GROUP_LINK)
    await message.answer(
        "👥 <b>Создание группы для сделки</b>\n\n"
        "Создайте группу, добавьте в неё второго участника и бота, затем отправьте ссылку-приглашение:",
        parse_mode="HTML"
    )

@dp.message(SafeDealStates.DEAL_GROUP_LINK)
async def process_safe_group_link(message: Message, state: FSMContext):
    link = message.text.strip()
    if not (link.startswith("https://t.me/") or link.startswith("t.me/")):
        await message.answer("❌ Отправьте корректную ссылку на группу Telegram:")
        return
    
    data = await state.get_data()
    deal_id = generate_deal_id()
    
    deal = {
        "id": deal_id,
        "creator_id": message.from_user.id,
        "creator_role": data.get("creator_role"),
        "buyer_id": data.get("buyer_id"),
        "seller_id": data.get("seller_id"),
        "buyer_username": data.get("buyer_username"),
        "seller_username": data.get("seller_username"),
        "amount": data.get("amount"),
        "description": data.get("description"),
        "deadline_days": data.get("deadline_days"),
        "group_link": link
    }
    
    if save_safe_deal(deal):
        await message.answer(
            f"✅ <b>Сделка создана!</b>\n\n"
            f"🆔 Номер сделки: <code>#{deal_id}</code>\n"
            f"💰 Сумма: {data.get('amount')} руб.\n"
            f"🔗 Группа: {link}\n\n"
            f"Ожидайте подтверждения от второй стороны.",
            parse_mode="HTML"
        )
        await state.clear()
    else:
        await message.answer("❌ Ошибка при создании сделки. Попробуйте позже.")


# ============================================================
#   КОМАНДА /test_connection — ТОЛЬКО ДЛЯ ВЛАДЕЛЬЦЕВ
# ============================================================

@dp.message(Command("test_connection"), F.chat.type == ChatType.PRIVATE)
async def cmd_test_connection(message: Message):
    """Тестирование подключения к ЮMoney API — только для владельцев"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав для использования этой команды.")
        return

    await message.answer("🔄 Тестируем подключение к ЮMoney...")

    try:
        import aiohttp as _aiohttp

        YOO_MONEY_CLIENT_ID = os.getenv("YOO_MONEY_CLIENT_ID", "CBBB9861A51751178B0351FC9B7F549CB8570B3D93090F9FDC8EDE1FF7C246F0")
        YOO_MONEY_CLIENT_SECRET = os.getenv("YOO_MONEY_CLIENT_SECRET", "6A5BE8FC665AF63D898B27AA35BDE31E04527F15A4D1962F602B471215B03A946B84D6C13F0B53124313500D9E566733C7676EBCEBBFA32350E1E380E83D2C96")
        YOO_MONEY_ACCOUNT = os.getenv("YOO_MONEY_ACCOUNT", "4100118560796193")
        YOO_MONEY_ACCESS_TOKEN = os.getenv("YOO_MONEY_ACCESS_TOKEN", "4100118560796193.06074F7AA0A3BC1C579BD8F3759B69B3AB4FAC68E9BED138E0EE0A66397F7A29AFFA6DD32BE06586421DC0F8F9659F57771FE57E68837C5729F525D2317617652B221DC315716422A41267681CBF7AB979D4257642A33E0A20257D1F27733822FD2134CE922AA1D72C25E8345D59ACF24901E9131E037E01E1ABE30DD1641CE4")
        YOO_BASE_URL = "https://yoomoney.ru/api"

        if not YOO_MONEY_ACCESS_TOKEN:
            await message.answer(
                "❌ <b>OAuth токен не установлен</b>\n\n"
                "Добавьте переменную окружения <code>YOO_MONEY_ACCESS_TOKEN</code>.",
                parse_mode="HTML"
            )
            return

        async with _aiohttp.ClientSession() as session:
            headers = {
                'Authorization': f'Bearer {YOO_MONEY_ACCESS_TOKEN}',
                'Content-Type': 'application/x-www-form-urlencoded'
            }

            # Проверяем информацию об аккаунте
            async with session.post(f"{YOO_BASE_URL}/account-info", headers=headers) as response:
                if response.status == 200:
                    account_info = await response.json()
                    balance = account_info.get('balance', 'N/A')
                    account_status = account_info.get('account_status', 'N/A')
                    account_type = account_info.get('account_type', 'N/A')
                    currency = account_info.get('currency', 'N/A')

                    formatted_balance = f"{balance:.2f}" if isinstance(balance, (int, float)) else str(balance)

                    await message.answer(
                        f"✅ <b>Подключение к ЮMoney успешно!</b>\n\n"
                        f"💰 <b>Баланс:</b> {formatted_balance} {currency}\n"
                        f"📊 <b>Статус счёта:</b> {account_status}\n"
                        f"🏦 <b>Тип счёта:</b> {account_type}\n"
                        f"👤 <b>Кошелёк:</b> {YOO_MONEY_ACCOUNT}\n"
                        f"🔑 <b>Токен активен:</b> Да",
                        parse_mode="HTML"
                    )

                    # Проверяем доступ к истории операций
                    await message.answer("🔄 Проверяем доступ к истории операций...")
                    params = {"type": "deposition", "records": 5, "details": "true"}
                    async with session.get(
                        f"{YOO_BASE_URL}/operation-history",
                        headers=headers,
                        params=params
                    ) as hist_resp:
                        if hist_resp.status == 200:
                            data = await hist_resp.json()
                            operations = data.get('operations', [])
                            await message.answer(
                                f"✅ <b>Доступ к истории операций подтверждён!</b>\n\n"
                                f"📈 <b>Последних операций:</b> {len(operations)}\n"
                                f"🔧 <b>API работает корректно</b>",
                                parse_mode="HTML"
                            )
                        else:
                            await message.answer(
                                f"⚠️ <b>Основное подключение работает, но есть ограничения:</b>\n\n"
                                f"❌ Не удалось получить историю операций\n"
                                f"📝 <b>Статус:</b> {hist_resp.status}\n\n"
                                f"<i>Проверьте scope разрешений в OAuth</i>",
                                parse_mode="HTML"
                            )
                else:
                    error_text = await response.text()
                    await message.answer(
                        f"❌ <b>Ошибка подключения к ЮMoney</b>\n\n"
                        f"Код ответа: {response.status}\n"
                        f"Ошибка: {error_text[:200]}\n\n"
                        "Возможные причины:\n"
                        "• Неверный или устаревший OAuth токен\n"
                        "• Неправильные client_id / client_secret\n"
                        "• Проблемы с сетью",
                        parse_mode="HTML"
                    )

    except Exception as e:
        await message.answer(
            f"❌ <b>Неожиданная ошибка при подключении:</b>\n\n"
            f"<code>{e}</code>\n\n"
            "Проверьте настройки и логи бота.",
            parse_mode="HTML"
        )


@dp.message(Command("start"))
async def cmd_start_with_reviews(message: Message, command: CommandObject, state: FSMContext):
    """Обработчик команды /start с параметрами"""
    if message.chat.type != ChatType.PRIVATE:
        return

    if MAINTENANCE_MODE and message.from_user.id not in ADMIN_IDS:
        await message.answer(
            "🔧 <b>Бот на технических работах</b>\n\n"
            "Приносим извинения за неудобства. Пожалуйста, попробуйте позже.",
            parse_mode="HTML"
        )
        return

    if is_user_blocked(message.from_user.id):
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
            f"Для разблокировки свяжитесь с администраторами.\n\n"
            f"🔗 <a href='https://t.me/KoshakFSB'>Связаться с администратором</a>",
            parse_mode="HTML"
        )
        return

    if command.args and command.args.startswith("reviews_"):
        try:
            seller_id = int(command.args.split("_")[1])
            await show_reviews_in_private(message, seller_id)
            return
        except (ValueError, IndexError):
            pass

    user_id = message.from_user.id

    if command.args and command.args.startswith("appeal_"):
        try:
            parts = command.args.split("_")
            punishment_type = parts[1]
            punished_user_id = int(parts[2])
        except (IndexError, ValueError):
            punishment_type = "наказание"
            punished_user_id = message.from_user.id
        await start_appeal_flow(message, state, punishment_type, punished_user_id)
        return

    if command.args == "onboarding":
        if not is_onboarding_completed(user_id):
            start_onboarding_db(user_id)
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text="✅ Да, поехали!", callback_data="onb_start"))
            builder.row(InlineKeyboardButton(text="⏳ Позже", callback_data="onb_later"))
            await message.answer(
                "👋 <b>Привет!</b>\n\n"
                "Добро пожаловать в наш чат 🎉\n\n"
                "Чтобы ты сразу знал все правила и не получил случайное наказание, "
                "пройди короткое обучение. Это займёт буквально <b>2-3 минуты</b>.\n\n"
                "Готов начать?",
                parse_mode="HTML",
                reply_markup=builder.as_markup()
            )
            return
        await message.answer("✅ Ты уже прошёл обучение! Добро пожаловать.", reply_markup=get_main_keyboard(message.from_user.id))
        return

    if is_onboarding_started(user_id) and not is_onboarding_completed(user_id):
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="✅ Да, поехали!", callback_data="onb_start"))
        builder.row(InlineKeyboardButton(text="⏳ Позже", callback_data="onb_later"))
        await message.answer(
            "👋 <b>Привет!</b>\n\n"
            "⚠️ Ты ещё не прошёл обязательное обучение для участников чата.\n\n"
            "Пройди его — это займёт всего 2-3 минуты и поможет тебе избежать случайных нарушений!",
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )
        return

    welcome_text = (
        "👋 <b>Привет!</b> Добро пожаловать в бот барахолки!\n\n"
        "Здесь ты можешь:\n"
        "• ⭐️ Оставлять отзывы о продавцах\n"
        "• 📢 Подавать жалобы на администраторов\n"
        "• 🔐 Воспользоваться системой безопасных сделок\n\n"
        "Используй кнопки меню ниже 👇"
    )
    
    await message.answer(
        welcome_text,
        parse_mode="HTML",
        reply_markup=get_main_keyboard(message.from_user.id)
    )

# ============================================================
#   ОБРАБОТЧИКИ REPLY-КНОПОК ГЛАВНОГО МЕНЮ В ЛС
# ============================================================

@dp.message(F.chat.type == ChatType.PRIVATE, F.text == "📢 Оставить жалобу на админа")
async def btn_complain_admin(message: Message, state: FSMContext):
    if is_user_blocked(message.from_user.id):
        await message.answer("🚫 Вы заблокированы в боте.", reply_markup=get_main_keyboard(message.from_user.id))
        return

    # Автоматически определяем username пользователя
    user = message.from_user
    if user.username:
        # Username есть — сохраняем и пропускаем этот шаг
        await state.update_data(username=f"@{user.username}")
        await message.answer(
            "👮 Укажите юзернейм администратора, на которого хотите пожаловаться (например, @admin):",
            parse_mode="HTML"
        )
        await state.set_state(AdminComplaintStates.waiting_for_admin_username)
    else:
        # Username нет — просим установить
        await message.answer(
            "⚠️ <b>У вас не установлен username в Telegram!</b>\n\n"
            "Для подачи жалобы необходим username, чтобы с вами могли связаться.\n\n"
            "Пожалуйста, установите username в настройках Telegram, а затем повторно нажмите кнопку жалобы.\n\n"
            "🔗 <a href='https://okbob.app/blog/telegram-set-username?ysclid=mm0dtsva4d256888466'>Как установить username</a>",
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=get_main_keyboard(message.from_user.id)
        )

@dp.message(F.chat.type == ChatType.PRIVATE, F.text == "📋 Просмотреть жалобы")
async def btn_view_complaints(message: Message):
    if not await is_bot_admin(message.from_user.id):
        await message.answer("❌ У вас нет прав для просмотра жалоб.", reply_markup=get_main_keyboard(message.from_user.id))
        return
    complaints = get_active_complaints()
    complaints_count = len(complaints)
    text = f"📋 <b>Активные жалобы на администраторов</b>\n\n📊 <b>Всего:</b> {complaints_count}\n\n"
    if complaints_count > 0:
        text += "<b>Список жалоб:</b>\n"
        for c in complaints[:10]:
            text += f"• #{c.get('id','N/A')} {c.get('username','?')} → {c.get('admin_username','?')}\n"
        if complaints_count > 10:
            text += f"\n... и ещё {complaints_count - 10}"
    else:
        text += "🎉 Активных жалоб нет!"
    await message.answer(text, parse_mode="HTML", reply_markup=get_complaints_keyboard())



@dp.message(F.chat.type == ChatType.PRIVATE, F.text == "📚 Пройти обучение")
async def btn_training(message: Message, state: FSMContext):
    """Кнопка «Пройти обучение» — запускает онбординг"""
    user_id = message.from_user.id
    if is_user_blocked(user_id):
        await message.answer("🚫 Вы заблокированы в боте.", reply_markup=get_main_keyboard(user_id))
        return
    if is_onboarding_completed(user_id):
        await message.answer(
            "✅ <b>Ты уже прошёл обучение!</b>\n\n"
            "Если хочешь пройти его снова — напиши /start.",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(user_id)
        )
        return
    start_onboarding_db(user_id)
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✅ Да, поехали!", callback_data="onb_start"))
    builder.row(InlineKeyboardButton(text="⏳ Позже", callback_data="onb_later"))
    await message.answer(
        "👋 <b>Привет!</b>\n\n"
        "Пройди короткое обучение — это займёт <b>2–3 минуты</b> и поможет "
        "не получить случайное наказание. Готов начать?",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )

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

@dp.message(Command("warns"))
async def cmd_warns(message: Message, command: CommandObject):
    """Просмотр предупреждений пользователя"""
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        clown_msg = await message.reply("🤡")
        await asyncio.sleep(2)
        await delete_message(message.chat.id, message.message_id)
        await mute_user(
            message.chat.id,
            message.from_user.id,
            MUTE_DURATION,
            "Использование команд бота",
            is_auto=True,
            message_thread_id=message.message_thread_id
        )
        return

    # Используем resolve_user_only для получения user_id
    user_id = await resolve_user_only(message, command.args)
    
    if not user_id:
        await message.answer(
            "❌ <b>Не указан пользователь!</b>\n\n"
            "📌 <b>Форматы использования:</b>\n\n"
            "1️⃣ <b>Ответ на сообщение:</b>\n"
            "   <code>/warns</code>\n"
            "   (без аргументов, просто ответьте на сообщение)\n\n"
            "2️⃣ <b>По ID пользователя:</b>\n"
            "   <code>/warns ID</code>\n"
            "   Пример: <code>/warns 123456789</code>\n\n"
            "3️⃣ <b>По @username:</b>\n"
            "   <code>/warns @username</code>\n"
            "   Пример: <code>/warns @user</code>",
            parse_mode="HTML"
        )
        return

    warns = get_user_warns(user_id, message.chat.id)
    user_mention = await get_user_mention(user_id)

    if not warns:
        await message.answer(f"✅ У пользователя {user_mention} нет активных предупреждений.", parse_mode="HTML")
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
            is_auto=True,
            message_thread_id=message.message_thread_id
        )
        return

    user_id = await resolve_user_only(message, command.args)
    
    if not user_id:
        await message.answer(
            "❌ <b>Не указан пользователь!</b>\n\n"
            "📌 <b>Используйте:</b>\n"
            "• <code>/clearwarns</code> - ответом на сообщение\n"
            "• <code>/clearwarns @username</code>\n"
            "• <code>/clearwarns ID</code>",
            parse_mode="HTML"
        )
        return

    clear_user_warns(user_id, message.chat.id)
    user_mention = await get_user_mention(user_id)

    await message.answer(
        f"✅ Предупреждения пользователя {user_mention} очищены.",
        parse_mode="HTML",
    )

@dp.message(Command("adblock"))
async def cmd_adblock(message: Message, command: CommandObject):
    """Блокирует возможность публиковать объявления для пользователя"""
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("❌ Эта команда работает только в личных сообщениях с ботом.")
        return
    
    if not await is_owner(message.from_user.id) and not await is_bot_admin(message.from_user.id):
        await message.answer("❌ У вас нет прав для этой команды.")
        return
    
    # Проверка роли (только admin и владелец)
    if not admin_can(message.from_user.id, "can_adblock"):
        role = get_admin_role(message.from_user.id)
        role_label = ADMIN_ROLES.get(role, {}).get("label", "вашей роли") if role else "вашей роли"
        await message.answer(
            f"❌ Роль <b>{role_label}</b> не позволяет использовать /adblock.\n"
            f"Эта команда доступна только <b>Администраторам</b>.",
            parse_mode="HTML"
        )
        return

    # Разбираем аргументы
    args = command.args
    
    # Если это ответ на сообщение (но в ЛС нет ответов, поэтому оставим для совместимости)
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
            await message.answer("❌ Укажите время: /adblock [время] [причина] или /adblock @user 1d [причина]")
            return
    else:
        # Если не ответ на сообщение, то парсим аргументы полностью
        if not args:
            await message.answer("❌ Укажите пользователя и время: /adblock @user 1d [причина]")
            return

        parts = args.split(maxsplit=2)
        
        if len(parts) < 2:
            await message.answer("❌ Формат: /adblock <пользователь> <время> [причина]")
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

    # Блокируем публикацию объявлений
    
    duration_str = await format_duration(duration)
    user_mention = await get_user_mention(user_id)
    admin_mention = await get_user_mention(message.from_user.id)
    
    await message.answer(
        f"🚫 <b>Блокировка публикации объявлений</b>\n\n"
        f"👤 <b>Пользователь:</b> {user_mention}\n"
        f"⏳ <b>Срок:</b> {duration_str}\n"
        f"📝 <b>Причина:</b> {reason}\n"
        f"👮 <b>Заблокировал:</b> {admin_mention}\n\n"
        f"<i>Пользователь не сможет публиковать объявления в течение указанного срока.</i>",
        parse_mode="HTML"
    )
    
    # Уведомляем пользователя о блокировке
    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"🚫 <b>Вам заблокирована публикация объявлений</b>\n\n"
                 f"⏳ <b>Срок:</b> {duration_str}\n"
                 f"📝 <b>Причина:</b> {reason}\n\n"
                 f"<i>В течение этого времени вы не сможете публиковать объявления в чате.</i>",
            parse_mode="HTML"
        )
    except:
        pass  # Не удалось отправить уведомление

@dp.message(Command("unadblock"))
async def cmd_unadblock(message: Message, command: CommandObject):
    """Снимает блокировку публикации объявлений с пользователя"""
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("❌ Эта команда работает только в личных сообщениях с ботом.")
        return
    
    if not await is_owner(message.from_user.id) and not await is_bot_admin(message.from_user.id):
        await message.answer("❌ У вас нет прав для этой команды.")
        return

    if not command.args:
        await message.answer("❌ Укажите пользователя: /unadblock @user")
        return

    user_id = await get_user_id_from_message(command.args)
    if not user_id:
        await message.answer("❌ Пользователь не найден.")
        return

    # Проверяем, есть ли активная блокировка
        await message.answer("✅ У пользователя нет активной блокировки публикаций.")
        return

    # Снимаем блокировку
    
    user_mention = await get_user_mention(user_id)
    admin_mention = await get_user_mention(message.from_user.id)
    
    await message.answer(
        f"✅ <b>Блокировка публикации объявлений снята</b>\n\n"
        f"👤 <b>Пользователь:</b> {user_mention}\n"
        f"👮 <b>Снял блокировку:</b> {admin_mention}\n\n"
        f"<i>Пользователь снова может публиковать объявления.</i>",
        parse_mode="HTML"
    )
    
    # Уведомляем пользователя о снятии блокировки
    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"✅ <b>С вас снята блокировка публикации объявлений</b>\n\n"
                 f"<i>Вы снова можете публиковать объявления в чате.</i>",
            parse_mode="HTML"
        )
    except:
        pass

@dp.message(Command("adblock_info"))
async def cmd_adblock_info(message: Message, command: CommandObject):
    """Показывает информацию о блокировке публикаций пользователя"""
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("❌ Эта команда работает только в личных сообщениях с ботом.")
        return
    
    if not await is_owner(message.from_user.id) and not await is_bot_admin(message.from_user.id):
        await message.answer("❌ У вас нет прав для этой команды.")
        return

    if not command.args:
        await message.answer("❌ Укажите пользователя: /adblock_info @user")
        return

    user_id = await get_user_id_from_message(command.args)
    if not user_id:
        await message.answer("❌ Пользователь не найден.")
        return

    user_mention = await get_user_mention(user_id)
    
    if not block_info:
        await message.answer(f"✅ У пользователя {user_mention} нет активной блокировки публикаций.")
        return
    
    issued_by_mention = await get_user_mention(block_info['issued_by'])
    expires_at = block_info['expires_at']
    
    if expires_at:
        expires_str = datetime.fromisoformat(expires_at).strftime('%d.%m.%Y %H:%M')
        expires_text = f"до {expires_str}"
    else:
        expires_text = "навсегда"
    
    await message.answer(
        f"🚫 <b>Информация о блокировке публикаций</b>\n\n"
        f"👤 <b>Пользователь:</b> {user_mention}\n"
        f"📝 <b>Причина:</b> {block_info['reason'] or 'Не указана'}\n"
        f"👮 <b>Заблокировал:</b> {issued_by_mention}\n"
        f"📅 <b>Дата блокировки:</b> {block_info['issued_at']}\n"
        f"⏳ <b>Срок:</b> {expires_text}",
        parse_mode="HTML"
    )

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

    # Разбираем аргументы вручную: /admin_add @user <role> [display_name]
    # Роли: jr_mod / junior_moderator, mod / moderator, sr_mod / senior_moderator, admin
    ROLE_ALIASES = {
        "jr_mod": "junior_moderator",
        "junior_moderator": "junior_moderator",
        "мл_мод": "junior_moderator",
        "мл.мод": "junior_moderator",
        "mod": "moderator",
        "moderator": "moderator",
        "мод": "moderator",
        "модератор": "moderator",
        "sr_mod": "senior_moderator",
        "senior_moderator": "senior_moderator",
        "ст_мод": "senior_moderator",
        "ст.мод": "senior_moderator",
        "admin": "admin",
        "администратор": "admin",
        "адм": "admin",
    }

    parsed = await parse_command_args_v2(message, command, has_time=False)

    if parsed['error']:
        await message.answer(
            f"{parsed['error']}\n\n"
            "📌 <b>Форматы:</b>\n"
            "• <code>/admin_add @user &lt;роль&gt; [имя]</code>\n"
            "• <code>/admin_add ID &lt;роль&gt; [имя]</code>\n\n"
            "🎭 <b>Роли:</b>\n"
            "• <code>jr_mod</code> — Мл. модератор (только муты)\n"
            "• <code>mod</code> — Модератор (муты + варны)\n"
            "• <code>sr_mod</code> — Ст. модератор (муты + варны + баны)\n"
            "• <code>admin</code> — Администратор (муты + варны + баны + adblock)",
            parse_mode="HTML"
        )
        return

    user_id = parsed['user_id']
    if not user_id:
        await message.answer("❌ Укажите пользователя (ответом на сообщение или @username/id).")
        return

    # Извлекаем роль и имя из оставшихся args
    role_str = "moderator"
    display_name = None
    extra_args = []

    if command.args:
        # Убираем из args всё что связано с user_id
        parts = command.args.split()
        # Первый токен мог быть @user или ID — пропустим его если parsed нашёл пользователя
        start_idx = 0
        if parts and (parts[0].startswith("@") or parts[0].isdigit()):
            start_idx = 1
        extra_args = parts[start_idx:]

    if extra_args:
        potential_role = extra_args[0].lower().replace("-", "_")
        if potential_role in ROLE_ALIASES:
            role_str = ROLE_ALIASES[potential_role]
            if len(extra_args) > 1:
                display_name = " ".join(extra_args[1:])
        else:
            # Всё остальное — display_name
            display_name = " ".join(extra_args)

    role_data = ADMIN_ROLES.get(role_str, ADMIN_ROLES["moderator"])
    
    # Получаем информацию о пользователе
    try:
        user = await bot.get_chat(user_id)
        user_mention = await get_user_mention(user_id)
        
        if display_name is None:
            display_name = user.first_name or user.username or str(user_id)
        
        # Назначаем администратора в группе с правами согласно роли
        tg_rights = role_data["tg_rights"]
        await bot.promote_chat_member(
            chat_id=message.chat.id,
            user_id=user_id,
            **tg_rights
        )
        
        # Задаём звание в группе
        try:
            await bot.set_chat_administrator_custom_title(
                chat_id=message.chat.id,
                user_id=user_id,
                custom_title=role_data["label"]
            )
        except Exception:
            pass  # Может не работать в некоторых группах
        
        # Добавляем в базу данных бота с ролью
        add_admin(user_id, message.from_user.id, role=role_str, display_name=display_name)
        
        # Список доступных команд по роли
        cmds = []
        if role_data["can_mute"]: cmds.append("/mute")
        if role_data["can_warn"]: cmds.append("/warn")
        if role_data["can_ban"]: cmds.append("/ban")
        if role_data["can_adblock"]: cmds.append("/adblock")
        cmds_text = ", ".join(cmds) if cmds else "—"
        
        await message.answer(
            f"✅ {user_mention} назначен как <b>{role_data['emoji']} {role_data['label']}</b>\n"
            f"📛 Отображаемое имя: <b>{display_name}</b>\n"
            f"🔧 Доступные команды: {cmds_text}",
            parse_mode="HTML"
        )
        
        # Уведомляем нового администратора
        try:
            await bot.send_message(
                chat_id=user_id,
                text=f"🎉 <b>Вам назначены права администратора</b>\n\n"
                     f"Роль: <b>{role_data['emoji']} {role_data['label']}</b>\n\n"
                     f"<b>Доступные команды:</b> {cmds_text}\n\n"
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

    parsed = await parse_command_args_v2(message, command, has_time=False)

    if parsed['error']:
        await message.answer(
            f"{parsed['error']}\n\n"
            "📌 <b>Форматы:</b>\n"
            "• <code>/admin_remove</code> (ответом на сообщение)\n"
            "• <code>/admin_remove ID</code>\n"
            "• <code>/admin_remove @username</code>",
            parse_mode="HTML"
        )
        return

    user_id = parsed['user_id']
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


@dp.message(Command("admins"))
async def cmd_admins_public(message: Message):
    """Публичная команда — список администраторов с именами и специализацией"""
    admins_info = get_all_admins_with_info()
    
    if not admins_info:
        await message.answer("👮 Список администраторов пока пуст.")
        return
    
    lines = ["👮 <b>Команда администрации</b>\n"]
    
    # Группируем по ролям (от старшего к младшему)
    role_order = ["admin", "senior_moderator", "moderator", "junior_moderator"]
    grouped: Dict[str, List] = {r: [] for r in role_order}
    
    for admin in admins_info:
        role = admin.get("role") or "moderator"
        if role not in grouped:
            grouped["moderator"] = grouped.get("moderator", [])
            grouped["moderator"].append(admin)
        else:
            grouped[role].append(admin)
    
    for role_key in role_order:
        members = grouped.get(role_key, [])
        if not members:
            continue
        
        role_data = ADMIN_ROLES[role_key]
        # Специализация роли
        perms = []
        if role_data["can_mute"]: perms.append("муты")
        if role_data["can_warn"]: perms.append("варны")
        if role_data["can_ban"]: perms.append("баны")
        if role_data["can_adblock"]: perms.append("adblock")
        spec = ", ".join(perms)
        
        lines.append(f"{role_data['emoji']} <b>{role_data['label']}</b> — <i>{spec}</i>")
        
        for admin in members:
            user_id = admin["user_id"]
            display_name = admin.get("display_name")
            if not display_name:
                try:
                    user = await bot.get_chat(user_id)
                    display_name = user.first_name or user.username or str(user_id)
                except:
                    display_name = str(user_id)
            lines.append(f"   ├ <a href='tg://user?id={user_id}'>{display_name}</a>")
        
        lines.append("")  # пустая строка между группами
    
    await message.answer("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)

@dp.message(Command("admin_warn", "awarn"))
async def cmd_admin_warn(message: Message, command: CommandObject):
    if not await is_owner(message.from_user.id):
        await message.answer("❌ Только владелец может выдавать предупреждения администраторам.")
        return

    parsed = await parse_command_args_v2(message, command, has_time=False)

    if parsed['error']:
        await message.answer(
            f"{parsed['error']}\n\n"
            "📌 <b>Форматы:</b>\n"
            "• <code>/admin_warn [Причина]</code> (ответом на сообщение)\n"
            "• <code>/admin_warn ID [Причина]</code>\n"
            "• <code>/admin_warn @username [Причина]</code>",
            parse_mode="HTML"
        )
        return

    user_id = parsed['user_id']
    reason = parsed['reason']

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

    parsed = await parse_command_args_v2(message, command, has_time=False)

    if parsed['error']:
        await message.answer(
            f"{parsed['error']}\n\n"
            "📌 <b>Форматы:</b>\n"
            "• <code>/admin_unwarn</code> (ответом на сообщение)\n"
            "• <code>/admin_unwarn ID</code>\n"
            "• <code>/admin_unwarn @username</code>",
            parse_mode="HTML"
        )
        return

    user_id = parsed['user_id']
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
    
    # Получаем ID пользователя
    user_id = None
    
    # Проверяем, есть ли ответ на сообщение
    if message.reply_to_message:
        user_id = message.reply_to_message.from_user.id
        logger.info(f"Получен ID из ответа: {user_id}")
    # Иначе проверяем аргументы команды
    elif command.args:
        args = command.args.strip()
        # Пытаемся получить ID из аргументов
        if args.isdigit():
            user_id = int(args)
        elif args.startswith('@'):
            try:
                user = await bot.get_chat(args)
                user_id = user.id
            except Exception as e:
                logger.error(f"Не удалось получить пользователя по юзернейму {args}: {e}")
                await message.answer("❌ Пользователь с таким юзернеймом не найден.")
                return
        else:
            # Возможно это просто ID без @
            try:
                user_id = int(args)
            except ValueError:
                await message.answer("❌ Неверный формат. Используйте: /ban_info @username, /ban_info ID или ответьте на сообщение пользователя.")
                return
    
    if not user_id:
        await message.answer("❌ Укажите пользователя: /ban_info @username, /ban_info ID или ответьте на сообщение пользователя.")
        return
    
    # Проверяем, что это не ID бота (ID ботов обычно начинаются с определенных цифр, но лучше просто проверить)
    try:
        user = await bot.get_chat(user_id)
        if user.type == 'private' and user.is_bot:
            await message.answer("❌ Эта команда предназначена для пользователей, а не для ботов.")
            return
    except Exception as e:
        logger.error(f"Ошибка при получении информации о пользователе: {e}")
        # Продолжаем, даже если не удалось получить информацию
    
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
        # Fallback: пробуем отправить в групповой чат, если ЛС недоступны
        try:
            await bot.send_message(
                CHAT_ID,
                f"⚠️ <b>Жалоба (администраторы недоступны в ЛС)</b>\n\n{report_text}",
                parse_mode="HTML"
            )
            await confirm_msg.edit_text(
                "✅ Администраторы временно недоступны в ЛС. Жалоба опубликована в чате.",
                parse_mode="HTML"
            )
        except Exception:
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
        # Пытаемся ответить на callback сразу, чтобы избежать "query is too old"
        try:
            await callback.answer()
        except Exception:
            pass  # Callback устарел — просто продолжаем обработку без answer

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
        
        # callback.answer уже вызван в начале функции
        
    except Exception as e:
        logger.error(f"Ошибка обработки callback: {e}")
        try:
            await callback.answer("❌ Ошибка при обработке действия")
        except:
            pass

@dp.callback_query(F.data == "complain_admin")
async def start_complaint_callback(callback: types.CallbackQuery, state: FSMContext):
    """Оставлен для обратной совместимости со старыми сообщениями"""
    await callback.answer()
    if is_user_blocked(callback.from_user.id):
        await callback.message.answer("🚫 Вы заблокированы в боте.")
        return

    # Автоматически определяем username пользователя
    user = callback.from_user
    if user.username:
        # Username есть — сохраняем и пропускаем этот шаг
        await state.update_data(username=f"@{user.username}")
        await callback.message.answer(
            "👮 Укажите юзернейм администратора, на которого хотите пожаловаться (например, @admin):",
            parse_mode="HTML"
        )
        await state.set_state(AdminComplaintStates.waiting_for_admin_username)
    else:
        # Username нет — просим установить
        await callback.message.answer(
            "⚠️ <b>У вас не установлен username в Telegram!</b>\n\n"
            "Для подачи жалобы необходим username, чтобы с вами могли связаться.\n\n"
            "Пожалуйста, установите username в настройках Telegram, а затем повторно нажмите кнопку жалобы.\n\n"
            "🔗 <a href='https://okbob.app/blog/telegram-set-username?ysclid=mm0dtsva4d256888466'>Как установить username</a>",
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        await state.clear()

@dp.message(AdminComplaintStates.waiting_for_username)
async def process_username(message: Message, state: FSMContext):
    # Если пользователь отправил не текст (фото, стикер и т.д.) или команду — игнорируем FSM
    if not message.text:
        await message.answer("❌ Пожалуйста, отправьте текстовый юзернейм (например, @username):")
        return

    # Если пользователь отправил команду — выходим из FSM и не обрабатываем
    if message.text.startswith('/'):
        await state.clear()
        return

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
    if not message.text:
        await message.answer("❌ Пожалуйста, отправьте текстовый юзернейм (например, @admin):")
        return

    if message.text.startswith('/'):
        await state.clear()
        return

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
    if not message.text:
        await message.answer("❌ Пожалуйста, отправьте текстовое описание жалобы:")
        return

    if message.text.startswith('/'):
        await state.clear()
        return

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
            reply_markup=get_main_keyboard(message.from_user.id)
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
            reply_markup=get_main_keyboard(message.from_user.id)
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
            reply_markup=get_main_keyboard(message.from_user.id)
        )
    else:
        await message.answer(
            "❌ К сожалению, не удалось отправить вашу жалобу. "
            "Пожалуйста, попробуйте позже или свяжитесь с владельцами напрямую.",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(message.from_user.id)
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
        await message.answer("❌ Нечего отменять.", reply_markup=get_main_keyboard(message.from_user.id))
        return
    
    await state.clear()
    await message.answer(
        "✅ Процесс подачи жалобы отменен.",
        reply_markup=get_main_keyboard(message.from_user.id)
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
    
    try:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=get_complaints_keyboard()
        )
    except Exception:
        await callback.message.answer(
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
        f"🚨 <b>Жалоба #{complaint['id']}</b>\n\n"
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
                 f"👮 <b>Администратор:</b> {complaint['admin_username']}\n"
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
                 f"🆔 <b>ID жалобы:</b> #{complaint_id}\n"
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

# ==================== НОВАЯ СИСТЕМА НАКАЗАНИЙ 2026 ====================

def _looks_like_user_id(arg: str) -> bool:
    """
    Проверяет, похож ли аргумент на ID пользователя или @username,
    а не на временной формат (30m, 2h, 1d, 1w).
    """
    if arg.startswith('@'):
        return True
    # Если это чисто цифры — это ID пользователя (временные форматы содержат буквы)
    if arg.isdigit():
        return True
    return False


async def parse_command_args_v2(message: Message, command: CommandObject, required_args: int = 0, has_time: bool = False) -> Optional[Dict]:
    """
    Универсальный парсер аргументов для команд наказаний.
    
    Форматы:
    - С reply: /warn [Причина]
                /mute [Причина]
                /tmute [Время] [Причина]
                /unmute, /unwarn, /unban
    - Без reply: /warn [ID] [Причина]
                 /mute [ID] [Причина]
                 /tmute [ID] [Время] [Причина]
                 /unmute [ID], /unwarn [ID], /unban [ID]
    
    Исключение: если reply есть, но первый аргумент выглядит как ID/username,
    используем ID из аргументов, а не из reply.
    """
    result = {
        'user_id': None,
        'reason': 'Не указана',
        'duration': None,
        'error': None
    }
    
    logger.info(f"=== Парсинг команды: {command.command} ===")
    logger.info(f"Есть reply: {message.reply_to_message is not None}")
    logger.info(f"Аргументы: '{command.args}'")
    
    args = command.args.strip().split() if command.args else []
    
    # Определяем: используем ли мы reply или аргументы для получения пользователя
    use_reply = False
    if message.reply_to_message:
        # Если аргументов нет, или первый аргумент НЕ похож на ID/username — берём из reply
        if not args or not _looks_like_user_id(args[0]):
            use_reply = True
    
    if use_reply:
        # СЛУЧАЙ 1: Ответ на сообщение — пользователь из reply
        result['user_id'] = message.reply_to_message.from_user.id
        logger.info(f"ID из reply: {result['user_id']}")
        
        if not args:
            # Нет аргументов — причина/время не указаны
            if has_time:
                result['error'] = "❌ Укажите время! Используйте: 30m, 2h, 1d, 1w"
                return result
            return result
        
        if has_time:
            # Для команд с временем (reply): /tmute [Время] [Причина]
            time_str = args[0]
            result['duration'] = parse_time(time_str)
            if not result['duration']:
                result['error'] = f"❌ Неверный формат времени: {time_str}. Используйте: 30m, 2h, 1d, 1w"
                return result
            result['reason'] = ' '.join(args[1:]) if len(args) > 1 else 'Не указана'
        else:
            # Для команд без времени (reply): /warn [Причина]
            result['reason'] = command.args.strip() or 'Не указана'
        
        logger.info(f"Причина: '{result['reason']}'")
        return result
    
    # СЛУЧАЙ 2: Пользователь из аргументов (нет reply или первый аргумент — ID/username)
    if not args:
        result['error'] = "❌ Не указан пользователь!"
        return result
    
    # Получаем ID пользователя из первого аргумента
    user_identifier = args[0]
    
    if user_identifier.isdigit():
        result['user_id'] = int(user_identifier)
    elif user_identifier.startswith('@'):
        try:
            user = await bot.get_chat(user_identifier)
            result['user_id'] = user.id
        except Exception as e:
            logger.error(f"Ошибка получения пользователя по юзернейму {user_identifier}: {e}")
            result['error'] = f"❌ Пользователь {user_identifier} не найден"
            return result
    else:
        try:
            result['user_id'] = int(user_identifier)
        except ValueError:
            result['error'] = f"❌ Неверный формат идентификатора: {user_identifier}"
            return result
    
    logger.info(f"ID из аргументов: {result['user_id']}")
    
    # Парсим остальные аргументы
    remaining_args = args[1:]
    
    if has_time:
        # Формат: /tmute [ID] [Время] [Причина]
        if not remaining_args:
            result['error'] = "❌ Укажите время! Формат: /tmute ID 1d [Причина]"
            return result
        
        time_str = remaining_args[0]
        result['duration'] = parse_time(time_str)
        if not result['duration']:
            result['error'] = f"❌ Неверный формат времени: {time_str}. Используйте: 30m, 2h, 1d, 1w"
            return result
        
        result['reason'] = ' '.join(remaining_args[1:]) if len(remaining_args) > 1 else 'Не указана'
    else:
        # Формат: /warn [ID] [Причина]
        result['reason'] = ' '.join(remaining_args) if remaining_args else 'Не указана'
    
    logger.info(f"Причина: '{result['reason']}'")
    return result

async def check_admin_rights_v2(message: Message, user_id: int, target_id: int) -> Tuple[bool, str]:
    """Проверяет права администратора и цели наказания"""
    # Проверяем права администратора у инициатора
    if not await is_chat_admin(user_id, message.chat.id):
        return False, "admin"
    
    # Проверяем, не является ли цель администратором
    if await is_chat_admin(target_id, message.chat.id):
        return False, "target_admin"
    
    return True, "ok"

async def auto_punish_non_admin(message: Message):
    """Автоматическое наказание для не-администраторов, пытающихся использовать команды"""
    await delete_message(message.chat.id, message.message_id)
    await mute_user(
        message.chat.id,
        message.from_user.id,
        MUTE_DURATION,
        "Использование команд бота",
        is_auto=True,
        message_thread_id=message.message_thread_id
    )
    
    # Отправляем сообщение о наказании
    user_mention = await get_user_mention(message.from_user.id)
    await bot.send_message(
        message.chat.id,
        f"🤡 {user_mention} получил мут на {MUTE_DURATION_DAYS} день за использование команд бота без прав!",
        parse_mode="HTML",
        message_thread_id=message.message_thread_id
    )

# ==================== КОМАНДЫ НАКАЗАНИЙ ====================

@dp.message(Command("warn"))
async def cmd_warn_v2(message: Message, command: CommandObject):
    """Выдача предупреждения"""
    # Проверка прав
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        await auto_punish_non_admin(message)
        return
    
    # Проверка роли (мл. модератор не может делать варны)
    if not admin_can(message.from_user.id, "can_warn"):
        await message.answer(
            "❌ Ваша роль (<b>Мл. модератор</b>) не позволяет выдавать предупреждения.\n"
            "Минимальная роль для /warn — <b>Модератор</b>.",
            parse_mode="HTML"
        )
        return
    
    # Парсим аргументы
    parsed = await parse_command_args_v2(message, command, has_time=False)
    
    if parsed['error']:
        await message.answer(
            f"{parsed['error']}\n\n"
            "📌 <b>Форматы:</b>\n"
            "• <code>/warn</code> (ответом на сообщение)\n"
            "• <code>/warn ID Причина</code>\n"
            "• <code>/warn @username Причина</code>",
            parse_mode="HTML"
        )
        return
    
    if not parsed['user_id']:
        await message.answer("❌ Не удалось определить пользователя")
        return
    
    # Проверяем права
    check_result, check_type = await check_admin_rights_v2(message, message.from_user.id, parsed['user_id'])
    if not check_result:
        if check_type == "target_admin":
            await message.answer("❌ Нельзя выдавать предупреждения администраторам через эту команду. Используйте /admin_warn")
        else:
            await auto_punish_non_admin(message)
        return
    
    # Выдаем предупреждение
    await warn_user(message.chat.id, parsed['user_id'], parsed['reason'], message.message_thread_id)

@dp.message(Command("unwarn"))
async def cmd_unwarn_v2(message: Message, command: CommandObject):
    """Снятие последнего предупреждения"""
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        await auto_punish_non_admin(message)
        return
    
    parsed = await parse_command_args_v2(message, command, has_time=False)
    
    if parsed['error']:
        await message.answer(
            f"{parsed['error']}\n\n"
            "📌 <b>Форматы:</b>\n"
            "• <code>/unwarn</code> (ответом на сообщение)\n"
            "• <code>/unwarn ID</code>\n"
            "• <code>/unwarn @username</code>",
            parse_mode="HTML"
        )
        return
    
    if not parsed['user_id']:
        await message.answer("❌ Не удалось определить пользователя")
        return
    
    # Проверяем права
    check_result, check_type = await check_admin_rights_v2(message, message.from_user.id, parsed['user_id'])
    if not check_result:
        if check_type == "target_admin":
            await message.answer("❌ Нельзя снимать предупреждения у администраторов")
        else:
            await auto_punish_non_admin(message)
        return
    
    # Получаем все активные варны
    warns = get_user_warns(parsed['user_id'], message.chat.id)
    
    if not warns:
        user_mention = await get_user_mention(parsed['user_id'])
        await message.answer(f"✅ У пользователя {user_mention} нет активных предупреждений", parse_mode="HTML")
        return
    
    # Удаляем последнее предупреждение
    last_warn = max(warns, key=lambda x: x['id'])
    remove_warn(last_warn['id'])
    
    user_mention = await get_user_mention(parsed['user_id'])
    admin_mention = await get_user_mention(message.from_user.id)
    remaining = len(warns) - 1
    
    await message.answer(
        f"✅ <b>Предупреждение снято</b>\n\n"
        f"👤 <b>Пользователь:</b> {user_mention}\n"
        f"👮 <b>Снял:</b> {admin_mention}\n"
        f"📊 <b>Осталось предупреждений:</b> {remaining}",
        parse_mode="HTML",
    )

@dp.message(Command("mute"))
async def cmd_mute_v2(message: Message, command: CommandObject):
    """Мут на 1 день"""
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        await auto_punish_non_admin(message)
        return
    
    # Проверка роли (все роли кроме полного отсутствия могут мутить)
    if not admin_can(message.from_user.id, "can_mute"):
        await message.answer("❌ У вас нет прав для выдачи мута.", parse_mode="HTML")
        return
    
    parsed = await parse_command_args_v2(message, command, has_time=False)
    
    if parsed['error']:
        await message.answer(
            f"{parsed['error']}\n\n"
            f"📌 <b>Форматы (мут на {MUTE_DURATION_DAYS} день):</b>\n"
            "• <code>/mute</code> (ответом на сообщение)\n"
            "• <code>/mute ID Причина</code>\n"
            "• <code>/mute @username Причина</code>",
            parse_mode="HTML"
        )
        return
    
    if not parsed['user_id']:
        await message.answer("❌ Не удалось определить пользователя")
        return
    
    check_result, check_type = await check_admin_rights_v2(message, message.from_user.id, parsed['user_id'])
    if not check_result:
        if check_type == "target_admin":
            await message.answer("❌ Нельзя мутить администраторов")
        else:
            await auto_punish_non_admin(message)
        return
    
    await mute_user(message.chat.id, parsed['user_id'], MUTE_DURATION, parsed['reason'], message_thread_id=message.message_thread_id)

@dp.message(Command("tmute"))
async def cmd_tmute_v2(message: Message, command: CommandObject):
    """Мут на указанное время"""
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        await auto_punish_non_admin(message)
        return
    
    if not admin_can(message.from_user.id, "can_mute"):
        await message.answer("❌ У вас нет прав для выдачи мута.", parse_mode="HTML")
        return
    
    parsed = await parse_command_args_v2(message, command, has_time=True)
    
    if parsed['error']:
        await message.answer(
            f"{parsed['error']}\n\n"
            "📌 <b>Форматы (время: 30m, 2h, 1d, 1w):</b>\n"
            "• <code>/tmute 1d Причина</code> (ответом на сообщение)\n"
            "• <code>/tmute ID 2h Причина</code>\n"
            "• <code>/tmute @username 30m Причина</code>",
            parse_mode="HTML"
        )
        return
    
    if not parsed['user_id']:
        await message.answer("❌ Не удалось определить пользователя")
        return
    
    check_result, check_type = await check_admin_rights_v2(message, message.from_user.id, parsed['user_id'])
    if not check_result:
        if check_type == "target_admin":
            await message.answer("❌ Нельзя мутить администраторов")
        else:
            await auto_punish_non_admin(message)
        return
    
    await mute_user(message.chat.id, parsed['user_id'], parsed['duration'], parsed['reason'], message_thread_id=message.message_thread_id)

@dp.message(Command("unmute"))
async def cmd_unmute_v2(message: Message, command: CommandObject):
    """Снятие мута"""
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        await auto_punish_non_admin(message)
        return
    
    parsed = await parse_command_args_v2(message, command, has_time=False)
    
    if parsed['error']:
        await message.answer(
            f"{parsed['error']}\n\n"
            "📌 <b>Форматы:</b>\n"
            "• <code>/unmute</code> (ответом на сообщение)\n"
            "• <code>/unmute ID</code>\n"
            "• <code>/unmute @username</code>",
            parse_mode="HTML"
        )
        return
    
    if not parsed['user_id']:
        await message.answer("❌ Не удалось определить пользователя")
        return
    
    # Снимаем мут
    await unmute_user(message.chat.id, parsed['user_id'], message.message_thread_id)
    
    # Деактивируем в БД
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE mutes SET is_active = FALSE WHERE user_id = ? AND chat_id = ? AND is_active = TRUE",
        (parsed['user_id'], message.chat.id)
    )
    conn.commit()
    conn.close()

@dp.message(Command("ban"))
async def cmd_ban_v2(message: Message, command: CommandObject):
    """Бан навсегда"""
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        await auto_punish_non_admin(message)
        return
    
    # Проверка роли (мл. модератор и модератор не могут банить)
    if not admin_can(message.from_user.id, "can_ban"):
        role = get_admin_role(message.from_user.id)
        role_label = ADMIN_ROLES.get(role, {}).get("label", "вашей роли") if role else "вашей роли"
        await message.answer(
            f"❌ Роль <b>{role_label}</b> не позволяет выдавать баны.\n"
            f"Минимальная роль для /ban — <b>Ст. модератор</b>.",
            parse_mode="HTML"
        )
        return
    
    parsed = await parse_command_args_v2(message, command, has_time=False)
    
    if parsed['error']:
        await message.answer(
            f"{parsed['error']}\n\n"
            "📌 <b>Форматы (бан навсегда):</b>\n"
            "• <code>/ban</code> (ответом на сообщение)\n"
            "• <code>/ban ID Причина</code>\n"
            "• <code>/ban @username Причина</code>",
            parse_mode="HTML"
        )
        return
    
    if not parsed['user_id']:
        await message.answer("❌ Не удалось определить пользователя")
        return
    
    check_result, check_type = await check_admin_rights_v2(message, message.from_user.id, parsed['user_id'])
    if not check_result:
        if check_type == "target_admin":
            await message.answer("❌ Нельзя банить администраторов")
        else:
            await auto_punish_non_admin(message)
        return
    
    await ban_user(message.chat.id, parsed['user_id'], None, parsed['reason'], message_thread_id=message.message_thread_id)

@dp.message(Command("tban"))
async def cmd_tban_v2(message: Message, command: CommandObject):
    """Бан на указанное время"""
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        await auto_punish_non_admin(message)
        return
    
    if not admin_can(message.from_user.id, "can_ban"):
        role = get_admin_role(message.from_user.id)
        role_label = ADMIN_ROLES.get(role, {}).get("label", "вашей роли") if role else "вашей роли"
        await message.answer(
            f"❌ Роль <b>{role_label}</b> не позволяет выдавать баны.\n"
            f"Минимальная роль для /tban — <b>Ст. модератор</b>.",
            parse_mode="HTML"
        )
        return
    
    parsed = await parse_command_args_v2(message, command, has_time=True)
    
    if parsed['error']:
        await message.answer(
            f"{parsed['error']}\n\n"
            "📌 <b>Форматы (время: 30m, 2h, 1d, 1w):</b>\n"
            "• <code>/tban 1d Причина</code> (ответом на сообщение)\n"
            "• <code>/tban ID 2h Причина</code>\n"
            "• <code>/tban @username 30m Причина</code>",
            parse_mode="HTML"
        )
        return
    
    if not parsed['user_id']:
        await message.answer("❌ Не удалось определить пользователя")
        return
    
    check_result, check_type = await check_admin_rights_v2(message, message.from_user.id, parsed['user_id'])
    if not check_result:
        if check_type == "target_admin":
            await message.answer("❌ Нельзя банить администраторов")
        else:
            await auto_punish_non_admin(message)
        return
    
    await ban_user(message.chat.id, parsed['user_id'], parsed['duration'], parsed['reason'], message_thread_id=message.message_thread_id)

@dp.message(Command("unban"))
async def cmd_unban_v2(message: Message, command: CommandObject):
    """Снятие бана"""
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        await auto_punish_non_admin(message)
        return
    
    parsed = await parse_command_args_v2(message, command, has_time=False)
    
    if parsed['error']:
        await message.answer(
            f"{parsed['error']}\n\n"
            "📌 <b>Форматы:</b>\n"
            "• <code>/unban</code> (ответом на сообщение)\n"
            "• <code>/unban ID</code>\n"
            "• <code>/unban @username</code>",
            parse_mode="HTML"
        )
        return
    
    if not parsed['user_id']:
        await message.answer("❌ Не удалось определить пользователя")
        return
    
    # Снимаем бан
    await unban_user(message.chat.id, parsed['user_id'], message.message_thread_id)
    
    # Деактивируем в БД
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE bans SET is_active = FALSE WHERE user_id = ? AND chat_id = ? AND is_active = TRUE",
        (parsed['user_id'], message.chat.id)
    )
    conn.commit()
    conn.close()

# ==================== КОНЕЦ НОВОЙ СИСТЕМЫ ====================

# Обработчики для объявлений
@dp.message(Command("public"))
async def cmd_public(message: Message):
    """Команда для управления объявлениями"""
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("❌ Эта команда работает только в личных сообщениях с ботом.")
        return
    
    # Проверяем, не заблокирован ли пользователь
    if is_user_blocked(message.from_user.id):
        await message.answer(
            "🚫 <b>Вы заблокированы в боте</b>\n\n"
            "Вы не можете использовать функции бота из-за нарушений правил.",
            parse_mode="HTML"
        )
        return

    # Проверяем, прошёл ли пользователь онбординг
    if not is_onboarding_completed(message.from_user.id):
        builder = InlineKeyboardBuilder()
        bot_info = await bot.get_me()
        builder.row(InlineKeyboardButton(
            text="📚 Пройти обучение",
            url=f"https://t.me/{bot_info.username}?start=onboarding"
        ))
        await message.answer(
            "⚠️ <b>Сначала пройди обучение!</b>\n\n"
            "Публикация объявлений доступна только после прохождения обязательного обучения.\n\n"
            "Это займёт 2-3 минуты 👇",
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )
        return
    
    welcome_text = """
    📢 <b>Управление объявлениями</b>
    
    Здесь вы можете создавать и публиковать объявления в тему «Барахолка».
    
    <b>Правила публикации:</b>
    • Максимум {MAX_ADS_PER_DAY} объявлений в день
    • Интервал между публикациями: {MIN_AD_INTERVAL_HOURS} часа
    • За нарушение лимитов - мут на {MUTE_DURATION_DAYS} день
    
    Выберите действие:
    """.format(
        MAX_ADS_PER_DAY=MAX_ADS_PER_DAY,
        MIN_AD_INTERVAL_HOURS=MIN_AD_INTERVAL_HOURS,
        MUTE_DURATION_DAYS=MUTE_DURATION_DAYS
    )
    
    await message.answer(welcome_text, parse_mode="HTML", reply_markup=get_public_menu_keyboard())

@dp.callback_query(F.data == "back_to_public_menu")
async def back_to_public_menu(callback: types.CallbackQuery):
    """Возврат в главное меню публикаций"""
    await callback.answer()
    await callback.message.edit_text(
        "📢 <b>Управление объявлениями</b>\n\nВыберите действие:",
        parse_mode="HTML",
        reply_markup=get_public_menu_keyboard()
    )

@dp.callback_query(F.data.startswith("back_from_reviews:"))
async def back_from_reviews(callback: types.CallbackQuery, state: FSMContext):
    """Возврат из отзывов в зависимости от контекста"""
    parts = callback.data.split(":")
    if len(parts) < 2:
        await callback.answer("❌ Неверный формат данных")
        return
    
    user_id = int(parts[1])
    
    # Проверяем, откуда пришел запрос
    if callback.message.chat.type == ChatType.PRIVATE:
        # В ЛС - возвращаемся в главное меню
        await callback.message.edit_text(
            "📢 <b>Управление объявлениями</b>\n\nВыберите действие:",
            parse_mode="HTML",
            reply_markup=get_public_menu_keyboard()
        )
    else:
        # В чате - удаляем сообщение с отзывами
        await callback.message.delete()
    
    await callback.answer()

async def show_reviews_in_private(message: Message, seller_id: int):
    """Показывает отзывы о продавце в личных сообщениях"""
    
    # Получаем статистику
    avg_rating, review_count = get_user_rating_stats(seller_id)
    
    # Получаем отзывы
    reviews = get_user_reviews(seller_id)
    
    # Получаем информацию о продавце
    try:
        seller = await bot.get_chat(seller_id)
        seller_name = seller.first_name or seller.username or str(seller_id)
        seller_username = f"@{seller.username}" if seller.username else str(seller_id)
    except:
        seller_name = str(seller_id)
        seller_username = str(seller_id)
    
    # Формируем текст
    stars = "⭐" * int(avg_rating) + "½" * (avg_rating % 1 >= 0.5)
    text = (
        f"👤 <b>Профиль продавца {seller_name}</b>\n\n"
        f"📊 <b>Рейтинг:</b> {avg_rating} {stars}\n"
        f"📝 <b>Всего отзывов:</b> {review_count}\n\n"
    )
    
    if reviews:
        text += "<b>Отзывы:</b>\n"
        for i, review in enumerate(reviews[:10], 1):  # Показываем последние 10 отзывов
            try:
                from_user = await bot.get_chat(review['from_user_id'])
                from_name = from_user.first_name or from_user.username or str(review['from_user_id'])
            except:
                from_name = str(review['from_user_id'])
            
            stars_review = "⭐" * review['rating']
            text += f"\n{i}. {stars_review} от {from_name}:\n"
            text += f"   {review['review_text']}\n"
            text += f"   🕐 {review['created_at'][:16]}\n"
    else:
        text += "📭 <i>У продавца пока нет отзывов</i>"
    
    # Создаем клавиатуру
    keyboard = InlineKeyboardBuilder()
    
    # Проверяем, может ли пользователь оставить отзыв
    if message.from_user.id != seller_id:
        existing = get_user_review_from_user(message.from_user.id, seller_id)
        if not existing:
            keyboard.row(
                InlineKeyboardButton(
                    text="✏️ Оставить отзыв", 
                    callback_data=f"leave_review_ls:{seller_id}"
                )
            )
    
    # Кнопка возврата в главное меню
    keyboard.row(
        InlineKeyboardButton(
            text="📢 Главное меню", 
            callback_data="back_to_public_menu"
        )
    )
    
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard.as_markup())

@dp.callback_query(F.data.startswith("leave_review:"))
async def start_leave_review(callback: types.CallbackQuery, state: FSMContext):
    """Начинает процесс оставления отзыва"""
    target_user_id = int(callback.data.split(":")[1])
    
    if target_user_id == callback.from_user.id:
        await callback.answer("❌ Нельзя оставить отзыв о себе")
        return
    
    # Проверяем, не оставлял ли уже отзыв
    existing = get_user_review_from_user(callback.from_user.id, target_user_id)
    if existing:
        await callback.answer("❌ Вы уже оставляли отзыв этому пользователю")
        return
    
    await state.update_data(target_user_id=target_user_id)
    await callback.message.edit_text(
        "⭐ <b>Оцените пользователя</b>\n\nВыберите оценку от 1 до 5:",
        parse_mode="HTML",
        reply_markup=get_rating_keyboard()
    )
    await state.set_state(ReviewStates.waiting_for_rating)
    await callback.answer()

@dp.callback_query(F.data.startswith("leave_review_ls:"))
async def start_leave_review_ls(callback: types.CallbackQuery, state: FSMContext):
    """Начинает процесс оставления отзыва в ЛС"""
    target_user_id = int(callback.data.split(":")[1])
    
    if target_user_id == callback.from_user.id:
        await callback.answer("❌ Нельзя оставить отзыв о себе")
        return
    
    # Проверяем, не оставлял ли уже отзыв
    existing = get_user_review_from_user(callback.from_user.id, target_user_id)
    if existing:
        await callback.answer("❌ Вы уже оставляли отзыв этому пользователю")
        return
    
    await state.update_data(target_user_id=target_user_id)
    await callback.message.edit_text(
        "⭐ <b>Оцените продавца</b>\n\nВыберите оценку от 1 до 5:",
        parse_mode="HTML",
        reply_markup=get_rating_keyboard()
    )
    await state.set_state(ReviewStates.waiting_for_rating)
    await callback.answer()

@dp.callback_query(F.data.startswith("rating:"), ReviewStates.waiting_for_rating)
async def process_review_rating(callback: types.CallbackQuery, state: FSMContext):
    """Обрабатывает выбор оценки"""
    rating = int(callback.data.split(":")[1])
    
    await state.update_data(rating=rating)
    await callback.message.edit_text(
        f"✏️ <b>Напишите отзыв</b>\n\n"
        f"Вы выбрали оценку: {'⭐' * rating}\n\n"
        f"Опишите ваше впечатление от общения с пользователем:",
        parse_mode="HTML"
    )
    await state.set_state(ReviewStates.waiting_for_review_text)
    await callback.answer()

@dp.message(ReviewStates.waiting_for_review_text)
async def process_review_text(message: Message, state: FSMContext):
    """Обрабатывает текст отзыва"""
    review_text = message.text.strip()
    
    if len(review_text) < 10:
        await message.answer("❌ Отзыв слишком короткий. Напишите более развернутый отзыв (минимум 10 символов):")
        return
    
    data = await state.get_data()
    target_user_id = data['target_user_id']
    rating = data['rating']
    
    # Сохраняем отзыв
    add_user_review(message.from_user.id, target_user_id, rating, review_text)
    
    # Получаем обновленную статистику
    avg_rating, review_count = get_user_rating_stats(target_user_id)
    
    try:
        target_user = await bot.get_chat(target_user_id)
        target_name = target_user.first_name or target_user.username or str(target_user_id)
    except:
        target_name = str(target_user_id)
    
    await message.answer(
        f"✅ <b>Отзыв оставлен!</b>\n\n"
        f"👤 Пользователь: {target_name}\n"
        f"⭐ Оценка: {'⭐' * rating}\n"
        f"📊 Новый рейтинг: {avg_rating} ({review_count} отзывов)\n\n"
        f"<i>Спасибо за ваш отзыв!</i>",
        parse_mode="HTML"
    )
    
    # Возвращаемся к профилю
    await show_user_reviews_after_review(message, target_user_id)
    await state.clear()

async def show_user_reviews_after_review(message: Message, target_user_id: int):
    """Показывает профиль после оставления отзыва"""
    avg_rating, review_count = get_user_rating_stats(target_user_id)
    reviews = get_user_reviews(target_user_id)
    
    if target_user_id == message.from_user.id:
        title = "👤 <b>Ваш профиль</b>"
    else:
        try:
            user = await bot.get_chat(target_user_id)
            name = user.first_name or user.username or str(target_user_id)
            title = f"👤 <b>Профиль пользователя {name}</b>"
        except:
            title = f"👤 <b>Профиль пользователя</b>"
    
    stars = "⭐" * int(avg_rating) + "½" * (avg_rating % 1 >= 0.5)
    text = (
        f"{title}\n\n"
        f"📊 <b>Рейтинг:</b> {avg_rating} {stars}\n"
        f"📝 <b>Всего отзывов:</b> {review_count}\n\n"
    )
    
    if reviews:
        text += "<b>Последние отзывы:</b>\n"
        for review in reviews[:5]:
            try:
                from_user = await bot.get_chat(review['from_user_id'])
                from_name = from_user.first_name or from_user.username or str(review['from_user_id'])
            except:
                from_name = str(review['from_user_id'])
            
            stars_review = "⭐" * review['rating']
            text += f"\n• {stars_review} от {from_name}:\n  {review['review_text'][:50]}...\n"
    
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=get_user_reviews_keyboard(target_user_id, message.from_user.id)
    )

@dp.callback_query(F.data == "cancel_review")
async def cancel_review(callback: types.CallbackQuery, state: FSMContext):
    """Отменяет процесс оставления отзыва"""
    await state.clear()
    await callback.message.edit_text(
        "❌ Отзыв отменен.",
        parse_mode="HTML",
        reply_markup=get_public_menu_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "back_to_ad_from_reviews")
async def back_to_ad_from_reviews(callback: types.CallbackQuery, state: FSMContext):
    """Возврат к объявлению из отзывов"""
    data = await state.get_data()
    target_user_id = data.get('review_target_user')
    previous_message_id = data.get('previous_message_id')
    
    if previous_message_id:
        # Пытаемся вернуться к предыдущему сообщению
        try:
            await callback.message.delete()
        except:
            pass
        
        # Показываем профиль без редактирования
        await show_user_reviews(callback, state)
    else:
        await callback.answer()
        await callback.message.edit_text(
            "📢 <b>Управление объявлениями</b>",
            parse_mode="HTML",
            reply_markup=get_public_menu_keyboard()
        )

@dp.message(Command("profile"))
async def cmd_profile(message: Message):
    """Команда для просмотра своего профиля"""
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("❌ Эта команда работает только в личных сообщениях с ботом.")
        return
    
    user_id = message.from_user.id
    
    # Получаем статистику
    avg_rating, review_count = get_user_rating_stats(user_id)
    
    # Получаем отзывы
    reviews = get_user_reviews(user_id)
    
    # Получаем статистику объявлений
    ads = get_user_ads(user_id)
    published_ads = [ad for ad in ads if ad['status'] == 'published']
    draft_ads = [ad for ad in ads if ad['status'] == 'draft']
    
    # Проверяем лимиты публикаций
    cooldown_text = "✅ Можно публиковать" if can_publish else f"⏳ Следующее через {next_available.strftime('%H:%M')}"
    
    stars = "⭐" * int(avg_rating) + "½" * (avg_rating % 1 >= 0.5)
    text = (
        f"👤 <b>Ваш профиль</b>\n\n"
        f"📊 <b>Рейтинг:</b> {avg_rating} {stars}\n"
        f"📝 <b>Всего отзывов:</b> {review_count}\n\n"
        f"📦 <b>Объявления:</b>\n"
        f"• 📝 Черновиков: {len(draft_ads)}\n"
        f"• ✅ Опубликовано: {len(published_ads)}\n"
        f"• {cooldown_text}\n\n"
    )
    
    if reviews:
        text += "<b>Ваши последние отзывы:</b>\n"
        for review in reviews[:3]:
            try:
                from_user = await bot.get_chat(review['from_user_id'])
                from_name = from_user.first_name or from_user.username or str(review['from_user_id'])
            except:
                from_name = str(review['from_user_id'])
            
            stars_review = "⭐" * review['rating']
            text += f"\n• {stars_review} от {from_name}:\n  {review['review_text'][:50]}...\n"
    else:
        text += "📭 <i>У вас пока нет отзывов</i>"
    
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="📢 Управление объявлениями", callback_data="back_to_public_menu"))
    
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard.as_markup())

@dp.callback_query(F.data == "my_profile")
async def my_profile_callback(callback: types.CallbackQuery):
    """Обработчик для кнопки Мой профиль"""
    await callback.answer()
    
    user_id = callback.from_user.id
    
    # Получаем статистику
    avg_rating, review_count = get_user_rating_stats(user_id)
    
    # Получаем отзывы
    reviews = get_user_reviews(user_id)
    
    # Получаем статистику объявлений
    ads = get_user_ads(user_id)
    published_ads = [ad for ad in ads if ad['status'] == 'published']
    draft_ads = [ad for ad in ads if ad['status'] == 'draft']
    
    # Проверяем лимиты публикаций
    cooldown_text = "✅ Можно публиковать" if can_publish else f"⏳ Следующее через {next_available.strftime('%H:%M')}"
    
    stars = "⭐" * int(avg_rating) + "½" * (avg_rating % 1 >= 0.5)
    text = (
        f"👤 <b>Ваш профиль</b>\n\n"
        f"📊 <b>Рейтинг:</b> {avg_rating} {stars}\n"
        f"📝 <b>Всего отзывов:</b> {review_count}\n\n"
        f"📦 <b>Объявления:</b>\n"
        f"• 📝 Черновиков: {len(draft_ads)}\n"
        f"• ✅ Опубликовано: {len(published_ads)}\n"
        f"• {cooldown_text}\n\n"
    )
    
    if reviews:
        text += "<b>Ваши последние отзывы:</b>\n"
        for review in reviews[:3]:
            try:
                from_user = await bot.get_chat(review['from_user_id'])
                from_name = from_user.first_name or from_user.username or str(review['from_user_id'])
            except:
                from_name = str(review['from_user_id'])
            
            stars_review = "⭐" * review['rating']
            text += f"\n• {stars_review} от {from_name}:\n  {review['review_text'][:50]}...\n"
    else:
        text += "📭 <i>У вас пока нет отзывов</i>"
    
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="📢 Управление объявлениями", callback_data="back_to_public_menu"))
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard.as_markup())

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

    # Блокируем сообщения от пользователей, не прошедших онбординг
    # (кроме администраторов)
    if not await is_chat_admin(user_id, message.chat.id) and not is_onboarding_completed(user_id):
        await delete_message(message.chat.id, message.message_id)
        try:
            bot_info = await bot.get_me()
            kb = InlineKeyboardBuilder()
            kb.row(InlineKeyboardButton(
                text="📚 Пройти обучение",
                url=f"https://t.me/{bot_info.username}?start=onboarding"
            ))
            reminder = await message.answer(
                f"⚠️ {await get_user_mention(user_id)}, чтобы писать в чат — сначала пройди обязательное обучение!\n\n"
                f"Нажми кнопку ниже 👇",
                parse_mode="HTML",
                reply_markup=kb.as_markup()
            )
            await asyncio.sleep(15)
            await delete_message(message.chat.id, reminder.message_id)
        except Exception as e:
            logger.error(f"Ошибка напоминания об онбординге: {e}")
        return

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

        # Журнал модерации
        user_mention_log = await get_user_mention(user_id)
        await send_to_mod_log(
            f"🤬 УСВ (запрещённое слово)\n\n"
            f"👤 <b>Пользователь:</b> {user_mention_log}\n"
            f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
            f"🔤 <b>Слово:</b> <code>{found_word}</code>\n"
            f"🕐 <b>Время:</b> {get_moscow_time().strftime('%d.%m.%Y %H:%M:%S')}"
        )
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

# ============================================================
#             ОНБОРДИНГ — ОБУЧАЮЩИЙ ДИАЛОГ ДЛЯ НОВИЧКОВ
# ============================================================

def is_onboarding_completed(user_id: int) -> bool:
    """Проверяет, прошёл ли пользователь онбординг"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT is_completed FROM onboarding WHERE user_id = ?",
        (user_id,)
    )
    row = cursor.fetchone()
    conn.close()
    return bool(row and row[0])

def is_onboarding_started(user_id: int) -> bool:
    """Проверяет, начат ли онбординг"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM onboarding WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def start_onboarding_db(user_id: int):
    """Создаёт запись онбординга в БД"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO onboarding (user_id, started_at) VALUES (?, ?)",
        (user_id, datetime.now())
    )
    conn.commit()
    conn.close()

def complete_onboarding_db(user_id: int):
    """Отмечает онбординг как завершённый"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE onboarding SET is_completed = TRUE, completed_at = ? WHERE user_id = ?",
        (datetime.now(), user_id)
    )
    conn.commit()
    conn.close()

def fail_onboarding_db(user_id: int):
    """Фиксирует провал онбординга и устанавливает кулдаун 12 часов"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """UPDATE onboarding
           SET failed_at = ?, fail_count = COALESCE(fail_count, 0) + 1,
               started_at = CURRENT_TIMESTAMP
           WHERE user_id = ?""",
        (datetime.now(), user_id)
    )
    conn.commit()
    conn.close()

def get_onboarding_cooldown_remaining(user_id: int) -> Optional[timedelta]:
    """Возвращает оставшееся время кулдауна или None если кулдауна нет"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT failed_at FROM onboarding WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if not row or not row[0]:
        return None
    failed_at = datetime.fromisoformat(str(row[0]))
    cooldown_end = failed_at + timedelta(hours=12)
    remaining = cooldown_end - datetime.now()
    return remaining if remaining.total_seconds() > 0 else None

def reset_onboarding_for_retry(user_id: int):
    """Сбрасывает состояние для повторного прохождения (оставляет failed_at и fail_count)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE onboarding SET failed_at = NULL, started_at = ? WHERE user_id = ?",
        (datetime.now(), user_id)
    )
    conn.commit()
    conn.close()

def get_onboarding_keyboard_start() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✅ Да, поехали!", callback_data="onb_start"))
    builder.row(InlineKeyboardButton(text="⏳ Позже", callback_data="onb_later"))
    return builder.as_markup()

def get_onboarding_next_keyboard(next_step: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="➡️ Продолжить", callback_data=next_step))
    return builder.as_markup()

# Множество user_id, для которых онбординг уже запущен в текущей сессии (защита от дублей)
_onboarding_active: Set[int] = set()

# Обработчик изменений статуса бота (my_chat_member) — устраняет "is not handled"
@dp.my_chat_member()
async def handle_my_chat_member(update: ChatMemberUpdated):
    """Обрабатывает изменения статуса самого бота в чатах"""
    new_status = update.new_chat_member.status
    if new_status == ChatMemberStatus.MEMBER:
        logger.info(f"Бот добавлен в чат {update.chat.id} ({update.chat.title})")
    elif new_status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED]:
        logger.info(f"Бот удалён из чата {update.chat.id} ({update.chat.title})")
    elif new_status == ChatMemberStatus.ADMINISTRATOR:
        logger.info(f"Бот получил права администратора в чате {update.chat.id}")

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
        
        user = update.new_chat_member.user
        user_id = user.id

        # Пропускаем ботов
        if user.is_bot:
            return

        # Защита от дублирующихся событий: если онбординг уже запущен в этой сессии — пропускаем
        if user_id in _onboarding_active:
            return
        # Если онбординг уже начат (запись в БД есть) — пропускаем повторный запуск
        if is_onboarding_started(user_id) and not is_onboarding_completed(user_id):
            return

        # Проверяем, не проходил ли уже онбординг (защита от повторного прохождения)
        if is_onboarding_completed(user_id):
            # Просто краткое приветствие для тех, кто вернулся
            welcome_msg = await bot.send_message(
                update.chat.id,
                f"👋 <b>С возвращением, {await get_user_mention(user_id)}!</b>",
                parse_mode="HTML",
                message_thread_id=405671
            )
            await asyncio.sleep(30)
            await delete_message(update.chat.id, welcome_msg.message_id)
            return

        # Отправляем приветствие в чат
        _onboarding_active.add(user_id)
        try:
            welcome_msg = await bot.send_message(
                update.chat.id,
                f"👋 <b>Добро пожаловать в чат, {await get_user_mention(user_id)}!</b>\n\n"
                f"📩 <i>Я написал тебе в личные сообщения — пройди короткое обучение, чтобы начать общаться!</i>",
                parse_mode="HTML",
                message_thread_id=405671
            )
            await asyncio.sleep(60)
            await delete_message(update.chat.id, welcome_msg.message_id)

            # Ограничиваем права до прохождения онбординга
            try:
                onboarding_restrictions = ChatPermissions(
                    can_send_messages=False,
                    can_send_media_messages=False,
                    can_send_polls=False,
                    can_send_other_messages=False,
                    can_add_web_page_previews=False,
                    can_change_info=False,
                    can_invite_users=False,
                    can_pin_messages=False,
                )
                await bot.restrict_chat_member(update.chat.id, user_id, onboarding_restrictions)
                logger.info(f"Ограничены права пользователя {user_id} до прохождения онбординга")
            except Exception as e:
                logger.error(f"Не удалось ограничить права пользователя {user_id}: {e}")

            # Запускаем онбординг в ЛС
            try:
                await bot.send_message(
                    user_id,
                    f"👋 <b>Привет, {user.first_name}!</b>\n\n"
                    f"Добро пожаловать в наш чат 🎉\n\n"
                    f"Чтобы ты сразу знал все правила и не получил случайное наказание, "
                    f"я проведу тебя через короткое обучение. Это займёт буквально <b>2-3 минуты</b>.\n\n"
                    f"Готов начать?",
                    parse_mode="HTML",
                    reply_markup=get_onboarding_keyboard_start()
                )
                start_onboarding_db(user_id)
                logger.info(f"Онбординг запущен для пользователя {user_id}")
            except Exception as e:
                logger.warning(f"Не удалось отправить ЛС для онбординга пользователю {user_id}: {e}")
                # Не помечаем как завершённый — вместо этого отправляем кнопку прямо в чат
                try:
                    bot_info = await bot.get_me()
                    kb = InlineKeyboardBuilder()
                    kb.row(InlineKeyboardButton(
                        text="📚 Пройти обучение",
                        url=f"https://t.me/{bot_info.username}?start=onboarding"
                    ))
                    fallback_msg = await bot.send_message(
                        update.chat.id,
                        f"👋 {await get_user_mention(user_id)}, добро пожаловать!\n\n"
                        f"📩 Чтобы писать в чат — сначала пройди короткое обучение. "
                        f"Нажми кнопку ниже и напиши боту /start 👇",
                        parse_mode="HTML",
                        reply_markup=kb.as_markup(),
                        message_thread_id=405671
                    )
                    start_onboarding_db(user_id)
                    await asyncio.sleep(120)
                    await delete_message(update.chat.id, fallback_msg.message_id)
                except Exception as e2:
                    logger.error(f"Ошибка отправки кнопки онбординга в чат: {e2}")
                    start_onboarding_db(user_id)
                    complete_onboarding_db(user_id)
        finally:
            _onboarding_active.discard(user_id)


# --- ШАГ 0: Кнопка "Да, поехали!" ---
@dp.callback_query(F.data == "onb_start")
async def onboarding_step1(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    user_id = callback.from_user.id

    # Проверяем кулдаун после провала
    remaining = get_onboarding_cooldown_remaining(user_id)
    if remaining:
        total_seconds = int(remaining.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        await callback.message.edit_text(
            "⏳ <b>Онбординг временно недоступен</b>\n\n"
            f"Ты допустил ошибку при прохождении теста. Повторная попытка доступна через:\n\n"
            f"<b>⏱ {hours} ч. {minutes} мин.</b>\n\n"
            "Используй это время, чтобы перечитать правила чата. Удачи! 📖",
            parse_mode="HTML"
        )
        return

    # Если был провал раньше — сбрасываем для новой попытки
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT failed_at FROM onboarding WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row and row[0] is None:
        reset_onboarding_for_retry(user_id)

    await state.set_state(OnboardingStates.step_1_welcome)

    text = (
        "📋 <b>Шаг 1 из 6 — Правила чата</b>\n\n"
        "Давай начнём с самого важного — правил. Прочитай их внимательно, "
        "в конце будет небольшая проверка 😉\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📜 <b>ПРАВИЛА ЧАТА</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🚫 <b>1. Объявления — только через бота</b>\n"
        "Размещать объявления разрешено <b>исключительно через бота</b>. "
        "Писать объявления вручную в чат запрещено. Также запрещено указывать в объявлении username другого человека.\n"
        "🔥 <i>Нарушение: запрет на публикацию + мут 1-2 дня</i>\n\n"
        "⚠️ <b>2. Нельзя упоминать сторонние вейп-шопы</b>\n"
        "Каждое упоминение = 1 предупреждение. 3 предупреждения = бан.\n\n"
        "🤖 <b>3. Нельзя использовать команды бота в чате</b>\n"
        "Взаимодействие с ботом только в личных сообщениях.\n"
        "🔥 <i>Нарушение: мут на 1 день</i>\n\n"
        "☠️ <b>4. Реклама запрещённых веществ</b>\n"
        "🔥 <i>Нарушение: перманентный бан без предупреждения</i>\n\n"
        "⛔ <b>5. Конкуренция и перепродажа</b>\n"
        "Запрещена любая конкуренция или перепродажа нового товара.\n"
        "🔥 <i>Нарушение: запрет на публикацию + мут 7 дней</i>\n\n"
        "⚠️ <b>6. Беспричинный тег администраторов</b>\n"
        "Нельзя тегать админов без причины. Для жалоб используй /report.\n"
        "🔥 <i>Нарушение: 1 предупреждение</i>\n\n"
        "🚫 <b>7. Слив личных данных (деанон)</b>\n"
        "Запрещено публиковать фото/видео других людей без их согласия.\n"
        "🔥 <i>Нарушение: бан на 30 дней</i>\n\n"
        "🤯 <b>8. Флуд и спам</b>\n"
        "Запрещены повторяющиеся сообщения, бессмысленные символы, спам эмодзи.\n"
        "🔥 <i>Нарушение: предупреждение → мут 1-7 дней</i>\n\n"
        "🎬 <b>9. Запрещённый медиаконтент</b>\n"
        "Нельзя публиковать шокирующий, непристойный контент и контент 18+.\n"
        "🔥 <i>Нарушение: мут до 7 дней → бан</i>\n\n"
        "🔧 <b>10. Технические нарушения</b>\n"
        "Запрещены попытки взлома, несколько аккаунтов, сообщения не по теме.\n"
        "🔥 <i>Нарушение: варн / бан 30 дней — перм</i>\n\n"
        "⚖️ <b>11. Политика и религия</b>\n"
        "Запрещены политические споры, религиозные провокации, экстремизм.\n"
        "🔥 <i>Нарушение: мут 7 дней → бан</i>\n\n"
        "💎 <i>Администрация оставляет за собой право наказывать за нарушения, не указанные в правилах!</i>"
    )

    await callback.message.edit_text(text, parse_mode="HTML",
                                     reply_markup=get_onboarding_next_keyboard("onb_quiz"))


# ============================================================
#   ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ: ПРОВАЛ ОНБОРДИНГА
# ============================================================

async def handle_onboarding_fail(callback: types.CallbackQuery, state: FSMContext, hint: str):
    """Фиксирует провал, сбрасывает состояние и показывает экран кулдауна."""
    user_id = callback.from_user.id
    await state.clear()
    fail_onboarding_db(user_id)

    remaining = get_onboarding_cooldown_remaining(user_id)
    total_seconds = int(remaining.total_seconds()) if remaining else 43200
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60

    await callback.answer("❌ Неверно! Онбординг остановлен.", show_alert=True)
    try:
        await callback.message.edit_text(
            "❌ <b>Неверный ответ — онбординг остановлен</b>\n\n"
            f"💡 <i>Подсказка: {hint}</i>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Перед повторной попыткой внимательно перечитай правила и инструкции.\n\n"
            f"⏱ <b>Повторная попытка через: {hours} ч. {minutes} мин.</b>\n\n"
            "Когда время истечёт — напиши /start и нажми кнопку обучения.",
            parse_mode="HTML"
        )
    except Exception:
        pass


# ============================================================
#   ТЕСТ 1 — 6 вопросов по правилам (после блока правил)
# ============================================================

def get_quiz1_q1_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Только через бота (@TheVapeNeonBot)", callback_data="onb_q1_correct"))
    builder.row(InlineKeyboardButton(text="Написать прямо в чат", callback_data="onb_q1_wrong"))
    builder.row(InlineKeyboardButton(text="Попросить друга разместить за меня", callback_data="onb_q1_wrong"))
    return builder.as_markup()

def get_quiz1_q2_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Тебя забанят навсегда", callback_data="onb_q2_wrong"))
    builder.row(InlineKeyboardButton(text="Ничего, это разрешено", callback_data="onb_q2_wrong"))
    builder.row(InlineKeyboardButton(text="Мут на 1 день", callback_data="onb_q2_correct"))
    return builder.as_markup()

def get_quiz1_q3_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Предупреждение, а при 3-м — бан", callback_data="onb_q3_correct"))
    builder.row(InlineKeyboardButton(text="Сразу перманентный бан", callback_data="onb_q3_wrong"))
    builder.row(InlineKeyboardButton(text="Ничего, это разрешено", callback_data="onb_q3_wrong"))
    return builder.as_markup()

def get_quiz1_q4_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Мут до 7 дней, потом бан", callback_data="onb_q4_correct"))
    builder.row(InlineKeyboardButton(text="Только предупреждение", callback_data="onb_q4_wrong"))
    builder.row(InlineKeyboardButton(text="Ничего, контент разрешён", callback_data="onb_q4_wrong"))
    return builder.as_markup()

def get_quiz1_q5_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Перманентный бан без предупреждения", callback_data="onb_q5_correct"))
    builder.row(InlineKeyboardButton(text="3 предупреждения, потом бан", callback_data="onb_q5_wrong"))
    builder.row(InlineKeyboardButton(text="Мут на 7 дней", callback_data="onb_q5_wrong"))
    return builder.as_markup()

def get_quiz1_q6_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Запрет публикаций + мут 7 дней", callback_data="onb_q6_correct"))
    builder.row(InlineKeyboardButton(text="Только предупреждение", callback_data="onb_q6_wrong"))
    builder.row(InlineKeyboardButton(text="Перманентный бан", callback_data="onb_q6_wrong"))
    return builder.as_markup()

# --- Тест 1, вопрос 1 из 6 ---
@dp.callback_query(F.data == "onb_quiz")
async def onboarding_quiz_q1(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(OnboardingStates.step_3_rules_quiz)
    await callback.message.edit_text(
        "🧠 <b>Проверка знаний — вопрос 1 из 6</b>\n\n"
        "Как правильно разместить объявление о продаже в чате?",
        parse_mode="HTML",
        reply_markup=get_quiz1_q1_keyboard()
    )

@dp.callback_query(F.data == "onb_q1_wrong")
async def onboarding_q1_wrong(callback: types.CallbackQuery, state: FSMContext):
    await handle_onboarding_fail(callback, state, "Объявления размещаются <b>только через бота</b> — правило №1.")

@dp.callback_query(F.data == "onb_q1_correct")
async def onboarding_q1_correct(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("✅ Правильно!")
    await callback.message.edit_text(
        "✅ <b>Верно!</b>\n\n"
        "🧠 <b>Проверка знаний — вопрос 2 из 6</b>\n\n"
        "Что произойдёт, если ты напишешь команду бота прямо в чат?",
        parse_mode="HTML",
        reply_markup=get_quiz1_q2_keyboard()
    )

@dp.callback_query(F.data == "onb_q2_wrong")
async def onboarding_q2_wrong(callback: types.CallbackQuery, state: FSMContext):
    await handle_onboarding_fail(callback, state, "Использование команд бота в чате — нарушение правила №3. Наказание: <b>мут на 1 день</b>.")

@dp.callback_query(F.data == "onb_q2_correct")
async def onboarding_q2_correct(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("✅ Правильно!")
    await callback.message.edit_text(
        "✅ <b>Верно!</b>\n\n"
        "🧠 <b>Проверка знаний — вопрос 3 из 6</b>\n\n"
        "Ты упомянул в чате название стороннего вейп-шопа. Что тебя ждёт?",
        parse_mode="HTML",
        reply_markup=get_quiz1_q3_keyboard()
    )

@dp.callback_query(F.data == "onb_q3_wrong")
async def onboarding_q3_wrong(callback: types.CallbackQuery, state: FSMContext):
    await handle_onboarding_fail(callback, state, "Каждое упоминание стороннего магазина = 1 предупреждение. 3 предупреждения = <b>бан</b>. Правило №2.")

@dp.callback_query(F.data == "onb_q3_correct")
async def onboarding_q3_correct(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("✅ Правильно!")
    await callback.message.edit_text(
        "✅ <b>Верно!</b>\n\n"
        "🧠 <b>Проверка знаний — вопрос 4 из 6</b>\n\n"
        "Ты опубликовал в чате контент 18+. Что тебя ждёт?",
        parse_mode="HTML",
        reply_markup=get_quiz1_q4_keyboard()
    )

@dp.callback_query(F.data == "onb_q4_wrong")
async def onboarding_q4_wrong(callback: types.CallbackQuery, state: FSMContext):
    await handle_onboarding_fail(callback, state, "Запрещённый медиаконтент (правило №9): <b>мут до 7 дней</b>, при повторении — <b>бан</b>.")

@dp.callback_query(F.data == "onb_q4_correct")
async def onboarding_q4_correct(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("✅ Правильно!")
    await callback.message.edit_text(
        "✅ <b>Верно!</b>\n\n"
        "🧠 <b>Проверка знаний — вопрос 5 из 6</b>\n\n"
        "Кто-то в чате рекламирует запрещённые вещества. Какое наказание ждёт нарушителя?",
        parse_mode="HTML",
        reply_markup=get_quiz1_q5_keyboard()
    )

@dp.callback_query(F.data == "onb_q5_wrong")
async def onboarding_q5_wrong(callback: types.CallbackQuery, state: FSMContext):
    await handle_onboarding_fail(callback, state, "Реклама запрещённых веществ (правило №4): <b>перманентный бан без предупреждений</b>.")

@dp.callback_query(F.data == "onb_q5_correct")
async def onboarding_q5_correct(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("✅ Правильно!")
    await callback.message.edit_text(
        "✅ <b>Верно!</b>\n\n"
        "🧠 <b>Проверка знаний — вопрос 6 из 6</b>\n\n"
        "Ты начал перепродавать чужой новый товар в чате. Что тебя ждёт?",
        parse_mode="HTML",
        reply_markup=get_quiz1_q6_keyboard()
    )

@dp.callback_query(F.data == "onb_q6_wrong")
async def onboarding_q6_wrong(callback: types.CallbackQuery, state: FSMContext):
    await handle_onboarding_fail(callback, state, "Конкуренция и перепродажа нового товара (правило №5): <b>запрет публикаций + мут 7 дней</b>.")

@dp.callback_query(F.data == "onb_q6_correct")
async def onboarding_q6_correct(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("✅ Правильно! Тест пройден!")
    await state.set_state(OnboardingStates.step_4_order)

    text = (
        "🎯 <b>Отлично! Все 6 вопросов пройдены!</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📋 <b>Шаг 2 из 6 — Как сделать заказ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🛍️ Ты можешь заказать товар у:\n\n"
        "👉 <b>Вика</b> (владелец): @darknesss43\n"
        "👉 <b>Тимур</b> (администратор): @lydnk\n\n"
        "📦 <b>Процесс заказа:</b>\n"
        "1️⃣ Напиши, что хочешь заказать\n"
        "2️⃣ Оплати товар\n"
        "3️⃣ Ожидай доставку в конце недели\n\n"
        "📊 <b>Прайс-лист:</b> https://t.me/c/3879084610/3\n\n"
        "🔥 <i>Только лучшие товары и быстрая доставка!</i>"
    )
    await callback.message.edit_text(text, parse_mode="HTML",
                                     reply_markup=get_onboarding_next_keyboard("onb_ads"))


# --- ШАГ 3: Как публиковать объявления ---
@dp.callback_query(F.data == "onb_ads")
async def onboarding_ads(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(OnboardingStates.step_5_ads)

    text = (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📋 <b>Шаг 3 из 6 — Как опубликовать объявление</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📢 Хочешь продать что-то? Вот пошаговая инструкция:\n\n"
        "1️⃣ Открой бота: @TheVapeNeonBot\n"
        "2️⃣ Нажми <b>«Запустить»</b> или напиши /start\n"
        "3️⃣ Введи команду /public\n"
        "4️⃣ Заполни форму объявления (фото, описание, цена)\n"
        "5️⃣ Перейди в раздел <b>«Мои объявления»</b>\n"
        "6️⃣ Выбери созданное объявление\n"
        "7️⃣ Нажми кнопку <b>«Опубликовать»</b>\n\n"
        "⚠️ <b>Важно помнить:</b>\n"
        "• Нельзя публиковать объявления вручную в чат\n"
        "• Нельзя указывать в объявлении username другого человека\n"
        "• Соблюдай лимит публикаций"
    )

    await callback.message.edit_text(text, parse_mode="HTML",
                                     reply_markup=get_onboarding_next_keyboard("onb_complaints"))


# --- ШАГ 4: Как подавать жалобы ---
@dp.callback_query(F.data == "onb_complaints")
async def onboarding_complaints(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(OnboardingStates.step_6_complaints)

    text = (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📋 <b>Шаг 4 из 6 — Как подать жалобу</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚖️ Если тебя наказали несправедливо или ты заметил нарушение — ты можешь пожаловаться!\n\n"
        "🔹 <b>Жалоба на пользователя:</b>\n"
        "Ответь на сообщение нарушителя командой:\n"
        "<code>/report [причина]</code>\n"
        "Или: <code>/report @username [причина]</code>\n\n"
        "🔹 <b>Жалоба на администратора:</b>\n"
        "1. Открой бота в ЛС: /start\n"
        "2. Нажми кнопку <b>«📢 Оставить жалобу на админа»</b>\n"
        "3. Следуй инструкциям бота\n\n"
        "⏰ <b>Сроки рассмотрения:</b>\n"
        "• На пользователей — до 24 часов\n"
        "• На администраторов — до 48 часов\n\n"
        "👮 <b>Важно:</b>\n"
        "• Ложные жалобы могут привести к наказанию\n"
        "• Жалобы анонимны для других участников\n"
        "• Решение администрации является окончательным"
    )

    await callback.message.edit_text(text, parse_mode="HTML",
                                     reply_markup=get_onboarding_next_keyboard("onb_final_quiz"))


# ============================================================
#   ТЕСТ 2 — 6 вопросов (после блока жалоб, финальный)
# ============================================================

def get_quiz2_q1_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="/report @username причина", callback_data="onb_fq1_correct"))
    builder.row(InlineKeyboardButton(text="Написать об этом в чат", callback_data="onb_fq1_wrong"))
    builder.row(InlineKeyboardButton(text="Тегнуть администратора в чате", callback_data="onb_fq1_wrong"))
    return builder.as_markup()

def get_quiz2_q2_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Ничего, это не запрещено", callback_data="onb_fq2_wrong"))
    builder.row(InlineKeyboardButton(text="Получишь предупреждение сам", callback_data="onb_fq2_correct"))
    builder.row(InlineKeyboardButton(text="Администратора накажут", callback_data="onb_fq2_wrong"))
    return builder.as_markup()

def get_quiz2_q3_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Нажать «📢 Оставить жалобу на админа» в боте", callback_data="onb_fq3_correct"))
    builder.row(InlineKeyboardButton(text="Написать жалобу прямо в чат", callback_data="onb_fq3_wrong"))
    builder.row(InlineKeyboardButton(text="Написать другому администратору в ЛС", callback_data="onb_fq3_wrong"))
    return builder.as_markup()

def get_quiz2_q4_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="До 24 часов", callback_data="onb_fq4_correct"))
    builder.row(InlineKeyboardButton(text="До 1 часа", callback_data="onb_fq4_wrong"))
    builder.row(InlineKeyboardButton(text="До 7 дней", callback_data="onb_fq4_wrong"))
    return builder.as_markup()

def get_quiz2_q5_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Другие участники чата не видят жалобу", callback_data="onb_fq5_correct"))
    builder.row(InlineKeyboardButton(text="Жалоба публикуется в чат", callback_data="onb_fq5_wrong"))
    builder.row(InlineKeyboardButton(text="Ник жалобщика виден всем", callback_data="onb_fq5_wrong"))
    return builder.as_markup()

def get_quiz2_q6_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Использовать /report или кнопку жалобы в боте", callback_data="onb_fq6_correct"))
    builder.row(InlineKeyboardButton(text="Ответить нарушителю в чате", callback_data="onb_fq6_wrong"))
    builder.row(InlineKeyboardButton(text="Написать напрямую любому администратору в ЛС", callback_data="onb_fq6_wrong"))
    return builder.as_markup()

# --- Финальный тест, вопрос 1 из 6 ---
@dp.callback_query(F.data == "onb_final_quiz")
async def onboarding_final_quiz(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(OnboardingStates.step_7_final_quiz)
    await callback.message.edit_text(
        "🧠 <b>Финальный тест — вопрос 1 из 6</b>\n\n"
        "Ты увидел, что участник нарушает правила в чате. "
        "Как правильно пожаловаться на него?",
        parse_mode="HTML",
        reply_markup=get_quiz2_q1_keyboard()
    )

@dp.callback_query(F.data == "onb_fq1_wrong")
async def onboarding_fq1_wrong(callback: types.CallbackQuery, state: FSMContext):
    await handle_onboarding_fail(callback, state, "Жалобы на участников подаются командой <b>/report @username причина</b>. Не пиши об этом в чат.")

@dp.callback_query(F.data == "onb_fq1_correct")
async def onboarding_fq1_correct(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("✅ Правильно!")
    await callback.message.edit_text(
        "✅ <b>Верно!</b>\n\n"
        "🧠 <b>Финальный тест — вопрос 2 из 6</b>\n\n"
        "Ты подал ложную жалобу на участника, который ничего не нарушал. "
        "Что тебя ждёт?",
        parse_mode="HTML",
        reply_markup=get_quiz2_q2_keyboard()
    )

@dp.callback_query(F.data == "onb_fq2_wrong")
async def onboarding_fq2_wrong(callback: types.CallbackQuery, state: FSMContext):
    await handle_onboarding_fail(callback, state, "Ложные жалобы наказуемы: <b>предупреждение получит сам жалобщик</b>.")

@dp.callback_query(F.data == "onb_fq2_correct")
async def onboarding_fq2_correct(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("✅ Правильно!")
    await callback.message.edit_text(
        "✅ <b>Верно!</b>\n\n"
        "🧠 <b>Финальный тест — вопрос 3 из 6</b>\n\n"
        "Ты считаешь, что администратор наказал тебя несправедливо. "
        "Как правильно это обжаловать?",
        parse_mode="HTML",
        reply_markup=get_quiz2_q3_keyboard()
    )

@dp.callback_query(F.data == "onb_fq3_wrong")
async def onboarding_fq3_wrong(callback: types.CallbackQuery, state: FSMContext):
    await handle_onboarding_fail(callback, state, "Жалобы на администраторов подаются через кнопку <b>«📢 Оставить жалобу на админа»</b> в боте (/start).")

@dp.callback_query(F.data == "onb_fq3_correct")
async def onboarding_fq3_correct(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("✅ Правильно!")
    await callback.message.edit_text(
        "✅ <b>Верно!</b>\n\n"
        "🧠 <b>Финальный тест — вопрос 4 из 6</b>\n\n"
        "За какое время администрация рассмотрит жалобу на обычного пользователя?",
        parse_mode="HTML",
        reply_markup=get_quiz2_q4_keyboard()
    )

@dp.callback_query(F.data == "onb_fq4_wrong")
async def onboarding_fq4_wrong(callback: types.CallbackQuery, state: FSMContext):
    await handle_onboarding_fail(callback, state, "Срок рассмотрения жалоб на пользователей — <b>до 24 часов</b>. На администраторов — до 48 часов.")

@dp.callback_query(F.data == "onb_fq4_correct")
async def onboarding_fq4_correct(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("✅ Правильно!")
    await callback.message.edit_text(
        "✅ <b>Верно!</b>\n\n"
        "🧠 <b>Финальный тест — вопрос 5 из 6</b>\n\n"
        "Что верно про анонимность жалоб в этом чате?",
        parse_mode="HTML",
        reply_markup=get_quiz2_q5_keyboard()
    )

@dp.callback_query(F.data == "onb_fq5_wrong")
async def onboarding_fq5_wrong(callback: types.CallbackQuery, state: FSMContext):
    await handle_onboarding_fail(callback, state, "Жалобы <b>анонимны для других участников</b> — никто в чате не увидит, что ты пожаловался.")

@dp.callback_query(F.data == "onb_fq5_correct")
async def onboarding_fq5_correct(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("✅ Правильно!")
    await callback.message.edit_text(
        "✅ <b>Верно!</b>\n\n"
        "🧠 <b>Финальный тест — вопрос 6 из 6</b>\n\n"
        "Ты заметил спам в чате. Какой правильный способ сообщить об этом администрации?",
        parse_mode="HTML",
        reply_markup=get_quiz2_q6_keyboard()
    )

@dp.callback_query(F.data == "onb_fq6_wrong")
async def onboarding_fq6_wrong(callback: types.CallbackQuery, state: FSMContext):
    await handle_onboarding_fail(callback, state, "Правильный способ: <b>/report</b> или кнопка жалобы в боте. Не отвечай нарушителю и не пиши напрямую в ЛС администраторам.")

@dp.callback_query(F.data == "onb_fq6_correct")
async def onboarding_complete(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("🎉 Все вопросы пройдены!")
    await state.clear()

    user_id = callback.from_user.id
    complete_onboarding_db(user_id)

    # Снимаем ограничения на отправку сообщений в чат
    try:
        full_permissions = ChatPermissions(
            can_send_messages=True,
            can_send_media_messages=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
            can_change_info=False,
            can_invite_users=False,
            can_pin_messages=False,
        )
        await bot.restrict_chat_member(CHAT_ID, user_id, full_permissions)
        logger.info(f"Восстановлены права пользователя {user_id} после онбординга")
    except Exception as e:
        logger.error(f"Не удалось восстановить права пользователя {user_id}: {e}")

    await callback.message.edit_text(
        "🎉 <b>Поздравляем! Ты прошёл обучение!</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "✅ Ты знаешь правила чата\n"
        "✅ Знаешь, как сделать заказ\n"
        "✅ Знаешь, как публиковать объявления\n"
        "✅ Знаешь, как подавать жалобы\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🚀 <b>Добро пожаловать в чат! Приятного общения!</b>\n\n"
        "💡 <i>Если что-то забудешь — напиши /start чтобы вернуться в меню бота.</i>",
        parse_mode="HTML"
    )
    # Показываем Reply-клавиатуру отдельным сообщением
    await callback.message.answer(
        "👇 Главное меню:",
        reply_markup=get_main_keyboard(callback.from_user.id)
    )


# --- Кнопка "Позже" ---
@dp.callback_query(F.data == "onb_later")
async def onboarding_later(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text(
        "⏳ <b>Хорошо, напомню позже!</b>\n\n"
        "Когда будешь готов — нажми кнопку 📚 Пройти обучение в меню ниже.\n\n"
        "⚠️ <i>Рекомендуем пройти обучение до того, как начнёшь писать в чат — "
        "так ты не получишь случайное наказание.</i>",
        parse_mode="HTML"
    )
    await callback.message.answer(
        "👇 Главное меню:",
        reply_markup=get_main_keyboard(callback.from_user.id)
    )

# ============================================================
#                  КОНЕЦ БЛОКА ОНБОРДИНГА
# ============================================================



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

# ============================================================
#         ЕЖЕДНЕВНАЯ РАССЫЛКА ПРАВИЛ / ЗАКАЗОВ / ЖАЛОБ
# ============================================================

DAILY_RULES_TEXT = """📜 Правила чата 📜

🚫 1. Нарушение правил отправки объявлений (НПОО)
   • Максимум 1 объявление в 1.5 часа
   • Максимум 5 объявлений в день
   • Любое редактирование объявления должно быть В САМОМ объявлении. Последующее упоминание объявления с его редакцией (или без редакции) тоже считается НПОО.
   • Если вы отправили одно объявление и прошло полтора часа, но после вашего объявления не было других (любых) сообщений, то мы будем считать это за НПОО (Нужно, чтобы было хотя бы 30 сообщений после вашего объявления!)
   🔥 Нарушение: мут на 1 - 3 дней (В зависимости от прошедшего времени) по причине «НПОО»

⚠️ 2. Упоминание сторонних вейп-шопов
   • За каждое упоминание: 1 предупреждение
   • 3 предупреждения: бан

🤖 3. Использование бота
   • Любое взаимодействие с ботом запрещено
   🔥 Нарушение: автоматический мут на 1 день

☠️ 4. Реклама запрещённых веществ
   🔥 Нарушение: перманентный бан

⛔ 5. Конкуренция и перепродажа
   • Любая конкуренция или перепродажа нового товара
   🔥 Нарушение: мут на 7 дней

⚠️ 6. Беспричинное упоминание администраторов
• Нельзя тегать админов просто чтобы пообщаться или задать общие вопросы
• Для общих вопросов используйте обычные сообщения в чат
• Для жалоб используйте команду /report
🔥 Нарушение: 1 предупреждение

Исключение: администраторы могут упоминать друг друга

🚫 7. Слив личных данных (фото/видео лиц/деанон)
   • Запрещена публикация фото или видео, деанона других людей без их согласия
   🔥 Нарушение: бан на 30 дней

🤯 8. Флуд и спам
   • Запрещен бессмысленный флуд (повторяющиеся сообщения, символы, emoji)
   🔥 Нарушение: предупреждение → мут от 1 до 7 дней

💢 9. Оскорбления и токсичность
   • Запрещены прямые оскорбления участников чата
   • Запрещены унизительные высказывания по любому признаку
   • Запрещены призывы к конфликтам и травле
   🔥 Нарушение: предупреждение → мут до 3 дней

🎬 10. Медиаконтент
   • Запрещены видео/фото шокирующего или непристойного содержания
   • Запрещен контент 18+
   🔥 Нарушение: мут до 7 дней → бан при повторных нарушениях

🔧 11. Технические нарушения
   • Запрещены попытки взлома или обхода ограничений
   • Запрещено создание нескольких аккаунтов для обхода наказаний
   🔥 Нарушение: бан от 30 дней до перманентного

⚖️ 12. Политика и религия
   • Запрещены острые политические дискуссии
   • Запрещены религиозные провокации и разжигание вражды
   • Запрещена пропаганда экстремистских идей
   🔥 Нарушение: мут 7 дней → бан"""

DAILY_ORDERS_TEXT = """🛍️ Как сделать заказ? 🛍️

Вы можете заказать качественные товары у владельца барахолки: 
👉 darknesss43.t.me (Вика) 👈
или администратора:
👉 lydnk.t.me (Тимур)👈

Процесс заказа:
1️⃣ Напишите, что хотите заказать
2️⃣ Оплатите товар
3️⃣ Ожидайте доставку в конце недели

🔥 Только лучшие товары и быстрая доставка!

📊 Прайс: https://t.me/c/3782896026/3"""

DAILY_COMPLAINTS_TEXT = """⚖️ Система обжалования наказаний ⚖️

🛡️ Если вас наказали необоснованно - вы имеете право подать жалобу!

📋 Как подать жалобу:

🔹 На обычного пользователя:
• Ответьте на сообщение нарушителя командой:
  /report [причина]
• Или укажите пользователя:
  /report @username [причина]

🔹 На администратора:
1. Перейдите в ЛС бота: /start
2. Нажмите кнопку «📢 Оставить жалобу на админа»
3. Следуйте инструкциям бота

⏰ Сроки рассмотрения:
• Жалобы на пользователей - в течение 24 часов
• Жалобы на администраторов - в течение 48 часов

📝 Что указывать в жалобе:
• Конкретное нарушение
• Дата и время инцидента  
• Доказательства (скриншоты, ссылки)
• Вашу версию событий

👮 Важно:
• Ложные жалобы могут привести к наказанию
• Жалобы анонимны для других пользователей
• Решение владельцев чата является окончательным

🤝 Мы за справедливую модерацию!"""


async def _do_send_info():
    """Отправляет правила, заказы и жалобы в чат."""
    for text in [DAILY_RULES_TEXT, DAILY_ORDERS_TEXT, DAILY_COMPLAINTS_TEXT]:
        try:
            await bot.send_message(
                chat_id=CHAT_ID,
                text=text,
                disable_web_page_preview=True
            )
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Ошибка при отправке периодического сообщения: {e}")
    logger.info("Периодическая рассылка правил успешно отправлена")


async def send_periodic_info():
    """При запуске сразу отправляет (если 7:00–1:59 МСК),
    затем каждые 3 ч только в этом окне."""
    await asyncio.sleep(10)  # ждём инициализации бота

    def in_window(h: int) -> bool:
        """True если час h входит в окно 7:00–1:59 МСК."""
        return h >= 7 or h < 2

    # Сразу отправляем при запуске, если время подходящее
    now = get_moscow_time()
    if in_window(now.hour):
        await _do_send_info()

    while True:
        try:
            now = get_moscow_time()
            if in_window(now.hour):
                # Следующая отправка через 3 часа
                next_send = now + timedelta(hours=3)
                # Если следующий слот попадает в «тихое» время (2:00–6:59) — переносим на 7:00
                if 2 <= next_send.hour < 7:
                    next_send = next_send.replace(hour=7, minute=0, second=0, microsecond=0)
            else:
                # Сейчас тихое время — ждём до 7:00
                next_send = now.replace(hour=7, minute=0, second=0, microsecond=0)
                if next_send <= now:
                    next_send += timedelta(days=1)

            wait_sec = max(0, (next_send - now).total_seconds())
            logger.info(f"Рассылка: следующая отправка через {int(wait_sec)} сек. ({next_send.strftime('%d.%m %H:%M')} МСК)")
            await asyncio.sleep(wait_sec)

            # Перепроверяем окно прямо перед отправкой
            if in_window(get_moscow_time().hour):
                await _do_send_info()

        except Exception as e:
            logger.error(f"Ошибка в задаче периодической рассылки: {e}")
            await asyncio.sleep(300)


# Основная функция
async def main():
    logger.info("Запуск бота...")
    
    # Получаем username бота
    await set_bot_username()
    
    # Помечаем существующих участников как прошедших онбординг (только при первом запуске)
    logger.info("Инициализация онбординга для существующих участников...")
    await mark_existing_members_as_onboarded()

    # Рассылаем напоминание об обучении тем, кто ещё не прошёл
    logger.info("Рассылка напоминаний об обучении...")
    await notify_incomplete_onboarding()
    
    # Восстанавливаем активные наказания
    logger.info("Восстановление активных наказаний...")
    await restore_active_punishments()
    
    # Запускаем фоновые задачи
    asyncio.create_task(cleanup_expired_data())
    asyncio.create_task(monitor_expired_punishments())  # Новая задача
    asyncio.create_task(send_periodic_info())  # Рассылка правил каждые 3 часа
    
    # Регистрируем middleware для режима тех.работ
    dp.message.middleware(MaintenanceMiddleware())
    dp.callback_query.middleware(MaintenanceMiddleware())
    
    # Запускаем бота
    await dp.start_polling(
        bot,
        allowed_updates=["message", "callback_query", "chat_member", "my_chat_member"]
    )

if __name__ == "__main__":
    asyncio.run(main())
