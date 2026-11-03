"""
School days: every day in the period minus weekends, school breaks and holidays. The dashboard
uses this as the base for "% of school days lost". Public-school calendar; private schools and
local holidays (city fiestas, charter days) can differ, so the count is an estimate.

Sources
- SY 2024-25: DepEd Order 9, s. 2024 (Jul 29, 2024 - Apr 15, 2025); classes resumed Jan 2, 2025
  after the Christmas break (ABS-CBN, Jul 15, 2024).
- SY 2025-26: DepEd Order 12, s. 2025 (Jun 16, 2025 - Mar 31, 2026). The midyear break
  (Oct 27-31, 2025) and Christmas break (Dec 20, 2025 - Jan 4, 2026) come from DepEd's monthly
  calendar as reported by DepEd Club; Manila Bulletin confirms the Dec 20 start.
- SY 2026-27: opened Jun 8, 2026 (DepEd); its term breaks aren't included yet.
- Holidays: Proclamation 727, s. 2024 (2025) with Proclamations 729 and 878; Proclamation 1006,
  s. 2025 (2026). Eid'l Fitr and Eid'l Adha are set by separate proclamations each year.
"""

BREAKS = [
    ("2024-12-21", "2025-01-01", "Christmas break (SY 2024-25)"),
    ("2025-04-16", "2025-06-15", "End-of-school-year break (SY 2024-25 ended Apr 15)"),
    ("2025-10-27", "2025-10-31", "Midyear break (SY 2025-26)"),
    ("2025-12-20", "2026-01-04", "Christmas break (SY 2025-26)"),
    ("2026-04-01", "2026-06-07", "End-of-school-year break (SY 2025-26 ended Mar 31; SY 2026-27 opened Jun 8)"),
]

HOLIDAYS = [
    # 2025 - Proclamation 727 (+ 729, 878) and Eid proclamations
    ("2025-01-01", "New Year's Day"), ("2025-01-29", "Chinese New Year"), ("2025-04-01", "Eid'l Fitr"),
    ("2025-04-09", "Araw ng Kagitingan"), ("2025-04-17", "Maundy Thursday"), ("2025-04-18", "Good Friday"),
    ("2025-04-19", "Black Saturday"), ("2025-05-01", "Labor Day"), ("2025-05-12", "Election day"),
    ("2025-06-06", "Eid'l Adha"), ("2025-06-12", "Independence Day"), ("2025-07-27", "Iglesia ni Cristo anniversary"),
    ("2025-08-21", "Ninoy Aquino Day"), ("2025-08-25", "National Heroes Day"), ("2025-10-31", "All Saints' Day Eve"),
    ("2025-11-01", "All Saints' Day"), ("2025-11-30", "Bonifacio Day"), ("2025-12-08", "Immaculate Conception"),
    ("2025-12-24", "Christmas Eve"), ("2025-12-25", "Christmas Day"), ("2025-12-30", "Rizal Day"),
    ("2025-12-31", "Last day of the year"),
    # 2026 - Proclamation 1006 and Eid proclamations
    ("2026-01-01", "New Year's Day"), ("2026-02-17", "Lunar New Year"), ("2026-03-20", "Eid'l Fitr"),
    ("2026-04-02", "Maundy Thursday"), ("2026-04-03", "Good Friday"), ("2026-04-04", "Black Saturday"),
    ("2026-04-09", "Araw ng Kagitingan"), ("2026-05-01", "Labor Day"), ("2026-06-12", "Independence Day"),
    ("2026-08-21", "Ninoy Aquino Day"), ("2026-08-31", "National Heroes Day"), ("2026-11-01", "All Saints' Day"),
    ("2026-11-02", "All Souls' Day"), ("2026-11-30", "Bonifacio Day"), ("2026-12-08", "Immaculate Conception"),
    ("2026-12-24", "Christmas Eve"), ("2026-12-25", "Christmas Day"), ("2026-12-30", "Rizal Day"),
    ("2026-12-31", "Last day of the year"),
]


def as_json() -> dict:
    return {
        "breaks": [list(b) for b in BREAKS],
        "holidays": [list(h) for h in HOLIDAYS],
        "note": "Public-school calendar: weekends, DepEd school breaks and national holidays are not school days. "
                "Local holidays and private-school calendars can differ, so this is an estimate.",
    }
