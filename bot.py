import os
import html
import sqlite3

from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    ChatMemberUpdated,
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


# ============================================================
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")
ADMIN_ID = os.getenv("ADMIN_ID")

FOLDER_EMPLOYEE = os.getenv("FOLDER_EMPLOYEE", "")
FOLDER_MANAGER = os.getenv("FOLDER_MANAGER", "")
FOLDER_DIRECTOR = os.getenv("FOLDER_DIRECTOR", "")

# Через запятую:
# -1001234567890,-1009876543210
CORPORATE_CHAT_IDS_RAW = os.getenv("CORPORATE_CHAT_IDS", "")


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

if not WEBHOOK_URL:
    raise RuntimeError("WEBHOOK_URL is not set")


bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ============================================================
# БАЗА
# ============================================================

DB_FILE = "bot.db"


def db():
    connection = sqlite3.connect(DB_FILE)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    connection = db()

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS employees (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT NOT NULL,
            phone TEXT,
            role TEXT,
            status TEXT NOT NULL DEFAULT 'pending'
        )
        """
    )

    connection.commit()
    connection.close()


def save_pending_user(
    telegram_id: int,
    username: str | None,
    full_name: str,
    phone: str
):
    connection = db()

    connection.execute(
        """
        INSERT INTO employees (
            telegram_id,
            username,
            full_name,
            phone,
            role,
            status
        )
        VALUES (?, ?, ?, ?, NULL, 'pending')

        ON CONFLICT(telegram_id)
        DO UPDATE SET
            username = excluded.username,
            full_name = excluded.full_name,
            phone = excluded.phone,
            role = NULL,
            status = 'pending'
        """,
        (
            telegram_id,
            username,
            full_name,
            phone,
        ),
    )

    connection.commit()
    connection.close()


def approve_user(
    telegram_id: int,
    role: str
):
    connection = db()

    connection.execute(
        """
        UPDATE employees
        SET
            role = ?,
            status = 'active'
        WHERE telegram_id = ?
        """,
        (
            role,
            telegram_id,
        ),
    )

    connection.commit()
    connection.close()


def reject_user(
    telegram_id: int
):
    connection = db()

    connection.execute(
        """
        UPDATE employees
        SET status = 'rejected'
        WHERE telegram_id = ?
        """,
        (telegram_id,),
    )

    connection.commit()
    connection.close()


def get_user(
    telegram_id: int
):
    connection = db()

    row = connection.execute(
        """
        SELECT *
        FROM employees
        WHERE telegram_id = ?
        """,
        (telegram_id,),
    ).fetchone()

    connection.close()

    return row


# ============================================================
# ТЕГ
# ============================================================

def make_member_tag(full_name: str) -> str:

    full_name = " ".join(
        full_name.strip().split()
    )

    # Если ФИО помещается
    if len(full_name) <= 16:
        return full_name

    parts = full_name.split()

    # Ожидаем:
    # Имя Фамилия
    if len(parts) >= 2:

        first_name = parts[0]
        last_name = parts[-1]

        tag = (
            f"{last_name} "
            f"{first_name[0]}."
        )

        if len(tag) <= 16:
            return tag

        # Если фамилия сама очень длинная
        # оставляем место под " И."
        max_last_name = 13

        return (
            f"{last_name[:max_last_name]} "
            f"{first_name[0]}."
        )

    return full_name[:16]


# ============================================================
# РОЛИ
# ============================================================

ROLE_NAMES = {
    "employee": "Сотрудник",
    "manager": "Менеджер",
    "director": "Управляющий",
}


def get_role_name(role: str) -> str:
    return ROLE_NAMES.get(
        role,
        role,
    )


def get_folder(role: str) -> str:

    folders = {
        "employee": FOLDER_EMPLOYEE,
        "manager": FOLDER_MANAGER,
        "director": FOLDER_DIRECTOR,
    }

    return folders.get(
        role,
        "",
    )


# ============================================================
# КОРПОРАТИВНЫЕ ЧАТЫ
# ============================================================

def corporate_chat_ids():

    result = []

    if not CORPORATE_CHAT_IDS_RAW:
        return result

    for value in CORPORATE_CHAT_IDS_RAW.split(","):

        value = value.strip()

        if not value:
            continue

        try:
            result.append(
                int(value)
            )

        except ValueError:
            pass

    return result


# ============================================================
# СОСТОЯНИЯ РЕГИСТРАЦИИ
# ============================================================

class Registration(StatesGroup):
    waiting_phone = State()
    waiting_name = State()


# ============================================================
# КЛАВИАТУРЫ
# ============================================================

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


def role_keyboard(
    user_id: int
):

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👤 Сотрудник",
                    callback_data=(
                        f"approve:employee:{user_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🧑‍💼 Менеджер",
                    callback_data=(
                        f"approve:manager:{user_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="👔 Управляющий",
                    callback_data=(
                        f"approve:director:{user_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=(
                        f"reject:{user_id}"
                    ),
                )
            ],
        ]
    )


# ============================================================
# /START
# ============================================================

@dp.message(CommandStart())
async def start(
    message: Message
):

    employee = get_user(
        message.from_user.id
    )

    if (
        employee
        and employee["status"] == "active"
    ):

        role = get_role_name(
            employee["role"]
        )

        await message.answer(
            "✅ Вы уже зарегистрированы.\n\n"
            f"ФИО: {employee['full_name']}\n"
            f"Роль: {role}"
        )

        return

    await message.answer(
        "👋 Добро пожаловать!\n\n"
        "Это бот доступа к рабочим чатам.\n\n"
        "Нажмите кнопку ниже для регистрации.",
        reply_markup=registration_keyboard(),
    )


# ============================================================
# СЛУЖЕБНЫЕ КОМАНДЫ
# ============================================================

@dp.message(Command("myid"))
async def my_id(
    message: Message
):

    await message.answer(
        "Ваш Telegram ID:\n\n"
        f"<code>{message.from_user.id}</code>",
        parse_mode="HTML",
    )


@dp.message(Command("chatid"))
async def chat_id(
    message: Message
):

    await message.answer(
        "ID этого чата:\n\n"
        f"<code>{message.chat.id}</code>",
        parse_mode="HTML",
    )


# ============================================================
# НАЧАЛО РЕГИСТРАЦИИ
# ============================================================

@dp.callback_query(
    F.data == "register"
)
async def register(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.answer()

    await state.set_state(
        Registration.waiting_phone
    )

    await callback.message.answer(
        "Для регистрации подтвердите "
        "ваш номер телефона.",
        reply_markup=phone_keyboard(),
    )


# ============================================================
# ПОЛУЧАЕМ ТЕЛЕФОН
# ============================================================

@dp.message(
    Registration.waiting_phone,
    F.contact,
)
async def receive_phone(
    message: Message,
    state: FSMContext,
):

    contact = message.contact

    if (
        contact.user_id
        != message.from_user.id
    ):

        await message.answer(
            "❌ Необходимо отправить "
            "именно свой номер телефона."
        )

        return

    await state.update_data(
        phone=contact.phone_number
    )

    await state.set_state(
        Registration.waiting_name
    )

    await message.answer(
        "Введите ваше имя и фамилию.\n\n"
        "Например:\n"
        "Илья Иванов",
        reply_markup=ReplyKeyboardRemove(),
    )


@dp.message(
    Registration.waiting_phone
)
async def wrong_phone(
    message: Message
):

    await message.answer(
        "Используйте кнопку "
        "«📱 Отправить мой номер»."
    )


# ============================================================
# ПОЛУЧАЕМ ФИО
# ============================================================

@dp.message(
    Registration.waiting_name
)
async def receive_name(
    message: Message,
    state: FSMContext,
):

    if not message.text:
        return

    full_name = (
        " ".join(
            message.text.strip().split()
        )
    )

    parts = full_name.split()

    if len(parts) < 2:

        await message.answer(
            "Введите имя и фамилию.\n\n"
            "Например:\n"
            "Илья Иванов"
        )

        return

    data = await state.get_data()

    phone = data["phone"]

    user_id = (
        message.from_user.id
    )

    save_pending_user(
        telegram_id=user_id,
        username=message.from_user.username,
        full_name=full_name,
        phone=phone,
    )

    await state.clear()

    await message.answer(
        "✅ Заявка на регистрацию отправлена.\n\n"
        "Ожидайте подтверждения управляющего."
    )

    if not ADMIN_ID:
        return

    username = (
        f"@{message.from_user.username}"
        if message.from_user.username
        else "не указан"
    )

    safe_name = html.escape(
        full_name
    )

    safe_username = html.escape(
        username
    )

    safe_phone = html.escape(
        phone
    )

    text = (
        "🆕 <b>Новая регистрация</b>\n\n"
        f"<b>ФИО:</b> {safe_name}\n"
        f"<b>Телефон:</b> {safe_phone}\n"
        f"<b>Telegram:</b> {safe_username}\n\n"
        "Подтвердите роль сотрудника:"
    )

    await bot.send_message(
        chat_id=int(ADMIN_ID),
        text=text,
        parse_mode="HTML",
        reply_markup=role_keyboard(
            user_id
        ),
    )


# ============================================================
# АВТОМАТИЧЕСКАЯ УСТАНОВКА ТЕГА
# ============================================================

async def set_employee_tag(
    chat_id: int,
    telegram_id: int,
):

    employee = get_user(
        telegram_id
    )

    if not employee:
        return False

    if employee["status"] != "active":
        return False

    full_name = employee["full_name"]

    tag = make_member_tag(
        full_name
    )

    try:

        await bot.set_chat_member_tag(
            chat_id=chat_id,
            user_id=telegram_id,
            tag=tag,
        )

        return True

    except Exception as error:

        print(
            "TAG ERROR:",
            chat_id,
            telegram_id,
            str(error),
        )

        return False


# ============================================================
# ПРОВЕРКА УЖЕ СУЩЕСТВУЮЩИХ ЧАТОВ
# ============================================================

async def tag_existing_memberships(
    telegram_id: int
):

    for chat_id in corporate_chat_ids():

        try:

            member = await bot.get_chat_member(
                chat_id=chat_id,
                user_id=telegram_id,
            )

            if member.status in {
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.RESTRICTED,
            }:

                await set_employee_tag(
                    chat_id,
                    telegram_id,
                )

        except Exception as error:

            print(
                "MEMBERSHIP CHECK ERROR:",
                chat_id,
                telegram_id,
                str(error),
            )


# ============================================================
# УПРАВЛЯЮЩИЙ ПОДТВЕРЖДАЕТ РОЛЬ
# ============================================================

@dp.callback_query(
    F.data.startswith("approve:")
)
async def approve(
    callback: CallbackQuery
):

    if not ADMIN_ID:

        await callback.answer(
            "ADMIN_ID не настроен.",
            show_alert=True,
        )

        return

    if (
        callback.from_user.id
        != int(ADMIN_ID)
    ):

        await callback.answer(
            "У вас нет доступа.",
            show_alert=True,
        )

        return

    parts = callback.data.split(":")

    role = parts[1]
    user_id = int(parts[2])

    employee = get_user(
        user_id
    )

    if not employee:

        await callback.answer(
            "Сотрудник не найден.",
            show_alert=True,
        )

        return

    approve_user(
        telegram_id=user_id,
        role=role,
    )

    readable_role = (
        get_role_name(role)
    )

    await callback.answer(
        "Регистрация подтверждена"
    )

    original = (
        callback.message.html_text
        or callback.message.text
    )

    await callback.message.edit_text(
        original
        + "\n\n"
        + "✅ <b>Подтверждено</b>\n"
        + f"<b>Роль:</b> "
        + html.escape(readable_role),
        parse_mode="HTML",
    )

    folder = get_folder(
        role
    )

    if folder:

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=(
                            "📁 Добавить "
                            "рабочие чаты"
                        ),
                        url=folder,
                    )
                ]
            ]
        )

        await bot.send_message(
            chat_id=user_id,
            text=(
                "✅ <b>Регистрация подтверждена</b>\n\n"
                f"Ваша роль: "
                f"<b>{html.escape(readable_role)}</b>\n\n"
                "Нажмите кнопку ниже "
                "и добавьте рабочие чаты."
            ),
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    else:

        await bot.send_message(
            chat_id=user_id,
            text=(
                "✅ Регистрация подтверждена.\n\n"
                f"Ваша роль: {readable_role}\n\n"
                "Ссылка на рабочие чаты "
                "пока не настроена."
            ),
        )

    # Если человек уже состоит
    # в каком-то корпоративном чате,
    # ставим тег сразу.

    await tag_existing_memberships(
        user_id
    )


# ============================================================
# ОТКЛОНЕНИЕ
# ============================================================

@dp.callback_query(
    F.data.startswith("reject:")
)
async def reject(
    callback: CallbackQuery
):

    if not ADMIN_ID:
        return

    if (
        callback.from_user.id
        != int(ADMIN_ID)
    ):

        await callback.answer(
            "У вас нет доступа.",
            show_alert=True,
        )

        return

    parts = callback.data.split(":")

    user_id = int(parts[1])

    reject_user(
        user_id
    )

    await callback.answer(
        "Заявка отклонена"
    )

    await callback.message.edit_text(
        callback.message.html_text
        + "\n\n"
        + "❌ <b>Заявка отклонена</b>",
        parse_mode="HTML",
    )

    await bot.send_message(
        chat_id=user_id,
        text=(
            "❌ Регистрация не подтверждена.\n\n"
            "Обратитесь к управляющему."
        ),
    )


# ============================================================
# ЧЕЛОВЕК ВСТУПИЛ В ГРУППУ
# ============================================================

@dp.chat_member()
async def member_changed(
    event: ChatMemberUpdated
):

    user = (
        event.new_chat_member.user
    )

    # Ботов не обрабатываем
    if user.is_bot:
        return

    new_status = (
        event.new_chat_member.status
    )

    old_status = (
        event.old_chat_member.status
    )

    # Нас интересует именно появление
    # обычного участника в группе
    joined = (
        old_status
        in {
            ChatMemberStatus.LEFT,
            ChatMemberStatus.KICKED,
        }
        and
        new_status
        in {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.RESTRICTED,
        }
    )

    if not joined:
        return

    # Проверяем:
    # зарегистрирован ли он
    # и подтверждён ли управляющим

    employee = get_user(
        user.id
    )

    if not employee:
        return

    if employee["status"] != "active":
        return

    # Автоматически ставим ФИО

    await set_employee_tag(
        chat_id=event.chat.id,
        telegram_id=user.id,
    )


# ============================================================
# WEBHOOK
# ============================================================

async def on_startup(
    bot: Bot
):

    # chat_member ОБЯЗАТЕЛЬНО указываем,
    # иначе Telegram не будет
    # присылать события вступления.

    await bot.set_webhook(
        url=f"{WEBHOOK_URL}/webhook",
        allowed_updates=[
            "message",
            "callback_query",
            "chat_member",
        ],
    )


async def health(
    request
):

    return web.Response(
        text="OK"
    )


app = web.Application()

app.router.add_get(
    "/",
    health,
)


SimpleRequestHandler(
    dispatcher=dp,
    bot=bot,
).register(
    app,
    path="/webhook",
)


dp.startup.register(
    on_startup
)


setup_application(
    app,
    dp,
    bot=bot,
)


# ============================================================
# ЗАПУСК
# ============================================================

if __name__ == "__main__":

    init_db()

    port = int(
        os.getenv(
            "PORT",
            "10000",
        )
    )

    web.run_app(
        app,
        host="0.0.0.0",
        port=port,
    )
