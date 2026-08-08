import os

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application


BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

if not WEBHOOK_URL:
    raise RuntimeError("WEBHOOK_URL is not set")


bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "👋 Добро пожаловать!\n\n"
        "Бот доступа к рабочим чатам работает.\n\n"
        "Следующий этап — регистрация сотрудника."
    )


async def on_startup(bot: Bot):
    await bot.set_webhook(f"{WEBHOOK_URL}/webhook")


async def health(request):
    return web.Response(text="OK")


app = web.Application()

app.router.add_get("/", health)

SimpleRequestHandler(
    dispatcher=dp,
    bot=bot,
).register(app, path="/webhook")

dp.startup.register(on_startup)

setup_application(app, dp, bot=bot)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    web.run_app(app, host="0.0.0.0", port=port)
