"""Telegram group signal ingestion."""

from .parser import SignalParseError, parse_telegram_bet_signal

__all__ = ["SignalParseError", "parse_telegram_bet_signal"]
