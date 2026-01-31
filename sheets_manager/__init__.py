"""
Google Sheets Manager - Async library for managing Google Spreadsheets with caching.
"""

from .manager import SheetsManager
from .exceptions import (
    SheetsManagerError,
    TeamNotFoundException,
    ExerciseNotFoundException,
    GoogleSheetsAPIError,
    AuthenticationError,
)

__version__ = "1.0.0"
__all__ = [
    "SheetsManager",
    "SheetsManagerError",
    "TeamNotFoundException",
    "ExerciseNotFoundException",
    "GoogleSheetsAPIError",
    "AuthenticationError",
]
