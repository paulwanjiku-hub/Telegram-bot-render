import os
import csv
import logging
import asyncio
from aiofile import AIOFile, Writer
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)
from telegram.error import BadRequest
from keep_alive import keep_alive  # Flask server

# --- Logging setup ---
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# --- Load environment variables ---
from dotenv import load_dotenv
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("⚠️ BOT_TOKEN is missing!")
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

async def remove_from_favorites(user_id, url):
    rows = []
    async with AIOFile(FAV_FILE, "r", encoding="utf-8") as af:
        content = await af.read()
        reader = csv.DictReader(content.splitlines())
        for row in reader:
            if not (row["user_id"] == str(user_id) and row["url"] == url):
                rows.append(row)
    async with AIOFile(FAV_FILE, "w", encoding="utf-8", newline="") as af:
        writer = Writer(af)
        writer_obj = csv.DictWriter(af, fieldnames=["user_id","title","price","bedrooms","location","url","image_url"])
        await writer_obj.writeheader()
        await writer_obj.writerows(rows)
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

# --- Show favorites ---
async def show_favorites(update_or_query, context, user_id):
    favs = await load_user_favorites(user_id)
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

# --- Show listing ---
async def show_listing(query, context, index):
    results = context.user_data.get("results") or []
    if not results:
        await query.edit_message_text("❌ No listings found. Try another search.")
        return

    index = max(0, min(index, len(results)-1))
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
    if index < len(results)-1:
        nav_buttons.append(InlineKeyboardButton("➡️ Next", callback_data="next"))
    if nav_buttons: keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("⭐ Add to Favorites", callback_data=f"addfav_{listing.get('url')}")])
    keyboard.append([InlineKeyboardButton("🔄 Start Over", callback_data="restart")])

    try:
        await query.edit_message_media(
            media=InputMediaPhoto(
                media=listing.get("image_url",""),
                caption=caption,
                parse_mode="Markdown",
            ),
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except:
        await query.edit_message_text(
            text=caption,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

# --- Commands ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await show_location_menu(update.message, context)

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏓 Bot is alive and responding!")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Kiambu House Hunter Bot Help*\n\n"
        "Commands:\n"
        "• /start – Begin a new house search 🏡\n"
        "• /favorites – View your saved favorites ⭐\n"
        "• /ping – Check if bot is alive 🏓\n"
        "• /help – Show this help message ℹ️",
        parse_mode="Markdown"
    )

async def favorites_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    await show_favorites(update, context, user_id)

async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text("ℹ️ Please type /start to begin your house search 🏡")

# --- Button handler ---
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data: return
    try: await query.answer()
    except BadRequest as e:
        if "query is too old" not in str(e).lower(): raise

    user_id = query.from_user.id

    if query.data == "restart":
        context.user_data.clear()
        await show_location_menu(query.message, context)

    elif query.data.startswith("loc_"):
        location = query.data.split("_",1)[1]
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
        context.user_data["price"] = None if price=="none" else int(price)

        location = context.user_data.get("location")
        bedrooms = context.user_data.get("bedrooms",0)
        max_price = context.user_data.get("price")

        results = [
            l for l in LISTINGS
            if location.lower() in l.get("location","").lower()
            and l.get("bedrooms",0) >= bedrooms
            and (max_price is None or l.get("price",0)<=max_price)
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

    elif query.data=="next":
        index = context.user_data.get("index",0)+1
        await show_listing(query, context, index)
    elif query.data=="prev":
        index = context.user_data.get("index",0)-1
        await show_listing(query, context, index)
    elif query.data.startswith("addfav_"):
        index = context.user_data.get("index",0)
        listing = context.user_data.get("results",[])[index]
        await add_to_favorites(user_id, listing)
        await query.answer("⭐ Added to favorites!", show_alert=False)
    elif query.data.startswith("removefav_"):
        url = query.data.split("_",1)[1]
        await remove_from_favorites(user_id, url)
        await query.answer("❌ Removed from favorites!", show_alert=False)
        await show_favorites(update, context, user_id)

# --- Main ---
def main():
    keep_alive()
    app_bot = Application.builder().token(TOKEN).build()

    # Handlers
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("ping", ping))
    app_bot.add_handler(CommandHandler("help", help_command))
    app_bot.add_handler(CommandHandler("favorites", favorites_command))
    app_bot.add_handler(CallbackQueryHandler(button))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback))

    logger.info("🤖 Bot is running...")
    app_bot.run_polling()

if __name__=="__main__":
    main()
