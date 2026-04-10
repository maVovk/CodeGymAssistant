"""
Telegram bot for marking team exercises in Google Sheets.
Flow: /start -> city -> action -> exercise -> team (stay on team list).
"""

import logging
import os
from typing import Optional

from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from sheets_manager import (
    SheetsManager,
    TeamNotFoundException,
    ExerciseNotFoundException,
    GoogleSheetsAPIError,
    AuthenticationError,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

PARSE_MODE = "HTML"
KEY_SELECTED_EXERCISE = "selected_exercise"
KEY_EXERCISES = "exercises"
KEY_TEAMS = "teams"
KEY_ACTION_TYPE = "action_type"
KEY_CITY = "city"
KEY_LAST_RESULT = "last_result"

ACTION_CHECK = "check"
ACTION_UNCHECK = "uncheck"

CALLBACK_ACTION_CHECK = "action:check"
CALLBACK_ACTION_UNCHECK = "action:uncheck"
CALLBACK_ACTION_TOGGLE = "action:toggle"
CALLBACK_TEAM_PREFIX = "t:"
CALLBACK_EXERCISE_PREFIX = "e:"
CALLBACK_CITY_PREFIX = "city:"
CALLBACK_NAV_BACK_TO_EXERCISES = "nav:back_exercises"
CALLBACK_NAV_BACK_TO_ACTIONS = "nav:back_actions"
CALLBACK_NAV_BACK_TO_CITIES = "nav:back_cities"

KEY_IS_ADMIN = "is_admin"
KEY_ADMIN_CITY = "admin_city"
KEY_ADMIN_STATE = "admin_state"
KEY_ADMIN_EXERCISES = "admin_exercises"
KEY_ADMIN_EXERCISE_NAME = "admin_exercise_name"

ADMIN_AWAITING_PASSWORD = "awaiting_password"
ADMIN_AWAITING_NAME = "awaiting_name"
ADMIN_AWAITING_RENAME = "awaiting_rename"
KEY_ADMIN_PASSWORD_ATTEMPTS = "admin_pw_attempts"
ADMIN_MAX_PASSWORD_ATTEMPTS = 3

CALLBACK_ADMIN_CITY_PREFIX = "adm_city:"
CALLBACK_ADMIN_ADD = "adm:add"
CALLBACK_ADMIN_REMOVE = "adm:remove"
CALLBACK_ADMIN_RENAME = "adm:rename"
CALLBACK_ADMIN_EXERCISE_PREFIX = "adm_ex:"
CALLBACK_ADMIN_RENAME_EXERCISE_PREFIX = "adm_ren:"
CALLBACK_ADMIN_CONFIRM_DELETE = "adm:confirm_del"
CALLBACK_ADMIN_BACK_CITIES = "adm:back_cities"
CALLBACK_ADMIN_BACK_ACTIONS = "adm:back_actions"

CITIES_ENV = "CITIES"
N_EXERCISES_ENV = "N_EXERCISES"
EXERCISE_NAMES_ENV = "EXCLUDED_NAMES"
DEFAULT_EXCLUDED_NAMES = ["Сдано задач", "Разница"]


# ---------------------------------------------------------------------------
#  HTML escape
# ---------------------------------------------------------------------------

def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


    # ---------------------------------------------------------------------------
#  Message templates
# ---------------------------------------------------------------------------

def _action_label(action_type: str) -> str:
    if action_type == ACTION_UNCHECK:
        return "отменить зачёт"
    return "зачесть упражнение"


def msg_welcome() -> str:
    return (
        "<blockquote>"
        "<b>Трекер упражнений</b>"
        "</blockquote>\n"
        "\n"
        "Выберите ваш город:"
    )


def msg_action_select(city: str) -> str:
    return (
        "<blockquote>"
        f"<b>Город:</b>  {_esc(city)}"
        "</blockquote>\n"
        "\n"
        "Что необходимо сделать?"
    )


def msg_exercise_select(city: str, action_type: str) -> str:
    return (
        "<blockquote>"
        f"<b>Город:</b>  {_esc(city)}\n"
        f"<b>Действие:</b>  {_action_label(action_type)}"
        "</blockquote>\n"
        "\n"
        "Выберите упражнение:"
    )


def msg_team_select(
    city: str,
    action_type: str,
    exercise: str,
    last_result: Optional[str] = None,
) -> str:
    header = (
        "<blockquote>"
        f"<b>Город:</b>  {_esc(city)}\n"
        f"<b>Действие:</b>  {_action_label(action_type)}\n"
        f"<b>Упражнение:</b>  {_esc(exercise)}"
        "</blockquote>"
    )
    if last_result:
        return f"{header}\n\n<i>{_esc(last_result)}</i>"
    return f"{header}\n\nВыберите команду:"


def msg_error(text: str) -> str:
    return f"<b>Ошибка</b>\n\n{text}"


def msg_no_cities() -> str:
    return (
        "<b>Бот не настроен</b>\n"
        "\n"
        "Список городов не задан в конфигурации. Обратитесь к администратору."
    )


def msg_admin_welcome() -> str:
    return (
        "<blockquote>"
        "<b>Панель администратора</b>"
        "</blockquote>\n"
        "\n"
        "Выберите город:"
    )


def msg_admin_action(city: str, result: Optional[str] = None) -> str:
    header = (
        "<blockquote>"
        f"<b>Панель администратора</b>\n"
        f"<b>Город:</b>  {_esc(city)}"
        "</blockquote>"
    )
    if result:
        return f"{header}\n\n<i>{_esc(result)}</i>\n\nВыберите действие:"
    return f"{header}\n\nВыберите действие:"


def msg_admin_enter_name(city: str) -> str:
    return (
        "<blockquote>"
        f"<b>Панель администратора</b>\n"
        f"<b>Город:</b>  {_esc(city)}"
        "</blockquote>\n"
        "\n"
        "Введите название нового упражнения:"
    )


def msg_admin_select_exercise(city: str) -> str:
    return (
        "<blockquote>"
        f"<b>Панель администратора</b>\n"
        f"<b>Город:</b>  {_esc(city)}"
        "</blockquote>\n"
        "\n"
        "Выберите упражнение для удаления:"
    )


def msg_admin_select_exercise_rename(city: str) -> str:
    return (
        "<blockquote>"
        f"<b>Панель администратора</b>\n"
        f"<b>Город:</b>  {_esc(city)}"
        "</blockquote>\n"
        "\n"
        "Выберите упражнение для переименования:"
    )


def msg_admin_enter_new_name(city: str, exercise: str) -> str:
    return (
        "<blockquote>"
        f"<b>Панель администратора</b>\n"
        f"<b>Город:</b>  {_esc(city)}\n"
        f"<b>Упражнение:</b>  {_esc(exercise)}"
        "</blockquote>\n"
        "\n"
        "Введите новое название:"
    )


def msg_admin_confirm_delete(city: str, exercise: str) -> str:
    return (
        "<blockquote>"
        f"<b>Панель администратора</b>\n"
        f"<b>Город:</b>  {_esc(city)}\n"
        f"<b>Упражнение:</b>  {_esc(exercise)}"
        "</blockquote>\n"
        "\n"
        "Удалить это упражнение из таблицы?\n"
        "<i>Это действие необратимо.</i>"
    )


# ---------------------------------------------------------------------------
#  Keyboards
# ---------------------------------------------------------------------------

def _chunk_buttons(
    items: list[str],
    prefix: str,
    columns: int = 1,
    start_index: int = 0,
) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i, text in enumerate(items):
        row.append(InlineKeyboardButton(
            text, callback_data=f"{prefix}{start_index + i}",
        ))
        if len(row) >= columns:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows



def _toggle_button(action_type: str) -> InlineKeyboardButton:
    if action_type == ACTION_UNCHECK:
        label = "Переключить на: зачесть упражнение"
    else:
        label = "Переключить на: отменить зачёт"
    return InlineKeyboardButton(label, callback_data=CALLBACK_ACTION_TOGGLE)


def _city_keyboard(context: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    cities = context.application.bot_data.get("cities") or []
    keyboard = _chunk_buttons(cities, CALLBACK_CITY_PREFIX, columns=2)
    return InlineKeyboardMarkup(keyboard)


def _action_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Зачесть упражнение", callback_data=CALLBACK_ACTION_CHECK)],
        [InlineKeyboardButton("Отменить зачёт", callback_data=CALLBACK_ACTION_UNCHECK)],
        [InlineKeyboardButton("Сменить город", callback_data=CALLBACK_NAV_BACK_TO_CITIES)],
    ])


def _exercise_keyboard(
    exercises: list[str], action_type: str,
) -> InlineKeyboardMarkup:
    keyboard = _chunk_buttons(exercises, CALLBACK_EXERCISE_PREFIX, columns=1)
    keyboard.append([
        InlineKeyboardButton("Назад", callback_data=CALLBACK_NAV_BACK_TO_ACTIONS),
    ])
    keyboard.append([_toggle_button(action_type)])
    return InlineKeyboardMarkup(keyboard)


def _team_keyboard(
    teams: list[str], action_type: str,
) -> InlineKeyboardMarkup:
    keyboard = _chunk_buttons(teams, CALLBACK_TEAM_PREFIX, columns=1)
    keyboard.append([
        InlineKeyboardButton("Назад", callback_data=CALLBACK_NAV_BACK_TO_EXERCISES),
    ])
    keyboard.append([_toggle_button(action_type)])
    return InlineKeyboardMarkup(keyboard)


def _admin_city_keyboard(context: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    cities = context.application.bot_data.get("cities") or []
    keyboard = _chunk_buttons(cities, CALLBACK_ADMIN_CITY_PREFIX, columns=2)
    return InlineKeyboardMarkup(keyboard)


def _admin_action_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Добавить упражнение", callback_data=CALLBACK_ADMIN_ADD)],
        [InlineKeyboardButton("Переименовать упражнение", callback_data=CALLBACK_ADMIN_RENAME)],
        [InlineKeyboardButton("Удалить упражнение", callback_data=CALLBACK_ADMIN_REMOVE)],
        [InlineKeyboardButton("Назад", callback_data=CALLBACK_ADMIN_BACK_CITIES)],
    ])


def _admin_exercise_keyboard(exercises: list[str]) -> InlineKeyboardMarkup:
    keyboard = _chunk_buttons(exercises, CALLBACK_ADMIN_EXERCISE_PREFIX, columns=1)
    keyboard.append([InlineKeyboardButton("Назад", callback_data=CALLBACK_ADMIN_BACK_ACTIONS)])
    return InlineKeyboardMarkup(keyboard)


def _admin_rename_exercise_keyboard(exercises: list[str]) -> InlineKeyboardMarkup:
    keyboard = _chunk_buttons(exercises, CALLBACK_ADMIN_RENAME_EXERCISE_PREFIX, columns=1)
    keyboard.append([InlineKeyboardButton("Назад", callback_data=CALLBACK_ADMIN_BACK_ACTIONS)])
    return InlineKeyboardMarkup(keyboard)


def _admin_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Отмена", callback_data=CALLBACK_ADMIN_BACK_ACTIONS)],
    ])


def _admin_confirm_delete_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Удалить", callback_data=CALLBACK_ADMIN_CONFIRM_DELETE)],
        [InlineKeyboardButton("Отмена", callback_data=CALLBACK_ADMIN_BACK_ACTIONS)],
    ])


# ---------------------------------------------------------------------------
#  Env helpers
# ---------------------------------------------------------------------------

def _get_cities_from_env() -> list[str]:
    raw = os.getenv(CITIES_ENV, "")
    return [s.strip() for s in raw.split(",") if s.strip()]


def _get_exercise_filter():
    exclude_raw = os.getenv(EXERCISE_NAMES_ENV)
    if exclude_raw and exclude_raw.strip():
        return None, [s.strip() for s in exclude_raw.split(",") if s.strip()]
    n_raw = os.getenv(N_EXERCISES_ENV)
    if n_raw:
        try:
            return int(n_raw), None
        except ValueError:
            pass
    return None, DEFAULT_EXCLUDED_NAMES


def get_manager(context: ContextTypes.DEFAULT_TYPE) -> SheetsManager:
    return context.application.bot_data["sheets_manager"]


def get_spreadsheet_id(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.application.bot_data["spreadsheet_id"]


# ---------------------------------------------------------------------------
#  Initialization
# ---------------------------------------------------------------------------

async def post_init(application: Application) -> None:
    credentials_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    spreadsheet_id = os.getenv("SPREADSHEET_ID")
    if not credentials_file or not spreadsheet_id:
        raise RuntimeError(
            "Set GOOGLE_SERVICE_ACCOUNT_FILE and SPREADSHEET_ID in .env"
        )
    manager = SheetsManager(credentials_file=credentials_file)
    await manager.initialize()
    application.bot_data["sheets_manager"] = manager
    application.bot_data["spreadsheet_id"] = spreadsheet_id
    application.bot_data["exercise_filter"] = _get_exercise_filter()
    application.bot_data["cities"] = _get_cities_from_env()

    await application.bot.set_my_commands([
        BotCommand("start", "Начать работу"),
    ])
    logger.info("Bot initialized.")


# ---------------------------------------------------------------------------
#  Navigation helpers
# ---------------------------------------------------------------------------

def _get_current_step_content(
    context: ContextTypes.DEFAULT_TYPE,
) -> tuple[str, InlineKeyboardMarkup]:
    city = context.user_data.get(KEY_CITY)
    action = context.user_data.get(KEY_ACTION_TYPE)
    exercises = context.user_data.get(KEY_EXERCISES)
    selected_exercise = context.user_data.get(KEY_SELECTED_EXERCISE)
    teams = context.user_data.get(KEY_TEAMS)
    last_result = context.user_data.get(KEY_LAST_RESULT)

    if not city:
        return msg_welcome(), _city_keyboard(context)

    if not action:
        return msg_action_select(city), _action_keyboard()

    if exercises and not selected_exercise:
        return (
            msg_exercise_select(city, action),
            _exercise_keyboard(exercises, action),
        )

    if teams and selected_exercise:
        return (
            msg_team_select(city, action, selected_exercise, last_result),
            _team_keyboard(teams, action),
        )

    return msg_action_select(city), _action_keyboard()


async def _render_current_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    text, keyboard = _get_current_step_content(context)
    await update.callback_query.edit_message_text(
        text, reply_markup=keyboard, parse_mode=PARSE_MODE,
    )


async def _reply_current_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.message:
        return
    text, keyboard = _get_current_step_content(context)
    await update.message.reply_text(
        text, reply_markup=keyboard, parse_mode=PARSE_MODE,
    )


# ---------------------------------------------------------------------------
#  Handlers
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    cities = context.application.bot_data.get("cities") or []
    if not cities:
        await update.message.reply_text(msg_no_cities(), parse_mode=PARSE_MODE)
        return
    await update.message.reply_text(
        msg_welcome(),
        reply_markup=_city_keyboard(context),
        parse_mode=PARSE_MODE,
    )


async def cmd_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.user_data.get(KEY_IS_ADMIN):
        await update.message.reply_text(
            "Отправьте /start, чтобы начать работу.",
            parse_mode=PARSE_MODE,
        )
        return
    context.user_data.clear()
    cities = context.application.bot_data.get("cities") or []
    if not cities:
        await update.message.reply_text(msg_no_cities(), parse_mode=PARSE_MODE)
        return
    await update.message.reply_text(
        msg_welcome(),
        reply_markup=_city_keyboard(context),
        parse_mode=PARSE_MODE,
    )


async def callback_city(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    data = update.callback_query.data
    if not data or not data.startswith(CALLBACK_CITY_PREFIX):
        return
    try:
        index = int(data[len(CALLBACK_CITY_PREFIX):], 10)
    except ValueError:
        return
    cities = context.application.bot_data.get("cities") or []
    if not cities or index < 0 or index >= len(cities):
        return

    city = cities[index]
    context.user_data.clear()
    context.user_data[KEY_CITY] = city

    await update.callback_query.edit_message_text(
        msg_action_select(city),
        reply_markup=_action_keyboard(),
        parse_mode=PARSE_MODE,
    )


async def callback_action_check(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.callback_query.answer()
    await _start_exercise_flow(update, context, ACTION_CHECK)


async def callback_action_uncheck(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.callback_query.answer()
    await _start_exercise_flow(update, context, ACTION_UNCHECK)


async def _start_exercise_flow(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    action_type: str,
) -> None:
    context.user_data[KEY_ACTION_TYPE] = action_type
    context.user_data.pop(KEY_SELECTED_EXERCISE, None)
    context.user_data.pop(KEY_TEAMS, None)
    context.user_data.pop(KEY_LAST_RESULT, None)

    manager = get_manager(context)
    spreadsheet_id = get_spreadsheet_id(context)

    try:
        max_count, exclude_filter = context.application.bot_data.get(
            "exercise_filter", (None, None),
        )
        exercises = await manager.get_exercises(
            spreadsheet_id,
            max_count=max_count,
            excluded_names=exclude_filter,
        )
    except AuthenticationError:
        logger.exception("Auth error loading exercises")
        await update.callback_query.edit_message_text(
            msg_error("Не удалось авторизоваться. Обратитесь к администратору."),
            parse_mode=PARSE_MODE,
        )
        return
    except GoogleSheetsAPIError:
        logger.exception("API error loading exercises")
        await update.callback_query.edit_message_text(
            msg_error("Не удалось загрузить упражнения. Попробуйте позже."),
            parse_mode=PARSE_MODE,
        )
        return

    if not exercises:
        await update.callback_query.edit_message_text(
            msg_error("В таблице пока нет упражнений."),
            parse_mode=PARSE_MODE,
        )
        return

    context.user_data[KEY_EXERCISES] = exercises
    city = context.user_data.get(KEY_CITY, "")

    await update.callback_query.edit_message_text(
        msg_exercise_select(city, action_type),
        reply_markup=_exercise_keyboard(exercises, action_type),
        parse_mode=PARSE_MODE,
    )


async def callback_exercise(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.callback_query.answer()
    data = update.callback_query.data
    if not data or not data.startswith(CALLBACK_EXERCISE_PREFIX):
        return
    try:
        index = int(data[len(CALLBACK_EXERCISE_PREFIX):], 10)
    except ValueError:
        return
    exercises = context.user_data.get(KEY_EXERCISES)
    if not exercises or index < 0 or index >= len(exercises):
        return

    exercise_name = exercises[index]
    context.user_data[KEY_SELECTED_EXERCISE] = exercise_name
    context.user_data.pop(KEY_LAST_RESULT, None)

    city = context.user_data.get(KEY_CITY)
    action_type = context.user_data.get(KEY_ACTION_TYPE, ACTION_CHECK)

    if not city:
        await update.callback_query.edit_message_text(
            msg_error("Город не выбран. Отправьте /start."),
            parse_mode=PARSE_MODE,
        )
        return

    manager = get_manager(context)
    spreadsheet_id = get_spreadsheet_id(context)

    try:
        teams = await manager.get_teams(spreadsheet_id, city=city)
    except AuthenticationError:
        logger.exception("Auth error loading teams")
        await update.callback_query.edit_message_text(
            msg_error("Не удалось авторизоваться. Обратитесь к администратору."),
            parse_mode=PARSE_MODE,
        )
        context.user_data.pop(KEY_SELECTED_EXERCISE, None)
        return
    except GoogleSheetsAPIError:
        logger.exception("API error loading teams")
        await update.callback_query.edit_message_text(
            msg_error("Не удалось загрузить команды. Попробуйте позже."),
            parse_mode=PARSE_MODE,
        )
        context.user_data.pop(KEY_SELECTED_EXERCISE, None)
        return

    if not teams:
        await update.callback_query.edit_message_text(
            msg_error("В таблице пока нет команд."),
            parse_mode=PARSE_MODE,
        )
        context.user_data.pop(KEY_SELECTED_EXERCISE, None)
        return

    context.user_data[KEY_TEAMS] = teams

    await update.callback_query.edit_message_text(
        msg_team_select(city, action_type, exercise_name),
        reply_markup=_team_keyboard(teams, action_type),
        parse_mode=PARSE_MODE,
    )


async def callback_team(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    data = update.callback_query.data
    if not data or not data.startswith(CALLBACK_TEAM_PREFIX):
        await update.callback_query.answer()
        return
    try:
        index = int(data[len(CALLBACK_TEAM_PREFIX):], 10)
    except ValueError:
        await update.callback_query.answer()
        return

    teams = context.user_data.get(KEY_TEAMS)
    exercise_name = context.user_data.get(KEY_SELECTED_EXERCISE)
    if not teams or exercise_name is None or index < 0 or index >= len(teams):
        await update.callback_query.answer("Произошла ошибка", show_alert=True)
        return

    team_name = teams[index]
    manager = get_manager(context)
    spreadsheet_id = get_spreadsheet_id(context)
    action_type = context.user_data.get(KEY_ACTION_TYPE, ACTION_CHECK)
    city = context.user_data.get(KEY_CITY)

    if not city:
        await update.callback_query.answer("Город не выбран", show_alert=True)
        return

    try:
        if action_type == ACTION_UNCHECK:
            await manager.uncheck_team_exercise(
                spreadsheet_id=spreadsheet_id,
                team_name=team_name,
                exercise_name=exercise_name,
                city=city,
            )
        else:
            await manager.check_team_exercise(
                spreadsheet_id=spreadsheet_id,
                team_name=team_name,
                exercise_name=exercise_name,
                city=city,
            )
    except TeamNotFoundException:
        await update.callback_query.answer(
            f"\u00ab{team_name}\u00bb \u2014 команда не найдена в таблице",
            show_alert=True,
        )
        return
    except ExerciseNotFoundException:
        await update.callback_query.answer(
            f"\u00ab{exercise_name}\u00bb \u2014 упражнение не найдено в таблице",
            show_alert=True,
        )
        return
    except (GoogleSheetsAPIError, AuthenticationError):
        logger.exception("Sheets operation failed")
        await update.callback_query.answer(
            "Не удалось выполнить операцию. Попробуйте ещё раз.",
            show_alert=True,
        )
        return

    if action_type == ACTION_UNCHECK:
        toast = f"Команде \u00ab{team_name}\u00bb отменён зачёт"
        result_line = f"Команде \u00ab{team_name}\u00bb успешно отменён зачёт о выполнении упражнения \u00ab{exercise_name}\u00bb"
    else:
        toast = f"Команде \u00ab{team_name}\u00bb зачтено"
        result_line = f"Команде \u00ab{team_name}\u00bb успешно зачтено выполнение упражнения \u00ab{exercise_name}\u00bb"

    context.user_data.pop(KEY_LAST_RESULT, None)
    context.user_data.pop(KEY_SELECTED_EXERCISE, None)
    context.user_data.pop(KEY_TEAMS, None)
    context.user_data.pop(KEY_EXERCISES, None)

    await update.callback_query.answer(toast)
    await update.callback_query.edit_message_text(
        f"<i>{_esc(result_line)}</i>",
        parse_mode=PARSE_MODE,
    )
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=msg_action_select(city),
        reply_markup=_action_keyboard(),
        parse_mode=PARSE_MODE,
    )


async def callback_action_toggle(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    current = context.user_data.get(KEY_ACTION_TYPE, ACTION_CHECK)
    new_action = ACTION_UNCHECK if current != ACTION_UNCHECK else ACTION_CHECK
    context.user_data[KEY_ACTION_TYPE] = new_action
    context.user_data.pop(KEY_LAST_RESULT, None)

    new_label = _action_label(new_action).capitalize()
    await update.callback_query.answer(f"Режим: {new_label}")
    await _render_current_step(update, context)



async def callback_nav_back_to_exercises(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.callback_query.answer()
    context.user_data.pop(KEY_TEAMS, None)
    context.user_data.pop(KEY_SELECTED_EXERCISE, None)
    context.user_data.pop(KEY_LAST_RESULT, None)
    await _render_current_step(update, context)


async def callback_nav_back_to_actions(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.callback_query.answer()
    for key in (KEY_SELECTED_EXERCISE, KEY_EXERCISES, KEY_TEAMS,
                KEY_ACTION_TYPE, KEY_LAST_RESULT):
        context.user_data.pop(key, None)
    await _render_current_step(update, context)


async def callback_nav_back_to_cities(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.callback_query.answer()
    context.user_data.clear()
    await update.callback_query.edit_message_text(
        msg_welcome(),
        reply_markup=_city_keyboard(context),
        parse_mode=PARSE_MODE,
    )


# ---------------------------------------------------------------------------
#  Admin handlers
# ---------------------------------------------------------------------------

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await update.message.delete()
    except Exception:
        pass

    if context.user_data.get(KEY_IS_ADMIN):
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=msg_admin_welcome(),
            reply_markup=_admin_city_keyboard(context),
            parse_mode=PARSE_MODE,
        )
        return

    context.user_data[KEY_ADMIN_STATE] = ADMIN_AWAITING_PASSWORD
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Введите пароль администратора:",
        parse_mode=PARSE_MODE,
    )


async def _admin_handle_password(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    password_input = update.message.text.strip()
    context.user_data.pop(KEY_ADMIN_STATE, None)

    try:
        await update.message.delete()
    except Exception:
        pass

    password = os.getenv("ADMIN_PASSWORD", "")

    if not password:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=msg_error("Пароль администратора не настроен."),
            parse_mode=PARSE_MODE,
        )
        return

    if password_input != password:
        attempts = context.user_data.get(KEY_ADMIN_PASSWORD_ATTEMPTS, 0) + 1

        if attempts >= ADMIN_MAX_PASSWORD_ATTEMPTS:
            context.user_data.clear()
            cities = context.application.bot_data.get("cities") or []
            if not cities:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=msg_no_cities(),
                    parse_mode=PARSE_MODE,
                )
                return
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=msg_welcome(),
                reply_markup=_city_keyboard(context),
                parse_mode=PARSE_MODE,
            )
            return

        context.user_data[KEY_ADMIN_PASSWORD_ATTEMPTS] = attempts
        context.user_data[KEY_ADMIN_STATE] = ADMIN_AWAITING_PASSWORD
        remaining = ADMIN_MAX_PASSWORD_ATTEMPTS - attempts
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"Неверный пароль. Осталось попыток: {remaining}",
            parse_mode=PARSE_MODE,
        )
        return

    context.user_data.clear()
    context.user_data[KEY_IS_ADMIN] = True

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=msg_admin_welcome(),
        reply_markup=_admin_city_keyboard(context),
        parse_mode=PARSE_MODE,
    )


async def callback_admin_city(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not context.user_data.get(KEY_IS_ADMIN):
        await update.callback_query.answer()
        return
    await update.callback_query.answer()
    data = update.callback_query.data
    if not data or not data.startswith(CALLBACK_ADMIN_CITY_PREFIX):
        return
    try:
        index = int(data[len(CALLBACK_ADMIN_CITY_PREFIX):], 10)
    except ValueError:
        return
    cities = context.application.bot_data.get("cities") or []
    if not cities or index < 0 or index >= len(cities):
        return

    city = cities[index]
    context.user_data[KEY_ADMIN_CITY] = city
    context.user_data.pop(KEY_ADMIN_STATE, None)
    context.user_data.pop(KEY_ADMIN_EXERCISES, None)
    context.user_data.pop(KEY_ADMIN_EXERCISE_NAME, None)

    await update.callback_query.edit_message_text(
        msg_admin_action(city),
        reply_markup=_admin_action_keyboard(),
        parse_mode=PARSE_MODE,
    )


async def callback_admin_add(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not context.user_data.get(KEY_IS_ADMIN):
        await update.callback_query.answer()
        return
    await update.callback_query.answer()
    city = context.user_data.get(KEY_ADMIN_CITY)
    if not city:
        return

    context.user_data[KEY_ADMIN_STATE] = ADMIN_AWAITING_NAME

    await update.callback_query.edit_message_text(
        msg_admin_enter_name(city),
        reply_markup=_admin_cancel_keyboard(),
        parse_mode=PARSE_MODE,
    )


async def callback_admin_remove(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not context.user_data.get(KEY_IS_ADMIN):
        await update.callback_query.answer()
        return
    await update.callback_query.answer()
    city = context.user_data.get(KEY_ADMIN_CITY)
    if not city:
        return

    manager = get_manager(context)
    spreadsheet_id = get_spreadsheet_id(context)

    try:
        exercises = await manager.get_exercises(spreadsheet_id, city=city)
    except (GoogleSheetsAPIError, AuthenticationError):
        logger.exception("Failed to load exercises for admin")
        await update.callback_query.edit_message_text(
            msg_error("Не удалось загрузить упражнения."),
            parse_mode=PARSE_MODE,
        )
        return

    if not exercises:
        await update.callback_query.answer("В таблице нет упражнений", show_alert=True)
        return

    context.user_data[KEY_ADMIN_EXERCISES] = exercises

    await update.callback_query.edit_message_text(
        msg_admin_select_exercise(city),
        reply_markup=_admin_exercise_keyboard(exercises),
        parse_mode=PARSE_MODE,
    )


async def callback_admin_rename(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not context.user_data.get(KEY_IS_ADMIN):
        await update.callback_query.answer()
        return
    await update.callback_query.answer()
    city = context.user_data.get(KEY_ADMIN_CITY)
    if not city:
        return

    manager = get_manager(context)
    spreadsheet_id = get_spreadsheet_id(context)

    try:
        exercises = await manager.get_exercises(spreadsheet_id, city=city)
    except (GoogleSheetsAPIError, AuthenticationError):
        logger.exception("Failed to load exercises for admin rename")
        await update.callback_query.edit_message_text(
            msg_error("Не удалось загрузить упражнения."),
            parse_mode=PARSE_MODE,
        )
        return

    if not exercises:
        await update.callback_query.answer("В таблице нет упражнений", show_alert=True)
        return

    context.user_data[KEY_ADMIN_EXERCISES] = exercises

    await update.callback_query.edit_message_text(
        msg_admin_select_exercise_rename(city),
        reply_markup=_admin_rename_exercise_keyboard(exercises),
        parse_mode=PARSE_MODE,
    )


async def callback_admin_rename_exercise(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not context.user_data.get(KEY_IS_ADMIN):
        await update.callback_query.answer()
        return
    await update.callback_query.answer()
    data = update.callback_query.data
    if not data or not data.startswith(CALLBACK_ADMIN_RENAME_EXERCISE_PREFIX):
        return
    try:
        index = int(data[len(CALLBACK_ADMIN_RENAME_EXERCISE_PREFIX):], 10)
    except ValueError:
        return
    exercises = context.user_data.get(KEY_ADMIN_EXERCISES)
    if not exercises or index < 0 or index >= len(exercises):
        return

    exercise_name = exercises[index]
    context.user_data[KEY_ADMIN_EXERCISE_NAME] = exercise_name
    context.user_data[KEY_ADMIN_STATE] = ADMIN_AWAITING_RENAME
    city = context.user_data.get(KEY_ADMIN_CITY, "")

    await update.callback_query.edit_message_text(
        msg_admin_enter_new_name(city, exercise_name),
        reply_markup=_admin_cancel_keyboard(),
        parse_mode=PARSE_MODE,
    )


async def callback_admin_exercise(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not context.user_data.get(KEY_IS_ADMIN):
        await update.callback_query.answer()
        return
    await update.callback_query.answer()
    data = update.callback_query.data
    if not data or not data.startswith(CALLBACK_ADMIN_EXERCISE_PREFIX):
        return
    try:
        index = int(data[len(CALLBACK_ADMIN_EXERCISE_PREFIX):], 10)
    except ValueError:
        return
    exercises = context.user_data.get(KEY_ADMIN_EXERCISES)
    if not exercises or index < 0 or index >= len(exercises):
        return

    exercise_name = exercises[index]
    context.user_data[KEY_ADMIN_EXERCISE_NAME] = exercise_name
    city = context.user_data.get(KEY_ADMIN_CITY, "")

    await update.callback_query.edit_message_text(
        msg_admin_confirm_delete(city, exercise_name),
        reply_markup=_admin_confirm_delete_keyboard(),
        parse_mode=PARSE_MODE,
    )


async def callback_admin_confirm_delete(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not context.user_data.get(KEY_IS_ADMIN):
        await update.callback_query.answer()
        return

    exercise_name = context.user_data.get(KEY_ADMIN_EXERCISE_NAME)
    city = context.user_data.get(KEY_ADMIN_CITY)
    if not exercise_name or not city:
        await update.callback_query.answer("Произошла ошибка", show_alert=True)
        return

    manager = get_manager(context)
    spreadsheet_id = get_spreadsheet_id(context)

    try:
        await manager.remove_exercise(spreadsheet_id, exercise_name, city=city)
    except ExerciseNotFoundException:
        await update.callback_query.answer(
            "Упражнение не найдено в таблице", show_alert=True,
        )
        return
    except (GoogleSheetsAPIError, AuthenticationError):
        logger.exception("Failed to remove exercise")
        await update.callback_query.answer(
            "Не удалось удалить упражнение. Попробуйте позже.",
            show_alert=True,
        )
        return

    context.user_data.pop(KEY_ADMIN_EXERCISE_NAME, None)
    context.user_data.pop(KEY_ADMIN_EXERCISES, None)

    await update.callback_query.answer("Упражнение удалено")
    await update.callback_query.edit_message_text(
        "<i>Упражнение успешно удалено</i>",
        parse_mode=PARSE_MODE,
    )
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=msg_admin_action(city),
        reply_markup=_admin_action_keyboard(),
        parse_mode=PARSE_MODE,
    )


async def callback_admin_back_cities(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.callback_query.answer()
    context.user_data.pop(KEY_ADMIN_CITY, None)
    context.user_data.pop(KEY_ADMIN_STATE, None)
    context.user_data.pop(KEY_ADMIN_EXERCISES, None)
    context.user_data.pop(KEY_ADMIN_EXERCISE_NAME, None)

    await update.callback_query.edit_message_text(
        msg_admin_welcome(),
        reply_markup=_admin_city_keyboard(context),
        parse_mode=PARSE_MODE,
    )


async def callback_admin_back_actions(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.callback_query.answer()
    city = context.user_data.get(KEY_ADMIN_CITY, "")
    context.user_data.pop(KEY_ADMIN_STATE, None)
    context.user_data.pop(KEY_ADMIN_EXERCISES, None)
    context.user_data.pop(KEY_ADMIN_EXERCISE_NAME, None)

    await update.callback_query.edit_message_text(
        msg_admin_action(city),
        reply_markup=_admin_action_keyboard(),
        parse_mode=PARSE_MODE,
    )


async def _admin_handle_exercise_name(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    name = update.message.text.strip()
    city = context.user_data.get(KEY_ADMIN_CITY)
    context.user_data.pop(KEY_ADMIN_STATE, None)

    if not name or not city:
        await update.message.reply_text(
            msg_error("Название не может быть пустым."),
            parse_mode=PARSE_MODE,
        )
        return

    manager = get_manager(context)
    spreadsheet_id = get_spreadsheet_id(context)

    existing = await manager.get_exercises(spreadsheet_id, city=city)
    normalized_new = name.strip().lower()
    for ex in existing:
        if ex.strip().lower() == normalized_new:
            await update.message.reply_text(
                msg_admin_action(city, result=f"Упражнение \u00ab{name}\u00bb уже существует"),
                reply_markup=_admin_action_keyboard(),
                parse_mode=PARSE_MODE,
            )
            return

    try:
        await manager.add_exercise(spreadsheet_id, name, city=city)
    except (GoogleSheetsAPIError, AuthenticationError):
        logger.exception("Failed to add exercise")
        await update.message.reply_text(
            msg_error("Не удалось добавить упражнение. Попробуйте позже."),
            parse_mode=PARSE_MODE,
        )
        return

    await update.message.reply_text(
        "<i>Упражнение успешно добавлено</i>",
        parse_mode=PARSE_MODE,
    )
    await update.message.reply_text(
        msg_admin_action(city),
        reply_markup=_admin_action_keyboard(),
        parse_mode=PARSE_MODE,
    )


async def _admin_handle_rename(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    new_name = update.message.text.strip()
    city = context.user_data.get(KEY_ADMIN_CITY)
    old_name = context.user_data.get(KEY_ADMIN_EXERCISE_NAME)
    context.user_data.pop(KEY_ADMIN_STATE, None)

    if not new_name or not city or not old_name:
        await update.message.reply_text(
            msg_error("Название не может быть пустым."),
            parse_mode=PARSE_MODE,
        )
        return

    manager = get_manager(context)
    spreadsheet_id = get_spreadsheet_id(context)

    existing = await manager.get_exercises(spreadsheet_id, city=city)
    normalized_new = new_name.strip().lower()
    for ex in existing:
        if ex.strip().lower() == normalized_new:
            await update.message.reply_text(
                msg_admin_action(city, result=f"Упражнение \u00ab{new_name}\u00bb уже существует"),
                reply_markup=_admin_action_keyboard(),
                parse_mode=PARSE_MODE,
            )
            return

    try:
        await manager.rename_exercise(spreadsheet_id, old_name, new_name, city=city)
    except ExerciseNotFoundException:
        await update.message.reply_text(
            msg_error(f"Упражнение \u00ab{old_name}\u00bb не найдено в таблице."),
            parse_mode=PARSE_MODE,
        )
        return
    except (GoogleSheetsAPIError, AuthenticationError):
        logger.exception("Failed to rename exercise")
        await update.message.reply_text(
            msg_error("Не удалось переименовать упражнение. Попробуйте позже."),
            parse_mode=PARSE_MODE,
        )
        return

    context.user_data.pop(KEY_ADMIN_EXERCISE_NAME, None)
    context.user_data.pop(KEY_ADMIN_EXERCISES, None)

    await update.message.reply_text(
        "<i>Упражнение успешно переименовано</i>",
        parse_mode=PARSE_MODE,
    )
    await update.message.reply_text(
        msg_admin_action(city),
        reply_markup=_admin_action_keyboard(),
        parse_mode=PARSE_MODE,
    )


# ---------------------------------------------------------------------------
#  Fallbacks
# ---------------------------------------------------------------------------

async def fallback_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.callback_query.answer()
    await _render_current_step(update, context)


async def fallback_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    admin_state = context.user_data.get(KEY_ADMIN_STATE)
    if admin_state == ADMIN_AWAITING_PASSWORD:
        await _admin_handle_password(update, context)
        return
    if context.user_data.get(KEY_IS_ADMIN):
        if admin_state == ADMIN_AWAITING_NAME:
            await _admin_handle_exercise_name(update, context)
            return
        if admin_state == ADMIN_AWAITING_RENAME:
            await _admin_handle_rename(update, context)
            return
    if context.user_data.get(KEY_CITY):
        await _reply_current_step(update, context)
        return
    await update.message.reply_text(
        "Отправьте /start, чтобы начать работу.",
        parse_mode=PARSE_MODE,
    )


async def unknown_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await _reply_current_step(update, context)


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main() -> None:
    token = os.getenv("TG_TOKEN")
    if not token:
        raise RuntimeError("Set TG_TOKEN in .env")

    application = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .build()
    )

    application.add_handler(CommandHandler("admin", cmd_admin))
    application.add_handler(CommandHandler("panel", cmd_panel))
    application.add_handler(CommandHandler("start", cmd_start))

    application.add_handler(
        CallbackQueryHandler(callback_admin_city, pattern=f"^{CALLBACK_ADMIN_CITY_PREFIX}\\d+$")
    )
    application.add_handler(
        CallbackQueryHandler(callback_admin_add, pattern=f"^{CALLBACK_ADMIN_ADD}$")
    )
    application.add_handler(
        CallbackQueryHandler(callback_admin_rename, pattern=f"^{CALLBACK_ADMIN_RENAME}$")
    )
    application.add_handler(
        CallbackQueryHandler(callback_admin_remove, pattern=f"^{CALLBACK_ADMIN_REMOVE}$")
    )
    application.add_handler(
        CallbackQueryHandler(callback_admin_rename_exercise, pattern=f"^{CALLBACK_ADMIN_RENAME_EXERCISE_PREFIX}\\d+$")
    )
    application.add_handler(
        CallbackQueryHandler(callback_admin_exercise, pattern=f"^{CALLBACK_ADMIN_EXERCISE_PREFIX}\\d+$")
    )
    application.add_handler(
        CallbackQueryHandler(callback_admin_confirm_delete, pattern=f"^{CALLBACK_ADMIN_CONFIRM_DELETE}$")
    )
    application.add_handler(
        CallbackQueryHandler(callback_admin_back_cities, pattern=f"^{CALLBACK_ADMIN_BACK_CITIES}$")
    )
    application.add_handler(
        CallbackQueryHandler(callback_admin_back_actions, pattern=f"^{CALLBACK_ADMIN_BACK_ACTIONS}$")
    )

    application.add_handler(
        CallbackQueryHandler(callback_city, pattern=f"^{CALLBACK_CITY_PREFIX}\\d+$")
    )
    application.add_handler(
        CallbackQueryHandler(callback_action_check, pattern=f"^{CALLBACK_ACTION_CHECK}$")
    )
    application.add_handler(
        CallbackQueryHandler(callback_action_uncheck, pattern=f"^{CALLBACK_ACTION_UNCHECK}$")
    )
    application.add_handler(
        CallbackQueryHandler(callback_action_toggle, pattern=f"^{CALLBACK_ACTION_TOGGLE}$")
    )
    application.add_handler(
        CallbackQueryHandler(callback_exercise, pattern=f"^{CALLBACK_EXERCISE_PREFIX}\\d+$")
    )
    application.add_handler(
        CallbackQueryHandler(callback_team, pattern=f"^{CALLBACK_TEAM_PREFIX}\\d+$")
    )
    application.add_handler(
        CallbackQueryHandler(
            callback_nav_back_to_exercises,
            pattern=f"^{CALLBACK_NAV_BACK_TO_EXERCISES}$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            callback_nav_back_to_actions,
            pattern=f"^{CALLBACK_NAV_BACK_TO_ACTIONS}$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            callback_nav_back_to_cities,
            pattern=f"^{CALLBACK_NAV_BACK_TO_CITIES}$",
        )
    )

    application.add_handler(CallbackQueryHandler(fallback_callback))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_message),
    )
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    application.run_polling(bootstrap_retries=5)


if __name__ == "__main__":
    main()
