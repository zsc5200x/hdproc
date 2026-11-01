"""Compatibility entry point for fixture matching and manual mapping storage."""
from src.services.fixture_matching import (
    AUTO_MAP_TIME_MINUTES,
    CROWN_TIMEZONE,
    MERGE_DISPLAY_TIMEZONE,
    PAIR_TIME_TOLERANCE_MINUTES,
    PINNACLE_TIMEZONE,
    MappingConflict,
    build_alias_index,
    competition_conflict,
    competition_profile,
    crown_kickoff_datetime,
    fixture_pair_problem,
    normalize_league,
    normalize_name,
    pinnacle_kickoff_datetime,
    prepare_crown_event,
    prepare_pinnacle_event,
    rank_candidates,
    rank_league_candidates,
    summarize_leagues,
)
from src.storage.fixture_mappings import FixtureMergeStore

__all__ = [
    "AUTO_MAP_TIME_MINUTES", "CROWN_TIMEZONE", "MERGE_DISPLAY_TIMEZONE",
    "PAIR_TIME_TOLERANCE_MINUTES", "PINNACLE_TIMEZONE",
    "MappingConflict", "build_alias_index", "competition_conflict",
    "competition_profile", "crown_kickoff_datetime", "fixture_pair_problem",
    "normalize_league", "normalize_name", "pinnacle_kickoff_datetime",
    "prepare_crown_event",
    "prepare_pinnacle_event", "rank_candidates", "rank_league_candidates",
    "summarize_leagues", "FixtureMergeStore",
]
