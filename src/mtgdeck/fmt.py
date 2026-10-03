"""German number formatting for texts the user reads (warnings, checks, notes): 1.234,50 € · Ø 2,85."""

from __future__ import annotations

_SIGNS = {"eur": "€", "usd": "$"}


def num(value: float, digits: int = 2) -> str:
    """1234.5 -> '1.234,50' (digits=2)."""
    s = f"{float(value):,.{digits}f}"
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def money(value: float, currency: str = "eur", digits: int = 2) -> str:
    """32 -> '32,00 €'; whole budgets can use digits=0 -> '150 €'."""
    return f"{num(value, digits)} {_SIGNS.get(currency.lower(), currency.upper())}"
