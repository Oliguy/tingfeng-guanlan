"""Pure calendar values; no database, filesystem, network or upstream code imports."""
from bisect import bisect_left, bisect_right
from datetime import date, timedelta
from types import MappingProxyType


def monday(value):
    day = date.fromisoformat(value)
    return (day - timedelta(days=day.weekday())).isoformat()


class Calendar:
    def __init__(self, days, *, revision='fixture', sources=(), official_years=None):
        normalized = {}
        for key, value in days.items():
            if date.fromisoformat(key).isoformat() != key or (value is not None and type(value) is not int):
                raise ValueError('交易日历日期或状态无效')
            if value not in (0, 1, None):
                raise ValueError('交易日历状态无效')
            normalized[key] = value
        self.days = MappingProxyType(normalized)
        self.dates = tuple(sorted(normalized))
        self.open_dates = tuple(d for d in self.dates if normalized[d] == 1)
        self.valid_dates = tuple(d for d in self.dates if normalized[d] in (0,1))
        self._weeks_cache = {}
        self.revision = revision
        self.sources = tuple(sources)
        self.official_years = official_years or {}
        self.coverage_start = self.dates[0] if self.dates else None
        self.coverage_end = self.dates[-1] if self.dates else None

    def state(self, day, market='CN_A_REFERENCE'):
        date.fromisoformat(day)
        if market not in ('CN_A_REFERENCE', 'SSE'):
            if market not in self.official_years.get(day[:4], ()):
                return None
        return self.days.get(day)

    def covered(self, start, end=None):
        end = end or start
        a, b = date.fromisoformat(start), date.fromisoformat(end)
        if a > b:
            raise ValueError('日历起止日期无效')
        return bisect_right(self.valid_dates,end)-bisect_left(self.valid_dates,start)==(b-a).days+1

    def sessions(self, start, end):
        return self.open_dates[bisect_left(self.open_dates, start):bisect_right(self.open_dates, end)]

    def previous(self, day, inclusive=False):
        index = (bisect_right if inclusive else bisect_left)(self.open_dates, day) - 1
        # A known open date cannot be called the nearest if unknown days intervene.
        if index < 0:
            return None
        target = self.open_dates[index]
        return target if self.covered(target, day) else None

    def next(self, day, inclusive=False):
        index = (bisect_left if inclusive else bisect_right)(self.open_dates, day)
        if index >= len(self.open_dates):
            return None
        target = self.open_dates[index]
        return target if self.covered(day, target) else None

    def adjacent_weeks(self, older, newer):
        if older >= newer:
            return False
        start = (date.fromisoformat(older) + timedelta(days=7)).isoformat()
        end = (date.fromisoformat(newer) - timedelta(days=1)).isoformat()
        if start > end:
            return True
        return self.covered(start, end) and not self.sessions(start, end)

    def weeks(self, start, end):
        """Trading or unknown weeks in a bounded range; wholly closed weeks disappear."""
        key=(start,end)
        if key in self._weeks_cache:return self._weeks_cache[key]
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        cursor = date.fromisoformat(monday(start))
        result = []
        while cursor <= last:
            a, b = max(cursor, first), min(cursor + timedelta(days=6), last)
            begin, finish = a.isoformat(), b.isoformat()
            sessions = self.sessions(begin, finish)
            complete = self.covered(begin, finish)
            if sessions or not complete:
                result.append(MappingProxyType({'week_start': cursor.isoformat(), 'sessions': sessions,
                               'known': complete, 'last_session': sessions[-1] if sessions else finish}))
            cursor += timedelta(days=7)
        if len(self._weeks_cache)>=256:self._weeks_cache.pop(next(iter(self._weeks_cache)),None)
        result=tuple(result);self._weeks_cache[key]=result
        return result
