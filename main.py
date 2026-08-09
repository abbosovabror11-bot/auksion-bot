# ==============================================================================
# PROJECT: TELEGRAM NFT & STARS AUCTION WITH SUM BALANCE SYSTEM
# ARCHITECTURE: Modular Monolith with Asynchronous Core & Web Server (aiogram 3.x)
# ==============================================================================

import asyncio
import logging
import sys
import sqlite3
import os
from datetime import datetime
from typing import Dict, List, Optional

from aiohttp import web
from aiogram import Bot, Dispatcher, F, types, Router, html
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BotCommand, BotCommandScopeDefault, LabeledPrice, PreCheckoutQuery
)
from aiogram.exceptions import TelegramBadRequest

# ==============================================================================
# 1. CONFIGURATION & LOGGING
# ==============================================================================

TOKEN = "8655535261:AAETrrG_B7Q_DxChzSuFhaWZ8jnmmggtW4c"
ADMIN_IDS = [8694110588]    # Sizning Telegram ID raqamingiz

CARD_NUMBER = "9860606756173831"
CARD_HOLDER = "ABBOSOV ABRORBEK"
SUM_PER_STAR = 180          # 1 Stars = 180 so'm

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s"
)
logger = logging.getLogger("NFTAuctionBot")

# ==============================================================================
# 2. DATABASE MANAGER (SQLITE ENGINE)
# ==============================================================================

class Database:
    def __init__(self, db_file: str = "nft_auction_bot.db"):
        self.db_file = db_file
        self.init_db()

    def get_connection(self):
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    bot_balance REAL DEFAULT 0.0,
                    joined_date TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS channels (
                    channel_id INTEGER PRIMARY KEY,
                    owner_id INTEGER,
                    channel_title TEXT,
                    username TEXT,
                    balance REAL DEFAULT 0.0,
                    total_auctions_count INTEGER DEFAULT 0,
                    total_stars_generated REAL DEFAULT 0.0
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS auctions (
                    auction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    creator_id INTEGER,
                    channel_id INTEGER,
                    message_id INTEGER,
                    prize_name TEXT,
                    nft_link TEXT,
                    current_price REAL,
                    current_leader_id INTEGER,
                    current_leader_name TEXT,
                    status TEXT DEFAULT 'active',
                    created_at TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS payments (
                    payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount_sum REAL,
                    amount_stars REAL,
                    photo_id TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT
                )
            """)
            conn.commit()
        logger.info("Ma'lumotlar bazasi ishga tushirildi.")

db = Database()

# ==============================================================================
# 3. FSM STATES (STATE MACHINE)
# ==============================================================================

class AuctionStates(StatesGroup):
    waiting_for_channel_id = State()
    waiting_for_prize_name = State()
    waiting_for_nft_link = State()
    waiting_for_start_price = State()

class TopUpStates(StatesGroup):
    waiting_for_sum = State()
    waiting_for_receipt = State()

class AdminStates(StatesGroup):
    waiting_for_broadcast_text = State()

# ==============================================================================
# 4. KEYBOARDS (KEYBOARD GENERATORS)
# ==============================================================================

class Keyboards:
    @staticmethod
    def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
        keyboard = [
            [InlineKeyboardButton(text="📢 O'z Kanalimda Auksion Ochish", callback_data="create_auction")],
            [InlineKeyboardButton(text="🔥 Faol Auksionlar", callback_data="list_auctions")],
            [InlineKeyboardButton(text="⭐ Kabinetim & Balans", callback_data="user_balance"),
             InlineKeyboardButton(text="🏆 Top-10 Kanallar", callback_data="top_channels")],
            [InlineKeyboardButton(text="📞 Qo'llanma & Yordam", callback_data="help_menu")]
        ]
        if is_admin:
            keyboard.append([InlineKeyboardButton(text="👑 Admin Panel", callback_data="admin_panel")])
        return InlineKeyboardMarkup(inline_keyboard=keyboard)

    @staticmethod
    def admin_panel() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💳 To'lov So'rovlarini Tasdiqlash", callback_data="admin_pending_payments")],
                [InlineKeyboardButton(text="📊 To'liq Statistika", callback_data="admin_stats")],
                [InlineKeyboardButton(text="🛠️ Auksionlarni Boshqarish & Yakunlash", callback_data="admin_manage_auctions")],
                [InlineKeyboardButton(text="✉️ Hammaga Xabar Yuborish", callback_data="admin_broadcast")],
                [InlineKeyboardButton(text="◀️ Bosh Menyu", callback_data="back_home")]
            ]
        )

    @staticmethod
    def balance_menu() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💳 Balansni So'm orqali to'ldirish", callback_data="topup_sum")],
                [InlineKeyboardButton(text="◀️ Orqaga", callback_data="back_home")]
            ]
        )

    @staticmethod
    def auction_bid_options(auction_id: int, current_price: float) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="⭐ Telegram Stars orqali", callback_data=f"bid_tg_{auction_id}"),
                    InlineKeyboardButton(text="💳 Bot balansi (So'm/Star) orqali", callback_data=f"bid_bot_{auction_id}")
                ],
                [InlineKeyboardButton(text="◀️ Orqaga", callback_data="back_home")]
            ]
        )

    @staticmethod
    def auction_bid_keyboard(auction_id: int, current_price: float) -> InlineKeyboardMarkup:
        p = int(current_price)
        buttons = []
        row = []
        
        for i in range(1, 11):
            next_price = p + i
            row.append(InlineKeyboardButton(text=f"{next_price} ⭐", callback_data=f"paybid_{auction_id}_{next_price}"))
            if len(row) == 5:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        return InlineKeyboardMarkup(inline_keyboard=buttons)

    @staticmethod
    def back_home() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Orqaga", callback_data="back_home")]]
        )

# ==============================================================================
# 5. HANDLERS & ROUTERS
# ==============================================================================

router = Router()

@router.message(CommandStart())
async def cmd_start(message: Message):
    user = message.from_user
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user.id,))
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO users (user_id, username, full_name, bot_balance, joined_date) VALUES (?, ?, ?, 0.0, ?)",
                (user.id, user.username, user.full_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            conn.commit()

    is_admin = user.id in ADMIN_IDS
    await message.answer(
        f"Assalomu alaykum, <b>{html.quote(user.full_name)}</b>!\n\n"
        "Telegram NFT va Stars auksionlari botiga xush kelibsiz.",
        reply_markup=Keyboards.main_menu(is_admin),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "back_home")
async def process_back_home(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    is_admin = callback.from_user.id in ADMIN_IDS
    try:
        await callback.message.edit_text(
            "Asosiy menyuga qaytdingiz. Kerakli bo'limni tanlang:",
            reply_markup=Keyboards.main_menu(is_admin)
        )
    except TelegramBadRequest:
        await callback.message.answer(
            "Asosiy menyuga qaytdingiz. Kerakli bo'limni tanlang:",
            reply_markup=Keyboards.main_menu(is_admin)
        )
    await callback.answer()

@router.callback_query(F.data == "user_balance")
async def process_user_balance(callback: CallbackQuery):
    user_id = callback.from_user.id
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user_row = cursor.fetchone()
        bot_balance = user_row["bot_balance"] if user_row else 0.0

        cursor.execute("SELECT * FROM channels WHERE owner_id = ?", (user_id,))
        channels = cursor.fetchall()

    text = (
        f"👤 <b>Shaxsiy kabinet va balans:</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"⭐ Botdagi balansingiz: <b>{bot_balance} Stars</b>\n\n"
    )
    if not channels:
        text += "Sizda hali kanallar va ularning balanslari mavjud emas."
    else:
        text += "<b>Kanadlaringizdagi mablag'lar (95% qismi):</b>\n"
        for ch in channels:
            title = html.quote(ch['channel_title'] or "Kanal")
            text += f"▪️ <b>{title}</b> — ⭐ {ch['balance']} Stars\n"

    await callback.message.edit_text(text, reply_markup=Keyboards.balance_menu(), parse_mode="HTML")
    await callback.answer()

# --- TOP UP BALANCE VIA SUM ---
@router.callback_query(F.data == "topup_sum")
async def process_topup_sum(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        f"💳 <b>Balansni to'ldirish</b>\n\n"
        f"Kurs: <b>1 Star = {SUM_PER_STAR} so'm</b>\n"
        f"Karta raqami: <code>{CARD_NUMBER}</code>\n"
        f"Karta egasi: <b>{CARD_HOLDER}</b>\n\n"
        f"Iltimos, kartaga o'tkazmoqchi bo'lgan <b>so'm miqdorini</b> kiriting (masalan: 18000):",
        parse_mode="HTML"
    )
    await state.set_state(TopUpStates.waiting_for_sum)
    await callback.answer()

@router.message(TopUpStates.waiting_for_sum)
async def process_topup_sum_input(message: Message, state: FSMContext):
    try:
        sum_amount = float(message.text.strip())
        if sum_amount <= 0:
            raise ValueError()
    except ValueError:
        return await message.answer("⚠️ Iltimos, to'g'ri raqam kiriting (masalan: 18000):")

    stars_amount = sum_amount / SUM_PER_STAR
    await state.update_data(sum_amount=sum_amount, stars_amount=stars_amount)

    await message.answer(
        f"Siz kiritgan summa: <b>{sum_amount} so'm</b>\n"
        f"Hisobingizga tushadigan Stars: <b>{stars_amount:.2f} ⭐</b>\n\n"
        f"Karta raqamiga pulni o'tkazing va to'lovni tasdiqlovchi <b>chekni (skrinshotni)</b> rasm ko'rinishida yuboring:",
        parse_mode="HTML"
    )
    await state.set_state(TopUpStates.waiting_for_receipt)

@router.message(TopUpStates.waiting_for_receipt, F.photo)
async def process_topup_receipt(message: Message, state: FSMContext):
    data = await state.get_data()
    sum_amount = data["sum_amount"]
    stars_amount = data["stars_amount"]
    photo_id = message.photo[-1].file_id
    user_id = message.from_user.id
    user_name = message.from_user.full_name

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO payments (user_id, amount_sum, amount_stars, photo_id, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
        """, (user_id, sum_amount, stars_amount, photo_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        payment_id = cursor.lastrowid

    # Adminga xabar yuborish
    admin_text = (
        f"🔔 <b>YANGI TO'LOV SO'ROVI! #id{payment_id}</b>\n\n"
        f"👤 Foydalanuvchi: <a href='tg://user?id={user_id}'>{html.quote(user_name)}</a> (<code>{user_id}</code>)\n"
        f"💰 Summa: <b>{sum_amount} so'm</b>\n"
        f"⭐ Berilishi kerak: <b>{stars_amount:.2f} Stars</b>"
    )
    admin_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"approve_pay_{payment_id}"),
                InlineKeyboardButton(text="❌ Rad etish", callback_data=f"reject_pay_{payment_id}")
            ]
        ]
    )

    for admin_id in ADMIN_IDS:
        try:
            await message.bot.send_photo(chat_id=admin_id, photo=photo_id, caption=admin_text, reply_markup=admin_kb, parse_mode="HTML")
        except Exception:
            pass

    await message.answer("✅ Chekingiz adminga yuborildi! Admin tasdiqlagach, balansingizga avtomatik qo'shiladi.", reply_markup=Keyboards.main_menu(message.from_user.id in ADMIN_IDS))
    await state.clear()

@router.message(TopUpStates.waiting_for_receipt)
async def process_topup_receipt_wrong(message: Message):
    await message.answer("⚠️ Iltimos, to'lov chekini rasm ko'rinishida yuboring!")

# --- ADMIN PAYMENT APPROVAL ---
@router.callback_query(F.data == "admin_pending_payments")
async def process_admin_pending_payments(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("❌ Ruxsat yo'q!", show_alert=True)

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM payments WHERE status = 'pending'")
        payments = cursor.fetchall()

    if not payments:
        return await callback.message.edit_text("📭 Hozircha kutilayotgan to'lovlar yo'q.", reply_markup=Keyboards.admin_panel())

    await callback.message.edit_text(f"📋 Kutilayotgan to'lovlar soni: {len(payments)} ta. Ularni ko'rib chiqish uchun admin chatdagi rasmlarga qarang.", reply_markup=Keyboards.admin_panel())
    await callback.answer()

@router.callback_query(F.data.startswith("approve_pay_"))
async def process_approve_pay(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("❌ Ruxsat yo'q!", show_alert=True)

    payment_id = int(callback.data.split("_")[2])
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM payments WHERE payment_id = ? AND status = 'pending'", (payment_id,))
        pay = cursor.fetchone()

        if not pay:
            return await callback.answer("❌ Bu to'lov topilmadi yoki allaqachon ko'rib chiqilgan!", show_alert=True)

        user_id = pay["user_id"]
        stars_amount = pay["amount_stars"]

        # Balansga qo'shish
        cursor.execute("UPDATE users SET bot_balance = bot_balance + ? WHERE user_id = ?", (stars_amount, user_id))
        cursor.execute("UPDATE payments SET status = 'approved' WHERE payment_id = ?", (payment_id,))
        conn.commit()

    try:
        await callback.message.bot.send_message(
            chat_id=user_id,
            text=f"✅ Sizning to'lovingiz tasdiqlandi! Balansingizga <b>{stars_amount:.2f} Stars</b> qo'shildi.",
            parse_mode="HTML"
        )
    except Exception:
        pass

    await callback.message.edit_caption(
        caption=callback.message.caption + "\n\n<b>✅ TASDIQLANDI VA BALANSGA TUSHIRILDI</b>",
        reply_markup=None,
        parse_mode="HTML"
    )
    await callback.answer("To'lov muvaffaqiyatli tasdiqlandi!")

@router.callback_query(F.data.startswith("reject_pay_"))
async def process_reject_pay(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("❌ Ruxsat yo'q!", show_alert=True)

    payment_id = int(callback.data.split("_")[2])
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM payments WHERE payment_id = ? AND status = 'pending'", (payment_id,))
        pay = cursor.fetchone()

        if not pay:
            return await callback.answer("❌ Bu to'lov topilmadi!", show_alert=True)

        user_id = pay["user_id"]
        cursor.execute("UPDATE payments SET status = 'rejected' WHERE payment_id = ?", (payment_id,))
        conn.commit()

    try:
        await callback.message.bot.send_message(
            chat_id=user_id,
            text="❌ Afsuski, to'lov chekingiz admin tomonidan rad etildi. Savollar bo'lsa admin bilan bog'laning.",
            parse_mode="HTML"
        )
    except Exception:
        pass

    await callback.message.edit_caption(
        caption=callback.message.caption + "\n\n<b>❌ RAD ETILDI</b>",
        reply_markup=None,
        parse_mode="HTML"
    )
    await callback.answer("To'lov rad etildi.")

@router.callback_query(F.data == "top_channels")
async def process_top_channels(callback: CallbackQuery):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM channels ORDER BY total_stars_generated DESC LIMIT 10")
        channels = cursor.fetchall()

    if not channels:
        text = "🏆 <b>Hozircha reytingda kanallar mavjud emas.</b>"
    else:
        text = "🏆 <b>Top-10 Kanallar (Auksionlar va Yig'ilgan Stars):</b>\n\n"
        for idx, ch in enumerate(channels, 1):
            title = html.quote(ch['channel_title'] or "Kanal")
            total_auc = ch['total_auctions_count'] or 0
            total_stars = ch['total_stars_generated'] or 0.0
            text += f"{idx}. <b>{title}</b>\n   ▫️ Auksionlar soni: {total_auc} ta\n   ▫️ Yig'ilgan stars: ⭐ {total_stars}\n\n"

    await callback.message.edit_text(text, reply_markup=Keyboards.back_home(), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "help_menu")
async def process_help_menu(callback: CallbackQuery):
    text = (
        "📞 <b>Qo'llanma:</b>\n\n"
        "1. Botni kanalingizga admin qiling.\n"
        "2. Auksion ochish tugmasini bosing, sovrin nomi va NFT havolasini kiriting.\n"
        "3. Ishtirokchilar Telegram Stars yoki so'm orqali to'ldirilgan bot balansi bilan stavka qo'shadilar.\n"
        "4. Auksion yakunlanganda 95% kanal balansiga tushadi, 5% esa bevosita sizning Telegram profilingizga yuboriladi."
    )
    await callback.message.edit_text(text, reply_markup=Keyboards.back_home(), parse_mode="HTML")
    await callback.answer()

# --- AUCTION CREATION (NFT & PRIZE) ---
@router.callback_query(F.data == "create_auction")
async def process_start_create_auction(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "📢 Auksion o'tkazmoqchi bo'lgan <b>kanalingiz ID raqamini</b> yoki <b>@username</b> raqamini kiriting:\n"
        "<i>(Masalan: -100123456789) Bot o'sha kanalda admin bo'lishi shart!</i>",
        parse_mode="HTML"
    )
    await state.set_state(AuctionStates.waiting_for_channel_id)
    await callback.answer()

@router.message(AuctionStates.waiting_for_channel_id)
async def process_auction_channel(message: Message, state: FSMContext):
    try:
        chat_info = await message.bot.get_chat(message.text.strip())
        await state.update_data(channel_id=chat_info.id, channel_title=chat_info.title or "Kanal", channel_username=chat_info.username)
    except Exception as e:
        return await message.answer(f"⚠️ Xatolik: {e}\nBot kanalga admin qilinganligini tekshirib, qaytadan kiriting:")
    
    await message.answer("🎁 <b>Sovrin nomini</b> kiriting (masalan: Telegram Username yoki NFT raqami):", parse_mode="HTML")
    await state.set_state(AuctionStates.waiting_for_prize_name)

@router.message(AuctionStates.waiting_for_prize_name)
async def process_auction_prize_name(message: Message, state: FSMContext):
    await state.update_data(prize_name=message.text)
    await message.answer("🔗 Agar bu <b>Telegram NFT</b> bo'lsa uning havolasini (silkasini) yuboring. Agar NFT bo'lmasa <b>'yo'q'</b> deb yozib yuboring:", parse_mode="HTML")
    await state.set_state(AuctionStates.waiting_for_nft_link)

@router.message(AuctionStates.waiting_for_nft_link)
async def process_auction_nft_link(message: Message, state: FSMContext):
    link_text = message.text.strip()
    nft_link = link_text if link_text.lower() != "yo'q" else "Mavjud emas"
    await state.update_data(nft_link=nft_link)
    
    await message.answer("💰 Boshlang'ich narxni kiriting (Stars miqdori, masalan: 10):")
    await state.set_state(AuctionStates.waiting_for_start_price)

@router.message(AuctionStates.waiting_for_start_price)
async def process_auction_start_price(message: Message, state: FSMContext):
    try:
        start_price = float(message.text)
    except ValueError:
        return await message.answer("⚠️ Faqat raqam kiriting!")
    
    data = await state.get_data()
    user_id = message.from_user.id
    prize_name = data["prize_name"]
    nft_link = data["nft_link"]

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO channels (channel_id, owner_id, channel_title, username) 
            VALUES (?, ?, ?, ?) 
            ON CONFLICT(channel_id) DO UPDATE SET channel_title = excluded.channel_title, owner_id = excluded.owner_id
        """, (data["channel_id"], user_id, data["channel_title"], data["channel_username"]))
        
        cursor.execute("""
            INSERT INTO auctions (creator_id, channel_id, message_id, prize_name, nft_link, current_price, current_leader_id, current_leader_name, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, data["channel_id"], 0, prize_name, nft_link, start_price,
            0, "Hozircha yo'q", "active", datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        
        # Kanalning auksionlar sonini oshirish
        cursor.execute("UPDATE channels SET total_auctions_count = total_auctions_count + 1 WHERE channel_id = ?", (data["channel_id"],))
        conn.commit()
        auction_id = cursor.lastrowid

    nft_line = f"🔗 NFT havola: <a href='{nft_link}'>Ko'rish</a>\n" if nft_link != "Mavjud emas" else ""

    auction_text = (
        f"🔥 <b>YANGI AUKSION BOSHLANDI!</b>\n\n"
        f"🎁 Sovrin: <b>{prize_name}</b>\n"
        f"{nft_line}"
        f"💰 Boshlang'ich narx: <b>{start_price} Stars</b>\n"
        f"👤 Etakchi: Hozircha yo'q\n\n"
        f"Stavka qilish uchun pastdagi tugmani bosing:"
    )

    try:
        sent_msg = await message.bot.send_message(
            chat_id=data["channel_id"],
            text=auction_text,
            reply_markup=Keyboards.auction_bid_keyboard(auction_id, start_price),
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE auctions SET message_id = ? WHERE auction_id = ?", (sent_msg.message_id, auction_id))
            conn.commit()
            
        await message.answer("✅ Auksion kanalingizga muvaffaqiyatli joylandi!", reply_markup=Keyboards.main_menu(message.from_user.id in ADMIN_IDS))
    except Exception as e:
        await message.answer(f"⚠️ Xatolik yuz berdi: {e}", reply_markup=Keyboards.main_menu(message.from_user.id in ADMIN_IDS))

    await state.clear()

# --- BIDDING METHODS (TELEGRAM STARS OR BOT BALANCE) ---
@router.callback_query(F.data.startswith("paybid_"))
async def process_pay_bid_options(callback: CallbackQuery):
    parts = callback.data.split("_")
    auction_id = int(parts[1])
    stars_amount = int(parts[2])

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM auctions WHERE auction_id = ?", (auction_id,))
        auction = cursor.fetchone()
        
        if not auction or auction["status"] != "active":
            return await callback.answer("❌ Bu auksion faol emas!", show_alert=True)
        if stars_amount <= auction["current_price"]:
            return await callback.answer("❌ Tanlangan stavka joriy narxdan baland bo'lishi kerak!", show_alert=True)

    # Foydalanuvchiga qaysi usulda to'lamoqchiligini tanlatamiz
    text = (
        f"⚡ <b>Stavka qilish: {stars_amount} Stars</b>\n\n"
        f"Qaysi usul orqali stavka qilmoqchisiz?"
    )
    await callback.message.answer(text, reply_markup=Keyboards.auction_bid_options(auction_id, stars_amount), parse_mode="HTML")
    await callback.answer()

# 1. Telegram Stars orqali to'lov (Invoice)
@router.callback_query(F.data.startswith("bid_tg_"))
async def process_bid_tg(callback: CallbackQuery):
    # Bu yerda oddiygina oxirgi tanlangan summani topish uchun callback_data dan foydalanamiz yoki xabardan o'qiymiz
    # Yoki to'g'ridan to'g'ri invoice ochish uchun xabardan summani olamiz
    try:
        parts = callback.message.text.split(":")
        # Xabardan summani ajratib olamiz
        line = [l for l in callback.message.text.split("\n") if "Stavka qilish" in l][0]
        stars_amount = int(''.join(filter(str.isdigit, line.split(":")[1])))
        auction_id = int(callback.data.split("_")[2])
    except Exception:
        return await callback.answer("❌ Xatolik yuz berdi.", show_alert=True)

    title = f"Auksion stavkasi"
    description = f"{stars_amount} Stars to'lab yetakchi bo'lish."
    payload = f"auction_{auction_id}_{stars_amount}"
    prices = [LabeledPrice(label="Stars", amount=stars_amount)]

    try:
        await callback.message.bot.send_invoice(
            chat_id=callback.from_user.id,
            title=title,
            description=description,
            payload=payload,
            currency="XTR",
            prices=prices
        )
        await callback.answer("To'lov oynasi yuborildi. Shaxsiy chatni tekshiring!")
    except Exception as e:
        await callback.answer(f"Xatolik: {e}", show_alert=True)

@router.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@router.message(F.successful_payment)
async def process_successful_payment(message: Message):
    payload = message.successful_payment.invoice_payload
    if not payload.startswith("auction_"):
        return
    
    parts = payload.split("_")
    auction_id = int(parts[1])
    stars_amount = int(parts[2])
    finalize_bid(message, auction_id, stars_amount, message.from_user.id, message.from_user.full_name)

# 2. Bot balansi (So'm orqali to'ldirilgan) orqali stavka qilish
@router.callback_query(F.data.startswith("bid_bot_"))
async def process_bid_bot(callback: CallbackQuery):
    try:
        line = [l for l in callback.message.text.split("\n") if "Stavka qilish" in l][0]
        stars_amount = float(''.join(filter(lambda x: x.isdigit() or x == '.', line.split(":")[1])))
        auction_id = int(callback.data.split("_")[2])
    except Exception:
        return await callback.answer("❌ Xatolik yuz berdi.", show_alert=True)

    user_id = callback.from_user.id
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT bot_balance FROM users WHERE user_id = ?", (user_id,))
        user_row = cursor.fetchone()
        bot_balance = user_row["bot_balance"] if user_row else 0.0

        if bot_balance < stars_amount:
            return await callback.answer(f"❌ Botdagi balansingiz yetarli emas! (Balans: {bot_balance} Stars)", show_value=True, show_alert=True)

        cursor.execute("SELECT * FROM auctions WHERE auction_id = ? AND status = 'active'", (auction_id,))
        auction = cursor.fetchone()
        if not auction:
            return await callback.answer("❌ Bu auksion faol emas!", show_alert=True)
        if stars_amount <= auction["current_price"]:
            return await callback.answer("❌ Stavka joriy narxdan baland bo'lishi kerak!", show_alert=True)

        # Balansdan ayirish
        cursor.execute("UPDATE users SET bot_balance = bot_balance - ? WHERE user_id = ?", (stars_amount, user_id))
        conn.commit()

    finalize_bid(callback.message, auction_id, stars_amount, user_id, callback.from_user.full_name, is_bot_balance=True)
    await callback.answer("✅ Bot balansingizdan stavka qilindi!")

def finalize_bid(message_obj, auction_id, stars_amount, user_id, user_name, is_bot_balance=False):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM auctions WHERE auction_id = ? AND status = 'active'", (auction_id,))
        auction = cursor.fetchone()
        
        if not auction:
            return
        
        new_price = float(stars_amount)
        
        cursor.execute("""
            UPDATE auctions 
            SET current_price = ?, current_leader_id = ?, current_leader_name = ?
            WHERE auction_id = ?
        """, (new_price, user_id, user_name, auction_id))
        
        cursor.execute("""
            UPDATE channels 
            SET total_stars_generated = total_stars_generated + ?
            WHERE channel_id = ?
        """, (new_price, auction["channel_id"]))
        
        conn.commit()

    nft_link = auction["nft_link"]
    nft_line = f"🔗 NFT havola: <a href='{nft_link}'>Ko'rish</a>\n" if nft_link != "Mavjud emas" else ""

    updated_text = (
        f"🔥 <b>YANGI AUKSION!</b>\n\n"
        f"🎁 Sovrin: <b>{auction['prize_name']}</b>\n"
        f"{nft_line}"
        f"💰 Joriy narx: <b>{new_price} Stars</b>\n"
        f"👤 Etakchi: <b>{user_name}</b>\n\n"
        f"Oxirgi stavka: +{stars_amount} Stars!"
    )

    bot_inst = message_obj.bot if hasattr(message_obj, 'bot') else message_obj
    # Asynchronous sending/editing
    asyncio.create_task(update_channel_auction_msg(bot_inst, auction["channel_id"], auction["message_id"], auction_id, new_price, updated_text))
    if not is_bot_balance and hasattr(message_obj, 'answer'):
        asyncio.create_task(message_obj.answer(f"✅ Muvaffaqiyatli {stars_amount} Stars stavka qilindi!"))

async def update_channel_auction_msg(bot, channel_id, message_id, auction_id, new_price, updated_text):
    try:
        await bot.edit_message_text(
            chat_id=channel_id,
            message_id=message_id,
            text=updated_text,
            reply_markup=Keyboards.auction_bid_keyboard(auction_id, new_price),
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    except Exception:
        pass

@router.callback_query(F.data == "list_auctions")
async def process_list_active_auctions(callback: CallbackQuery):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM auctions WHERE status = 'active'")
        auctions = cursor.fetchall()

    if not auctions:
        text = "📭 Hozirda faol auksionlar mavjud emas."
        keyboard = Keyboards.back_home()
    else:
        text = "🔥 <b>Faol auksionlar va ularni tugatish:</b>\n\n"
        buttons = []
        for auc in auctions:
            text += f"▪️ <b>{auc['prize_name']}</b> — Narx: {auc['current_price']} Stars (Etakchi: {auc['current_leader_name']})\n"
            buttons.append([InlineKeyboardButton(text=f"🏁 Tugatish: {auc['prize_name']} ({auc['current_price']} ⭐)", callback_data=f"finish_auc_{auc['auction_id']}")])
        buttons.append([InlineKeyboardButton(text="◀️ Orqaga", callback_data="back_home")])
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

# --- ADMIN / CHANNEL OWNER PANEL & 95% / 5% COMMISSION SYSTEM ---
@router.callback_query(F.data == "admin_panel")
async def process_admin_panel(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("❌ Ruxsat yo'q!", show_alert=True)
    await callback.message.edit_text("👑 <b>Admin boshqaruv paneli:</b>", reply_markup=Keyboards.admin_panel(), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "admin_stats")
async def process_admin_stats(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("❌ Ruxsat yo'q!", show_alert=True)
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM users")
        users_count = cursor.fetchone()["cnt"]

    text = (
        f"📊 <b>Statistika:</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{users_count} ta</b>\n"
        f"👑 5% ulushlar bevosita Telegram'dagi Yulduzlar (Stars) profilingizga tushadi."
    )
    await callback.message.edit_text(text, reply_markup=Keyboards.admin_panel(), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "admin_manage_auctions")
async def process_admin_manage_auctions(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("❌ Ruxsat yo'q!", show_alert=True)
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM auctions WHERE status = 'active'")
        auctions = cursor.fetchall()

    if not auctions:
        text = "📭 Faol auksionlar yo'q."
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Orqaga", callback_data="admin_panel")]])
    else:
        text = "🛠️ <b>Auksionni tugatish va 95% / 5% ga taqsimlash:</b>"
        buttons = []
        for auc in auctions:
            buttons.append([InlineKeyboardButton(text=f"🏁 Yakunlash: {auc['prize_name']} ({auc['current_price']} ⭐)", callback_data=f"finish_auc_{auc['auction_id']}")])
        buttons.append([InlineKeyboardButton(text="◀️ Orqaga", callback_data="admin_panel")])
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("finish_auc_"))
async def process_finish_auction(callback: CallbackQuery):
    auction_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM auctions WHERE auction_id = ? AND status = 'active'", (auction_id,))
        auction = cursor.fetchone()
        
        if not auction:
            return await callback.answer("❌ Auksion topilmadi yoki allaqachon tugatilgan!", show_alert=True)
        
        cursor.execute("SELECT owner_id FROM channels WHERE channel_id = ?", (auction["channel_id"],))
        ch_owner = cursor.fetchone()
        owner_id = ch_owner["owner_id"] if ch_owner else 0

        if user_id not in ADMIN_IDS and user_id != owner_id:
            return await callback.answer("❌ Bu auksionni faqat admin yoki kanal egasi tugatishi mumkin!", show_alert=True)
        
        total_price = auction["current_price"]
        admin_share_stars = int(total_price * 0.05)      # 5%
        channel_share = total_price * 0.95               # 95% kanal balansiga

        if admin_share_stars < 1 and total_price >= 20:
            admin_share_stars = 1

        # 95% qismini kanal balansiga yozish
        cursor.execute("UPDATE channels SET balance = balance + ? WHERE channel_id = ?", (channel_share, auction["channel_id"]))
        # Auksionni yopish
        cursor.execute("UPDATE auctions SET status = 'finished' WHERE auction_id = ?", (auction_id,))
        conn.commit()

    if admin_share_stars > 0:
        try:
            await callback.message.bot.send_invoice(
                chat_id=ADMIN_IDS[0],
                title="Admin 5% Komissiyasi",
                description=f"Auksion yakunlandi. {admin_share_stars} Stars ulushingiz.",
                payload=f"admin_com_{auction_id}",
                currency="XTR",
                prices=[LabeledPrice(label="Komissiya", amount=admin_share_stars)]
            )
        except Exception as e:
            logger.error(f"Adminga stars yuborishda xatolik: {e}")

    try:
        await callback.message.bot.edit_message_text(
            chat_id=auction["channel_id"],
            message_id=auction["message_id"],
            text=f"🏁 <b>AUKSION YAKUNLANDI!</b>\n\n"
                 f"🎁 Sovrin: {auction['prize_name']}\n"
                 f"💰 Jami bank: {total_price} Stars\n"
                 f"👑 G'olib: <b>{auction['current_leader_name']}</b>",
            reply_markup=None,
            parse_mode="HTML"
        )
    except Exception:
        pass

    await callback.answer(f"✅ Auksion yakunlandi!\n⭐ 95% ({channel_share} Stars) kanal balansiga, 5% adminga yuborildi.", show_alert=True)
    
    try:
        await callback.message.edit_text("✅ Auksion muvaffaqiyatli yakunlandi va balanslar taqsimlandi.", reply_markup=Keyboards.back_home())
    except Exception:
        pass

@router.callback_query(F.data == "admin_broadcast")
async def process_admin_broadcast(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("❌ Ruxsat yo'q!", show_alert=True)
    
    await callback.message.answer("✉️ Ommaviy xabar matnini kiriting:")
    await state.set_state(AdminStates.waiting_for_broadcast_text)
    await callback.answer()

@router.message(AdminStates.waiting_for_broadcast_text)
async def execute_broadcast(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users")
        users = cursor.fetchall()

    success = 0
    for row in users:
        try:
            await message.bot.send_message(chat_id=row["user_id"], text=message.text, parse_mode="HTML")
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass

    await message.answer(f"✅ Xabar yuborildi: {success} ta foydalanuvchiga.", reply_markup=Keyboards.admin_panel())
    await state.clear()

# ==============================================================================
# 6. WEB SERVER FOR RENDER
# ==============================================================================

async def handle_ping(request):
    return web.Response(text="Bot is running perfectly!", status=200)

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Web server port {port} da ishga tushdi.")

# ==============================================================================
# 7. MAIN ENGINE
# ==============================================================================

async def main():
    bot = Bot(token=TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    
    dp.include_router(router)
    
    await bot.set_my_commands([
        BotCommand(command="start", description="Botni qayta ishga tushirish")
    ], scope=BotCommandScopeDefault())

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("NFT Auksion boti ishga tushdi!")
    
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot to'xtatildi.")
