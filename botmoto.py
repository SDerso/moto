# =========================
# BOTMOTO T-BANK FIXED
# Оплата вручную на карту Тинькофф
# Полная админка, резерв, очередь, уведомления, автоснятие
# =========================

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
BOT_TOKEN = os.getenv("API_TOKEN")
CHAT_ID = -100411379361
ADMIN_IDS = [411379361]
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
CREATE TABLE IF NOT EXISTS settings(
    id INTEGER PRIMARY KEY,
    price_per_day INTEGER
)
""")
cursor.execute("INSERT OR IGNORE INTO settings(id, price_per_day) VALUES(1,?)",(PRICE_PER_DAY,))
conn.commit()

# ================= FSM =================
class Order(StatesGroup):
    choosing_days = State()
    choosing_date = State()
    writing_post = State()

# ================= KEYBOARDS =================
def main_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("📌 Купить закреп"))
    kb.add(KeyboardButton("🧾 История"))
    return kb

def days_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("1 день"))
    kb.add(KeyboardButton("3 дня"))
    kb.add(KeyboardButton("7 дней"))
    return kb

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
    # формируем inline_keyboard по 2 кнопки в ряд
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    row = []
    for idx, btn in enumerate(buttons, start=1):
        row.append(btn)
        if idx % 2 == 0:
            kb.inline_keyboard.append(row)
            row = []
    if row:
        kb.inline_keyboard.append(row)
    return kb

def admin_confirmation_keyboard(purchase_id):
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("✅ Подтвердить", callback_data=f"confirm_{purchase_id}"),
        InlineKeyboardButton("❌ Отменить", callback_data=f"cancel_{purchase_id}")
    )
    return kb

# ================= LOGIC =================
def get_price():
    cursor.execute("SELECT price_per_day FROM settings WHERE id=1")
    return cursor.fetchone()[0]

def is_slot_free(start_date, days):
    end_date = start_date + timedelta(days=days)
    cursor.execute("SELECT start_time,end_time,status FROM purchases WHERE status IN ('waiting_admin','active')")
    for db_start_str, db_end_str, _ in cursor.fetchall():
        db_start = datetime.fromisoformat(db_start_str)
        db_end = datetime.fromisoformat(db_end_str)
        if not (end_date <= db_start or start_date >= db_end):
            return False
    return True

def add_purchase_reserve(telegram_id, post_text, start_time, end_time):
    cursor.execute("""
        INSERT INTO purchases(telegram_id, post_text, start_time, end_time, status)
        VALUES(?,?,?,?,?)
    """,(telegram_id, post_text, start_time.isoformat(), end_time.isoformat(), "waiting_admin"))
    conn.commit()
    return cursor.lastrowid

async def activate_purchase(purchase_id):
    cursor.execute("SELECT post_text,start_time,end_time FROM purchases WHERE id=?",(purchase_id,))
    row = cursor.fetchone()
    if not row:
        return
    post_text, start_time_str, end_time_str = row
    start_time = datetime.fromisoformat(start_time_str)
    end_time = datetime.fromisoformat(end_time_str)
    if datetime.now() >= start_time:
        msg = await bot.send_message(CHAT_ID, post_text)
        await bot.pin_chat_message(CHAT_ID, msg.message_id)
        cursor.execute("UPDATE purchases SET status='active', message_id=? WHERE id=?",(msg.message_id,purchase_id))
        conn.commit()

async def scheduler():
    while True:
        now = datetime.now()
        # снять завершившиеся закрепы
        cursor.execute("SELECT id,telegram_id,end_time,message_id FROM purchases WHERE status='active'")
        for purchase_id, tg_id, end_time_str, message_id in cursor.fetchall():
            end_time = datetime.fromisoformat(end_time_str)
            if now >= end_time:
                try:
                    await bot.unpin_chat_message(CHAT_ID, message_id)
                    await bot.send_message(tg_id,"⏰ Ваш закреп закончился")
                except:
                    pass
                cursor.execute("UPDATE purchases SET status='finished' WHERE id=?",(purchase_id,))
                conn.commit()
        # активировать резервы, если дата наступила
        cursor.execute("SELECT id FROM purchases WHERE status='waiting_admin'")
        for purchase_id, in cursor.fetchall():
            await activate_purchase(purchase_id)
        await asyncio.sleep(60)

# ================= HANDLERS =================
@dp.message(F.text == "/start")
async def start(message: types.Message):
    cursor.execute("INSERT OR IGNORE INTO users VALUES(?,?)",(message.from_user.id,message.from_user.username))
    conn.commit()
    await message.answer("🏍 Добро пожаловать в систему закрепов!", reply_markup=main_menu())

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
    await message.answer(f"💳 Резерв создан.\n\nОплатите на карту Тинькофф и укажите в примечании:\n'Закреп в ТГ {start_date.date()}'")
    for admin_id in ADMIN_IDS:
        await bot.send_message(admin_id,
            f"📌 Новый заказ резерв:\nКлиент: @{message.from_user.username}\nДата: {start_date.date()}\nТекст:\n{post_text}",
            reply_markup=admin_confirmation_keyboard(purchase_id)
        )
    await state.clear()

# ================= ADMIN CALLBACK =================
@dp.callback_query(F.data.startswith("confirm_"))
async def confirm_payment(callback: types.CallbackQuery):
    purchase_id = int(callback.data.split("_")[1])
    await activate_purchase(purchase_id)
    cursor.execute("SELECT telegram_id FROM purchases WHERE id=?",(purchase_id,))
    tg_id = cursor.fetchone()[0]
    await bot.send_message(tg_id,"✅ Ваша оплата подтверждена. Закреп активирован.")
    await callback.message.edit_text("✅ Оплата подтверждена. Закреп активирован")
    await callback.answer()

@dp.callback_query(F.data.startswith("cancel_"))
async def cancel_payment(callback: types.CallbackQuery):
    purchase_id = int(callback.data.split("_")[1])
    cursor.execute("SELECT telegram_id FROM purchases WHERE id=?",(purchase_id,))
    tg_id = cursor.fetchone()[0]
    cursor.execute("UPDATE purchases SET status='cancelled' WHERE id=?",(purchase_id,))
    conn.commit()
    await bot.send_message(tg_id,"❌ Оплата не подтверждена. Резерв снят.")
    await callback.message.edit_text("❌ Оплата не подтверждена. Резерв снят.")
    await callback.answer()

# ================= USER HISTORY =================
@dp.message(F.text == "🧾 История")
async def history(message: types.Message):
    cursor.execute("SELECT start_time,end_time,status FROM purchases WHERE telegram_id=?",(message.from_user.id,))
    rows = cursor.fetchall()
    if not rows:
        await message.answer("История пуста")
        return
    text = "🧾 Ваша история:\n\n"
    for r in rows:
        text += f"{r[0][:10]} - {r[1][:10]} | {r[2]}\n"
    await message.answer(text)

# ================= ADMIN PRICE =================
@dp.message(F.text.startswith("/setprice"))
async def set_price(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        price = int(message.text.split()[1])
        cursor.execute("UPDATE settings SET price_per_day=? WHERE id=1",(price,))
        conn.commit()
        await message.answer(f"💰 Новая цена за день: {price}")
    except:
        await message.answer("❌ Использование: /setprice 700")

# ================= START =================
async def main():
    asyncio.create_task(scheduler())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

