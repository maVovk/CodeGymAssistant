"""
Utility functions for Google Sheets operations.
"""

from typing import Optional


def column_number_to_letter(column_number: int) -> str:
    """
    Convert column number to letter notation (1 -> A, 2 -> B, ... 27 -> AA).
    
    Args:
        column_number: Column number (1-based index)
        
    Returns:
        Column letter notation (e.g., 'A', 'B', 'AA', 'AB')
        
    Examples:
        >>> column_number_to_letter(1)
        'A'
        >>> column_number_to_letter(26)
        'Z'
        >>> column_number_to_letter(27)
        'AA'
        >>> column_number_to_letter(28)
        'AB'
    """
    result = ""
    while column_number > 0:
        column_number -= 1  # Convert to 0-based
        result = chr(65 + (column_number % 26)) + result
        column_number //= 26
    return result


def column_letter_to_number(column_letter: str) -> int:
    """
    Convert column letter notation to number (A -> 1, B -> 2, ... AA -> 27).
    
    Args:
        column_letter: Column letter notation (e.g., 'A', 'B', 'AA')
        
    Returns:
        Column number (1-based index)
        
    Examples:
        >>> column_letter_to_number('A')
        1
        >>> column_letter_to_number('Z')
        26
        >>> column_letter_to_number('AA')
        27
    """
    result = 0
    for char in column_letter.upper():
        result = result * 26 + (ord(char) - 64)
    return result


def validate_team_name(team_name: Optional[str]) -> None:
    """
    Validate team name input.
    
    Args:
        team_name: Team name to validate
        
    Raises:
        ValueError: If team name is invalid
    """
    if not team_name:
        raise ValueError("Team name cannot be empty")
    
    if not isinstance(team_name, str):
        raise ValueError("Team name must be a string")
    
    if len(team_name.strip()) == 0:
        raise ValueError("Team name cannot be only whitespace")


def validate_exercise_name(exercise_name: Optional[str]) -> None:
    """
    Validate exercise name input.
    
    Args:
        exercise_name: Exercise name to validate
        
    Raises:
        ValueError: If exercise name is invalid
    """
    if not exercise_name:
        raise ValueError("Exercise name cannot be empty")
    
    if not isinstance(exercise_name, str):
        raise ValueError("Exercise name must be a string")
    
    if len(exercise_name.strip()) == 0:
        raise ValueError("Exercise name cannot be only whitespace")


def validate_spreadsheet_id(spreadsheet_id: Optional[str]) -> None:
    """
    Validate spreadsheet ID input.
    
    Args:
        spreadsheet_id: Spreadsheet ID to validate
        
    Raises:
        ValueError: If spreadsheet ID is invalid
    """
    if not spreadsheet_id:
        raise ValueError("Spreadsheet ID cannot be empty")
    
    if not isinstance(spreadsheet_id, str):
        raise ValueError("Spreadsheet ID must be a string")
    
    if len(spreadsheet_id.strip()) == 0:
        raise ValueError("Spreadsheet ID cannot be only whitespace")


def build_cell_notation(row: int, column: int) -> str:
    """
    Build A1 notation for a cell from row and column numbers.
    
    Args:
        row: Row number (1-based)
        column: Column number (1-based)
        
    Returns:
        A1 notation (e.g., 'B5', 'AA10')
        
    Examples:
        >>> build_cell_notation(5, 2)
        'B5'
        >>> build_cell_notation(10, 27)
        'AA10'
    """
    column_letter = column_number_to_letter(column)
    return f"{column_letter}{row}"


def normalize_name(name: str) -> str:
    """
    Normalize a name for comparison (trim whitespace, lowercase).
    
    Args:
        name: Name to normalize
        
    Returns:
        Normalized name
        
    Examples:
        >>> normalize_name('  Team Alpha  ')
        'team alpha'
        >>> normalize_name('Exercise 1')
        'exercise 1'
    """
    return name.strip().lower()
