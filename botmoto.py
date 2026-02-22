# =========================
# BOTMOTO PRO VERSION
# SQLite + Queue + AutoRenew + Notifications + Admin + Pally
# Compatible with BotHost
# =========================

import asyncio
import sqlite3
import uuid
import aiohttp
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.filters.state import State, StatesGroup

# ================= CONFIG =================

BOT_TOKEN = "ВАШ_ТОКЕН"
CHAT_ID = -100XXXXXXXXXX
ADMIN_IDS = [123456789]

PALLY_API = "https://ВАШ_API_PALLY"
PALLY_SHOP_ID = "SHOP_ID"
PALLY_SECRET = "SECRET_KEY"

PRICE_PER_DAY = 500

# ==========================================

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
    auto_renew INTEGER DEFAULT 0,
    notified INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS queue(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_id INTEGER,
    created_at TEXT
)
""")

conn.commit()

# ================= FSM =================

class Order(StatesGroup):
    choosing_days = State()
    choosing_date = State()
    writing_post = State()
    choosing_autorenew = State()

# ================= KEYBOARDS =================

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📌 Купить закреп")],
            [KeyboardButton(text="🧾 История")],
            [KeyboardButton(text="⚙ Автопродление")]
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

def autorenew_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Да")],
            [KeyboardButton(text="Нет")]
        ],
        resize_keyboard=True
    )

def date_keyboard():
    kb = InlineKeyboardMarkup()
    today = datetime.now()
    for i in range(14):
        d = today + timedelta(days=i)
        status = "🟢"
        if not is_slot_free(d, 1):
            status = "🔴"
        kb.add(
            InlineKeyboardButton(
                text=f"{status} {d.strftime('%d-%m')}",
                callback_data=f"date_{d.date()}"
            )
        )
    return kb

# ================= LOGIC =================

def is_slot_free(start_date, days):
    end_date = start_date + timedelta(days=days)
    cursor.execute("""
    SELECT start_time, end_time 
    FROM purchases 
    WHERE status IN ('active','queued','waiting_payment')
""")
    rows = cursor.fetchall()
    for row in rows:
        db_start = datetime.fromisoformat(row[0])
        db_end = datetime.fromisoformat(row[1])
        if not (end_date <= db_start or start_date >= db_end):
            return False
    return True

def add_to_queue(purchase_id):
    cursor.execute(
        "INSERT INTO queue(purchase_id,created_at) VALUES(?,?)",
        (purchase_id, datetime.now().isoformat())
    )
    conn.commit()

async def activate_next():
    now = datetime.now()

    cursor.execute("""
        SELECT q.id, p.id, p.post_text, p.start_time, p.end_time
        FROM queue q
        JOIN purchases p ON q.purchase_id = p.id
        ORDER BY q.id ASC
    """)
    rows = cursor.fetchall()

    for row in rows:
        queue_id, purchase_id, post_text, start_time, end_time = row
        start_dt = datetime.fromisoformat(start_time)

        if now >= start_dt:
            msg = await bot.send_message(CHAT_ID, post_text)
            await bot.pin_chat_message(CHAT_ID, msg.message_id)

            cursor.execute("""
                UPDATE purchases
                SET status='active', message_id=?
                WHERE id=?
            """, (msg.message_id, purchase_id))

            cursor.execute("DELETE FROM queue WHERE id=?", (queue_id,))
            conn.commit()
            break

# ================= PAYMENT =================

async def create_payment(order_id, amount):
    async with aiohttp.ClientSession() as session:
        async with session.post(PALLY_API, json={
            "shop_id": PALLY_SHOP_ID,
            "order_id": order_id,
            "amount": amount,
            "secret": PALLY_SECRET
        }) as resp:
            return await resp.json()
            # ================= PAYMENT CHECKER =================

async def check_payment(order_id):
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{PALLY_API}/check", params={
            "shop_id": PALLY_SHOP_ID,
            "order_id": order_id,
            "secret": PALLY_SECRET
        }) as resp:
            data = await resp.json()
            return data.get("status") == "paid"


# ================= BACKGROUND TASK =================

async def scheduler():
    while True:
        now = datetime.now()

        # Уведомления за 12 часов
        cursor.execute("""
        SELECT id, telegram_id, end_time, notified 
        FROM purchases 
        WHERE status='active'
        """)
        rows = cursor.fetchall()

        for row in rows:
            purchase_id, tg_id, end_time, notified = row
            end_time_dt = datetime.fromisoformat(end_time)

            if not notified and end_time_dt - now <= timedelta(hours=12):
                try:
                    await bot.send_message(
                        tg_id,
                        "⏰ Ваш закреп заканчивается через 12 часов!"
                    )
                except:
                    pass
                cursor.execute(
                    "UPDATE purchases SET notified=1 WHERE id=?",
                    (purchase_id,)
                )
                conn.commit()

            # Если срок вышел
            if now >= end_time_dt:
                cursor.execute(
                    "SELECT message_id, auto_renew FROM purchases WHERE id=?",
                    (purchase_id,)
                )
                data = cursor.fetchone()
                message_id, auto_renew = data

                try:
                    await bot.unpin_chat_message(CHAT_ID, message_id)
                except:
                    pass

                if auto_renew:
                    new_end = end_time_dt + timedelta(days=1)
                    cursor.execute("""
                        UPDATE purchases
                        SET end_time=?, notified=0
                        WHERE id=?
                    """, (new_end.isoformat(), purchase_id))
                    conn.commit()
                else:
                    cursor.execute("""
                        UPDATE purchases
                        SET status='finished'
                        WHERE id=?
                    """, (purchase_id,))
                    conn.commit()
                    await activate_next()

        await asyncio.sleep(60)


# ================= COMMANDS =================

@dp.message(F.text == "/start")
async def start(message: types.Message):
    cursor.execute(
        "INSERT OR IGNORE INTO users VALUES(?,?)",
        (message.from_user.id, message.from_user.username)
    )
    conn.commit()

    await message.answer(
        "🏍 Добро пожаловать в систему закрепов!",
        reply_markup=main_menu()
    )


@dp.message(F.text == "📌 Купить закреп")
async def buy(message: types.Message, state: FSMContext):
    await message.answer(
        f"💰 Цена за 1 день: {PRICE_PER_DAY} руб\nВыберите срок:",
        reply_markup=days_keyboard()
    )
    await state.set_state(Order.choosing_days)


@dp.message(Order.choosing_days)
async def choose_days(message: types.Message, state: FSMContext):
    days = int(message.text.split()[0])
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
    await callback.message.answer("✍ Отправьте текст поста")
    await state.set_state(Order.writing_post)


@dp.message(Order.writing_post)
async def receive_post(message: types.Message, state: FSMContext):
    await state.update_data(post_text=message.text)
    await message.answer("🔄 Включить автопродление?", reply_markup=autorenew_keyboard())
    await state.set_state(Order.choosing_autorenew)


@dp.message(Order.choosing_autorenew)
async def autorenew_choice(message: types.Message, state: FSMContext):
    auto = 1 if message.text == "Да" else 0
    data = await state.get_data()

    days = data["days"]
    start_date = datetime.fromisoformat(data["start_date"])
    end_date = start_date + timedelta(days=days)

    order_id = str(uuid.uuid4())
    amount = PRICE_PER_DAY * days

    cursor.execute("""
        INSERT INTO purchases
        (telegram_id, post_text, start_time, end_time, status, auto_renew)
        VALUES(?,?,?,?,?,?)
    """, (
        message.from_user.id,
        data["post_text"],
        start_date.isoformat(),
        end_date.isoformat(),
        "waiting_payment",
        auto
    ))
    conn.commit()

    payment = await create_payment(order_id, amount)

    await message.answer(
        f"💳 Оплатите закреп: {payment.get('payment_url')}"
    )

    # Проверка оплаты
    for _ in range(60):
        paid = await check_payment(order_id)
        if paid:
            cursor.execute("""
                UPDATE purchases SET status='queued'
                WHERE telegram_id=? AND status='waiting_payment'
            """, (message.from_user.id,))
            conn.commit()

            cursor.execute("""
                SELECT id FROM purchases 
                WHERE telegram_id=? 
                ORDER BY id DESC LIMIT 1
            """, (message.from_user.id,))
            purchase_id = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM purchases WHERE status='active'")
active_count = cursor.fetchone()[0]

if active_count == 0:
    cursor.execute("""
        SELECT post_text, start_time, end_time 
        FROM purchases WHERE id=?
    """, (purchase_id,))
    p = cursor.fetchone()

    post_text, start_time, end_time = p
    start_dt = datetime.fromisoformat(start_time)

    if datetime.now() >= start_dt:
        msg = await bot.send_message(CHAT_ID, post_text)
        await bot.pin_chat_message(CHAT_ID, msg.message_id)

        cursor.execute("""
            UPDATE purchases
            SET status='active', message_id=?
            WHERE id=?
        """, (msg.message_id, purchase_id))
        conn.commit()

        await message.answer("✅ Оплата прошла. Закреп активирован.")
    else:
        add_to_queue(purchase_id)
        await message.answer("✅ Оплата прошла. Добавлено в очередь.")
else:
    add_to_queue(purchase_id)
    await message.answer("✅ Оплата прошла. Добавлено в очередь.")
            break

        await asyncio.sleep(10)

    await state.clear()


@dp.message(F.text == "🧾 История")
async def history(message: types.Message):
    cursor.execute("""
        SELECT start_time,end_time,status
        FROM purchases
        WHERE telegram_id=?
    """, (message.from_user.id,))
    rows = cursor.fetchall()

    if not rows:
        await message.answer("История пуста")
        return

    text = "🧾 Ваша история:\n\n"
    for r in rows:
        text += f"{r[0][:10]} - {r[1][:10]} | {r[2]}\n"

    await message.answer(text)


# ================= ADMIN =================

@dp.message(F.text.startswith("/admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer("⚙ Админ панель:\n/setprice 700")


@dp.message(F.text.startswith("/setprice"))
async def set_price(message: types.Message):
    global PRICE_PER_DAY
    if message.from_user.id not in ADMIN_IDS:
        return
    PRICE_PER_DAY = int(message.text.split()[1])
    await message.answer(f"💰 Новая цена: {PRICE_PER_DAY}")


# ================= START =================

async def main():
    asyncio.create_task(scheduler())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

