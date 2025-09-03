import os
import asyncio
import logging
import aiosqlite
import time
import random
from datetime import datetime, timezone
import uuid

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
import json

load_dotenv()

# ---------- CONFIG ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
WALLET = os.getenv("WALLET", "UQCWJ9SuF0HbpjVqPqfccE9watW4HzJ3uMRaoKyV23ERcco_")
DB_PATH = os.getenv("DB_PATH", "contest.db")

# ---------- LOGGING ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- BOT & DISPATCHER ----------
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN muhit o'zgaruvchisi o'rnatilmagan.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ---------- FSM States ----------
class NewContestStates(StatesGroup):
    waiting_name = State()
    waiting_prize_name = State()
    waiting_ticket_price = State()
    waiting_end_time = State()
    waiting_image = State()
    waiting_target_chat = State()
    confirm = State()

class BuyTicket(StatesGroup):
    waiting_for_count = State()
    waiting_for_payment = State()

# Vaqtinchalik ma'lumotlarni saqlash uchun global lug'at
pending_approvals = {}

# ---------- DB helpers ----------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS contests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            prize_name TEXT,
            ticket_price_nano INTEGER,
            end_ts INTEGER,
            post_chat_id INTEGER,
            post_message_id INTEGER,
            image_file_id TEXT,
            active INTEGER DEFAULT 1
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contest_id INTEGER,
            user_id INTEGER,
            username TEXT,
            qty INTEGER,
            created_ts INTEGER,
            tx_hash TEXT
        )
        """)
        await db.commit()
    logger.info("Database initialized.")

# ---------- UTILITY ----------
def now_ts():
    return int(time.time())

def human_time_left(end_ts):
    from datetime import timedelta
    sec = max(0, end_ts - now_ts())
    return str(timedelta(seconds=sec)).split('.')[0]

# ---------- HANDLERS ----------
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    args = message.text.split()
    contest_id = None
    if len(args) > 1 and args[1].isdigit():
        contest_id = int(args[1])
    
    if contest_id:
        await state.set_state(BuyTicket.waiting_for_count)
        await state.update_data(contest_id=contest_id)
        await message.answer("Nechta chipta sotib olmoqchisiz? (raqam kiriting)")
    else:
        kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Chipta sotib olish")]], resize_keyboard=True)
        await message.answer("Assalomu alaykum! chipta sotib oling:", reply_markup=kb)

@dp.message(F.text == "Chipta sotib olish")
async def show_contests_from_menu(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, name, prize_name, ticket_price_nano, end_ts FROM contests WHERE active=1 AND end_ts > ?", (now_ts(),))
        contests = await cur.fetchall()
    
    if not contests:
        await message.answer("Hozirda faol konkurslar mavjud emas.")
        return

    for c_id, name, prize, price_nano, end_ts in contests:
        text = (
            f"🎉 <b>Konkurs:</b> {name}\n"
            f"🏆 <b>Sovgʻa:</b> {prize}\n"
            f"🎟 <b>Chipta narxi:</b> {price_nano / 1e9} TON\n"
            f"⏳ <b>Tugashgacha:</b> {human_time_left(end_ts)}\n"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Chipta sotib olish", callback_data=f"buy_ticket_{c_id}")]
        ])
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("buy_ticket_"))
async def start_buy_ticket_process(callback: CallbackQuery, state: FSMContext):
    contest_id = int(callback.data.split('_')[-1])
    await state.set_state(BuyTicket.waiting_for_count)
    await state.update_data(contest_id=contest_id)
    await callback.message.answer("Nechta chipta sotib olmoqchisiz? (raqam kiriting)")
    await callback.answer()

@dp.message(BuyTicket.waiting_for_count)
async def get_ticket_count(message: Message, state: FSMContext):
    user_data = await state.get_data()
    contest_id = user_data.get("contest_id")
    
    if not contest_id:
        await message.reply("Konkurs ma'lumotlari topilmadi. Qayta urinib ko'ring.")
        await state.clear()
        return

    if not message.text.isdigit():
        await message.answer("Faqat raqam kiriting.")
        return
    
    count = int(message.text)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT ticket_price_nano FROM contests WHERE id=?", (contest_id,))
        row = await cur.fetchone()
        if not row:
            await message.reply("Konkurs topilmadi.")
            await state.clear()
            return
        
        price_per_ticket = row[0] / 1e9
        total_ton = round(price_per_ticket * count, 9)
        await state.update_data(ticket_count=count, total=total_ton)
        
    payment_info = (
        f"Chipta sotib olish: {count} ta\n"
        f"Jami summa: {total_ton} TON\n"
        f"Manzil: `{WALLET}`\n\n"
        "❗️Muhim❗️: iltimos, ko‘rsatilgan aniq summani yuboring.\n"
        "To‘lov yuborilgach, bot avtomatik ravishda chipta yozadi.\n"
        "Iltimos, to‘lov chekini rasm sifatida yuboring."
    )
    
    await message.answer(payment_info, parse_mode='Markdown')
    
    await state.set_state(BuyTicket.waiting_for_payment)

@dp.message(BuyTicket.waiting_for_payment, F.content_type == types.ContentType.PHOTO)
async def receive_payment_photo(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    count = user_data.get("ticket_count")
    total = user_data.get("total")
    user_id = message.from_user.id
    contest_id = user_data.get("contest_id")
    username = message.from_user.username
    
    # CALLBACK_DATA_INVALID xatosini tuzatish: Ma'lumotni saqlash uchun unikal ID yaratish
    tx_id = str(uuid.uuid4())
    pending_approvals[tx_id] = {
        "user_id": user_id,
        "contest_id": contest_id,
        "count": count,
        "username": username
    }
    
    await bot.send_photo(
        ADMIN_ID,
        photo=message.photo[-1].file_id,
        caption=(
            f"Foydalanuvchi @{username} to‘lov yubordi.\n"
            f"{count} ta chipta, {total} TON.\n"
            "Tasdiqlaysizmi?"
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                # Callback data'da endi faqat unikal ID bor
                InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"approve_{tx_id}"),
                InlineKeyboardButton(text="❌ Soxta to‘lov", callback_data=f"decline_{tx_id}")
            ]
        ])
    )
    await message.answer("To‘lovingiz qabul qilindi va admin tekshiryapti. Iltimos kuting.")
    await state.clear()

@dp.message(BuyTicket.waiting_for_payment)
async def handle_invalid_payment_type(message: Message):
    await message.answer("Iltimos, to‘lov chekini **rasm** shaklida yuboring.")


# ---------- CALLBACKS ----------
@dp.callback_query(F.data.startswith("approve_"))
async def approve_payment(callback: CallbackQuery):
    """
    Admin to'lovni tasdiqlaganida ishga tushadi.
    Foydalanuvchiga chiptalarni beradi va ma'lumotlar bazasiga yozadi.
    """
    tx_id = callback.data.split('_', 1)[1]
    data = pending_approvals.get(tx_id)

    if not data:
        await callback.answer("Bu to'lov eskirgan yoki xato yuz berdi.")
        return

    # Callback_data'dan to'g'ri ma'lumotlarni olish
    user_id = data.get("user_id")
    contest_id = data.get("contest_id")
    count = data.get("count")
    username = data.get("username")
    
    # Foydalanuvchi ma'lumotlarini ma'lumotlar bazasiga yozish
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT qty FROM tickets WHERE contest_id=? AND user_id=?", (contest_id, user_id))
        exist = await cur.fetchone()
        
        if exist:
            new_qty = exist[0] + count
            await db.execute("UPDATE tickets SET qty=?, created_ts=?, username=? WHERE contest_id=? AND user_id=?",
                             (new_qty, now_ts(), username, contest_id, user_id))
        else:
            await db.execute("INSERT INTO tickets (contest_id, user_id, username, qty, created_ts) VALUES (?, ?, ?, ?, ?)",
                             (contest_id, user_id, username, count, now_ts()))
        await db.commit()
    
    await bot.send_message(user_id, f"Muvaffaqiyat! Siz {count} ta chipta sotib oldingiz.")
    
    await callback.message.edit_caption(callback.message.caption + "\n✅ Tasdiqlangan")
    await callback.answer("Foydalanuvchi tasdiqlandi.")
    # Ish tugagach, ma'lumotni xotiradan o'chirish
    del pending_approvals[tx_id]

@dp.callback_query(F.data.startswith("decline_"))
async def decline_payment(callback: CallbackQuery):
    """
    Admin to'lovni rad etganida ishga tushadi.
    """
    tx_id = callback.data.split('_', 1)[1]
    data = pending_approvals.get(tx_id)

    if not data:
        await callback.answer("Bu to'lov eskirgan yoki xato yuz berdi.")
        return
        
    user_id = data.get("user_id")
    
    await bot.send_message(user_id, "To‘lov soxta yoki tasdiqlanmadi. Iltimos, to‘lovingizni qayta tekshirib ko‘ring.")
    
    await callback.message.edit_caption(callback.message.caption + "\n❌ Rad etilgan")
    await callback.answer("Foydalanuvchi rad etildi.")
    # Ish tugagach, ma'lumotni xotiradan o'chirish
    del pending_approvals[tx_id]


# ---------- ADMIN COMMANDS (unchanged) ----------
@dp.message(Command("new_contest"))
async def cmd_new_contest(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        await msg.reply("Faqat admin foydalanishi mumkin.")
        return
    await state.set_state(NewContestStates.waiting_name)
    await msg.reply("Konkurs nomini kiriting:")

@dp.message(NewContestStates.waiting_name)
async def process_name(msg: Message, state: FSMContext):
    await state.update_data(name=msg.text)
    await state.set_state(NewContestStates.waiting_prize_name)
    await msg.reply("Sovgʻa nomini kiriting:")

@dp.message(NewContestStates.waiting_prize_name)
async def process_prize(msg: Message, state: FSMContext):
    await state.update_data(prize_name=msg.text)
    await state.set_state(NewContestStates.waiting_ticket_price)
    await msg.reply("Chipta narxini TON ko‘rinishida kiriting (masalan 0.1):")

@dp.message(NewContestStates.waiting_ticket_price)
async def process_price(msg: Message, state: FSMContext):
    try:
        val = float(msg.text.replace(",", "."))
        price_nano = int(val * 1e9)
    except (ValueError, IndexError):
        await msg.reply("Xato format. Misol: 0.1")
        return
    await state.update_data(ticket_price_nano=price_nano)
    await state.set_state(NewContestStates.waiting_end_time)
    await msg.reply("Tugash vaqtini YYYY-MM-DD HH:MM formatida kiriting (UTC):")

@dp.message(NewContestStates.waiting_end_time)
async def process_end_time(msg: Message, state: FSMContext):
    try:
        dt = datetime.strptime(msg.text.strip(), "%Y-%m-%d %H:%M")
        end_ts = int(dt.replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        await msg.reply("Xato format. Misol: 2025-09-05 20:00")
        return
    await state.update_data(end_ts=end_ts)
    await state.set_state(NewContestStates.waiting_image)
    await msg.reply("Sovgʻa rasm yoki rasm linkini yuboring (yoki 'skip'):")

@dp.message(NewContestStates.waiting_image, F.photo)
async def process_image(msg: Message, state: FSMContext):
    file_id = msg.photo[-1].file_id
    await state.update_data(image_file_id=file_id)
    await state.set_state(NewContestStates.waiting_target_chat)
    await msg.reply("Konkurs postini qaysi chatga tashlamoqchisiz? chat id ni yuboring (masalan -1001234567890) yoki '@channelusername':")

@dp.message(NewContestStates.waiting_image)
async def process_image_text(msg: Message, state: FSMContext):
    text = msg.text.strip()
    if text.lower() == "skip":
        await state.update_data(image_file_id=None)
    else:
        await state.update_data(image_file_id=text)
    
    await state.set_state(NewContestStates.waiting_target_chat)
    await msg.reply("Rasm ma'lumoti saqlandi. Chat id yoki @username kiriting:")

@dp.message(NewContestStates.waiting_target_chat)
async def process_target_chat(msg: Message, state: FSMContext):
    target = msg.text.strip()
    await state.update_data(target_chat=target)
    data = await state.get_data()
    summary = (
        f"Nom: {data['name']}\n"
        f"Sovgʻa: {data['prize_name']}\n"
        f"Chipta narxi (nanoTON): {data['ticket_price_nano']}\n"
        f"Tugash (UTC): {datetime.utcfromtimestamp(data['end_ts']).strftime('%Y-%m-%d %H:%M')}\n"
        f"Chat: {target}\n"
        f"Rasm: {'bor' if data.get('image_file_id') else 'yoq'}"
    )
    await state.set_state(NewContestStates.confirm)
    await msg.reply("Quyidagicha konkurs yaratilsinmi?\n\n" + summary + "\n\nYes/No")

@dp.message(NewContestStates.confirm)
async def process_confirm(msg: Message, state: FSMContext):
    if msg.text.lower() not in ("yes", "y", "ha", "ha!", "ok"):
        await state.clear()
        await msg.reply("Bekor qilindi.")
        return
    data = await state.get_data()
    
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO contests (name, prize_name, ticket_price_nano, end_ts, image_file_id, post_chat_id, post_message_id, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (data["name"], data["prize_name"], data["ticket_price_nano"], data["end_ts"], data.get("image_file_id"), 0, 0, 1))
        contest_id = cur.lastrowid
        await db.commit()

    text = (
        f"🎉 <b>{data['name']}</b>\n\n"
        f"🏆 <b>Sovgʻa:</b> {data['prize_name']}\n"
        f"🎟 <b>Chipta narxi:</b> {data['ticket_price_nano'] / 1e9} TON\n"
        f"⏳ <b>Tugashgacha:</b> {human_time_left(data['end_ts'])}\n"
        f"👥 <b>Qatnashchilar:</b> 0\n"
    )
    
    me = await bot.get_me()
    bot_username = me.username
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎟 Chipta sotib olish", url=f"https://t.me/{bot_username}?start={contest_id}")],
    ])
    
    try:
        target = data["target_chat"]
        image_id = data.get("image_file_id")
        
        if image_id and image_id.startswith("http"):
            sent = await bot.send_photo(chat_id=target, photo=image_id, caption=text, parse_mode="HTML", reply_markup=keyboard)
        elif image_id:
            sent = await bot.send_photo(chat_id=target, photo=image_id, caption=text, parse_mode="HTML", reply_markup=keyboard)
        else:
            sent = await bot.send_message(chat_id=target, text=text, parse_mode="HTML", reply_markup=keyboard)

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE contests SET post_chat_id=?, post_message_id=? WHERE id=?", (sent.chat.id, sent.message_id, contest_id))
            await db.commit()

        await bot.send_message(ADMIN_ID, "Konkurs post qilindi!")
    except Exception as e:
        logger.exception("Post yuborilmadi:")
        await bot.send_message(ADMIN_ID, f"Post yuborishda xatolik: {e}")
    finally:
        await state.clear()

# ---------- BACKGROUND TASKS ----------
async def update_posts_task(bot_username: str):
    while True:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT id, name, prize_name, ticket_price_nano, end_ts, post_chat_id, post_message_id, image_file_id FROM contests WHERE active=1")
                contests = await cur.fetchall()
                for c in contests:
                    cid, name, prize, price_nano, end_ts, chat_id, msg_id, image_file_id = c
                    
                    if now_ts() >= end_ts:
                        await finalize_contest(cid)
                        continue

                    cur2 = await db.execute("SELECT SUM(qty) FROM tickets WHERE contest_id=?", (cid,))
                    sumrow = await cur2.fetchone()
                    participants = sumrow[0] or 0
                    time_left = human_time_left(end_ts)
                    text = (
                        f"🎉 <b>{name}</b>\n\n"
                        f"🏆 <b>Sovgʻa:</b> {prize}\n"
                        f"🎟 <b>Chipta narxi:</b> {price_nano / 1e9} TON\n"
                        f"⏳ <b>Tugashgacha:</b> {time_left}\n"
                        f"👥 <b>Qatnashchilar:</b> {participants}\n"
                    )
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🎟 Chipta sotib olish", url=f"https://t.me/{bot_username}?start={cid}")],
                    ])
                    
                    try:
                        if image_file_id:
                            await bot.edit_message_caption(chat_id=chat_id, message_id=msg_id, caption=text, parse_mode="HTML", reply_markup=keyboard)
                        else:
                            await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, parse_mode="HTML", reply_markup=keyboard)
                    except Exception as e:
                        logger.debug(f"edit failed for contest {cid}: {e}")
            await asyncio.sleep(60)
        except Exception:
            logger.exception("update_posts_task error")
            await asyncio.sleep(10)

async def finalize_contest(contest_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT post_chat_id, post_message_id, name, prize_name, image_file_id FROM contests WHERE id=? AND active=1", (contest_id,))
        row = await cur.fetchone()
        if not row: return
        post_chat_id, post_message_id, name, prize, image_file_id = row
        
        await db.execute("UPDATE contests SET active=0 WHERE id=?", (contest_id,))
        
        cur2 = await db.execute("SELECT user_id, username, qty FROM tickets WHERE contest_id=?", (contest_id,))
        rows = await cur2.fetchall()
        total = sum(r[2] for r in rows) if rows else 0
        
        text = f"✅ <b>{name}</b>\n\n<b>Konkurs tugadi!</b>\n"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        
        if total == 0:
            text += "Hech kim ishtirok etmadi."
        else:
            pool = [ (user_id, username) for user_id, username, qty in rows for _ in range(qty) ]
            winner_user_id, winner_username = random.choice(pool)
            winner_info = f"@{winner_username or winner_user_id}"
            
            text += f"🏆 <b>G‘olib:</b> {winner_info}\n"
            text += f"🎉 <b>Sovgʻa:</b> {prize}"
            
            try:
                await bot.send_message(winner_user_id, f"Siz g‘olib bo‘ldingiz! Tabriklar! 🎉\nSovgʻa: {prize}")
            except Exception:
                logger.debug("Could not notify winner")

        try:
            if image_file_id:
                await bot.edit_message_caption(chat_id=post_chat_id, message_id=post_message_id, caption=text, parse_mode="HTML", reply_markup=keyboard)
            else:
                await bot.edit_message_text(chat_id=post_chat_id, message_id=msg_id, text=text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            logger.debug(f"Could not edit message for contest {contest_id}")
        
        await db.commit()

# ---------- STARTUP ----------
async def main():
    await init_db()
    me = await bot.get_me()
    bot_username = me.username
    asyncio.create_task(update_posts_task(bot_username))
    logger.info("Bot started and background tasks launched.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot to'xtatildi.")
