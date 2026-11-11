import asyncio
import logging
import html
import warnings
import re
import datetime
from typing import Any, Dict, List, Optional, Tuple

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
)

from telegram.constants import ChatMemberStatus, ParseMode

from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    PicklePersistence,
    filters,
)

from telegram.warnings import PTBUserWarning

# ----------------------------------------------------
# تنظیمات اولیه و پیکربندی ربات
# ----------------------------------------------------
TOKEN = "8981908056:AAH_M7k9HcW3n3ufvCuw7wk5suiTBG59wEs"  # توکن ربات شما

# نادیده گرفتن هشدارهای مزاحم PTB برای تمیز ماندن لاگ
warnings.filterwarnings("ignore", category=PTBUserWarning)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ----------------------------------------------------
# سیستم ذخیره‌سازی دائمی (Persistence)
# ----------------------------------------------------
def get_bot_config(context: ContextTypes.DEFAULT_TYPE) -> dict:
    if "config" not in context.bot_data:
        context.bot_data["config"] = {
            "channel_id": None,
            "channel_title": None,
            "spoiler_on": False,
        }
    return context.bot_data["config"]

# ----------------------------------------------------
# تعریف وضعیت‌ها (FSM States)
# ----------------------------------------------------
WAITING_FOR_CHANNEL = 0
WAITING_FOR_ACTOR = 1
WAITING_FOR_DOWNLOAD_LINK = 2
WAITING_FOR_NODE_CHOICE = 3
WAITING_FOR_NODE_LINK = 4
WAITING_FOR_MEDIA = 5
WAITING_FOR_SEND_CONFIRMATION = 6
WAITING_FOR_READY_POST = 7
WAITING_FOR_EDIT_READY_ACTOR = 8

# ----------------------------------------------------
# تنظیمات جمع‌آوری آلبوم (Media Group Cache)
# ----------------------------------------------------
_MEDIA_GROUP_DONE: set[Tuple[int, str]] = set()


def _clear_post_data(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in ("actor", "download_link", "node_link", "post_media", "final_caption", "last_preview_id", "flow"):
        context.user_data.pop(key, None)


def _set_flow(context: ContextTypes.DEFAULT_TYPE, flow_name: Optional[str]) -> None:
    if flow_name is None:
        context.user_data.pop("flow", None)
    else:
        context.user_data["flow"] = flow_name


async def _forget_media_group_later(key: Tuple[int, str], delay: float = 30.0) -> None:
    await asyncio.sleep(delay)
    _MEDIA_GROUP_DONE.discard(key)


# ----------------------------------------------------
# سیستم تراز هوشمند و ضد بهم ریختگی بنر (Anti-Break Banner System)
# ----------------------------------------------------
def build_banner_text(data: Dict[str, Any]) -> str:
    actor = html.escape(data.get("actor", ""))
    download_link = data.get("download_link", "#")
    node_link = data.get("node_link")

    # NBSP (Non-Breaking Space): فاصله‌ای که در موبایل جمع یا شکسته نمی‌شود
    NBSP = "\u00A0"

    banner = f"𝐴𝑐𝑡𝑜𝑟𝑠 : {actor}\n"
    banner += "卍━━━━━━━━━━━━━━━━━━━━卍\n\n"

    # بخش اول بنر (با استفاده از سیستم تراز میلی‌متری NBSP)
    banner += "<blockquote>"
    banner += "╭━◦•◉✿ Click to get it ✿◉•◦━╮\n"
    banner += "│\n"

    v_text = f"│{NBSP*2}●➜{NBSP*8}<a href=\"{download_link}\">𝑽𝒊𝒅𝒆𝒐</a>{NBSP*7}|{NBSP*9}"
    banner += f"{v_text}👈{NBSP}|♤\n"

    if node_link:
        banner += "│\n"
        n_text = f"│{NBSP*2}●➜{NBSP*8}<a href=\"{node_link}\">𝑵𝑶𝑫𝑬</a>{NBSP*8}|{NBSP*9}"
        banner += f"{n_text}👈{NBSP}|♤\n"

    banner += "│\n"
    banner += "╰━◦•◉✿ Click to get it ✿◉•◦━╯"
    banner += "</blockquote>\n\n"

    banner += "卍━━━━━━━━━━━━━━━━━━━━卍\n\n"

    # بخش پایینی بنر
    banner += "<blockquote>"
    banner += "┏━━━━━━━━━━━━━━━━┓卍\n"
    banner += f"┃{NBSP*2}<a href=\"https://t.me/onlyRQF\">𓆩♡𓆪 𝑨𝒍𝒍 𝑪𝒉𝒂𝒏𝒆𝒍 𓆩♡𓆪</a>{NBSP*2}┃\n"
    banner += "┗━━━━━━━━━━━━━━━━┛卍"
    banner += "</blockquote>"

    return banner


def build_settings_keyboard(context: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    config = get_bot_config(context)
    status_emoji = "🟢 روشن" if config["spoiler_on"] else "🔴 خاموش"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"اسپویلر مدیا: {status_emoji}", callback_data="toggle_spoiler")]
        ]
    )


def _build_media_group(media_data_list: List[Dict[str, str]], caption: str, spoiler_setting: bool):
    media_group = []
    for idx, item in enumerate(media_data_list):
        item_caption = caption if idx == 0 else None
        parse_mode = ParseMode.HTML if idx == 0 else None

        if item["type"] == "photo":
            media_group.append(
                InputMediaPhoto(
                    media=item["file_id"],
                    caption=item_caption,
                    parse_mode=parse_mode,
                    has_spoiler=spoiler_setting,
                )
            )
        elif item["type"] == "video":
            media_group.append(
                InputMediaVideo(
                    media=item["file_id"],
                    caption=item_caption,
                    parse_mode=parse_mode,
                    has_spoiler=spoiler_setting,
                )
            )
    return media_group


async def _send_preview_step(message, context: ContextTypes.DEFAULT_TYPE, media_data_list: List[Dict[str, str]]):
    user_data = context.user_data
    final_caption = build_banner_text(user_data)
    context.user_data["final_caption"] = final_caption

    await message.reply_text("👀 <b>مرحله پیش‌نمایش پست شما</b>\nدر حال تولید پیش‌نمایش آلبوم نهایی...", parse_mode=ParseMode.HTML)

    config = get_bot_config(context)
    spoiler_setting = config["spoiler_on"]
    preview_media_group = _build_media_group(media_data_list, final_caption, spoiler_setting)

    try:
        sent_messages = await context.bot.send_media_group(
            chat_id=message.chat_id,
            media=preview_media_group,
            reply_to_message_id=message.message_id,
        )
        context.user_data["last_preview_id"] = sent_messages[-1].message_id

        await context.bot.send_message(
            chat_id=message.chat_id,
            text=(
                "📝 <b>تایید و ارسال نهایی</b>\n\n"
                "پست بالا پیش‌نمایش کار شماست.\n"
                "برای ارسال این آلبوم به کانال ثبت شده، روی پیام بالا (یا همین پیام) <b>ریپلای</b> کنید و کلمه دقیق «<b>ارسال</b>» را بفرستید."
            ),
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message.message_id,
        )
        return WAITING_FOR_SEND_CONFIRMATION
    except Exception as e:
        await message.reply_text(f"❌ خطا در تولید پیش‌نمایش نهایی: {str(e)}\nعملیات لغو شد. مجدداً با دستورات مربوطه شروع کنید.")
        _clear_post_data(context)
        return ConversationHandler.END


# ----------------------------------------------------
# دستور عمومی انصراف (/cancel)
# ----------------------------------------------------
async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None:
        return ConversationHandler.END

    current_flow = context.user_data.get("flow")
    if current_flow is None:
        await message.reply_text("❌ هیچ عملیاتی برای لغو کردن وجود ندارد.")
        return ConversationHandler.END

    _clear_post_data(context)
    await message.reply_text("🔄 عملیات جاری لغو شد و ربات به حالت عادی بازگشت.")
    return ConversationHandler.END


# ----------------------------------------------------
# دستور /start و خوشامدگویی
# ----------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None:
        return

    help_text = (
        "👋 سلام! به ربات مدیریت و ساخت پست کانال خوش آمدید.\n\n"
        "📜 <b>لیست دستورات ربات:</b>\n"
        "🔹 /start - شروع کار با ربات و نمایش راهنما\n"
        "🔹 /channel - مدیریت و ثبت کانال مرجع\n"
        "🔹 /post - فرآیند مرحله به مرحله ساخت پست جدید\n"
        "🔹 /ready_post - ساخت پست جدید از روی بنرهای قدیمی (کپی بنر)\n"
        "🔹 /history - نمایش تاریخچه ۵ پست ارسال شده اخیر (سیستم حافظه)\n"
        "🔹 /settings - تنظیمات پیشرفته ربات (اسپویلر و ...)\n"
        "🔹 /cancel - لغو عملیات در هر مرحله از ساخت پست\n\n"
        "💡 برای شروع ابتدا از دستور /channel استفاده کنید تا کانال خود را به ربات معرفی کنید."
    )
    await message.reply_text(help_text, parse_mode=ParseMode.HTML)


# ----------------------------------------------------
# بخش مدیریت و ثبت کانال (/channel)
# ----------------------------------------------------
async def channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None:
        return ConversationHandler.END

    config = get_bot_config(context)

    if config["channel_id"] is None:
        await message.reply_text(
            "📢 <b>ثبت کانال جدید</b>\n\n"
            "هنوز هیچ کانالی در ربات ثبت نشده است.\n"
            "میتوانید به یکی از 3 روش زیر کانال را معرفی کنید:\n"
            "1️⃣ یک پیام از کانال <b>فوروارد</b> کنید.\n"
            "2️⃣ <b>یوزرنیم</b> یا <b>لینک</b> کانال را بفرستید (مثال: `channel_id@`)\n"
            "3️⃣ <b>آیدی عددی</b> کانال را ارسال کنید.",
            parse_mode=ParseMode.HTML
        )
        _set_flow(context, "channel")
        return WAITING_FOR_CHANNEL

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 ویرایش و تغییر کانال", callback_data="edit_channel")]
        ]
    )
    await message.reply_text(
        f"📢 <b>کانال ثبت شده فعلی:</b>\n"
        f"👤 نام کانال: {config['channel_title']}\n"
        f"🆔 آیدی عددی: <code>{config['channel_id']}</code>\n\n"
        f"⚠️ ویرایش این دستور تنها در این بخش قابل استفاده است.",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )
    return ConversationHandler.END


async def edit_channel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None or query.message is None:
        return WAITING_FOR_CHANNEL

    await query.answer()
    await query.message.edit_text(
        "🔄 <b>مراحل ویرایش کانال:</b>\n\n"
        "لطفاً آیدی عددی، لینک، یوزرنیم کانال را بفرستید و یا پستی از آن را به اینجا فوروارد کنید:",
        parse_mode=ParseMode.HTML,
    )
    _set_flow(context, "channel")
    return WAITING_FOR_CHANNEL


async def process_channel_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None:
        return WAITING_FOR_CHANNEL

    target_chat_id = None

    if getattr(message, "forward_origin", None) and hasattr(message.forward_origin, "chat"):
        target_chat_id = message.forward_origin.chat.id
    else:
        text_input = (message.text or "").strip()

        if text_input.startswith("https://t.me/"):
            text_input = "@" + text_input.replace("https://t.me/", "")

        if not text_input.startswith("@"):
            try:
                target_chat_id = int(text_input)
            except ValueError:
                pass

        if target_chat_id is None:
            if text_input.startswith("@"):
                target_chat_id = text_input
            else:
                await message.reply_text("❌ فرمت ارسالی نامعتبر است. لطفاً آیدی عددی، یوزرنیم (با @)، لینک کانال و یا یک پیام فورواردی بفرستید.")
                return WAITING_FOR_CHANNEL

    try:
        chat_info = await context.bot.get_chat(chat_id=target_chat_id)

        if chat_info.type != "channel":
            await message.reply_text("❌ آیدی وارد شده متعلق به یک گروه یا اکانت است! لطفاً فقط کانال معرفی کنید.")
            return WAITING_FOR_CHANNEL

        member = await context.bot.get_chat_member(chat_id=chat_info.id, user_id=context.bot.id)

        if member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
            if member.status == ChatMemberStatus.OWNER or getattr(member, "can_post_messages", False):
                config = get_bot_config(context)
                config["channel_id"] = chat_info.id
                config["channel_title"] = chat_info.title

                await message.reply_text(
                    f"✅ کانال با موفقیت تایید و ثبت شد!\n"
                    f"📢 <b>{chat_info.title}</b>\n"
                    f"🆔 آیدی دقیق کانال: <code>{chat_info.id}</code>\n\n"
                    f"💡 اکنون می‌توانید با دستور /post اقدام به ساخت پست کنید.",
                    parse_mode=ParseMode.HTML,
                )
                _set_flow(context, None)
                return ConversationHandler.END

            await message.reply_text("❌ ربات در این کانال ادمین است اما دسترسی «ارسال پیام / Post Messages» را ندارد. لطفاً دسترسی را فعال کرده و مجدد تلاش کنید.")
            return WAITING_FOR_CHANNEL

        await message.reply_text("❌ ربات در کانال ارسالی عضو یا ادمین نیست. لطفاً ابتدا ربات را در کانال ادمین کنید.")
        return WAITING_FOR_CHANNEL

    except Exception as e:
        error_msg = str(e).lower()
        if "not found" in error_msg:
            await message.reply_text("❌ کانال پیدا نشد! مطمئن شوید که آیدی/یوزرنیم کانال درست است و ربات در کانال ادمین شده است.")
        elif "not a member" in error_msg or "kicked" in error_msg:
            await message.reply_text("❌ ربات در کانال عضو نیست! ابتدا ربات را در کانال ادمین کنید و مجدداً تلاش کنید.")
        else:
            await message.reply_text(
                "❌ خطا در برقراری ارتباط با کانال!\n"
                "مطمئن شوید آیدی درست است و ربات در کانال ادمین شده باشد.\n"
                f"جزئیات خطا: {str(e)}"
            )
        return WAITING_FOR_CHANNEL


# ----------------------------------------------------
# بخش تنظیمات ربات (/settings)
# ----------------------------------------------------
async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None:
        return
    await message.reply_text("⚙️ <b>تنظیمات ربات مدیریت پست:</b>", reply_markup=build_settings_keyboard(context), parse_mode=ParseMode.HTML)


async def toggle_spoiler_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None or query.message is None:
        return

    config = get_bot_config(context)
    config["spoiler_on"] = not config["spoiler_on"]

    await query.answer("وضعیت اسپویلر تغییر کرد.")
    await query.message.edit_reply_markup(reply_markup=build_settings_keyboard(context))


# ----------------------------------------------------
# فرآیند ساخت پست آلبومی و مرحله‌ای (/post)
# ----------------------------------------------------
async def post_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None:
        return ConversationHandler.END

    config = get_bot_config(context)

    if config["channel_id"] is None:
        await message.reply_text("⚠️ ابتدا باید از طریق دستور /channel یک کانال برای ربات تعریف کنید.")
        return ConversationHandler.END

    try:
        member = await context.bot.get_chat_member(chat_id=config["channel_id"], user_id=context.bot.id)
        if member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER] or (
            member.status == ChatMemberStatus.ADMINISTRATOR and not getattr(member, "can_post_messages", False)
        ):
            await message.reply_text("⚠️ ربات دیگر در کانال ثبت شده دسترسی ادمین یا ارسال پیام ندارد. لطفاً وضعیت را بررسی کنید.")
            return ConversationHandler.END
    except Exception:
        await message.reply_text("⚠️ خطایی در تایید دسترسی کانال رخ داد. مطمئن شوید ربات هنوز در کانال ادمین است.")
        return ConversationHandler.END

    _clear_post_data(context)
    _set_flow(context, "post")
    context.user_data["post_media"] = []

    await message.reply_text("🎬 <b>مرحله اول: مشخص کردن شخصیت</b>\n\nلطفاً نام شخصیت (Actor) را ارسال کنید:", parse_mode=ParseMode.HTML)
    return WAITING_FOR_ACTOR


async def process_actor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None or not message.text:
        return WAITING_FOR_ACTOR

    actor_name = message.text.strip()
    if not actor_name.startswith("#"):
        actor_name = f"#{actor_name}"

    context.user_data["actor"] = actor_name

    await message.reply_text("📥 <b>مرحله دوم: لینک دانلود</b>\n\nلطفاً لینک یا متن حاوی لینک مربوط به بخش 𝑽𝒊𝒅𝒆𝒐 را ارسال کنید:", parse_mode=ParseMode.HTML)
    return WAITING_FOR_DOWNLOAD_LINK


async def process_download_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None or not message.text:
        return WAITING_FOR_DOWNLOAD_LINK

    text_input = message.text.strip()
    # استخراج هوشمند لینک از داخل متن با ریجکس
    match = re.search(r'(https?://[^\s"<>]+)', text_input)

    if not match:
        await message.reply_text("❌ هیچ لینک معتبری (شروع با http یا https) در متن شما یافت نشد. مجدداً ارسال کنید:")
        return WAITING_FOR_DOWNLOAD_LINK

    context.user_data["download_link"] = match.group(1)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="بله، می‌خواهم", callback_data="node_yes"),
                InlineKeyboardButton(text="خیر، حذف شود", callback_data="node_no"),
            ]
        ]
    )
    await message.reply_text("⚡ آیا مایلید برای قسمت 𝑵𝑶𝑫𝑬 هم لینکی تعیین کنید؟", reply_markup=kb)
    return WAITING_FOR_NODE_CHOICE


async def process_node_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None or query.message is None:
        return WAITING_FOR_NODE_CHOICE

    await query.answer()
    if query.data == "node_yes":
        await query.message.edit_text("⚡ لطفاً لینک یا متن حاوی لینک مربوط به بخش 𝑵𝑶𝑫𝑬 را ارسال کنید:")
        return WAITING_FOR_NODE_LINK

    context.user_data["node_link"] = None
    await query.message.edit_text(
        "📸 <b>مرحله سوم: دریافت مدیا (عکس یا ویدیو)</b>\n\n"
        "اکنون مدیاهای خود را (چه تکی و چه آلبومی) بفرستید.\n"
        "ربات به طور خودکار همه را در یک آلبوم جمع‌آوری می‌کند.\n\n"
        "⚠️ پس از ارسال آخرین مدیا، کلمه دقیق «<b>پایان</b>» را بفرستید تا ساخت پست شروع شود.",
        parse_mode=ParseMode.HTML,
    )
    return WAITING_FOR_MEDIA


async def process_node_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None or not message.text:
        return WAITING_FOR_NODE_LINK

    text_input = message.text.strip()
    match = re.search(r'(https?://[^\s"<>]+)', text_input)

    if not match:
        await message.reply_text("❌ لینک معتبری در متن شما پیدا نشد. مجدداً ارسال کنید:")
        return WAITING_FOR_NODE_LINK

    context.user_data["node_link"] = match.group(1)
    await message.reply_text(
        "📸 <b>مرحله سوم: دریافت مدیا (عکس یا ویدیو)</b>\n\n"
        "اکنون مدیاهای خود را (چه تکی و چه آلبومی) بفرستید.\n"
        "ربات به طور خودکار همه را در یک آلبوم جمع‌آوری می‌کند.\n\n"
        "⚠️ پس از ارسال آخرین مدیا، کلمه دقیق «<b>پایان</b>» را بفرستید تا ساخت پست شروع شود.",
        parse_mode=ParseMode.HTML,
    )
    return WAITING_FOR_MEDIA


# ----------------------------------------------------
# سیستم جدید دریافت مدیاها و جمع‌آوری به صورت یکجا
# ----------------------------------------------------
async def process_media_collection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None:
        return WAITING_FOR_MEDIA

    if "post_media" not in context.user_data:
        context.user_data["post_media"] = []

    # ذخیره فایل در حافظه موقت بسته به نوع آن
    if message.photo:
        context.user_data["post_media"].append({"type": "photo", "file_id": message.photo[-1].file_id})
    elif message.video:
        context.user_data["post_media"].append({"type": "video", "file_id": message.video.file_id})
    else:
        return WAITING_FOR_MEDIA

    # جلوگیری از ارسال پیام‌های رگباری هنگام دریافت البوم‌های تلگرام با مکانیزم تاخیر نیم ثانیه‌ای در نمایش تعداد
    if message.media_group_id:
        key = (message.chat_id, message.media_group_id)
        if key not in _MEDIA_GROUP_DONE:
            _MEDIA_GROUP_DONE.add(key)
            context.application.create_task(_forget_media_group_later(key))
            await asyncio.sleep(0.5)
            count = len(context.user_data["post_media"])
            await message.reply_text(f"📥 مدیا(ها) به آلبوم موقت اضافه شدند. (تعداد کل مدیاها تا الان: {count} عدد)\n✍️ همچنان بفرستید یا در صورت اتمام کار کلمه «<b>پایان</b>» را ارسال کنید.", parse_mode=ParseMode.HTML)
    else:
        count = len(context.user_data["post_media"])
        await message.reply_text(f"📥 مدیا اضافه شد. (تعداد کل مدیاها تا الان: {count} عدد)\n✍️ در صورت اتمام کار کلمه «<b>پایان</b>» را ارسال کنید.", parse_mode=ParseMode.HTML)

    return WAITING_FOR_MEDIA


async def finish_media_collection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None:
        return WAITING_FOR_MEDIA

    media_data_list = context.user_data.get("post_media", [])
    if not media_data_list:
        await message.reply_text("❌ شما هیچ مدیایی ارسال نکرده‌اید! لطفاً ابتدا مدیا بفرستید و سپس کلمه پایان را ارسال کنید.")
        return WAITING_FOR_MEDIA

    # هدایت به گام پیش‌نمایش آلبوم نهایی ساخته شده
    return await _send_preview_step(message, context, media_data_list)


# ----------------------------------------------------
# دستور جدید پردازش پست‌های آماده و کپی بنر (/ready_post)
# ----------------------------------------------------
async def ready_post_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None:
        return ConversationHandler.END

    config = get_bot_config(context)
    if config["channel_id"] is None:
        await message.reply_text("⚠️ ابتدا باید از طریق دستور /channel یک کانال برای ربات تعریف کنید.")
        return ConversationHandler.END

    _clear_post_data(context)
    _set_flow(context, "ready_post")
    context.user_data["post_media"] = []

    await message.reply_text(
        "📋 <b>حالت ساخت پست از روی بنر آماده</b>\n\n"
        "لطفاً متن بنر کامل قدیمی را ارسال کنید تا ربات مشخصات شخصیت، لینک ویدیو و لینک نود را به صورت خودکار از آن استخراج کند:",
        parse_mode=ParseMode.HTML
    )
    return WAITING_FOR_READY_POST


async def process_ready_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None or not message.text:
        return WAITING_FOR_READY_POST

    text = message.text

    # استخراج تمام لینک‌ها به جز لینک کانال اصلی ثبت شده در بنر
    urls = re.findall(r'(https?://[^\s"<>]+)', text)
    links = [u for u in urls if "publickinm" not in u]

    download_link = links[0] if len(links) > 0 else None
    node_link = links[1] if len(links) > 1 else None

    # استخراج شخصیت (Actor)
    actor = ""
    lines = text.split("\n")
    for line in lines:
        if "𝐴𝑐𝑡𝑜𝑟𝑠" in line or "Actors" in line or "شخصیت" in line:
            parts = line.split(":")
            if len(parts) > 1:
                actor = parts[1].strip()
                break

    if not actor and lines:
        first_line = lines[0]
        if ":" in first_line:
            actor = first_line.split(":", 1)[1].strip()
        else:
            actor = first_line.strip()

    if not actor or not download_link:
        await message.reply_text(
            "❌ <b>خطا در تحلیل بنر آماده!</b>\n"
            "ربات نتوانست اطلاعات پایه (شخصیت و لینک دانلود) را از متن ارسالی استخراج کند. "
            "مطمئن شوید فرمت بنر دستکاری نشده باشد و مجدداً ارسال کنید."
        )
        return WAITING_FOR_READY_POST

    # ذخیره داده‌های استخراج شده
    context.user_data["actor"] = actor if actor.startswith("#") else f"#{actor}"
    context.user_data["download_link"] = download_link
    context.user_data["node_link"] = node_link

    # قابلیت خلاقانه دکمه‌های شیشه‌ای تایید و ویرایش اطلاعات
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ اطلاعات درسته ➜ مرحله آپلود مدیا", callback_data="confirm_ready_fields")],
        [InlineKeyboardButton("✏️ ویرایش نام شخصیت (Actor)", callback_data="edit_ready_actor")],
        [InlineKeyboardButton("❌ لغو عملیات", callback_data="cancel_ready")]
    ])

    node_status = node_link if node_link else "🔴 ثبت نشده"
    await message.reply_text(
        f"🧠 <b>اطلاعات استخراج شده هوشمند بنر:</b>\n\n"
        f"👤 <b>شخصیت:</b> {context.user_data['actor']}\n"
        f"📥 <b>لینک ویدیو:</b> <code>{download_link}</code>\n"
        f"⚡ <b>لینک نود:</b> <code>{node_status}</code>\n\n"
        f"✨ آیا اطلاعات بالا تایید است؟ یکی از گزینه‌های زیر را انتخاب کنید:",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )
    return WAITING_FOR_READY_POST


async def ready_post_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None or query.message is None:
        return WAITING_FOR_READY_POST
    await query.answer()

    data = query.data
    if data == "confirm_ready_fields":
        await query.message.edit_text(
            "📸 <b>مرحله دریافت مدیا (آلبوم بنر آماده)</b>\n\n"
            "اکنون تمامی عکس‌ها و ویدیوهای خود را (چه تکی و چه گروهی) بفرستید.\n"
            "پس از اتمام ارسال، کلمه «<b>پایان</b>» را بنویسید.",
            parse_mode=ParseMode.HTML
        )
        return WAITING_FOR_MEDIA
    elif data == "edit_ready_actor":
        await query.message.edit_text("✍️ لطفاً نام جدید شخصیت (Actor) را ارسال کنید:")
        return WAITING_FOR_EDIT_READY_ACTOR
    elif data == "cancel_ready":
        _clear_post_data(context)
        await query.message.edit_text("🔄 عملیات پردازش پست آماده کپی لغو شد.")
        return ConversationHandler.END


async def process_edit_ready_actor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None or not message.text:
        return WAITING_FOR_EDIT_READY_ACTOR

    actor = message.text.strip()
    context.user_data["actor"] = actor if actor.startswith("#") else f"#{actor}"

    download_link = context.user_data["download_link"]
    node_link = context.user_data["node_link"]
    node_status = node_link if node_link else "🔴 ثبت نشده"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ اطلاعات درسته ➜ مرحله آپلود مدیا", callback_data="confirm_ready_fields")],
        [InlineKeyboardButton("✏️ ویرایش مجدد شخصیت", callback_data="edit_ready_actor")],
        [InlineKeyboardButton("❌ لغو عملیات", callback_data="cancel_ready")]
    ])

    await message.reply_text(
        f"🧠 <b>اطلاعات شخصیت با موفقیت بروز شد:</b>\n\n"
        f"👤 <b>شخصیت جدید:</b> {context.user_data['actor']}\n"
        f"📥 <b>لینک ویدیو:</b> <code>{download_link}</code>\n"
        f"⚡ <b>لینک نود:</b> <code>{node_status}</code>\n\n"
        f"✨ آیا اطلاعات مورد تایید است؟",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )
    return WAITING_FOR_READY_POST


# ----------------------------------------------------
# تایید نهایی ارسال پست و سیستم ثبت حافظه (Advanced Memory)
# ----------------------------------------------------
async def process_send_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None:
        return WAITING_FOR_SEND_CONFIRMATION

    if message.text and message.text.strip() == "ارسال":
        if not message.reply_to_message:
            await message.reply_text("❌ برای ارسال، شما باید حتماً روی پیام پیش‌نمایش یا پیام راهنمای بالا **ریپلای** کنید و عبارت «ارسال» را بنویسید.")
            return WAITING_FOR_SEND_CONFIRMATION

        user_data = context.user_data
        media_data_list = user_data.get("post_media", [])
        final_caption = user_data.get("final_caption", "")

        config = get_bot_config(context)
        spoiler_setting = config["spoiler_on"]
        channel_id = config["channel_id"]

        channel_media_group = _build_media_group(media_data_list, final_caption, spoiler_setting)

        try:
            await context.bot.send_media_group(chat_id=channel_id, media=channel_media_group)
            await message.reply_text(f"🚀 پست شما با موفقیت به کانال **{config['channel_title']}** ارسال شد!", parse_mode=ParseMode.HTML)

            # --- سیستم حافظه فوق پیشرفته و هوشمند ربات ---
            if "history_logs" not in context.user_data:
                context.user_data["history_logs"] = []

            log_entry = {
                "actor": user_data.get("actor"),
                "download_link": user_data.get("download_link"),
                "node_link": user_data.get("node_link"),
                "media_count": len(media_data_list),
                "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            context.user_data["history_logs"].append(log_entry)
            if len(context.user_data["history_logs"]) > 5:
                context.user_data["history_logs"].pop(0)  # نگه داشتن حداکثر ۵ مورد اخیر
            # -------------------------------------------

            _clear_post_data(context)
            _set_flow(context, None)
            return ConversationHandler.END
        except Exception as e:
            await message.reply_text(f"❌ متاسفانه هنگام ارسال پست به کانال خطایی رخ داد:\n{str(e)}")
            return WAITING_FOR_SEND_CONFIRMATION
    else:
        await message.reply_text("⚠️ عبارت ارسالی اشتباه است. اگر می‌خواهید پست ارسال شود باید کلمه «<b>ارسال</b>» را روی پیام ریپلای کنید. در غیر این صورت برای لغو فرآیند دستور /cancel را بفرستید.", parse_mode=ParseMode.HTML)
        return WAITING_FOR_SEND_CONFIRMATION


# دستور فراخوانی سیستم حافظه ربات
async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None:
        return

    logs = context.user_data.get("history_logs", [])
    if not logs:
        await message.reply_text("🗄 <b>حافظه ربات خالی است!</b>\nهنوز هیچ پستی از طریق ربات با این اکانت ارسال نشده است.", parse_mode=ParseMode.HTML)
        return

    text = "🗄 <b>تاریخچه ۵ پست اخیر ارسال شده شما (سیستم حافظه پیشرفته):</b>\n\n"
    for idx, log in enumerate(reversed(logs), 1):
        text += f"{idx}️⃣ 🎬 <b>شخصیت:</b> {log['actor']}\n"
        text += f"📅 <b>زمان ارسال:</b> {log['time']}\n"
        text += f"📦 <b>تعداد مدیا آلبوم:</b> {log['media_count']} عدد\n"
        text += f"🔗 <b>لینک ویدیو:</b> {log['download_link']}\n"
        if log['node_link']:
            text += f"⚡ <b>لینک نود:</b> {log['node_link']}\n"
        text += "•——————————————————•\n"

    await message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


# ----------------------------------------------------
# راه‌اندازی نهایی ربات (Main Entry Point)
# ----------------------------------------------------
def main() -> None:
    # راه‌اندازی دیتابیس لوکال (فایل bot_database.pickle در کنار سورس ساخته خواهد شد)
    persistence = PicklePersistence(filepath="bot_database.pickle")

    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .persistence(persistence)
        .concurrent_updates(True)
        .build()
    )

    conversation_handler = ConversationHandler(
        entry_points=[
            CommandHandler("channel", channel_command),
            CommandHandler("post", post_command),
            CommandHandler("ready_post", ready_post_command),
            CommandHandler("cancel", cancel_handler),
        ],
        states={
            WAITING_FOR_CHANNEL: [
                CallbackQueryHandler(edit_channel_callback, pattern="^edit_channel$"),
                MessageHandler(filters.ALL & ~filters.COMMAND, process_channel_registration)
            ],
            WAITING_FOR_ACTOR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_actor)
            ],
            WAITING_FOR_DOWNLOAD_LINK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_download_link)
            ],
            WAITING_FOR_NODE_CHOICE: [
                CallbackQueryHandler(process_node_choice, pattern="^node_")
            ],
            WAITING_FOR_NODE_LINK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_node_link)
            ],
            WAITING_FOR_MEDIA: [
                MessageHandler(filters.Regex("^(پایان)$") | filters.Command("done"), finish_media_collection),
                MessageHandler(filters.PHOTO | filters.VIDEO, process_media_collection)
            ],
            WAITING_FOR_SEND_CONFIRMATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_send_confirmation)
            ],
            WAITING_FOR_READY_POST: [
                CallbackQueryHandler(ready_post_callback, pattern="^(confirm_ready_fields|edit_ready_actor|cancel_ready)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_ready_post)
            ],
            WAITING_FOR_EDIT_READY_ACTOR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_edit_ready_actor)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)],
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CallbackQueryHandler(toggle_spoiler_callback, pattern="^toggle_spoiler$"))
    application.add_handler(conversation_handler)

    # رفع کامل نقص کات شدن بخش Polling
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()