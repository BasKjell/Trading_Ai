"""Exchange calendars needed to know the regular session of a given day (no third-party dependency).

NYSE early closes (13:00 New York) from the NYSE holiday calendar. For 2016-2025 every date was checked
against the downloaded SIP data: on exactly these days the volume after 13:00 collapses to after-hours
levels, and on no other day. Full holidays need no entry: there are no regular-session trades.
Days after ``NYSE_COVERED_UNTIL`` are treated as unknown and are not part of the session (fail closed).
"""

from __future__ import annotations

from datetime import date, time

NYSE_EARLY_CLOSE = time(13, 0)
NYSE_COVERED_UNTIL = date(2026, 12, 31)
NYSE_EARLY_CLOSES = frozenset(
    date.fromisoformat(day)
    for day in (
        "2016-11-25",
        "2017-07-03",
        "2017-11-24",
        "2018-07-03",
        "2018-11-23",
        "2018-12-24",
        "2019-07-03",
        "2019-11-29",
        "2019-12-24",
        "2020-11-27",
        "2020-12-24",
        "2021-11-26",
        "2022-11-25",
        "2023-07-03",
        "2023-11-24",
        "2024-07-03",
        "2024-11-29",
        "2024-12-24",
        "2025-07-03",
        "2025-11-28",
        "2025-12-24",
        "2026-11-27",
        "2026-12-24",
    )
)
