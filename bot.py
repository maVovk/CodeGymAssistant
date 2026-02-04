"""
Telegram bot for marking team exercises in Google Sheets.
Flow: /start -> choose city ONLY -> choose action -> choose exercise -> choose team.
"""

import logging
import os
from typing import Optional
from telegram.ext import MessageHandler, filters
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
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

# User data keys
KEY_SELECTED_EXERCISE = "selected_exercise"
KEY_EXERCISES = "exercises"
KEY_TEAMS = "teams"
KEY_ACTION_TYPE = "action_type"
KEY_CITY = "city"

ACTION_CHECK = "check"
ACTION_UNCHECK = "uncheck"

CALLBACK_ACTION_CHECK = "action:check"
CALLBACK_ACTION_UNCHECK = "action:uncheck"
CALLBACK_ACTION_TOGGLE = "action:toggle"
CALLBACK_TEAM_PREFIX = "t:"
CALLBACK_EXERCISE_PREFIX = "e:"
CALLBACK_CITY_PREFIX = "city:"
CALLBACK_NAV_BACK_TO_EXERCISES = "nav:back_exercises"
CALLBACK_NAV_BACK_TO_CITIES = "nav:back_cities"

MESSAGE_START_WITH_CITY = (
    "Привет! Я помогу отметить выполненное упражнение для команды в таблице.\n\n"
    "Для начала выберите ваш город:"
)

MESSAGE_ACTIONS = (
    "Выберите действие:"
)
MESSAGE_SELECT_TEAM = "Выберите команду:"
MESSAGE_SELECT_EXERCISE = "Выберите упражнение:"
MESSAGE_DONE = "Готово! Упражнение отмечено."
MESSAGE_UNCHECK_DONE = "Готово! Отметка снята."
MESSAGE_ERROR_SHEETS = "Ошибка доступа к таблице. Попробуйте позже."
MESSAGE_ERROR_AUTH = "Ошибка авторизации. Проверьте настройки бота."
MESSAGE_ERROR_GENERIC = "Произошла ошибка. Попробуйте позже."
MESSAGE_NO_CITY = "Сначала выберите город командой /start."

CITIES_ENV = "CITIES"

def _get_cities_from_env() -> list[str]:
    """Get cities list from CITIES env variable."""
    raw = os.getenv(CITIES_ENV, "")
    return [s.strip() for s in raw.split(",") if s.strip()]

def get_manager(context: ContextTypes.DEFAULT_TYPE) -> SheetsManager:
    return context.application.bot_data["sheets_manager"]

def get_spreadsheet_id(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.application.bot_data["spreadsheet_id"]

def _action_keyboard() -> InlineKeyboardMarkup:
    """Keyboard with 'Отметить упражнение' and 'Снять отметку' buttons."""
    keyboard = [
        [
            InlineKeyboardButton("Отметить упражнение", callback_data=CALLBACK_ACTION_CHECK),
            InlineKeyboardButton("Снять отметку", callback_data=CALLBACK_ACTION_UNCHECK),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def _action_label(action_type: str) -> str:
    """Human-readable action label for headers."""
    return "Отметить упражнение" if action_type != ACTION_UNCHECK else "Снять отметку"

def _header_text(
    *,
    city: Optional[str],
    action_type: Optional[str],
    exercise_name: Optional[str] = None,
) -> str:
    """Build common markdown header for screens."""
    if not city:
        return ""
    lines: list[str] = [f"**Город: {city}**"]
    if action_type:
        lines.append(f"**Режим: {_action_label(action_type)}**")
    if exercise_name:
        lines.append(f"**Упражнение: {exercise_name}**")
    return "\n".join(lines) + "\n\n"

def _toggle_action_button(action_type: str) -> InlineKeyboardButton:
    if action_type == ACTION_UNCHECK:
        return InlineKeyboardButton("Переключить режим", callback_data=CALLBACK_ACTION_TOGGLE)
    return InlineKeyboardButton("Переключить режим", callback_data=CALLBACK_ACTION_TOGGLE)

def _chunk_keyboard_buttons(
    items: list[str],
    prefix: str,
    columns: int = 2,
) -> list[list[InlineKeyboardButton]]:
    """Create button rows with N columns."""
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i, text in enumerate(items):
        row.append(InlineKeyboardButton(text, callback_data=f"{prefix}{i}"))
        if len(row) >= columns:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows

def _exercise_keyboard(exercises: list[str], action_type: str) -> InlineKeyboardMarkup:
    # Exercises: one button per row for easier tapping/reading
    keyboard = _chunk_keyboard_buttons(exercises, CALLBACK_EXERCISE_PREFIX, columns=1)
    keyboard.append([
        InlineKeyboardButton("К городам", callback_data=CALLBACK_NAV_BACK_TO_CITIES),
        _toggle_action_button(action_type),
    ])
    return InlineKeyboardMarkup(keyboard)

def _team_keyboard(teams: list[str], action_type: str) -> InlineKeyboardMarkup:
    # Teams: one button per row for easier tapping/reading
    keyboard = _chunk_keyboard_buttons(teams, CALLBACK_TEAM_PREFIX, columns=1)
    keyboard.append(
        [
            InlineKeyboardButton("К упражнениям", callback_data=CALLBACK_NAV_BACK_TO_EXERCISES),
            _toggle_action_button(action_type),
        ]
    )
    return InlineKeyboardMarkup(keyboard)

async def _render_current_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Redraw current step for callback-driven navigation."""
    text, keyboard = await _get_current_step_prompt(context)
    await update.callback_query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode="Markdown",
    )

async def _reply_current_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply with current step for message-driven navigation."""
    if not update.message:
        return
    text, keyboard = await _get_current_step_prompt(context)
    await update.message.reply_text(
        text,
        reply_markup=keyboard,
        parse_mode="Markdown",
    )

def _city_keyboard(context: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    """Keyboard with city name buttons."""
    cities = context.application.bot_data.get("cities") or []
    keyboard = [
        [InlineKeyboardButton(city, callback_data=f"{CALLBACK_CITY_PREFIX}{i}")]
        for i, city in enumerate(cities)
    ]
    return InlineKeyboardMarkup(keyboard)

# Exercise list filter
N_EXERCISES_ENV = "N_EXERCISES"
EXERCISE_NAMES_ENV = "EXCLUDED_NAMES"
DEFAULT_EXCLUDED_NAMES = ["Сдано задач", "Разница"]

def _get_exercise_filter():
    """Return (max_count, exclude_filter) for get_exercises."""
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

async def post_init(application: Application) -> None:
    """Initialize SheetsManager and store in bot_data."""
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
    logger.info("SheetsManager initialized.")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start: ALWAYS show city selection first + reset city."""
    context.user_data.pop(KEY_CITY, None)

    cities = context.application.bot_data.get("cities") or []
    if not cities:
        await update.message.reply_text("Список городов не настроен.")
        return

    await update.message.reply_text(
        MESSAGE_START_WITH_CITY,
        reply_markup=_city_keyboard(context),
        parse_mode="Markdown",
    )

async def callback_city(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User selected city: save it and show actions ONLY."""
    await update.callback_query.answer()
    data = update.callback_query.data
    if not data.startswith(CALLBACK_CITY_PREFIX):
        return

    try:
        index = int(data[len(CALLBACK_CITY_PREFIX):], 10)
    except ValueError:
        return

    cities = context.application.bot_data.get("cities") or []
    if not cities or index < 0 or index >= len(cities):
        await update.callback_query.edit_message_text(MESSAGE_ERROR_GENERIC)
        return

    city = cities[index]
    context.user_data[KEY_CITY] = city

    # reset flow state on city change
    for key in (KEY_SELECTED_EXERCISE, KEY_EXERCISES, KEY_TEAMS, KEY_ACTION_TYPE):
        context.user_data.pop(key, None)

    await update.callback_query.edit_message_text(
        f"{_header_text(city=city, action_type=None)}{MESSAGE_ACTIONS}",
        reply_markup=_action_keyboard(),
        parse_mode="Markdown",
    )

async def _start_exercise_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, action_type: str) -> None:
    """Load exercises and show exercise buttons. action_type is ACTION_CHECK or ACTION_UNCHECK."""
    context.user_data[KEY_ACTION_TYPE] = action_type
    # reset deeper flow state
    context.user_data.pop(KEY_SELECTED_EXERCISE, None)
    context.user_data.pop(KEY_TEAMS, None)
    manager = get_manager(context)
    spreadsheet_id = get_spreadsheet_id(context)

    try:
        max_count, exclude_filter = context.application.bot_data.get("exercise_filter", (None, None))
        exercises = await manager.get_exercises(
            spreadsheet_id,
            max_count=max_count,
            excluded_names=exclude_filter,
        )
    except (GoogleSheetsAPIError, AuthenticationError) as e:
        logger.exception("Failed to get exercises")
        msg = MESSAGE_ERROR_AUTH if isinstance(e, AuthenticationError) else MESSAGE_ERROR_SHEETS
        await update.callback_query.edit_message_text(msg)
        return

    if not exercises:
        await update.callback_query.edit_message_text("В таблице нет упражнений.")
        return

    context.user_data[KEY_EXERCISES] = exercises
    city = context.user_data.get(KEY_CITY)
    header = _header_text(city=city, action_type=action_type)
    await update.callback_query.edit_message_text(
        f"{header}{MESSAGE_SELECT_EXERCISE}",
        reply_markup=_exercise_keyboard(exercises, action_type),
        parse_mode="Markdown",
    )

async def callback_action_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start check flow."""
    await update.callback_query.answer()
    await _start_exercise_flow(update, context, ACTION_CHECK)

async def callback_action_uncheck(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start uncheck flow."""
    await update.callback_query.answer()
    await _start_exercise_flow(update, context, ACTION_UNCHECK)

async def callback_exercise(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User selected exercise: save it, load teams, show inline team buttons."""
    await update.callback_query.answer()
    data = update.callback_query.data
    if not data.startswith(CALLBACK_EXERCISE_PREFIX):
        return

    try:
        index = int(data[len(CALLBACK_EXERCISE_PREFIX) :], 10)
    except ValueError:
        return

    exercises = context.user_data.get(KEY_EXERCISES)
    if not exercises or index < 0 or index >= len(exercises):
        await update.callback_query.edit_message_text(MESSAGE_ERROR_GENERIC)
        return

    exercise_name = exercises[index]
    context.user_data[KEY_SELECTED_EXERCISE] = exercise_name

    manager = get_manager(context)
    spreadsheet_id = get_spreadsheet_id(context)
    city = context.user_data.get(KEY_CITY)
    action_type = context.user_data.get(KEY_ACTION_TYPE, ACTION_CHECK)

    if not city:
        await update.callback_query.edit_message_text(MESSAGE_NO_CITY)
        return

    try:
        teams = await manager.get_teams(spreadsheet_id, city=city)
    except (GoogleSheetsAPIError, AuthenticationError) as e:
        logger.exception("Failed to get teams")
        msg = MESSAGE_ERROR_AUTH if isinstance(e, AuthenticationError) else MESSAGE_ERROR_SHEETS
        await update.callback_query.edit_message_text(msg)
        context.user_data.pop(KEY_SELECTED_EXERCISE, None)
        return

    if not teams:
        await update.callback_query.edit_message_text("В таблице нет команд.")
        context.user_data.pop(KEY_SELECTED_EXERCISE, None)
        return

    context.user_data[KEY_TEAMS] = teams
    header = _header_text(city=city, action_type=action_type, exercise_name=exercise_name)
    await update.callback_query.edit_message_text(
        f"{header}{MESSAGE_SELECT_TEAM}",
        reply_markup=_team_keyboard(teams, action_type),
        parse_mode="Markdown",
    )

async def callback_action_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle action mode (check <-> uncheck) and redraw current step."""
    await update.callback_query.answer()
    current = context.user_data.get(KEY_ACTION_TYPE, ACTION_CHECK)
    context.user_data[KEY_ACTION_TYPE] = ACTION_UNCHECK if current != ACTION_UNCHECK else ACTION_CHECK

    await _render_current_step(update, context)

async def callback_nav_back_to_exercises(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Go back from team selection to exercise selection."""
    await update.callback_query.answer()
    context.user_data.pop(KEY_TEAMS, None)
    context.user_data.pop(KEY_SELECTED_EXERCISE, None)

    await _render_current_step(update, context)

async def callback_nav_back_to_cities(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Go back from exercise selection to city selection."""
    await update.callback_query.answer()
    # Reset all flow state except city (we'll reset city too to show city selection)
    for key in (KEY_SELECTED_EXERCISE, KEY_EXERCISES, KEY_TEAMS, KEY_ACTION_TYPE, KEY_CITY):
        context.user_data.pop(key, None)

    await update.callback_query.edit_message_text(
        MESSAGE_START_WITH_CITY,
        reply_markup=_city_keyboard(context),
        parse_mode="Markdown",
    )

async def callback_team(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute check/uncheck after user selected team (exercise is already selected)."""
    await update.callback_query.answer()
    data = update.callback_query.data
    if not data.startswith(CALLBACK_TEAM_PREFIX):
        return

    try:
        index = int(data[len(CALLBACK_TEAM_PREFIX):], 10)
    except ValueError:
        return

    teams = context.user_data.get(KEY_TEAMS)
    exercise_name = context.user_data.get(KEY_SELECTED_EXERCISE)
    if not teams or exercise_name is None or index < 0 or index >= len(teams):
        await update.callback_query.edit_message_text(MESSAGE_ERROR_GENERIC)
        return

    team_name = teams[index]
    manager = get_manager(context)
    spreadsheet_id = get_spreadsheet_id(context)
    action_type = context.user_data.get(KEY_ACTION_TYPE, ACTION_CHECK)
    city = context.user_data.get(KEY_CITY)

    if not city:
        await update.callback_query.edit_message_text(MESSAGE_NO_CITY)
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
        await update.callback_query.edit_message_text(
            f"Команда «{team_name}» не найдена в таблице."
        )
    except ExerciseNotFoundException:
        await update.callback_query.edit_message_text(
            f"Упражнение «{exercise_name}» не найдено в таблице."
        )
    except (GoogleSheetsAPIError, AuthenticationError) as e:
        logger.exception("Sheets operation failed")
        msg = MESSAGE_ERROR_AUTH if isinstance(e, AuthenticationError) else MESSAGE_ERROR_SHEETS
        await update.callback_query.edit_message_text(msg)
    else:
        done_message = MESSAGE_UNCHECK_DONE if action_type == ACTION_UNCHECK else MESSAGE_DONE
        await update.callback_query.edit_message_text(
            done_message,
            reply_markup=_action_keyboard(),
        )

    # Clear temporary state (KEEP city)
    for key in (KEY_SELECTED_EXERCISE, KEY_EXERCISES, KEY_TEAMS, KEY_ACTION_TYPE):
        context.user_data.pop(key, None)

async def _get_current_step_prompt(context: ContextTypes.DEFAULT_TYPE):
    """
    Determine what the user should do next and return (text, reply_markup).
    """
    city = context.user_data.get(KEY_CITY)
    action = context.user_data.get(KEY_ACTION_TYPE)
    teams = context.user_data.get(KEY_TEAMS)
    exercises = context.user_data.get(KEY_EXERCISES)

    if not city:
        return MESSAGE_START_WITH_CITY, _city_keyboard(context)

    if not action:
        return MESSAGE_ACTIONS, _action_keyboard()

    # After action is selected the next step is selecting EXERCISE, then TEAM.
    if exercises and not context.user_data.get(KEY_SELECTED_EXERCISE):
        header = _header_text(city=city, action_type=action)
        return f"{header}{MESSAGE_SELECT_EXERCISE}", _exercise_keyboard(exercises, action)

    if teams:
        ex = context.user_data.get(KEY_SELECTED_EXERCISE)
        header = _header_text(city=city, action_type=action, exercise_name=ex)
        return f"{header}{MESSAGE_SELECT_TEAM}", _team_keyboard(teams, action)

    return f"{_header_text(city=city, action_type=None)}{MESSAGE_ACTIONS}", _action_keyboard()

async def fallback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle ANY unexpected callback query.
    """
    await update.callback_query.answer()
    await _render_current_step(update, context)

async def fallback_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle ANY text message that is not expected.
    """
    await _reply_current_step(update, context)

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles unknown command.
    """
    await _reply_current_step(update, context)

def main() -> None:
    token = os.getenv("TG_TOKEN")
    if not token:
        raise RuntimeError("Set TG_TOKEN in .env")

    application = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .build()
    )

    # Handlers order matters!
    application.add_handler(CommandHandler("start", cmd_start))
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
        CallbackQueryHandler(callback_nav_back_to_exercises, pattern=f"^{CALLBACK_NAV_BACK_TO_EXERCISES}$")
    )
    application.add_handler(
        CallbackQueryHandler(callback_nav_back_to_cities, pattern=f"^{CALLBACK_NAV_BACK_TO_CITIES}$")
    )

    application.add_handler(CallbackQueryHandler(fallback_callback))
    #Simple text handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_message))
    # Unknown command handler
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    application.run_polling()

if __name__ == "__main__":
    main()
