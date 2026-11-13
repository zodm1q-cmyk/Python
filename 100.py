# ==================== Pornhub Telegram Downloader Bot (Full Version) ====================
# تمام پروژه در یک فایل واحد
# نسخه کامل و به‌روزرسانی‌شده
# نیازمندی‌ها:
# pip install python-telegram-bot==21.6 yt-dlp python-dotenv

import os
import asyncio
import tempfile
import time
import logging
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode, ChatAction
from telegram.error import BadRequest, TimedOut, NetworkError
import yt_dlp

# -------------------- تنظیمات اولیه --------------------
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN") or "توکن_بات_تو_را_اینجا_بگذار"

# تنظیم لاگ
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# -------------------- ابزارهای کمکی --------------------
def human_size(num: float) -> str:
    """تبدیل بایت به فرمت خوانا"""
    if num is None or num < 0:
        return "نامشخص"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(num) < 1024.0:
            return f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"


def human_speed(num: float) -> str:
    """تبدیل سرعت به فرمت خوانا"""
    if not num or num <= 0:
        return "۰ B/s"
    return human_size(num) + "/s"


def human_time(seconds: Optional[float]) -> str:
    """تبدیل ثانیه به فرمت خوانا (دقیقه:ثانیه)"""
    if seconds is None or seconds < 0:
        return "--:--"
    seconds = int(seconds)
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def progress_bar(percent: float, length: int = 16) -> str:
    """نوار پیشرفت زیبا"""
    percent = max(0.0, min(100.0, percent))
    filled = int(length * percent / 100)
    return "█" * filled + "░" * (length - filled)


def get_ydl_opts(quality: str, progress_hook):
    """تنظیمات yt-dlp بر اساس کیفیت انتخاب‌شده"""
    format_map = {
        "best": "best[height<=1080]/best",
        "720": "best[height<=720]/best",
        "480": "best[height<=480]/best",
        "360": "best[height<=360]/best",
    }
    return {
        "format": format_map.get(quality, "best[height<=720]/best"),
        "outtmpl": "%(id)s.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [progress_hook],
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
        "retries": 5,
        "fragment_retries": 5,
        "file_access_retries": 3,
    }


# -------------------- دستورات بات --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🔥 **Pornhub Downloader Bot**\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "لینک ویدیوی **Pornhub** را بفرستید تا برایتان دانلود و آپلود کنم.\n\n"
        "✨ **ویژگی‌ها:**\n"
        "• انتخاب کیفیت (بهترین / ۷۲۰ / ۴۸۰ / ۳۶۰)\n"
        "• نمایش پیشرفت لحظه‌ای دانلود و آپلود\n"
        "• نمایش سرعت دانلود + ETA\n"
        "• نمایش رزولوشن، مدت زمان و حجم فایل\n"
        "• رابط کاربری تمیز و مدرن\n\n"
        "برای راهنمایی بیشتر دستور /help را بزنید."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 **راهنمای استفاده**\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "۱. لینک کامل ویدیوی Pornhub را ارسال کنید.\n"
        "۲. کیفیت مورد نظر را از دکمه‌ها انتخاب کنید.\n"
        "۳. منتظر بمانید تا دانلود و آپلود انجام شود.\n\n"
        "⚠️ **نکات مهم:**\n"
        "• حداکثر حجم قابل آپلود توسط بات معمولی: **۴۹ مگابایت**\n"
        "• اگر فایل بزرگ بود، کیفیت پایین‌تر انتخاب کنید.\n"
        "• فقط لینک‌های pornhub.com پشتیبانی می‌شوند.\n"
        "• در صورت خطا، چند ثانیه صبر کنید و دوباره امتحان کنید.\n\n"
        "دستورات:\n"
        "/start - شروع مجدد\n"
        "/help - نمایش این راهنما"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    # بررسی ساده لینک
    if not any(domain in url.lower() for domain in ["pornhub.com", "pornhub.org", "phncdn.com"]):
        await update.message.reply_text(
            "❌ فقط لینک‌های **Pornhub** پشتیبانی می‌شوند.\n"
            "لطفاً لینک معتبر ارسال کنید.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    keyboard = [
        [InlineKeyboardButton("🔥 بهترین کیفیت (تا 1080p)", callback_data=f"q_best|{url}")],
        [
            InlineKeyboardButton("720p", callback_data=f"q_720|{url}"),
            InlineKeyboardButton("480p", callback_data=f"q_480|{url}"),
            InlineKeyboardButton("360p", callback_data=f"q_360|{url}"),
        ],
    ]
    await update.message.reply_text(
        "🎬 لینک دریافت شد.\nکیفیت مورد نظر را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def quality_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data.startswith("q_"):
        return

    try:
        quality, url = data.split("|", 1)
        quality = quality.replace("q_", "")
    except ValueError:
        await query.edit_message_text("❌ داده نامعتبر.")
        return

    status_msg = await query.edit_message_text(
        "⏳ در حال استخراج اطلاعات ویدیو...\nلطفاً صبر کنید."
    )

    # متغیرهای پیشرفت
    downloaded_bytes = 0
    total_bytes = 0
    speed = 0.0
    eta = None
    last_edit_time = 0.0

    def progress_hook(d: dict):
        nonlocal downloaded_bytes, total_bytes, speed, eta
        if d.get("status") == "downloading":
            downloaded_bytes = d.get("downloaded_bytes", 0) or 0
            total_bytes = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            speed = d.get("speed") or 0.0
            eta = d.get("eta")

    # پوشه موقت
    with tempfile.TemporaryDirectory() as tmpdir:
        original_cwd = os.getcwd()
        os.chdir(tmpdir)

        try:
            ydl_opts = get_ydl_opts(quality, progress_hook)

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # استخراج اطلاعات بدون دانلود
                info = ydl.extract_info(url, download=False)

                title = (info.get("title") or "بدون عنوان")[:60]
                duration = info.get("duration")
                width = info.get("width")
                height = info.get("height")
                filesize = info.get("filesize") or info.get("filesize_approx") or 0
                resolution = f"{width}x{height}" if width and height else "نامشخص"
                duration_str = human_time(duration) if duration else "نامشخص"

                # پیام اولیه دانلود
                await status_msg.edit_text(
                    f"📥 **در حال دانلود...**\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"🎬 `{title}`\n"
                    f"📐 رزولوشن: `{resolution}`\n"
                    f"⏱ مدت زمان: `{duration_str}`\n"
                    f"📦 حجم تقریبی: `{human_size(filesize)}`\n\n"
                    f"{progress_bar(0)} `0.0%`\n"
                    f"⬇️ دانلود شده: `0 B`\n"
                    f"🚀 سرعت: `۰ B/s`\n"
                    f"⏳ باقی‌مانده: `--:--`",
                    parse_mode=ParseMode.MARKDOWN,
                )

                # شروع دانلود در executor
                loop = asyncio.get_event_loop()
                download_task = loop.run_in_executor(None, lambda: ydl.download([url]))

                # حلقه نمایش پیشرفت
                while not download_task.done():
                    await asyncio.sleep(1.4)
                    now = time.time()

                    if now - last_edit_time >= 1.3:
                        percent = 0.0
                        if total_bytes > 0:
                            percent = min(99.5, (downloaded_bytes / total_bytes) * 100)

                        text = (
                            f"📥 **در حال دانلود...**\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n\n"
                            f"🎬 `{title}`\n"
                            f"📐 رزولوشن: `{resolution}`\n"
                            f"⏱ مدت زمان: `{duration_str}`\n\n"
                            f"{progress_bar(percent)} `{percent:.1f}%`\n\n"
                            f"⬇️ دانلود شده: `{human_size(downloaded_bytes)}`\n"
                            f"📦 کل: `{human_size(total_bytes)}`\n"
                            f"🚀 سرعت: `{human_speed(speed)}`\n"
                            f"⏳ باقی‌مانده: `{human_time(eta)}`"
                        )
                        try:
                            await status_msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
                        except (BadRequest, TimedOut, NetworkError):
                            pass
                        last_edit_time = now

                # منتظر اتمام دانلود
                await download_task

                # پیدا کردن فایل دانلود شده
                files = list(Path(".").glob("*"))
                if not files:
                    await status_msg.edit_text("❌ دانلود ناموفق بود. فایل پیدا نشد.")
                    return

                video_path = max(files, key=lambda p: p.stat().st_size)  # بزرگ‌ترین فایل
                file_size = video_path.stat().st_size

                # بررسی محدودیت حجم تلگرام (بات معمولی ≈ ۵۰ مگ)
                if file_size > 49 * 1024 * 1024:
                    await status_msg.edit_text(
                        f"❌ **فایل خیلی بزرگ است**\n\n"
                        f"حجم فایل: `{human_size(file_size)}`\n"
                        f"حداکثر مجاز بات: حدود ۴۹ مگابایت\n\n"
                        f"لطفاً کیفیت پایین‌تر (۴۸۰p یا ۳۶۰p) را انتخاب کنید.",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    return

                # مرحله آپلود
                await status_msg.edit_text(
                    f"📤 **در حال آپلود به تلگرام...**\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"🎬 `{title}`\n"
                    f"📦 اندازه نهایی: `{human_size(file_size)}`\n"
                    f"📐 رزولوشن: `{resolution}`\n\n"
                    f"{progress_bar(10)} `در حال ارسال فایل...`\n"
                    f"لطفاً صبر کنید، این مرحله ممکن است کمی طول بکشد.",
                    parse_mode=ParseMode.MARKDOWN,
                )

                await context.bot.send_chat_action(
                    chat_id=query.message.chat_id,
                    action=ChatAction.UPLOAD_VIDEO
                )

                # آپلود ویدیو
                with open(video_path, "rb") as video_file:
                    await context.bot.send_video(
                        chat_id=query.message.chat_id,
                        video=video_file,
                        caption=(
                            f"🔥 **{title}**\n\n"
                            f"📐 `{resolution}` | ⏱ `{duration_str}` | 📦 `{human_size(file_size)}`"
                        ),
                        supports_streaming=True,
                        width=width,
                        height=height,
                        duration=int(duration) if duration else None,
                        read_timeout=180,
                        write_timeout=180,
                        connect_timeout=60,
                        pool_timeout=60,
                    )

                # پیام موفقیت نهایی
                await status_msg.edit_text(
                    f"✅ **آپلود با موفقیت انجام شد!**\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"🎬 `{title}`\n"
                    f"📐 رزولوشن: `{resolution}`\n"
                    f"⏱ مدت زمان: `{duration_str}`\n"
                    f"📦 حجم نهایی: `{human_size(file_size)}`\n\n"
                    f"ویدیو در چت ارسال شد.",
                    parse_mode=ParseMode.MARKDOWN,
                )

        except yt_dlp.utils.DownloadError as e:
            error_msg = str(e)[:400]
            await status_msg.edit_text(
                f"❌ **خطا در دانلود**\n\n`{error_msg}`\n\n"
                f"ممکن است ویدیو خصوصی باشد، لینک اشتباه باشد یا نیاز به کوکی داشته باشد.",
                parse_mode=ParseMode.MARKDOWN,
            )
            logger.error(f"DownloadError: {e}")

        except Exception as e:
            error_msg = str(e)[:400]
            await status_msg.edit_text(
                f"❌ **خطای غیرمنتظره**\n\n`{error_msg}`",
                parse_mode=ParseMode.MARKDOWN,
            )
            logger.exception("Unexpected error in quality_callback")

        finally:
            os.chdir(original_cwd)


# -------------------- اجرای بات --------------------
def main():
    if not TOKEN or TOKEN.strip() == "" or TOKEN == "توکن_بات_تو_را_اینجا_بگذار":
        print("❌ خطا: توکن بات تنظیم نشده است.")
        print("۱. فایل .env بسازید و بنویسید: BOT_TOKEN=xxxx")
        print("۲. یا مستقیماً داخل کد جایگزین کنید.")
        return

    app = (
        Application.builder()
        .token(TOKEN)
        .read_timeout(60)
        .write_timeout(60)
        .connect_timeout(30)
        .pool_timeout(30)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & \~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(quality_callback))

    print("✅ بات با موفقیت راه‌اندازی شد و در حال اجراست...")
    print("برای توقف: Ctrl + C")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()