from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction


def TickSize(target_symbol: str) -> Decimal:
    """
    Return the tick size for the given symbol as a Decimal.
    Stub — replace with actual lookup logic.
    """
    raise NotImplementedError("Replace with actual tick size lookup")


def RoundImplied(raw_price: float | Decimal | str,
                 target_symbol: str,
                 implied_side: str) -> Decimal:
    try:
        tick = Decimal(str(TickSize(target_symbol)))
        raw = Decimal(str(raw_price))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(
            f"raw_price and TickSize({target_symbol!r}) must be valid decimals"
        ) from exc

    if not tick.is_finite() or tick <= 0:
        raise ValueError(f"TickSize({target_symbol!r}) = {tick}; must be > 0")

    if not raw.is_finite():
        raise ValueError(f"raw_price = {raw}; must be finite")

    # Decimal division is context-sensitive. Convert the finite decimal inputs
    # to exact rationals so that floor/ceiling decisions cannot lose a tiny
    # fractional component at a tick boundary.
    quotient = Fraction(raw) / Fraction(tick)

    if implied_side == "Bid":
        adjusted = quotient.numerator // quotient.denominator

    elif implied_side == "Offer":
        adjusted = -(quotient.numerator // -quotient.denominator)

    else:
        raise ValueError(f"Unknown implied_side: {implied_side!r}; expected 'Bid' or 'Offer'")

    # Decimal multiplication is also context-sensitive. Reserve enough
    # precision to represent the integer tick count and the tick exactly.
    precision = max(28, len(str(abs(adjusted))) + len(tick.as_tuple().digits) + 2)
    with localcontext() as context:
        context.prec = precision
        return Decimal(adjusted) * tick


# ---------------------------------------------------------------------------
# Tests — run with: python3 round_implied.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Override TickSize for testing
    _TICK_MAP = {
        "ZSDF_M2": Decimal("0.5"),
        "ZSDF_SPREAD": Decimal("0.25"),
        "ZSDF_INT": Decimal("1"),
    }

    def TickSize(sym: str) -> Decimal:
        return _TICK_MAP[sym]

    tests = [
        # (raw_price, symbol, side, expected)
        (2500.9,  "ZSDF_M2", "Bid",   Decimal("2500.5")),
        (8001.1,  "ZSDF_M2", "Bid",   Decimal("8001.0")),
        (8001.25, "ZSDF_M2", "Bid",   Decimal("8001.0")),
        (2501.9,  "ZSDF_M2", "Offer", Decimal("2502.0")),
        (2502.15, "ZSDF_M2", "Bid",   Decimal("2502.0")),
        (0.3,     "ZSDF_M2", "Bid",   Decimal("0.0")),
        (0.3,     "ZSDF_M2", "Offer", Decimal("0.5")),
        ("12.4",  "ZSDF_M2", "Bid",   Decimal("12.0")),
        ("12.4",  "ZSDF_M2", "Offer", Decimal("12.5")),
        ("-0.1",  "ZSDF_SPREAD", "Bid",   Decimal("-0.25")),
        ("-0.1",  "ZSDF_SPREAD", "Offer", Decimal("0.00")),
        (
            Decimal("1.0000000000000000000000000001"),
            "ZSDF_INT",
            "Offer",
            Decimal("2"),
        ),
    ]

    all_ok = True
    for raw, sym, side, expected in tests:
        result = RoundImplied(raw, sym, side)
        status = "✅" if result == expected else "❌"
        if result != expected:
            all_ok = False
        print(f"{status}  RoundImplied({str(raw):>8}, {sym:>11}, {side:>5}) = {result:>6}  expected {expected}")

    if not all_ok:
        raise SystemExit("\nSome tests FAILED")

    print("\nAll tests passed")
