import os
import csv
import logging
import hashlib
from threading import Thread
from typing import List
from asyncio import Lock
from flask import Flask
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# -------------------------
# Flask keep-alive
# -------------------------
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is alive!"

def _run_flask():
    app.run(host="0.0.0.0", port=5000)

def keep_alive():
    Thread(target=_run_flask, daemon=True).start()

# -------------------------
# Logging
# -------------------------
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------------
# Token
# -------------------------
TOKEN = os.getenv("BOT_TOKEN") or "7674430173:AAFrlCjke51w7y5KqLr9bj4_gb5t2J8AcYo"

# -------------------------
# Files
# -------------------------
LISTINGS_FILE = "listings_with_url.csv"
FAV_FILE = "favorites.csv"
if not os.path.exists(FAV_FILE):
    with open(FAV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["user_id", "title", "price", "bedrooms", "location", "url", "image_url"])
        writer.writeheader()

# -------------------------
# Helpers
# -------------------------
def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()

def add_favorite(user_id: int, listing: dict):
    try:
        with open(FAV_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["user_id", "title", "price", "bedrooms", "location", "url", "image_url"])
            writer.writerow({
                "user_id": str(user_id),
                "title": listing.get("title", ""),
                "price": listing.get("price", ""),
                "bedrooms": listing.get("bedrooms", ""),
                "location": listing.get("location", ""),
                "url": listing.get("url", ""),
                "image_url": listing.get("image_url", ""),
            })
        return True
    except Exception as e:
        logger.exception("Failed to add favorite: %s", e)
        return False

def remove_favorite_by_hash(user_id: int, url_hash: str) -> bool:
    rows = []
    removed = False
    try:
        with open(FAV_FILE, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                if r["user_id"] == str(user_id) and _md5(r.get("url", "")) == url_hash:
                    removed = True
                    continue
                rows.append(r)
        with open(FAV_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["user_id", "title", "price", "bedrooms", "location", "url", "image_url"])
            writer.writeheader()
            writer.writerows(rows)
    except Exception as e:
        logger.exception("Failed to remove favorite: %s", e)
    return removed

def load_user_favorites(user_id: int) -> List[dict]:
    out = []
    try:
        with open(FAV_FILE, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                if r["user_id"] == str(user_id):
                    out.append(r)
    except Exception as e:
        logger.exception("Failed to load favorites: %s", e)
    return out

# -------------------------
# Listings
# -------------------------
listings: List[dict] = []

def normalize_bedrooms(raw) -> str:
    if raw is None or str(raw).strip() == "":
        return "Unknown"
    s = str(raw).strip()
    try:
        v = int(float(s))
        return "Bedsitter" if v == 0 else str(v)
    except:
        if s.lower() in ("bedsitter", "bedsit", "bed sitter"):
            return "Bedsitter"
        return s

def safe_int_price(raw) -> int:
    try:
        return int(float(raw))
    except:
        return 0

def load_listings():
    global listings
    listings = []
    if not os.path.exists(LISTINGS_FILE):
        logger.warning("Listings file not found: %s", LISTINGS_FILE)
        return
    try:
        with open(LISTINGS_FILE, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                loc = (r.get("location") or "").strip().title()
                br = normalize_bedrooms(r.get("bedrooms", ""))
                price = safe_int_price(r.get("price", 0))
                listings.append({
                    "title": (r.get("title") or "").strip(),
                    "location": loc,
                    "image_url": (r.get("image_url") or "").strip(),
                    "url": (r.get("url") or "").strip(),
                    "bedrooms": br,
                    "price": price,
                })
        logger.info("✅ Loaded %d listings from %s", len(listings), LISTINGS_FILE)
        logger.info("Available locations: %s", sorted({l["location"] for l in listings}))
        if listings:
            logger.info("Sample listing: %s", listings[0])
    except Exception as e:
        logger.exception("Failed to load listings: %s", e)
        listings = []

load_listings()
LOCATIONS = sorted({(l.get("location") or "").strip() for l in listings if l.get("location")}) or ["Kiambu Town", "Thika", "Ruiru", "Juja", "Limuru", "Githunguri"]
BEDROOM_OPTIONS = ["Bedsitter", "1", "2", "3", "4+"]
BUDGET_OPTIONS = [
    ("≤ 10k", 0, 10000),
    ("≤ 20k", 10001, 20000),
    ("≤ 30k", 20001, 30000),
    ("≤ 50k", 30001, 50000),
    ("Any", 0, float("inf")),
]

def chunk(items: List, n: int) -> List[List]:
    return [items[i:i+n] for i in range(0, len(items), n)]

def build_location_keyboard():
    buttons = [InlineKeyboardButton(loc, callback_data=f"location|{loc}") for loc in LOCATIONS]
    rows = chunk(buttons, 2)
    rows.append([InlineKeyboardButton("⭐ My Favorites", callback_data="favorites")])
    rows.append([InlineKeyboardButton("❓ Help", callback_data="help")])
    return InlineKeyboardMarkup(rows)

def build_bedroom_keyboard():
    btns = [InlineKeyboardButton(b, callback_data=f"bedrooms|{b}") for b in BEDROOM_OPTIONS]
    rows = chunk(btns, 3)
    rows.append([InlineKeyboardButton("🔙 Back to Locations", callback_data="restart")])
    return InlineKeyboardMarkup(rows)

def build_budget_keyboard():
    btns = []
    for label, mn, mx in BUDGET_OPTIONS:
        mx_serial = "inf" if mx == float("inf") else str(mx)
        btns.append(InlineKeyboardButton(label, callback_data=f"budget|{mn}|{mx_serial}"))
    rows = chunk(btns, 2)
    rows.append([InlineKeyboardButton("🔙 Back to Bedrooms", callback_data="back_to_bedrooms")])
    return InlineKeyboardMarkup(rows)

def build_pagination_keyboard(page: int, total: int, saved: bool=False):
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅ Prev", callback_data="page|prev"))
    if page < total - 1:
        nav.append(InlineKeyboardButton("Next ➡", callback_data="page|next"))
    fav_button = InlineKeyboardButton("⭐ Save" if not saved else "❌ Remove", callback_data="fav_toggle")
    extra_row = [
        InlineKeyboardButton("🔙 Back to Budget", callback_data="back_to_budget"),
        InlineKeyboardButton("🔄 New Search", callback_data="restart")
    ]
    rows = []
    if nav:
        rows.append(nav)
    rows.append([fav_button])
    rows.append(extra_row)
    return InlineKeyboardMarkup(rows)

# -------------------------
# State
# -------------------------
user_state = {}
user_locks = {}

def get_user_lock(uid: int) -> Lock:
    if uid not in user_locks:
        user_locks[uid] = Lock()
    return user_locks[uid]

# -------------------------
# Handlers
# -------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user or (update.callback_query.from_user if update.callback_query else None)
    if not user:
        return
    if not listings:
        await update.message.reply_text("Sorry, no listings are available at the moment. Try again later.")
        return
    user_state[user.id] = {}
    kb = build_location_keyboard()
    try:
        if update.message:
            await update.message.reply_text("🏡 Welcome! Select a location:", reply_markup=kb)
        elif update.callback_query:
            await update.callback_query.message.edit_text("🏡 Welcome! Select a location:", reply_markup=kb)
    except Exception:
        await update.message.reply_text("🏡 Welcome! Select a location:", reply_markup=kb)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 *How to use*\n\n"
        "1. /start → choose Location → Bedrooms → Budget\n"
        "2. Browse results with Prev / Next\n"
        "3. Save a listing with ⭐ Save and view saved with /favorites\n\n"
        "Use the back buttons to change previous selections."
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    favs = load_user_favorites(user.id)
    if not favs:
        await update.message.reply_text("⭐ You have no favorites yet. Save one from the results screen.")
        return
    for f in favs:
        caption = (
            f"🏠 {f.get('title')}\n"
            f"📍 {f.get('location')}\n"
            f"🛏 {f.get('bedrooms')}\n"
            f"💰 {f.get('price')}\n"
            f"🔗 {f.get('url')}"
        )
        url_hash = _md5(f.get("url", ""))
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Remove", callback_data=f"fav_remove|{url_hash}")],
            [InlineKeyboardButton("🔄 Start New Search", callback_data="restart")]
        ])
        if f.get("image_url"):
            await update.message.reply_photo(photo=f.get("image_url"), caption=caption, reply_markup=kb)
        else:
            await update.message.reply_text(caption, reply_markup=kb)

# -------------------------
# Listing display helper
# -------------------------
async def send_or_edit_listing(context: ContextTypes.DEFAULT_TYPE, query, uid: int, refresh_only: bool=False):
    st = user_state.get(uid, {})
    results = st.get("results", [])
    if not results:
        await query.message.reply_text("Session expired or no results. Use /start.")
        return
    page = st.get("page", 0)
    res = results[page]
    total = len(results)
    favs = load_user_favorites(uid)
    url_hash = _md5(res.get("url", ""))
    saved = any(_md5(f.get("url", "")) == url_hash for f in favs)
    caption = (
        f"📄 Result {page+1} of {total}\n\n"
        f"🏡 {res.get('title')}\n"
        f"📍 {res.get('location')}\n"
        f"🛏 {res.get('bedrooms')}\n"
        f"💰 KES {res.get('price'):,}\n"
        f"🔗 {res.get('url')}\n"
    )
    markup = build_pagination_keyboard(page, total, saved=saved)
    display = st.get("display")
    if display and not refresh_only:
        chat_id = display.get("chat_id")
        msg_id = display.get("message_id")
        if res.get("image_url"):
            media = InputMediaPhoto(media=res.get("image_url"), caption=caption)
            try:
                await context.bot.edit_message_media(chat_id=chat_id, message_id=msg_id, media=media, reply_markup=markup)
                return
            except:
                pass
        try:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=caption, reply_markup=markup)
            return
        except:
            pass
    if res.get("image_url"):
        sent = await query.message.reply_photo(photo=res.get("image_url"), caption=caption, reply_markup=markup)
    else:
        sent = await query.message.reply_text(caption, reply_markup=markup)
    user_state[uid]["display"] = {"chat_id": sent.chat_id, "message_id": sent.message_id}

# -------------------------
# Match bedrooms helper
# -------------------------
def match_bedrooms(listing_bedrooms: str, selected_bedrooms: str) -> bool:
    if selected_bedrooms == "4+":
        try:
            return int(listing_bedrooms) >= 4
        except ValueError:
            return False
    return listing_bedrooms == selected_bedrooms

# -------------------------
# Callback handler with lock
# -------------------------
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.callback_query.from_user
    if not user:
        return
    uid = user.id
    lock = get_user_lock(uid)
    async with lock:
        try:
            query = update.callback_query
            await query.answer()
            data = query.data or ""
            if uid not in user_state:
                user_state[uid] = {}

            # Split callback data
            parts = data.split("|")
            action = parts[0]

            if action == "location":
                location = parts[1]
                user_state[uid]["location"] = location
                kb = build_bedroom_keyboard()
                await query.message.edit_text(f"📍 Selected: {location}\nChoose bedrooms:", reply_markup=kb)

            elif action == "bedrooms":
                bedrooms = parts[1]
                user_state[uid]["bedrooms"] = bedrooms
                kb = build_budget_keyboard()
                await query.message.edit_text(f"🛏 Selected: {bedrooms}\nChoose budget:", reply_markup=kb)

            elif action == "budget":
                if len(parts) != 3:
                    logger.error("Invalid budget callback data: %s", data)
                    await query.message.reply_text("Error processing budget. Try /start again.")
                    return
                try:
                    min_price, max_price = int(parts[1]), parts[2]
                    max_price = float("inf") if max_price == "inf" else int(max_price)
                except ValueError as e:
                    logger.error("Invalid budget values: %s, error: %s", data, e)
                    await query.message.reply_text("Error processing budget. Try /start again.")
                    return
                if "location" not in user_state[uid] or "bedrooms" not in user_state[uid]:
                    logger.error("Missing user state for user %d: %s", uid, user_state[uid])
                    await query.message.reply_text("Session expired. Use /start.")
                    user_state[uid] = {}
                    return
                user_state[uid]["min_price"] = min_price
                user_state[uid]["max_price"] = max_price
                logger.info("Filtering for user %d: location=%s, bedrooms=%s, price=%d-%s",
                            uid, user_state[uid].get("location", "N/A"), user_state[uid].get("bedrooms", "N/A"), min_price, max_price)
                filtered = [
                    l for l in listings
                    if l["location"] == user_state[uid]["location"]
                    and match_bedrooms(l["bedrooms"], user_state[uid]["bedrooms"])
                    and min_price <= l["price"] <= max_price
                ]
                if not filtered:
                    logger.info("No matches. Sample listing for debugging: %s", listings[0] if listings else "No listings")
                    for l in listings[:3]:
                        logger.info("Listing: loc=%s, beds=%s, price=%d",
                                    l["location"], l["bedrooms"], l["price"])
                logger.info("Found %d matching listings", len(filtered))
                user_state[uid]["results"] = filtered
                user_state[uid]["page"] = 0
                if not filtered:
                    await query.message.edit_text(
                        "No listings found for your criteria. Try again?",
                        reply_markup=build_location_keyboard()
                    )
                    user_state[uid] = {}
                    return
                await send_or_edit_listing(context, query, uid)

            elif action == "page":
                direction = parts[1]
                page = user_state[uid].get("page", 0)
                if direction == "prev" and page > 0:
                    user_state[uid]["page"] = page - 1
                elif direction == "next" and page < len(user_state[uid]["results"]) - 1:
                    user_state[uid]["page"] = page + 1
                await send_or_edit_listing(context, query, uid, refresh_only=True)

            elif action == "fav_toggle":
                results = user_state[uid].get("results", [])
                page = user_state[uid].get("page", 0)
                if not results:
                    await query.message.reply_text("Session expired. Use /start.")
                    return
                listing = results[page]
                url_hash = _md5(listing.get("url", ""))
                favs = load_user_favorites(uid)
                saved = any(_md5(f.get("url", "")) == url_hash for f in favs)
                if saved:
                    remove_favorite_by_hash(uid, url_hash)
                    await query.message.reply_text("Removed from favorites!")
                else:
                    add_favorite(uid, listing)
                    await query.message.reply_text("Added to favorites!")
                await send_or_edit_listing(context, query, uid, refresh_only=True)

            elif action == "fav_remove":
                url_hash = parts[1]
                if remove_favorite_by_hash(uid, url_hash):
                    await query.message.reply_text("Favorite removed!")
                else:
                    await query.message.reply_text("Failed to remove favorite.")
                await query.message.delete()

            elif action == "restart":
                user_state[uid] = {}
                kb = build_location_keyboard()
                await query.message.edit_text("🏡 Welcome! Select a location:", reply_markup=kb)

            elif action == "back_to_bedrooms":
                user_state[uid].pop("min_price", None)
                user_state[uid].pop("max_price", None)
                user_state[uid].pop("results", None)
                user_state[uid].pop("page", None)
                user_state[uid].pop("display", None)
                kb = build_bedroom_keyboard()
                await query.message.edit_text(
                    f"📍 Selected: {user_state[uid].get('location', 'N/A')}\nChoose bedrooms:",
                    reply_markup=kb
                )

            elif action == "back_to_budget":
                user_state[uid].pop("results", None)
                user_state[uid].pop("page", None)
                user_state[uid].pop("display", None)
                kb = build_budget_keyboard()
                await query.message.edit_text(
                    f"🛏 Selected: {user_state[uid].get('bedrooms', 'N/A')}\nChoose budget:",
                    reply_markup=kb
                )

            elif action == "favorites":
                favs = load_user_favorites(uid)
                if not favs:
                    await query.message.reply_text("⭐ You have no favorites yet.")
                    return
                for f in favs:
                    caption = (
                        f"🏠 {f.get('title')}\n"
                        f"📍 {f.get('location')}\n"
                        f"🛏 {f.get('bedrooms')}\n"
                        f"💰 {f.get('price')}\n"
                        f"🔗 {f.get('url')}"
                    )
                    url_hash = _md5(f.get("url", ""))
                    kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("❌ Remove", callback_data=f"fav_remove|{url_hash}")],
                        [InlineKeyboardButton("🔄 Start New Search", callback_data="restart")]
                    ])
                    if f.get("image_url"):
                        await query.message.reply_photo(photo=f.get("image_url"), caption=caption, reply_markup=kb)
                    else:
                        await query.message.reply_text(caption, reply_markup=kb)

            elif action == "help":
                text = (
                    "🤖 *How to use*\n\n"
                    "1. /start → choose Location → Bedrooms → Budget\n"
                    "2. Browse results with Prev / Next\n"
                    "3. Save a listing with ⭐ Save and view saved with /favorites\n\n"
                    "Use the back buttons to change previous selections."
                )
                await query.message.reply_text(text, parse_mode="Markdown")

        except Exception as e:
            logger.exception("Error in callback_handler: %s", e)
            await query.message.reply_text("Something went wrong. Try /start again.")

# -------------------------
# Main
# -------------------------
def main():
    keep_alive()
    app_bot = Application.builder().token(TOKEN).build()
    app_bot.add_handler(CommandHandler("start", cmd_start))
    app_bot.add_handler(CommandHandler("help", cmd_help))
    app_bot.add_handler(CommandHandler("favorites", cmd_favorites))
    app_bot.add_handler(CallbackQueryHandler(callback_handler))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: u.message.reply_text("Use /start to begin.")))
    logger.info("Bot starting...")
    app_bot.run_polling()

if __name__ == "__main__":
    main()