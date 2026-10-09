from guanlan_data.training_queries import query
"""Professional-session metadata and counters; never market snapshots."""
from datetime import datetime,timezone
from guanlan_domain.training.account import numbers; from guanlan_domain.training.account import D

def timestamp():return datetime.now(timezone.utc).isoformat(timespec='milliseconds')
def record(store,c,s):
    stats=s.get('_record_stats')
    if stats is None:
        events=store.actions(c,s['id']);peak=float(D(s['account'].get('initial_capital',100000)));drawdown=0
        for e in events:
            peak=max(peak,e['nav']);drawdown=max(drawdown,(peak-e['nav'])/peak*100 if peak else 0)
        stats={'peak':peak,'drawdown':drawdown,'buys':sum(e['action']=='BUY' for e in events),'sells':sum(e['action']=='SELL' for e in events),'holds':sum(e['action']=='HOLD' for e in events)}
        s['_record_stats']=stats
    a=numbers(s['account'],s.get('mark',100),s['day']);stats['peak']=max(stats['peak'],a['nav'])
    stats['drawdown']=max(stats['drawdown'],(stats['peak']-a['nav'])/stats['peak']*100 if stats['peak'] else 0)
    row=query(c, 'sessions_12', (s['id'],)).fetchone() if not s.get('started_at') else None
    started=s.get('started_at') or (row[0].replace(' ','T')+'Z' if row else None)
    end=s.get('ended_at');last=s.get('last_activity_at') or s.get('started_at');duration=None
    if started and (end or last):
        try:duration=max(0,round((datetime.fromisoformat(end or last)-datetime.fromisoformat(started)).total_seconds()))
        except (ValueError,TypeError):pass
    unrealized=float(D(a['units'])*(D(s.get('mark',100))-D(a['cost'])))
    return {'started_at':started,'last_activity_at':last,'ended_at':end,'duration_seconds':duration,
        'end_reason':s.get('end_reason') or {'completed':'completed','boundary':'source_boundary','revealed':'revealed','abandoned':'interrupted'}.get(s['status']),
        'buys':stats['buys'],'sells':stats['sells'],'holds':stats['holds'],'drawdown_percent':stats['drawdown'],
        'position_days':len(s['position_days']),'initial_capital':a['initial_capital'],'profit':a['profit'],
        'unrealized_profit':unrealized,'realized_profit':a['profit']-unrealized,
        'origin_capital':a['origin_capital'],'cumulative_profit':a['cumulative_profit'],'cumulative_return':a['cumulative_return'],
        'cumulative_fees':a['cumulative_fees'],'carry_method':s.get('carry_method'),
        'previous_session':s.get('previous_session')}
