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

# Настройка логирования (упрощенная версия для Dockhost)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
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

# Путь к базе данных (используем текущую директорию на Dockhost)
DB_PATH = "bot_database.db"

# Инициализация базы данных
def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
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
        logger.info("База данных успешно инициализирована")
    except Exception as e:
        logger.error(f"Ошибка инициализации базы данных: {e}")

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
   • <i>Любая конкуренция или перепродажа нового товар</i>
   🔥 <i>Нарушение: мут на 7 дней</i>

💎 <b>Администрация оставляет за собой право наказывать за другие нарушения, не указанные в правилах!</b>
"""

ORDER_MESSAGE = """
🛍️ <b>Как сделать заказ?</b> 🛍️

Вы можете заказать качественные товары у владельца барахолки: 
👉 @darknesss43 (Вика) 👈
или администратора:
👉 @barsss_amnyam 👈

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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    expires_at = datetime.now() + timedelta(days=WARN_EXPIRE_DAYS)
    cursor.execute(
        "INSERT INTO warns (user_id, chat_id, reason, issued_by, expires_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, chat_id, reason, issued_by, expires_at),
    )
    conn.commit()
    conn.close()

def get_user_warns(user_id: int, chat_id: int) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM warns WHERE id = ?", (warn_id,))
    conn.commit()
    conn.close()

def clear_user_warns(user_id: int, chat_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM warns WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)
    )
    conn.commit()
    conn.close()

def add_mute(user_id: int, chat_id: int, reason: str, issued_by: int, duration: timedelta = None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    expires_at = datetime.now() + duration if duration else None
    cursor.execute(
        "INSERT INTO mutes (user_id, chat_id, reason, issued_by, expires_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, chat_id, reason, issued_by, expires_at),
    )
    conn.commit()
    conn.close()

def add_ban(user_id: int, chat_id: int, reason: str, issued_by: int, duration: timedelta = None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    expires_at = datetime.now() + duration if duration else None
    cursor.execute(
        "INSERT INTO bans (user_id, chat_id, reason, issued_by, expires_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, chat_id, reason, issued_by, expires_at),
    )
    conn.commit()
    conn.close()

def add_admin_warn(user_id: int, reason: str, issued_by: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO admin_warns (user_id, reason, issued_by) VALUES (?, ?, ?)",
        (user_id, reason, issued_by),
    )
    conn.commit()
    conn.close()

def get_admin_warns(user_id: int) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE admin_warns SET is_active = FALSE WHERE id = ?", (warn_id,))
    conn.commit()
    conn.close()

def remove_last_admin_warn(user_id: int):
    """Удаляет последнее предупреждение администратора"""
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE admin_warns SET is_active = FALSE WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def add_admin(user_id: int, added_by: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)",
        (user_id, added_by),
    )
    conn.commit()
    conn.close()

def remove_admin(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def is_admin(user_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM admins WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def get_all_admins() -> List[int]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM admins")
    admins = [row[0] for row in cursor.fetchall()]
    conn.close()
    return admins

def is_user_activated(user_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM user_activations WHERE user_id = ?",
        (user_id,),
    )
    result = cursor.fetchone()
    conn.close()
    return result is not None

def activate_user(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO user_activations (user_id) VALUES (?)",
        (user_id,),
    )
    conn.commit()
    conn.close()

def add_user_ad(user_id: int, message_text: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO user_ads (user_id, message_text) VALUES (?, ?)",
        (user_id, message_text),
    )
    conn.commit()
    conn.close()

def get_today_ads_count(user_id: int) -> int:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM user_ads WHERE user_id = ? AND DATE(sent_at) = DATE('now')",
        (user_id,),
    )
    count = cursor.fetchone()[0]
    conn.close()
    return count

def get_last_ad_time(user_id: int) -> Optional[datetime]:
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO donations (user_id, amount, currency, message, is_anonymous) VALUES (?, ?, ?, ?, ?)",
        (user_id, amount, currency, message, is_anonymous),
    )
    conn.commit()
    conn.close()

def get_total_donations() -> float:
    """Получает общую сумму донатов"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(amount) FROM donations WHERE amount IS NOT NULL")
    result = cursor.fetchone()[0]
    conn.close()
    return result or 0.0

def get_recent_donations(limit: int = 10) -> List[Dict[str, Any]]:
    """Получает последние донаты"""
    conn = sqlite3.connect(DB_PATH)
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
async def mute_user(chat_id: int, user_id: int, duration: timedelta = None, reason: str = None, issued_by: int = None, is_auto: bool = False) -> bool:
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
            issued_by_mention = await get_user_mention(issued_by) if issued_by else "Система"
            message_text = (
                f"🔇 <b>Мут пользователя</b>\n\n"
                f"👤 <b>Пользователь:</b> {user_mention}\n"
                f"👮‍♂️ <b>Выдал:</b> {issued_by_mention}\n"
                f"⏳ <b>Срок:</b> {duration_str}{reason_str}"
            )

        await bot.send_message(chat_id, message_text, parse_mode="HTML")
        add_mute(user_id, chat_id, reason, issued_by or chat_id, duration)
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

async def ban_user(chat_id: int, user_id: int, duration: timedelta = None, reason: str = None, issued_by: int = None) -> bool:
    try:
        until_date = datetime.now() + duration if duration else None
        await bot.ban_chat_member(chat_id, user_id, until_date=until_date)

        duration_str = await format_duration(duration)
        reason_str = f"\n📝 <b>Причина:</b> {reason}" if reason else ""
        user_mention = await get_user_mention(user_id)
        issued_by_mention = await get_user_mention(issued_by) if issued_by else "Система"

        await bot.send_message(
            chat_id,
            f"🚫 <b>Бан пользователя</b>\n\n"
            f"👤 <b>Пользователь:</b> {user_mention}\n"
            f"👮‍♂️ <b>Выдал:</b> {issued_by_mention}\n"
            f"⏳ <b>Срок:</b> {duration_str}{reason_str}",
            parse_mode="HTML",
        )

        add_ban(user_id, chat_id, reason, issued_by or chat_id, duration)
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

async def warn_user(chat_id: int, user_id: int, reason: str = None, issued_by: int = None) -> bool:
    try:
        add_warn(user_id, chat_id, reason, issued_by or chat_id)
        warns = get_user_warns(user_id, chat_id)

        reason_str = f"\n📝 <b>Причина:</b> {reason}" if reason else ""
        user_mention = await get_user_mention(user_id)
        issued_by_mention = await get_user_mention(issued_by) if issued_by else "Система"

        await bot.send_message(
            chat_id,
            f"⚠️ <b>Предупреждение выдано</b>\n\n"
            f"👤 <b>Пользователь:</b> {user_mention}\n"
            f"👮‍♂️ <b>Выдал:</b> {issued_by_mention}\n"
            f"🔢 <b>Всего предупреждений:</b> {len(warns)}{reason_str}\n"
            f"📅 <b>Действует:</b> {WARN_EXPIRE_DAYS} дней",
            parse_mode="HTML",
        )

        if len(warns) >= 3:
            await ban_user(chat_id, user_id, reason="3 предупреждения", issued_by=issued_by)
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
            f"🔢 <b>Всего предупреждений:</b> {len(warns)}{reason_str}\n\n"
        )

        if len(warns) >= 3:
            message_text += "🚫 <b>3 предупреждения - снятие прав администратора!</b>"
            remove_admin(user_id)
            clear_admin_warns(user_id)
            
            # Уведомляем в чат
            await bot.send_message(CHAT_ID, message_text, parse_mode="HTML")
        else:
            # Отправляем только админам
            admins = get_all_admins() + ADMIN_IDS
            for admin_id in admins:
                try:
                    await bot.send_message(admin_id, message_text, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"Не удалось отправить предупреждение админу {admin_id}: {e}")

        return True
    except Exception as e:
        logger.error(f"Ошибка выдачи варна админу: {e}")
        return False

# Обработчики команд
@dp.message(Command("start"))
async def cmd_start(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        await message.answer(
            "👋 <b>Привет! Я бот-модератор для чата.</b>\n\n"
            "🤖 <i>Мои функции доступны только в групповых чатах.</i>\n"
            "📋 Для просмотра команд используйте /help",
            parse_mode="HTML"
        )

@dp.message(Command("help"))
async def cmd_help(message: Message):
    help_text = """
🤖 <b>Команды бота-модератора</b>

👮‍♂️ <b>Для администраторов:</b>
• /warn [пользователь] [причина] - выдать предупреждение
• /unwarn [пользователь] - снять последнее предупреждение
• /mute [пользователь] [время] [причина] - замутить пользователя
• /unmute [пользователь] - размутить пользователя
• /ban [пользователь] [время] [причина] - забанить пользователя
• /unban [пользователь] - разбанить пользователя
• /warns [пользователь] - посмотреть предупреждения
• /clear_warns [пользователь] - очистить все предупреждения

👨‍💼 <b>Управление администраторами:</b>
• /add_admin [пользователь] - добавить администратора
• /remove_admin [пользователь] - удалить администратора
• /admin_warn [администратор] [причина] - предупреждение админу
• /admin_unwarn [администратор] - снять предупреждение админу
• /admin_warns [администратор] - предупреждения админа
• /admins - список администраторов

📊 <b>Информационные:</b>
• /rules - показать правила чата
• /order - как сделать заказ
• /donate - поддержать разработку
• /stats - статистика бота

⚙️ <b>Для владельца:</b>
• /broadcast [сообщение] - рассылка всем пользователям
• /db_backup - резервная копия базы данных

📝 <i>Большинство команд требуют ответа на сообщение пользователя.</i>
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
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="💳 Поддержать разработку", url=DONATE_LINK))
    
    await message.answer(
        DONATE_MESSAGE.format(donate_link=DONATE_LINK),
        parse_mode="HTML",
        reply_markup=keyboard.as_markup()
    )

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if not await is_admin_user(message.from_user.id):
        await message.answer("❌ Эта команда только для администраторов.")
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Статистика варнов
        cursor.execute("SELECT COUNT(*) FROM warns WHERE expires_at > ?", (datetime.now(),))
        active_warns = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM warns")
        total_warns = cursor.fetchone()[0]
        
        # Статистика мутов
        cursor.execute("SELECT COUNT(*) FROM mutes WHERE is_active = TRUE")
        active_mutes = cursor.fetchone()[0]
        
        # Статистика банов
        cursor.execute("SELECT COUNT(*) FROM bans WHERE is_active = TRUE")
        active_bans = cursor.fetchone()[0]
        
        # Статистика пользователей
        cursor.execute("SELECT COUNT(DISTINCT user_id) FROM user_activations")
        activated_users = cursor.fetchone()[0]
        
        # Статистика донатов
        total_donations = get_total_donations()
        
        conn.close()
        
        stats_text = (
            "📊 <b>Статистика бота</b>\n\n"
            f"⚠️ <b>Активные предупреждения:</b> {active_warns}\n"
            f"📋 <b>Всего предупреждений:</b> {total_warns}\n"
            f"🔇 <b>Активные муты:</b> {active_mutes}\n"
            f"🚫 <b>Активные баны:</b> {active_bans}\n"
            f"👥 <b>Активированные пользователи:</b> {activated_users}\n"
            f"💰 <b>Сумма донатов:</b> {total_donations:.2f} RUB\n\n"
            f"🔄 <b>Последнее обновление:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        
        await message.answer(stats_text, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        await message.answer("❌ Ошибка получения статистики.")

@dp.message(Command("warn"))
async def cmd_warn(message: Message, command: CommandObject):
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        await message.answer("❌ Эта команда только для администраторов чата.")
        return

    result = await resolve_user_reference(message, command.args)
    if not result:
        await message.answer("❌ Укажите пользователя (ответом на сообщение или @username/id).")
        return

    user_id, reason = result
    success = await warn_user(message.chat.id, user_id, reason, message.from_user.id)
    if success:
        await message.delete()
    else:
        await message.answer("❌ Не удалось выдать предупреждение.")

@dp.message(Command("unwarn"))
async def cmd_unwarn(message: Message, command: CommandObject):
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        await message.answer("❌ Эта команда только для администраторов чата.")
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите пользователя (ответом на сообщение или @username/id).")
        return

    warns = get_user_warns(user_id, message.chat.id)
    if not warns:
        await message.answer("❌ У пользователя нет активных предупреждений.")
        return

    remove_warn(warns[-1]["id"])
    user_mention = await get_user_mention(user_id)
    await message.answer(f"✅ Снято последнее предупреждение с {user_mention}")

@dp.message(Command("warns"))
async def cmd_warns(message: Message, command: CommandObject):
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        await message.answer("❌ Эта команда только для администраторов чата.")
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите пользователя (ответом на сообщение или @username/id).")
        return

    warns = get_user_warns(user_id, message.chat.id)
    user_mention = await get_user_mention(user_id)

    if not warns:
        await message.answer(f"✅ У {user_mention} нет активных предупреждений.")
        return

    warns_text = "\n".join(
        [
            f"{i+1}. {warn['reason']} ({warn['issued_at']})"
            for i, warn in enumerate(warns)
        ]
    )

    await message.answer(
        f"⚠️ <b>Предупреждения {user_mention}:</b>\n\n{warns_text}\n\n"
        f"📅 <b>Всего:</b> {len(warns)} из 3",
        parse_mode="HTML",
    )

@dp.message(Command("clear_warns"))
async def cmd_clear_warns(message: Message, command: CommandObject):
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        await message.answer("❌ Эта команда только для администраторов чата.")
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите пользователя (ответом на сообщение или @username/id).")
        return

    clear_user_warns(user_id, message.chat.id)
    user_mention = await get_user_mention(user_id)
    await message.answer(f"✅ Все предупреждения {user_mention} очищены.")

@dp.message(Command("mute"))
async def cmd_mute(message: Message, command: CommandObject):
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        await message.answer("❌ Эта команда только для администраторов чата.")
        return

    args = command.args.split() if command.args else []
    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите пользователя (ответом на сообщение или @username/id).")
        return

    # Парсим время и причину
    duration = None
    reason = None
    
    if args and len(args) > 1:
        # Если есть аргументы, пробуем распарсить время из первого аргумента
        time_arg = args[1] if message.reply_to_message else args[0]
        duration = parse_time(time_arg)
        if duration:
            # Остальное - причина
            reason_parts = args[2:] if message.reply_to_message else args[1:]
            if not reason_parts and len(args) > (2 if message.reply_to_message else 1):
                reason_parts = args[2:] if message.reply_to_message else args[1:]
            reason = " ".join(reason_parts) if reason_parts else "Не указана"
        else:
            # Если время не распарсилось, всё - причина
            reason_parts = args[1:] if message.reply_to_message else args
            reason = " ".join(reason_parts) if reason_parts else "Не указана"
    else:
        reason = "Не указана"

    # Если время не указано, используем стандартное
    if not duration:
        duration = MUTE_DURATION

    success = await mute_user(message.chat.id, user_id, duration, reason, message.from_user.id)
    if success:
        await message.delete()
    else:
        await message.answer("❌ Не удалось замутить пользователя.")

@dp.message(Command("unmute"))
async def cmd_unmute(message: Message, command: CommandObject):
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        await message.answer("❌ Эта команда только для администраторов чата.")
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите пользователя (ответом на сообщение или @username/id).")
        return

    success = await unmute_user(message.chat.id, user_id)
    if success:
        await message.answer("✅ Пользователь размучен.")
    else:
        await message.answer("❌ Не удалось размутить пользователя.")

@dp.message(Command("ban"))
async def cmd_ban(message: Message, command: CommandObject):
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        await message.answer("❌ Эта команда только для администраторов чата.")
        return

    args = command.args.split() if command.args else []
    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите пользователя (ответом на сообщение или @username/id).")
        return

    # Парсим время и причину
    duration = None
    reason = None
    
    if args and len(args) > 1:
        # Если есть аргументы, пробуем распарсить время из первого аргумента
        time_arg = args[1] if message.reply_to_message else args[0]
        duration = parse_time(time_arg)
        if duration:
            # Остальное - причина
            reason_parts = args[2:] if message.reply_to_message else args[1:]
            reason = " ".join(reason_parts) if reason_parts else "Не указана"
        else:
            # Если время не распарсилось, всё - причина
            reason_parts = args[1:] if message.reply_to_message else args
            reason = " ".join(reason_parts) if reason_parts else "Не указана"
    else:
        reason = "Не указана"

    success = await ban_user(message.chat.id, user_id, duration, reason, message.from_user.id)
    if success:
        await message.delete()
    else:
        await message.answer("❌ Не удалось забанить пользователя.")

@dp.message(Command("unban"))
async def cmd_unban(message: Message, command: CommandObject):
    if not await is_chat_admin(message.from_user.id, message.chat.id):
        await message.answer("❌ Эта команда только для администраторов чата.")
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите пользователя (ответом на сообщение или @username/id).")
        return

    success = await unban_user(message.chat.id, user_id)
    if success:
        await message.answer("✅ Пользователь разбанен.")
    else:
        await message.answer("❌ Не удалось разбанить пользователя.")

@dp.message(Command("add_admin"))
async def cmd_add_admin(message: Message, command: CommandObject):
    if not await is_owner(message.from_user.id):
        await message.answer("❌ Эта команда только для владельца бота.")
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите пользователя (ответом на сообщение или @username/id).")
        return

    add_admin(user_id, message.from_user.id)
    user_mention = await get_user_mention(user_id)
    await message.answer(f"✅ {user_mention} добавлен в администраторы бота.")

@dp.message(Command("remove_admin"))
async def cmd_remove_admin(message: Message, command: CommandObject):
    if not await is_owner(message.from_user.id):
        await message.answer("❌ Эта команда только для владельца бота.")
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите пользователя (ответом на сообщение или @username/id).")
        return

    remove_admin(user_id)
    user_mention = await get_user_mention(user_id)
    await message.answer(f"✅ {user_mention} удален из администраторов бота.")

@dp.message(Command("admins"))
async def cmd_admins(message: Message):
    if not await is_admin_user(message.from_user.id):
        await message.answer("❌ Эта команда только для администраторов.")
        return

    admins = get_all_admins()
    owner_mentions = [await get_user_mention(admin_id) for admin_id in ADMIN_IDS]
    admin_mentions = [await get_user_mention(admin_id) for admin_id in admins]

    text = "👑 <b>Владельцы бота:</b>\n" + "\n".join(owner_mentions) if owner_mentions else ""
    text += "\n\n👨‍💼 <b>Администраторы бота:</b>\n" + "\n".join(admin_mentions) if admin_mentions else "Нет администраторов"

    await message.answer(text, parse_mode="HTML")

@dp.message(Command("admin_warn"))
async def cmd_admin_warn(message: Message, command: CommandObject):
    if not await is_owner(message.from_user.id):
        await message.answer("❌ Эта команда только для владельца бота.")
        return

    result = await resolve_user_reference(message, command.args)
    if not result:
        await message.answer("❌ Укажите администратора (ответом на сообщение или @username/id).")
        return

    user_id, reason = result
    
    if not is_admin(user_id) and user_id not in ADMIN_IDS:
        await message.answer("❌ Указанный пользователь не является администратором.")
        return

    success = await warn_admin(user_id, reason, message.from_user.id)
    if success:
        await message.answer("✅ Предупреждение выдано администратору.")
    else:
        await message.answer("❌ Не удалось выдать предупреждение.")

@dp.message(Command("admin_unwarn"))
async def cmd_admin_unwarn(message: Message, command: CommandObject):
    if not await is_owner(message.from_user.id):
        await message.answer("❌ Эта команда только для владельца бота.")
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите администратора (ответом на сообщение или @username/id).")
        return

    if not is_admin(user_id) and user_id not in ADMIN_IDS:
        await message.answer("❌ Указанный пользователь не является администратором.")
        return

    warn_id = remove_last_admin_warn(user_id)
    if warn_id:
        user_mention = await get_user_mention(user_id)
        await message.answer(f"✅ Снято последнее предупреждение с {user_mention}")
    else:
        await message.answer("❌ У администратора нет активных предупреждений.")

@dp.message(Command("admin_warns"))
async def cmd_admin_warns(message: Message, command: CommandObject):
    if not await is_admin_user(message.from_user.id):
        await message.answer("❌ Эта команда только для администраторов.")
        return

    user_id = await resolve_user_only(message, command.args)
    if not user_id:
        await message.answer("❌ Укажите администратора (ответом на сообщение или @username/id).")
        return

    if not is_admin(user_id) and user_id not in ADMIN_IDS:
        await message.answer("❌ Указанный пользователь не является администратором.")
        return

    warns = get_admin_warns(user_id)
    user_mention = await get_user_mention(user_id)

    if not warns:
        await message.answer(f"✅ У {user_mention} нет активных предупреждений.")
        return

    warns_text = "\n".join(
        [
            f"{i+1}. {warn['reason']} (от {await get_user_mention(warn['issued_by'])}, {warn['issued_at']})"
            for i, warn in enumerate(warns)
        ]
    )

    await message.answer(
        f"⚠️ <b>Предупреждения администратора {user_mention}:</b>\n\n{warns_text}\n\n"
        f"📅 <b>Всего:</b> {len(warns)} из 3",
        parse_mode="HTML",
    )

@dp.message(Command("broadcast"))
async def cmd_broadcast(message: Message, command: CommandObject):
    if not await is_owner(message.from_user.id):
        await message.answer("❌ Эта команда только для владельца бота.")
        return

    if not command.args:
        await message.answer("❌ Укажите сообщение для рассылки.")
        return

    # Получаем всех активированных пользователей
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT user_id FROM user_activations")
    users = [row[0] for row in cursor.fetchall()]
    conn.close()

    success_count = 0
    fail_count = 0

    for user_id in users:
        try:
            await bot.send_message(user_id, f"📢 <b>Рассылка от администратора:</b>\n\n{command.args}", parse_mode="HTML")
            success_count += 1
            await asyncio.sleep(0.1)  # Задержка чтобы не спамить
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение пользователю {user_id}: {e}")
            fail_count += 1

    await message.answer(
        f"✅ Рассылка завершена:\n"
        f"✓ Успешно: {success_count}\n"
        f"✗ Не удалось: {fail_count}"
    )

@dp.message(Command("db_backup"))
async def cmd_db_backup(message: Message):
    if not await is_owner(message.from_user.id):
        await message.answer("❌ Эта команда только для владельца бота.")
        return

    try:
        # Просто отправляем файл базы данных
        with open(DB_PATH, 'rb') as db_file:
            await message.answer_document(db_file, caption="📦 Резервная копия базы данных")
    except Exception as e:
        logger.error(f"Ошибка создания бэкапа: {e}")
        await message.answer("❌ Ошибка создания резервной копии.")

# Обработчики сообщений
@dp.message(F.chat.type == ChatType.SUPERGROUP)
async def handle_group_message(message: Message):
    try:
        user_id = message.from_user.id
        chat_id = message.chat.id
        
        # Проверяем, активирован ли пользователь
        if not is_user_activated(user_id):
            activate_user(user_id)
            # Приветственное сообщение для новых пользователей
            welcome_text = (
                f"👋 Добро пожаловать, {await get_user_mention(user_id)}!\n\n"
                f"📖 Обязательно ознакомьтесь с правилами чата: /rules\n"
                f"🛍️ Как сделать заказ: /order\n\n"
                f"💬 Приятного общения!"
            )
            await message.answer(welcome_text, parse_mode="HTML")
        
        # Проверка на спам объявлениями
        if is_ad_message(message.text or message.caption or ""):
            today_ads = get_today_ads_count(user_id)
            last_ad_time = get_last_ad_time(user_id)
            
            # Проверяем лимит в день
            if today_ads >= MAX_ADS_PER_DAY:
                violations_count = get_today_violations_count(user_id) + 1
                add_ad_violation(user_id)
                
                # Увеличиваем срок мута с каждым нарушением
                mute_days = min(7, violations_count)  # Максимум 7 дней
                duration = timedelta(days=mute_days)
                
                await mute_user(
                    chat_id, user_id, duration, 
                    f"Превышение лимита объявлений ({today_ads}/{MAX_ADS_PER_DAY} за день)",
                    is_auto=True
                )
                await delete_message(chat_id, message.message_id)
                return
            
            # Проверяем интервал между объявлениями
            if last_ad_time:
                time_since_last_ad = datetime.now() - last_ad_time
                if time_since_last_ad < MIN_AD_INTERVAL:
                    violations_count = get_today_violations_count(user_id) + 1
                    add_ad_violation(user_id)
                    
                    # Увеличиваем срок мута с каждым нарушением
                    mute_days = min(7, violations_count)
                    duration = timedelta(days=mute_days)
                    
                    await mute_user(
                        chat_id, user_id, duration, 
                        f"Слишком частое размещение объявлений ({time_since_last_ad.seconds//60} мин.)",
                        is_auto=True
                    )
                    await delete_message(chat_id, message.message_id)
                    return
            
            # Если все проверки пройдены, добавляем объявление в историю
            add_user_ad(user_id, message.text or message.caption or "")
        
        # Проверка на триггерные слова
        text = (message.text or message.caption or "").lower()
        for trigger, variants in TRIGGER_WORDS.items():
            for variant in variants:
                if variant in text:
                    # Цензурируем сообщение
                    censored_text = censor_trigger_word(message.text or message.caption or "", trigger)
                    
                    # Удаляем оригинальное сообщение
                    await delete_message(chat_id, message.message_id)
                    
                    # Отправляем цензурированную версию
                    warning_text = (
                        f"⚠️ <b>Внимание!</b> Использование запрещенных слов не приветствуется.\n\n"
                        f"👤 <b>Пользователь:</b> {await get_user_mention(user_id)}\n"
                        f"📝 <b>Сообщение:</b> {censored_text}"
                    )
                    
                    await message.answer(warning_text, parse_mode="HTML")
                    return
        
        # Автоматическое удаление команд бота от обычных пользователей
        if message.text and message.text.startswith('/') and not await is_chat_admin(user_id, chat_id):
            command = message.text.split()[0].lower()
            if command in ['/warn', '/mute', '/ban', '/unwarn', '/unmute', '/unban', '/add_admin', '/remove_admin']:
                await delete_message(chat_id, message.message_id)
                
                # Мут за использование команд бота
                await mute_user(
                    chat_id, user_id, MUTE_DURATION, 
                    "Использование команд бота", 
                    is_auto=True
                )
                return
                
    except Exception as e:
        logger.error(f"Ошибка обработки сообщения: {e}")

@dp.message(F.new_chat_members)
async def handle_new_members(message: Message):
    try:
        for new_member in message.new_chat_members:
            user_id = new_member.id
            
            # Активируем пользователя
            activate_user(user_id)
            
            # Приветственное сообщение
            welcome_text = (
                f"👋 Добро пожаловать, {await get_user_mention(user_id)}!\n\n"
                f"📖 Обязательно ознакомьтесь с правилами чата: /rules\n"
                f"🛍️ Как сделать заказ: /order\n\n"
                f"💬 Приятного общения!"
            )
            
            await message.answer(welcome_text, parse_mode="HTML")
            
    except Exception as e:
        logger.error(f"Ошибка обработки новых участников: {e}")

@dp.message(F.left_chat_member)
async def handle_left_member(message: Message):
    try:
        # Просто удаляем сообщение о выходе
        await delete_message(message.chat.id, message.message_id)
    except Exception as e:
        logger.error(f"Ошибка обработки выхода участника: {e}")

# Обработчик донатов
@dp.message(F.text.contains('донат') | F.text.contains('donate'))
async def handle_donate_mention(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        keyboard = InlineKeyboardBuilder()
        keyboard.row(InlineKeyboardButton(text="💳 Поддержать разработку", url=DONATE_LINK))
        
        await message.answer(
            DONATE_MESSAGE.format(donate_link=DONATE_LINK),
            parse_mode="HTML",
            reply_markup=keyboard.as_markup()
        )

# Функция для запуска бота
async def main():
    logger.info("Запуск бота...")
    
    # Запускаем планировщик правил
    asyncio.create_task(rules_scheduler())
    
    # Удаляем вебхук (на всякий случай)
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Запускаем поллинг
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
