import os
import csv
import logging
import asyncio
from dotenv import load_dotenv
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, Bot
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.error import InvalidToken

# --- Load environment variables ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("⚠️ BOT_TOKEN is missing!")

# --- Logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- CSV file path ---
CSV_FILE = "listings.csv"

def load_listings():
    listings = []
    if os.path.exists(CSV_FILE):
        with open(CSV_FILE, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            listings = list(reader)
    logger.info(f"✅ Loaded {len(listings)} listings from CSV.")
    return listings

# --- Bot commands ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    LISTINGS = load_listings()
    if not LISTINGS:
        await update.message.reply_text("No listings available at the moment.")
        return

    keyboard = [
        [InlineKeyboardButton(item["title"], callback_data=str(i))]
        for i, item in enumerate(LISTINGS)
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Select a listing:", reply_markup=reply_markup)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    LISTINGS = load_listings()
    idx = int(query.data)
    if idx >= len(LISTINGS):
        await query.message.reply_text("Listing not found.")
        return

    item = LISTINGS[idx]
    text = f"Title: {item['title']}\nPrice: {item.get('price', 'N/A')}\nDescription: {item.get('description', 'N/A')}"
    if item.get("image_url"):
        try:
            await query.message.reply_photo(photo=item["image_url"], caption=text)
        except:
            await query.message.reply_text(text)
    else:
        await query.message.reply_text(text)

# --- Keep-alive Flask server ---
app_server = Flask('')

@app_server.route('/')
def home():
    return "🤖 Bot is alive ✅"

async def run_flask():
    """Run Flask server in a thread."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: app_server.run(host="0.0.0.0", port=8080))

# --- Main async function ---
async def main():
    # Start Flask server
    asyncio.create_task(run_flask())

    # Verify token
    try:
        bot = Bot(TOKEN)
        me = await bot.get_me()
        logger.info(f"✅ Token is valid. Bot username: @{me.username}")
    except InvalidToken:
        raise ValueError("❌ Invalid BOT_TOKEN! Check your .env file.")

    # Start Telegram bot
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))

    logger.info("🤖 Bot is starting polling...")
    await app.run_polling()

# --- Run bot ---
if __name__ == "__main__":
    asyncio.run(main())
