"""
Unit normalization — maps the many ways staff write a unit onto one canonical
name per measure (kg, ltr, pcs), and converts the small variants (grams,
millilitres) into the base unit, so a balance is never a sum of mixed units.

Deterministic on purpose: arithmetic like 500 gms -> 0.5 kg shouldn't depend
on the LLM. Units outside the table (bags, packets, boxes...) pass through
lowercased and unconverted.
"""

# alias -> (canonical unit, factor to multiply the quantity by)
_ALIASES: dict[str, tuple[str, float]] = {}
for _unit, _factor, _names in [
    ("kg", 1, ["kg", "kgs", "kilo", "kilos", "kilogram", "kilograms"]),
    ("kg", 0.001, ["g", "gm", "gms", "gram", "grams"]),
    ("ltr", 1, ["l", "lt", "ltr", "ltrs", "litre", "litres", "liter", "liters"]),
    ("ltr", 0.001, ["ml", "mls", "millilitre", "millilitres", "milliliter", "milliliters"]),
    ("pcs", 1, ["pc", "pcs", "piece", "pieces"]),
]:
    for _name in _names:
        _ALIASES[_name] = (_unit, _factor)


def _clean(unit: str | None) -> str | None:
    cleaned = (unit or "").strip().lower().rstrip(".")
    return cleaned or None


def canonical_unit(unit: str | None) -> str | None:
    """The canonical name for a unit as written, e.g. 'Litres' -> 'ltr'. Unknown
    units come back cleaned (lowercased, trimmed) but otherwise unchanged."""
    cleaned = _clean(unit)
    return _ALIASES[cleaned][0] if cleaned in _ALIASES else cleaned


def normalize_quantity(quantity: float, unit: str | None) -> tuple[float, str | None, str | None]:
    """Returns (quantity, unit, converted_from). `converted_from` is a short
    "500 gms" label only when the number actually changed (a real conversion),
    not for a pure respelling like 'litres' -> 'ltr'."""
    cleaned = _clean(unit)
    if cleaned not in _ALIASES:
        return quantity, cleaned, None
    canonical, factor = _ALIASES[cleaned]
    if factor == 1:
        return quantity, canonical, None
    return round(quantity * factor, 6), canonical, f"{quantity:g} {cleaned}"
