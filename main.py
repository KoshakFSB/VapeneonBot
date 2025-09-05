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

# Настройка логирования с ротацией
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        RotatingFileHandler(
            "bot.log", 
            maxBytes=5*1024*1024,  # 5 MB
            backupCount=5,
            encoding='utf-8'
        ),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Конфигурация бота
BOT_TOKEN = "7271080423:AAHvrgBXya-82CRosyxkenpbfvO6LnNsnnA"  # Замените на ваш токен
ADMIN_IDS = [1014311717, 7461610956]  # Ваши ID администраторов
CHAT_ID = -1002125767388  # ID вашего чата
WARN_EXPIRE_DAYS = 7

# Ограничения
MAX_ADS_PER_DAY = 5
MIN_AD_INTERVAL = timedelta(hours=1.5)
MUTE_DURATION = timedelta(days=1)
RULES_INTERVAL = timedelta(hours=1.5)  # Интервал отправки правил

# Данные для донатов
DONATE_LINK = "https://www.sberbank.com/sms/pbpn?requisiteNumber=2202208057115496"
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

    # Таблица для донатов (новая таблица)
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

Вы можете заordered качественные товары у владельца барахолки: 
👉 @darknesss43 (Вика) 👈
или администратора:
👉 @barsss_amnyam 👈

<b>Процесс заказа:</b>
1️⃣ Напишите, что хотите заordered
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

        message_text = (
            f"⚠️ <b>Предупреждение администратору</b>\n\n"
            f"👨‍💼 <b>Администратор:</b> {user_mention}\n"
            f"🔢 <b>Всего предупреждений:</b> {len(warns)}{reason_str}\n"
            f"👤 <b>Выдал:</b> {issued_by_mention}"
        )

        # Отправляем сообщение в чат
        await bot.send_message(CHAT_ID, message_text, parse_mode="HTML")
        return True
    except Exception as e:
        logger.error(f"Ошибка выдачи предупреждения администратору: {e}")
        return False

async def check_triggers(text: str) -> Optional[str]:
    """Проверяет текст на наличие триггерных слов и возвращает найденное слово"""
    text_lower = text.lower()
    for trigger, variants in TRIGGER_WORDS.items():
        for variant in variants:
            if variant in text_lower:
                return trigger
    return None

async def check_ads_limit(user_id: int, message_text: str) -> Tuple[bool, Optional[str]]:
    """Проверяет лимит объявлений пользователя"""
    today_ads = get_today_ads_count(user_id)
    last_ad_time = get_last_ad_time(user_id)
    
    # Проверяем дневной лимит
    if today_ads >= MAX_ADS_PER_DAY:
        add_ad_violation(user_id)
        violations_count = get_today_violations_count(user_id)
        
        if violations_count == 1:
            return False, "first_warning"
        elif violations_count == 2:
            return False, "second_warning"
        else:
            return False, "mute"
    
    # Проверяем интервал между объявлениями
    if last_ad_time:
        time_since_last_ad = datetime.now() - last_ad_time
        if time_since_last_ad < MIN_AD_INTERVAL:
            remaining_time = MIN_AD_INTERVAL - time_since_last_ad
            minutes = int(remaining_time.total_seconds() // 60)
            return False, f"interval_{minutes}"
    
    # Если все проверки пройдены, добавляем объявление
    add_user_ad(user_id, message_text)
    return True, None

# Обработчики команд
@dp.message(Command("start"))
async def cmd_start(message: Message):
    if message.chat.type != ChatType.PRIVATE:
        return
    
    user_id = message.from_user.id
    if not is_user_activated(user_id):
        activate_user(user_id)
        await message.answer(
            "✅ <b>Активация завершена!</b>\n\n"
            "Теперь вы можете пользоваться ботом. "
            "Для просмотра доступных команд используйте /help",
            parse_mode="HTML"
        )
    else:
        await message.answer(
            "🤖 <b>Добро пожаловать!</b>\n\n"
            "Это бот для модерации чата. "
            "Для просмотра доступных команд используйте /help",
            parse_mode="HTML"
        )

@dp.message(Command("help"))
async def cmd_help(message: Message):
    if message.chat.type != ChatType.PRIVATE:
        return
    
    help_text = """
📋 <b>Доступные команды:</b>

👮‍♂️ <b>Для администраторов:</b>
• /mute [время] [причина] - Замутить пользователя
• /unmute - Размутить пользователя
• /ban [время] [причина] - Забанить пользователя
• /unban - Разбанить пользователя
• /warn [причина] - Выдать предупреждение
• /unwarn - Снять предупреждение
• /warns - Посмотреть предупреждения пользователя
• /clearwarns - Очистить все предупреждения

👨‍💼 <b>Для владельцев бота:</b>
• /admin add [id] - Добавить администратора
• /admin remove [id] - Удалить администратора
• /admin list - Список администраторов
• /adminwarn [причина] - Выдать предупреждение администратору
• /adminunwarn - Снять предупреждение администратору
• /adminwarns - Посмотреть предупреждения администратора

📊 <b>Общие команды:</b>
• /rules - Правила чата
• /order - Как сделать заказ
• /donate - Поддержать развитие бота
• /stats - Статистика бота

💡 <i>Для использования команд в чате, убедитесь что бот имеет необходимые права!</i>
"""
    await message.answer(help_text, parse_mode="HTML")

@dp.message(Command("rules"))
async def cmd_rules(message: Message):
    await message.answer(RULES_MESSAGE, parse_mode="HTML")

@dp.message(Command("order"))
async def cmd_order(message: Message):
    await message.answer(ORDER_MESSAGE, parse_mode="HTML")

@dp.message(Command("donate"))
async def cmd_donate(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❤️ Поддержать проект", url=DONATE_LINK)]
    ])
    
    await message.answer(
        DONATE_MESSAGE.format(donate_link=DONATE_LINK),
        parse_mode="HTML",
        reply_markup=keyboard,
        disable_web_page_preview=True
    )

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if not await is_admin_user(message.from_user.id):
        await message.answer("❌ Эта команда только для администраторов!")
        return
    
    try:
        # Получаем статистику из базы данных
        conn = sqlite3.connect("bot_database.db")
        cursor = conn.cursor()
        
        # Количество варнов
        cursor.execute("SELECT COUNT(*) FROM warns WHERE expires_at > ?", (datetime.now(),))
        active_warns = cursor.fetchone()[0]
        
        # Количество мутов
        cursor.execute("SELECT COUNT(*) FROM mutes WHERE is_active = TRUE AND (expires_at IS NULL OR expires_at > ?)", (datetime.now(),))
        active_mutes = cursor.fetchone()[0]
        
        # Количество банов
        cursor.execute("SELECT COUNT(*) FROM bans WHERE is_active = TRUE AND (expires_at IS NULL OR expires_at > ?)", (datetime.now(),))
        active_bans = cursor.fetchone()[0]
        
        # Количество администраторов
        cursor.execute("SELECT COUNT(*) FROM admins")
        admins_count = cursor.fetchone()[0]
        
        # Общая сумма донатов
        total_donations = get_total_donations()
        
        conn.close()
        
        stats_text = f"""
📊 <b>Статистика бота</b>

⚠️ <b>Активные предупреждения:</b> {active_warns}
🔇 <b>Активные муты:</b> {active_mutes}
🚫 <b>Активные баны:</b> {active_bans}
👨‍💼 <b>Администраторы:</b> {admins_count}
💰 <b>Общая сумма донатов:</b> {total_donations:.2f} руб

🤖 <b>Версия бота:</b> 2.0
📅 <b>Последнее обновление:</b> 2024
"""
        await message.answer(stats_text, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        await message.answer("❌ Ошибка получения статистики!")

@dp.message(Command("mute"))
async def cmd_mute(message: Message, command: CommandObject):
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        await message.answer("❌ Эта команда только для администраторов чата!")
        return
    
    user_ref = await resolve_user_reference(message, command.args)
    if not user_ref:
        await message.answer("❌ Укажите пользователя! Ответьте на сообщение или укажите ID/@username")
        return
    
    user_id, reason = user_ref
    
    # Парсим время из аргументов
    time_str = None
    if command.args:
        args = command.args.split()
        for arg in args:
            parsed_time = parse_time(arg)
            if parsed_time:
                time_str = arg
                # Убираем время из причины
                if reason:
                    reason = reason.replace(arg, '').strip()
                break
    
    duration = parse_time(time_str) if time_str else MUTE_DURATION
    
    success = await mute_user(message.chat.id, user_id, duration, reason)
    if success:
        await message.delete()
    else:
        await message.answer("❌ Ошибка при муте пользователя!")

@dp.message(Command("unmute"))
async def cmd_unmute(message: Message, command: CommandObject):
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        await message.answer("❌ Эта команда только для администраторов чата!")
        return
    
    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите пользователя! Ответьте на сообщение или укажите ID/@username")
        return
    
    success = await unmute_user(message.chat.id, user_id)
    if success:
        await message.delete()
    else:
        await message.answer("❌ Ошибка при размуте пользователя!")

@dp.message(Command("ban"))
async def cmd_ban(message: Message, command: CommandObject):
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        await message.answer("❌ Эта команда только для администраторов чата!")
        return
    
    user_ref = await resolve_user_reference(message, command.args)
    if not user_ref:
        await message.answer("❌ Укажите пользователя! Ответьте на сообщение или укажите ID/@username")
        return
    
    user_id, reason = user_ref
    
    # Парсим время из аргументов
    time_str = None
    duration = None
    if command.args:
        args = command.args.split()
        for arg in args:
            parsed_time = parse_time(arg)
            if parsed_time:
                time_str = arg
                duration = parsed_time
                # Убираем время из причины
                if reason:
                    reason = reason.replace(arg, '').strip()
                break
    
    success = await ban_user(message.chat.id, user_id, duration, reason)
    if success:
        await message.delete()
    else:
        await message.answer("❌ Ошибка при бане пользователя!")

@dp.message(Command("unban"))
async def cmd_unban(message: Message, command: CommandObject):
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        await message.answer("❌ Эта команда только для администраторов чата!")
        return
    
    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите пользователя! Ответьте на сообщение или укажите ID/@username")
        return
    
    success = await unban_user(message.chat.id, user_id)
    if success:
        await message.delete()
    else:
        await message.answer("❌ Ошибка при разбане пользователя!")

@dp.message(Command("warn"))
async def cmd_warn(message: Message, command: CommandObject):
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        await message.answer("❌ Эта команда только для администраторов чата!")
        return
    
    user_ref = await resolve_user_reference(message, command.args)
    if not user_ref:
        await message.answer("❌ Укажите пользователя! Ответьте на сообщение или укажите ID/@username")
        return
    
    user_id, reason = user_ref
    
    success = await warn_user(message.chat.id, user_id, reason)
    if success:
        await message.delete()
    else:
        await message.answer("❌ Ошибка при выдаче предупреждения!")

@dp.message(Command("unwarn"))
async def cmd_unwarn(message: Message, command: CommandObject):
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        await message.answer("❌ Эта команда только для администраторов чата!")
        return
    
    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите пользователя! Ответьте на сообщение или укажите ID/@username")
        return
    
    warns = get_user_warns(user_id, message.chat.id)
    if not warns:
        await message.answer("❌ У пользователя нет активных предупреждений!")
        return
    
    # Удаляем последнее предупреждение
    last_warn = warns[-1]
    remove_warn(last_warn["id"])
    
    user_mention = await get_user_mention(user_id)
    await message.answer(
        f"✅ <b>Предупреждение снято</b>\n\n"
        f"👤 <b>Пользователь:</b> {user_mention}\n"
        f"🔢 <b>Осталось предупреждений:</b> {len(warns) - 1}",
        parse_mode="HTML"
    )

@dp.message(Command("warns"))
async def cmd_warns(message: Message, command: CommandObject):
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        await message.answer("❌ Эта команда только для администраторов чата!")
        return
    
    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите пользователя! Ответьте на сообщение или укажите ID/@username")
        return
    
    warns = get_user_warns(user_id, message.chat.id)
    user_mention = await get_user_mention(user_id)
    
    if not warns:
        await message.answer(f"✅ У пользователя {user_mention} нет активных предупреждений!")
        return
    
    warns_text = "\n".join(
        [f"• {warn['reason']} ({warn['issued_at']})" for warn in warns]
    )
    
    await message.answer(
        f"⚠️ <b>Предупреждения пользователя {user_mention}</b>\n\n"
        f"{warns_text}\n\n"
        f"📅 <b>Всего:</b> {len(warns)} из 3",
        parse_mode="HTML"
    )

@dp.message(Command("clearwarns"))
async def cmd_clearwarns(message: Message, command: CommandObject):
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        await message.answer("❌ Эта команда только для администраторов чата!")
        return
    
    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите пользователя! Ответьте на сообщение или укажите ID/@username")
        return
    
    clear_user_warns(user_id, message.chat.id)
    user_mention = await get_user_mention(user_id)
    
    await message.answer(
        f"✅ <b>Все предупреждения очищены</b>\n\n"
        f"👤 <b>Пользователь:</b> {user_mention}",
        parse_mode="HTML"
    )

@dp.message(Command("admin"))
async def cmd_admin(message: Message, command: CommandObject):
    if not await is_owner(message.from_user.id):
        await message.answer("❌ Эта команда только для владельца бота!")
        return
    
    if not command.args:
        await message.answer("❌ Укажите действие: add, remove, list")
        return
    
    args = command.args.split()
    action = args[0].lower()
    
    if action == "add":
        if len(args) < 2:
            await message.answer("❌ Укажите ID пользователя!")
            return
        
        try:
            user_id = int(args[1])
            add_admin(user_id, message.from_user.id)
            user_mention = await get_user_mention(user_id)
            await message.answer(f"✅ Пользователь {user_mention} добавлен в администраторы!")
        except ValueError:
            await message.answer("❌ Неверный формат ID!")
    
    elif action == "remove":
        if len(args) < 2:
            await message.answer("❌ Укажите ID пользователя!")
            return
        
        try:
            user_id = int(args[1])
            remove_admin(user_id)
            user_mention = await get_user_mention(user_id)
            await message.answer(f"✅ Пользователь {user_mention} удален из администраторов!")
        except ValueError:
            await message.answer("❌ Неверный формат ID!")
    
    elif action == "list":
        admins = get_all_admins()
        if not admins:
            await message.answer("📋 Список администраторов пуст!")
            return
        
        admin_list = []
        for admin_id in admins:
            mention = await get_user_mention(admin_id)
            admin_list.append(f"• {mention}")
        
        await message.answer(
            f"👨‍💼 <b>Список администраторов:</b>\n\n" + "\n".join(admin_list),
            parse_mode="HTML"
        )
    
    else:
        await message.answer("❌ Неизвестное действие! Используйте: add, remove, list")

@dp.message(Command("adminwarn"))
async def cmd_adminwarn(message: Message, command: CommandObject):
    if not await is_owner(message.from_user.id):
        await message.answer("❌ Эта команда только для владельца бота!")
        return
    
    user_ref = await resolve_user_reference(message, command.args)
    if not user_ref:
        await message.answer("❌ Укажите администратора! Ответьте на сообщение или укажите ID/@username")
        return
    
    user_id, reason = user_ref
    
    if not await is_admin_user(user_id):
        await message.answer("❌ Этот пользователь не является администратором!")
        return
    
    success = await warn_admin(user_id, reason, message.from_user.id)
    if not success:
        await message.answer("❌ Ошибка при выдаче предупреждения администратору!")

@dp.message(Command("adminunwarn"))
async def cmd_adminunwarn(message: Message, command: CommandObject):
    if not await is_owner(message.from_user.id):
        await message.answer("❌ Эта команда только для владельца бота!")
        return
    
    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите администратора! Ответьте на сообщение или укажите ID/@username")
        return
    
    if not await is_admin_user(user_id):
        await message.answer("❌ Этот пользователь не является администратором!")
        return
    
    warn_id = remove_last_admin_warn(user_id)
    if warn_id:
        user_mention = await get_user_mention(user_id)
        await message.answer(f"✅ Последнее предупреждение снято с администратора {user_mention}!")
    else:
        await message.answer("❌ У администратора нет активных предупреждений!")

@dp.message(Command("adminwarns"))
async def cmd_adminwarns(message: Message, command: CommandObject):
    if not await is_owner(message.from_user.id):
        await message.answer("❌ Эта команда только для владельца бота!")
        return
    
    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите администратора! Ответьте на сообщение или укажите ID/@username")
        return
    
    if not await is_admin_user(user_id):
        await message.answer("❌ Этот пользователь не является администратором!")
        return
    
    warns = get_admin_warns(user_id)
    user_mention = await get_user_mention(user_id)
    
    if not warns:
        await message.answer(f"✅ У администратора {user_mention} нет активных предупреждений!")
        return
    
    warns_text = "\n".join(
        [f"• {warn['reason']} (выдал: {await get_user_mention(warn['issued_by'])}, {warn['issued_at']})" for warn in warns]
    )
    
    await message.answer(
        f"⚠️ <b>Предупреждения администратора {user_mention}</b>\n\n"
        f"{warns_text}\n\n"
        f"📅 <b>Всего:</b> {len(warns)}",
        parse_mode="HTML"
    )

# Обработчики сообщений
@dp.message(F.text)
async def handle_message(message: Message):
    if message.chat.type != ChatType.SUPERGROUP or message.chat.id != CHAT_ID:
        return
    
    user_id = message.from_user.id
    text = message.text or message.caption or ""
    
    # Проверяем, является ли пользователь администратором чата
    is_admin_chat = await is_chat_admin(user_id, message.chat.id)
    
    # Проверяем триггерные слова (только для не-администраторов)
    if not is_admin_chat:
        trigger = await check_triggers(text)
        if trigger:
            censored_text = censor_trigger_word(text, trigger)
            await delete_message(message.chat.id, message.message_id)
            
            user_mention = await get_user_mention(user_id)
            warning_msg = await message.answer(
                f"⚠️ <b>Внимание!</b>\n\n"
                f"👤 {user_mention}, использование триггерных слов запрещено!\n"
                f"📝 <b>Сообщение:</b> {censored_text}\n\n"
                f"❗ <i>Следующее нарушение приведет к муту!</i>",
                parse_mode="HTML"
            )
            
            # Удаляем предупреждение через 10 секунд
            await asyncio.sleep(10)
            await delete_message(message.chat.id, warning_msg.message_id)
            return
    
    # Проверяем объявления (только для не-администраторов)
    if not is_admin_chat and is_ad_message(text):
        allowed, error_type = await check_ads_limit(user_id, text)
        
        if not allowed:
            await delete_message(message.chat.id, message.message_id)
            user_mention = await get_user_mention(user_id)
            
            if error_type == "first_warning":
                warning_msg = await message.answer(
                    f"⚠️ <b>Превышен лимит объявлений!</b>\n\n"
                    f"👤 {user_mention}, вы превысили дневной лимит объявлений (5 в день).\n"
                    f"❗ <i>Следующее нарушение приведет к муту!</i>",
                    parse_mode="HTML"
                )
            elif error_type == "second_warning":
                warning_msg = await message.answer(
                    f"🚫 <b>Последнее предупреждение!</b>\n\n"
                    f"👤 {user_mention}, вы повторно превысили лимит объявлений.\n"
                    f"❗ <i>Следующее нарушение приведет к муту на 1 день!</i>",
                    parse_mode="HTML"
                )
            elif error_type == "mute":
                await mute_user(message.chat.id, user_id, MUTE_DURATION, "Превышение лимита объявлений", True)
                return
            elif error_type.startswith("interval_"):
                minutes = error_type.split("_")[1]
                warning_msg = await message.answer(
                    f"⏳ <b>Слишком часто!</b>\n\n"
                    f"👤 {user_mention}, между объявлениями должен быть интервал не менее 1.5 часа.\n"
                    f"🕐 <b>Подождите еще:</b> {minutes} минут\n\n"
                    f"❗ <i>Нарушение интервала приведет к удалению сообщения!</i>",
                    parse_mode="HTML"
                )
            
            # Удаляем предупреждение через 10 секунд
            await asyncio.sleep(10)
            await delete_message(message.chat.id, warning_msg.message_id)
            return

# Обработчик новых участников
@dp.chat_member()
async def handle_chat_member_update(update: ChatMemberUpdated):
    if update.chat.id != CHAT_ID:
        return
    
    # Приветственное сообщение для новых участников
    if update.old_chat_member.status == ChatMemberStatus.LEFT and update.new_chat_member.status == ChatMemberStatus.MEMBER:
        user_mention = await get_user_mention(update.new_chat_member.user.id)
        welcome_msg = await update.chat.send_message(
            f"👋 <b>Добро пожаловать, {user_mention}!</b>\n\n"
            f"📜 Обязательно ознакомьтесь с правилами: /rules\n"
            f"🛍️ Как сделать заказ: /order\n\n"
            f"💎 Приятного общения!",
            parse_mode="HTML"
        )
        
        # Удаляем приветствие через 1 минуту
        await asyncio.sleep(60)
        await delete_message(update.chat.id, welcome_msg.message_id)

# Запуск бота
async def main():
    logger.info("Запуск бота...")
    
    # Запускаем планировщик правил
    asyncio.create_task(rules_scheduler())
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())