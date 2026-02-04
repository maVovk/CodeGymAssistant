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

            if city is not None:
                worksheet = await self._get_worksheet_by_city(spreadsheet, city)
            else:
                worksheet = await spreadsheet.get_worksheet(0) # First sheet
            
            # Batch read: Get teams from column A (starting row 2) and exercises from row 1 (starting column B)
            # We'll read a reasonable range - A2:A100 for teams, B1:ZZ1 for exercises
            
            # Get all values at once for efficiency
            all_values = await worksheet.get_all_values()
            
            if not all_values or len(all_values) < 1:
                raise GoogleSheetsAPIError("Spreadsheet appears to be empty")
            
            # Extract exercise names from row 1 (index 0), starting from column B (index 1)
            exercise_names = []
            if len(all_values) > 0:
                first_row = all_values[0]
                exercise_names = [cell for cell in first_row[1:] if cell.strip()]
            
            # Extract team names from column A (index 0), starting from row 2 (index 1)
            team_names = []
            for row in all_values[1:]:
                if row and row[0].strip():
                    team_names.append(row[0].strip())
                else:
                    # Stop at first empty team name
                    break
            
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
                # Row index: teams start at row 2 (index 0 in teams list = row 2)
                return idx + 2
        
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
                # Column index: exercises start at column B (index 0 in exercises list = column 2)
                return idx + 2
        
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
            if city is not None:
                worksheet = await self._get_worksheet_by_city(spreadsheet, city)
            else:
                worksheet = await spreadsheet.get_worksheet(0)
            
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
            if city is not None:
                worksheet = await self._get_worksheet_by_city(spreadsheet, city)
            else:
                worksheet = await spreadsheet.get_worksheet(0)

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
        Check (mark as completed) a team's exercise by setting checkbox to TRUE.
        
        This method will:
        1. Validate inputs
        2. Load structure from cache or API (only structure, not values)
        3. Find the correct cell
        4. Update the cell with TRUE (native Google Sheets checkbox)
        5. Return the previous value
        
        Args:
            spreadsheet_id: The ID of the Google Spreadsheet
            team_name: Name of the team (from column A)
            exercise_name: Name of the exercise (from row 1)
            
        Returns:
            Previous value of the cell (before setting to TRUE)
            
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
        previous_value = await self._update_cell_value(spreadsheet_id, row, col, True, city=city)
        return previous_value
    
    async def uncheck_team_exercise(
        self,
        spreadsheet_id: str,
        team_name: str,
        exercise_name: str,
        city: Optional[str] = None
    ) -> Any:
        """
        Uncheck (mark as incomplete) a team's exercise by setting checkbox to FALSE.
        
        Useful for correcting mistakes or resetting exercise status.
        
        This method will:
        1. Validate inputs
        2. Load structure from cache or API (only structure, not values)
        3. Find the correct cell
        4. Update the cell with FALSE (native Google Sheets checkbox)
        5. Return the previous value
        
        Args:
            spreadsheet_id: The ID of the Google Spreadsheet
            team_name: Name of the team (from column A)
            exercise_name: Name of the exercise (from row 1)
            
        Returns:
            Previous value of the cell (before setting to FALSE)
            
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
        
        # Update the cell to FALSE and return previous value
        previous_value = await self._update_cell_value(spreadsheet_id, row, col, False, city=city)
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
            List of exercise names from row 1 (starting column B), possibly filtered.
            
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

    async def _get_worksheet_by_city(self, spreadsheet, city_name: str):
        try:
            worksheets = await spreadsheet.worksheets()
            normalized_city = normalize_name(city_name)

            for ws in worksheets:
                if normalize_name(ws.title) == normalized_city:
                    return ws
            raise GoogleSheetsAPIError(
                f"Worksheet (city) '{city_name}' not found."
            )

        except Exception as e:
            raise GoogleSheetsAPIError(
                f"Failed to find worksheet for city '{city_name}': {e}",
                original_error=e
            )