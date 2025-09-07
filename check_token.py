import os
from telegram import Bot
from telegram.error import InvalidToken

# Load token from environment or .env
TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise ValueError("❌ BOT_TOKEN not found. Set it in your environment or .env file!")

try:
    bot = Bot(token=TOKEN)
    me = bot.get_me()  # This will raise InvalidToken if wrong
    print(f"✅ Token is valid. Bot username: @{me.username}")
except InvalidToken:
    print("❌ Invalid token! Please check your BOT_TOKEN.")
