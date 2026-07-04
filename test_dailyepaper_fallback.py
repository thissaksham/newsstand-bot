"""Self-check for dailyepaper.in fallback-page matching (no network).

Run: python test_dailyepaper_fallback.py
"""

from scrapers.dailyepaper_in import match_title_page

LINKS = [
    "https://dailyepaper.in/hindustan-epaper-2025/",
    "https://dailyepaper.in/hindustan-times-epaper-download-2026/",
    "https://dailyepaper.in/economic-times-newspaper-2025/",
    "https://dailyepaper.in/economic-times-newspaper-today-2026/",
    "https://dailyepaper.in/pioneer-epaper-pdf-2025/",
    "https://dailyepaper.in/business-standard-hindi-epaper-feb-2026/",
    "https://dailyepaper.in/english-newspapers/",
    "https://dailyepaper.in/about/",
]

# boilerplate guard: "Hindustan" must not grab hindustan-times-*
assert match_title_page(LINKS, "Hindustan", 2026) == LINKS[0]
assert match_title_page(LINKS, "Hindustan Times", 2026) == LINKS[1]
# current-year page preferred over the stale previous-year one
assert match_title_page(LINKS, "Economic Times", 2026) == LINKS[3]
# leading "The" optional on both sides
assert match_title_page(LINKS, "The Pioneer", 2026) == LINKS[4]
# parenthesised variant maps to the variant page
assert match_title_page(LINKS, "Business Standard (Hindi)", 2026) == LINKS[5]
# nav/category pages never match
assert match_title_page(LINKS, "English", 2026) is None
assert match_title_page(LINKS, "About", 2026) is None
# unknown paper
assert match_title_page(LINKS, "No Such Paper", 2026) is None

print("test_dailyepaper_fallback: OK")
