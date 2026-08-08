import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import asyncio

TOKEN = "8655535261:AAETrrG_B7Q_DxChzSuFhaWZ8jnmmggtW4c"
ADMIN_ID = 8694110588
BOT_USERNAME = "YourBotUsername"  # Bot usernamingizni yozing

BID_ADMIN_PERCENT = 0.01  # Har bir stavkadan sizga keladigan 1%

bot = Bot(token=TOKEN)
dp = Dispatcher()

auctions = {}       # auction_id: {data}
user_balances = {}  # user_id: balance
all_users = set()   # foydalanuvchilar

class AuctionStates(StatesGroup):
    waiting_for_channel = State()
    waiting_for_item = State()
    waiting_for_price = State()

class AdminStates(StatesGroup):
    waiting_for_broadcast = State()

# --- Menular ---
def get_main_menu(user_id):
    keyboard = [
        [InlineKeyboardButton(text="📢 Auksion ochish", callback_data="start_auction")],
        [InlineKeyboardButton(text="💳 Mening balansim", callback_data="my_balance")],
        [InlineKeyboardButton(text="⚙️ Yo'riqnoma", callback_data="help_info")]
    ]
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton(text="👑 Admin Panel", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Statistika", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📢 Xabar yuborish", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🔙 Bosh menyu", callback_data="main_menu")]
    ])

def get_channel_auction_keyboard(auction_id, current_price):
    buttons = [
        [
            InlineKeyboardButton(text=f"{current_price + 1} ⭐", callback_data=f"bid_{auction_id}_{current_price + 1}"),
            InlineKeyboardButton(text=f"{current_price + 2} ⭐", callback_data=f"bid_{auction_id}_{current_price + 2}"),
            InlineKeyboardButton(text=f"{current_price + 3} ⭐", callback_data=f"bid_{auction_id}_{current_price + 3}"),
            InlineKeyboardButton(text=f"{current_price + 4} ⭐", callback_data=f"bid_{auction_id}_{current_price + 4}"),
            InlineKeyboardButton(text=f"{current_price + 5} ⭐", callback_data=f"bid_{auction_id}_{current_price + 5}")
        ],
        [
            InlineKeyboardButton(text=f"{current_price + 6} ⭐", callback_data=f"bid_{auction_id}_{current_price + 6}"),
            InlineKeyboardButton(text=f"{current_price + 7} ⭐", callback_data=f"bid_{auction_id}_{current_price + 7}"),
            InlineKeyboardButton(text=f"{current_price + 8} ⭐", callback_data=f"bid_{auction_id}_{current_price + 8}"),
            InlineKeyboardButton(text=f"{current_price + 9} ⭐", callback_data=f"bid_{auction_id}_{current_price + 9}"),
            InlineKeyboardButton(text=f"{current_price + 10} ⭐", callback_data=f"bid_{auction_id}_{current_price + 10}")
        ],
        [InlineKeyboardButton(text="💳 Mening balansim", callback_data="my_balance")],
        [InlineKeyboardButton(text="🤖 Botga Kirish", url=f"https://t.me/{BOT_USERNAME}")],
        [InlineKeyboardButton(text="⏹ Auksionni to'xtatish", callback_data=f"stop_{auction_id}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- Start ---
@dp.message(Command("start"))
async def start(message: types.Message):
    all_users.add(message.from_user.id)
    text = (
        "🤖 **Auksion Botiga xush kelibsiz!**\n\n"
        "• Kanallaringizda skrinshotdagidek qulay auksionlar o'tkazing.\n"
        "• Har bir qilingan stavkadan **1% avtomatik sizning profilingizga** tushadi, qolgani bankka yig'iladi."
    )
    await message.answer(text, reply_markup=get_main_menu(message.from_user.id), parse_mode="Markdown")

@dp.callback_query(F.data == "main_menu")
async def callback_main_menu(callback: types.CallbackQuery):
    await callback.message.edit_text("🏠 **Bosh menyu:**", reply_markup=get_main_menu(callback.from_user.id), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "help_info")
async def callback_help(callback: types.CallbackQuery):
    text = (
        "ℹ️ **Qo'llanma:**\n\n"
        "1. Botni kanalingizga **Admin** qiling.\n"
        "2. 'Auksion ochish' tugmasini bosib kanal havolasini yuboring.\n"
        "3. Sovrin yoki NFT havolasini yozing.\n"
        "4. Boshlang'ich narxni belgilang.\n"
        "5. Har bir stavkadan 1% sizga keladi, auksion tugaganda esa qolgan bank kanal egasiga o'tadi."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Orqaga", callback_data="main_menu")]])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "my_balance")
async def callback_balance(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    bal = user_balances.get(user_id, 0)
    
    if callback.message.chat.type != "private":
        await callback.answer(f"Sizning balansingiz: {bal} Stars", show_alert=True)
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Orqaga", callback_data="main_menu")]])
        text = f"💳 **Sizning Balansingiz**\n\n👤 ID: `{user_id}`\n⭐ Yig'ilgan starslar: **{bal} Stars**"
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        await callback.answer()

# --- Admin Panel ---
@dp.callback_query(F.data == "admin_panel")
async def callback_admin_panel(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Bu bo'lim faqat admin uchun!", show_alert=True)
        return
    await callback.message.edit_text("👑 **Admin Paneli:**", reply_markup=get_admin_menu(), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "admin_stats")
async def callback_admin_stats(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    text = (
        f"📊 **Statistika:**\n\n"
        f"👥 Foydalanuvchilar: {len(all_users)} ta\n"
        f"🏛 Faol auksionlar: {len(auctions)} ta\n"
        f"⚙️ Har bir stavkadan tushadigan ulushingiz: {int(BID_ADMIN_PERCENT * 100)}%"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin_panel")]])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "admin_broadcast")
async def callback_admin_broadcast(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.message.answer("📢 Barcha foydalanuvchilarga yubormoqchi bo'lgan xabaringizni yuboring:")
    await state.set_state(AdminStates.waiting_for_broadcast)
    await callback.answer()

@dp.message(AdminStates.waiting_for_broadcast)
async def process_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return
    success = 0
    for uid in all_users:
        try:
            await message.send_copy(chat_id=uid)
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await message.answer(f"✅ Xabar {success} ta foydalanuvchiga yuborildi!")
    await state.clear()

# --- Auksion Ochish ---
@dp.callback_query(F.data == "start_auction")
async def callback_start_auction(callback: types.CallbackQuery, state: FSMContext):
    all_users.add(callback.from_user.id)
    await callback.message.answer("📢 Auksion o'tkaziladigan kanalingiz havolasini yuboring (masalan: `@kanal_nomi`):")
    await state.set_state(AuctionStates.waiting_for_channel)
    await callback.answer()

@dp.message(AuctionStates.waiting_for_channel)
async def process_channel(message: types.Message, state: FSMContext):
    await state.update_data(channel=message.text)
    await message.answer("🎁 Sovrin nomini yoki **NFT havolasini** yuboring:")
    await state.set_state(AuctionStates.waiting_for_item)

@dp.message(AuctionStates.waiting_for_item)
async def process_item(message: types.Message, state: FSMContext):
    await state.update_data(item=message.text)
    await message.answer("💰 Boshlang'ich narxni kiriting (faqat raqam, masalan: 1):")
    await state.set_state(AuctionStates.waiting_for_price)

@dp.message(AuctionStates.waiting_for_price)
async def process_price(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Iltimos, faqat raqam kiriting!")
        return
        
    data = await state.get_data()
    channel = data['channel']
    item = data['item']
    price = int(message.text)
    
    text = (
        f"👤 **{message.from_user.full_name}** auksionni **{price}** ⭐ bilan boshladi!\n\n"
        f"👨‍💻 **Auksion**\n\n"
        f"⚜️ Holati: Boshlangan\n"
        f"🎁 Sovrin / NFT: {item}\n"
        f"💰 Auksion banki: {price} ⭐\n"
        f"⛏ Garovlar soni: 0 ta\n\n"
        f"👑 Lider: Hozircha yo'q\n\n"
        f"👇 Garovni oshirish uchun miqdorni tanlang:"
    )
    
    try:
        sent_msg = await bot.send_message(
            chat_id=channel,
            text=text,
            reply_markup=get_channel_auction_keyboard(0, price),
            parse_mode="Markdown"
        )
        
        real_auction_id = sent_msg.message_id
        
        await bot.edit_message_reply_markup(
            chat_id=channel,
            message_id=real_auction_id,
            reply_markup=get_channel_auction_keyboard(real_auction_id, price)
        )
        
        auctions[real_auction_id] = {
            'channel': channel,
            'item': item,
            'current_price': price,
            'owner_id': message.from_user.id,
            'leader_id': None,
            'leader_name': "Hozircha yo'q",
            'total_bank': price,
            'bids_count': 0,
            'message_id': real_auction_id
        }
        
        await message.answer("✅ Auksion kanalda muvaffaqiyatli e'lon qilindi!")
    except Exception as e:
        await message.answer(f"❌ Xatolik yuz berdi: {e}\n\nBot kanalda Admin ekanligini va havolasi to'g'riligini tekshiring!")
        
    await state.clear()

# --- Stavka qilish (Har bir stavkadan 1% sizga keladi) ---
@dp.callback_query(F.data.startswith("bid_"))
async def handle_bid(callback: types.CallbackQuery):
    _, auction_id, amount = callback.data.split("_")
    auction_id = int(auction_id)
    amount = int(amount)
    
    if auction_id not in auctions:
        await callback.answer("Bu auksion topilmadi yoki yakunlangan!", show_alert=True)
        return
        
    auction = auctions[auction_id]
    if amount <= auction['current_price']:
        await callback.answer("Stavka joriy narxdan baland bo'lishi kerak!", show_alert=True)
        return

    # Telegram Invoice orqali to'lov oynasi
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="Auksionga stavka qilish",
        description=f"Sovrin: {auction['item']} uchun {amount} ⭐ stavka",
        payload=f"auction_{auction_id}_{amount}",
        currency="XTR",
        prices=[LabeledPrice(label="Stars Garov", amount=amount)]
    )
    await callback.answer()

@dp.pre_checkout_query()
async def on_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)

@dp.message(F.successful_payment)
async def on_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload
    if not payload.startswith("auction_"):
        return
        
    _, auction_id, amount = payload.split("_")
    auction_id = int(auction_id)
    amount = int(amount)
    
    if auction_id not in auctions:
        await message.answer("Auksion allaqachon yakunlangan.")
        return
        
    auction = auctions[auction_id]
    
    # Har bir stavka miqdoridan 1% darhol sizning profilingizga (ADMIN_ID ga) tushadi
    admin_cut = int(amount * BID_ADMIN_PERCENT)
    if admin_cut < 1 and amount > 0:
        admin_cut = 1
        
    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=f"💰 Stavka qilingandan 1% ulush tushdi!\nMiqdor: {admin_cut} ⭐ (Stavka: {amount} ⭐)"
        )
    except Exception:
        pass
        
    auction['current_price'] = amount
    auction['leader_id'] = message.from_user.id
    auction['leader_name'] = message.from_user.full_name
    auction['total_bank'] += amount
    auction['bids_count'] += 1
    
    # Kanal ichidagi postni yangilash
    updated_text = (
        f"👤 **{auction['leader_name']}** auksionni **{amount}** ⭐ ga ko'tardi!\n\n"
        f"👨‍💻 **Auksion**\n\n"
        f"⚜️ Holati: Boshlangan\n"
        f"🎁 Sovrin / NFT: {auction['item']}\n"
        f"💰 Auksion banki: {auction['total_bank']} ⭐\n"
        f"⛏ Garovlar soni: {auction['bids_count']} ta\n\n"
        f"👑 Lider: {auction['leader_name']}! Tikdi {amount} ⭐!\n\n"
        f"👇 Garovni oshirish uchun miqdorni tanlang:"
    )
    
    try:
        await bot.edit_message_text(
            chat_id=auction['channel'],
            message_id=auction['message_id'],
            text=updated_text,
            reply_markup=get_channel_auction_keyboard(auction_id, amount),
            parse_mode="Markdown"
        )
    except Exception:
        pass
        
    await message.answer(f"✅ Stavka qabul qilindi! Siz auksion liderisiz.")

# --- Auksionni to'xtatish va qolgan bankni kanal egasiga berish ---
@dp.callback_query(F.data.startswith("stop_"))
async def stop_auction(callback: types.CallbackQuery):
    _, auction_id = callback.data.split("_")
    auction_id = int(auction_id)
    
    if auction_id not in auctions:
        await callback.answer("Bu auksion allaqachon yakunlangan!", show_alert=True)
        return
        
    auction = auctions[auction_id]
    
    if callback.from_user.id != auction['owner_id']:
        await callback.answer("Bu auksionni faqat uni ochgan kishi to'xtata oladi!", show_alert=True)
        return
        
    total_bank = auction['total_bank']
    leader_name = auction['leader_name']
    leader_id = auction['leader_id']
    item = auction['item']
    owner_id = auction['owner_id']
    
    # Auksion tugaganda jami to'plangan bank to'liq kanal egasining balansiga o'tadi
    user_balances[owner_id] = user_balances.get(owner_id, 0) + total_bank
    
    text = (
        f"🛑 **Auksion Yakunlandi!**\n\n"
        f"🎁 Sovrin / NFT: {item}\n"
        f"💰 Jami bank: {total_bank} ⭐\n"
        f"👑 G'olib: {leader_name}\n"
        f"💵 Kanal egasi balansiga o'tdi: {total_bank} ⭐"
    )
    
    try:
        await bot.edit_message_text(
            chat_id=auction['channel'],
            message_id=auction['message_id'],
            text=text,
            reply_markup=None,
            parse_mode="Markdown"
        )
    except Exception:
        pass
        
    if leader_id:
        try:
            await bot.send_message(leader_id, f"🎉 Tabriklaymiz! Siz auksionda g'olib bo'ldingiz! Sovrin/NFT: {item}")
        except Exception:
            pass
            
    del auctions[auction_id]
    await callback.answer("Auksion muvaffaqiyatli yakunlandi!")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
