# bot.py
import os
import csv
import logging
import hashlib
from threading import Thread
from flask import Flask
from typing import List, Optional
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
# Logging
# -------------------------
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------------
# Keep-alive (small Flask app)
# -------------------------
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is alive!"

def _run_flask():
    # run Flask on port 5000 locally
    app.run(host="0.0.0.0", port=5000)

def keep_alive():
    Thread(target=_run_flask, daemon=True).start()

# -------------------------
# Token loading (env first, fallback allowed for local dev)
# -------------------------
TOKEN = os.getenv("BOT_TOKEN")

# ✅ fallback for local development
if not TOKEN:
    TOKEN = "7674430173:AAFrlCjke51w7y5KqLr9bj4_gb5t2J8AcYo"

# -------------------------
# Files & favorites helper
# -------------------------
LISTINGS_FILE = "listings_with_url.csv"
FAV_FILE = "favorites.csv"

# create favorites file with header if missing
if not os.path.exists(FAV_FILE):
    try:
        with open(FAV_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["user_id", "title", "price", "bedrooms", "location", "url", "image_url"])
            writer.writeheader()
    except Exception as e:
        logger.exception("Could not create favorites file: %s", e)

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
# Load & normalize listings
# -------------------------
listings: List[dict] = []

def normalize_bedrooms(raw) -> str:
    if raw is None:
        return "Unknown"
    s = str(raw).strip()
    if s == "":
        return "Unknown"
    try:
        v = int(float(s))
        return "Bedsitter" if v == 0 else str(v)
    except Exception:
        if s.lower() in ("bedsitter", "bedsit", "bed sitter"):
            return "Bedsitter"
        return s

def safe_int_price(raw) -> int:
    try:
        return int(float(raw))
    except Exception:
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
    except Exception as e:
        logger.exception("Failed to load listings: %s", e)
        listings = []

load_listings()

# Clean + dedupe locations
LOCATIONS = sorted({(l.get("location") or "").strip() for l in listings if l.get("location")})
if not LOCATIONS:
    LOCATIONS = ["Kiambu Town", "Thika", "Ruiru", "Juja", "Limuru", "Githunguri"]

BEDROOM_OPTIONS = ["Bedsitter", "1", "2", "3", "4+"]
BUDGET_OPTIONS = [
    ("≤ 10k", 0, 10000),
    ("≤ 20k", 10001, 20000),
    ("≤ 30k", 20001, 30000),
    ("≤ 50k", 30001, 50000),
    ("Any", 0, float("inf")),
]

# -------------------------
# Helpers: UI builders
# -------------------------
def chunk(items: List, n: int) -> List[List]:
    return [items[i:i+n] for i in range(0, len(items), n)]

def build_location_keyboard():
    buttons = [InlineKeyboardButton(loc, callback_data=f"location|{loc}") for loc in LOCATIONS]
    rows = chunk(buttons, 2)  # 2 columns
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
    extra_row = [InlineKeyboardButton("🔙 Back to Budget", callback_data="back_to_budget"), InlineKeyboardButton("🔄 New Search", callback_data="restart")]

    rows = []
    if nav:
        rows.append(nav)
    rows.append([fav_button])
    rows.append(extra_row)
    return InlineKeyboardMarkup(rows)

# -------------------------
# State store (in-memory)
# -------------------------
# user_state[user_id] = {
#   "location": str, "bedrooms": str, "budget_min": int, "budget_max": int,
#   "results": [listing, ...], "page": int,
#   "display": {"chat_id": int, "message_id": int}   # where the current listing is shown (for editing)
# }
user_state = {}

# -------------------------
# Handlers
# -------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user or (update.callback_query.from_user if update.callback_query else None)
        if not user:
            return
        user_state[user.id] = {}
        kb = build_location_keyboard()
        if update.message:
            await update.message.reply_text("🏡 Welcome! Select a location:", reply_markup=kb)
        elif update.callback_query:
            try:
                await update.callback_query.message.edit_text("🏡 Welcome! Select a location:", reply_markup=kb)
            except Exception:
                await update.callback_query.message.reply_text("🏡 Welcome! Select a location:", reply_markup=kb)
    except Exception as e:
        logger.exception("Error in /start: %s", e)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 *How to use*\n\n"
        "1. /start → choose Location → Bedrooms → Budget\n"
        "2. Browse results one-by-one with Prev / Next\n"
        "3. Save a listing with ⭐ Save and view saved with /favorites\n\n"
        "Use the back buttons to change previous selections."
    )
    try:
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(text)

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
        try:
            if f.get("image_url"):
                await update.message.reply_photo(photo=f.get("image_url"), caption=caption, reply_markup=kb)
            else:
                await update.message.reply_text(caption, reply_markup=kb)
        except Exception:
            await update.message.reply_text(caption, reply_markup=kb)

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        user = query.from_user
        uid = user.id
        data = query.data or ""

        # ensure session
        if uid not in user_state:
            user_state[uid] = {}

        # Restart
        if data == "restart":
            await cmd_start(update, context)
            return

        # Help (button)
        if data == "help":
            # send help via chat
            try:
                await query.message.reply_text(
                    "Use /start to begin. Select location, bedrooms, budget. Save favorites with ⭐ Save. View with /favorites."
                )
            except Exception:
                pass
            return

        # Favorites menu
        if data == "favorites":
            # show user's favorites (same as /favorites)
            fake_update = update
            # call favorites command handler (we have access to context)
            await cmd_favorites(fake_update, context)
            return

        # Back to bedrooms
        if data == "back_to_bedrooms":
            kb = build_bedroom_keyboard()
            try:
                await query.message.edit_text("🛏️ Select number of bedrooms:", reply_markup=kb)
            except Exception:
                await query.message.reply_text("🛏️ Select number of bedrooms:", reply_markup=kb)
            return

        # Back to budget
        if data == "back_to_budget":
            kb = build_budget_keyboard()
            try:
                await query.message.edit_text("💰 Select budget:", reply_markup=kb)
            except Exception:
                await query.message.reply_text("💰 Select budget:", reply_markup=kb)
            return

        # Location chosen
        if data.startswith("location|"):
            _, loc = data.split("|", 1)
            user_state[uid]["location"] = loc.strip()
            kb = build_bedroom_keyboard()
            try:
                await query.message.edit_text(f"📍 Location: *{loc}*\n\n🛏️ Select number of bedrooms:", parse_mode="Markdown", reply_markup=kb)
            except Exception:
                await query.message.reply_text(f"📍 Location: {loc}\n\n🛏️ Select number of bedrooms:", reply_markup=kb)
            return

        # Bedrooms chosen
        if data.startswith("bedrooms|"):
            _, br = data.split("|", 1)
            user_state[uid]["bedrooms"] = br
            kb = build_budget_keyboard()
            try:
                await query.message.edit_text(f"🛏 Bedrooms: *{br}*\n\n💰 Choose budget:", parse_mode="Markdown", reply_markup=kb)
            except Exception:
                await query.message.reply_text(f"🛏 Bedrooms: {br}\n\n💰 Choose budget:", reply_markup=kb)
            return

        # Budget chosen -> compute results and start pagination
        if data.startswith("budget|"):
            try:
                _, mn_s, mx_s = data.split("|")
                mn = int(mn_s)
                mx = float("inf") if mx_s == "inf" else int(mx_s)
            except Exception:
                mn, mx = 0, float("inf")
            user_state[uid]["budget_min"] = mn
            user_state[uid]["budget_max"] = mx

            # filter listings
            loc = user_state[uid].get("location")
            br = user_state[uid].get("bedrooms")
            results = []
            for L in listings:
                if (L.get("location") or "") != (loc or ""):
                    continue
                lb = L.get("bedrooms", "")
                # bedrooms matching
                if br == "Bedsitter":
                    if lb not in ("Bedsitter", "0"):
                        continue
                elif br == "4+":
                    try:
                        if int(lb) < 4:
                            continue
                    except Exception:
                        continue
                else:
                    # numeric match
                    if lb != br:
                        # try numeric compare
                        try:
                            if int(lb) != int(br):
                                continue
                        except Exception:
                            continue
                # price
                try:
                    p = int(L.get("price", 0))
                except Exception:
                    p = 0
                if not (mn <= p <= mx):
                    continue
                results.append(L)

            if not results:
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Start New Search", callback_data="restart")],
                                           [InlineKeyboardButton("🔙 Back to Bedrooms", callback_data="back_to_bedrooms")]])
                try:
                    await query.message.edit_text("😔 No listings found for your filters.", reply_markup=kb)
                except Exception:
                    await query.message.reply_text("😔 No listings found for your filters.", reply_markup=kb)
                return

            # store results & page
            user_state[uid]["results"] = results
            user_state[uid]["page"] = 0
            user_state[uid].pop("display", None)  # clear display info
            # show first page as fresh message (so we can edit this message later)
            await send_or_edit_listing(context, query, uid)
            return

        # Pagination control
        if data.startswith("page|"):
            if "results" not in user_state[uid]:
                await query.message.reply_text("Session expired. Use /start to begin again.")
                return
            if data == "page|next":
                user_state[uid]["page"] = min(user_state[uid]["page"] + 1, len(user_state[uid]["results"]) - 1)
            elif data == "page|prev":
                user_state[uid]["page"] = max(user_state[uid]["page"] - 1, 0)
            # edit the display message (if stored) or send new
            await send_or_edit_listing(context, query, uid)
            return

        # Favorite toggle (save or remove)
        if data == "fav_toggle":
            # current listing is results[page]
            res = None
            if "results" in user_state.get(uid, {}) and "page" in user_state.get(uid, {}):
                res = user_state[uid]["results"][user_state[uid]["page"]]
            if not res:
                await query.answer("No listing selected.", show_alert=False)
                return
            url = res.get("url", "")
            url_hash = _md5(url)
            # check if already in favorites for this user
            favs = load_user_favorites(uid)
            already = any(_md5(f.get("url","")) == url_hash for f in favs)
            if already:
                removed = remove_favorite_by_hash(uid, url_hash)
                await query.answer("Removed from favorites." if removed else "Not removed.", show_alert=False)
            else:
                ok = add_favorite(uid, res)
                await query.answer("Saved to favorites." if ok else "Could not save.", show_alert=False)
            # update keyboard to reflect new fav state
            await send_or_edit_listing(context, query, uid, refresh_only=True)
            return

        # Remove from favorites (from /favorites view)
        if data.startswith("fav_remove|"):
            _, url_hash = data.split("|",1)
            removed = remove_favorite_by_hash(uid, url_hash)
            await query.answer("Removed." if removed else "Not found.", show_alert=False)
            # refresh favorites list: call /favorites
            # best to send the list again
            await cmd_favorites(update, context)
            return

        logger.info("Unhandled callback: %s", data)
    except Exception as e:
        logger.exception("Error in callback_handler: %s", e)

# -------------------------
# Listing display & pagination helpers
# -------------------------
async def send_or_edit_listing(context: ContextTypes.DEFAULT_TYPE, query, uid: int, refresh_only: bool=False):
    """
    Show the listing for user uid at page user_state[uid]['page'].
    If a 'display' message is stored in state, try to edit that message.
    Otherwise, send a fresh message and store its chat_id/message_id so next edits can target it.
    If refresh_only is True, only update keyboard/caption without re-sending.
    """
    try:
        st = user_state.get(uid, {})
        results = st.get("results", [])
        if not results:
            await query.message.reply_text("Session expired or no results. Use /start.")
            return
        page = st.get("page", 0)
        res = results[page]
        total = len(results)

        # favorites check
        favs = load_user_favorites(uid)
        url_hash = _md5(res.get("url",""))
        saved = any(_md5(f.get("url","")) == url_hash for f in favs)

        caption = (
            f"📄 Result {page+1} of {total}\n\n"
            f"🏡 {res.get('title')}\n"
            f"📍 {res.get('location')}\n"
            f"🛏 {res.get('bedrooms')}\n"
            f"💰 KES {res.get('price'):,}\n"
            f"🔗 {res.get('url')}\n"
        )

        markup = build_pagination_keyboard(page, total, saved=saved)

        # If we already have a display message saved, try to edit that
        display = st.get("display")
        if display and not refresh_only:
            # attempt to edit existing message (photo or text)
            chat_id = display.get("chat_id")
            msg_id = display.get("message_id")
            if res.get("image_url"):
                media = InputMediaPhoto(media=res.get("image_url"), caption=caption)
                try:
                    await context.bot.edit_message_media(chat_id=chat_id, message_id=msg_id, media=media, reply_markup=markup)
                    return
                except Exception:
                    logger.debug("edit_message_media failed, will try edit_message_text/send new", exc_info=True)
            # fallback to edit text
            try:
                await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=caption, reply_markup=markup)
                return
            except Exception:
                logger.debug("edit_message_text failed for stored message, will send new message", exc_info=True)

        # send a fresh message (and store it)
        if res.get("image_url"):
            try:
                sent = await query.message.reply_photo(photo=res.get("image_url"), caption=caption, reply_markup=markup)
            except Exception:
                sent = await query.message.reply_text(caption, reply_markup=markup)
        else:
            sent = await query.message.reply_text(caption, reply_markup=markup)

        # store display message info so future page changes edit this message
        try:
            user_state[uid]["display"] = {"chat_id": sent.chat_id, "message_id": sent.message_id}
        except Exception:
            logger.debug("Couldn't store display message info.", exc_info=True)
    except Exception as e:
        logger.exception("Error in send_or_edit_listing: %s", e)
        try:
            await query.message.reply_text("An error occurred while showing listing. Try /start.")
        except Exception:
            pass

# -------------------------
# Start / Dispatcher
# -------------------------
def main():
    keep_alive()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("favorites", cmd_favorites))
    app.add_handler(CallbackQueryHandler(callback_handler))
    # optional: fallback for text
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: u.message.reply_text("Use /start to begin.")))

    logger.info("Bot starting (polling)...")
    try:
        app.run_polling()
    except Exception:
        logger.exception("Run polling failed, exiting.")

if __name__ == "__main__":
    main()
