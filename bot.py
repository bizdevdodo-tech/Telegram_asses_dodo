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


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")
ADMIN_ID_RAW = os.getenv("ADMIN_ID")

# Пока одна тестовая ссылка для ВСЕХ подтвержденных сотрудников
GROUP_INVITE_URL = "https://t.me/+MB8rCDcCRJ5kOTMy"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

if not WEBHOOK_URL:
    raise RuntimeError("WEBHOOK_URL is not set")

if not ADMIN_ID_RAW:
    raise RuntimeError("ADMIN_ID is not set")

SUPERADMIN_ID = int(ADMIN_ID_RAW)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DB_FILE = "bot.db"

PIZZERIAS = [
    "1-1", "1-2", "1-3",
    "1-4", "1-5", "1-6",
    "1-7", "1-8", "1-9",
]

ROLE_NAMES = {
    "employee": "Сотрудник",
    "manager": "Менеджер",
    "director": "Управляющий",
}


# =========================================================
# DATABASE
# =========================================================

def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS employees (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT NOT NULL,
            phone TEXT,
            pizzeria TEXT,
            role TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            approved_by INTEGER
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chats (
            chat_id INTEGER PRIMARY KEY,
            title TEXT
        )
        """
    )

    conn.commit()
    conn.close()


def save_employee(
    telegram_id,
    username,
    full_name,
    phone,
    pizzeria=None,
    role=None,
    status="pending",
):
    conn = db()

    conn.execute(
        """
        INSERT INTO employees (
            telegram_id,
            username,
            full_name,
            phone,
            pizzeria,
            role,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)

        ON CONFLICT(telegram_id)
        DO UPDATE SET
            username = excluded.username,
            full_name = excluded.full_name,
            phone = excluded.phone,
            pizzeria = excluded.pizzeria,
            role = excluded.role,
            status = excluded.status
        """,
        (
            telegram_id,
            username,
            full_name,
            phone,
            pizzeria,
            role,
            status,
        ),
    )

    conn.commit()
    conn.close()


def get_employee(user_id):
    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM employees
        WHERE telegram_id = ?
        """,
        (user_id,),
    ).fetchone()

    conn.close()
    return row


def set_employee_pizzeria(user_id, pizzeria):
    conn = db()

    conn.execute(
        """
        UPDATE employees
        SET pizzeria = ?
        WHERE telegram_id = ?
        """,
        (pizzeria, user_id),
    )

    conn.commit()
    conn.close()


def set_employee_role(user_id, role):
    conn = db()

    conn.execute(
        """
        UPDATE employees
        SET role = ?
        WHERE telegram_id = ?
        """,
        (role, user_id),
    )

    conn.commit()
    conn.close()


def activate_employee(user_id, approved_by):
    conn = db()

    conn.execute(
        """
        UPDATE employees
        SET
            status = 'active',
            approved_by = ?
        WHERE telegram_id = ?
        """,
        (approved_by, user_id),
    )

    conn.commit()
    conn.close()


def dismiss_employee(user_id):
    conn = db()

    conn.execute(
        """
        UPDATE employees
        SET status = 'dismissed'
        WHERE telegram_id = ?
        """,
        (user_id,),
    )

    conn.commit()
    conn.close()


def get_director(pizzeria):
    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM employees
        WHERE
            pizzeria = ?
            AND role = 'director'
            AND status = 'active'
        LIMIT 1
        """,
        (pizzeria,),
    ).fetchone()

    conn.close()
    return row


def get_people(pizzeria, role):
    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM employees
        WHERE
            pizzeria = ?
            AND role = ?
            AND status = 'active'
        ORDER BY full_name
        """,
        (pizzeria, role),
    ).fetchall()

    conn.close()
    return rows


def save_chat(chat_id, title):
    conn = db()

    conn.execute(
        """
        INSERT INTO chats (
            chat_id,
            title
        )
        VALUES (?, ?)

        ON CONFLICT(chat_id)
        DO UPDATE SET
            title = excluded.title
        """,
        (chat_id, title),
    )

    conn.commit()
    conn.close()


def get_chats():
    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM chats
        ORDER BY title
        """
    ).fetchall()

    conn.close()
    return rows


# =========================================================
# HELPERS
# =========================================================

def safe(value):
    return html.escape(str(value or ""))


def role_name(role):
    return ROLE_NAMES.get(role, role)


def make_member_tag(full_name):
    full_name = " ".join(full_name.strip().split())

    if len(full_name) <= 16:
        return full_name

    parts = full_name.split()

    if len(parts) >= 2:
        first_name = parts[0]
        last_name = parts[-1]

        tag = f"{last_name} {first_name[0]}."

        if len(tag) <= 16:
            return tag

        # 13 + пробел + буква + точка = 16
        return f"{last_name[:13]} {first_name[0]}."

    return full_name[:16]


async def apply_employee_tag(chat_id, user_id):
    employee = get_employee(user_id)

    if not employee:
        return False

    if employee["status"] != "active":
        return False

    tag = make_member_tag(employee["full_name"])

    try:
        await bot.set_chat_member_tag(
            chat_id=chat_id,
            user_id=user_id,
            tag=tag,
        )
        return True

    except Exception as error:
        print(
            "TAG ERROR:",
            chat_id,
            user_id,
            repr(error),
        )
        return False


async def send_access(user_id):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📁 Вступить в рабочие группы",
                    url=GROUP_INVITE_URL,
                )
            ]
        ]
    )

    employee = get_employee(user_id)

    await bot.send_message(
        user_id,
        (
            "✅ <b>Регистрация подтверждена</b>\n\n"
            f"Пиццерия: <b>{safe(employee['pizzeria'])}</b>\n"
            f"Должность: <b>{safe(role_name(employee['role']))}</b>\n\n"
            "Нажмите кнопку ниже для вступления "
            "в рабочие группы."
        ),
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def remove_from_all_chats(user_id):
    removed = 0
    errors = 0

    for chat in get_chats():
        try:
            member = await bot.get_chat_member(
                chat_id=chat["chat_id"],
                user_id=user_id,
            )

            if member.status in {
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.RESTRICTED,
            }:
                await bot.ban_chat_member(
                    chat_id=chat["chat_id"],
                    user_id=user_id,
                )

                # Сразу снимаем бан.
                # Человек удален, но не заблокирован навечно.
                await bot.unban_chat_member(
                    chat_id=chat["chat_id"],
                    user_id=user_id,
                    only_if_banned=True,
                )

                removed += 1

        except Exception as error:
            errors += 1
            print(
                "REMOVE ERROR:",
                chat["chat_id"],
                user_id,
                repr(error),
            )

    return removed, errors


# =========================================================
# STATES
# =========================================================

class Registration(StatesGroup):
    waiting_phone = State()
    waiting_name = State()
    waiting_director_answer = State()
    waiting_pizzeria = State()
    waiting_role = State()


# =========================================================
# KEYBOARDS
# =========================================================

def register_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📝 Зарегистрироваться",
                    callback_data="reg:start",
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


def director_question_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👔 Да",
                    callback_data="reg:director_yes",
                ),
                InlineKeyboardButton(
                    text="Нет",
                    callback_data="reg:director_no",
                ),
            ]
        ]
    )


def pizzeria_keyboard(prefix, user_id=None):
    rows = []

    for i in range(0, len(PIZZERIAS), 3):
        row = []

        for pizzeria in PIZZERIAS[i:i + 3]:
            if user_id is None:
                callback = f"{prefix}:{pizzeria}"
            else:
                callback = f"{prefix}:{user_id}:{pizzeria}"

            row.append(
                InlineKeyboardButton(
                    text=pizzeria,
                    callback_data=callback,
                )
            )

        rows.append(row)

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def employee_role_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👤 Сотрудник",
                    callback_data="reg:role:employee",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🧑‍💼 Менеджер",
                    callback_data="reg:role:manager",
                )
            ],
        ]
    )


def approval_keyboard(user_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить",
                    callback_data=f"approve:{user_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"reject:{user_id}",
                ),
            ]
        ]
    )


def replace_director_keyboard(
    new_user_id,
    old_user_id,
    pizzeria,
):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, заменить",
                    callback_data=(
                        f"director_replace:"
                        f"{new_user_id}:"
                        f"{old_user_id}:"
                        f"{pizzeria}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Нет",
                    callback_data=f"director_replace_cancel:{new_user_id}",
                )
            ],
        ]
    )


def superadmin_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Уволить",
                    callback_data="menu:dismiss",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 Сотрудники",
                    callback_data="menu:employees",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👔 Управляющие",
                    callback_data="menu:directors",
                )
            ],
        ]
    )


def dismiss_role_keyboard(pizzeria):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👤 Сотрудники",
                    callback_data=f"dismiss_role:{pizzeria}:employee",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🧑‍💼 Менеджеры",
                    callback_data=f"dismiss_role:{pizzeria}:manager",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👔 Управляющий",
                    callback_data=f"dismiss_role:{pizzeria}:director",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data="menu:dismiss",
                )
            ],
        ]
    )


def confirm_dismiss_keyboard(user_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔥 Да, уволить",
                    callback_data=f"dismiss_confirm:{user_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Отмена",
                    callback_data="menu:main",
                )
            ],
        ]
    )


# =========================================================
# START
# =========================================================

@dp.message(CommandStart())
async def start(message: Message):
    employee = get_employee(message.from_user.id)

    if employee and employee["status"] == "active":
        await message.answer(
            (
                "✅ Вы зарегистрированы.\n\n"
                f"ФИО: {employee['full_name']}\n"
                f"Пиццерия: {employee['pizzeria']}\n"
                f"Должность: {role_name(employee['role'])}"
            )
        )
        return

    await message.answer(
        (
            "👋 Добро пожаловать.\n\n"
            "Это бот регистрации и доступа "
            "к рабочим Telegram-группам."
        ),
        reply_markup=register_keyboard(),
    )


# =========================================================
# SUPERADMIN MENU
# =========================================================

@dp.message(Command("menu"))
async def menu_command(message: Message):
    if message.from_user.id != SUPERADMIN_ID:
        return

    await message.answer(
        "⚙️ <b>Меню супер-администратора</b>",
        parse_mode="HTML",
        reply_markup=superadmin_menu(),
    )


@dp.message(Command("меню"))
async def menu_command_ru(message: Message):
    if message.from_user.id != SUPERADMIN_ID:
        return

    await message.answer(
        "⚙️ <b>Меню супер-администратора</b>",
        parse_mode="HTML",
        reply_markup=superadmin_menu(),
    )


@dp.callback_query(F.data == "menu:main")
async def menu_main(callback: CallbackQuery):
    if callback.from_user.id != SUPERADMIN_ID:
        return

    await callback.answer()

    await callback.message.edit_text(
        "⚙️ <b>Меню супер-администратора</b>",
        parse_mode="HTML",
        reply_markup=superadmin_menu(),
    )


# =========================================================
# REGISTRATION
# =========================================================

@dp.callback_query(F.data == "reg:start")
async def reg_start(
    callback: CallbackQuery,
    state: FSMContext,
):
    await callback.answer()

    await state.set_state(
        Registration.waiting_phone
    )

    await callback.message.answer(
        "Отправьте свой номер телефона.",
        reply_markup=phone_keyboard(),
    )


@dp.message(
    Registration.waiting_phone,
    F.contact,
)
async def reg_phone(
    message: Message,
    state: FSMContext,
):
    if message.contact.user_id != message.from_user.id:
        await message.answer(
            "❌ Необходимо отправить именно свой контакт."
        )
        return

    await state.update_data(
        phone=message.contact.phone_number
    )

    await state.set_state(
        Registration.waiting_name
    )

    await message.answer(
        (
            "Введите имя и фамилию.\n\n"
            "Например:\n"
            "Илья Иванов"
        ),
        reply_markup=ReplyKeyboardRemove(),
    )


@dp.message(Registration.waiting_phone)
async def reg_phone_wrong(message: Message):
    await message.answer(
        "Нажмите кнопку «📱 Отправить мой номер»."
    )


@dp.message(Registration.waiting_name)
async def reg_name(
    message: Message,
    state: FSMContext,
):
    if not message.text:
        return

    full_name = " ".join(
        message.text.strip().split()
    )

    if len(full_name.split()) < 2:
        await message.answer(
            "❌ Введите имя и фамилию."
        )
        return

    await state.update_data(
        full_name=full_name
    )

    await state.set_state(
        Registration.waiting_director_answer
    )

    await message.answer(
        "Вы являетесь управляющим пиццерии?",
        reply_markup=director_question_keyboard(),
    )


# =========================================================
# REGISTRATION: DIRECTOR
# =========================================================

@dp.callback_query(
    Registration.waiting_director_answer,
    F.data == "reg:director_yes",
)
async def reg_director_yes(
    callback: CallbackQuery,
    state: FSMContext,
):
    await callback.answer()

    data = await state.get_data()

    user_id = callback.from_user.id

    save_employee(
        telegram_id=user_id,
        username=callback.from_user.username,
        full_name=data["full_name"],
        phone=data["phone"],
        role="director",
        status="pending",
    )

    await state.clear()

    await callback.message.edit_text(
        (
            "✅ Заявка отправлена.\n\n"
            "Ожидайте подтверждения "
            "супер-администратора."
        )
    )

    employee = get_employee(user_id)

    await bot.send_message(
        SUPERADMIN_ID,
        (
            "👔 <b>Регистрация управляющего</b>\n\n"
            f"<b>ФИО:</b> {safe(employee['full_name'])}\n"
            f"<b>Телефон:</b> {safe(employee['phone'])}\n\n"
            "Выберите пиццерию:"
        ),
        parse_mode="HTML",
        reply_markup=pizzeria_keyboard(
            "director_pizzeria",
            user_id,
        ),
    )


@dp.callback_query(
    F.data.startswith("director_pizzeria:")
)
async def director_choose_pizzeria(
    callback: CallbackQuery,
):
    if callback.from_user.id != SUPERADMIN_ID:
        return

    _, user_id_raw, pizzeria = callback.data.split(":")

    user_id = int(user_id_raw)

    employee = get_employee(user_id)

    if not employee or employee["status"] != "pending":
        await callback.answer(
            "Заявка уже обработана.",
            show_alert=True,
        )
        return

    current = get_director(pizzeria)

    if current and current["telegram_id"] != user_id:
        await callback.answer()

        await callback.message.answer(
            (
                f"⚠️ <b>В {pizzeria} уже есть управляющий.</b>\n\n"
                f"Сейчас: <b>{safe(current['full_name'])}</b>\n"
                f"Новый: <b>{safe(employee['full_name'])}</b>\n\n"
                "Удалить предыдущего управляющего "
                "и назначить нового?"
            ),
            parse_mode="HTML",
            reply_markup=replace_director_keyboard(
                user_id,
                current["telegram_id"],
                pizzeria,
            ),
        )
        return

    set_employee_pizzeria(
        user_id,
        pizzeria,
    )

    activate_employee(
        user_id,
        SUPERADMIN_ID,
    )

    await callback.answer(
        "Управляющий назначен"
    )

    await callback.message.edit_text(
        (
            "✅ <b>Управляющий назначен</b>\n\n"
            f"{safe(employee['full_name'])}\n"
            f"Пиццерия: <b>{pizzeria}</b>"
        ),
        parse_mode="HTML",
    )

    await send_access(user_id)


@dp.callback_query(
    F.data.startswith("director_replace:")
)
async def director_replace(callback: CallbackQuery):
    if callback.from_user.id != SUPERADMIN_ID:
        return

    (
        _,
        new_user_raw,
        old_user_raw,
        pizzeria,
    ) = callback.data.split(":")

    new_user_id = int(new_user_raw)
    old_user_id = int(old_user_raw)

    old_employee = get_employee(old_user_id)
    new_employee = get_employee(new_user_id)

    if not new_employee:
        await callback.answer(
            "Новый сотрудник не найден.",
            show_alert=True,
        )
        return

    # Старый управляющий увольняется
    dismiss_employee(old_user_id)

    removed, errors = await remove_from_all_chats(
        old_user_id
    )

    # Новый назначается
    set_employee_pizzeria(
        new_user_id,
        pizzeria,
    )

    set_employee_role(
        new_user_id,
        "director",
    )

    activate_employee(
        new_user_id,
        SUPERADMIN_ID,
    )

    await callback.answer(
        "Управляющий заменен"
    )

    await callback.message.edit_text(
        (
            f"✅ <b>Управляющий {pizzeria} заменен</b>\n\n"
            f"Был: {safe(old_employee['full_name'])}\n"
            f"Стал: <b>{safe(new_employee['full_name'])}</b>\n\n"
            f"Предыдущий удален из групп: {removed}\n"
            f"Ошибок удаления: {errors}"
        ),
        parse_mode="HTML",
    )

    try:
        await bot.send_message(
            old_user_id,
            (
                "Ваш рабочий доступ прекращен.\n\n"
                f"Пиццерия: {pizzeria}"
            ),
        )
    except Exception:
        pass

    await send_access(new_user_id)


@dp.callback_query(
    F.data.startswith("director_replace_cancel:")
)
async def director_replace_cancel(
    callback: CallbackQuery,
):
    if callback.from_user.id != SUPERADMIN_ID:
        return

    await callback.answer("Отменено")

    await callback.message.edit_text(
        "❌ Назначение управляющего отменено."
    )


# =========================================================
# REGISTRATION: NOT DIRECTOR
# =========================================================

@dp.callback_query(
    Registration.waiting_director_answer,
    F.data == "reg:director_no",
)
async def reg_director_no(
    callback: CallbackQuery,
    state: FSMContext,
):
    await callback.answer()

    await state.set_state(
        Registration.waiting_pizzeria
    )

    await callback.message.edit_text(
        "Выберите вашу пиццерию:",
        reply_markup=pizzeria_keyboard(
            "reg_pizzeria"
        ),
    )


@dp.callback_query(
    Registration.waiting_pizzeria,
    F.data.startswith("reg_pizzeria:"),
)
async def reg_employee_pizzeria(
    callback: CallbackQuery,
    state: FSMContext,
):
    await callback.answer()

    pizzeria = callback.data.split(":")[1]

    await state.update_data(
        pizzeria=pizzeria
    )

    await state.set_state(
        Registration.waiting_role
    )

    await callback.message.edit_text(
        (
            f"Пиццерия: <b>{pizzeria}</b>\n\n"
            "Выберите должность:"
        ),
        parse_mode="HTML",
        reply_markup=employee_role_keyboard(),
    )


@dp.callback_query(
    Registration.waiting_role,
    F.data.startswith("reg:role:"),
)
async def reg_employee_role(
    callback: CallbackQuery,
    state: FSMContext,
):
    await callback.answer()

    role = callback.data.split(":")[2]

    data = await state.get_data()

    user_id = callback.from_user.id

    save_employee(
        telegram_id=user_id,
        username=callback.from_user.username,
        full_name=data["full_name"],
        phone=data["phone"],
        pizzeria=data["pizzeria"],
        role=role,
        status="pending",
    )

    await state.clear()

    await callback.message.edit_text(
        (
            "✅ Заявка на регистрацию отправлена.\n\n"
            "Ожидайте подтверждения."
        )
    )

    employee = get_employee(user_id)

    director = get_director(
        employee["pizzeria"]
    )

    # Если управляющий есть — заявка ему.
    # Если нет — супер-админу.
    if director:
        approver_id = director["telegram_id"]

        heading = (
            "🆕 <b>Новый сотрудник вашей пиццерии</b>"
        )

    else:
        approver_id = SUPERADMIN_ID

        heading = (
            "⚠️ <b>У пиццерии нет назначенного управляющего</b>"
        )

    await bot.send_message(
        approver_id,
        (
            f"{heading}\n\n"
            f"<b>Пиццерия:</b> {safe(employee['pizzeria'])}\n"
            f"<b>ФИО:</b> {safe(employee['full_name'])}\n"
            f"<b>Должность:</b> {safe(role_name(employee['role']))}\n"
            f"<b>Телефон:</b> {safe(employee['phone'])}\n\n"
            "Подтвердить регистрацию?"
        ),
        parse_mode="HTML",
        reply_markup=approval_keyboard(
            user_id
        ),
    )


# =========================================================
# APPROVE / REJECT EMPLOYEE
# =========================================================

@dp.callback_query(
    F.data.startswith("approve:")
)
async def approve_employee(
    callback: CallbackQuery,
):
    user_id = int(
        callback.data.split(":")[1]
    )

    employee = get_employee(user_id)

    if not employee:
        await callback.answer(
            "Сотрудник не найден.",
            show_alert=True,
        )
        return

    if employee["status"] != "pending":
        await callback.answer(
            "Заявка уже обработана.",
            show_alert=True,
        )
        return

    director = get_director(
        employee["pizzeria"]
    )

    allowed_ids = {SUPERADMIN_ID}

    if director:
        allowed_ids.add(
            director["telegram_id"]
        )

    if callback.from_user.id not in allowed_ids:
        await callback.answer(
            "У вас нет доступа.",
            show_alert=True,
        )
        return

    activate_employee(
        user_id,
        callback.from_user.id,
    )

    await callback.answer(
        "Сотрудник подтвержден"
    )

    await callback.message.edit_text(
        callback.message.html_text
        + "\n\n✅ <b>Регистрация подтверждена</b>",
        parse_mode="HTML",
    )

    await send_access(user_id)


@dp.callback_query(
    F.data.startswith("reject:")
)
async def reject_employee(
    callback: CallbackQuery,
):
    user_id = int(
        callback.data.split(":")[1]
    )

    employee = get_employee(user_id)

    if not employee:
        return

    director = get_director(
        employee["pizzeria"]
    )

    allowed_ids = {SUPERADMIN_ID}

    if director:
        allowed_ids.add(
            director["telegram_id"]
        )

    if callback.from_user.id not in allowed_ids:
        await callback.answer(
            "У вас нет доступа.",
            show_alert=True,
        )
        return

    dismiss_employee(user_id)

    await callback.answer(
        "Регистрация отклонена"
    )

    await callback.message.edit_text(
        callback.message.html_text
        + "\n\n❌ <b>Регистрация отклонена</b>",
        parse_mode="HTML",
    )

    try:
        await bot.send_message(
            user_id,
            (
                "❌ Регистрация не подтверждена.\n\n"
                "Обратитесь к управляющему."
            ),
        )
    except Exception:
        pass


# =========================================================
# SUPERADMIN: DISMISS
# =========================================================

@dp.callback_query(F.data == "menu:dismiss")
async def menu_dismiss(callback: CallbackQuery):
    if callback.from_user.id != SUPERADMIN_ID:
        return

    await callback.answer()

    await callback.message.edit_text(
        "❌ <b>Увольнение</b>\n\nВыберите пиццерию:",
        parse_mode="HTML",
        reply_markup=pizzeria_keyboard(
            "dismiss_pizzeria"
        ),
    )


@dp.callback_query(
    F.data.startswith("dismiss_pizzeria:")
)
async def dismiss_choose_pizzeria(
    callback: CallbackQuery,
):
    if callback.from_user.id != SUPERADMIN_ID:
        return

    await callback.answer()

    pizzeria = callback.data.split(":")[1]

    await callback.message.edit_text(
        (
            f"❌ <b>Увольнение</b>\n\n"
            f"Пиццерия: <b>{pizzeria}</b>\n\n"
            "Выберите должность:"
        ),
        parse_mode="HTML",
        reply_markup=dismiss_role_keyboard(
            pizzeria
        ),
    )


@dp.callback_query(
    F.data.startswith("dismiss_role:")
)
async def dismiss_choose_role(
    callback: CallbackQuery,
):
    if callback.from_user.id != SUPERADMIN_ID:
        return

    _, pizzeria, role = callback.data.split(":")

    people = get_people(
        pizzeria,
        role,
    )

    await callback.answer()

    if not people:
        await callback.message.edit_text(
            (
                f"В {pizzeria} нет активных сотрудников "
                f"с должностью «{role_name(role)}»."
            ),
            reply_markup=dismiss_role_keyboard(
                pizzeria
            ),
        )
        return

    rows = []

    for person in people:
        rows.append(
            [
                InlineKeyboardButton(
                    text=person["full_name"],
                    callback_data=(
                        f"dismiss_person:"
                        f"{person['telegram_id']}"
                    ),
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=f"dismiss_pizzeria:{pizzeria}",
            )
        ]
    )

    await callback.message.edit_text(
        (
            f"❌ <b>Увольнение</b>\n\n"
            f"Пиццерия: <b>{pizzeria}</b>\n"
            f"Должность: <b>{safe(role_name(role))}</b>\n\n"
            "Выберите сотрудника:"
        ),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=rows
        ),
    )


@dp.callback_query(
    F.data.startswith("dismiss_person:")
)
async def dismiss_choose_person(
    callback: CallbackQuery,
):
    if callback.from_user.id != SUPERADMIN_ID:
        return

    user_id = int(
        callback.data.split(":")[1]
    )

    employee = get_employee(user_id)

    if not employee:
        await callback.answer(
            "Сотрудник не найден.",
            show_alert=True,
        )
        return

    await callback.answer()

    await callback.message.edit_text(
        (
            "⚠️ <b>Точно уволить?</b>\n\n"
            f"<b>{safe(employee['full_name'])}</b>\n"
            f"Пиццерия: {safe(employee['pizzeria'])}\n"
            f"Должность: {safe(role_name(employee['role']))}\n\n"
            "После подтверждения сотрудник будет "
            "удален из известных боту рабочих групп."
        ),
        parse_mode="HTML",
        reply_markup=confirm_dismiss_keyboard(
            user_id
        ),
    )


@dp.callback_query(
    F.data.startswith("dismiss_confirm:")
)
async def dismiss_confirm(
    callback: CallbackQuery,
):
    if callback.from_user.id != SUPERADMIN_ID:
        return

    user_id = int(
        callback.data.split(":")[1]
    )

    employee = get_employee(user_id)

    if not employee:
        await callback.answer(
            "Сотрудник не найден.",
            show_alert=True,
        )
        return

    if employee["status"] != "active":
        await callback.answer(
            "Сотрудник уже не активен.",
            show_alert=True,
        )
        return

    dismiss_employee(user_id)

    removed, errors = await remove_from_all_chats(
        user_id
    )

    await callback.answer(
        "Сотрудник уволен"
    )

    await callback.message.edit_text(
        (
            "✅ <b>Сотрудник уволен</b>\n\n"
            f"{safe(employee['full_name'])}\n"
            f"Пиццерия: {safe(employee['pizzeria'])}\n"
            f"Должность: {safe(role_name(employee['role']))}\n\n"
            f"Удален из групп: <b>{removed}</b>\n"
            f"Ошибок: <b>{errors}</b>"
        ),
        parse_mode="HTML",
        reply_markup=superadmin_menu(),
    )

    try:
        await bot.send_message(
            user_id,
            "Ваш рабочий доступ прекращен."
        )
    except Exception:
        pass


# =========================================================
# SUPERADMIN: VIEW EMPLOYEES
# =========================================================

@dp.callback_query(F.data == "menu:employees")
async def menu_employees(callback: CallbackQuery):
    if callback.from_user.id != SUPERADMIN_ID:
        return

    await callback.answer()

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM employees
        WHERE status = 'active'
        ORDER BY pizzeria, role, full_name
        """
    ).fetchall()

    conn.close()

    if not rows:
        text = "👥 Активных сотрудников пока нет."

    else:
        lines = ["👥 <b>Активные сотрудники</b>\n"]

        current_pizzeria = None

        for person in rows:
            if person["pizzeria"] != current_pizzeria:
                current_pizzeria = person["pizzeria"]

                lines.append(
                    f"\n<b>{safe(current_pizzeria)}</b>"
                )

            lines.append(
                "• "
                f"{safe(person['full_name'])} — "
                f"{safe(role_name(person['role']))}"
            )

        text = "\n".join(lines)

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=superadmin_menu(),
    )


# =========================================================
# SUPERADMIN: DIRECTORS
# =========================================================

@dp.callback_query(F.data == "menu:directors")
async def menu_directors(callback: CallbackQuery):
    if callback.from_user.id != SUPERADMIN_ID:
        return

    await callback.answer()

    lines = [
        "👔 <b>Управляющие</b>\n"
    ]

    for pizzeria in PIZZERIAS:
        director = get_director(pizzeria)

        if director:
            lines.append(
                f"<b>{pizzeria}</b> — "
                f"{safe(director['full_name'])}"
            )
        else:
            lines.append(
                f"<b>{pizzeria}</b> — не назначен"
            )

    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=superadmin_menu(),
    )


# =========================================================
# CHAT MEMBER EVENTS
# =========================================================

@dp.chat_member()
async def chat_member_changed(
    event: ChatMemberUpdated,
):
    # Запоминаем группу автоматически.
    save_chat(
        event.chat.id,
        event.chat.title or str(event.chat.id),
    )

    user = event.new_chat_member.user

    if user.is_bot:
        return

    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status

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

    employee = get_employee(user.id)

    # Неизвестного человека пока не трогаем.
    if not employee:
        return

    # Уволенный сотрудник снова вошел —
    # сразу удаляем из группы.
    if employee["status"] == "dismissed":
        try:
            await bot.ban_chat_member(
                chat_id=event.chat.id,
                user_id=user.id,
            )

            await bot.unban_chat_member(
                chat_id=event.chat.id,
                user_id=user.id,
                only_if_banned=True,
            )

        except Exception as error:
            print(
                "DISMISSED REJOIN ERROR:",
                event.chat.id,
                user.id,
                repr(error),
            )

        return

    if employee["status"] != "active":
        return

    # Активный сотрудник —
    # автоматически ставим ФИО-тег.
    await apply_employee_tag(
        event.chat.id,
        user.id,
    )


# =========================================================
# UTILITIES
# =========================================================

@dp.message(Command("myid"))
async def myid(message: Message):
    await message.answer(
        f"Ваш Telegram ID:\n<code>{message.from_user.id}</code>",
        parse_mode="HTML",
    )


@dp.message(Command("chatid"))
async def chatid(message: Message):
    await message.answer(
        f"ID чата:\n<code>{message.chat.id}</code>",
        parse_mode="HTML",
    )


# =========================================================
# WEBHOOK
# =========================================================

async def on_startup(bot: Bot):
    await bot.set_webhook(
        url=f"{WEBHOOK_URL}/webhook",
        allowed_updates=[
            "message",
            "callback_query",
            "chat_member",
        ],
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


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    init_db()

    port = int(
        os.getenv("PORT", "10000")
    )

    web.run_app(
        app,
        host="0.0.0.0",
        port=port,
    )
