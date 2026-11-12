# Meeting planner library

Build a small library that finds free time in a day as three plain Python modules in the root of the code directory: `clock.py`, `spans.py` and `planner.py`. Python 3, standard library only, no third-party packages, no network. `spans.py` uses `clock.py`, and `planner.py` uses both. A time of day is a whole number of minutes since midnight (`int`), from 0 to 1440; a span is a pair `(start, end)` of such numbers with `start < end`.
Every function below must behave exactly as described. Where an exception is named, raise exactly that class.

## Requirements

1. `clock.py` provides `parse_time(text)`, which turns a 24-hour time written as `HH:MM` into minutes since midnight. Both parts are exactly two digits, hours from 00 to 23 and minutes from 00 to 59: `parse_time("09:30")` is `570`, `parse_time("00:00")` is `0`, `parse_time("23:59")` is `1439`. The one exception is the end of the day, `parse_time("24:00")` is `1440`. Anything else raises `ValueError`: `parse_time("9:30")` raises `ValueError`, and so do `"09:60"`, `"24:01"`, `"0930"`, `" 09:30"` and `""`.

2. `clock.py` also provides `format_time(minutes)`, the reverse: `format_time(570)` is `"09:30"`, `format_time(0)` is `"00:00"`, `format_time(1440)` is `"24:00"`. A number below 0 or above 1440 raises `ValueError`; a value that is not an `int`, including `bool`, raises `TypeError`.

3. `clock.py` also provides `parse_duration(text)`, which turns a length of time into minutes. It accepts hours, minutes or both, as in `parse_duration("2h")` is `120`, `parse_duration("45m")` is `45` and `parse_duration("1h30m")` is `90`; a bare number is minutes, so `parse_duration("15")` is `15`. Surrounding whitespace is ignored. When hours are given, the minutes part must be below 60. A duration of zero, an empty text and anything else that does not fit these forms (such as `"h"`, `"1h60m"`, `"30m1h"` or `"1.5h"`) raises `ValueError`.

4. `spans.py` provides `parse_span(text)`, which turns `"START-END"`, two times in the form of `parse_time` separated by a hyphen that may have spaces around it, into a tuple: `parse_span("09:00-10:30")` is `(540, 630)` and `parse_span("09:00 - 10:30")` is `(540, 630)`. The end must be later than the start; otherwise, and for a bad time or a missing hyphen, it raises `ValueError`.

5. `spans.py` also provides `merge(spans)`, which takes a list of `(start, end)` pairs in any order and returns a new list of tuples sorted by start in which spans that overlap or touch are joined into one: `merge([(600, 660), (540, 600), (700, 720)])` is `[(540, 660), (700, 720)]`. The input list is not changed, and `merge([])` is `[]`.

6. `planner.py` provides `busy(schedule_text)`, which reads a schedule with one appointment per line: a span in the form of `parse_span` at the start of the line, optionally followed by whitespace and a free-text label. Blank lines and lines whose first non-whitespace character is `#` are ignored. It returns the merged busy spans as `merge` does: `busy("09:00-10:00 standup\n09:30-11:00 review\n13:00-14:00")` is `[(540, 660), (780, 840)]`. A line that does not start with a valid span raises `ValueError`.

7. `planner.py` also provides `free_slots(schedule_text, day_start, day_end, duration)`, which returns the gaps of the working day in which nothing is scheduled and which are at least `duration` long. `day_start` and `day_end` are times in the form of `parse_time` and `duration` is a text for `parse_duration`. Appointments outside the working day are ignored and ones that cross its edge count only for the part inside. The result is a list of strings in the form `"HH:MM-HH:MM"`, in order: `free_slots("09:00-10:00 standup\n13:00-14:00 lunch", "08:00", "17:00", "1h")` is `["08:00-09:00", "10:00-13:00", "14:00-17:00"]`. If `day_end` is not later than `day_start` it raises `ValueError`.

8. `planner.py` also provides `first_slot(schedule_text, day_start, day_end, duration)`, which returns the earliest time at which a meeting of exactly `duration` fits, as a string in the form `"HH:MM-HH:MM"`, or `None` when no gap of the working day is long enough: `first_slot("08:00-09:30 call", "08:00", "17:00", "45m")` is `"09:30-10:15"`.
