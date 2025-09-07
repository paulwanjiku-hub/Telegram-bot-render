import os
import csv
import logging
from aiofile import AIOFile, Writer
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)
from telegram.error import BadRequest
from keep_alive import keep_alive
from dotenv import load_dotenv

# --- Logging ---
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Load env ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("⚠️ BOT_TOKEN missing!")
logger.info(f"✅ BOT_TOKEN loaded: {TOKEN[:5]}...{TOKEN[-5:]}")

# --- CSV files ---
LISTINGS_FILE = "listings_with_url.csv"
FAV_FILE = "favorites.csv"

if not os.path.exists(FAV_FILE):
    with open(FAV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["user_id","title","price","bedrooms","location","url","image_url"])
        writer.writeheader()

# --- Load listings ---
def load_listings():
    listings = []
    try:
        with open(LISTINGS_FILE, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    row["bedrooms"] = int(row.get("bedrooms",0))
                    row["price"] = int(row.get("price",0))
                    listings.append(row)
                except ValueError:
                    continue
    except FileNotFoundError:
        logger.warning(f"{LISTINGS_FILE} not found. Starting with empty listings.")
    logger.info(f"✅ Loaded {len(listings)} listings from CSV.")
    return listings

LISTINGS = load_listings()

# --- Async favorites operations ---
async def add_to_favorites(user_id, listing):
    async with AIOFile(FAV_FILE, "a", encoding="utf-8", newline="") as af:
        writer = Writer(af)
        row = f'{user_id},"{listing.get("title","Untitled")}",{listing.get("price","N/A")},{listing.get("bedrooms",0)},"{listing.get("location","Unknown")}",{listing.get("url","#")},{listing.get("image_url","")}\n'
        await writer(row)
        await af.fsync()

async def load_user_favorites(user_id):
    favs = []
    async with AIOFile(FAV_FILE, "r", encoding="utf-8") as af:
        content = await af.read()
        reader = csv.DictReader(content.splitlines())
        for row in reader:
            if row["user_id"] == str(user_id):
                favs.append(row)
    return favs

# --- Locations ---
LOCATIONS = ["Kiambu Town", "Thika", "Ruiru", "Juja", "Limuru", "Githunguri"]

# --- Show location menu ---
async def show_location_menu(target, context):
    keyboard, row = [], []
    for i, loc in enumerate(LOCATIONS, start=1):
        row.append(InlineKeyboardButton(loc, callback_data=f"loc_{loc}"))
        if i % 2 == 0:
            keyboard.append(row)
            row = []
    if row: keyboard.append(row)

    await target.reply_text(
        "*🏡 Welcome to Kiambu House Hunter!*\n\nSelect your location:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await show_location_menu(update.message, context)

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏓 Bot is alive!")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Kiambu House Hunter Help*\n\n"
        "• /start – Begin search 🏡\n"
        "• /ping – Check bot 🏓\n"
        "• /help – Show help ℹ️",
        parse_mode="Markdown"
    )

async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text("ℹ️ Please type /start to begin your search 🏡")

# --- Button handler ---
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data: return
    try: await query.answer()
    except BadRequest as e:
        if "query is too old" not in str(e).lower(): raise

    if query.data.startswith("loc_"):
        location = query.data.split("_",1)[1]
        context.user_data["location"] = location
        await query.edit_message_text(f"✅ Location: {location}\n\n(Next steps...)")

# --- Main ---
def main():
    keep_alive()
    app = Application.builder().token(TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback))

    logger.info("🤖 Bot is running...")
    app.run_polling()

if __name__=="__main__":
    main()
