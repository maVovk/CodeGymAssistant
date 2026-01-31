"""
Telegram bot for marking team exercises in Google Sheets.
Flow: /start -> "Отметить упражнение" -> choose team (inline) -> choose exercise (inline) -> check_team_exercise().
"""

import logging
import os

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

ACTION_CHECK = "check"
ACTION_UNCHECK = "uncheck"

CALLBACK_ACTION_CHECK = "action:check"
CALLBACK_ACTION_UNCHECK = "action:uncheck"
CALLBACK_TEAM_PREFIX = "t:"
CALLBACK_EXERCISE_PREFIX = "e:"

MESSAGE_START = (
    "Привет! Я помогу отметить выполненное упражнение для команды в таблице. "
    "Нажмите кнопку ниже, чтобы начать."
)
MESSAGE_SELECT_TEAM = "Выберите команду:"
MESSAGE_SELECT_EXERCISE = "Выберите упражнение:"
MESSAGE_DONE = "Готово! Упражнение отмечено."
MESSAGE_UNCHECK_DONE = "Готово! Отметка снята."
MESSAGE_ERROR_SHEETS = "Ошибка доступа к таблице. Попробуйте позже."
MESSAGE_ERROR_AUTH = "Ошибка авторизации. Проверьте настройки бота."
MESSAGE_ERROR_GENERIC = "Произошла ошибка. Попробуйте позже."


def get_manager(context: ContextTypes.DEFAULT_TYPE) -> SheetsManager:
    return context.application.bot_data["sheets_manager"]


def get_spreadsheet_id(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.application.bot_data["spreadsheet_id"]


def _main_keyboard() -> InlineKeyboardMarkup:
    """Keyboard with 'Отметить упражнение' and 'Снять отметку' buttons."""
    keyboard = [
        [
            InlineKeyboardButton("Отметить упражнение", callback_data=CALLBACK_ACTION_CHECK),
            InlineKeyboardButton("Снять отметку", callback_data=CALLBACK_ACTION_UNCHECK),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


# Exercise list filter: use first N columns and/or only these names (from .env or fallback).
N_EXERCISES_ENV = "N_EXERCISES"
EXERCISE_NAMES_ENV = "EXCLUDED_NAMES"
DEFAULT_EXCLUDED_NAMES = ["Сдано задач", "Разница"]


def _get_exercise_filter():
    """Return (max_count, exclude_filter) for get_exercises. Prefer EXERCISE_NAMES over N_EXERCISES."""
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
    logger.info("SheetsManager initialized.")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start: show greeting and action buttons."""
    await update.message.reply_text(
        MESSAGE_START,
        reply_markup=_main_keyboard(),
    )


async def _start_team_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, action_type: str) -> None:
    """Load teams and show team buttons. action_type is ACTION_CHECK or ACTION_UNCHECK."""
    context.user_data[KEY_ACTION_TYPE] = action_type
    manager = get_manager(context)
    spreadsheet_id = get_spreadsheet_id(context)

    try:
        teams = await manager.get_teams(spreadsheet_id)
    except (GoogleSheetsAPIError, AuthenticationError) as e:
        logger.exception("Failed to get teams")
        msg = MESSAGE_ERROR_AUTH if isinstance(e, AuthenticationError) else MESSAGE_ERROR_SHEETS
        await update.callback_query.edit_message_text(msg)
        return

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
    """User pressed 'Отметить упражнение': start team flow for check."""
    await update.callback_query.answer()
    await _start_team_flow(update, context, ACTION_CHECK)


async def callback_action_uncheck(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User pressed 'Снять отметку': start team flow for uncheck."""
    await update.callback_query.answer()
    await _start_team_flow(update, context, ACTION_UNCHECK)


async def callback_team(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User selected team: save it, load exercises, show inline exercise buttons."""
    await update.callback_query.answer()
    data = update.callback_query.data
    if not data.startswith(CALLBACK_TEAM_PREFIX):
        return

    try:
        index = int(data[len(CALLBACK_TEAM_PREFIX) :], 10)
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

    max_count, exclude_filter = context.application.bot_data.get("exercise_filter", (None, None))
    try:
        exercises = await manager.get_exercises(
            spreadsheet_id,
            max_count=max_count,
            excluded_names=exclude_filter,
        )
    except (GoogleSheetsAPIError, AuthenticationError) as e:
        logger.exception("Failed to get exercises")
        msg = MESSAGE_ERROR_AUTH if isinstance(e, AuthenticationError) else MESSAGE_ERROR_SHEETS
        await update.callback_query.edit_message_text(msg)
        context.user_data.pop(KEY_SELECTED_TEAM, None)
        return

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
    """User selected exercise: call check_team_exercise and clear state."""
    await update.callback_query.answer()
    data = update.callback_query.data
    if not data.startswith(CALLBACK_EXERCISE_PREFIX):
        return

    try:
        index = int(data[len(CALLBACK_EXERCISE_PREFIX) :], 10)
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

    try:
        if action_type == ACTION_UNCHECK:
            await manager.uncheck_team_exercise(
                spreadsheet_id=spreadsheet_id,
                team_name=team_name,
                exercise_name=exercise_name,
            )
        else:
            await manager.check_team_exercise(
                spreadsheet_id=spreadsheet_id,
                team_name=team_name,
                exercise_name=exercise_name,
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
        logger.exception("check_team_exercise failed")
        msg = MESSAGE_ERROR_AUTH if isinstance(e, AuthenticationError) else MESSAGE_ERROR_SHEETS
        await update.callback_query.edit_message_text(msg)
    else:
        done_message = MESSAGE_UNCHECK_DONE if action_type == ACTION_UNCHECK else MESSAGE_DONE
        await update.callback_query.edit_message_text(
            done_message,
            reply_markup=_main_keyboard(),
        )

    for key in (KEY_SELECTED_TEAM, KEY_EXERCISES, KEY_TEAMS, KEY_ACTION_TYPE):
        context.user_data.pop(key, None)


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

    application.add_handler(CommandHandler("start", cmd_start))
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

    application.run_polling()


if __name__ == "__main__":
    main()
