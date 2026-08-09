# ==============================================================================
# PROJECT: ULTIMATE TELEGRAM CHANNEL AUCTION & STARS MANAGEMENT SYSTEM
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
    BotCommand, BotCommandScopeDefault
)
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

# ==============================================================================
# 1. CONFIGURATION & LOGGING
# ==============================================================================

TOKEN = "8655535261:AAETrrG_B7Q_DxChzSuFhaWZ8jnmmggtW4c"
ADMIN_IDS = [543210123]  # O'zingizning Admin ID raqamingiz
COMMISSION_PERCENT = 1.0   # Har bir stavka qiymatidan admin balansiga tushadigan foiz (%)

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s"
)
logger = logging.getLogger("UltimateAuctionBot")

# ==============================================================================
# 2. DATABASE MANAGER (SQLITE ENGINE)
# ==============================================================================

class Database:
    def __init__(self, db_file: str = "ultimate_bot.db"):
        self.db_file = db_file
        self.init_db()

    def get_connection(self):
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            # Foydalanuvchilar jadvali
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    balance REAL DEFAULT 0.0,
                    stars_balance INTEGER DEFAULT 0,
                    joined_date TEXT
                )
            """)
            # Kanallar va faollik reytingi uchun jadval
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS channels (
                    channel_id INTEGER PRIMARY KEY,
                    channel_title TEXT,
                    username TEXT,
                    total_bids_count INTEGER DEFAULT 0,
                    total_stars_generated REAL DEFAULT 0.0
                )
            """)
            # Auksionlar jadvali
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS auctions (
                    auction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER,
                    message_id INTEGER,
                    lot_name TEXT,
                    lot_description TEXT,
                    current_price REAL,
                    min_step REAL,
                    current_leader_id INTEGER,
                    current_leader_name TEXT,
                    status TEXT DEFAULT 'active',
                    created_at TEXT
                )
            """)
            # Admin moliyaviy / komissiya balansi
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS admin_wallet (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    earned_stars REAL DEFAULT 0.0
                )
            """)
            cursor.execute("INSERT OR IGNORE INTO admin_wallet (id, earned_stars) VALUES (1, 0.0)")
            conn.commit()
        logger.info("Ma'lumotlar bazasi muvaffaqiyatli ishga tushirildi.")

db = Database()

# ==============================================================================
# 3. FSM STATES (STATE MACHINE)
# ==============================================================================

class AuctionStates(StatesGroup):
    waiting_for_channel_id = State()
    waiting_for_lot_name = State()
    waiting_for_lot_desc = State()
    waiting_for_start_price = State()

class AdminStates(StatesGroup):
    waiting_for_broadcast_text = State()

# ==============================================================================
# 4. KEYBOARDS (KEYBOARD GENERATORS)
# ==============================================================================

class Keyboards:
    @staticmethod
    def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
        keyboard = [
            [InlineKeyboardButton(text="🔥 Faol Auksionlar", callback_data="list_auctions")],
            [InlineKeyboardButton(text="⭐ Stars / Kabinet", callback_data="user_balance"),
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
                [InlineKeyboardButton(text="📢 Auksion Yaratish", callback_data="admin_create_auction")],
                [InlineKeyboardButton(text="🛑 Auksionni To'xtatish", callback_data="admin_stop_auction_list")],
                [InlineKeyboardButton(text="✉️ Hammaga Xabar Yuborish", callback_data="admin_broadcast")],
                [InlineKeyboardButton(text="📊 Statistika", callback_data="admin_stats")],
                [InlineKeyboardButton(text="◀️ Bosh Menyu", callback_data="back_home")]
            ]
        )

    @staticmethod
    def auction_bid_keyboard(auction_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="⭐ +1 Star", callback_data=f"bid_{auction_id}_1"),
                    InlineKeyboardButton(text="⭐ +5 Stars", callback_data=f"bid_{auction_id}_5"),
                    InlineKeyboardButton(text="⭐ +10 Stars", callback_data=f"bid_{auction_id}_10")
                ],
                [
                    InlineKeyboardButton(text="🚀 Maxsus Stavka (+50)", callback_data=f"bid_{auction_id}_50")
                ]
            ]
        )

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
                "INSERT INTO users (user_id, username, full_name, joined_date) VALUES (?, ?, ?, ?)",
                (user.id, user.username, user.full_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            conn.commit()

    is_admin = user.id in ADMIN_IDS
    await message.answer(
        f"Assalomu alaykum, <b>{html.quote(user.full_name)}</b>!\n\n"
        "Kanal auksionlari va Stars boshqaruv tizimiga xush kelibsiz.\n"
        "Kerakli bo'limni tanlang:",
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

# --- USER PROFILE & TOP CHANNELS ---
@router.callback_query(F.data == "user_balance")
async def process_user_balance(callback: CallbackQuery):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT balance, stars_balance FROM users WHERE user_id = ?", (callback.from_user.id,))
        row = cursor.fetchone()
        balance = row["balance"] if row else 0.0
        stars = row["stars_balance"] if row else 0

    text = (
        f"👤 <b>Sizning kabinetingiz:</b>\n\n"
        f"🆔 ID: <code>{callback.from_user.id}</code>\n"
        f"💰 Asosiy balans: <b>{balance} so'm</b>\n"
        f"⭐ Stars balans: <b>{stars} ta</b>"
    )
    await callback.message.edit_text(text, reply_markup=Keyboards.back_home(), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "top_channels")
async def process_top_channels(callback: CallbackQuery):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM channels ORDER BY total_stars_generated DESC LIMIT 10")
        channels = cursor.fetchall()

    if not channels:
        text = "🏆 <b>Hozircha reytingda kanallar mavjud emas.</b>"
    else:
        text = "🏆 <b>Eng ko'p faoliyat ko'rsatgan Top-10 Kanallar:</b>\n\n"
        for idx, ch in enumerate(channels, 1):
            title = html.quote(ch['channel_title'] or "Noma'lum kanal")
            text += f"{idx}. <b>{title}</b> — ⭐ {ch['total_stars_generated']} Stars ({ch['total_bids_count']} ta stavka)\n"

    await callback.message.edit_text(text, reply_markup=Keyboards.back_home(), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "help_menu")
async def process_help_menu(callback: CallbackQuery):
    text = (
        "📞 <b>Qo'llanma va Yordam:</b>\n\n"
        "1. Bot orqali kanallarda qiziqarli lotlar bo'yicha auksionlar o'tkaziladi.\n"
        "2. Har bir stavka qilinganda belgilangan foiz (1%) avtomatik ravishda admin balansiga o'tadi.\n"
        "3. Istalgan vaqtda auksionni boshlash va to'xtatish imkoniyati mavjud."
    )
    await callback.message.edit_text(text, reply_markup=Keyboards.back_home(), parse_mode="HTML")
    await callback.answer()

# --- ADMIN PANEL & BROADCAST ---
@router.callback_query(F.data == "admin_panel")
async def process_admin_panel(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("❌ Sizda bu huquq yo'q!", show_alert=True)
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
        cursor.execute("SELECT COUNT(*) as cnt FROM auctions WHERE status = 'active'")
        active_auctions_count = cursor.fetchone()["cnt"]
        cursor.execute("SELECT earned_stars FROM admin_wallet WHERE id = 1")
        admin_stars = cursor.fetchone()["earned_stars"]

    text = (
        f"📊 <b>Bot Statistikasi va Hisobot:</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{users_count} ta</b>\n"
        f"🔥 Faol auksionlar: <b>{active_auctions_count} ta</b>\n"
        f"👑 Admin komissiya balansi: <b>⭐ {admin_stars} Stars</b>"
    )
    await callback.message.edit_text(text, reply_markup=Keyboards.admin_panel(), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "admin_broadcast")
async def process_admin_broadcast(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("❌ Ruxsat yo'q!", show_alert=True)
    
    await callback.message.answer("✉️ Barcha foydalanuvchilarga yuborilishi kerak bo'lgan xabar matnini kiriting:")
    await state.set_state(AdminStates.waiting_for_broadcast_text)
    await callback.answer()

@router.message(AdminStates.waiting_for_broadcast_text)
async def execute_broadcast(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    broadcast_text = message.text
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users")
        users = cursor.fetchall()

    await message.answer(f"🚀 Xabar yuborish boshlandi. Jami foydalanuvchilar: {len(users)} ta")
    
    success_count = 0
    for row in users:
        try:
            await message.bot.send_message(chat_id=row["user_id"], text=broadcast_text, parse_mode="HTML")
            success_count += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass

    await message.answer(f"✅ Xabar tarqatish yakunlandi!\nMuvaffaqiyatli yuborildi: {success_count} ta", reply_markup=Keyboards.admin_panel())
    await state.clear()

# --- AUCTION CREATION & MANAGEMENT ---
@router.callback_query(F.data == "admin_create_auction")
async def process_start_create_auction(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("❌ Ruxsat yo'q!", show_alert=True)
    
    await callback.message.answer("📢 Auksion o'tkaziladigan kanal ID raqamini kiriting (masalan: `-100123456789`):")
    await state.set_state(AuctionStates.waiting_for_channel_id)
    await callback.answer()

@router.message(AuctionStates.waiting_for_channel_id)
async def process_auction_channel(message: Message, state: FSMContext):
    try:
        channel_id = int(message.text)
    except ValueError:
        return await message.answer("⚠️ Kanal ID raqam bo'lishi kerak! Qaytadan kiriting:")
    
    await state.update_data(channel_id=channel_id)
    await message.answer("📦 Auksion lotining nomini kiriting:")
    await state.set_state(AuctionStates.waiting_for_lot_name)

@router.message(AuctionStates.waiting_for_lot_name)
async def process_auction_lot_name(message: Message, state: FSMContext):
    await state.update_data(lot_name=message.text)
    await message.answer("📝 Lot haqida qisqacha tavsif kiriting:")
    await state.set_state(AuctionStates.waiting_for_lot_desc)

@router.message(AuctionStates.waiting_for_lot_desc)
async def process_auction_lot_desc(message: Message, state: FSMContext):
    await state.update_data(lot_desc=message.text)
    await message.answer("💰 Boshlang'ich narxni kiriting (Stars miqdori, masalan: 10):")
    await state.set_state(AuctionStates.waiting_for_start_price)

@router.message(AuctionStates.waiting_for_start_price)
async def process_auction_start_price(message: Message, state: FSMContext):
    try:
        start_price = float(message.text)
    except ValueError:
        return await message.answer("⚠️ Iltimos, to'g'ri raqam kiriting!")
    
    data = await state.get_data()
    channel_id = data["channel_id"]
    lot_name = data["lot_name"]
    lot_desc = data["lot_desc"]

    channel_title = f"Kanal ID: {channel_id}"
    try:
        chat_info = await message.bot.get_chat(channel_id)
        channel_title = chat_info.title or channel_title
    except Exception:
        pass

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO channels (channel_id, channel_title, username) 
            VALUES (?, ?, ?) 
            ON CONFLICT(channel_id) DO UPDATE SET channel_title = excluded.channel_title
        """, (channel_id, channel_title, getattr(chat_info, 'username', None)))
        
        cursor.execute("""
            INSERT INTO auctions (channel_id, message_id, lot_name, lot_description, current_price, min_step, current_leader_id, current_leader_name, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            channel_id, 0, lot_name, lot_desc, start_price, 1.0,
            0, "Hozircha yo'q", "active", datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        conn.commit()
        auction_id = cursor.lastrowid

    auction_text = (
        f"🔥 <b>YANGI AUKSION BOSHLANDI!</b>\n\n"
        f"📦 Lot: <b>{lot_name}</b>\n"
        f"📄 Tavsif: {lot_desc}\n"
        f"💰 Boshlang'ich narx: <b>{start_price} Stars</b>\n"
        f"👤 Etakchi: Hozircha yo'q\n\n"
        f"Pastdagi tugmalar orqali stavka qiling!"
    )

    try:
        sent_msg = await message.bot.send_message(
            chat_id=channel_id,
            text=auction_text,
            reply_markup=Keyboards.auction_bid_keyboard(auction_id),
            parse_mode="HTML"
        )
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE auctions SET message_id = ? WHERE auction_id = ?", (sent_msg.message_id, auction_id))
            conn.commit()
            
        await message.answer("✅ Auksion kanalga muvaffaqiyatli joylashtirildi!", reply_markup=Keyboards.admin_panel())
    except Exception as e:
        await message.answer(f"⚠️ Kanalga yuborishda xatolik yuz berdi: {e}\nBot kanalga admin qilinganligini tekshiring.", reply_markup=Keyboards.admin_panel())

    await state.clear()

@router.callback_query(F.data == "admin_stop_auction_list")
async def process_stop_auction_list(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("❌ Ruxsat yo'q!", show_alert=True)
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM auctions WHERE status = 'active'")
        auctions = cursor.fetchall()

    if not auctions:
        text = "📭 Hozirda to'xtatish mumkin bo'lgan faol auksionlar yo'q."
        kb = Keyboards.admin_panel()
    else:
        text = "🛑 <b>To'xtatmoqchi bo'lgan auksioningizni tanlang:</b>\n\n"
        keyboard = []
        for auc in auctions:
            keyboard.append([InlineKeyboardButton(text=f"Lot: {auc['lot_name']} ({auc['current_price']} Stars)", callback_data=f"stop_auc_{auc['auction_id']}")])
        keyboard.append([InlineKeyboardButton(text="◀️ Orqaga", callback_data="admin_panel")])
        kb = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("stop_auc_"))
async def process_stop_auction_action(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("❌ Ruxsat yo'q!", show_alert=True)
    
    auction_id = int(callback.data.split("_")[2])
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM auctions WHERE auction_id = ? AND status = 'active'", (auction_id,))
        auc = cursor.fetchone()
        
        if not auc:
            return await callback.answer("❌ Auksion topilmadi yoki allaqachon yopilgan!", show_alert=True)
        
        cursor.execute("UPDATE auctions SET status = 'finished' WHERE auction_id = ?", (auction_id,))
        conn.commit()

    finish_text = (
        f"🛑 <b>AUKSION YAKUNLANDI!</b>\n\n"
        f"📦 Lot: <b>{auc['lot_name']}</b>\n"
        f"💰 Yakuniy narx: <b>{auc['current_price']} Stars</b>\n"
        f"🎉 G'olib: <b>{auc['current_leader_name']}</b>"
    )
    
    try:
        await callback.bot.edit_message_text(
            chat_id=auc['channel_id'],
            message_id=auc['message_id'],
            text=finish_text,
            parse_mode="HTML"
        )
    except Exception:
        pass

    await callback.message.edit_text("✅ Auksion muvaffaqiyatli to'xtatildi va kanalga e'lon qilindi!", reply_markup=Keyboards.admin_panel())
    await callback.answer()

# --- BIDDING & 1% COMMISSION ENGINE ---
@router.callback_query(F.data.startswith("bid_"))
async def process_bid_action(callback: CallbackQuery):
    parts = callback.data.split("_")
    if len(parts) < 3:
        return await callback.answer("Xatolik!", show_alert=True)
    
    auction_id = int(parts[1])
    increment = float(parts[2])
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM auctions WHERE auction_id = ? AND status = 'active'", (auction_id,))
        auction = cursor.fetchone()
        
        if not auction:
            return await callback.answer("❌ Bu auksion allaqachon yakunlangan!", show_alert=True)
        
        new_price = auction["current_price"] + increment
        user_name = callback.from_user.full_name
        user_id = callback.from_user.id
        
        # 1% komissiyani hisoblash va admin profiliga/balansiga qo'shish
        admin_commission = increment * (COMMISSION_PERCENT / 100.0)
        
        cursor.execute("UPDATE admin_wallet SET earned_stars = earned_stars + ? WHERE id = 1", (admin_commission,))
        
        cursor.execute("""
            UPDATE auctions 
            SET current_price = ?, current_leader_id = ?, current_leader_name = ?
            WHERE auction_id = ?
        """, (new_price, user_id, user_name, auction_id))
        
        cursor.execute("""
            UPDATE channels 
            SET total_bids_count = total_bids_count + 1, total_stars_generated = total_stars_generated + ?
            WHERE channel_id = ?
        """, (increment, auction["channel_id"]))
        
        conn.commit()

    updated_text = (
        f"🔥 <b>YANGI AUKSION BOSHLANDI!</b>\n\n"
        f"📦 Lot: <b>{auction['lot_name']}</b>\n"
        f"📄 Tavsif: {auction['lot_description']}\n"
        f"💰 Joriy narx: <b>{new_price} Stars</b>\n"
        f"👤 Etakchi: <b>{user_name}</b>\n\n"
        f"Stavka qilindi (+{increment} Stars)! Komissiya admin balansiga o'tkazildi."
    )
    
    try:
        await callback.message.edit_text(
            updated_text,
            reply_markup=Keyboards.auction_bid_keyboard(auction_id),
            parse_mode="HTML"
        )
    except TelegramBadRequest:
        pass
    
    await callback.answer(f"Siz muvaffaqiyatli +{increment} Star stavka qildingiz!")

@router.callback_query(F.data == "list_auctions")
async def process_list_active_auctions(callback: CallbackQuery):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM auctions WHERE status = 'active'")
        auctions = cursor.fetchall()

    if not auctions:
        text = "📭 Hozirda faol auksionlar mavjud emas."
        kb = Keyboards.back_home()
    else:
        text = "🔥 <b>Barcha faol auksionlar ro'yxati:</b>\n\n"
        for auc in auctions:
            text += f"▪️ <b>{auc['lot_name']}</b> — Narx: {auc['current_price']} Stars (Etakchi: {auc['current_leader_name']})\n"
        kb = Keyboards.back_home()

    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

# ==============================================================================
# 6. WEB SERVER FOR RENDER (UPTIME PING HANDLER)
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
# 7. MAIN ENGINE INITIALIZATION
# ==============================================================================

async def main():
    bot = Bot(token=TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    
    dp.include_router(router)
    
    await bot.set_my_commands([
        BotCommand(command="start", description="Botni qayta ishga tushirish / Bosh menyu")
    ], scope=BotCommandScopeDefault())

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Mukammal Auksion va Stars boti muvaffaqiyatli ishga tushdi!")
    
    # Web server va Polling'ni birgalikda ishga tushirish
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot to'xtatildi.")
