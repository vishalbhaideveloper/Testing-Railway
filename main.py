
import asyncio
import json
import logging
import os
import re
import time
import shutil

import requests
import instaloader
import yt_dlp
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router, types
from aiogram.filters import Command  # ✅ Correct for Aiogram v3

from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, FSInputFile

# ----------------- Configuration & Setup -----------------

# Configure logging
logging.basicConfig(level=logging.INFO)

# Load environment variables and constants
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = 7574316340  # Replace with your Telegram ID
DELETE_DELAY = 1800   # 30 minutes (in seconds)

# Initialize bot and dispatcher (note: Dispatcher now takes no parameters)
bot = Bot(token=TOKEN)
dp = Dispatcher()  # <-- Updated: Do not pass bot here
router = Router()
dp.include_router(router)

# ----------------- Text Formatting Functions -----------------

def format_text(text: str) -> str:
    """
    Convert Markdown-style **bold** text to Telegram HTML <b>bold</b> format.
    """
    return re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)

def clean_text(text):
    """Removes ANSI escape sequences (color codes) from text."""
    return re.sub(r'\x1b\[[0-9;]*m', '', text)  # Remove color codes


# ----------------- Patching Message Methods -----------------

# Save original functions
original_answer = Message.answer
original_edit_text = Message.edit_text

async def patched_answer(self: Message, text: str, *args, **kwargs):
    formatted_text = format_text(text)
    return await original_answer(self, formatted_text, *args, **kwargs, parse_mode="HTML")

async def patched_edit_text(self: Message, text: str, *args, **kwargs):
    formatted_text = format_text(text)
    return await original_edit_text(self, formatted_text, *args, **kwargs, parse_mode="HTML")

# Apply patches
Message.answer = patched_answer
Message.edit_text = patched_edit_text

# ----------------- Data Storage Helpers -----------------

DATA_FILE = "videoBotData.json"

def load_data():
    """
    Load user data from JSON file.
    """
    try:
        with open(DATA_FILE, "r") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"total_users": 0, "blocked_users": 0, "user_ids": []}

def save_data(data):
    """
    Save user data to JSON file.
    """
    with open(DATA_FILE, "w") as file:
        json.dump(data, file, indent=4)

# ----------------- Utility: Schedule Message Deletion -----------------

async def schedule_deletion(message: types.Message, delay: int):
    """
    Deletes a given message after a specified delay.
    """
    await asyncio.sleep(delay)
    try:
        await message.delete()
        logging.info("Deleted message after delay.")
    except Exception as e:
        logging.error(f"Error deleting message: {e}")

# ----------------- Video Download Functions -----------------

def run_yt_dlp(url: str, ydl_opts: dict):
    """
    Runs yt-dlp in a separate thread.
    """
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

async def download_youtube(url: str):
    """
    Downloads a YouTube video asynchronously.
    """
    timestamp = int(time.time())
    filename = f"youtube_{timestamp}.mp4"

    ydl_opts = {
        'format': 'best[ext=mp4]',
        'outtmpl': filename,
        'noplaylist': True
    }
    try:
        await asyncio.to_thread(run_yt_dlp, url, ydl_opts)
        return filename
    except Exception as e:
        logging.error(f"YouTube Download Error: {e}")
        return None

import requests

async def download_instagram(url: str):
    """
    Tries to download an Instagram video using yt-dlp.
    If yt-dlp fails, it falls back to Snapinsta API.
    """
    timestamp = int(time.time())
    filename = f"instagram_{timestamp}.mp4"

    ydl_opts = {
        'format': 'best[ext=mp4]',
        'outtmpl': filename,
        'noplaylist': True
    }

    try:
        # Try yt-dlp first
        await asyncio.to_thread(run_yt_dlp, url, ydl_opts)
        return filename
    except Exception as e:
        logging.warning(f"yt-dlp failed: {e}. Trying Snapinsta API...")

        # Fallback: Use Snapinsta API
        try:
            snapinsta_api = "https://snapinsta.io/api/ajaxSearch"
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            data = {"q": url}

            response = requests.post(snapinsta_api, headers=headers, data=data, timeout=10)
            response_data = response.json()

            video_url = response_data.get("links", [{}])[0].get("url")

            if video_url:
                # Download the video file
                await download_file(video_url, filename)
                return filename
            else:
                logging.error("Snapinsta API did not return a video URL.")
                return None
        except Exception as e:
            logging.error(f"Instagram Snapinsta Download Error: {e}")
            return None

async def download_tiktok(url: str):
    """
    Downloads a TikTok video asynchronously using the TikMate API.
    """
    try:
        response = requests.get(f"https://api.tikmate.app/api/lookup?url={url}", timeout=10)
        data = response.json()
        video_url = data.get('videoUrl')
        if video_url:
            filename = f"tiktok_{int(time.time())}.mp4"
            await download_file(video_url, filename)
            return filename
    except Exception as e:
        logging.error(f"TikTok Download Error: {e}")
        return None

async def download_twitter(url: str):
    """
    Downloads a Twitter video asynchronously using the twdown API.
    """
    try:
        response = requests.get(f"https://twdown.net/download?url={url}", timeout=10)
        data = response.json()
        video_url = data.get("video_url")
        if video_url:
            filename = f"twitter_{int(time.time())}.mp4"
            await download_file(video_url, filename)
            return filename
    except Exception as e:
        logging.error(f"Twitter Download Error: {e}")
        return None

def download_file_sync(url: str, filename: str):
    """
    Synchronously downloads a video file.
    """
    response = requests.get(url, stream=True, timeout=20)
    with open(filename, "wb") as f:
        for chunk in response.iter_content(chunk_size=1024):
            if chunk:
                f.write(chunk)
    return filename

async def download_file(url: str, filename: str):
    """
    Downloads a video file asynchronously by offloading the blocking task.
    """
    return await asyncio.to_thread(download_file_sync, url, filename)
@router.message(Command("start"))
async def start(message: types.Message):
    """
    The /start command handler:
    - Updates usage statistics.
    - Sends a welcome message with inline buttons for help and support.
    """
    user_id = message.from_user.id
    data = load_data()

    # Add user only if they are new
    if user_id not in data["user_ids"]:
        data["user_ids"].append(user_id)

    # Dynamically update total user count
    data["total_users"] = len(data["user_ids"])

    # If user was blocked before and unblocked now, just decrease blocked_users
    if user_id in data.get("blocked_user_ids", []):
        if data["blocked_users"] > 0:
            data["blocked_users"] -= 1
        data["blocked_user_ids"].remove(user_id)  # Remove from blocked list

    save_data(data)

    buttons = [
        [InlineKeyboardButton(text="📜 Help", callback_data="help")],
        [InlineKeyboardButton(text="💬 Contact & Support", url="https://t.me/+6peJqny8QKA5ZDU1")]
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    await message.answer(
        "👋 **Welcome to Video Downloader Bot!**\n\n"
        "📌 Send me a **YouTube**, **Instagram**, **TikTok**, or **Twitter** video link, and I'll download it for you! 🎥",
        reply_markup=keyboard
    )

@router.callback_query()
async def handle_callback(query: types.CallbackQuery):
    """
    Callback query handler for the help button.
    """
    if query.data == "help":
        await query.message.edit_text(
            "ℹ️ **How to Use:**\n\n"
            "1️⃣ Send me a valid video link from YouTube, Instagram, TikTok, or Twitter.\n"
            "2️⃣ Wait for processing, and I'll send your video! 🎬\n\n"
            "⚠️ **Note:** Videos are deleted after 30 minutes to prevent copyright issues."
        )

@router.message()
async def download_video(message: types.Message):
    """
    Handles video download requests:
    - Validates that the message contains a URL.
    - Calls the appropriate download function based on the URL.
    - If successful, sends the video and schedules deletion of the related messages.
    """
    url = message.text.strip()
    
    # Validate URL (simple regex approach)
    url_pattern = re.compile(r'https?://[^\s]+')
    if not url_pattern.match(url):
        return  # Ignore messages that are not links

    await message.answer("⏳ **Processing your request... This may take a minute.**")

    filename = None
    try:
        if "youtube.com" in url or "youtu.be" in url:
            filename = await download_youtube(url)
        elif "instagram.com" in url:
            filename = await download_instagram(url)
        elif "tiktok.com" in url:
            filename = await download_tiktok(url)
        elif "twitter.com" in url:
            filename = await download_twitter(url)
        else:
            await message.answer("❌ **Invalid URL!** Please send a valid video link.")
            return
    except Exception as e:
        logging.error(f"Error downloading video: {e}")
        await message.answer("❌ **Download failed. Please try again!**")
        return

    if filename:
        sent_message = await send_large_video(message, filename)
        # Schedule deletion of the original request message
        asyncio.create_task(schedule_deletion(message, DELETE_DELAY))
    else:
        await message.answer("❌ **Failed to download the video. Please try again.**")

async def send_large_video(message: types.Message, filename: str):
    """
    Sends the video file:
    - Uses answer_document if the file is larger than 50 MB.
    - Uses answer_video otherwise.
    - Deletes the local video file immediately after sending.
    - Schedules deletion of the sent message after 30 minutes.
    """
    try:
        file_size = os.path.getsize(filename)
        if file_size > 50 * 1024 * 1024:  # File larger than 50 MB
            sent_message = await message.answer_document(
                FSInputFile(filename),
                caption="📁 Here is your video.\n\n🚀 *This message will be deleted in 30 minutes to prevent copyright issues.*"
            )
        else:
            sent_message = await message.answer_video(
                FSInputFile(filename),
                caption="🎥 Here is your video.\n\n🚀 *This message will be deleted in 30 minutes to prevent copyright issues.*"
            )

        # Delete the file immediately after sending
        os.remove(filename)
        logging.info(f"Deleted file: {filename}")

        # Schedule deletion of the sent message
        asyncio.create_task(schedule_deletion(sent_message, DELETE_DELAY))
        return sent_message
    except Exception as e:
        logging.error(f"Error sending file: {e}")
        await message.answer("❌ **Failed to send the file. Please try again.**")
        return None
@router.my_chat_member()
@router.my_chat_member()
async def handle_block(event: types.ChatMemberUpdated):
    """
    Detects when a user blocks or unblocks the bot and updates statistics accordingly.
    """
    user_id = event.from_user.id
    data = load_data()

    if event.new_chat_member.status in ["kicked", "left"]:  # User blocked the bot
        if user_id in data["user_ids"]:
            data["user_ids"].remove(user_id)
            data["blocked_users"] += 1
            if "blocked_user_ids" not in data:
                data["blocked_user_ids"] = []
            if user_id not in data["blocked_user_ids"]:
                data["blocked_user_ids"].append(user_id)  # Store blocked user ID

    elif event.new_chat_member.status == "member":  # User unblocked the bot
        if user_id in data.get("blocked_user_ids", []):
            if data["blocked_users"] > 0:
                data["blocked_users"] -= 1
            data["blocked_user_ids"].remove(user_id)  # Remove from blocked list
            data["user_ids"].append(user_id)  # Add user back

    # Dynamically update total user count
    data["total_users"] = len(data["user_ids"])

    save_data(data)


@dp.message(Command("stats"))
async def stats(message: types.Message):
    logging.info(f"Received /stats command from user: {message.from_user.id}")

    if message.from_user.id != OWNER_ID:
        logging.warning(f"Unauthorized access attempt by user: {message.from_user.id}")
        return await message.answer("❌ You are not authorized to view stats!")

    try:
        data = load_data()
        logging.info("Loaded bot stats successfully.")
        
        # FIX: parse_mode is handled by your patched message.answer
        await message.answer(
            f"📊 **Bot Stats**:\n\n"
            f"👥 **Total Users:** {data.get('total_users', 0)}\n"
            f"🚫 **Blocked Users:** {data.get('blocked_users', 0)}\n"
        )
        logging.info("Sent bot stats to the owner.")
    except Exception as e:
        logging.error(f"Error retrieving bot stats: {e}")
        await message.answer("❌ An error occurred while fetching stats.")


# ----------------- Start the Bot -----------------

if __name__ == "__main__":
    # In aiogram v3, start polling by passing the bot instance.
    asyncio.run(dp.start_polling(bot))