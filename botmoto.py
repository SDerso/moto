import asyncio
import sqlite3
from datetime import datetime, timedelta
import os
import contextlib
from aiogram.filters import CommandStart
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.filters.state import State, StatesGroup

# ================= CONFIG =================
BOT_TOKEN = os.getenv("API_TOKEN")  # вставьте сюда токен
CHAT_ID = -1002078737043  # чат для закрепов
ADMIN_IDS = [411379361]  # ID админа
PRICE_PER_DAY = 100

# ================= INIT BOT =================
bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ================= DATABASE CONTEXT MANAGER =================
@contextlib.contextmanager
def db_cursor():
    """
    Контекстный менеджер для работы с базой данных.
    Автоматически открывает и закрывает соединение.
    """
    conn = sqlite3.connect("botmoto.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        yield cursor
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

# ================= INIT DATABASE =================
def init_database():
    """Создает таблицы и обновляет структуру БД при запуске"""
    with db_cursor() as cursor:
        # ====== CREATE TABLES ======
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users(
            telegram_id INTEGER PRIMARY KEY,
            username TEXT
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS purchases(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            post_text TEXT,
            media TEXT,
            media_type TEXT,
            start_time TEXT,
            end_time TEXT,
            status TEXT,
            message_id INTEGER,
            notified INTEGER DEFAULT 0
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings(
            id INTEGER PRIMARY KEY,
            price_per_day INTEGER
        )
        """)

        cursor.execute(
            "INSERT OR IGNORE INTO settings(id, price_per_day) VALUES(1,?)",
            (PRICE_PER_DAY,)
        )

        # ====== SAFE STRUCTURE UPDATE ======
        cursor.execute("PRAGMA table_info(purchases)")
        columns = [col[1] for col in cursor.fetchall()]

        if "media" not in columns:
            cursor.execute("ALTER TABLE purchases ADD COLUMN media TEXT")

        if "media_type" not in columns:
            cursor.execute("ALTER TABLE purchases ADD COLUMN media_type TEXT")

    print("✅ База данных инициализирована")

# Вызываем инициализацию при запуске
init_database()

# ================= FSM =================
class Order(StatesGroup):
    choosing_days = State()
    choosing_date = State()
    writing_post = State()

class AdminStates(StatesGroup):
    waiting_new_price = State()
    waiting_broadcast = State()

# ================= KEYBOARDS =================
def user_payment_keyboard(purchase_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Я оплатил",
                callback_data=f"user_paid_{purchase_id}"
            ),
            InlineKeyboardButton(
                text="❌ Отказаться",
                callback_data=f"user_cancel_{purchase_id}"
            )
        ]
    ])

def admin_menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏳ Ожидают подтверждения", callback_data="admin_waiting")],
        [InlineKeyboardButton(text="🟢 Активные", callback_data="admin_active")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="❌ Отменённые", callback_data="admin_cancelled")],
        [InlineKeyboardButton(text="👥 Все пользователи", callback_data="admin_users")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="💰 Изменить цену", callback_data="admin_price")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")]
    ])

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📌 Купить закреп")],
            [KeyboardButton(text="🧾 История")]
        ],
        resize_keyboard=True
    )

def days_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="1 день")],
            [KeyboardButton(text="3 дня")],
            [KeyboardButton(text="7 дней")]
        ],
        resize_keyboard=True
    )

def date_keyboard():
    today = datetime.now()
    buttons = []
    for i in range(14):
        d = today + timedelta(days=i)
        status = "🟢" if is_slot_free(d, 1) else "🔴"
        buttons.append(InlineKeyboardButton(
            text=f"{status} {d.strftime('%d-%m')}",
            callback_data=f"date_{d.date()}"
        ))

    rows = []
    row = []
    for idx, btn in enumerate(buttons, start=1):
        row.append(btn)
        if idx % 2 == 0:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)

def admin_confirmation_keyboard(purchase_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_{purchase_id}"),
            InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_{purchase_id}")
        ]
    ])

# ================= LOGIC =================
def get_price() -> int:
    with db_cursor() as cursor:
        cursor.execute("SELECT price_per_day FROM settings WHERE id=1")
        result = cursor.fetchone()
        return result[0] if result else PRICE_PER_DAY

def get_total_income():
    with db_cursor() as cursor:
        cursor.execute("SELECT start_time, end_time FROM purchases WHERE status IN ('active','finished')")
        rows = cursor.fetchall()
    
    total = 0
    price = get_price()
    for row in rows:
        start = datetime.fromisoformat(row[0])
        end = datetime.fromisoformat(row[1])
        total += (end - start).days * price
    return total

def get_month_stats():
    now = datetime.now()
    month_start = datetime(now.year, now.month, 1)
    
    with db_cursor() as cursor:
        cursor.execute("SELECT start_time, end_time FROM purchases WHERE status IN ('active','finished')")
        rows = cursor.fetchall()
    
    total_income = 0
    total_sales = 0
    price = get_price()
    for row in rows:
        start = datetime.fromisoformat(row[0])
        if start >= month_start:
            end = datetime.fromisoformat(row[1])
            total_income += (end - start).days * price
            total_sales += 1
    return total_sales, total_income

def is_slot_free(start_date: datetime, days: int) -> bool:
    end_date = start_date + timedelta(days=days)
    
    with db_cursor() as cursor:
        cursor.execute("SELECT start_time, end_time FROM purchases WHERE status IN ('waiting_admin','active')")
        rows = cursor.fetchall()
    
    for row in rows:
        db_start = datetime.fromisoformat(row[0])
        db_end = datetime.fromisoformat(row[1])
        if not (end_date <= db_start or start_date >= db_end):
            return False
    return True

def add_purchase_reserve(telegram_id: int, post_text: str, start_time: datetime, end_time: datetime) -> int:
    with db_cursor() as cursor:
        cursor.execute("""
            INSERT INTO purchases(telegram_id, post_text, start_time, end_time, status)
            VALUES(?,?,?,?,?)
        """, (telegram_id, post_text, start_time.isoformat(), end_time.isoformat(), "waiting_payment"))
        return cursor.lastrowid

async def scheduler():
    while True:
        now = datetime.now()
        
        with db_cursor() as cursor:
            cursor.execute("""
                SELECT id, telegram_id, end_time, message_id
                FROM purchases
                WHERE status='active'
            """)
            active_purchases = cursor.fetchall()

        for purchase in active_purchases:
            purchase_id, tg_id, end_time_str, message_id = purchase
            end_time = datetime.fromisoformat(end_time_str)

            if now >= end_time:
                try:
                    await bot.unpin_chat_message(CHAT_ID, message_id)
                    await bot.send_message(tg_id, "⏰ Ваш закреп завершён.")
                except:
                    pass

                with db_cursor() as cursor:
                    cursor.execute("""
                        UPDATE purchases
                        SET status='finished'
                        WHERE id=?
                    """, (purchase_id,))

        await asyncio.sleep(20)

# ================= HANDLERS =================
@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):
    with db_cursor() as cursor:
        cursor.execute(
            "INSERT OR IGNORE INTO users(telegram_id, username) VALUES(?,?)",
            (message.from_user.id, message.from_user.username)
        )

    await message.answer(
        "🏍 Приветствуем в системе закрепов «Мото-Любители»!\n\n"
        "📌 Через этого бота вы можете закрепить свой пост в нашем чате (https://t.me/moto_kinechma) на выбранное время.\n"
        "💡 Важно: обязательно укажите в посте свой @username, чтобы другие могли связаться с вами.\n"
        "💳 Выберите срок закрепа, оплатите через кнопку и ждите подтверждения админа.\n\n"
        "🛠 Используйте кнопки ниже для покупки закрепа или просмотра вашей истории.",
        reply_markup=main_menu()
    )
    await state.clear()

@dp.message(F.text == "📌 Купить закреп")
async def buy(message: types.Message, state: FSMContext):
    await message.answer(f"💰 Цена за 1 день: {get_price()} руб\nВыберите срок:", reply_markup=days_keyboard())
    await state.set_state(Order.choosing_days)

@dp.message(Order.choosing_days)
async def choose_days(message: types.Message, state: FSMContext):
    try:
        days = int(message.text.split()[0])
    except:
        await message.answer("❌ Выберите корректный вариант")
        return
    await state.update_data(days=days)
    await message.answer("📅 Выберите дату начала:", reply_markup=date_keyboard())
    await state.set_state(Order.choosing_date)

@dp.callback_query(F.data.startswith("date_"))
async def choose_date(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()

    if "days" not in data:
        await callback.answer("❌ Сначала выберите количество дней", show_alert=True)
        return

    days = data["days"]
    date_str = callback.data.split("_")[1]
    start_date = datetime.fromisoformat(date_str)

    if not is_slot_free(start_date, days):
        await callback.answer("❌ Эти даты заняты", show_alert=True)
        return

    await state.update_data(start_date=start_date.isoformat())
    await callback.message.answer("✍ Отправьте текст поста для закрепа (можно с фото)")
    await state.set_state(Order.writing_post)
    await callback.answer()

@dp.message(Order.writing_post)
async def receive_post(message: types.Message, state: FSMContext):
    data = await state.get_data()
    
    if "days" not in data or "start_date" not in data:
        await message.answer("❌ Ошибка: начните процесс заново")
        await state.clear()
        return

    days = data["days"]
    start_date = datetime.fromisoformat(data["start_date"])
    end_date = start_date + timedelta(days=days)

    post_text = message.caption if message.caption else message.text

    media_type = "text"
    media_ids = None

    if message.photo:
        media_type = "photo"
        media_ids = message.photo[-1].file_id

    purchase_id = add_purchase_reserve(
        message.from_user.id,
        post_text,
        start_date,
        end_date
    )

    if media_ids:
        with db_cursor() as cursor:
            cursor.execute("""
                UPDATE purchases
                SET media=?, media_type=?
                WHERE id=?
            """, (media_ids, media_type, purchase_id))

    await message.answer(
        f"💳 Резерв создан!\n\n"
        f"📅 Срок закрепа: {days} {'день' if days == 1 else 'дня' if days < 5 else 'дней'}\n"
        f"💰 Сумма к оплате: {days * get_price()} руб\n\n"
        f"📌 Инструкция по оплате:\n"
        f"1️⃣ Переведите сумму на карту Т-Банк: 5536914058801691\n"
        f"2️⃣ В комментарии к платежу укажите дату закрепа и ваш @username\n"
        f"3️⃣ После оплаты нажмите кнопку «✅ Я оплатил» ниже, чтобы уведомить администратора\n\n"
        f"⏳ После подтверждения админом ваш пост будет закреплен на выбранное время.",
        reply_markup=user_payment_keyboard(purchase_id)
    )

    await state.clear()

@dp.callback_query(F.data.startswith("user_paid_"))
async def user_paid(callback: types.CallbackQuery):
    purchase_id = int(callback.data.split("_")[2])

    with db_cursor() as cursor:
        cursor.execute("SELECT telegram_id, post_text, start_time, end_time, status FROM purchases WHERE id=?", (purchase_id,))
        row = cursor.fetchone()
    
    if not row:
        await callback.answer("❌ Заказ не найден", show_alert=True)
        return
    
    user_id, post_text, start_str, end_str, status = row
    
    if status != "waiting_payment":
        await callback.answer("❌ Этот заказ уже обработан", show_alert=True)
        return
    
    start_date = datetime.fromisoformat(start_str)
    end_date = datetime.fromisoformat(end_str)
    days = (end_date - start_date).days

    with db_cursor() as cursor:
        cursor.execute("SELECT username FROM users WHERE telegram_id=?", (user_id,))
        user_row = cursor.fetchone()
    
    username = user_row[0] if user_row and user_row[0] else "Без username"

    with db_cursor() as cursor:
        cursor.execute("UPDATE purchases SET status='waiting_admin' WHERE id=?", (purchase_id,))

    admin_text = (
        f"💳 Пользователь сообщил об оплате!\n\n"
        f"👤 Ник: @{username}\n"
        f"🆔 ID: {user_id}\n"
        f"📅 {start_date.date()} - {end_date.date()} ({days} дней)\n"
        f"💰 Сумма: {days * get_price()} руб\n"
        f"📦 Пост:\n{post_text}"
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_text, reply_markup=admin_confirmation_keyboard(purchase_id))
        except:
            pass

    await callback.message.edit_text("✅ Ожидаем подтверждение администратора.")
    await callback.answer()

@dp.callback_query(F.data.startswith("user_cancel_"))
async def user_cancel(callback: types.CallbackQuery):
    purchase_id = int(callback.data.split("_")[2])
    
    with db_cursor() as cursor:
        cursor.execute("UPDATE purchases SET status='cancelled' WHERE id=?", (purchase_id,))
    
    await callback.message.edit_text("❌ Вы отказались от закрепа.")
    await callback.answer()

# ================= ADMIN HANDLERS =================
@dp.message(F.text == "/admin")
async def admin_panel(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer("🔧 Админ-панель Мото-Любители", reply_markup=admin_menu_keyboard())

@dp.callback_query(F.data == "admin_menu")
async def admin_menu_return(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.message.edit_text("🔧 Админ-панель", reply_markup=admin_menu_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "admin_waiting")
async def admin_waiting(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    with db_cursor() as cursor:
        cursor.execute("SELECT id, telegram_id, start_time, end_time FROM purchases WHERE status='waiting_admin'")
        rows = cursor.fetchall()
    
    if not rows:
        text = "⏳ Нет ожидающих подтверждения."
    else:
        text = "⏳ Ожидают подтверждения:\n\n"
        for r in rows:
            start = datetime.fromisoformat(r[2]).strftime('%d.%m')
            end = datetime.fromisoformat(r[3]).strftime('%d.%m')
            text += f"ID {r[0]} | Пользователь {r[1]} | {start}-{end}\n"
    
    try:
        await callback.message.edit_text(text, reply_markup=admin_menu_keyboard())
    except Exception as e:
        if "message is not modified" in str(e):
            await callback.answer("✅ Данные актуальны", show_alert=False)
        else:
            await callback.answer(f"❌ Ошибка: {str(e)[:50]}", show_alert=True)
    
    await callback.answer()

@dp.callback_query(F.data == "admin_active")
async def admin_active(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    with db_cursor() as cursor:
        cursor.execute("SELECT id, telegram_id, end_time FROM purchases WHERE status='active'")
        rows = cursor.fetchall()
    
    if not rows:
        text = "🟢 Нет активных закрепов."
        await callback.message.edit_text(text, reply_markup=admin_menu_keyboard())
        await callback.answer()
        return

    keyboard = []
    text = "🟢 Активные:\n\n"
    
    for r in rows:
        purchase_id = r[0]
        end_date = datetime.fromisoformat(r[2]).strftime('%d.%m %H:%M')
        text += f"ID {purchase_id} | Пользователь {r[1]} | до {end_date}\n"
        keyboard.append([
            InlineKeyboardButton(
                text=f"❌ Снять ID {purchase_id}",
                callback_data=f"admin_unpin_{purchase_id}"
            )
        ])

    keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")])
    
    try:
        await callback.message.edit_text(
            text, 
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
        )
    except Exception as e:
        if "message is not modified" not in str(e):
            await callback.answer(f"❌ Ошибка: {str(e)[:50]}", show_alert=True)
    
    await callback.answer()

@dp.callback_query(F.data.startswith("admin_unpin_"))
async def admin_unpin(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    purchase_id = int(callback.data.split("_")[2])

    with db_cursor() as cursor:
        cursor.execute("SELECT message_id, telegram_id, status FROM purchases WHERE id=?", (purchase_id,))
        row = cursor.fetchone()
    
    if not row:
        await callback.answer("❌ Заказ не найден", show_alert=True)
        return

    message_id, user_id, status = row
    
    if status not in ("active", "waiting_admin"):
        await callback.answer("❌ Этот закреп уже завершён или отменён", show_alert=True)
        return

    try:
        await bot.unpin_chat_message(chat_id=CHAT_ID, message_id=message_id)
    except Exception as e:
        await callback.answer(f"❌ Не удалось снять закреп: {e}", show_alert=True)
        return

    with db_cursor() as cursor:
        cursor.execute("UPDATE purchases SET status='cancelled' WHERE id=?", (purchase_id,))

    try:
        await bot.send_message(user_id, "❌ Ваш закреп был снят администратором.")
    except:
        pass

    await callback.message.edit_text(f"❌ Закреп ID {purchase_id} снят")
    await callback.answer()

@dp.callback_query(F.data == "admin_cancelled")
async def admin_cancelled(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    with db_cursor() as cursor:
        cursor.execute("SELECT id, telegram_id FROM purchases WHERE status='cancelled'")
        rows = cursor.fetchall()
    
    if not rows:
        text = "❌ Нет отменённых."
    else:
        text = "❌ Отменённые:\n\n"
        for r in rows:
            text += f"ID {r[0]} | Пользователь {r[1]}\n"
    
    try:
        await callback.message.edit_text(text, reply_markup=admin_menu_keyboard())
    except Exception as e:
        if "message is not modified" not in str(e):
            await callback.answer(f"❌ Ошибка: {str(e)[:50]}", show_alert=True)
    
    await callback.answer()

@dp.callback_query(F.data == "admin_users")
async def admin_users(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    with db_cursor() as cursor:
        cursor.execute("SELECT telegram_id, username FROM users")
        users = cursor.fetchall()

    if not users:
        text = "👥 Пользователей нет."
    else:
        text = "👥 Все пользователи:\n\n"
        for user in users:
            text += f"ID: {user[0]} | @{user[1] if user[1] else 'нет username'}\n"

    try:
        await callback.message.edit_text(text, reply_markup=admin_menu_keyboard())
    except Exception as e:
        if "message is not modified" not in str(e):
            await callback.answer(f"❌ Ошибка: {str(e)[:50]}", show_alert=True)
    
    await callback.answer()

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return

    await callback.message.edit_text("✍ Введите текст для рассылки:")
    await state.set_state(AdminStates.waiting_broadcast)
    await callback.answer()

@dp.message(AdminStates.waiting_broadcast)
async def process_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    text = message.text
    
    with db_cursor() as cursor:
        cursor.execute("SELECT telegram_id FROM users")
        users = cursor.fetchall()

    sent = 0
    failed = 0

    for user in users:
        try:
            await bot.send_message(user[0], text)
            sent += 1
            await asyncio.sleep(0.05)
        except:
            failed += 1

    await message.answer(
        f"📢 Рассылка завершена\n\n"
        f"✅ Отправлено: {sent}\n"
        f"❌ Ошибок: {failed}"
    )
    await state.clear()

@dp.callback_query(F.data == "admin_price")
async def admin_change_price(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    current_price = get_price()
    await callback.message.edit_text(
        f"💰 Текущая цена: {current_price} руб\n"
        f"Введите новую цену за день:"
    )
    await state.set_state(AdminStates.waiting_new_price)
    await callback.answer()

@dp.message(AdminStates.waiting_new_price)
async def process_new_price(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    try:
        new_price = int(message.text)
        if new_price <= 0:
            await message.answer("❌ Цена должна быть положительным числом")
            return
            
        with db_cursor() as cursor:
            cursor.execute("UPDATE settings SET price_per_day=? WHERE id=1", (new_price,))
        
        await message.answer(f"✅ Новая цена установлена: {new_price} руб")
        await state.clear()
    except ValueError:
        await message.answer("❌ Введите число")

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    total_income = get_total_income()
    month_sales, month_income = get_month_stats()
    
    text = (
        "📊 Статистика\n\n"
        f"💰 Общий доход: {total_income} руб\n\n"
        f"📅 За текущий месяц:\n"
        f"Продаж: {month_sales}\n"
        f"Доход: {month_income} руб"
    )
    
    try:
        await callback.message.edit_text(text, reply_markup=admin_menu_keyboard())
    except Exception as e:
        if "message is not modified" not in str(e):
            await callback.answer(f"❌ Ошибка: {str(e)[:50]}", show_alert=True)
    
    await callback.answer()

@dp.callback_query(F.data.startswith("confirm_"))
async def confirm_payment(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    purchase_id = int(callback.data.split("_")[1])

    with db_cursor() as cursor:
        cursor.execute("""
            SELECT telegram_id, post_text, media, media_type, start_time, end_time, status
            FROM purchases
            WHERE id=?
        """, (purchase_id,))
        row = cursor.fetchone()

    if not row:
        await callback.answer("❌ Заказ не найден", show_alert=True)
        return

    user_id, post_text, media_str, media_type, reserved_start, reserved_end, status = row

    if status != "waiting_admin":
        await callback.answer("❌ Этот заказ уже обработан", show_alert=True)
        return

    reserved_start_dt = datetime.fromisoformat(reserved_start)
    reserved_end_dt = datetime.fromisoformat(reserved_end)
    days = (reserved_end_dt - reserved_start_dt).days

    real_start = datetime.now()
    real_end = real_start + timedelta(days=days)

    try:
        # Проверяем доступ к чату
        try:
            chat = await bot.get_chat(CHAT_ID)
        except Exception as e:
            error_msg = (
                f"❌ Бот не имеет доступа к чату!\n\n"
                f"📌 Чат ID: {CHAT_ID}\n"
                f"🔧 Добавьте бота в чат и сделайте администратором\n"
                f"❌ Ошибка: {e}"
            )
            await callback.message.edit_text(error_msg)
            await callback.answer()
            return

        # Публикуем пост
        if media_type == "text" or not media_str:
            msg = await bot.send_message(CHAT_ID, post_text)
        elif media_type == "photo":
            msg = await bot.send_photo(CHAT_ID, media_str, caption=post_text)
        else:
            msg = await bot.send_message(CHAT_ID, post_text)

        # Пытаемся закрепить
        try:
            await bot.pin_chat_message(CHAT_ID, msg.message_id)
        except Exception as pin_error:
            # Если не можем закрепить, но пост отправили
            await bot.send_message(
                user_id,
                f"✅ Оплата подтверждена!\n"
                f"Пост опубликован, но не закреплен (нет прав на закреп).\n"
                f"Администратор закрепит его вручную."
            )
            await callback.message.edit_text(
                f"✅ Пост опубликован, но НЕ ЗАКРЕПЛЕН!\n"
                f"❌ Ошибка закрепа: {pin_error}\n\n"
                f"🔧 Дайте боту право 'Закреплять сообщения'"
            )
            # Всё равно обновляем статус
            with db_cursor() as cursor:
                cursor.execute("""
                    UPDATE purchases
                    SET status='active',
                        start_time=?,
                        end_time=?,
                        message_id=?
                    WHERE id=?
                """, (real_start.isoformat(), real_end.isoformat(), msg.message_id, purchase_id))
            await callback.answer()
            return

        # Если всё хорошо
        with db_cursor() as cursor:
            cursor.execute("""
                UPDATE purchases
                SET status='active',
                    start_time=?,
                    end_time=?,
                    message_id=?
                WHERE id=?
            """, (real_start.isoformat(), real_end.isoformat(), msg.message_id, purchase_id))

        await bot.send_message(
            user_id,
            f"✅ Оплата подтверждена!\n"
            f"Закреп активирован на {days} дн. ({days*24} часов)"
        )

        await callback.message.edit_text("✅ Оплата подтверждена. Закреп активирован.")
        
    except Exception as e:
        error_text = f"❌ Ошибка при публикации: {e}\n\n"
        if "chat not found" in str(e).lower():
            error_text += "🔧 Бот не в чате! Добавьте бота в чат."
        elif "not enough rights" in str(e).lower():
            error_text += "🔧 У бота нет прав! Сделайте бота администратором."
        
        await callback.message.edit_text(error_text)

    await callback.answer()

@dp.callback_query(F.data.startswith("cancel_"))
async def cancel_payment(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    purchase_id = int(callback.data.split("_")[1])
    
    with db_cursor() as cursor:
        cursor.execute("SELECT telegram_id FROM purchases WHERE id=?", (purchase_id,))
        row = cursor.fetchone()
    
    if row:
        user_id = row[0]
        with db_cursor() as cursor:
            cursor.execute("UPDATE purchases SET status='cancelled' WHERE id=?", (purchase_id,))
        
        try:
            await bot.send_message(user_id, "❌ Оплата не подтверждена. Резерв снят.")
        except:
            pass
        
        await callback.message.edit_text("❌ Оплата не подтверждена. Резерв снят.")
    else:
        await callback.message.edit_text("❌ Заказ не найден.")
    
    await callback.answer()

@dp.message(F.text == "🧾 История")
async def history(message: types.Message):
    with db_cursor() as cursor:
        cursor.execute("SELECT start_time, end_time, status FROM purchases WHERE telegram_id=?", (message.from_user.id,))
        rows = cursor.fetchall()
    
    if not rows:
        await message.answer("📭 История пуста")
        return
    
    text = "🧾 Ваша история:\n\n"
    for r in rows:
        start = datetime.fromisoformat(r[0]).strftime('%d.%m.%Y')
        end = datetime.fromisoformat(r[1]).strftime('%d.%m.%Y')
        status_map = {
            'waiting_payment': '⏳ Ожидает оплаты',
            'waiting_admin': '👀 Ожидает админа',
            'active': '✅ Активен',
            'finished': '✅ Завершён',
            'cancelled': '❌ Отменён'
        }
        status_text = status_map.get(r[2], r[2])
        text += f"{start} - {end} | {status_text}\n"
    
    await message.answer(text)

# ================= START =================
async def main():
    print("🤖 Бот запускается...")
    print(f"👑 Админы: {ADMIN_IDS}")
    print(f"📢 Чат для закрепов: {CHAT_ID}")
    
    # Запускаем планировщик
    asyncio.create_task(scheduler())
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
