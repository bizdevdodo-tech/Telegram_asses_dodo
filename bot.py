import os

from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from aiogram.webhook.aiohttp_server import (
    SimpleRequestHandler,
    setup_application,
)


BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")
ADMIN_ID = os.getenv("ADMIN_ID")

FOLDER_EMPLOYEE = os.getenv("FOLDER_EMPLOYEE")
FOLDER_MANAGER = os.getenv("FOLDER_MANAGER")
FOLDER_DIRECTOR = os.getenv("FOLDER_DIRECTOR")


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

if not WEBHOOK_URL:
    raise RuntimeError("WEBHOOK_URL is not set")


bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Для теста заявки храним в памяти.
# После перезапуска сервера они очистятся.
pending_users = {}


class Registration(StatesGroup):
    waiting_phone = State()
    waiting_name = State()


def registration_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📝 Зарегистрироваться",
                    callback_data="register",
                )
            ]
        ]
    )


def phone_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="📱 Отправить мой номер",
                    request_contact=True,
                )
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def role_keyboard(user_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👤 Сотрудник",
                    callback_data=f"approve:employee:{user_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🧑‍💼 Менеджер",
                    callback_data=f"approve:manager:{user_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👔 Управляющий",
                    callback_data=f"approve:director:{user_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"reject:{user_id}",
                )
            ],
        ]
    )


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "👋 Добро пожаловать!\n\n"
        "Здесь вы можете получить доступ к рабочим чатам.\n\n"
        "Для начала пройдите регистрацию.",
        reply_markup=registration_keyboard(),
    )


@dp.message(Command("myid"))
async def my_id(message: Message):
    await message.answer(
        f"Ваш Telegram ID:\n\n"
        f"<code>{message.from_user.id}</code>",
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "register")
async def register(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    await state.set_state(Registration.waiting_phone)

    await callback.message.answer(
        "Отправьте номер телефона кнопкой ниже.",
        reply_markup=phone_keyboard(),
    )


@dp.message(Registration.waiting_phone, F.contact)
async def get_phone(message: Message, state: FSMContext):
    # Нельзя отправить чужой контакт вместо своего
    if message.contact.user_id != message.from_user.id:
        await message.answer(
            "Пожалуйста, отправьте именно свой номер телефона."
        )
        return

    await state.update_data(
        phone=message.contact.phone_number
    )

    await state.set_state(Registration.waiting_name)

    await message.answer(
        "Введите имя и фамилию.\n\n"
        "Например: Илья Иванов",
        reply_markup=ReplyKeyboardRemove(),
    )


@dp.message(Registration.waiting_phone)
async def phone_wrong(message: Message):
    await message.answer(
        "Нажмите кнопку «📱 Отправить мой номер»."
    )


@dp.message(Registration.waiting_name)
async def get_name(message: Message, state: FSMContext):
    full_name = message.text.strip()

    if len(full_name) < 3:
        await message.answer(
            "Введите имя и фамилию текстом."
        )
        return

    data = await state.get_data()
    phone = data["phone"]

    user_id = message.from_user.id

    pending_users[user_id] = {
        "telegram_id": user_id,
        "username": message.from_user.username,
        "full_name": full_name,
        "phone": phone,
    }

    await state.clear()

    await message.answer(
        "✅ Регистрация отправлена.\n\n"
        "Ожидайте подтверждения управляющего."
    )

    if not ADMIN_ID:
        await message.answer(
            "⚠️ Для теста администратор пока не настроен."
        )
        return

    username = (
        f"@{message.from_user.username}"
        if message.from_user.username
        else "не указан"
    )

    admin_text = (
        "🆕 <b>Новая регистрация</b>\n\n"
        f"<b>ФИО:</b> {full_name}\n"
        f"<b>Телефон:</b> {phone}\n"
        f"<b>Telegram:</b> {username}\n"
        f"<b>ID:</b> <code>{user_id}</code>\n\n"
        "Выберите роль этого человека:"
    )

    await bot.send_message(
        chat_id=int(ADMIN_ID),
        text=admin_text,
        parse_mode="HTML",
        reply_markup=role_keyboard(user_id),
    )


def get_folder(role: str):
    folders = {
        "employee": FOLDER_EMPLOYEE,
        "manager": FOLDER_MANAGER,
        "director": FOLDER_DIRECTOR,
    }
    return folders.get(role)


def role_name(role: str):
    roles = {
        "employee": "Сотрудник",
        "manager": "Менеджер",
        "director": "Управляющий",
    }
    return roles.get(role, role)


@dp.callback_query(F.data.startswith("approve:"))
async def approve(callback: CallbackQuery):
    if not ADMIN_ID or callback.from_user.id != int(ADMIN_ID):
        await callback.answer(
            "У вас нет доступа.",
            show_alert=True,
        )
        return

    _, role, user_id_text = callback.data.split(":")
    user_id = int(user_id_text)

    employee = pending_users.get(user_id)

    if not employee:
        await callback.answer(
            "Заявка уже обработана или потеряна после перезапуска.",
            show_alert=True,
        )
        return

    folder_url = get_folder(role)
    readable_role = role_name(role)

    await callback.answer("Сотрудник подтверждён")

    await callback.message.edit_text(
        callback.message.text
        + f"\n\n✅ <b>Подтверждено</b>\n"
        + f"Роль: {readable_role}",
        parse_mode="HTML",
    )

    text = (
        "✅ <b>Регистрация подтверждена</b>\n\n"
        f"Ваша роль: <b>{readable_role}</b>\n\n"
    )

    if folder_url:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📁 Добавить рабочие чаты",
                        url=folder_url,
                    )
                ]
            ]
        )

        text += "Нажмите кнопку ниже, чтобы добавить рабочие чаты."

        await bot.send_message(
            user_id,
            text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    else:
        text += (
            "Ссылка на рабочую папку для этой роли "
            "пока не настроена."
        )

        await bot.send_message(
            user_id,
            text,
            parse_mode="HTML",
        )

    pending_users.pop(user_id, None)


@dp.callback_query(F.data.startswith("reject:"))
async def reject(callback: CallbackQuery):
    if not ADMIN_ID or callback.from_user.id != int(ADMIN_ID):
        await callback.answer(
            "У вас нет доступа.",
            show_alert=True,
        )
        return

    _, user_id_text = callback.data.split(":")
    user_id = int(user_id_text)

    await callback.answer("Заявка отклонена")

    await callback.message.edit_text(
        callback.message.text
        + "\n\n❌ <b>Заявка отклонена</b>",
        parse_mode="HTML",
    )

    await bot.send_message(
        user_id,
        "❌ Регистрация не подтверждена.\n\n"
        "Обратитесь к управляющему."
    )

    pending_users.pop(user_id, None)


async def on_startup(bot: Bot):
    await bot.set_webhook(
        f"{WEBHOOK_URL}/webhook"
    )


async def health(request):
    return web.Response(text="OK")


app = web.Application()

app.router.add_get("/", health)

SimpleRequestHandler(
    dispatcher=dp,
    bot=bot,
).register(
    app,
    path="/webhook",
)

dp.startup.register(on_startup)

setup_application(
    app,
    dp,
    bot=bot,
)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))

    web.run_app(
        app,
        host="0.0.0.0",
        port=port,
    )
