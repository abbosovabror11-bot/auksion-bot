# ==============================================================================
# PROJECT: TELEGRAM NFT & STARS AUCTION SYSTEM (DYNAMIC BUTTONS)
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
COMMISSION_PERCENT = 5.0   # Auksion tugaganda admin profiliga tushadigan foiz (%)

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
                    joined_date TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS channels (
                    channel_id INTEGER PRIMARY KEY,
                    owner_id INTEGER,
                    channel_title TEXT,
                    username TEXT,
                    total_bids_count INTEGER DEFAULT 0,
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
                CREATE TABLE IF NOT EXISTS admin_wallet (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    earned_stars REAL DEFAULT 0.0
                )
            """)
            cursor.execute("INSERT OR IGNORE INTO admin_wallet (id, earned_stars) VALUES (1, 0.0)")
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
            [InlineKeyboardButton(text="⭐ Kabinetim", callback_data="user_balance"),
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
                [InlineKeyboardButton(text="📊 To'liq Statistika", callback_data="admin_stats")],
                [InlineKeyboardButton(text="🛠️ Auksionlarni Yakunlash / Boshqarish", callback_data="admin_manage_auctions")],
                [InlineKeyboardButton(text="✉️ Hammaga Xabar Yuborish", callback_data="admin_broadcast")],
                [InlineKeyboardButton(text="◀️ Bosh Menyu", callback_data="back_home")]
            ]
        )

    @staticmethod
    def auction_bid_keyboard(auction_id: int, current_price: float) -> InlineKeyboardMarkup:
        # Rasmda ko'rsatilgandek, joriy narxdan boshlab ketma-ket 10 ta tugma avtomatik hosil bo'ladi
        p = int(current_price)
        buttons = []
        row = []
        
        for i in range(1, 11):
            next_price = p + i
            row.append(InlineKeyboardButton(text=f"{next_price} ⭐", callback_data=f"paybid_{auction_id}_{next_price}"))
            if len(row) == 5:  # Bir qatorga 5 tadan tugma joylashadi
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
                "INSERT INTO users (user_id, username, full_name, joined_date) VALUES (?, ?, ?, ?)",
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
    text = (
        f"👤 <b>Shaxsiy kabinet:</b>\n\n"
        f"🆔 ID: <code>{callback.from_user.id}</code>\n"
        f"⭐ To'lovlar Telegram Stars orqali bevosita amalga oshiriladi."
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
        text = "🏆 <b>Top-10 Kanallar:</b>\n\n"
        for idx, ch in enumerate(channels, 1):
            title = html.quote(ch['channel_title'] or "Kanal")
            text += f"{idx}. <b>{title}</b> — ⭐ {ch['total_stars_generated']} Stars\n"

    await callback.message.edit_text(text, reply_markup=Keyboards.back_home(), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "help_menu")
async def process_help_menu(callback: CallbackQuery):
    text = (
        "📞 <b>Qo'llanma:</b>\n\n"
        "1. Botni kanalingizga admin qiling.\n"
        "2. Auksion ochish tugmasini bosing, sovrin nomi va NFT havolasini kiriting.\n"
        "3. Ishtirokchilar Telegram Stars orqali to'lov qilib stavka qo'shadilar."
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
            ON CONFLICT(channel_id) DO UPDATE SET channel_title = excluded.channel_title
        """, (data["channel_id"], user_id, data["channel_title"], data["channel_username"]))
        
        cursor.execute("""
            INSERT INTO auctions (creator_id, channel_id, message_id, prize_name, nft_link, current_price, current_leader_id, current_leader_name, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, data["channel_id"], 0, prize_name, nft_link, start_price,
            0, "Hozircha yo'q", "active", datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        conn.commit()
        auction_id = cursor.lastrowid

    nft_line = f"🔗 NFT havola: <a href='{nft_link}'>Ko'rish</a>\n" if nft_link != "Mavjud emas" else ""

    auction_text = (
        f"🔥 <b>YANGI AUKSION BOSHLANDI!</b>\n\n"
        f"🎁 Sovrin: <b>{prize_name}</b>\n"
        f"{nft_line}"
        f"💰 Boshlang'ich narx: <b>{start_price} Stars</b>\n"
        f"👤 Etakchi: Hozircha yo'q\n\n"
        f"Stavka qilish uchun pastdagi tugmalardan birini bosing:"
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

# --- TELEGRAM STARS INVOICE PAYMENT FOR BIDS ---
@router.callback_query(F.data.startswith("paybid_"))
async def process_pay_bid(callback: CallbackQuery):
    parts = callback.data.split("_")
    auction_id = int(parts[1])
    stars_amount = int(parts[2])
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM auctions WHERE auction_id = ?", (auction_id,))
        auction = cursor.fetchone()
        
        if not auction:
            return await callback.answer("❌ Bu auksion bazadan topilmadi!", show_alert=True)
        
        if auction["status"] != "active":
            return await callback.answer("❌ Bu auksion yopilgan!", show_alert=True)
            
        if stars_amount <= auction["current_price"]:
            return await callback.answer("❌ Tanlangan stavka joriy narxdan baland bo'lishi kerak!", show_alert=True)

    title = f"Auksion stavkasi: {auction['prize_name']}"
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
    user_name = message.from_user.full_name
    user_id = message.from_user.id

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM auctions WHERE auction_id = ? AND status = 'active'", (auction_id,))
        auction = cursor.fetchone()
        
        if not auction:
            return await message.answer("❌ Bu auksion allaqachon tugatilgan.")
        
        new_price = float(stars_amount)
        
        cursor.execute("""
            UPDATE auctions 
            SET current_price = ?, current_leader_id = ?, current_leader_name = ?
            WHERE auction_id = ?
        """, (new_price, user_id, user_name, auction_id))
        
        cursor.execute("""
            UPDATE channels 
            SET total_bids_count = total_bids_count + 1, total_stars_generated = total_stars_generated + ?
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

    try:
        await message.bot.edit_message_text(
            chat_id=auction["channel_id"],
            message_id=auction["message_id"],
            text=updated_text,
            reply_markup=Keyboards.auction_bid_keyboard(auction_id, new_price),
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    except Exception:
        pass

    await message.answer(f"✅ Muvaffaqiyatli {stars_amount} Stars stavka qilindi!")

@router.callback_query(F.data == "list_auctions")
async def process_list_active_auctions(callback: CallbackQuery):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM auctions WHERE status = 'active'")
        auctions = cursor.fetchall()

    if not auctions:
        text = "📭 Hozirda faol auksionlar mavjud emas."
    else:
        text = "🔥 <b>Faol auksionlar:</b>\n\n"
        for auc in auctions:
            text += f"▪️ <b>{auc['prize_name']}</b> — Narx: {auc['current_price']} Stars (Etakchi: {auc['current_leader_name']})\n"

    await callback.message.edit_text(text, reply_markup=Keyboards.back_home(), parse_mode="HTML")
    await callback.answer()

# --- ADMIN PANEL & 5% COMMISSION SYSTEM ---
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
        cursor.execute("SELECT earned_stars FROM admin_wallet WHERE id = 1")
        admin_stars = cursor.fetchone()["earned_stars"]

    text = (
        f"📊 <b>Statistika:</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{users_count} ta</b>\n"
        f"👑 Komissiyadan (5%) tushgan Stars: <b>⭐ {admin_stars} ta</b>"
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
        text = "🛠️ <b>Auksionni tugatish va 5% komissiyani olish:</b>"
        buttons = []
        for auc in auctions:
            buttons.append([InlineKeyboardButton(text=f"🏁 Yakunlash: {auc['prize_name']} ({auc['current_price']} Stars)", callback_data=f"finish_auc_{auc['auction_id']}")])
        buttons.append([InlineKeyboardButton(text="◀️ Orqaga", callback_data="admin_panel")])
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("finish_auc_"))
async def process_finish_auction(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("❌ Ruxsat yo'q!", show_alert=True)
    
    auction_id = int(callback.data.split("_")[2])
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM auctions WHERE auction_id = ? AND status = 'active'", (auction_id,))
        auction = cursor.fetchone()
        
        if not auction:
            return await callback.answer("❌ Auksion topilmadi yoki allaqachon tugatilgan!", show_alert=True)
        
        total_price = auction["current_price"]
        admin_commission = total_price * (COMMISSION_PERCENT / 100.0)
        
        cursor.execute("UPDATE admin_wallet SET earned_stars = earned_stars + ? WHERE id = 1", (admin_commission,))
        cursor.execute("UPDATE auctions SET status = 'finished' WHERE auction_id = ?", (auction_id,))
        conn.commit()

    try:
        await callback.message.bot.send_message(
            chat_id=ADMIN_IDS[0],
            text=f"🏁 <b>Auksion yakunlandi!</b>\n"
                 f"🎁 Sovrin: {auction['prize_name']}\n"
                 f"💰 Jami summa: {total_price} Stars\n"
                 f"👑 Sizning 5% ulushingiz: <b>⭐ {admin_commission} Stars</b> yozildi.",
            parse_mode="HTML"
        )
    except Exception:
        pass

    await callback.answer("✅ Auksion muvaffaqiyatli yakunlandi va 5% komissiya olindi!", show_alert=True)
    await process_admin_manage_auctions(callback)

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
