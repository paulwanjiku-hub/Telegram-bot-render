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
    raise ValueError("⚠️ BOT_TOKEN missing! Please check your .env file and set BOT_TOKEN.")
logger.info("✅ BOT_TOKEN loaded.")

# --- CSV files ---
LISTINGS_FILE = "listings_with_url.csv"
FAV_FILE = "favorites.csv"

FAV_FIELDS = ["user_id","title","price","bedrooms","location","url","image_url"]

if not os.path.exists(FAV_FILE):
    with open(FAV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FAV_FIELDS)
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
                    logger.warning(f"Skipping row with invalid data: {row}")
                    continue
    except FileNotFoundError:
        logger.warning(f"{LISTINGS_FILE} not found. Starting with empty listings.")
    logger.info(f"✅ Loaded {len(listings)} listings from CSV.")
    return listings

LISTINGS = load_listings()

# --- Async favorites operations ---
async def add_to_favorites(user_id, listing):
    # Use DictWriter for proper escaping and quoting
    async with AIOFile(FAV_FILE, "a", encoding="utf-8", newline="") as af:
        writer = Writer(af)
        # dict to CSV row
        row = {
            "user_id": user_id,
            "title": listing.get("title","Untitled"),
            "price": listing.get("price","N/A"),
            "bedrooms": listing.get("bedrooms",0),
            "location": listing.get("location","Unknown"),
            "url": listing.get("url","#"),
            "image_url": listing.get("image_url","")
        }
        # manually join values for async writing
        csv_row = f'{row["user_id"]},"{row["title"]}",{row["price"]},{row["bedrooms"]},"{row["location"]}",{row["url"]},{row["image_url"]}\n'
        await writer(csv_row)
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

# --- Show listings for location ---
async def show_listings_for_location(target, location):
    filtered = [l for l in LISTINGS if l.get("location") == location]
    if not filtered:
        await target.reply_text(f"😔 No listings found for {location}.")
        return
    for listing in filtered[:5]:  # Show top 5
        text = (
            f"*{listing['title']}*\n"
            f"💰 Price: {listing['price']}\n"
            f"🛏 Bedrooms: {listing['bedrooms']}\n"
            f"🌍 Location: {listing['location']}\n"
            f"[View Listing]({listing['url']})"
        )
        try:
            await target.reply_photo(listing.get("image_url",""), caption=text, parse_mode="Markdown")
        except Exception as e:
            # fallback if photo fails
            await target.reply_text(text, parse_mode="Markdown")

# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.message:
            await show_location_menu(update.message, context)
    except Exception as e:
        logger.error(f"Error in start handler: {e}")

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
        try:
            await query.edit_message_text(f"✅ Location: {location}\n\nHere are the latest listings:")
            await show_listings_for_location(query.message.chat, location)
        except Exception as e:
            logger.error(f"Error showing listings: {e}")
            await query.edit_message_text(f"✅ Location: {location}\n\nSorry, something went wrong showing listings.")

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