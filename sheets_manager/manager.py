"""
Main manager class for Google Sheets operations.
"""

import os
from typing import List, Optional, Tuple, Any
import gspread_asyncio
from google.oauth2.service_account import Credentials

from .cache import SpreadsheetCache
from .exceptions import (
    TeamNotFoundException,
    ExerciseNotFoundException,
    GoogleSheetsAPIError,
    AuthenticationError,
)
from .utils import (
    column_number_to_letter,
    validate_team_name,
    validate_exercise_name,
    validate_spreadsheet_id,
    build_cell_notation,
    normalize_name,
)


class SheetsManager:
    """
    Async manager for Google Sheets operations with caching support.
    
    This class provides methods to check team exercises in Google Spreadsheets
    with automatic caching of spreadsheet structure to minimize API calls.
    """
    
    # Google Sheets API scopes
    SCOPES = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive',
    ]
    # Physical exercises layout: O2:W2
    EXERCISE_HEADER_ROW_INDEX = 1  # zero-based index for row 2
    EXERCISE_START_COL_INDEX = 14  # zero-based index for column O
    EXERCISE_END_COL_INDEX_EXCLUSIVE = 23  # exclusive index for column W
    EXERCISE_START_COL_NUMBER = 15  # one-based column number for O

    # Algo tasks layout: C:L
    TASKS_START_COL_NUMBER = 3  # one-based column number for C
    TASKS_END_COL_NUMBER = 12  # one-based column number for L

    TEAM_START_ROW_INDEX = 2  # zero-based index for row 3
    TEAM_END_ROW_INDEX_EXCLUSIVE = 40  # exclusive index for row 40
    TEAM_START_ROW_NUMBER = 3  # one-based row number for row 3
    TEAM_COL_INDEX = 1  # zero-based index for column B

    WORKSHEET_TITLE = "Таблица"

    CHECKED_VALUE = "зачет"
    UNCHECKED_VALUE = "незачет"
    
    def __init__(
        self,
        credentials_file: Optional[str] = None,
        cache_ttl: int = 3600
    ):
        """
        Initialize the SheetsManager.
        
        Args:
            credentials_file: Path to Google service account JSON file.
                                 If None, will try to load from environment.
            cache_ttl: Cache time-to-live in seconds (default: 3600 = 1 hour)
            
        Raises:
            AuthenticationError: If service account file is not found or invalid
        """
        self.credentials_file = credentials_file
        self.cache = SpreadsheetCache(ttl_seconds=cache_ttl)
        self._agcm: Optional[gspread_asyncio.AsyncioGspreadClientManager] = None
        self._initialized = False
    
    def _get_creds(self) -> Credentials:
        """
        Get credentials from service account file.
        
        Returns:
            Google service account credentials
            
        Raises:
            AuthenticationError: If credentials cannot be loaded
        """
        if not self.credentials_file:
            raise AuthenticationError(
                "Service account file path not provided. "
                "Pass it to constructor or set GOOGLE_account_file env variable."
            )
        
        if not os.path.exists(self.credentials_file):
            raise AuthenticationError(
                f"Service account file not found: {self.credentials_file}"
            )
        
        try:
            creds = Credentials.from_service_account_file(self.credentials_file, scopes=self.SCOPES)
            return creds
        except Exception as e:
            raise AuthenticationError(
                f"Failed to load credentials from {self.credentials_file}",
                original_error=e
            )
    
    async def initialize(self) -> None:
        """
        Initialize the async gspread client.
        
        Must be called before using any other methods.
        
        Raises:
            AuthenticationError: If authentication fails
        """
        if self._initialized:
            return
        
        try:
            self._agcm = gspread_asyncio.AsyncioGspreadClientManager(self._get_creds)
            self._initialized = True
        except Exception as e:
            raise AuthenticationError(
                "Failed to initialize Google Sheets client",
                original_error=e
            )
    
    def _ensure_initialized(self) -> None:
        """Check if manager is initialized."""
        if not self._initialized or self._agcm is None:
            raise RuntimeError(
                "SheetsManager not initialized. Call await manager.initialize() first."
            )
    
    async def _load_structure(
        self,
        spreadsheet_id: str,
        city: Optional[str] = None
    ) -> Tuple[List[str], List[str]]:
        """
        Load spreadsheet structure (teams and exercises) from API.
        
        Args:
            spreadsheet_id: The ID of the spreadsheet
            
        Returns:
            Tuple of (team_names, exercise_names)
            
        Raises:
            GoogleSheetsAPIError: If API call fails
        """
        self._ensure_initialized()
        
        try:
            agc = await self._agcm.authorize()
            spreadsheet = await agc.open_by_key(spreadsheet_id)

            worksheet = await self._get_target_worksheet(spreadsheet)
            
            # Get all values at once for efficiency
            all_values = await worksheet.get_all_values()
            
            if not all_values or len(all_values) < 1:
                raise GoogleSheetsAPIError("Spreadsheet appears to be empty")
            
            # Extract physical exercise names from fixed range O2:W2.
            exercise_names = []
            if len(all_values) > self.EXERCISE_HEADER_ROW_INDEX:
                header_row = all_values[self.EXERCISE_HEADER_ROW_INDEX]
                exercise_names = [
                    cell.strip()
                    for cell in header_row[
                        self.EXERCISE_START_COL_INDEX:self.EXERCISE_END_COL_INDEX_EXCLUSIVE
                    ]
                    if cell.strip()
                ]
            
            # Extract team names from fixed range B3:B40.
            team_names = []
            for row in all_values[
                self.TEAM_START_ROW_INDEX:self.TEAM_END_ROW_INDEX_EXCLUSIVE
            ]:
                team_cell = (
                    row[self.TEAM_COL_INDEX].strip()
                    if len(row) > self.TEAM_COL_INDEX
                    else ""
                )
                if team_cell:
                    team_names.append(team_cell)
            
            return team_names, exercise_names
        except Exception as e:
            raise GoogleSheetsAPIError(
                f"Unexpected error loading spreadsheet: {str(e)}",
                original_error=e
            )
    
    async def _find_team_row(
        self,
        teams: List[str],
        team_name: str,
        spreadsheet_id: str
    ) -> int:
        """
        Find row number for a team.
        
        Args:
            teams: List of team names
            team_name: Team name to find
            spreadsheet_id: Spreadsheet ID (for error messages)
            
        Returns:
            Row number (1-based, accounting for header row)
            
        Raises:
            TeamNotFoundException: If team is not found
        """
        normalized_search = normalize_name(team_name)
        
        for idx, team in enumerate(teams):
            if normalize_name(team) == normalized_search:
                # Row index: teams start at row 3.
                return idx + self.TEAM_START_ROW_NUMBER
        
        raise TeamNotFoundException(team_name, spreadsheet_id)
    
    async def _find_exercise_col(
        self,
        exercises: List[str],
        exercise_name: str,
        spreadsheet_id: str
    ) -> int:
        """
        Find column number for an exercise.
        
        Args:
            exercises: List of exercise names
            exercise_name: Exercise name to find
            spreadsheet_id: Spreadsheet ID (for error messages)
            
        Returns:
            Column number (1-based, accounting for team names column)
            
        Raises:
            ExerciseNotFoundException: If exercise is not found
        """
        normalized_search = normalize_name(exercise_name)
        
        for idx, exercise in enumerate(exercises):
            if normalize_name(exercise) == normalized_search:
                # Column index: physical exercises start at column O.
                return idx + self.EXERCISE_START_COL_NUMBER
        
        raise ExerciseNotFoundException(exercise_name, spreadsheet_id)
    
    async def _get_cell_value(
        self,
        spreadsheet_id: str,
        row: int,
        col: int,
        city: Optional[str] = None
    ) -> Any:
        """
        Get current value from a cell.
        
        Args:
            spreadsheet_id: The ID of the spreadsheet
            row: Row number (1-based)
            col: Column number (1-based)
            
        Returns:
            Current cell value
            
        Raises:
            GoogleSheetsAPIError: If API call fails
        """
        cell_notation = build_cell_notation(row, col)
        
        try:
            agc = await self._agcm.authorize()
            spreadsheet = await agc.open_by_key(spreadsheet_id)
            worksheet = await self._get_target_worksheet(spreadsheet)
            
            cell = await worksheet.acell(cell_notation)
            return cell.value
            
        except Exception as e:
            raise GoogleSheetsAPIError(
                f"Failed to read cell {cell_notation}: {str(e)}",
                original_error=e
            )

    async def _update_cell_value(
        self,
        spreadsheet_id: str,
        row: int,
        col: int,
        value: Any,
        city: Optional[str] = None
    ) -> Any:
        """
        Update a cell and return its previous value.

        Args:
            spreadsheet_id: The ID of the spreadsheet
            row: Row number (1-based)
            col: Column number (1-based)
            value: New value to set

        Returns:
            Previous value of the cell

        Raises:
            GoogleSheetsAPIError: If API call fails
        """
        cell_notation = build_cell_notation(row, col)

        try:
            agc = await self._agcm.authorize()
            spreadsheet = await agc.open_by_key(spreadsheet_id)
            worksheet = await self._get_target_worksheet(spreadsheet)

            # Get current value first
            cell = await worksheet.acell(cell_notation)
            previous_value = cell.value

            # Update with new value
            await worksheet.update_acell(cell_notation, value)

            return previous_value

        except Exception as e:
            raise GoogleSheetsAPIError(
                f"Failed to update cell {cell_notation}: {str(e)}",
                original_error=e
            )

    async def check_team_exercise(
        self,
        spreadsheet_id: str,
        team_name: str,
        exercise_name: str,
        city: Optional[str] = None
    ) -> Any:
        """
        Check (mark as completed) a team's exercise by setting checkbox to "зачёт".
        
        This method will:
        1. Validate inputs
        2. Load structure from cache or API (only structure, not values)
        3. Find the correct cell
        4. Update the cell with "зачёт"
        5. Return the previous value
        
        Args:
            spreadsheet_id: The ID of the Google Spreadsheet
            team_name: Name of the team (from column A)
            exercise_name: Name of the exercise (from row 1)
            
        Returns:
            Previous value of the cell (before setting to "зачёт")
            
        Raises:
            ValueError: If inputs are invalid
            TeamNotFoundException: If team is not found
            ExerciseNotFoundException: If exercise is not found
            GoogleSheetsAPIError: If API call fails
            RuntimeError: If manager is not initialized
            
        Example:
            >>> manager = SheetsManager("credentials.json")
            >>> await manager.initialize()
            >>> previous = await manager.check_team_exercise(
            ...     "1abc123...",
            ...     "Team Alpha",
            ...     "Exercise 1"
            ... )
            >>> print(f"Previous value: {previous}")
        """
        # Validate inputs
        validate_spreadsheet_id(spreadsheet_id)
        validate_team_name(team_name)
        validate_exercise_name(exercise_name)
        
        self._ensure_initialized()
        cache_key = f"{spreadsheet_id}:{city or 'default'}"
        # Get structure from cache or load from API (only team/exercise names)
        async def fetch_structure():
            return await self._load_structure(spreadsheet_id, city=city)
        
        team_names, exercise_names = await self.cache.get_or_fetch(
            cache_key,
            fetch_structure
        )
        
        # Find row and column
        row = await self._find_team_row(team_names, team_name, spreadsheet_id)
        col = await self._find_exercise_col(exercise_names, exercise_name, spreadsheet_id)
        
        # Update the cell and return previous value
        previous_value = await self._update_cell_value(
            spreadsheet_id,
            row,
            col,
            self.CHECKED_VALUE,
            city=city,
        )
        return previous_value
    
    async def uncheck_team_exercise(
        self,
        spreadsheet_id: str,
        team_name: str,
        exercise_name: str,
        city: Optional[str] = None
    ) -> Any:
        """
        Uncheck (mark as incomplete) a team's exercise by setting checkbox to "незачёт".
        
        Useful for correcting mistakes or resetting exercise status.
        
        This method will:
        1. Validate inputs
        2. Load structure from cache or API (only structure, not values)
        3. Find the correct cell
        4. Update the cell with "незачёт"
        5. Return the previous value
        
        Args:
            spreadsheet_id: The ID of the Google Spreadsheet
            team_name: Name of the team (from column A)
            exercise_name: Name of the exercise (from row 1)
            
        Returns:
            Previous value of the cell (before setting to "незачёт")
            
        Raises:
            ValueError: If inputs are invalid
            TeamNotFoundException: If team is not found
            ExerciseNotFoundException: If exercise is not found
            GoogleSheetsAPIError: If API call fails
            RuntimeError: If manager is not initialized
            
        Example:
            >>> manager = SheetsManager("credentials.json")
            >>> await manager.initialize()
            >>> previous = await manager.uncheck_team_exercise(
            ...     "1abc123...",
            ...     "Team Alpha",
            ...     "Exercise 1"
            ... )
            >>> print(f"Previous value was: {previous}")
        """
        # Validate inputs
        validate_spreadsheet_id(spreadsheet_id)
        validate_team_name(team_name)
        validate_exercise_name(exercise_name)
        
        self._ensure_initialized()
        cache_key = f"{spreadsheet_id}:{city or 'default'}"
        # Get structure from cache or load from API (only team/exercise names)
        async def fetch_structure():
            return await self._load_structure(spreadsheet_id, city=city)
        
        team_names, exercise_names = await self.cache.get_or_fetch(
            cache_key,
            fetch_structure
        )
        
        # Find row and column
        row = await self._find_team_row(team_names, team_name, spreadsheet_id)
        col = await self._find_exercise_col(exercise_names, exercise_name, spreadsheet_id)
        
        # Update the cell to "незачёт" and return previous value
        previous_value = await self._update_cell_value(
            spreadsheet_id,
            row,
            col,
            self.UNCHECKED_VALUE,
            city=city,
        )
        return previous_value
    
    async def get_team_exercise_status(
        self,
        spreadsheet_id: str,
        team_name: str,
        exercise_name: str,
        city: Optional[str] = None
    ) -> Any:
        """
        Get current status of a team's exercise without modifying it.
        
        Args:
            spreadsheet_id: The ID of the Google Spreadsheet
            team_name: Name of the team (from column A)
            exercise_name: Name of the exercise (from row 1)
            
        Returns:
            Current value of the cell
            
        Raises:
            ValueError: If inputs are invalid
            TeamNotFoundException: If team is not found
            ExerciseNotFoundException: If exercise is not found
            GoogleSheetsAPIError: If API call fails
            RuntimeError: If manager is not initialized
            
        Example:
            >>> status = await manager.get_team_exercise_status(
            ...     "1abc123...",
            ...     "Team Alpha",
            ...     "Exercise 1"
            ... )
            >>> print(f"Status: {status}")
        """
        # Validate inputs
        validate_spreadsheet_id(spreadsheet_id)
        validate_team_name(team_name)
        validate_exercise_name(exercise_name)
        
        self._ensure_initialized()

        cache_key = f"{spreadsheet_id}:{city or 'default'}"
        # Get structure from cache or load from API
        async def fetch_structure():
            return await self._load_structure(spreadsheet_id, city=city)
        
        team_names, exercise_names = await self.cache.get_or_fetch(
            cache_key,
            fetch_structure
        )
        
        # Find row and column
        row = await self._find_team_row(team_names, team_name, spreadsheet_id)
        col = await self._find_exercise_col(exercise_names, exercise_name, spreadsheet_id)
        
        # Get cell value
        return await self._get_cell_value(spreadsheet_id, row, col, city=city)

    async def get_team_solved_count(
        self,
        spreadsheet_id: str,
        team_name: str,
        city: Optional[str] = None,
    ) -> int:
        """
        Count solved tasks for a team as number of non-empty exercise cells.

        The count is calculated in the algo tasks range C:L for the team's row.
        """
        validate_spreadsheet_id(spreadsheet_id)
        validate_team_name(team_name)
        self._ensure_initialized()

        cache_key = f"{spreadsheet_id}:{city or 'default'}"

        async def fetch_structure():
            return await self._load_structure(spreadsheet_id, city=city)

        team_names, _ = await self.cache.get_or_fetch(
            cache_key,
            fetch_structure,
        )
        row = await self._find_team_row(team_names, team_name, spreadsheet_id)

        try:
            agc = await self._agcm.authorize()
            spreadsheet = await agc.open_by_key(spreadsheet_id)
            worksheet = await self._get_target_worksheet(spreadsheet)

            start_col_letter = column_number_to_letter(self.TASKS_START_COL_NUMBER)
            end_col_letter = column_number_to_letter(self.TASKS_END_COL_NUMBER)
            range_notation = f"{start_col_letter}{row}:{end_col_letter}{row}"

            row_values = await worksheet.get(range_notation)
            if not row_values:
                return 0

            return sum(
                1
                for cell in row_values[0]
                if str(cell).strip()
            )
        except Exception as e:
            raise GoogleSheetsAPIError(
                f"Failed to count solved tasks for team '{team_name}': {str(e)}",
                original_error=e,
            )
    
    async def get_teams(self, spreadsheet_id: str, city: Optional[str] = None) -> List[str]:
        """
        Get list of all team names from the spreadsheet.
        
        Useful for validation and checking available teams at startup.
        Uses cache if available, otherwise loads from API.
        
        Args:
            spreadsheet_id: The ID of the Google Spreadsheet
            
        Returns:
            List of team names from column A (starting row 2)
            
        Raises:
            ValueError: If spreadsheet_id is invalid
            GoogleSheetsAPIError: If API call fails
            RuntimeError: If manager is not initialized
            
        Example:
            >>> manager = SheetsManager("credentials.json")
            >>> await manager.initialize()
            >>> teams = await manager.get_teams("1abc123...")
            >>> print(f"Available teams: {teams}")
        """
        # Validate input
        validate_spreadsheet_id(spreadsheet_id)
        self._ensure_initialized()

        cache_key = f"{spreadsheet_id}:{city or 'default'}"
        # Get structure from cache or load from API
        async def fetch_structure():
            return await self._load_structure(spreadsheet_id, city=city)
        
        team_names, _ = await self.cache.get_or_fetch(
            cache_key,
            fetch_structure
        )
        
        return team_names
    
    async def get_exercises(
        self,
        spreadsheet_id: str,
        max_count: Optional[int] = None,
        excluded_names: Optional[List[str]] = None,
        city: Optional[str] = None
    ) -> List[str]:
        """
        Get list of exercise names from the spreadsheet.
        
        Useful for validation and checking available exercises at startup.
        Uses cache if available, otherwise loads from API.
        
        Args:
            spreadsheet_id: The ID of the Google Spreadsheet
            max_count: If set, return only the first max_count exercises (columns).
            excluded_names: If set, return only exercises whose names are not in this list,
                in the order given.
            
        Returns:
            List of physical exercise names from fixed header range O2:W2, possibly filtered.
            
        Raises:
            ValueError: If spreadsheet_id is invalid
            GoogleSheetsAPIError: If API call fails
            RuntimeError: If manager is not initialized
            
        Example:
            >>> manager = SheetsManager("credentials.json")
            >>> await manager.initialize()
            >>> exercises = await manager.get_exercises("1abc123...")
            >>> exercises = await manager.get_exercises("1abc...", max_count=3)
            >>> exercises = await manager.get_exercises("1abc...", excluded_names=["Сдано задач", "Разница"])
        """
        # Validate input
        validate_spreadsheet_id(spreadsheet_id)
        self._ensure_initialized()

        cache_key = f"{spreadsheet_id}:{city or 'default'}"

        # Get structure from cache or load from API
        async def fetch_structure():
            return await self._load_structure(spreadsheet_id, city=city)
        
        _, exercise_names = await self.cache.get_or_fetch(
            cache_key,
            fetch_structure
        )
        
        if excluded_names is not None:
            return [name for name in exercise_names if name not in excluded_names]
        if max_count is not None:
            return exercise_names[:max_count]
        return exercise_names
    
    async def add_exercise(
        self,
        spreadsheet_id: str,
        exercise_name: str,
        city: Optional[str] = None,
    ) -> None:
        """
        Add a new exercise column to the spreadsheet.

        Appends a new column at the end of the header row with the given name
        (bold), inserts checkboxes for every existing team row, and
        invalidates cache.
        """
        validate_spreadsheet_id(spreadsheet_id)
        validate_exercise_name(exercise_name)
        self._ensure_initialized()

        try:
            agc = await self._agcm.authorize()
            spreadsheet = await agc.open_by_key(spreadsheet_id)
            worksheet = await self._get_target_worksheet(spreadsheet)

            header = await worksheet.row_values(1)
            next_col = len(header) + 1

            team_col = await worksheet.col_values(1)
            team_count = max(len(team_col) - 1, 0)

            col_letter = column_number_to_letter(next_col)
            await worksheet.update_cell(1, next_col, exercise_name)

            if team_count > 0:
                cell_range = f"{col_letter}2:{col_letter}{1 + team_count}"
                await worksheet.update(
                    cell_range,
                    [[self.UNCHECKED_VALUE]] * team_count,
                    raw=False,
                )

            sheet_id = worksheet.id
            col_idx = next_col - 1

            requests = [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 0,
                            "endRowIndex": 1,
                            "startColumnIndex": col_idx,
                            "endColumnIndex": col_idx + 1,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "textFormat": {"bold": True}
                            }
                        },
                        "fields": "userEnteredFormat.textFormat.bold",
                    }
                },
            ]

            if team_count > 0:
                requests.append({
                    "setDataValidation": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "endRowIndex": 1 + team_count,
                            "startColumnIndex": col_idx,
                            "endColumnIndex": col_idx + 1,
                        },
                        "rule": {
                            "condition": {"type": "BOOLEAN"},
                            "showCustomUi": True,
                        },
                    }
                })

            await spreadsheet.batch_update({"requests": requests})

            cache_key = f"{spreadsheet_id}:{city or 'default'}"
            await self.cache.invalidate(cache_key)

        except GoogleSheetsAPIError:
            raise
        except Exception as e:
            raise GoogleSheetsAPIError(
                f"Failed to add exercise '{exercise_name}': {str(e)}",
                original_error=e,
            )

    async def rename_exercise(
        self,
        spreadsheet_id: str,
        old_name: str,
        new_name: str,
        city: Optional[str] = None,
    ) -> None:
        """
        Rename an exercise column header without touching its data.

        Matches by exact name (case-sensitive) and preserves bold formatting.
        """
        validate_spreadsheet_id(spreadsheet_id)
        validate_exercise_name(old_name)
        validate_exercise_name(new_name)
        self._ensure_initialized()

        try:
            agc = await self._agcm.authorize()
            spreadsheet = await agc.open_by_key(spreadsheet_id)
            worksheet = await self._get_target_worksheet(spreadsheet)

            header = await worksheet.row_values(1)

            col_index = None
            for i, name in enumerate(header):
                if name.strip() == old_name.strip():
                    col_index = i + 1
                    break

            if col_index is None:
                raise ExerciseNotFoundException(old_name, spreadsheet_id)

            await worksheet.update_cell(1, col_index, new_name)

            sheet_id = worksheet.id
            col_idx = col_index - 1
            await spreadsheet.batch_update({"requests": [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 0,
                            "endRowIndex": 1,
                            "startColumnIndex": col_idx,
                            "endColumnIndex": col_idx + 1,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "textFormat": {"bold": True}
                            }
                        },
                        "fields": "userEnteredFormat.textFormat.bold",
                    }
                },
            ]})

            cache_key = f"{spreadsheet_id}:{city or 'default'}"
            await self.cache.invalidate(cache_key)

        except (ExerciseNotFoundException, GoogleSheetsAPIError):
            raise
        except Exception as e:
            raise GoogleSheetsAPIError(
                f"Failed to rename exercise '{old_name}' -> '{new_name}': {str(e)}",
                original_error=e,
            )

    async def remove_exercise(
        self,
        spreadsheet_id: str,
        exercise_name: str,
        city: Optional[str] = None,
    ) -> None:
        """
        Remove an exercise column from the spreadsheet.

        Finds the column by header name, deletes it entirely,
        and invalidates the structure cache.
        """
        validate_spreadsheet_id(spreadsheet_id)
        validate_exercise_name(exercise_name)
        self._ensure_initialized()

        try:
            agc = await self._agcm.authorize()
            spreadsheet = await agc.open_by_key(spreadsheet_id)
            worksheet = await self._get_target_worksheet(spreadsheet)

            header = await worksheet.row_values(1)
            normalized = normalize_name(exercise_name)

            col_index = None
            for i, name in enumerate(header):
                if normalize_name(name) == normalized:
                    col_index = i + 1
                    break

            if col_index is None:
                raise ExerciseNotFoundException(exercise_name, spreadsheet_id)

            await worksheet.delete_columns(col_index)

            cache_key = f"{spreadsheet_id}:{city or 'default'}"
            await self.cache.invalidate(cache_key)

        except (ExerciseNotFoundException, GoogleSheetsAPIError):
            raise
        except Exception as e:
            raise GoogleSheetsAPIError(
                f"Failed to remove exercise '{exercise_name}': {str(e)}",
                original_error=e,
            )

    async def invalidate_cache(self, spreadsheet_id: str) -> None:
        """
        Manually invalidate cache for a specific spreadsheet.
        
        Useful if you know the spreadsheet structure has changed.
        
        Args:
            spreadsheet_id: The ID of the spreadsheet to invalidate
        """
        await self.cache.invalidate(spreadsheet_id)
    
    async def clear_all_cache(self) -> None:
        """Clear all cached data."""
        await self.cache.clear()
    
    def get_cache_stats(self) -> dict:
        """
        Get statistics about the cache.
        
        Returns:
            Dictionary with cache statistics
        """
        return self.cache.get_cache_stats()

    async def _get_target_worksheet(self, spreadsheet):
        try:
            worksheets = await spreadsheet.worksheets()
            normalized_title = normalize_name(self.WORKSHEET_TITLE)

            for ws in worksheets:
                if normalize_name(ws.title) == normalized_title:
                    return ws
            raise GoogleSheetsAPIError(
                f"Worksheet '{self.WORKSHEET_TITLE}' not found."
            )

        except Exception as e:
            raise GoogleSheetsAPIError(
                f"Failed to find worksheet '{self.WORKSHEET_TITLE}': {e}",
                original_error=e
            )
