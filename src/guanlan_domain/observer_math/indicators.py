"""Pure display indicators, preserving the v16 browser chart calculation."""
from datetime import date, timedelta
import math


def _finite(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def moving_average(values,period):
    total,missing=0.0,0
    output=[]
    for index,value in enumerate(values):
        if _finite(value): total+=value
        else: missing+=1
        if index>=period:
            old=values[index-period]
            if _finite(old): total-=old
            else: missing-=1
        output.append(None if missing else total/min(period,index+1))
    return output


def exponential_average(values,period):
    alpha=2/(period+1);previous=None;output=[]
    for value in values:
        if not _finite(value): output.append(None);continue
        previous=value if previous is None else alpha*value+(1-alpha)*previous
        output.append(previous)
    return output


def week_key(value):
    day=date.fromisoformat(value)
    return (day-timedelta(days=day.weekday())).isoformat()


def align_weeks(weeks,calendar,*,start=None,end=None):
    if calendar is None or not weeks:return [dict(r) for r in weeks]
    start=start or weeks[0]['trade_date'];end=end or weeks[-1]['trade_date']
    observed={week_key(r['trade_date']):r for r in weeks}
    aligned=[];last_gap=-1000
    for i,slot in enumerate(calendar.weeks(start,end)):
        row=dict(observed.get(slot['week_start']) or {})
        if not row or not slot['known'] or row.get('calendar_gap'):
            row.update(trade_date=slot['last_session'],week_start=slot['week_start'],
                       open=None,high=None,low=None,close=None,volume=None,amount=None,
                       thirty_week_ma=None,calendar_gap=True,status='missing')
            last_gap=i
        elif i-last_gap<30:
            row['thirty_week_ma']=None
        aligned.append(row)
    return aligned


def aggregate_weekly(rows,calendar=None):
    weeks=[]
    for row in rows:
        key=week_key(row['trade_date'])
        if not weeks or weeks[-1]['week_start']!=key:
            weeks.append({**row,'week_start':key,'period_start':row['trade_date'],
                          'volume':row.get('volume') or 0,'amount':row.get('amount') or 0})
        else:
            last=weeks[-1]
            last.update(trade_date=row['trade_date'],close=row.get('close'),
                high=max(last.get('high') or 0,row.get('high') or 0),
                low=min(last.get('low') or 0,row.get('low') or 0),
                volume=last['volume']+(row.get('volume') or 0),amount=last['amount']+(row.get('amount') or 0),
                is_adjustment_boundary=bool(last.get('is_adjustment_boundary') or row.get('is_adjustment_boundary')))
    if calendar is not None and rows:
        present={r['trade_date'] for r in rows}
        slots={s['week_start']:s for s in calendar.weeks(rows[0]['trade_date'],rows[-1]['trade_date'])}
        for row in weeks:
            slot=slots.get(row['week_start'])
            # Mark missing sessions in a shortened/current week too. Never call them halts.
            if slot and any(d not in present for d in slot['sessions']):
                row['calendar_gap']=True
        return align_weeks(weeks,calendar,start=rows[0]['trade_date'],end=rows[-1]['trade_date'])
    return weeks


def chart_rows(daily,period,calendar=None):
    weekly,line=weekly_line(daily,calendar)
    rows=weekly if period=='weekly' else [dict(row) for row in daily]
    return apply_overlays(rows,line)


def weekly_line(daily,calendar=None):
    """Original 30-week calculation, independent from the other overlays."""
    weekly=aggregate_weekly(daily,calendar)
    prior={}
    for i,row in enumerate(weekly):
        values=[p.get('close') for p in weekly[max(0,i-29):i]]
        valid=len(values)==29 and all(_finite(v) for v in values)
        # Every day in this week has the same prior 29 closes. Preserve the
        # original summation order, doing that work once per week.
        prior[row['week_start']]=(sum(values) if valid else 0,valid)
    gaps={r['week_start'] for r in weekly if r.get('calendar_gap')}
    line={}
    for row in daily:
        key=week_key(row['trade_date'])
        previous,valid=prior.get(key,(0,False));last_close=row.get('close')
        line[row['trade_date']]=(previous+last_close)/30 if key not in gaps and valid and _finite(last_close) else None
    return weekly,line


class OverlayAccumulator:
    """Streaming form of the same moving/EMA calculations; stores 114 closes."""
    def __init__(self):
        from collections import deque
        self.closes=deque(maxlen=114);self.count=0;self.first=None;self.second=None
        self.totals={n:0.0 for n in (3,6,12,14,24,28,30,57,114)}
        self.missing={n:0 for n in self.totals}
    def append(self,value):
        averages={};index=self.count
        for n in self.totals:
            if _finite(value):self.totals[n]+=value
            else:self.missing[n]+=1
            if index>=n:
                old=self.closes[-n]
                if _finite(old):self.totals[n]-=old
                else:self.missing[n]-=1
            averages[n]=None if self.missing[n] else self.totals[n]/min(n,index+1)
        alpha=2/11
        if _finite(value):
            self.first=value if self.first is None else alpha*value+(1-alpha)*self.first
            self.second=self.first if self.second is None else alpha*self.first+(1-alpha)*self.second
            trend=self.second
        else:trend=None
        self.closes.append(value);self.count+=1
        def mean(periods):
            values=[averages[n] for n in periods]
            return sum(values)/len(values) if all(_finite(v) for v in values) else None
        return {'z_zhixing_short_trend':trend,'z_zhixing_bull_bear':mean((14,28,57,114)),
                'ma30':averages[30],'bbi':mean((3,6,12,24))}


def apply_overlays(rows,thirty_week_line):
    """Existing four overlays, with a separately supplied persisted MA30-week line.

    No trading-week aggregation or multi-year MA30 computation occurs here.
    """
    rows=[dict(row) for row in rows]
    closes=[row.get('close') for row in rows]
    averages={n:moving_average(closes,n) for n in (3,6,12,14,24,28,30,57,114)}
    trend=exponential_average(exponential_average(closes,10),10)
    for index,row in enumerate(rows):
        def average_periods(periods):
            values=[averages[n][index] for n in periods]
            return sum(values)/len(values) if all(_finite(v) for v in values) else None
        row.update(z_zhixing_short_trend=trend[index],z_zhixing_bull_bear=average_periods((14,28,57,114)),
                   ma30=averages[30][index],bbi=average_periods((3,6,12,24)),thirty_week_ma=thirty_week_line.get(row['trade_date']))
    return rows
