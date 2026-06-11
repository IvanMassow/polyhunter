"""Instrument universe.

Theatre A (energy): fixed list. Theatre B (crypto): BTC + ETH always, plus the
top-10 liquid alts by 90-day median dollar volume, rebalanced monthly.
"""

from __future__ import annotations

import statistics
from datetime import datetime

ENERGY_INSTRUMENTS = ["BRENT", "WTI", "TTF", "GASOIL", "FRO", "STNG"]
CRYPTO_CORE = ["BTC", "ETH"]
ALT_BASKET_SIZE = 10

# Stable/wrapped assets are liquidity, not exposure — never in the alt basket.
ALT_EXCLUDE = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "WBTC", "WETH", "STETH"}

THEATRE_BY_INSTRUMENT: dict[str, str] = {
    **{i: "energy" for i in ENERGY_INSTRUMENTS},
    **{i: "crypto" for i in CRYPTO_CORE},
    "ALT_BASKET": "crypto",
}


def energy_instruments() -> list[str]:
    return list(ENERGY_INSTRUMENTS)


def select_alt_basket(
    daily_dollar_volume: dict[str, list[float]],
    min_days: int = 60,
) -> list[str]:
    """Pick the top-N alts by 90-day median dollar volume.

    daily_dollar_volume: base asset -> daily dollar volumes (most recent ~90
    entries). Assets with fewer than min_days observations are skipped — a
    coin that just listed has no business in the basket.
    """
    scored: list[tuple[float, str]] = []
    for asset, vols in daily_dollar_volume.items():
        if asset in CRYPTO_CORE or asset in ALT_EXCLUDE:
            continue
        if len(vols) < min_days:
            continue
        scored.append((statistics.median(vols[-90:]), asset))
    scored.sort(reverse=True)
    return [asset for _, asset in scored[:ALT_BASKET_SIZE]]


def crypto_universe(alt_basket: list[str]) -> list[str]:
    return CRYPTO_CORE + list(alt_basket)


def theatre_of(instrument: str, alt_basket: list[str] | None = None) -> str:
    if instrument in THEATRE_BY_INSTRUMENT:
        return THEATRE_BY_INSTRUMENT[instrument]
    if alt_basket and instrument in alt_basket:
        return "crypto"
    raise KeyError(f"unknown instrument: {instrument}")


def rebalance_due(last_rebalance: datetime | None, now: datetime) -> bool:
    if last_rebalance is None:
        return True
    return (now.year, now.month) != (last_rebalance.year, last_rebalance.month)
