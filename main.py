import json
import logging
import os
from html import escape

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
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
    )


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Привет! 👋\n\n"
        "Я помогу оформить объявление о продаже автомобиля.\n"
        "Нажмите кнопку ниже, заполните форму — и объявление уйдёт на модерацию.",
        reply_markup=webapp_keyboard(),
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

    try:
        await bot.send_message(
            ADMIN_CHAT_ID,
            listing + sender_line,
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Failed to send listing to admin")
        await message.answer(
            "Не удалось отправить объявление на модерацию. Попробуйте позже."
        )
        return

    await message.answer("✅ Ваше объявление успешно отправлено на модерацию!")


async def main() -> None:
    logger.info("Bot starting…")
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
