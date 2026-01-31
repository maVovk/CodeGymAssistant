"""
Caching module for Google Sheets data with TTL support.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class CachedStructure:
    """Structure holding cached spreadsheet data."""
    team_names: List[str]
    exercise_names: List[str]
    last_updated: datetime = field(default_factory=datetime.now)
    
    def is_expired(self, ttl_seconds: int) -> bool:
        """Check if cache has expired based on TTL."""
        expiration_time = self.last_updated + timedelta(seconds=ttl_seconds)
        return datetime.now() > expiration_time


class SpreadsheetCache:
    """
    In-memory cache for spreadsheet structures with TTL support.
    
    Caches team names (column A) and exercise names (row 1) for each spreadsheet
    to minimize API calls to Google Sheets.
    """
    
    def __init__(self, ttl_seconds: int = 3600):
        """
        Initialize the cache.
        
        Args:
            ttl_seconds: Time to live for cached data in seconds (default: 1 hour)
        """
        self.ttl_seconds = ttl_seconds
        self._cache: Dict[str, CachedStructure] = {}
        self._lock = asyncio.Lock()
    
    async def get(self, spreadsheet_id: str) -> Optional[CachedStructure]:
        """
        Get cached structure for a spreadsheet.
        
        Args:
            spreadsheet_id: The ID of the spreadsheet
            
        Returns:
            CachedStructure if found and not expired, None otherwise
        """
        async with self._lock:
            if spreadsheet_id not in self._cache:
                return None
            
            cached = self._cache[spreadsheet_id]
            if cached.is_expired(self.ttl_seconds):
                # Cache expired, remove it
                del self._cache[spreadsheet_id]
                return None
            
            return cached
    
    async def set(
        self, 
        spreadsheet_id: str, 
        team_names: List[str], 
        exercise_names: List[str]
    ) -> None:
        """
        Store structure in cache.
        
        Args:
            spreadsheet_id: The ID of the spreadsheet
            team_names: List of team names from column A
            exercise_names: List of exercise names from row 1
        """
        async with self._lock:
            self._cache[spreadsheet_id] = CachedStructure(
                team_names=team_names,
                exercise_names=exercise_names,
                last_updated=datetime.now()
            )
    
    async def invalidate(self, spreadsheet_id: str) -> None:
        """
        Remove a specific spreadsheet from cache.
        
        Args:
            spreadsheet_id: The ID of the spreadsheet to invalidate
        """
        async with self._lock:
            if spreadsheet_id in self._cache:
                del self._cache[spreadsheet_id]
    
    async def clear(self) -> None:
        """Clear all cached data."""
        async with self._lock:
            self._cache.clear()
    
    async def get_or_fetch(
        self,
        spreadsheet_id: str,
        fetch_callback
    ) -> Tuple[List[str], List[str]]:
        """
        Get from cache or fetch using callback if not cached/expired.
        
        Args:
            spreadsheet_id: The ID of the spreadsheet
            fetch_callback: Async function to fetch data if not in cache
            
        Returns:
            Tuple of (team_names, exercise_names)
        """
        # Try to get from cache first
        cached = await self.get(spreadsheet_id)
        if cached is not None:
            return cached.team_names, cached.exercise_names
        
        # Cache miss or expired, fetch fresh data
        team_names, exercise_names = await fetch_callback()
        
        # Store in cache
        await self.set(spreadsheet_id, team_names, exercise_names)
        
        return team_names, exercise_names
    
    def get_cache_stats(self) -> Dict[str, any]:
        """
        Get statistics about the cache.
        
        Returns:
            Dictionary with cache statistics
        """
        return {
            "total_cached": len(self._cache),
            "spreadsheet_ids": list(self._cache.keys()),
            "ttl_seconds": self.ttl_seconds
        }
