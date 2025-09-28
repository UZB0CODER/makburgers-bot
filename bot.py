import os
import logging
import json
import asyncio
import aiohttp
from quart import Quart, request, jsonify
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from dotenv import load_dotenv

# .env faylini yuklaymiz (faqat lokal ishlatish uchun)
load_dotenv()

# --- 1. SOZLAMALAR VA GLOBAL O'ZGARUVCHILAR ---
# BOT_TOKEN ni faqat os.getenv orqali olish tavsiya etiladi (xavfsizlik uchun)
TOKEN = os.getenv('BOT_TOKEN')
# ADMIN_ID ni int ga o'tkazish
ADMIN_ID = int(os.getenv('ADMIN_ID', 0)) 
WEB_HOST = os.getenv('WEB_HOST')

# Quart app instance
app = Quart(__name__)

# Webhook manzilini yaratish
# WEBHOOK_PATH Telegram PTB so'rovlarni qabul qilish uchun kerak
WEBHOOK_PATH = f"/{TOKEN}" 
WEBHOOK_URL = f"{WEB_HOST}{WEBHOOK_PATH}"

# Basic logging setup
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Global variables
application = None 
user_orders = {}
USER_DATA_FILE = "user_data_cache.json" # Foydalanuvchi ma'lumotlarini saqlash uchun fayl

# -----------------
# 1. BOT SOZLAMALARI
# -----------------

MENU = {
    "🍔 Fast Food": {
        "item_h": ("Hotdog", 15000), 
        "item_l": ("Lavash", 25000), 
        "item_b": ("Burger", 30000)
    },
    "🥤 Ichimliklar": {
        "item_p": ("Pepsi", 8000), 
        "item_c": ("Cola", 8000), 
        "item_f": ("Fanta", 8000)
    },
    "🍰 Desertlar": {
        "item_ch": ("Cheesecake", 22000), 
        "item_t": ("Tort", 35000), 
        "item_d": ("Donut", 12000)
    }
}

ALL_ITEMS = {}
for category_name, items in MENU.items():
    for item_id, (name, price) in items.items():
        ALL_ITEMS[item_id] = (name, price, category_name)


# Foydalanuvchi ma'lumotlari: {user_id: {"phone": "+998xxxxxxxxx", "username": "..."}}
user_data = {}


# -----------------
# 2. YORDAMCHI FUNKSIYALAR
# -----------------

def get_order_summary(user_id: int) -> tuple[str, int]:
    """Buyurtma ro'yxatini va umumiy summani hisoblaydi."""
    orders = user_orders.get(user_id, {})
    active_orders = {item_id: count for item_id, count in orders.items() if count > 0}
    
    if not active_orders:
        return "🛒 Siz hali buyurtma qo‘shmagansiz.", 0

    summary_text = "📦 Sizning buyurtmangiz:\n"
    total_price = 0
    
    for item_id, count in active_orders.items():
        name, price, _ = ALL_ITEMS.get(item_id, ("Noma'lum", 0, ""))
        
        item_total = count * price
        total_price += item_total
        summary_text += f"    - {name} ({count}x) = {item_total:,} so'm\n".replace(",", " ")
        
    summary_text += f"\n💰 Jami: {total_price:,} so'm".replace(",", " ")
    return summary_text, total_price

def load_users_from_file():
    """Foydalanuvchi ma'lumotlarini JSON fayldan yuklaydi (Volume storage/Ephemeral diskda saqlash)."""
    try:
        if not os.path.exists(USER_DATA_FILE):
             return {}
        with open(USER_DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Kalitlarni int ga o'tkazish
            return {int(k): v for k, v in data.items()}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception as e:
        logger.error(f"Foydalanuvchi ma'lumotlarini yuklashda xato: {e}")
        return {}

def save_users_to_file():
    """Foydalanuvchi ma'lumotlarini JSON faylga saqlaydi."""
    try:
        with open(USER_DATA_FILE, 'w', encoding='utf-8') as f:
            # Kalitlarni str ga o'tkazish
            json.dump(user_data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Faylga saqlashda xato: {e}")

# -----------------
# 3. HANDLER FUNKSIYALARI
# -----------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Boshlang'ich /start buyrug'ini bajaradi va raqam so'raydi."""
    user_id = update.effective_user.id
    
    is_registered = user_id in user_data and "phone" in user_data[user_id]
    
    # 2. Agar foydalanuvchi ro'yxatdan o'tgan bo'lsa, to'g'ridan-to'g'ri menyuni ko'rsatish
    if is_registered:
        if user_id not in user_orders:
            user_orders[user_id] = {}
            
        await update.message.reply_text(f"👋 Xush kelibsiz, {user_data[user_id]['username']}! Buyurtma berishni davom ettirishingiz mumkin.")
        await show_main_menu(update, context)
        return
        
    # 3. Ro'yxatdan o'tishni so'rash
    button = [[KeyboardButton("📱 Raqamni yuborish", request_contact=True)]]
    markup = ReplyKeyboardMarkup(button, resize_keyboard=True, one_time_keyboard=True)
    
    await update.message.reply_text(
        "Ro‘yxatdan o‘tish va buyurtma berish uchun iltimos telefon raqamingizni yuboring:", 
        reply_markup=markup
    )

async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchi kontakt raqamini qabul qiladi va saqlaydi."""
    contact = update.message.contact
    phone = contact.phone_number.strip()
    user_id = update.effective_user.id

    # O'zbekiston raqamlarini tekshirish (faqat 998 bilan boshlanishi shart emas)
    # Ammo formatni saqlaymiz.
    if len(phone) >= 9: 
        final_phone = phone
        if not final_phone.startswith('+'):
             final_phone = '+' + final_phone
        
        # Ma'lumotlarni saqlash
        user_data[user_id] = {
            "phone": final_phone,
            "username": update.effective_user.full_name,
            "id": user_id
        }
        user_orders[user_id] = {}
        
        # Ma'lumotlarni doimiy saqlash
        save_users_to_file() 

        await update.message.reply_text("✅ Ro‘yxatdan o‘tish muvaffaqiyatli! Endi menyudan tanlang.")
        await show_main_menu(update, context)
    else:
        await update.message.reply_text("❌ Raqam noto‘g‘ri formatda. Iltimos, raqamni qayta yuboring.")

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asosiy menyuni ko'rsatish."""
    buttons = [
        ["🛍 Buyurtma berish", "🛒 Savatcha"],
        ["📝 Fikr bildirish", "⚙️ Sozlamalar"]
    ]
    markup = ReplyKeyboardMarkup(buttons, resize_keyboard=True)
    
    text = "Asosiy menyu. Kerakli bo'limni tanlang:"
    
    message = update.message or update.callback_query.message
    await message.reply_text(text, reply_markup=markup)


async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mahsulot kategoriyalarini ko'rsatish."""
    query = update.callback_query
    if query:
        await query.answer()

    buttons = [[InlineKeyboardButton(f"🍱 {cat}", callback_data=f"cat:{cat}")] for cat in MENU.keys()]
    buttons.append([InlineKeyboardButton("🛒 Savatcha | Tasdiqlash", callback_data="cart:view")])
    markup = InlineKeyboardMarkup(buttons)
    
    user_id = update.effective_user.id
    summary, _ = get_order_summary(user_id)
    
    text = f"{summary}\n\n---\n\n📋 Menyu kategoriyasini tanlang:"
    
    if query:
        await query.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")


def create_item_buttons(category_name: str, user_id: int):
    """Mahsulotlar ro'yxati, + / - / count tugmalari va Orqaga tugmasini yaratadi."""
    orders = user_orders.get(user_id, {})
    buttons = []
    
    for item_id, (name, price) in MENU[category_name].items():
        count = orders.get(item_id, 0)
        
        row1 = [
            InlineKeyboardButton("➖", callback_data=f"qty_dec:{item_id}"),
            InlineKeyboardButton(f" {count} ", callback_data="ignore"),
            InlineKeyboardButton("➕", callback_data=f"qty_inc:{item_id}")
        ]
        
        row2 = [InlineKeyboardButton(f"{name} - {price:,} so'm".replace(",", " "), callback_data="ignore")]
        
        buttons.extend([row2, row1])

    buttons.append([InlineKeyboardButton("⬅️ Barcha kategoriyalar", callback_data="back:categories")])
    
    return InlineKeyboardMarkup(buttons)


async def category_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kategoriyani tanlash. Mahsulotlar ro'yxatini chiqaradi."""
    query = update.callback_query
    await query.answer()
    category = query.data.split(":")[1]
    user_id = query.from_user.id

    context.user_data['current_category'] = category
    
    summary, _ = get_order_summary(user_id)
    markup = create_item_buttons(category, user_id)
    
    text = f"{summary}\n\n---\n\n**{category}** bo‘limi. Nechta kerakligini tanlang:"
    
    await query.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")


async def quantity_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mahsulot sonini + yoki - tugmasi orqali o'zgartirish."""
    query = update.callback_query
    await query.answer()
    
    action, item_id = query.data.split(":")
    user_id = query.from_user.id
    
    if user_id not in user_orders:
        user_orders[user_id] = {}
        
    current_count = user_orders[user_id].get(item_id, 0)
    
    if action == "qty_inc":
        new_count = current_count + 1
    elif action == "qty_dec":
        new_count = max(0, current_count - 1)
    else:
        return

    user_orders[user_id][item_id] = new_count
    
    category = context.user_data.get('current_category')
    if not category:
        # Agar category topilmasa, kategoriyalar menyusiga qaytaramiz
        await show_categories(update, context) 
        return

    summary, _ = get_order_summary(user_id)
    markup = create_item_buttons(category, user_id)
    
    text = f"{summary}\n\n---\n\n**{category}** bo‘limi. Nechta kerakligini tanlang:"
    
    await query.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")


async def cart_view_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Savatchani ko'rsatish va buyurtmani tasdiqlash tugmalarini chiqarish."""
    query = update.callback_query
    if query:
        await query.answer()
        message = query.message
    else:
        message = update.message
    
    user_id = update.effective_user.id
    summary, total = get_order_summary(user_id)
    
    has_items = total > 0
    
    buttons = []
    if has_items:
        buttons = [
            [InlineKeyboardButton("✅ Tasdiqlash va manzilni tanlash", callback_data="checkout:start")],
            [InlineKeyboardButton("🗑 Savatchani tozalash", callback_data="cart:clear")],
        ]
    
    buttons.append([InlineKeyboardButton("⬅️ Kategoriyalarga qaytish", callback_data="back:categories")])
    markup = InlineKeyboardMarkup(buttons)

    text = f"{summary}\n\n---\n\n🛒 **Savatcha** menyusi. Buyurtmani rasmiylashtirasizmi?"
    
    try:
        if query:
            await message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        else:
            await message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Savatchani ko'rsatishda xato: {e}")


async def cart_clear_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Savatchani tozlash."""
    query = update.callback_query
    await query.answer("Savatcha tozalandi.")
    user_id = query.from_user.id
    
    if user_id in user_orders:
        user_orders[user_id] = {}
        
    await show_categories(update, context)


async def checkout_start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Buyurtmani rasmiylashtirish. Yetkazib berish turini so'raydi."""
    query = update.callback_query
    await query.answer()

    buttons = [
        [InlineKeyboardButton("🚖 Yetkazib berish", callback_data="delivery:yes")],
        [InlineKeyboardButton("🏃 Borib olish", callback_data="delivery:no")]
    ]
    markup = InlineKeyboardMarkup(buttons)

    await query.edit_message_text("Qanday usulni tanlaysiz?", reply_markup=markup)

async def delivery_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Yetkazib berish turini qabul qilish."""
    query = update.callback_query
    await query.answer()
    choice = query.data.split(":")[1]
    
    # Message ni to'g'ri olish
    message = query.message

    context.user_data["delivery_choice"] = choice
    
    if choice == "yes":
        button = [[KeyboardButton("📍 Lokatsiyani yuborish", request_location=True)]]
        markup = ReplyKeyboardMarkup(button, resize_keyboard=True, one_time_keyboard=True)
        
        await message.edit_text("Manzil tanlanmoqda...")
        
        await message.reply_text(
            "📍 Yetkazib berish uchun iltimos, lokatsiyangizni yuboring:", 
            reply_markup=markup
        )
    else:
        await message.edit_text("✅ Buyurtmangiz qabul qilindi! (Borib olish) Sizga tez orada aloqaga chiqamiz.")
        await send_to_admin(update, context, "🏃 Borib olish")
        # Asosiy menyu tugmasini bosgandek ko'rsatamiz
        await show_main_menu(update, context) 

async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lokatsiyani qabul qilish va tasdiqlash."""
    location = update.message.location
    lat, lon = location.latitude, location.longitude

    context.user_data["temp_location"] = (lat, lon)

    text = f"Buyurtma qilmoqchi bo‘lgan manzilingiz:\n\n**Xarita koordinatalari:**\nLat: `{lat}`\nLon: `{lon}`\n\nUshbu manzilni tasdiqlaysizmi? (Kuryerga aniqroq ma'lumot kerak bo'lsa, siz bilan bog‘lanamiz)"
    buttons = [
        [InlineKeyboardButton("✅ Ha, tasdiqlayman", callback_data="confirm:yes")],
        [InlineKeyboardButton("❌ Yo‘q, qaytadan yuborish", callback_data="confirm:no")]
    ]
    markup = InlineKeyboardMarkup(buttons)

    # ReplyKeyboardni yashirish
    remove_markup = ReplyKeyboardMarkup([[""]], resize_keyboard=True, one_time_keyboard=True, selective=True)

    await update.message.reply_location(latitude=lat, longitude=lon, reply_markup=remove_markup)
    await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")

async def confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manzilni tasdiqlash va buyurtmani yakunlash."""
    query = update.callback_query
    await query.answer()
    data = query.data.split(":")

    if data[1] == "yes":
        await query.edit_message_text("✅ Manzil tasdiqlandi. Buyurtmangiz qabul qilindi! Kuryer tez orada aloqaga chiqadi.")
        await send_to_admin(update, context, "🚖 Yetkazib berish (Lokatsiya bilan)")
        await show_main_menu(update, context)
    else:
        # Lokatsiya qayta so'ralganda oldingi xabarni tahrirlash
        await query.edit_message_text("❌ Iltimos, lokatsiyani qaytadan to'g'ri yuboring.")
        button = [[KeyboardButton("📍 Lokatsiyani yuborish", request_location=True)]]
        markup = ReplyKeyboardMarkup(button, resize_keyboard=True, one_time_keyboard=True)
        await query.message.reply_text("📍 Iltimos, lokatsiyangizni yuboring:", reply_markup=markup)

async def send_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, delivery_type: str):
    """Yakuniy buyurtmani adminga yuboradi."""
    user_id = update.effective_user.id
    
    user_info = user_data.get(user_id, {})
    phone = user_info.get("phone", "Raqam topilmadi")
    username = user_info.get("username", update.effective_user.full_name)
    
    summary, total = get_order_summary(user_id)
    
    text = f"""
🚨 **Yangi Buyurtma!** 🚨

👤 **Mijoz:** {username}
📞 **Raqam:** `{phone}`
🆔 **User ID:** `{user_id}`

{summary}

🚚 **Yetkazib berish turi:** {delivery_type}
"""

    try:
        # Adminga buyurtma matnini yuborish
        await context.bot.send_message(chat_id=ADMIN_ID, text=text, parse_mode="Markdown")

        # Agar yetkazib berish bo'lsa, lokatsiyani alohida yuborish
        if delivery_type.startswith("🚖 Yetkazib berish") and "temp_location" in context.user_data:
            lat, lon = context.user_data["temp_location"]
            await context.bot.send_location(chat_id=ADMIN_ID, latitude=lat, longitude=lon)
        
        # Buyurtma yakunlangandan so'ng savatchani tozalash
        user_orders[user_id] = {}
        context.user_data.pop("temp_location", None) # Vaqtincha lokatsiyani o'chirish
        
    except Exception as e:
        logger.error(f"Adminga xabar yuborishda xatolik: {e}")
        # Foydalanuvchiga xato haqida xabar berish
        await update.effective_message.reply_text("⚠️ Uzr, buyurtmani qabul qilishda texnik xatolik yuz berdi. Iltimos, qayta urinib ko'ring.")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Matn xabarlarni qabul qilish (Asosiy menyu tugmalarini ushlash)."""
    text = update.message.text
    user_id = update.effective_user.id
    
    if user_id not in user_data or "phone" not in user_data[user_id]:
        await update.message.reply_text("Iltimos, avval /start buyrug'i orqali ro'yxatdan o'ting.")
        return

    if text == "🛍 Buyurtma berish":
        await show_categories(update, context)
    elif text == "🛒 Savatcha":
        await cart_view_handler(update, context)
    elif text == "📝 Fikr bildirish":
        # Bu yerga fikr bildirish mantig'ini qo'shishingiz kerak
        await update.message.reply_text("Fikr-mulohazalaringizni shu yerga yozing. Biz uni albatta ko'rib chiqamiz!")
    elif text == "⚙️ Sozlamalar":
        await update.message.reply_text("Sozlamalar bo'limi hozircha tayyor emas.")
    else:
        # Fikr bildirish rejimida bo'lmagan oddiy matnga javob
        await update.message.reply_text("Kechirasiz, men sizni tushunmadim. Menyudan tanlang.")

# -----------------
# 4. BOTNI ISHGA TUSHIRISH VA WEBHOOK
# -----------------

def init_handlers(application: Application):
    """Handlerlarni bot ilovasiga qo'shish."""
    application.add_handler(CommandHandler("start", start_command))
        
    application.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    application.add_handler(MessageHandler(filters.LOCATION, location_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    application.add_handler(CallbackQueryHandler(show_categories, pattern="^back:categories"))
    application.add_handler(CallbackQueryHandler(category_handler, pattern="^cat:"))
    application.add_handler(CallbackQueryHandler(quantity_handler, pattern="^qty_(inc|dec):"))
    application.add_handler(CallbackQueryHandler(cart_view_handler, pattern="^cart:view"))
    application.add_handler(CallbackQueryHandler(cart_clear_handler, pattern="^cart:clear"))
    application.add_handler(CallbackQueryHandler(checkout_start_handler, pattern="^checkout:start"))
    application.add_handler(CallbackQueryHandler(delivery_handler, pattern="^delivery:"))
    application.add_handler(CallbackQueryHandler(confirm_handler, pattern="^confirm:"))
    application.add_handler(CallbackQueryHandler(lambda update, context: update.callback_query.answer(), pattern="^ignore"))


@app.post(WEBHOOK_PATH)
async def telegram_webhook_handler():
    """Telegramdan kelgan POST so'rovlarini qabul qilish."""
    global application
    if application:
        # Quart orqali JSON so'rovini olish
        data = await request.get_json()
        
        # So'rovni PTBga yuborish
        update = Update.de_json(data, application.bot)
        
        # Yangilanishni asinxron tarzda qayta ishlash
        await application.process_update(update)
    
    # Telegram har doim '200 OK' javobini kutadi
    return jsonify({"status": "ok"})

@app.route("/", methods=["GET"])
async def home():
    """Tekshirish uchun bosh sahifa."""
    return f"Telegram Bot Ishlamoqda! Webhook URL: {WEBHOOK_URL}"


async def set_webhook_url(application: Application):
    """Webhook URL'sini o'rnatish. Bot ishga tushganda bir marta chaqiriladi."""
    if not WEB_HOST:
        logger.error("WEB_HOST topilmadi. Webhook o'rnatish bekor qilindi.")
        return

    # Webhookni o'rnatish uchun Telegram API'ga murojaat
    try:
        # Bu yerda application.bot.set_webhook() dan foydalanish tavsiya qilinadi
        await application.bot.set_webhook(url=WEBHOOK_URL, allowed_updates=Update.ALL_TYPES)
        logger.info(f"✅ Webhook muvaffaqiyatli o'rnatildi: {WEBHOOK_URL}")

        if ADMIN_ID > 0:
            await application.bot.send_message(
                chat_id=ADMIN_ID, 
                text=f"✅ Bot yangi Webhook manzilida ishga tushdi: {WEB_HOST}"
            )

    except Exception as e:
        logger.error(f"❌ Webhook o'rnatishda PTB xatosi: {e}")

async def startup():
    """Quart ishga tushganda Webhookni o'rnatish."""
    global application
    if application:
        await set_webhook_url(application)


def main() -> Quart:
    """Gunicorn uchun asosiy kirish nuqtasi. Bot Applicationni sozlaydi."""
    global application, user_data
    
    if not TOKEN:
        logger.error("FATAL: BOT_TOKEN o'rnatilmagan! Ilovani ishga tushirish bekor qilindi.")
        return app
        
    # Foydalanuvchi ma'lumotlarini yuklash
    user_data = load_users_from_file()
    logger.info(f"Bot ma'lumotlari yuklandi. Jami foydalanuvchilar: {len(user_data)}")

    # Bot ilovasini yaratish
    application = Application.builder().token(TOKEN).build()
    init_handlers(application)

    if WEB_HOST:
        # WEBHOOK rejimida ishga tushirish (Production uchun)
        logger.info("Bot WEBHOOK rejimida ishga tushmoqda.")
        # Quart serveri ishga tushishidan oldin Webhookni o'rnatish
        app.before_serving(startup)
        return app
    
    # POLLING rejimida ishga tushirish (Lokal rivojlanish uchun)
    logger.info("Bot POLLING rejimida ishga tushmoqda. (Faqat lokalda ishlaydi)")
    # Lokal test qilish uchun ishlatiladigan joy
    application.run_polling(allowed_updates=Update.ALL_TYPES)
    return app

if __name__ == "__main__":
    # Lokal test qilishda faqatgina "main" funksiyasini chaqirish kifoya
    app = main()
    if WEB_HOST:
         # Productionda Gunicorn ishga tushiradi. Lokal test uchun esa bu qism ishlaydi.
         # app.run()ni gunicorn/uvicorn ishlatadi
         pass
    else:
        # Agar lokalda WEB_HOST o'rnatilmagan bo'lsa, polling ishlashi kerak
        pass
