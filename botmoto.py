# botmoto.py
import asyncio, aiohttp, uuid
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.filters.state import State, StatesGroup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, select

# ===================== CONFIG =====================
BOT_TOKEN = "ВАШ_ТОКЕН"
CHAT_ID = -1001234567890
ADMIN_IDS = [123456789,987654321]

PALLY_SHOP_ID = "ВАШ_SHОП_ID"
PALLY_SECRET_KEY = "ВАШ_СЕКРЕТ"
PALLY_POLLING_INTERVAL = 30  # секунд

# ===================== DATABASE =====================
engine = create_async_engine("sqlite+aiosqlite:///botmoto.db", echo=False)
Session = async_sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True)
    username = Column(String)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Tariff(Base):
    __tablename__ = "tariffs"
    id = Column(Integer, primary_key=True)
    days = Column(Integer)
    price = Column(Integer)
    is_active = Column(Boolean, default=True)

class Purchase(Base):
    __tablename__ = "purchases"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    days = Column(Integer)
    price = Column(Integer)
    status = Column(String, default="pending") # pending/paid/active/completed
    payment_id = Column(String)
    post_text = Column(String, nullable=True)
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    message_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Queue(Base):
    __tablename__ = "queue"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    purchase_id = Column(Integer, ForeignKey("purchases.id"))
    status = Column(String, default="waiting") # waiting/active/completed
    created_at = Column(DateTime, default=datetime.utcnow)

# ===================== BOT & FSM =====================
bot = Bot(BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
scheduler = AsyncIOScheduler()

class OrderStates(StatesGroup):
    choosing_tariff = State()
    choosing_date = State()
    writing_post = State()
    waiting_payment = State()

# ===================== KEYBOARDS =====================
def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("📌 Купить закреп")],
            [KeyboardButton("🧾 История")],
            [KeyboardButton("🗓 Выбор даты")]
        ], resize_keyboard=True
    )

def admin_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("👥 Пользователи"), KeyboardButton("💰 Тарифы")],
            [KeyboardButton("📊 Статистика")]
        ], resize_keyboard=True
    )

def date_keyboard(start_date: datetime, days=14):
    kb = InlineKeyboardMarkup(row_width=3)
    for i in range(days):
        d = start_date + timedelta(days=i)
        kb.add(InlineKeyboardButton(d.strftime("%d-%m-%Y"), callback_data=f"date_{d.date()}"))
    return kb

# ===================== UTILS =====================
async def create_payment(order_id: str, amount: int):
    # Симуляция Pally API, заменить на реальный endpoint
    return {"payment_url": f"https://pally.example/pay/{order_id}", "payment_id": order_id}

async def poll_payments():
    while True:
        async with Session() as session:
            pending = await session.execute(select(Purchase).where(Purchase.status=="pending"))
            for p in pending.scalars():
                # Симулируем оплату через API
                # В реальности проверка Pally
                p.status = "paid"
                await session.commit()
                await activate_queue()
        await asyncio.sleep(PALLY_POLLING_INTERVAL)

async def activate_queue():
    async with Session() as session:
        queue_item = await session.execute(select(Queue).where(Queue.status=="waiting").order_by(Queue.created_at))
        queue_item = queue_item.scalars().first()
        if not queue_item:
            return
        purchase = await session.get(Purchase, queue_item.purchase_id)
        if purchase.status != "active":
            msg = await bot.send_message(CHAT_ID, purchase.post_text)
            await bot.pin_chat_message(CHAT_ID, msg.message_id)
            purchase.message_id = msg.message_id
            purchase.status = "active"
            queue_item.status = "active"
            await session.commit()
            schedule_unpin(purchase)

def schedule_unpin(purchase):
    scheduler.add_job(
        unpin_purchase,
        "date",
        run_date=purchase.end_time,
        args=[purchase.id]
    )

async def unpin_purchase(purchase_id:int):
    async with Session() as session:
        purchase = await session.get(Purchase, purchase_id)
        if purchase and purchase.status=="active":
            try:
                await bot.unpin_chat_message(CHAT_ID, purchase.message_id)
            except:
                pass
            purchase.status="completed"
            await session.commit()
            await activate_queue()

# ===================== HANDLERS =====================
@dp.message()
async def start(message: types.Message, state: FSMContext):
    async with Session() as session:
        user = await session.execute(select(User).where(User.telegram_id==message.from_user.id))
        if not user.scalars().first():
            session.add(User(telegram_id=message.from_user.id, username=message.from_user.username))
            await session.commit()
    await message.answer("Добро пожаловать!", reply_markup=main_menu())

@dp.message(lambda m: m.text=="📌 Купить закреп")
async def buy(message: types.Message, state: FSMContext):
    async with Session() as session:
        tariffs = await session.execute(select(Tariff).where(Tariff.is_active))
        tariffs = tariffs.scalars().all()
        text = "Выберите тариф:\n"
        for t in tariffs:
            text+=f"{t.id}. {t.days} дней — {t.price}₽\n"
        await message.answer(text)
    await state.set_state(OrderStates.choosing_tariff)

@dp.message(OrderStates.choosing_tariff)
async def tariff_chosen(message: types.Message, state: FSMContext):
    await state.update_data(tariff_id=int(message.text))
    kb = date_keyboard(datetime.now(), days=14)
    await message.answer("Выберите дату для закрепа:", reply_markup=kb)
    await state.set_state(OrderStates.choosing_date)

@dp.callback_query(lambda c: c.data.startswith("date_"), state=OrderStates.choosing_date)
async def date_chosen(call: types.CallbackQuery, state: FSMContext):
    date_str = call.data.split("_")[1]
    date_obj = datetime.fromisoformat(date_str)
    await state.update_data(start_date=date_obj)
    await call.message.answer("Отправьте текст поста для закрепа:")
    await state.set_state(OrderStates.writing_post)

@dp.message(OrderStates.writing_post)
async def post_written(message: types.Message, state: FSMContext):
    data = await state.get_data()
    tariff_id = data["tariff_id"]
    start_date = data["start_date"]
    post_text = message.text
    async with Session() as session:
        tariff = await session.get(Tariff, tariff_id)
        purchase = Purchase(user_id=message.from_user.id, days=tariff.days, price=tariff.price,
                            post_text=post_text, start_time=start_date, end_time=start_date+timedelta(days=tariff.days))
        await session.merge(purchase)
        await session.commit()
        order_id=str(uuid.uuid4())
        payment = await create_payment(order_id, tariff.price)
        purchase.payment_id=payment["payment_id"]
        await session.commit()
    await message.answer(f"Оплата: {payment['payment_url']}")
    await state.set_state(OrderStates.waiting_payment)

# ===================== MAIN =====================
async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    scheduler.start()
    asyncio.create_task(poll_payments())
    await dp.start_polling(bot)

if __name__=="__main__":
    asyncio.run(main())