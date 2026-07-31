"""Self-check for downmagaz post-title date parsing (no network).

Run: python test_downmagaz_dates.py
"""

from datetime import date, timedelta

from scrapers.downmagaz_net import parse_date_from_title

# 2-digit years — the format that broke Washington Post delivery on 2026-07-13.
assert parse_date_from_title("The Washington Post 7.13.26") == date(2026, 7, 13)
assert parse_date_from_title("The Washington Post - 07.13.2026") == date(2026, 7, 13)

# US titles are MM.DD when ambiguous; everything else is DD.MM. Years are well
# in the past so both readings are historical and the calendar guard stays out.
assert parse_date_from_title("The Washington Post - 07.09.2020") == date(2020, 7, 9)
assert parse_date_from_title("The New York Times - 07.12.2020") == date(2020, 7, 12)
assert parse_date_from_title("The Wall Street Journal - 07.11.2020") == date(2020, 7, 11)
assert parse_date_from_title("The Guardian - 07.09.2020") == date(2020, 9, 7)

# Unambiguous days win regardless of publication.
assert parse_date_from_title("Some Paper - 13.07.2026") == date(2026, 7, 13)

# A US paper missing from _US_DATE_PUBS reads months into the future under
# DD.MM; the calendar guard flips it to the sane reading. A future date is the
# one misparse that never self-heals — it outranks every real edition in
# catch-up forever. (Needs a month with room either side to express.)
_t = date.today()
if 2 <= _t.month <= 10:
    _d1, _d2 = _t.month - 1, _t.month + 2
    assert parse_date_from_title(
        f"The Boston Globe - {_d1:02d}.{_d2:02d}.{_t.year}"
    ) == date(_t.year, _d1, _d2)

# ...but a genuine near-future cover date (weeklies post a few days ahead of
# their cover date) is left alone, not flipped into a months-old date.
_soon = _t + timedelta(days=3)
if _soon.day <= 12:
    assert parse_date_from_title(
        f"The Economist - {_soon.day:02d}.{_soon.month:02d}.{_soon.year}"
    ) == _soon

# Monthlies collapse to the 1st.
assert parse_date_from_title("The Economist - June 2026") == date(2026, 6, 1)
assert parse_date_from_title("Wired USA - 07.2026") == date(2026, 7, 1)

# No parseable date => None, so the caller skips the post rather than
# stamping it with today's date (which would re-deliver it forever).
assert parse_date_from_title("The Washington Post Weekend Special") is None
assert parse_date_from_title("Some Magazine") is None

print("test_downmagaz_dates: OK")
