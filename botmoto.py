# botmoto_tbank_fixed_v3.py

import asyncio
import sqlite3
from datetime import datetime, timedelta
import os

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.filters.state import State, StatesGroup

# ================= CONFIG =================
BOT_TOKEN = os.getenv("API_TOKEN")  # вставьте сюда токен
CHAT_ID = -1003732723471  # чат для закрепов
ADMIN_IDS = [411379361]  # ID админа
PRICE_PER_DAY = 100

# ================= INIT BOT =================
bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ================= DATABASE =================
conn = sqlite3.connect("botmoto.db")
cursor = conn.cursor()

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
    start_time TEXT,
    end_time TEXT,
    status TEXT,
    message_id INTEGER,
    notified INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS attachments(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_id INTEGER,
    file_id TEXT,
    file_type TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS settings(
    id INTEGER PRIMARY KEY,
    price_per_day INTEGER
)
""")
cursor.execute("INSERT OR IGNORE INTO settings(id, price_per_day) VALUES(1,?)", (PRICE_PER_DAY,))
conn.commit()

# ================= FSM =================
class Order(StatesGroup):
    choosing_days = State()
    choosing_date = State()
    writing_post = State()

class AdminStates(StatesGroup):
    waiting_new_price = State()

# ================= KEYBOARDS =================
def user_payment_keyboard(purchase_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💳 Я оплатил", callback_data=f"user_paid_{purchase_id}"),
                InlineKeyboardButton(text="❌ Отказаться", callback_data=f"user_cancel_{purchase_id}")
            ]
        ]
    )

def admin_confirmation_keyboard(purchase_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_{purchase_id}"),
                InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_{purchase_id}")
            ]
        ]
    )

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

# ================= LOGIC =================
def get_price() -> int:
    cursor.execute("SELECT price_per_day FROM settings WHERE id=1")
    return cursor.fetchone()[0]

def is_slot_free(start_date: datetime, days: int) -> bool:
    end_date = start_date + timedelta(days=days)
    cursor.execute("SELECT start_time,end_time,status FROM purchases WHERE status IN ('waiting_admin','active')")
    for db_start_str, db_end_str, _ in cursor.fetchall():
        db_start = datetime.fromisoformat(db_start_str)
        db_end = datetime.fromisoformat(db_end_str)
        if not (end_date <= db_start or start_date >= db_end):
            return False
    return True

def add_purchase_reserve(telegram_id: int, post_text: str, start_time: datetime, end_time: datetime) -> int:
    cursor.execute("""
        INSERT INTO purchases(telegram_id, post_text, start_time, end_time, status)
        VALUES(?,?,?,?,?)
    """, (
        telegram_id,
        post_text,
        start_time.isoformat(),
        end_time.isoformat(),
        "waiting_payment"
    ))
    conn.commit()
    return cursor.lastrowid

async def activate_purchase(purchase_id: int):
    cursor.execute("SELECT post_text,start_time,end_time FROM purchases WHERE id=?", (purchase_id,))
    row = cursor.fetchone()
    if not row:
        return
    post_text, start_time_str, end_time_str = row
    start_time = datetime.fromisoformat(start_time_str)
    end_time = datetime.fromisoformat(end_time_str)
    if datetime.now() >= start_time:
        msg = await bot.send_message(CHAT_ID, post_text)
        await bot.pin_chat_message(CHAT_ID, msg.message_id)
        cursor.execute("UPDATE purchases SET status='active', message_id=? WHERE id=?", (msg.message_id, purchase_id))
        conn.commit()

async def scheduler():
    while True:
        now = datetime.now()
        # активировать ожидающие (если время пришло и админ подтвердил)
        cursor.execute("SELECT id, start_time FROM purchases WHERE status='waiting_admin'")
        for purchase_id, start_str in cursor.fetchall():
            start_date = datetime.fromisoformat(start_str)
            if now >= start_date:
                await activate_purchase(purchase_id)

        # завершить активные
        cursor.execute("SELECT id, telegram_id, end_time, message_id FROM purchases WHERE status='active'")
        for purchase_id, user_id, end_str, message_id in cursor.fetchall():
            end_date = datetime.fromisoformat(end_str)
            if now >= end_date:
                try:
                    await bot.unpin_chat_message(CHAT_ID, message_id)
                    await bot.send_message(user_id, "⏰ Ваш закреп завершён.")
                except:
                    pass
                cursor.execute("UPDATE purchases SET status='finished' WHERE id=?", (purchase_id,))
                conn.commit()

        await asyncio.sleep(30)

# ================= HANDLERS =================
@dp.message(F.text == "/start")
async def start(message: types.Message, state: FSMContext):
    cursor.execute("INSERT OR IGNORE INTO users VALUES(?,?)", (message.from_user.id, message.from_user.username))
    conn.commit()
    await message.answer("🏍 Добро пожаловать в систему закрепов!", reply_markup=main_menu())
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
    date_str = callback.data.split("_")[1]
    start_date = datetime.fromisoformat(date_str)
    data = await state.get_data()
    days = data["days"]
    if not is_slot_free(start_date, days):
        await callback.answer("❌ Эти даты заняты", show_alert=True)
        return
    await state.update_data(start_date=start_date.isoformat())
    await callback.message.answer("✍ Отправьте текст поста для закрепа")
    await state.set_state(Order.writing_post)
    await callback.answer()

@dp.message(Order.writing_post)
async def receive_post(message: types.Message, state: FSMContext):
    data = await state.get_data()
    days = data["days"]
    start_date = datetime.fromisoformat(data["start_date"])
    end_date = start_date + timedelta(days=days)
    post_text = message.text

    purchase_id = add_purchase_reserve(message.from_user.id, post_text, start_date, end_date)

    await message.answer(
        f"💳 Резерв создан!\nСумма: {days*get_price()} руб\nОплатите и нажмите кнопку ниже.",
        reply_markup=user_payment_keyboard(purchase_id)
    )
    await state.clear()

@dp.callback_query(F.data.startswith("user_paid_"))
async def user_paid(callback: types.CallbackQuery):
    purchase_id = int(callback.data.split("_")[2])

    cursor.execute("SELECT telegram_id, post_text, start_time, end_time FROM purchases WHERE id=?", (purchase_id,))
    row = cursor.fetchone()
    if not row:
        await callback.answer("❌ Заказ не найден", show_alert=True)
        return
    user_id, post_text, start_str, end_str = row
    start_date = datetime.fromisoformat(start_str)
    end_date = datetime.fromisoformat(end_str)
    days = (end_date - start_date).days

    cursor.execute("SELECT username FROM users WHERE telegram_id=?", (user_id,))
    user_row = cursor.fetchone()
    username = user_row[0] if user_row and user_row[0] else "Без username"

    cursor.execute("UPDATE purchases SET status='waiting_admin' WHERE id=?", (purchase_id,))
    conn.commit()

    admin_text = (
        f"💳 Оплата поступила!\n\n"
        f"👤 Ник: @{username}\n"
        f"🆔 ID: {user_id}\n"
        f"📅 {start_date.date()} - {end_date.date()} ({days} дней)\n"
        f"📦 Пост:\n{post_text}"
    )

    # отправка админам
    for admin_id in ADMIN_IDS:
        await bot.send_message(admin_id, admin_text, reply_markup=admin_confirmation_keyboard(purchase_id))

    await callback.message.edit_text("✅ Ожидаем подтверждение администратора.")
    await callback.answer()

# ================= ADMIN =================
@dp.callback_query(F.data.startswith("confirm_"))
async def confirm_payment(callback: types.CallbackQuery):
    purchase_id = int(callback.data.split("_")[1])
    await activate_purchase(purchase_id)
    cursor.execute("SELECT telegram_id FROM purchases WHERE id=?", (purchase_id,))
    user_id = cursor.fetchone()[0]
    await bot.send_message(user_id, "✅ Ваша оплата подтверждена. Закреп активирован.")
    await callback.message.edit_text("✅ Оплата подтверждена. Закреп активирован")
    await callback.answer()

@dp.callback_query(F.data.startswith("cancel_"))
async def cancel_payment(callback: types.CallbackQuery):
    purchase_id = int(callback.data.split("_")[1])
    cursor.execute("SELECT telegram_id FROM purchases WHERE id=?", (purchase_id,))
    user_id = cursor.fetchone()[0]
    cursor.execute("UPDATE purchases SET status='cancelled' WHERE id=?", (purchase_id,))
    conn.commit()
    await bot.send_message(user_id, "❌ Оплата не подтверждена. Резерв снят.")
    await callback.message.edit_text("❌ Оплата не подтверждена. Резерв снят.")
    await callback.answer()

@dp.callback_query(F.data.startswith("admin_unpin_"))
async def admin_unpin(callback: types.CallbackQuery):
    purchase_id = int(callback.data.split("_")[2])
    cursor.execute("SELECT message_id, telegram_id, status FROM purchases WHERE id=?", (purchase_id,))
    row = cursor.fetchone()
    if not row:
        await callback.answer("❌ Заказ не найден", show_alert=True)
        return
    message_id, user_id, status = row
    if status not in ("active", "waiting_admin"):
        await callback.answer("❌ Уже завершён или отменён", show_alert=True)
        return
    try:
        await bot.unpin_chat_message(CHAT_ID, message_id)
    except Exception as e:
        await callback.answer(f"❌ Не удалось снять: {e}", show_alert=True)
        return
    cursor.execute("UPDATE purchases SET status='cancelled' WHERE id=?", (purchase_id,))
    conn.commit()
    try:
        await bot.send_message(user_id, "❌ Ваш закреп снят админом.")
    except:
        pass
    await callback.message.edit_text(f"❌ Закреп ID {purchase_id} снят")
    await callback.answer()

# ================= START =================
async def main():
    asyncio.create_task(scheduler())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
