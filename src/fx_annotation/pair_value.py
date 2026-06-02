from dataclasses import dataclass


HIGH_VALUE_PAIRS = {
    "GBP_USD",
    "GBP_JPY",
    "USD_CAD",
    "USD_JPY",
    "XAU_USD",
}

LOW_VALUE_PAIRS = {
    "AUD_USD",
    "BTC_USD",
    "EUR_USD",
    "NZD_USD",
}


@dataclass(frozen=True)
class PairValue:
    tier: str
    label: str
    note: str


def pair_value_for_instrument(instrument: str) -> PairValue:
    normalized = instrument.upper()
    if normalized in HIGH_VALUE_PAIRS:
        return PairValue(
            tier="high_value",
            label="HIGH-VALUE PAIR",
            note="Q1 validation showed stronger tested edge for this strategy model.",
        )
    if normalized in LOW_VALUE_PAIRS:
        return PairValue(
            tier="low_value",
            label="LOW-VALUE PAIR - CAUTION",
            note="Technically valid setups can appear, but Q1 tested edge was weak on this pair.",
        )
    return PairValue(
        tier="unvalidated",
        label="UNVALIDATED PAIR",
        note="This pair has not shown enough validated edge yet; treat setup quality conservatively.",
    )


def pair_value_record(pair_value: PairValue) -> dict[str, str]:
    return {
        "tier": pair_value.tier,
        "label": pair_value.label,
        "note": pair_value.note,
    }
