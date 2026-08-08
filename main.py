# ==============================================================================
# PROJECT: ULTIMATE TELEGRAM CHANNEL AUCTION & STARS MANAGEMENT SYSTEM
# VERSION: 3.5.0 (Production Ready)
# ARCHITECTURE: Modular Monolith with Asynchronous Core (aiogram 3.x)
# ==============================================================================

import asyncio
import logging
import sys
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Union

from aiogram import Bot, Dispatcher, F, types, Router, html
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, BotCommand, BotCommandScopeDefault
)
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

# ==============================================================================
# 1. CONFIGURATION & LOGGING
# ==============================================================================

TOKEN = "8655535261:AAETrrG_B7Q_DxChzSuFhaWZ8jnmmggtW4c"
ADMIN_IDS = [543210123]  # Asosiy admin ID raqamlari
COMMISSION_PERCENT = 1.0   # Har bir stavka/auksiondan admin balansiga tushadigan foiz (%)

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s"
)
logger = logging.getLogger("AuctionBot")

# ==============================================================================
# 2. DATABASE MANAGER (SQLITE ENGINE)
# ==============================================================================

class Database:
    def __init__(self, db_file: str = "bot_database.db"):
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
                    created_at TEXT,
                    ends_at TEXT
                )
            """)
            # Tranzaksiyalar va to'lovlar tarixi
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    tx_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount REAL,
                    type TEXT,
                    status TEXT,
                    created_at TEXT
                )
            """)
            # Do'kon mahsulotlari / Xizmatlar
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS shop_items (
                    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT,
                    price REAL,
                    description TEXT
                )
            """)
            conn.commit()
        logger.info("Ma'lumotlar bazasi muvaffaqiyatli yuklandi va tekshirildi.")

db = Database()

# ==============================================================================
# 3. FSM STATES (STATE MACHINE)
# ==============================================================================

class AuctionStates(StatesGroup):
    waiting_for_lot_name = State()
    waiting_for_lot_desc = State()
    waiting_for_start_price = State()
    waiting_for_min_step = State()
    waiting_for_duration = State()

class ShopStates(StatesGroup):
    waiting_for_item_title = State()
    waiting_for_item_price = State()
    waiting_for_item_desc = State()

class PaymentStates(StatesGroup):
    waiting_for_screenshot = State()
    waiting_for_broadcast_text = State()

# ==============================================================================
# 4. KEYBOARDS (KEYBOARD GENERATORS)
# ==============================================================================

class Keyboards:
    @staticmethod
    def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
        keyboard = [
            [InlineKeyboardButton(text="🔥 Faol Auksionlar", callback_data="list_auctions")],
            [InlineKeyboardButton(text="⭐ Stars / Balans", callback_data="user_balance"),
             InlineKeyboardButton(text="🛍 Do'kon & Xizmatlar", callback_data="shop_menu")],
            [InlineKeyboardButton(text="📞 Yordam va Qo'llanma", callback_data="help_menu")]
        ]
        if is_admin:
            keyboard.append([InlineKeyboardButton(text="👑 Admin Panel", callback_data="admin_panel")])
        return InlineKeyboardMarkup(inline_keyboard=keyboard)

    @staticmethod
    def admin_panel() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📢 Auksion Yaratish", callback_data="admin_create_auction")],
                [InlineKeyboardButton(text="➕ Mahsulot Qo'shish", callback_data="admin_add_shop")],
                [InlineKeyboardButton(text="👥 Foydalanuvchilar Statistika", callback_data="admin_stats")],
                [InlineKeyboardButton(text="✉️ Hammaga Xabar Yuborish", callback_data="admin_broadcast")],
                [InlineKeyboardButton(text="◀️ Bosh Menyu", callback_data="back_home")]
            ]
        )

    @staticmethod
    def auction_bid_keyboard(auction_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="➕ 1 Star", callback_data=f"bid_{auction_id}_1"),
                    InlineKeyboardButton(text="➕ 5 Star", callback_data=f"bid_{auction_id}_5"),
                    InlineKeyboardButton(text="➕ 10 Star", callback_data=f"bid_{auction_id}_10")
                ],
                [
                    InlineKeyboardButton(text="🚀 Maxsus Stavka", callback_data=f"custom_bid_{auction_id}")
                ]
            ]
        )

    @staticmethod
    def back_home() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Orqaga", callback_data="back_home")]]
        )

# ==============================================================================
# 5. ROUTERS & HANDLERS IMPLEMENTATION
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
        "Kanal auksionlari, yulduzlar (Stars) va xizmatlar boshqaruv botiga xush kelibsiz.\n"
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

# --- USER BALANCE & SHOP ---
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

@router.callback_query(F.data == "shop_menu")
async def process_shop_menu(callback: CallbackQuery):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM shop_items")
        items = cursor.fetchall()

    if not items:
        text = "🛍 <b>Do'konimizda hozircha mahsulotlar mavjud emas.</b>"
        kb = Keyboards.back_home()
    else:
        text = "🛍 <b>Mavjud mahsulotlar va xizmatlar ro'yxati:</b>\n\n"
        keyboard = []
        for item in items:
            text += f"▪️ <b>{item['title']}</b> - {item['price']} so'm\n   {item['description']}\n\n"
            keyboard.append([InlineKeyboardButton(text=f"Sotib olish: {item['title']}", callback_data=f"buy_item_{item['item_id']}")])
        keyboard.append([InlineKeyboardButton(text="◀️ Orqaga", callback_data="back_home")])
        kb = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "help_menu")
async def process_help_menu(callback: CallbackQuery):
    text = (
        "📞 <b>Yordam va Yo'riqnoma:</b>\n\n"
        "1. Bot kanallarda qiziqarli lotlar bo'yicha auksionlar o'tkazadi.\n"
        "2. Stavka qilinganda belgilangan foiz komissiya admin hisobiga yo'naltiriladi.\n"
        "3. Savollar bo'yicha adminga murojaat qiling."
    )
    await callback.message.edit_text(text, reply_markup=Keyboards.back_home(), parse_mode="HTML")
    await callback.answer()

# --- ADMIN PANEL & AUCTION CREATION ---
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

    text = (
        f"📊 <b>Bot Statistikasi:</b>\n\n"
        f"👥 Foydalanuvchilar soni: <b>{users_count} ta</b>\n"
        f"🔥 Faol auksionlar: <b>{active_auctions_count} ta</b>"
    )
    await callback.message.edit_text(text, reply_markup=Keyboards.admin_panel(), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "admin_create_auction")
async def process_start_create_auction(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("❌ Ruxsat yo'q!", show_alert=True)
    
    await callback.message.answer("📦 Yangi auksion lotining nomini kiriting:")
    await state.set_state(AuctionStates.waiting_for_lot_name)
    await callback.answer()

@router.message(AuctionStates.waiting_for_lot_name)
async def process_lot_name(message: Message, state: FSMContext):
    await state.update_data(lot_name=message.text)
    await message.answer("📝 Lot haqida qisqacha tavsif (description) kiriting:")
    await state.set_state(AuctionStates.waiting_for_lot_desc)

@router.message(AuctionStates.waiting_for_lot_desc)
async def process_lot_desc(message: Message, state: FSMContext):
    await state.update_data(lot_desc=message.text)
    await message.answer("💰 Boshlang'ich narxni kiriting (raqamda, masalan: 10):")
    await state.set_state(AuctionStates.waiting_for_start_price)

@router.message(AuctionStates.waiting_for_start_price)
async def process_start_price(message: Message, state: FSMContext):
    try:
        price = float(message.text)
    except ValueError:
        return await message.answer("⚠️ Iltimos, to'g'ri raqam kiriting!")
    
    await state.update_data(start_price=price)
    
    # Ma'lumotlarni bazaga yozish va yakunlash
    data = await state.get_data()
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO auctions (channel_id, message_id, lot_name, lot_description, current_price, min_step, current_leader_id, current_leader_name, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            message.chat.id, 0, data['lot_name'], data['lot_desc'], data['start_price'], 1.0,
            0, "Hozircha yo'q", "active", datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        conn.commit()
        auction_id = cursor.lastrowid

    text = (
        f"🔥 <b>YANGI AUKSION E'LON QILINDI!</b>\n\n"
        f"📦 Lot: <b>{data['lot_name']}</b>\n"
        f"📄 Tavsif: {data['lot_desc']}\n"
        f"💰 Boshlang'ich narx: <b>{data['start_price']} Stars</b>\n"
        f"👤 Etakchi: Hozircha yo'q\n\n"
        f"Pastdagi tugmalar orqali stavka qiling!"
    )
    
    sent_msg = await message.answer(text, reply_markup=Keyboards.auction_bid_keyboard(auction_id), parse_mode="HTML")
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE auctions SET message_id = ? WHERE auction_id = ?", (sent_msg.message_id, auction_id))
        conn.commit()

    await state.clear()
    await message.answer("✅ Auksion muvaffaqiyatli ishga tushirildi!", reply_markup=Keyboards.admin_panel())

# --- BID PROCESSING & COMMISSION LOGIC ---
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
            return await callback.answer("❌ Bu auksion allaqachon yakunlangan yoki topilmadi!", show_alert=True)
        
        new_price = auction["current_price"] + increment
        user_name = callback.from_user.full_name
        user_id = callback.from_user.id
        
        # 1% komissiya hisoblash va admin balansiga yo'naltirish mantiqi
        admin_commission = new_price * (COMMISSION_PERCENT / 100.0)
        logger.info(f"Stavka qilindi: {increment} Stars. Komissiya ({COMMISSION_PERCENT}%): {admin_commission}")
        
        cursor.execute("""
            UPDATE auctions 
            SET current_price = ?, current_leader_id = ?, current_leader_name = ?
            WHERE auction_id = ?
        """, (new_price, user_id, user_name, auction_id))
        conn.commit()

    updated_text = (
        f"🔥 <b>YANGI AUKSION!</b>\n\n"
        f"📦 Lot: <b>{auction['lot_name']}</b>\n"
        f"📄 Tavsif: {auction['lot_description']}\n"
        f"💰 Joriy narx: <b>{new_price} Stars</b>\n"
        f"👤 Etakchi: <b>{user_name}</b>\n\n"
        f"Oxirgi stavka qo'shildi (+{increment} Stars). Komissiya muvaffaqiyatli ajratildi."
    )
    
    try:
        await callback.message.edit_text(updated_text, reply_markup=Keyboards.auction_bid_keyboard(auction_id), parse_mode="HTML")
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
        text = "🔥 <b>Barcha faol auksionlar:</b>\n\n"
        keyboard = []
        for auc in auctions:
            text += f"▪️ <b>{auc['lot_name']}</b> - Narx: {auc['current_price']} Stars (Etakchi: {auc['current_leader_name']})\n"
            keyboard.append([InlineKeyboardButton(text=f"Lot: {auc['lot_name']}", callback_data=f"view_auc_{auc['auction_id']}")])
        keyboard.append([InlineKeyboardButton(text="◀️ Orqaga", callback_data="back_home")])
        kb = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

# ==============================================================================
# 6. MAIN ENGINE INITIALIZATION & POLLING
# ==============================================================================

async def main():
    bot = Bot(token=TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    
    dp.include_router(router)
    
    # Bot buyruqlarini sozlash
    await bot.set_my_commands([
        BotCommand(command="start", description="Botni qayta ishga tushirish / Bosh menyu")
    ], scope=BotCommandScopeDefault())

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("To'liq mukammal Auksion boti ishga tushdi va xizmat ko'rsatishga tayyor!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot to'xtatildi.")
