import os
import sys
import subprocess
import csv
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)
from telegram.error import BadRequest
from keep_alive import keep_alive  # ✅ import from separate file

# --- Start keep-alive webserver (UptimeRobot will ping it) ---
keep_alive()

# --- Logging setup ---
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# --- Auto-install python-dotenv if missing ---
try:
    from dotenv import load_dotenv
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "python-dotenv"])
    from dotenv import load_dotenv

# --- Load environment variables ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("⚠️ BOT_TOKEN is missing! Please set it in .env or Render Secrets.")
else:
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
                    row["bedrooms"] = int(row.get("bedrooms", 0))
                    row["price"] = int(row.get("price", 0))
                    listings.append(row)
                except ValueError as e:
                    logger.warning(f"Skipping row due to bad data: {row} ({e})")
    except FileNotFoundError:
        logger.error(f"⚠️ {LISTINGS_FILE} not found. Starting with no listings.")
    logger.info(f"✅ Loaded {len(listings)} listings from CSV.")
    return listings

LISTINGS = load_listings()

# --- Favorites management ---
def add_to_favorites(user_id, listing):
    with open(FAV_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["user_id","title","price","bedrooms","location","url","image_url"])
        writer.writerow({
            "user_id": user_id,
            "title": listing.get("title","Untitled"),
            "price": listing.get("price","N/A"),
            "bedrooms": listing.get("bedrooms",0),
            "location": listing.get("location","Unknown"),
            "url": listing.get("url","#"),
            "image_url": listing.get("image_url","")
        })

def remove_from_favorites(user_id, url):
    rows = []
    with open(FAV_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not (row["user_id"] == str(user_id) and row["url"] == url):
                rows.append(row)
    with open(FAV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["user_id","title","price","bedrooms","location","url","image_url"])
        writer.writeheader()
        writer.writerows(rows)

def load_user_favorites(user_id):
    favs = []
    with open(FAV_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["user_id"] == str(user_id):
                favs.append(row)
    return favs

# --- Show favorites ---
async def show_favorites(update_or_query, context, user_id):
    favs = load_user_favorites(user_id)
    target = getattr(update_or_query, "message", None) or getattr(update_or_query, "callback_query", None).message

    if not favs:
        await target.reply_text("⭐ You have no favorites yet.")
        return

    for fav in favs:
        caption = (
            f"🏠 {fav['title']}\n"
            f"{fav['bedrooms']}BR — KES {fav['price']}\n"
            f"📍 {fav['location']}\n"
            f"🔗 [View Listing]({fav['url']})"
        )
        keyboard = [[InlineKeyboardButton("❌ Remove from Favorites", callback_data=f"removefav_{fav['url']}")]]
        try:
            await target.reply_photo(
                photo=fav["image_url"],
                caption=caption,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except:
            await target.reply_text(
                text=caption,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

# --- Bot commands ---
async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏓 Bot is alive and responding!")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🤖 *Kiambu House Hunter Bot Help*\n\n"
        "Commands:\n"
        "• /start – Begin a new house search 🏡\n"
        "• /favorites – View your saved favorites ⭐\n"
        "• /ping – Check if bot is alive 🏓\n"
        "• /help – Show this help message ℹ️"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def favorites_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    await show_favorites(update, context, user_id)

# --- Location flow ---
LOCATIONS = ["Kiambu Town", "Thika", "Ruiru", "Juja", "Limuru", "Githunguri"]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await show_location_menu(update.message, context)

async def show_location_menu(target, context):
    keyboard, row = [], []
    for i, loc in enumerate(LOCATIONS, start=1):
        row.append(InlineKeyboardButton(loc, callback_data=f"loc_{loc}"))
        if i % 2 == 0:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    await target.reply_text(
        "*🏡 Welcome to Kiambu House Hunter!*\n\nSelect your location:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

# --- Listings ---
async def show_listing(query, context, index):
    results = context.user_data.get("results") or []
    if not results:
        await query.edit_message_text("❌ No listings found. Try another search.")
        return

    index = max(0, min(index, len(results) - 1))
    context.user_data["index"] = index
    listing = results[index]

    caption = (
        f"🏠 {listing.get('title','Untitled')}\n"
        f"{listing.get('bedrooms',0)}BR — KES {listing.get('price','N/A')}\n"
        f"📍 {listing.get('location','Unknown')}\n"
        f"🔗 [View Listing]({listing.get('url','#')})\n\n"
        f"Result {index+1} of {len(results)}"
    )

    keyboard = []
    nav_buttons = []
    if index > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data="prev"))
    if index < len(results) - 1:
        nav_buttons.append(InlineKeyboardButton("➡️ Next", callback_data="next"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("⭐ Add to Favorites", callback_data=f"addfav_{listing.get('url')}")])
    keyboard.append([InlineKeyboardButton("🔄 Start Over", callback_data="restart")])

    try:
        await query.edit_message_media(
            media=InputMediaPhoto(
                media=listing.get("image_url", ""),
                caption=caption,
                parse_mode="Markdown",
            ),
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as e:
        logger.warning(f"Failed to send image: {e}")
        await query.edit_message_text(
            text=caption,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

# --- Button handler ---
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return
    try:
        await query.answer()
    except BadRequest as e:
        if "query is too old" not in str(e).lower():
            raise

    user_id = query.from_user.id

    if query.data == "restart":
        context.user_data.clear()
        await show_location_menu(query.message, context)
    elif query.data.startswith("loc_"):
        location = query.data.split("_", 1)[1]
        context.user_data["location"] = location
        keyboard = [
            [InlineKeyboardButton("Bedsitter", callback_data="bed_0")],
            [InlineKeyboardButton("1 Bedroom", callback_data="bed_1"), InlineKeyboardButton("2 Bedrooms", callback_data="bed_2")],
            [InlineKeyboardButton("3 Bedrooms", callback_data="bed_3"), InlineKeyboardButton("4+ Bedrooms", callback_data="bed_4")],
        ]
        await query.edit_message_text(
            text=f"✅ Location: {location}\n\nNow choose number of bedrooms:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    elif query.data.startswith("bed_"):
        bedrooms = int(query.data.split("_")[1])
        context.user_data["bedrooms"] = bedrooms
        keyboard = [
            [InlineKeyboardButton("Under KES 10,000", callback_data="price_10000"), InlineKeyboardButton("Under KES 20,000", callback_data="price_20000")],
            [InlineKeyboardButton("Under KES 30,000", callback_data="price_30000"), InlineKeyboardButton("No Limit", callback_data="price_none")],
        ]
        await query.edit_message_text(
            text=f"✅ Bedrooms: {bedrooms}\n\nNow choose your budget:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    elif query.data.startswith("price_"):
        price = query.data.split("_")[1]
        context.user_data["price"] = None if price == "none" else int(price)

        location = context.user_data.get("location")
        bedrooms = context.user_data.get("bedrooms", 0)
        max_price = context.user_data.get("price")

        results = [
            listing for listing in LISTINGS
            if location.lower() in listing.get("location","").lower()
            and listing.get("bedrooms",0) >= bedrooms
            and (max_price is None or listing.get("price",0) <= max_price)
        ]
        context.user_data["results"] = results
        context.user_data["index"] = 0

        if results:
            await show_listing(query, context, 0)
        else:
            await query.edit_message_text(
                "❌ No listings found for your search. Try again.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Start Over", callback_data="restart")]])
            )
    elif query.data == "next":
        index = context.user_data.get("index", 0) + 1
        await show_listing(query, context, index)
    elif query.data == "prev":
        index = context.user_data.get("index", 0) - 1
        await show_listing(query, context, index)
    elif query.data.startswith("addfav_"):
        index = context.user_data.get("index", 0)
        listing = context.user_data.get("results", [])[index]
        add_to_favorites(user_id, listing)
        await query.answer("⭐ Added to favorites!", show_alert=False)
    elif query.data.startswith("removefav_"):
        url = query.data.split("_", 1)[1]
        remove_from_favorites(user_id, url)
        await query.answer("❌ Removed from favorites!", show_alert=False)
        await show_favorites(update, context, user_id)

# --- Fallback ---
async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text("ℹ️ Please type /start to begin your house search 🏡")

# --- Main ---
def main():
    app_bot = Application.builder().token(TOKEN).build()

    # Add handlers
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("ping", ping))
    app_bot.add_handler(CommandHandler("help", help_command))
    app_bot.add_handler(CommandHandler("favorites", favorites_command))
    app_bot.add_handler(CallbackQueryHandler(button))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback))

    logger.info("🤖 Bot is running...")
    app_bot.run_polling()

# --- Run ---
if __name__ == "__main__":
    main()
