import asyncio
import json
import logging
import os
import uuid
from html import escape
from pathlib import Path

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    InputMediaDocument,
    InputMediaPhoto,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
WEBAPP_URL = os.getenv("WEBAPP_URL")
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8080"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in .env")
if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID is not set in .env")
if not WEBAPP_URL:
    raise RuntimeError(
        "WEBAPP_URL is not set in .env "
        "(public HTTPS URL to index.html, required for Mini App)"
    )

try:
    ADMIN_CHAT_ID = int(ADMIN_ID)
except ValueError as exc:
    raise RuntimeError("ADMIN_ID must be a numeric Telegram user/chat id") from exc

if not PUBLIC_BASE_URL:
    # Derive public origin from WEBAPP_URL (…/index.html → origin)
    from urllib.parse import urlsplit

    parts = urlsplit(WEBAPP_URL)
    PUBLIC_BASE_URL = f"{parts.scheme}://{parts.netloc}"

ROOT = Path(__file__).resolve().parent
UPLOAD_DIR = ROOT / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_PHOTO_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/jpg",
    "image/heic",
    "image/heif",
    "application/octet-stream",
}
MAX_PHOTOS = 10
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def webapp_keyboard() -> ReplyKeyboardMarkup:
    # sendData() works only for Mini Apps opened via ReplyKeyboard WebApp button
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="🚗 Создать объявление",
                    web_app=WebAppInfo(url=WEBAPP_URL),
                )
            ]
        ],
        resize_keyboard=True,
    )


def _val(data: dict, key: str, default: str = "—") -> str:
    value = data.get(key)
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def format_listing(data: dict, username: str | None) -> str:
    contact = _val(data, "contacts")
    username_part = f"@{username}" if username else "—"
    if contact != "—":
        contacts_line = f"{escape(contact)} | {escape(username_part)}"
    else:
        contacts_line = escape(username_part)

    bargain = _val(data, "bargain")
    price = _val(data, "price")
    price_line = f"{escape(price)} CZK"
    if bargain != "—":
        price_line += f" · Торг/обмен: {escape(bargain)}"

    photos = data.get("photos") or []
    photo_count = len(photos) if isinstance(photos, list) else 0
    photos_line = f"\n🖼 Фото: {photo_count} шт." if photo_count else ""

    return (
        f"🚗 <b>{escape(_val(data, 'brand_model'))}</b>\n"
        f"📅 Год: {escape(_val(data, 'year'))}\n"
        f"💰 Цена: {price_line}\n"
        f"📍 Локация: {escape(_val(data, 'location'))}\n"
        f"🛣 Пробег: {escape(_val(data, 'mileage'))}\n\n"
        f"⚙️ <b>Двигатель и трансмиссия</b>\n"
        f"• Двигатель: {escape(_val(data, 'engine'))}\n"
        f"• Объем/модификация: {escape(_val(data, 'engine_mod'))}\n"
        f"• Мощность: {escape(_val(data, 'power'))}\n"
        f"• КПП: {escape(_val(data, 'transmission'))}\n"
        f"• Привод: {escape(_val(data, 'drive'))}\n"
        f"• Кузов: {escape(_val(data, 'body'))}\n"
        f"• Цвет: {escape(_val(data, 'color'))}\n\n"
        f"🛠 <b>Состояние и обслуживание</b>\n"
        f"• STK до: {escape(_val(data, 'stk'))}\n"
        f"• Состояние: {escape(_val(data, 'condition'))}\n"
        f"• История: {escape(_val(data, 'history'))}\n"
        f"• Обслуживание: {escape(_val(data, 'service'))}\n\n"
        f"✨ <b>Комплектация</b>\n{escape(_val(data, 'equipment'))}\n\n"
        f"➕ <b>Дополнительно</b>\n{escape(_val(data, 'extra'))}\n\n"
        f"📞 <b>Контакты</b>\n{contacts_line}"
        f"{photos_line}"
    )


def resolve_photo_paths(photo_ids: list) -> list[Path]:
    paths: list[Path] = []
    for item in photo_ids[:MAX_PHOTOS]:
        name = str(item).strip()
        if not name or "/" in name or "\\" in name or ".." in name:
            continue
        path = UPLOAD_DIR / name
        if path.is_file():
            paths.append(path)
    return paths


async def send_listing_to_admin(listing_html: str, photo_paths: list[Path]) -> None:
    await bot.send_message(ADMIN_CHAT_ID, listing_html, parse_mode="HTML")
    if not photo_paths:
        return

    media: list[InputMediaPhoto] = []
    # Keep file handles open until send completes
    files = []
    try:
        for path in photo_paths:
            data = path.read_bytes()
            files.append(data)
            media.append(
                InputMediaPhoto(
                    media=BufferedInputFile(data, filename=path.name),
                )
            )
        # Telegram media groups are max 10
        await bot.send_media_group(ADMIN_CHAT_ID, media=media)
    finally:
        files.clear()


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else "—"
    is_admin = message.from_user and message.from_user.id == ADMIN_CHAT_ID
    admin_hint = (
        "✅ Вы админ — объявления будут приходить сюда."
        if is_admin
        else (
            f"ℹ️ Ваш Telegram ID: <code>{user_id}</code>\n"
            f"Сейчас ADMIN_ID в .env = <code>{ADMIN_CHAT_ID}</code>.\n"
            "Админ должен один раз нажать /start у этого бота, "
            "иначе модерация не дойдёт (ошибка chat not found)."
        )
    )
    await message.answer(
        "Привет! 👋\n\n"
        "Я помогу оформить объявление о продаже автомобиля.\n"
        "Нажмите кнопку ниже, заполните форму — и объявление уйдёт на модерацию.\n\n"
        f"{admin_hint}",
        reply_markup=webapp_keyboard(),
        parse_mode="HTML",
    )


@dp.message(Command("myid"))
async def cmd_myid(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else "—"
    await message.answer(
        f"Ваш Telegram ID: <code>{user_id}</code>\n"
        f"ADMIN_ID в боте: <code>{ADMIN_CHAT_ID}</code>",
        parse_mode="HTML",
    )


@dp.message(F.web_app_data)
async def handle_webapp_data(message: Message) -> None:
    try:
        data = json.loads(message.web_app_data.data)
        if not isinstance(data, dict):
            raise ValueError("Payload must be a JSON object")
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.exception("Failed to parse web_app_data")
        await message.answer(
            "Не удалось прочитать данные формы. Попробуйте отправить ещё раз."
        )
        return

    listing = format_listing(data, message.from_user.username if message.from_user else None)
    sender = message.from_user
    sender_line = (
        f"\n\n👤 Отправитель: "
        f"{escape(sender.full_name) if sender else '—'}"
        f" (id: {sender.id if sender else '—'})"
    )
    photo_ids = data.get("photos") if isinstance(data.get("photos"), list) else []
    photo_paths = resolve_photo_paths(photo_ids)

    try:
        await send_listing_to_admin(listing + sender_line, photo_paths)
    except TelegramBadRequest as exc:
        logger.exception("Failed to send listing to admin")
        await message.answer(
            "❌ Не удалось отправить объявление админу.\n\n"
            f"Причина Telegram: <code>{escape(exc.message)}</code>\n\n"
            f"ADMIN_ID сейчас: <code>{ADMIN_CHAT_ID}</code>\n"
            f"Ваш ID: <code>{sender.id if sender else '—'}</code>\n\n"
            "Что сделать:\n"
            "1) Админ должен открыть этого бота и нажать /start\n"
            "2) Проверить, что ADMIN_ID совпадает с ID из /myid у админа",
            parse_mode="HTML",
        )
        return
    except TelegramForbiddenError:
        logger.exception("Bot forbidden to message admin")
        await message.answer(
            "❌ Бот не может писать админу (заблокирован или нет диалога).\n"
            "Админ должен нажать /start у бота."
        )
        return
    except Exception:
        logger.exception("Failed to send listing to admin")
        await message.answer(
            "Не удалось отправить объявление на модерацию. Попробуйте позже."
        )
        return

    await message.answer("✅ Ваше объявление успешно отправлено на модерацию!")


def _guess_ext(filename: str, content_type: str, data: bytes) -> str:
    name = (filename or "").lower()
    ctype = (content_type or "").lower()
    if name.endswith(".png") or "png" in ctype:
        return ".png"
    if name.endswith(".webp") or "webp" in ctype:
        return ".webp"
    if name.endswith(".heic") or "heic" in ctype:
        return ".heic"
    if name.endswith(".heif") or "heif" in ctype:
        return ".heif"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    return ".jpg"


async def handle_upload(request: web.Request) -> web.Response:
    try:
        reader = await request.multipart()
    except Exception:
        logger.exception("Invalid multipart upload")
        return web.json_response({"ok": False, "error": "Некорректный запрос загрузки"}, status=400)

    saved: list[str] = []

    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name != "photos":
            continue
        if len(saved) >= MAX_PHOTOS:
            break

        content_type = (part.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        filename = part.filename or ""

        data = await part.read(decode=False)
        if not data:
            continue
        if len(data) > MAX_UPLOAD_BYTES:
            return web.json_response(
                {"ok": False, "error": "Файл слишком большой (макс. 8 МБ)"},
                status=400,
            )

        # Accept empty/unknown types from mobile browsers if payload looks like an image
        looks_like_image = (
            data[:3] == b"\xff\xd8\xff"
            or data[:8] == b"\x89PNG\r\n\x1a\n"
            or data[:4] == b"RIFF"
            or b"ftyp" in data[:32]
        )
        if content_type and content_type not in ALLOWED_PHOTO_TYPES and not looks_like_image:
            return web.json_response(
                {"ok": False, "error": f"Неподдерживаемый тип файла: {content_type}"},
                status=400,
            )

        ext = _guess_ext(filename, content_type, data)
        out_name = f"{uuid.uuid4().hex}{ext}"
        (UPLOAD_DIR / out_name).write_bytes(data)
        saved.append(out_name)
        logger.info("Uploaded photo %s (%s bytes)", out_name, len(data))

    if not saved:
        return web.json_response({"ok": False, "error": "Фото не получены"}, status=400)

    return web.json_response({"ok": True, "photos": saved})


async def handle_index(_: web.Request) -> web.FileResponse:
    return web.FileResponse(ROOT / "index.html")


def create_http_app() -> web.Application:
    app = web.Application(client_max_size=MAX_UPLOAD_BYTES * MAX_PHOTOS + 1024 * 1024)
    app.router.add_get("/", handle_index)
    app.router.add_get("/index.html", handle_index)
    app.router.add_post("/api/upload", handle_upload)
    app.router.add_static("/uploads/", path=str(UPLOAD_DIR), name="uploads")
    return app


async def main() -> None:
    app = create_http_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, HOST, PORT)
    await site.start()
    logger.info("HTTP server on http://%s:%s (public base %s)", HOST, PORT, PUBLIC_BASE_URL)
    logger.info("Bot starting…")
    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
