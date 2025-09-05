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

# Настройка логирования для хостинга
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        RotatingFileHandler("bot.log", maxBytes=10485760, backupCount=5),  # 10MB per file, 5 backups
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Конфигурация бота из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN", "7271080423:AAHvrgBXya-82CRosyxkenpbfvO6LnNsnnA")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "1014311717,7461610956").split(',')))
CHAT_ID = int(os.getenv("CHAT_ID", "-1002125767388"))
WARN_EXPIRE_DAYS = int(os.getenv("WARN_EXPIRE_DAYS", "7"))

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
    conn = sqlite3.connect("bot_database.db")
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

    # Таблица для активаций пользователей
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS user_activations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL UNIQUE,
        activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
def add_warn(user_id: int, chat_id: int, reason: str, issued_by: int):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    expires_at = datetime.now() + timedelta(days=WARN_EXPIRE_DAYS)
    cursor.execute(
        "INSERT INTO warns (user_id, chat_id, reason, issued_by, expires_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, chat_id, reason, issued_by, expires_at),
    )
    conn.commit()
    conn.close()

def get_user_warns(user_id: int, chat_id: int) -> List[Dict[str, Any]]:
    conn = sqlite3.connect("bot_database.db")
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
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM warns WHERE id = ?", (warn_id,))
    conn.commit()
    conn.close()

def clear_user_warns(user_id: int, chat_id: int):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM warns WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)
    )
    conn.commit()
    conn.close()

def add_mute(user_id: int, chat_id: int, reason: str, issued_by: int, duration: timedelta = None):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    expires_at = datetime.now() + duration if duration else None
    cursor.execute(
        "INSERT INTO mutes (user_id, chat_id, reason, issued_by, expires_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, chat_id, reason, issued_by, expires_at),
    )
    conn.commit()
    conn.close()

def add_ban(user_id: int, chat_id: int, reason: str, issued_by: int, duration: timedelta = None):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    expires_at = datetime.now() + duration if duration else None
    cursor.execute(
        "INSERT INTO bans (user_id, chat_id, reason, issued_by, expires_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, chat_id, reason, issued_by, expires_at),
    )
    conn.commit()
    conn.close()

def add_admin_warn(user_id: int, reason: str, issued_by: int):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO admin_warns (user_id, reason, issued_by) VALUES (?, ?, ?)",
        (user_id, reason, issued_by),
    )
    conn.commit()
    conn.close()

def get_admin_warns(user_id: int) -> List[Dict[str, Any]]:
    conn = sqlite3.connect("bot_database.db")
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
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE admin_warns SET is_active = FALSE WHERE id = ?", (warn_id,))
    conn.commit()
    conn.close()

def remove_last_admin_warn(user_id: int):
    """Удаляет последнее предупреждение администратора"""
    conn = sqlite3.connect("bot_database.db")
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
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE admin_warns SET is_active = FALSE WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def add_admin(user_id: int, added_by: int):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)",
        (user_id, added_by),
    )
    conn.commit()
    conn.close()

def remove_admin(user_id: int):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def is_admin(user_id: int) -> bool:
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM admins WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def get_all_admins() -> List[int]:
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM admins")
    admins = [row[0] for row in cursor.fetchall()]
    conn.close()
    return admins

def is_user_activated(user_id: int) -> bool:
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM user_activations WHERE user_id = ?",
        (user_id,),
    )
    result = cursor.fetchone()
    conn.close()
    return result is not None

def activate_user(user_id: int):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO user_activations (user_id) VALUES (?)",
        (user_id,),
    )
    conn.commit()
    conn.close()

def add_user_ad(user_id: int, message_text: str):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO user_ads (user_id, message_text) VALUES (?, ?)",
        (user_id, message_text),
    )
    conn.commit()
    conn.close()

def get_today_ads_count(user_id: int) -> int:
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM user_ads WHERE user_id = ? AND DATE(sent_at) = DATE('now')",
        (user_id,),
    )
    count = cursor.fetchone()[0]
    conn.close()
    return count

def get_last_ad_time(user_id: int) -> Optional[datetime]:
    conn = sqlite3.connect("bot_database.db")
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
    conn = sqlite3.connect("bot_database.db")
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
    conn = sqlite3.connect("bot_database.db")
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
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO donations (user_id, amount, currency, message, is_anonymous) VALUES (?, ?, ?, ?, ?)",
        (user_id, amount, currency, message, is_anonymous),
    )
    conn.commit()
    conn.close()

def get_total_donations() -> float:
    """Получает общую сумму донатов"""
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(amount) FROM donations WHERE amount IS NOT NULL")
    result = cursor.fetchone()[0]
    conn.close()
    return result or 0.0

def get_recent_donations(limit: int = 10) -> List[Dict[str, Any]]:
    """Получает последние донаты"""
    conn = sqlite3.connect("bot_database.db")
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

async def warn_admin(user_id: int, reason: str = None, issued_by: int = None) -> bool:
    try:
        add_admin_warn(user_id, reason, issued_by)
        warns = get_admin_warns(user_id)

        reason_str = f"\n📝 <b>Причина:</b> {reason}" if reason else ""
        user_mention = await get_user_mention(user_id)
        issued_by_mention = await get_user_mention(issued_by) if issued_by else "Система"

        await bot.send_message(
            CHAT_ID,
            f"⚠️ <b>Предупреждение администратору</b>\n\n"
            f"👤 <b>Администратор:</b> {user_mention}\n"
            f"🔢 <b>Всего предупреждений:</b> {len(warns)}{reason_str}\n"
            f"👨‍⚖️ <b>Выдал:</b> {issued_by_mention}",
            parse_mode="HTML",
        )

        if len(warns) >= 3:
            await bot.send_message(
                CHAT_ID,
                f"🚫 <b>Администратор {user_mention} снят с должности за 3 предупреждения!</b>",
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

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📜 Правила", callback_data="rules")],
        [InlineKeyboardButton(text="🛍️ Как заказать", callback_data="order")],
        [InlineKeyboardButton(text="❤️ Поддержать бота", callback_data="donate")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
    ])

    await message.answer(
        "👋 <b>Добро пожаловать в бот барахолки!</b>\n\n"
        "🤖 Я помогаю поддерживать порядок в чате и следить за соблюдением правил.\n\n"
        "📋 <b>Доступные функции:</b>\n"
        "• Автоматическая модерация объявлений\n"
        "• Система предупреждений\n"
        "• Управление администраторами\n"
        "• И многое другое!\n\n"
        "⚡ <i>Выберите нужный раздел:</i>",
        parse_mode="HTML",
        reply_markup=keyboard
    )

@dp.message(Command("rules"))
async def cmd_rules(message: Message):
    if message.chat.type != ChatType.PRIVATE:
        return

    await message.answer(RULES_MESSAGE, parse_mode="HTML")

@dp.message(Command("order"))
async def cmd_order(message: Message):
    if message.chat.type != ChatType.PRIVATE:
        return

    await message.answer(ORDER_MESSAGE, parse_mode="HTML")

@dp.message(Command("donate"))
async def cmd_donate(message: Message):
    if message.chat.type != ChatType.PRIVATE:
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Перевести по ссылке", url=DONATE_LINK)],
        [InlineKeyboardButton(text="📊 Посмотреть статистику", callback_data="donate_stats")],
    ])

    await message.answer(
        DONATE_MESSAGE.format(donate_link=DONATE_LINK),
        parse_mode="HTML",
        reply_markup=keyboard
    )

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.chat.type != ChatType.PRIVATE:
        return

    # Получаем статистику из базы данных
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    
    # Количество активных варнов
    cursor.execute("SELECT COUNT(*) FROM warns WHERE expires_at > ?", (datetime.now(),))
    active_warns = cursor.fetchone()[0]
    
    # Количество активных мутов
    cursor.execute("SELECT COUNT(*) FROM mutes WHERE is_active = TRUE AND (expires_at IS NULL OR expires_at > ?)", (datetime.now(),))
    active_mutes = cursor.fetchone()[0]
    
    # Количество активных банов
    cursor.execute("SELECT COUNT(*) FROM bans WHERE is_active = TRUE AND (expires_at IS NULL OR expires_at > ?)", (datetime.now(),))
    active_bans = cursor.fetchone()[0]
    
    # Количество администраторов
    cursor.execute("SELECT COUNT(*) FROM admins")
    admins_count = cursor.fetchone()[0]
    
    # Общая сумма донатов
    total_donations = get_total_donations()
    
    conn.close()

    stats_text = (
        "📊 <b>Статистика бота</b>\n\n"
        f"⚠️ <b>Активных предупреждений:</b> {active_warns}\n"
        f"🔇 <b>Активных мутов:</b> {active_mutes}\n"
        f"🚫 <b>Активных банов:</b> {active_bans}\n"
        f"👨‍💼 <b>Администраторов бота:</b> {admins_count}\n"
        f"💰 <b>Общая сумма донатов:</b> {total_donations:.2f} RUB\n\n"
        "🤖 <i>Бот работает стабильно и следит за порядком!</i>"
    )

    await message.answer(stats_text, parse_mode="HTML")

@dp.message(Command("warn"))
async def cmd_warn(message: Message, command: CommandObject):
    if not await is_bot_admin(message.from_user.id):
        await message.reply("❌ У вас нет прав для использования этой команды.")
        return

    user_info = await resolve_user_reference(message, command.args)
    if not user_info:
        await message.reply(
            "❌ Неверный формат команды. Используйте:\n"
            "/warn @username причина\n"
            "или ответьте на сообщение пользователя с /warn причина"
        )
        return

    user_id, reason = user_info
    if await warn_user(message.chat.id, user_id, reason):
        await message.delete()

@dp.message(Command("unwarn"))
async def cmd_unwarn(message: Message, command: CommandObject):
    if not await is_bot_admin(message.from_user.id):
        await message.reply("❌ У вас нет прав для использования этой команды.")
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.reply(
            "❌ Неверный формат команды. Используйте:\n"
            "/unwarn @username\n"
            "или ответьте на сообщение пользователя с /unwarn"
        )
        return

    warns = get_user_warns(user_id, message.chat.id)
    if not warns:
        await message.reply("⚠️ У пользователя нет активных предупреждений.")
        return

    remove_warn(warns[-1]["id"])
    user_mention = await get_user_mention(user_id)
    await message.reply(f"✅ Снято последнее предупреждение с {user_mention}")

@dp.message(Command("warns"))
async def cmd_warns(message: Message, command: CommandObject):
    if not await is_bot_admin(message.from_user.id):
        await message.reply("❌ У вас нет прав для использования этой команды.")
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.reply(
            "❌ Неверный формат команды. Используйте:\n"
            "/warns @username\n"
            "или ответьте на сообщение пользователя с /warns"
        )
        return

    warns = get_user_warns(user_id, message.chat.id)
    user_mention = await get_user_mention(user_id)

    if not warns:
        await message.reply(f"ℹ️ У {user_mention} нет активных предупреждений.")
        return

    warns_text = "\n".join(
        f"{i+1}. {warn['reason']} (до {warn['expires_at'][:10]})"
        for i, warn in enumerate(warns)
    )

    await message.reply(
        f"⚠️ <b>Предупреждения {user_mention}:</b>\n\n{warns_text}\n\n"
        f"📊 <b>Всего:</b> {len(warns)} из 3",
        parse_mode="HTML"
    )

@dp.message(Command("mute"))
async def cmd_mute(message: Message, command: CommandObject):
    if not await is_bot_admin(message.from_user.id):
        await message.reply("❌ У вас нет прав для использования этой команды.")
        return

    args = command.args.split() if command.args else []
    duration = parse_time(args[0]) if args else MUTE_DURATION
    reason = " ".join(args[1:]) if len(args) > 1 else "Не указана"

    user_info = await resolve_user_reference(message, command.args)
    if not user_info:
        await message.reply(
            "❌ Неверный формат команды. Используйте:\n"
            "/mute @username время причина\n"
            "или ответьте на сообщение пользователя с /mute время причина\n\n"
            "📝 <b>Формат времени:</b> 30m, 2h, 1d, 1w",
            parse_mode="HTML"
        )
        return

    user_id, _ = user_info
    if await mute_user(message.chat.id, user_id, duration, reason):
        await message.delete()

@dp.message(Command("unmute"))
async def cmd_unmute(message: Message, command: CommandObject):
    if not await is_bot_admin(message.from_user.id):
        await message.reply("❌ У вас нет прав для использования этой команды.")
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.reply(
            "❌ Неверный формат команды. Используйте:\n"
            "/unmute @username\n"
            "или ответьте на сообщение пользователя с /unmute"
        )
        return

    if await unmute_user(message.chat.id, user_id):
        await message.delete()

@dp.message(Command("ban"))
async def cmd_ban(message: Message, command: CommandObject):
    if not await is_bot_admin(message.from_user.id):
        await message.reply("❌ У вас нет прав для использования этой команды.")
        return

    args = command.args.split() if command.args else []
    duration = parse_time(args[0]) if args else None
    reason = " ".join(args[1:]) if len(args) > 1 else "Не указана"

    user_info = await resolve_user_reference(message, command.args)
    if not user_info:
        await message.reply(
            "❌ Неверный формат команды. Используйте:\n"
            "/ban @username время причина\n"
            "или ответьте на сообщение пользователя с /ban время причина\n\n"
            "📝 <b>Формат времени:</b> 30m, 2h, 1d, 1w",
            parse_mode="HTML"
        )
        return

    user_id, _ = user_info
    if await ban_user(message.chat.id, user_id, duration, reason):
        await message.delete()

@dp.message(Command("unban"))
async def cmd_unban(message: Message, command: CommandObject):
    if not await is_bot_admin(message.from_user.id):
        await message.reply("❌ У вас нет прав для использования этой команды.")
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.reply(
            "❌ Неверный формат команды. Используйте:\n"
            "/unban @username\n"
            "или ответьте на сообщение пользователя с /unban"
        )
        return

    if await unban_user(message.chat.id, user_id):
        await message.delete()

@dp.message(Command("clean"))
async def cmd_clean(message: Message, command: CommandObject):
    if not await is_bot_admin(message.from_user.id):
        await message.reply("❌ У вас нет прав для использования этой команды.")
        return

    count = 10
    if command.args and command.args.isdigit():
        count = min(int(command.args), 100)

    try:
        await message.delete()
        for i in range(count):
            try:
                await bot.delete_message(message.chat.id, message.message_id - i - 1)
            except:
                pass
    except Exception as e:
        logger.error(f"Ошибка очистки чата: {e}")

@dp.message(Command("add_admin"))
async def cmd_add_admin(message: Message, command: CommandObject):
    if not await is_owner(message.from_user.id):
        await message.reply("❌ Только владелец может добавлять администраторов.")
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.reply(
            "❌ Неверный формат команды. Используйте:\n"
            "/add_admin @username\n"
            "или ответьте на сообщение пользователя с /add_admin"
        )
        return

    if await is_bot_admin(user_id):
        await message.reply("ℹ️ Этот пользователь уже является администратором.")
        return

    add_admin(user_id, message.from_user.id)
    user_mention = await get_user_mention(user_id)
    await message.reply(f"✅ {user_mention} добавлен в администраторы бота.")

@dp.message(Command("remove_admin"))
async def cmd_remove_admin(message: Message, command: CommandObject):
    if not await is_owner(message.from_user.id):
        await message.reply("❌ Только владелец может удалять администраторов.")
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.reply(
            "❌ Неверный формат команды. Используйте:\n"
            "/remove_admin @username\n"
            "или ответьте на сообщение пользователя с /remove_admin"
        )
        return

    if not is_admin(user_id):
        await message.reply("ℹ️ Этот пользователь не является администратором.")
        return

    remove_admin(user_id)
    user_mention = await get_user_mention(user_id)
    await message.reply(f"✅ {user_mention} удалён из администраторов бота.")

@dp.message(Command("admin_warn"))
async def cmd_admin_warn(message: Message, command: CommandObject):
    if not await is_owner(message.from_user.id):
        await message.reply("❌ Только владелец может выдавать предупреждения администраторам.")
        return

    user_info = await resolve_user_reference(message, command.args)
    if not user_info:
        await message.reply(
            "❌ Неверный формат команды. Используйте:\n"
            "/admin_warn @username причина\n"
            "или ответьте на сообщение пользователя с /admin_warn причина"
        )
        return

    user_id, reason = user_info
    if not is_admin(user_id):
        await message.reply("❌ Этот пользователь не является администратором.")
        return

    if await warn_admin(user_id, reason, message.from_user.id):
        await message.delete()

@dp.message(Command("admin_unwarn"))
async def cmd_admin_unwarn(message: Message, command: CommandObject):
    if not await is_owner(message.from_user.id):
        await message.reply("❌ Только владелец может снимать предупреждения администраторам.")
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.reply(
            "❌ Неверный формат команды. Используйте:\n"
            "/admin_unwarn @username\n"
            "или ответьте на сообщение пользователя с /admin_unwarn"
        )
        return

    if not is_admin(user_id):
        await message.reply("❌ Этот пользователь не является администратором.")
        return

    warns = get_admin_warns(user_id)
    if not warns:
        await message.reply("ℹ️ У администратора нет активных предупреждений.")
        return

    removed_warn_id = remove_last_admin_warn(user_id)
    if removed_warn_id:
        user_mention = await get_user_mention(user_id)
        await message.reply(f"✅ Снято последнее предупреждение с администратора {user_mention}")
    else:
        await message.reply("❌ Не удалось снять предупреждение.")

@dp.message(Command("admin_warns"))
async def cmd_admin_warns(message: Message, command: CommandObject):
    if not await is_owner(message.from_user.id):
        await message.reply("❌ Только владелец может просматривать предупреждения администраторов.")
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.reply(
            "❌ Неверный формат команды. Используйте:\n"
            "/admin_warns @username\n"
            "или ответьте на сообщение пользователя с /admin_warns"
        )
        return

    if not is_admin(user_id):
        await message.reply("❌ Этот пользователь не является администратором.")
        return

    warns = get_admin_warns(user_id)
    user_mention = await get_user_mention(user_id)

    if not warns:
        await message.reply(f"ℹ️ У администратора {user_mention} нет активных предупреждений.")
        return

    warns_text = "\n".join(
        f"{i+1}. {warn['reason']} (выдал {await get_user_mention(warn['issued_by']) if warn['issued_by'] else 'Система'})"
        for i, warn in enumerate(warns)
    )

    await message.reply(
        f"⚠️ <b>Предупреждения администратора {user_mention}:</b>\n\n{warns_text}\n\n"
        f"📊 <b>Всего:</b> {len(warns)} из 3",
        parse_mode="HTML"
    )

@dp.message(Command("admins"))
async def cmd_admins(message: Message):
    if not await is_bot_admin(message.from_user.id):
        await message.reply("❌ У вас нет прав для использования этой команды.")
        return

    admins = get_all_admins()
    if not admins:
        await message.reply("ℹ️ Нет добавленных администраторов.")
        return

    admins_list = []
    for admin_id in admins:
        try:
            user = await bot.get_chat(admin_id)
            name = user.first_name or user.username or str(admin_id)
            admins_list.append(f"• {name} (ID: {admin_id})")
        except:
            admins_list.append(f"• Неизвестный пользователь (ID: {admin_id})")

    await message.reply(
        f"👨‍💼 <b>Администраторы бота:</b>\n\n" + "\n".join(admins_list),
        parse_mode="HTML"
    )

@dp.message(Command("activate"))
async def cmd_activate(message: Message):
    if message.chat.type != ChatType.PRIVATE:
        return

    if is_user_activated(message.from_user.id):
        await message.reply("ℹ️ Ваш аккаунт уже активирован.")
        return

    activate_user(message.from_user.id)
    await message.reply(
        "✅ <b>Аккаунт активирован!</b>\n\n"
        "Теперь вы можете участвовать в чате и размещать объявления.\n"
        "📜 Не забудьте ознакомиться с правилами: /rules",
        parse_mode="HTML"
    )

@dp.message(Command("my_stats"))
async def cmd_my_stats(message: Message):
    if message.chat.type != ChatType.PRIVATE:
        return

    user_id = message.from_user.id
    
    # Получаем статистику пользователя
    warns = get_user_warns(user_id, CHAT_ID)
    today_ads = get_today_ads_count(user_id)
    last_ad = get_last_ad_time(user_id)
    violations = get_today_violations_count(user_id)
    
    last_ad_str = f"⏰ <b>Последнее объявление:</b> {last_ad.strftime('%H:%M') if last_ad else 'ещё не было'}\n" if last_ad else ""
    
    stats_text = (
        "📊 <b>Ваша статистика</b>\n\n"
        f"⚠️ <b>Активных предупреждений:</b> {len(warns)}/3\n"
        f"📢 <b>Объявлений сегодня:</b> {today_ads}/{MAX_ADS_PER_DAY}\n"
        f"🚫 <b>Нарушений сегодня:</b> {violations}\n"
        f"{last_ad_str}\n"
        "📈 <i>Соблюдайте правила и ваш рейтинг будет расти!</i>"
    )
    
    await message.answer(stats_text, parse_mode="HTML")

@dp.message(Command("send_rules"))
async def cmd_send_rules(message: Message):
    if not await is_bot_admin(message.from_user.id):
        await message.reply("❌ У вас нет прав для использования этой команды.")
        return

    await send_rules_and_order_message()
    await message.reply("✅ Правила и информация о заказе отправлены.")

# Обработчики callback-запросов
@dp.callback_query(F.data == "rules")
async def callback_rules(callback: types.CallbackQuery):
    await callback.message.edit_text(RULES_MESSAGE, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "order")
async def callback_order(callback: types.CallbackQuery):
    await callback.message.edit_text(ORDER_MESSAGE, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "donate")
async def callback_donate(callback: types.CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Перевести по ссылке", url=DONATE_LINK)],
        [InlineKeyboardButton(text="📊 Статистика донатов", callback_data="donate_stats")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")],
    ])

    await callback.message.edit_text(
        DONATE_MESSAGE.format(donate_link=DONATE_LINK),
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()

@dp.callback_query(F.data == "donate_stats")
async def callback_donate_stats(callback: types.CallbackQuery):
    total_donations = get_total_donations()
    recent_donations = get_recent_donations(5)
    
    donations_text = ""
    for donation in recent_donations:
        if donation['is_anonymous']:
            user_text = "Анонимный донатер"
        else:
            try:
                user = await bot.get_chat(donation['user_id'])
                user_text = user.first_name or user.username or f"ID: {donation['user_id']}"
            except:
                user_text = f"ID: {donation['user_id']}"
        
        amount_text = f"{donation['amount']:.2f} {donation['currency']}" if donation['amount'] else "не указана"
        donations_text += f"• {user_text}: {amount_text}"
        if donation['message']:
            donations_text += f" - {donation['message']}"
        donations_text += f" ({donation['donated_at'][:10]})\n"
    
    stats_text = (
        "💰 <b>Статистика донатов</b>\n\n"
        f"💵 <b>Общая сумма:</b> {total_donations:.2f} RUB\n\n"
        "🎁 <b>Последние донаты:</b>\n"
        f"{donations_text if donations_text else 'Пока нет донатов'}\n\n"
        "🙏 <i>Спасибо за вашу поддержку!</i>"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Поддержать", callback_data="donate")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")],
    ])
    
    await callback.message.edit_text(stats_text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == "stats")
async def callback_stats(callback: types.CallbackQuery):
    # Получаем статистику (аналогично команде /stats)
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM warns WHERE expires_at > ?", (datetime.now(),))
    active_warns = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM mutes WHERE is_active = TRUE AND (expires_at IS NULL OR expires_at > ?)", (datetime.now(),))
    active_mutes = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM bans WHERE is_active = TRUE AND (expires_at IS NULL OR expires_at > ?)", (datetime.now(),))
    active_bans = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM admins")
    admins_count = cursor.fetchone()[0]
    
    total_donations = get_total_donations()
    
    conn.close()

    stats_text = (
        "📊 <b>Статистика бота</b>\n\n"
        f"⚠️ <b>Активных предупреждений:</b> {active_warns}\n"
        f"🔇 <b>Активных мутов:</b> {active_mutes}\n"
        f"🚫 <b>Активных банов:</b> {active_bans}\n"
        f"👨‍💼 <b>Администраторов бота:</b> {admins_count}\n"
        f"💰 <b>Общая сумма донатов:</b> {total_donations:.2f} RUB\n\n"
        "🤖 <i>Бот работает стабильно и следит за порядком!</i>"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")],
    ])
    
    await callback.message.edit_text(stats_text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == "back_to_main")
async def callback_back_to_main(callback: types.CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📜 Правила", callback_data="rules")],
        [InlineKeyboardButton(text="🛍️ Как заказать", callback_data="order")],
        [InlineKeyboardButton(text="❤️ Поддержать бота", callback_data="donate")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
    ])

    await callback.message.edit_text(
        "👋 <b>Добро пожаловать в бот барахолки!</b>\n\n"
        "🤖 Я помогаю поддерживать порядок в чате и следить за соблюдением правил.\n\n"
        "📋 <b>Доступные функции:</b>\n"
        "• Автоматическая модерация объявлений\n"
        "• Система предупреждений\n"
        "• Управление администраторами\n"
        "• И многое другое!\n\n"
        "⚡ <i>Выберите нужный раздел:</i>",
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()

# Обработчики сообщений
@dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def handle_group_message(message: Message):
    try:
        user_id = message.from_user.id
        chat_id = message.chat.id
        
        # Пропускаем сообщения от администраторов бота
        if await is_bot_admin(user_id):
            return
            
        # Пропускаем служебные сообщения
        if not message.text or message.text.startswith('/'):
            return
            
        text = message.text.lower()
        
        # Проверка на триггерные слова
        for trigger_word, variants in TRIGGER_WORDS.items():
            for variant in variants:
                if variant in text:
                    # Цензурируем сообщение
                    censored_text = censor_trigger_word(message.text, trigger_word)
                    await message.delete()
                    
                    warning_msg = await message.answer(
                        f"⚠️ <b>Внимание!</b>\n\n"
                        f"Сообщение от {await get_user_mention(user_id)} было удалено.\n"
                        f"📝 <b>Причина:</b> использование запрещённого слова\n\n"
                        f"💬 <b>Исходный текст:</b> <code>{censored_text}</code>",
                        parse_mode="HTML"
                    )
                    
                    # Удаляем предупреждение через 10 секунд
                    await asyncio.sleep(10)
                    await warning_msg.delete()
                    return
        
        # Проверка на объявления
        if is_ad_message(message.text):
            # Проверяем активацию пользователя
            if not is_user_activated(user_id):
                await message.delete()
                warning_msg = await message.answer(
                    f"⚠️ <b>Внимание!</b>\n\n"
                    f"Сообщение от {await get_user_mention(user_id)} было удалено.\n"
                    f"📝 <b>Причина:</b> неактивированный аккаунт\n\n"
                    f"🔓 <b>Решение:</b> активируйте аккаунт через бота @{bot._me.username} командой /activate",
                    parse_mode="HTML"
                )
                await asyncio.sleep(10)
                await warning_msg.delete()
                return
            
            current_time = datetime.now()
            today_ads = get_today_ads_count(user_id)
            last_ad_time = get_last_ad_time(user_id)
            
            # Проверка лимита объявлений в день
            if today_ads >= MAX_ADS_PER_DAY:
                await message.delete()
                add_ad_violation(user_id)
                violations_count = get_today_violations_count(user_id)
                
                # Определяем срок мута в зависимости от количества нарушений
                mute_duration = MUTE_DURATION * min(violations_count, 7)  # Максимум 7 дней
                
                warning_msg = await message.answer(
                    f"⚠️ <b>Превышен лимит объявлений!</b>\n\n"
                    f"👤 <b>Пользователь:</b> {await get_user_mention(user_id)}\n"
                    f"📊 <b>Объявлений сегодня:</b> {today_ads}/{MAX_ADS_PER_DAY}\n"
                    f"🚫 <b>Нарушение:</b> {violations_count}\n\n"
                    f"🔇 <b>Наказание:</b> мут на {await format_duration(mute_duration)}",
                    parse_mode="HTML"
                )
                
                await mute_user(chat_id, user_id, mute_duration, "Превышение лимита объявлений", is_auto=True)
                await asyncio.sleep(10)
                await warning_msg.delete()
                return
            
            # Проверка интервала между объявлениями
            if last_ad_time:
                time_since_last_ad = current_time - last_ad_time
                if time_since_last_ad < MIN_AD_INTERVAL:
                    remaining_time = MIN_AD_INTERVAL - time_since_last_ad
                    await message.delete()
                    
                    warning_msg = await message.answer(
                        f"⚠️ <b>Слишком частое размещение объявлений!</b>\n\n"
                        f"👤 <b>Пользователь:</b> {await get_user_mention(user_id)}\n"
                        f"⏰ <b>Прошло времени:</b> {await format_duration(time_since_last_ad)}\n"
                        f"🕒 <b>Минимальный интервал:</b> {await format_duration(MIN_AD_INTERVAL)}\n"
                        f"⏳ <b>Осталось ждать:</b> {await format_duration(remaining_time)}\n\n"
                        f"📝 <i>Повторное нарушение приведёт к муту!</i>",
                        parse_mode="HTML"
                    )
                    
                    await asyncio.sleep(10)
                    await warning_msg.delete()
                    return
            
            # Если все проверки пройдены, добавляем объявление в базу
            add_user_ad(user_id, message.text)
            
    except Exception as e:
        logger.error(f"Ошибка обработки сообщения: {e}")

@dp.message(F.chat.type == ChatType.PRIVATE)
async def handle_private_message(message: Message):
    # Обработка личных сообщений боту
    if message.text and not message.text.startswith('/'):
        await message.answer(
            "🤖 <b>Привет! Я бот модерации для чата барахолки.</b>\n\n"
            "📋 <b>Доступные команды:</b>\n"
            "/start - Главное меню\n"
            "/rules - Правила чата\n"
            "/order - Как сделать заказ\n"
            "/donate - Поддержать бота\n"
            "/stats - Статистика бота\n"
            "/activate - Активировать аккаунт\n"
            "/my_stats - Ваша статистика\n\n"
            "⚡ <i>Выберите нужную команду!</i>",
            parse_mode="HTML"
        )

# Обработчик новых участников
@dp.chat_member()
async def handle_chat_member_update(update: ChatMemberUpdated):
    try:
        if update.chat.id != CHAT_ID:
            return
            
        if update.new_chat_member.status == ChatMemberStatus.MEMBER:
            user_id = update.new_chat_member.user.id
            
            # Отправляем приветственное сообщение
            welcome_text = (
                f"👋 <b>Добро пожаловать, {await get_user_mention(user_id)}!</b>\n\n"
                "📜 <b>Обязательно ознакомьтесь с правилами:</b>\n"
                "• Используйте /rules в личных сообщениях бота\n"
                "• Активируйте аккаунт командой /activate\n\n"
                "🤖 <i>Бот @{bot._me.username} следит за порядком!</i>"
            )
            
            await bot.send_message(CHAT_ID, welcome_text, parse_mode="HTML")
            
    except Exception as e:
        logger.error(f"Ошибка обработки нового участника: {e}")

# Функция для периодической очистки устаревших данных
async def cleanup_task():
    """Периодическая очистка устаревших данных из базы"""
    while True:
        try:
            conn = sqlite3.connect("bot_database.db")
            cursor = conn.cursor()
            
            # Удаляем устаревшие варны
            cursor.execute("DELETE FROM warns WHERE expires_at <= ?", (datetime.now(),))
            
            # Деактивируем истёкшие муты
            cursor.execute(
                "UPDATE mutes SET is_active = FALSE WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (datetime.now(),)
            )
            
            # Деактивируем истёкшие баны
            cursor.execute(
                "UPDATE bans SET is_active = FALSE WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (datetime.now(),)
            )
            
            # Очищаем старые записи об объявлениях (старше 30 дней)
            thirty_days_ago = datetime.now() - timedelta(days=30)
            cursor.execute("DELETE FROM user_ads WHERE sent_at <= ?", (thirty_days_ago,))
            
            # Очищаем старые нарушения лимита (старше 7 дней)
            seven_days_ago = datetime.now() - timedelta(days=7)
            cursor.execute("DELETE FROM ad_limit_violations WHERE violation_date <= ?", (seven_days_ago,))
            
            conn.commit()
            conn.close()
            
            logger.info("Очистка устаревших данных выполнена успешно")
            
        except Exception as e:
            logger.error(f"Ошибка при очистке данных: {e}")
        
        # Ожидаем 1 час до следующей очистки
        await asyncio.sleep(3600)

# Функция для проверки и восстановления мутов/банов при перезапуске
async def restore_restrictions():
    """Восстанавливает активные муты и баны при перезапуске бота"""
    try:
        conn = sqlite3.connect("bot_database.db")
        cursor = conn.cursor()
        
        # Восстанавливаем активные муты
        cursor.execute(
            "SELECT user_id, chat_id, reason, expires_at FROM mutes WHERE is_active = TRUE AND (expires_at IS NULL OR expires_at > ?)",
            (datetime.now(),)
        )
        active_mutes = cursor.fetchall()
        
        for user_id, chat_id, reason, expires_at in active_mutes:
            try:
                duration = None
                if expires_at:
                    expires_dt = datetime.fromisoformat(expires_at)
                    duration = expires_dt - datetime.now()
                    if duration.total_seconds() <= 0:
                        continue
                
                await mute_user(chat_id, user_id, duration, f"Восстановление: {reason}", True)
                logger.info(f"Восстановлен мут для пользователя {user_id} в чате {chat_id}")
            except Exception as e:
                logger.error(f"Ошибка восстановления мута для {user_id}: {e}")
        
        # Восстанавливаем активные баны
        cursor.execute(
            "SELECT user_id, chat_id, reason, expires_at FROM bans WHERE is_active = TRUE AND (expires_at IS NULL OR expires_at > ?)",
            (datetime.now(),)
        )
        active_bans = cursor.fetchall()
        
        for user_id, chat_id, reason, expires_at in active_bans:
            try:
                duration = None
                if expires_at:
                    expires_dt = datetime.fromisoformat(expires_at)
                    duration = expires_dt - datetime.now()
                    if duration.total_seconds() <= 0:
                        continue
                
                await ban_user(chat_id, user_id, duration, f"Восстановление: {reason}")
                logger.info(f"Восстановлен бан для пользователя {user_id} в чате {chat_id}")
            except Exception as e:
                logger.error(f"Ошибка восстановления бана для {user_id}: {e}")
        
        conn.close()
        
    except Exception as e:
        logger.error(f"Ошибка при восстановлении ограничений: {e}")

# Обработка ошибок
@dp.errors()
async def errors_handler(update: types.Update, exception: Exception):
    """Обработчик ошибок"""
    try:
        logger.error(f"Ошибка при обработке update {update}: {exception}")
    except Exception as e:
        logger.error(f"Ошибка в обработчике ошибок: {e}")
    return True

# Запуск бота
async def main():
    """Основная функция запуска бота"""
    logger.info("Запуск бота...")
    
    try:
        # Восстанавливаем ограничения при запуске
        await restore_restrictions()
        
        # Запускаем фоновые задачи
        asyncio.create_task(rules_scheduler())
        asyncio.create_task(cleanup_task())
        
        logger.info("Бот запущен успешно")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске бота: {e}")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    # Создаем директорию для логов если её нет
    os.makedirs("logs", exist_ok=True)
    
    # Запускаем бота
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Неожиданная ошибка: {e}")
