"""
Telegram bot for marking team exercises in Google Sheets.
Flow: /start -> choose city ONLY -> "Отметить упражнение" -> choose team -> choose exercise.
"""

import logging
import os
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
KEY_SELECTED_TEAM = "selected_team"
KEY_EXERCISES = "exercises"
KEY_TEAMS = "teams"
KEY_ACTION_TYPE = "action_type"
KEY_CITY = "city"

ACTION_CHECK = "check"
ACTION_UNCHECK = "uncheck"

CALLBACK_ACTION_CHECK = "action:check"
CALLBACK_ACTION_UNCHECK = "action:uncheck"
CALLBACK_TEAM_PREFIX = "t:"
CALLBACK_EXERCISE_PREFIX = "e:"
CALLBACK_CITY_PREFIX = "city:"

KEY_CITY_TEAMS_CACHE = "city_teams_cache"  # {city: [teams]}
KEY_CITY_EXERCISES_CACHE = "city_exercises_cache"

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

    application.bot_data["city_teams_cache"] = {}
    application.bot_data["city_exercises_cache"] = {}
    logger.info("SheetsManager + caches initialized.")

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

    await update.callback_query.edit_message_text(
        f"**Город: {city}**\n\n{MESSAGE_ACTIONS}",
        reply_markup=_action_keyboard(),
        parse_mode="Markdown",
    )


async def _get_cached_teams(context: ContextTypes.DEFAULT_TYPE, city: str) -> list[str]:
    """Get teams from cached ones"""
    cache = context.application.bot_data.get(KEY_CITY_TEAMS_CACHE, {})
    if city in cache:
        logger.info(f"Using cached teams for {city}")
        return cache[city]

    manager = get_manager(context)
    spreadsheet_id = get_spreadsheet_id(context)

    try:
        teams = await manager.get_teams(spreadsheet_id, city=city)
        cache[city] = teams
        context.application.bot_data[KEY_CITY_TEAMS_CACHE] = cache
        logger.info(f"Cached {len(teams)} teams for {city}")
        return teams
    except Exception as e:
        logger.exception(f"Failed to cache teams for {city}")
        return []


async def _get_cached_exercises(context: ContextTypes.DEFAULT_TYPE, city: str) -> list[str]:
    """Gets exercise from cached ones"""
    cache = context.application.bot_data.get(KEY_CITY_EXERCISES_CACHE, {})
    if city in cache:
        logger.info(f"Using cached exercises for {city}")
        return cache[city]

    manager = get_manager(context)
    spreadsheet_id = get_spreadsheet_id(context)
    max_count, exclude_filter = context.application.bot_data.get("exercise_filter", (None, None))

    try:
        exercises = await manager.get_exercises(
            spreadsheet_id, max_count=max_count, excluded_names=exclude_filter, city=city
        )
        cache[city] = exercises
        context.application.bot_data[KEY_CITY_EXERCISES_CACHE] = cache
        logger.info(f"Cached {len(exercises)} exercises for {city}")
        return exercises
    except Exception as e:
        logger.exception(f"Failed to cache exercises for {city}")
        return []



async def _start_team_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, action_type: str) -> None:
    """Load teams from selected city."""
    context.user_data[KEY_ACTION_TYPE] = action_type
    manager = get_manager(context)
    spreadsheet_id = get_spreadsheet_id(context)

    city = context.user_data.get(KEY_CITY)
    if not city:
        await update.callback_query.edit_message_text(MESSAGE_NO_CITY)
        return

    teams = await _get_cached_teams(context, city)

    if not teams:
        await update.callback_query.edit_message_text("В таблице нет команд.")
        return



    context.user_data[KEY_TEAMS] = teams
    keyboard = [
        [InlineKeyboardButton(team, callback_data=f"{CALLBACK_TEAM_PREFIX}{i}")]
        for i, team in enumerate(teams)
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        MESSAGE_SELECT_TEAM,
        reply_markup=reply_markup,
    )

async def callback_action_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start check flow."""
    await update.callback_query.answer()
    await _start_team_flow(update, context, ACTION_CHECK)

async def callback_action_uncheck(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start uncheck flow."""
    await update.callback_query.answer()
    await _start_team_flow(update, context, ACTION_UNCHECK)

async def callback_team(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User selected team: load exercises from city."""
    await update.callback_query.answer()
    data = update.callback_query.data
    if not data.startswith(CALLBACK_TEAM_PREFIX):
        return

    try:
        index = int(data[len(CALLBACK_TEAM_PREFIX):], 10)
    except ValueError:
        return

    teams = context.user_data.get(KEY_TEAMS)
    if not teams or index < 0 or index >= len(teams):
        await update.callback_query.edit_message_text(MESSAGE_ERROR_GENERIC)
        return

    team_name = teams[index]
    context.user_data[KEY_SELECTED_TEAM] = team_name

    manager = get_manager(context)
    spreadsheet_id = get_spreadsheet_id(context)
    city = context.user_data.get(KEY_CITY)

    if not city:
        await update.callback_query.edit_message_text(MESSAGE_NO_CITY)
        return

    max_count, exclude_filter = context.application.bot_data.get("exercise_filter", (None, None))

    exercises = await _get_cached_exercises(context, city)

    if not exercises:
        await update.callback_query.edit_message_text("В таблице нет упражнений.")
        context.user_data.pop(KEY_SELECTED_TEAM, None)
        return

    context.user_data[KEY_EXERCISES] = exercises
    keyboard = [
        [InlineKeyboardButton(ex, callback_data=f"{CALLBACK_EXERCISE_PREFIX}{i}")]
        for i, ex in enumerate(exercises)
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        MESSAGE_SELECT_EXERCISE,
        reply_markup=reply_markup,
    )

async def callback_exercise(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute check/uncheck in selected city."""
    await update.callback_query.answer()
    data = update.callback_query.data
    if not data.startswith(CALLBACK_EXERCISE_PREFIX):
        return

    try:
        index = int(data[len(CALLBACK_EXERCISE_PREFIX):], 10)
    except ValueError:
        return

    exercises = context.user_data.get(KEY_EXERCISES)
    team_name = context.user_data.get(KEY_SELECTED_TEAM)
    if not exercises or team_name is None or index < 0 or index >= len(exercises):
        await update.callback_query.edit_message_text(MESSAGE_ERROR_GENERIC)
        return

    exercise_name = exercises[index]
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
    for key in (KEY_SELECTED_TEAM, KEY_EXERCISES, KEY_TEAMS, KEY_ACTION_TYPE):
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

    teams = await _get_cached_teams(context, city)
    if teams and not context.user_data.get(KEY_TEAMS):
        context.user_data[KEY_TEAMS] = teams

    exercises = await _get_cached_exercises(context, city) if context.user_data.get(KEY_SELECTED_TEAM) else []
    if exercises and not context.user_data.get(KEY_EXERCISES):
        context.user_data[KEY_EXERCISES] = exercises

    teams_in_userdata = context.user_data.get(KEY_TEAMS)
    if teams_in_userdata and not context.user_data.get(KEY_EXERCISES):
        keyboard = [[InlineKeyboardButton(team, callback_data=f"{CALLBACK_TEAM_PREFIX}{i}")]
                    for i, team in enumerate(teams_in_userdata)]
        return MESSAGE_SELECT_TEAM, InlineKeyboardMarkup(keyboard)

    exercises_in_userdata = context.user_data.get(KEY_EXERCISES)
    if exercises_in_userdata:
        keyboard = [[InlineKeyboardButton(ex, callback_data=f"{CALLBACK_EXERCISE_PREFIX}{i}")]
                    for i, ex in enumerate(exercises_in_userdata)]
        return MESSAGE_SELECT_EXERCISE, InlineKeyboardMarkup(keyboard)

    return MESSAGE_ACTIONS, _action_keyboard()

async def fallback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle ANY unexpected callback query.
    """
    await update.callback_query.answer()

    text, keyboard = await _get_current_step_prompt(context)

    await update.callback_query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode="Markdown",
    )

async def fallback_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle ANY text message that is not expected.
    """
    if not update.message:
        return

    text, keyboard = await _get_current_step_prompt(context)

    await update.message.reply_text(
        text,
        reply_markup=keyboard,
        parse_mode="Markdown",
    )

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles unknown command.
    """
    text, keyboard = _get_current_step_prompt(context)
    await update.message.reply_text(
        text,
        reply_markup=keyboard,
        parse_mode="Markdown",
    )

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
        CallbackQueryHandler(callback_team, pattern=f"^{CALLBACK_TEAM_PREFIX}\\d+$")
    )
    application.add_handler(
        CallbackQueryHandler(callback_exercise, pattern=f"^{CALLBACK_EXERCISE_PREFIX}\\d+$")
    )

    application.add_handler(CallbackQueryHandler(fallback_callback))
    #Simple text handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_message))
    # Unknown command handler
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    application.run_polling()

if __name__ == "__main__":
    main()
