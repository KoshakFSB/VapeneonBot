import os
import re
import asyncio
import logging
import random
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Set, Tuple, Any

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
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN не найден в переменных окружения")
    raise ValueError("BOT_TOKEN не установлен")

ADMIN_IDS_STR = os.getenv("ADMIN_IDS")
if not ADMIN_IDS_STR:
    logger.error("❌ ADMIN_IDS не найден в переменных окружения")
    raise ValueError("ADMIN_IDS не установлен")
ADMIN_IDS = list(map(int, ADMIN_IDS_STR.split(',')))

CHAT_ID_STR = os.getenv("CHAT_ID")
if not CHAT_ID_STR:
    logger.error("❌ CHAT_ID не найден в переменных окружения")
    raise ValueError("CHAT_ID не установлен")
CHAT_ID = int(CHAT_ID_STR)

# Ограничения
MAX_ADS_PER_DAY = int(os.getenv("MAX_ADS_PER_DAY", "5"))
MIN_AD_INTERVAL_HOURS = float(os.getenv("MIN_AD_INTERVAL_HOURS", "1.5"))
MUTE_DURATION_DAYS = int(os.getenv("MUTE_DURATION_DAYS", "1"))
RULES_INTERVAL_HOURS = float(os.getenv("RULES_INTERVAL_HOURS", "1.5"))

MIN_AD_INTERVAL = timedelta(hours=MIN_AD_INTERVAL_HOURS)
MUTE_DURATION = timedelta(days=MUTE_DURATION_DAYS)
RULES_INTERVAL = timedelta(hours=RULES_INTERVAL_HOURS)

# Данные для донатов
DONATE_LINK = os.getenv("DONATE_LINK", "https://www.sberbank.com/sms/pbpn?requisiteNumber=2202208057115496")
DONATE_MESSAGE = """
❤️ <b>Поддержать развитие бота</b> ❤️

🤖 Бот работает 24/7 и постоянно улучшается. 
Если вы хотите поддержать развитие проекта, можете сделать донат!

💳 <b>Способы перевода:</b>
• Сбербанк: <code>2202208057115496</code>
• По ссылке: {donate_link}

💰 <b>Любая сумма приятна и мотивирует!</b>

🙏 <i>Спасибо за вашу поддержку!</i>
"""

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

    # Индексы для улучшения производительности
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_warns_user_chat ON warns(user_id, chat_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_warns_expires ON warns(expires_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_mutes_user_chat ON mutes(user_id, chat_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_mutes_expires ON mutes(expires_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bans_user_chat ON bans(user_id, chat_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bans_expires ON bans(expires_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_ads_user_date ON user_ads(user_id, sent_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ad_violations_user_date ON ad_limit_violations(user_id, violation_date)")

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

# Красивые сообщения с оформлением
RULES_MESSAGE = """
📜 <b>Правила чата</b> 📜

🚫 <b>1. Спам объявлениями</b>
   • Максимум 1 объявление в 1.5 часа
   • Максимум 5 объявлений в день
   • Любое редактирование объявления должно быть В САМОМ объявлении. Последующее упоминание объявления с его редакцией тоже считается НПОО.
   🔥 <i>Нарушение: мут на 1 -7 дней (В зависимости от прошедшего времени) по причине «НПОО»</i>

⚠️ <b>2. Упоминание сторонних вейп-шопов</b>
   • За каждое упоминание: <i>1 предупреждение</i>
   • 3 предупреждения: <i>бан</i>

🤖 <b>3. Использование бота</b>
   • <i>Любое взаимодействие с ботом запрещено</i>
   🔥 <i>Нарушение: автоматический мут на 1 день</i>

☠️ <b>4. Реклама запрещённых веществ</b>
   🔥 <i>Нарушение: перманентный бан + занесение в чёрный список</i>

⛔ <b>5. Конкуренция и перепродажа</b>
   • <i>Любая конкуренция или перепродажа нового товара</i>
   🔥 <i>Нарушение: мут на 7 дней</i>

💎 <b>Администрация оставляет за собой право наказывать за другие нарушения, не указанные в правилах!</b>
"""

ORDER_MESSAGE = """
🛍️ <b>Как сделать заказ?</b> 🛍️

Вы можете заказать качественные товары у владельца барахолки: 
👉 @darknesss43 (Вика) 👈
или администратора:
👉 @barsss_amnyam (Влад) 👈

<b>Процесс заказа:</b>
1️⃣ Напишите, что хотите заказать
2️⃣ Оплатите товар
3️⃣ Ожидайте доставку в конце недели

🔥 <i>Только лучшие товары и быстрая доставка!</i>

📊 <b>Прайс:</b> https://t.me/c/2361598273/5
"""

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Глобальные переменные для хранения ID сообщений с правилами и заказами
last_rules_message_id = None
last_order_message_id = None

async def send_rules_and_order_message():
    global last_rules_message_id, last_order_message_id
    
    try:
        # Удаляем предыдущие сообщения
        if last_rules_message_id:
            try:
                await bot.delete_message(CHAT_ID, last_rules_message_id)
            except:
                pass
                
        if last_order_message_id:
            try:
                await bot.delete_message(CHAT_ID, last_order_message_id)
            except:
                pass
        
        # Отправляем новые сообщения
        rules_msg = await bot.send_message(CHAT_ID, RULES_MESSAGE, parse_mode="HTML")
        last_rules_message_id = rules_msg.message_id

        await asyncio.sleep(3)
        
        order_msg = await bot.send_message(CHAT_ID, ORDER_MESSAGE, parse_mode="HTML")
        last_order_message_id = order_msg.message_id
        
    except Exception as e:
        logger.error(f"Ошибка отправки правил: {e}")

async def rules_scheduler():
    """Планировщик для отправки правил каждые 1.5 часа"""
    while True:
        try:
            await send_rules_and_order_message()
            logger.info("Правила и заказ отправлены по расписанию")
        except Exception as e:
            logger.error(f"Ошибка в планировщике правил: {e}")
        
        await asyncio.sleep(RULES_INTERVAL.total_seconds())

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

# Новые функции для донатов
def add_donation(user_id: int, amount: float = None, currency: str = "RUB", message: str = None, is_anonymous: bool = False):
    """Добавляет запись о донате в базу данных"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO donations (user_id, amount, currency, message, is_anonymous) VALUES (?, ?, ?, ?, ?)",
        (user_id, amount, currency, message, is_anonymous),
    )
    conn.commit()
    conn.close()

def get_total_donations() -> float:
    """Получает общую сумму донатов"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(amount) FROM donations WHERE amount IS NOT NULL")
    result = cursor.fetchone()[0]
    conn.close()
    return result or 0.0

def get_recent_donations(limit: int = 10) -> List[Dict[str, Any]]:
    """Получает последние донаты"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT d.user_id, d.amount, d.currency, d.message, d.donated_at, d.is_anonymous 
           FROM donations d 
           WHERE d.amount IS NOT NULL 
           ORDER BY d.donated_at DESC 
           LIMIT ?""",
        (limit,)
    )
    donations = [
        {
            "user_id": row[0],
            "amount": row[1],
            "currency": row[2],
            "message": row[3],
            "donated_at": row[4],
            "is_anonymous": row[5]
        }
        for row in cursor.fetchall()
    ]
    conn.close()
    return donations

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

def censor_trigger_word(text: str, trigger: str) -> str:
    """Заменяет триггерное слово на первую букву и звёздочки"""
    words = text.split()
    censored_words = []
    
    for word in words:
        lower_word = word.lower()
        # Проверяем все варианты написания триггерного слова
        for variant in TRIGGER_WORDS.get(trigger, []):
            if variant in lower_word:
                # Оставляем первую букву, остальные заменяем на звёздочки
                if len(word) > 1:
                    censored_word = word[0] + '*' * (len(word) - 1)
                    censored_words.append(censored_word)
                else:
                    censored_words.append(word)
                break
        else:
            censored_words.append(word)
    
    return ' '.join(censored_words)

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

# Обработчики команд
@dp.message(Command("start"))
async def cmd_start(message: Message):
    if message.chat.type != ChatType.PRIVATE:
        return

    welcome_text = """
    👋 <b>Привет! Я бот-модератор для чата.</b>

    🤖 <b>Мои возможности:</b>
    • Автоматическая модерация объявлений
    • Система предупреждений
    • Мут/бан пользователей
    • Управление администраторами

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

    ❤️ <b>Поддержать бота:</b> /donate
    """
    await message.answer(welcome_text, parse_mode="HTML")

@dp.message(Command("rules"))
async def cmd_rules(message: Message):
    if message.chat.id != CHAT_ID:
        return

    await message.answer(RULES_MESSAGE, parse_mode="HTML")

@dp.message(Command("order"))
async def cmd_order(message: Message):
    if message.chat.id != CHAT_ID:
        return

    await message.answer(ORDER_MESSAGE, parse_mode="HTML")

@dp.message(Command("donate"))
async def cmd_donate(message: Message):
    donate_text = DONATE_MESSAGE.format(donate_link=DONATE_LINK)
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="💳 Поддержать", url=DONATE_LINK))
    
    await message.answer(donate_text, parse_mode="HTML", reply_markup=keyboard.as_markup())

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

    # Статистика донатов
    total_donations = get_total_donations()

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

    💰 <b>Донаты:</b>
    • Всего: {total_donations:.2f} руб.

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

    if not command.args:
        await message.answer("❌ Укажите время и пользователя: /tmute 1h @user [причина]")
        return

    # Разбираем аргументы
    args = command.args.split(maxsplit=2)
    
    # Проверяем минимальное количество аргументов
    if len(args) < 1:
        await message.answer("❌ Формат: /tmute <время> <пользователь> [причина]")
        return

    time_str = args[0]
    user_identifier = None
    reason = "Не указана"

    # Если это ответ на сообщение, берем пользователя из ответа
    if message.reply_to_message:
        user_id = message.reply_to_message.from_user.id
        # Причина - все остальные аргументы
        if len(args) > 1:
            reason = ' '.join(args[1:])
    else:
        # Если не ответ, проверяем наличие идентификатора пользователя
        if len(args) < 2:
            await message.answer("❌ Формат: /tmute <время> <пользователь> [причина]")
            return
        
        user_identifier = args[1]
        reason = args[2] if len(args) > 2 else "Не указана"

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

    if not command.args:
        await message.answer("❌ Укажите время и пользователя: /tban 1d @user [причина]")
        return

    # Разбираем аргументы
    args = command.args.split(maxsplit=2)
    
    if len(args) < 1:
        await message.answer("❌ Формат: /tban <время> <пользователь> [причина]")
        return

    time_str = args[0]
    user_identifier = None
    reason = "Не указана"

    # Если это ответ на сообщение
    if message.reply_to_message:
        user_id = message.reply_to_message.from_user.id
        if len(args) > 1:
            reason = ' '.join(args[1:])
    else:
        if len(args) < 2:
            await message.answer("❌ Формат: /tban <время> <пользователь> [причина]")
            return
        
        user_identifier = args[1]
        reason = args[2] if len(args) > 2 else "Не указана"

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

@dp.message(Command("admin_add"))
async def cmd_admin_add(message: Message, command: CommandObject):
    if not await is_owner(message.from_user.id):
        await message.answer("❌ Только владелец может добавлять администраторов.")
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите пользователя (ответом на сообщение или @username/id).")
        return

    add_admin(user_id, message.from_user.id)
    user_mention = await get_user_mention(user_id)

    await message.answer(
        f"✅ Пользователь {user_mention} добавлен в администраторы.",
        parse_mode="HTML"  # Добавьте этот параметр
    )

@dp.message(Command("admin_remove"))
async def cmd_admin_remove(message: Message, command: CommandObject):
    if not await is_owner(message.from_user.id):
        await message.answer("❌ Только владелец может удалять администраторов.")
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите пользователя (ответом на сообщение или @username/id).")
        return

    remove_admin(user_id)
    user_mention = await get_user_mention(user_id)

    await message.answer(
        f"✅ Пользователь {user_mention} удален из администраторов.",
        parse_mode="HTML"  # Добавьте этот параметр
    )

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

    # Проверяем, является ли пользователь администратором чата (включая реальных Telegram администраторов)
    is_chat_admin_user = await is_chat_admin(user_id, message.chat.id)
    is_bot_admin_user = is_admin(user_id)  # Администратор в базе данных бота
    
    if not (is_chat_admin_user or is_bot_admin_user):
        await message.answer("❌ Указанный пользователь не является администратором.")
        return

    await warn_admin(user_id, reason, message.from_user.id)

@dp.message(Command("admin_unwarn"))
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

    warn_id = remove_last_admin_warn(user_id)
    user_mention = await get_user_mention(user_id)

    if warn_id:
        await message.answer(f"✅ Снято последнее предупреждение у администратора {user_mention}.")
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
    
    # Формируем сообщение для администраторов (без указания чата)
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
            text="🔇 Мут 7д", 
            callback_data=f"mute7:{reported_user_id}:{message.chat.id}"
        ),
        InlineKeyboardButton(
            text="🚫 Бан", 
            callback_data=f"ban:{reported_user_id}:{message.chat.id}"
        )
    )
    keyboard.row(
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
        elif data.startswith("mute7:"):
            action = "mute7"
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
        reporter_match = re.search(r'Жалоба от:.*?tg://user\?id=(\d+)', message_text)
        if reporter_match:
            reporter_id = int(reporter_match.group(1))
            reporter_mention = await get_user_mention(reporter_id)
        
        result_message = None
        
        if action == "warn":
            success = await warn_user(chat_id, user_id, "Жалоба от пользователя")
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
            success = await mute_user(chat_id, user_id, duration, "Жалоба от пользователя")
            action_text = f"мут на {duration_str}" if success else "ошибка при муте"
            
            if success:
                result_message = await bot.send_message(
                    chat_id,
                    f"🔇 <b>По жалобе пользователя {reporter_mention}</b>\n\n"
                    f"👤 Пользователь {user_mention} получил мут на {duration_str}\n"
                    f"👮 Действие выполнено: {admin_mention}",
                    parse_mode="HTML"
                )
            
        elif action == "mute7":
            duration = timedelta(days=7)
            duration_str = "7 дней"
            success = await mute_user(chat_id, user_id, duration, "Жалоба от пользователя")
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
            success = await ban_user(chat_id, user_id, None, "Жалоба от пользователя")
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

    # Проверка на триггерные слова
    for trigger_word, variants in TRIGGER_WORDS.items():
        for variant in variants:
            if variant in text.lower():
                # Цензурируем сообщение
                censored_text = censor_trigger_word(text, trigger_word)
                
                # Удаляем оригинальное сообщение
                await delete_message(message.chat.id, message.message_id)
                
                # Отправляем цензурированную версию
                warning_msg = await message.answer(
                    f"⚠️ <b>Сообщение пользователя {await get_user_mention(user_id)} было отцензурировано:</b>\n\n"
                    f"{censored_text}\n\n"
                    f"<i>Использование запрещенных слов не допускается!</i>",
                    parse_mode="HTML"
                )
                
                # Удаляем предупреждение через 10 секунд
                await asyncio.sleep(10)
                await delete_message(message.chat.id, warning_msg.message_id)
                return

    # Проверка на объявления
    if is_ad_message(text):

        today_ads = get_today_ads_count(user_id)
        last_ad_time = get_last_ad_time(user_id)

        # Проверка лимита объявлений в день
        if today_ads >= MAX_ADS_PER_DAY:
            await delete_message(message.chat.id, message.message_id)
            
            violation_count = get_today_violations_count(user_id) + 1
            add_ad_violation(user_id)
            
            # Определяем срок мута в зависимости от количества нарушений
            if violation_count == 1:
                mute_duration = timedelta(hours=6)
            elif violation_count == 2:
                mute_duration = timedelta(days=1)
            else:
                mute_duration = timedelta(days=7)
            
            await mute_user(
                message.chat.id, 
                user_id, 
                mute_duration, 
                f"Превышение лимита объявлений ({today_ads}/{MAX_ADS_PER_DAY} за день)",
                is_auto=True
            )
            return

        # Проверка интервала между объявлениями
        if last_ad_time:
            time_since_last_ad = datetime.now() - last_ad_time
            if time_since_last_ad < MIN_AD_INTERVAL:
                await delete_message(message.chat.id, message.message_id)
                
                remaining_time = MIN_AD_INTERVAL - time_since_last_ad
                minutes = int(remaining_time.total_seconds() // 60)
                seconds = int(remaining_time.total_seconds() % 60)
                
                warning_msg = await message.answer(
                    f"⏳ <b>Слишком часто!</b>\n\n"
                    f"Пользователь {await get_user_mention(user_id)}, "
                    f"подождите еще {minutes} мин. {seconds} сек. перед следующим объявлением.",
                    parse_mode="HTML"
                )
                
                # Удаляем предупреждение через 30 секунд
                await asyncio.sleep(30)
                await delete_message(message.chat.id, warning_msg.message_id)
                return

        # Если все проверки пройдены, добавляем объявление в базу
        add_user_ad(user_id, text)

    # Автоматическое удаление команд бота от обычных пользователей
    if text.startswith('/') and not await is_chat_admin(user_id, message.chat.id):
        # Список разрешенных команд для обычных пользователей
        allowed_commands = ['/report', '/rules', '/order', '/start', '/donate']
        
        # Проверяем, является ли команда разрешенной
        command_parts = text.split()
        command_name = command_parts[0].lower()  # Получаем имя команды
        
        if command_name not in allowed_commands:
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

        📜 <b>Обязательно ознакомьтесь с правилами:</b>
        • Напишите /rules - чтобы прочитать правила
        • Напишите /order - чтобы узнать как сделать заказ

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
            
            # Очищаем истекшие варны
            cursor.execute("DELETE FROM warns WHERE expires_at <= ?", (datetime.now(),))
            
            # Деактивируем истекшие муты
            cursor.execute(
                "UPDATE mutes SET is_active = FALSE WHERE expires_at <= ? AND is_active = TRUE",
                (datetime.now(),)
            )
            
            # Деактивируем истекшие баны
            cursor.execute(
                "UPDATE bans SET is_active = FALSE WHERE expires_at <= ? AND is_active = TRUE",
                (datetime.now(),)
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
    asyncio.create_task(rules_scheduler())
    
    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
