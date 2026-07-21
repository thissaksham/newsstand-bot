"""Self-check for downmagaz post-title date parsing (no network).

Run: python test_downmagaz_dates.py
"""

from datetime import date

from scrapers.downmagaz_net import parse_date_from_title

# 2-digit years — the format that broke Washington Post delivery on 2026-07-13.
assert parse_date_from_title("The Washington Post 7.13.26") == date(2026, 7, 13)
assert parse_date_from_title("The Washington Post - 07.13.2026") == date(2026, 7, 13)

# US titles are MM.DD when ambiguous; everything else is DD.MM.
assert parse_date_from_title("The Washington Post - 07.09.2026") == date(2026, 7, 9)
assert parse_date_from_title("The Guardian - 07.09.2026") == date(2026, 9, 7)

# Unambiguous days win regardless of publication.
assert parse_date_from_title("Some Paper - 13.07.2026") == date(2026, 7, 13)

# Monthlies collapse to the 1st.
assert parse_date_from_title("The Economist - June 2026") == date(2026, 6, 1)
assert parse_date_from_title("Wired USA - 07.2026") == date(2026, 7, 1)

# No parseable date => None, so the caller skips the post rather than
# stamping it with today's date (which would re-deliver it forever).
assert parse_date_from_title("The Washington Post Weekend Special") is None
assert parse_date_from_title("Some Magazine") is None

print("test_downmagaz_dates: OK")
