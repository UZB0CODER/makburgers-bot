import logging
import os
import json
from flask import Flask, request, jsonify
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
import asyncio
import requests

# --- 1. SOZLAMALAR ---
# Telegram bot tokenini va ADMIN ID ni o'rnating
TOKEN = os.environ.get('BOT_TOKEN')
# Agar WEB_HOST kiritilmagan bo'lsa, lokal Polling rejimida ishlash uchun None o'rnating
WEB_HOST = os.environ.get('WEB_HOST') 

# Ma'lumotlarni saqlash fayli (lokal rejim uchun)
USER_DATA_FILE = 'user_data_cache.json'
FIRESTORE_ENABLED = False  # Firebase ishlatilmaydi

# Flask app ni yaratish
app_flask = Flask(__name__)

# Logging sozlamalari
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- 2. GLOBAL MA'LUMOTLAR VA HOLAT ---
user_data = {}
application = None # Global telegram Application instance

MENU = {
    "cat_fastfood": {
        "name_uz": "🍔 Fast Food",
        "items": {
            "item_hotdog": ("Hotdog", 15000),
            "item_lavash": ("Lavash", 25000),
            "item_burger": ("Burger", 30000),
        }
    },
    "cat_drinks": {
        "name_uz": "🥤 Ichimliklar",
        "items": {
            "item_cola": ("Coca Cola (1L)", 10000),
            "item_fanta": ("Fanta (1L)", 9000),
        }
    }
}

# --- 3. MA'LUMOTLARNI SAQLASH FUNKSIYALARI (LOKAL REJIM UCHUN) ---

def load_users_from_file() -> dict:
    """JSON fayldan foydalanuvchi ma'lumotlarini yuklaydi."""
    if not os.path.exists(USER_DATA_FILE):
        return {}
    try:
        with open(USER_DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return {int(k): v for k, v in data.items()}
    except Exception as e:
        logger.error(f"Fayldan yuklashda xatolik: {e}")
        return {}

def save_users_to_file() -> None:
    """Foydalanuvchi ma'lumotlarini JSON faylga saqlaydi."""
    if not FIRESTORE_ENABLED:
        try:
            with open(USER_DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(user_data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"Faylga saqlashda xatolik: {e}")

# --- 4. ASOSIY HANDLERLAR (Sizning bot mantiqingiz) ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Botni ishga tushirishda /start buyrug'ini qabul qiladi."""
    chat_id = update.message.chat_id
    
    if chat_id not in user_data:
        user_data[chat_id] = {'state': 'awaiting_contact', 'cart': {}, 'language': 'uz'}
        save_users_to_file()

        keyboard = [
            [KeyboardButton("📞 Mening raqamimni yuborish", request_contact=True)]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text("Ro'yxatdan o'tish uchun telefon raqamingizni yuboring:", reply_markup=reply_markup)
        return

    await show_main_menu(update, context)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Asosiy menyuni ko'rsatadi."""
    chat_id = update.effective_chat.id
    user_data[chat_id]['state'] = 'main_menu'
    save_users_to_file()
    
    keyboard = [
        ["🛍 Buyurtma berish", "🛒 Savatcha"],
        ["📝 Fikr bildirish", "⚙️ Sozlamalar"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await context.bot.send_message(
        chat_id=chat_id,
        text="Asosiy menyu. Kerakli bo'limni tanlang:",
        reply_markup=reply_markup
    )

async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Telefon raqamini qabul qiladi va ro'yxatdan o'tkazadi."""
    chat_id = update.message.chat_id
    contact = update.message.contact
    
    if user_data.get(chat_id, {}).get('state') == 'awaiting_contact' and contact and contact.user_id == chat_id:
        user_data[chat_id]['phone'] = contact.phone_number
        user_data[chat_id]['state'] = 'registered'
        save_users_to_file()
        
        await update.message.reply_text(f"Rahmat! Siz {contact.phone_number} raqami bilan muvaffaqiyatli ro'yxatdan o'tdingiz!")
        await show_main_menu(update, context)
        return

    await update.message.reply_text("Iltimos, avval /start buyrug'i orqali ro'yxatdan o'ting.")

async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mahsulot kategoriyalarini ko'rsatadi."""
    query = update.callback_query
    if query:
        await query.answer()
        chat_id = query.message.chat_id
        message_id = query.message.message_id
    else:
        chat_id = update.message.chat_id
        message_id = None 

    user_data[chat_id]['state'] = 'selecting_category'
    save_users_to_file()
    
    keyboard = []
    for key, data in MENU.items():
        keyboard.append([InlineKeyboardButton(data['name_uz'], callback_data=f"cat:{key}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "Kategoriyani tanlang:"
    
    if query:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup
        )

async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Manzil qabul qilindi. Buyurtmani rasmiylashtirish davom etmoqda...")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    if text == "🛍 Buyurtma berish":
        await show_categories(update, context)
    elif text == "🛒 Savatcha":
        await cart_view_handler(update, context, is_text_command=True)
    elif text == "⬅️ Orqaga" or text == "❌ Bekor qilish":
        await show_main_menu(update, context)
    else:
        await update.message.reply_text("Tushunmadim. Asosiy menyudan tanlang.")

async def category_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    category_key = query.data.split(':')[1]
    
    text = f"**{MENU[category_key]['name_uz']}** bo'limi."
    keyboard = []
    
    for item_key, item_data in MENU[category_key]['items'].items():
        name, price = item_data
        keyboard.append([
            InlineKeyboardButton(f"{name} - {price:,} so'm", callback_data=f"add:{item_key}")
        ])

    keyboard.append([InlineKeyboardButton("⬅️ Bosh menyuga", callback_data="back:categories")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text=text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def quantity_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Miqdor o'zgartirildi (Mantiq qo'shilishi kerak).")

async def cart_view_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, is_text_command=False) -> None:
    chat_id = update.effective_chat.id
    cart = user_data.get(chat_id, {}).get('cart', {})
    
    text = "🛒 **Savatchangiz:**\n\n"
    total_price = 0
    
    if not cart:
        text += "Savatchangiz bo'sh. Buyurtma berish bo'limidan mahsulot tanlang."
        keyboard = [[InlineKeyboardButton("🛍 Buyurtma berish", callback_data="back:categories")]]
    else:
        for item_key, qty in cart.items():
            item_name = "Noma'lum mahsulot"
            item_price = 0
            for category_key, category_data in MENU.items():
                if item_key in category_data['items']:
                    item_name, item_price = category_data['items'][item_key]
                    break
            
            sub_total = qty * item_price
            total_price += sub_total
            
            text += f"▪️ {item_name} x {qty} dona = {sub_total:,} so'm\n"
        
        text += f"\n**Jami: {total_price:,} so'm**"
        
        keyboard = [
            [InlineKeyboardButton("✅ Rasmiylashtirish", callback_data="checkout:start")],
            [InlineKeyboardButton("🗑 Savatchani tozalash", callback_data="cart:clear")],
            [InlineKeyboardButton("⬅️ Qo'shishni davom etish", callback_data="back:categories")]
        ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if is_text_command:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

async def cart_clear_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat_id = query.message.chat_id
    
    user_data[chat_id]['cart'] = {}
    save_users_to_file()
    
    await query.answer("Savatcha tozalandi.")
    await show_categories(update, context)

async def checkout_start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Buyurtmani rasmiylashtirish (Mantiq qo'shilishi kerak).")
    await query.edit_message_text("Manzilingizni yuboring.")

async def delivery_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pass

async def confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pass

@app_flask.route(f'/{TOKEN}', methods=['POST'])
async def webhook():
    """Telegramdan kelgan yangilanishlarni (webhook) qayta ishlaydi."""
    if request.method == "POST":
        update_json = request.get_json(force=True)
        # Webhook handler asinxron bo'lishi kerak. Bu yerda application.process_update() asinxron funksiyasini chaqiramiz.
        await application.process_update(Update.de_json(update_json, application.bot))
        return jsonify({"status": "ok"}), 200
    return jsonify({"status": "method not allowed"}), 405

@app_flask.route('/')
def home():
    """Saytning asosiy sahifasi (Tekshirish uchun)."""
    return "Makburgers Bot veb-xizmati ishlamoqda!", 200

# --- 5. BOTNI ISHGA TUSHIRISH FUNKSIYASI ---

def init_handlers(app: Application) -> None:
    """Handlerlarni Application ob'ektiga qo'shadi."""
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    app.add_handler(MessageHandler(filters.LOCATION, location_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(CallbackQueryHandler(show_categories, pattern="^back:categories"))
    app.add_handler(CallbackQueryHandler(category_handler, pattern="^cat:"))
    app.add_handler(CallbackQueryHandler(quantity_handler, pattern="^qty_(inc|dec):"))
    app.add_handler(CallbackQueryHandler(cart_view_handler, pattern="^cart:view"))
    app.add_handler(CallbackQueryHandler(cart_clear_handler, pattern="^cart:clear"))
    app.add_handler(CallbackQueryHandler(checkout_start_handler, pattern="^checkout:start"))
    app.add_handler(CallbackQueryHandler(delivery_handler, pattern="^delivery:"))
    app.add_handler(CallbackQueryHandler(confirm_handler, pattern="^confirm:"))
    app.add_handler(CallbackQueryHandler(lambda update, context: asyncio.create_task(update.callback_query.answer()), pattern="^ignore"))


async def set_webhook(app: Application) -> None:
    """Telegramda Webhook URL'ni o'rnatadi."""
    if not TOKEN:
        logger.error("BOT_TOKEN kiritilmagan!")
        return
        
    if WEB_HOST and WEB_HOST.startswith('https://'):
        WEBHOOK_FULL_URL = f"{WEB_HOST}{TOKEN}"
        
        # Requests kutubxonasi yordamida Webhook o'rnatish
        telegram_set_webhook_url = f"https://api.telegram.org/bot{TOKEN}/setWebhook"
        try:
            response = requests.post(telegram_set_webhook_url, json={'url': WEBHOOK_FULL_URL})
            
            if response.status_code == 200 and response.json().get('ok'):
                 logger.info(f"Webhook muvaffaqiyatli o'rnatildi: {WEBHOOK_FULL_URL}")
            else:
                 logger.error(f"Webhook o'rnatishda xato: {response.text}")
        except Exception as e:
            logger.error(f"Webhook o'rnatish uchun API chaqiruvida xato: {e}")

def main() -> Flask:
    """Botni ishga tushirish funksiyasi (Railway tomonidan chaqiriladi)."""
    global application, user_data
    
    if not TOKEN:
        logger.error("FATAL: BOT_TOKEN muhit o'zgaruvchisi o'rnatilmagan. Iltimos, Railway'da sozlang.")
        # Agar tokenni topa olmasa, bo'sh Flask ilovasini qaytaradi
        return app_flask
        
    # Ma'lumotlarni yuklash (Serverga joylashish uchun ham, lokal ishga tushirish uchun ham)
    user_data = load_users_from_file()
    logger.info(f"Bot ma'lumotlari yuklandi. Jami foydalanuvchilar: {len(user_data)}")

    # Telegram Application ob'ektini yaratish
    application = Application.builder().token(TOKEN).concurrent_updates(True).build()
    init_handlers(application)

    if WEB_HOST:
        # WEBHOOK rejimi (Railway uchun)
        logger.info("Bot Webhook rejimida ishga tushmoqda.")
        
        # set_webhook ni asinxron ishga tushirish kerak, chunki main() sinkron chaqiriladi
        asyncio.run(set_webhook(application))

        # Waitress serveri Flask app_flask obyektini chaqiradi va u ishga tushadi
        return app_flask
    else:
        # POLLING rejimi (Lokal kompyuter uchun)
        logger.warning("WEB_HOST o'rnatilmagan. Lokal (Polling) rejimida ishga tushmoqda.")
        try:
            application.run_polling(allowed_updates=Update.ALL_TYPES)
        except KeyboardInterrupt:
            logger.info("Polling to'xtatildi (Ctrl+C).")
        finally:
            save_users_to_file()
            
if __name__ == "__main__":
    main()
