"""
Custom exceptions for Google Sheets Manager.
"""


class SheetsManagerError(Exception):
    """Base exception for all Sheets Manager errors."""
    pass


class TeamNotFoundException(SheetsManagerError):
    """Raised when team name is not found in column A."""
    
    def __init__(self, team_name: str, spreadsheet_id: str):
        self.team_name = team_name
        self.spreadsheet_id = spreadsheet_id
        super().__init__(
            f"Team '{team_name}' not found in spreadsheet {spreadsheet_id}"
        )


class ExerciseNotFoundException(SheetsManagerError):
    """Raised when exercise name is not found in row 1."""
    
    def __init__(self, exercise_name: str, spreadsheet_id: str):
        self.exercise_name = exercise_name
        self.spreadsheet_id = spreadsheet_id
        super().__init__(
            f"Exercise '{exercise_name}' not found in spreadsheet {spreadsheet_id}"
        )


class GoogleSheetsAPIError(SheetsManagerError):
    """Raised when Google Sheets API returns an error."""
    
    def __init__(self, message: str, original_error=None):
        self.original_error = original_error
        super().__init__(f"Google Sheets API error: {message}")


class AuthenticationError(SheetsManagerError):
    """Raised when authentication with Google fails."""
    
    def __init__(self, message: str, original_error=None):
        self.original_error = original_error
        super().__init__(f"Authentication error: {message}")
