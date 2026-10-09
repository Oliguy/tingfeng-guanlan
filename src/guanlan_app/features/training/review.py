from guanlan_data.training_queries import query
"""Immutable decision receipts, post-hoc notes and explicitly seen replays."""
import copy,json,uuid
from guanlan_domain.training.account import numbers; from guanlan_domain.training.account import initial; from guanlan_domain.training.account import D
from guanlan_app.features.training.projection import project

def public_receipt(event):
    result={k:v for k,v in event.items() if not k.startswith('_')}
    after=event.get('_account_after');before=event.get('_account_before')
    if after is None and before:
        after=dict(before);units=D(event['units']);fee=D(event['fee']);price=D(event['price'])
        if event['action']=='BUY':after.update(cash='0',units=str(units),buy_day=event['day'],cost=str(D(before['cash'])/units) if units else '0')
        elif event['action']=='SELL':after.update(cash=str(D(before['cash'])+units*price-fee),units='0',buy_day=None,cost='0')
        after['fees']=str(D(before['fees'])+fee)
    if after:
        result['account_after']={k:v for k,v in numbers(after,event['price'],event['day']).items() if k in ('cash','units','unlocked','frozen','cost','fees')}
        result['account_after']['nav']=event['nav']
    return result

def summary(session,events):
    a=numbers(session['account'],session.get('mark',100),session['day'])
    curve=[a['initial_capital']]+[r['nav'] for r in events]+[a['nav']]
    peak=a['initial_capital'];drawdown=0
    for nav in curve:peak=max(peak,nav);drawdown=max(drawdown,(peak-nav)/peak*100 if peak else 0)
    drawdown=max(drawdown,session.get('_record_stats',{}).get('drawdown',0))
    benchmark=numbers(session['benchmark'],session.get('mark',100),session['day'])
    return {'return_percent':a['return'],'cumulative_return':a['cumulative_return'],'drawdown_percent':drawdown,'position_days':len(session['position_days']),
            'trades':sum(r['action']!='HOLD' for r in events),'benchmark_return':benchmark['return'],'flat_return':0,
            'eligible':session['status']=='completed' and not session['restricted'] and not session['seen'] and session['kind']=='blind',
            'mark_only':True,'source_invalid':bool(session.get('review_invalid'))}

def at(service,c,params):
    s=service.store.get(c,params['id'])
    if s['status'] not in ('completed','revealed','boundary') and not s.get('has_revealed'):raise ValueError('请先结束并揭晓，再回看操作')
    service.guard(s)
    events=service.store.actions(c,s['id']);event=next((r for r in events if r['revision']==params.get('revision')),None)
    if not event:raise ValueError('原操作点不存在')
    used=next((u for u in s['adopted'] if u['day']==event['day'] and u['phase']==event['phase']),None)
    old=dict(s,day=event['day'],phase=event['phase'],date=used['date'])
    p=project(service.data,old,period=params.get('period','daily'))
    if any(p[k]!=used[k] for k in ('point_hash','history_hash','background_hash')):raise ValueError('原操作点来源变化，暂停回看')
    position=None
    if '_account_before' in event:
        a=event['_account_before']
        position={'units':float(D(a['units'])),'cost':float(D(a['cost']))}
        if event['action']=='BUY':
            position={'units':event['units'],'cost':float(D(event['price'])+D(event['fee'])/D(event['units']))}
        elif event['action']=='SELL':position={'units':0,'cost':0}
    note=query(c, 'notes_14', (s['id'],event['revision'])).fetchone()
    return {k:p[k] for k in ('bars','quotes','opening','price','period')}|{'day':event['day'],'phase':event['phase'],
       'receipt':public_receipt(event),'position':position,'note':note[0] if note else '', 'mistake':bool(note[1]) if note else False}

def replay(service,c,params):
    original=service.store.get(c,params['id']);at(service,c,params)
    event=next(e for e in service.store.actions(c,original['id']) if e['revision']==params['revision'])
    used=next(u for u in original['adopted'] if u['day']==event['day'] and u['phase']==event['phase'])
    if '_account_before' not in event:raise ValueError('该旧记录没有完整决策前账户，不能重练')
    s=copy.deepcopy(original);s.update(id=uuid.uuid4().hex,date=used['date'],phase=event['phase'],day=event['day'],revision=0,
      status='active',kind='replay',seen=True,restricted=False,adopted=[],account=event['_account_before'],
      benchmark=event['_benchmark_before'],position_days=event['_position_days_before'],replay_parent=original['id'],replay_revision=event['revision'])
    s.pop('review_invalid',None);s.pop('has_revealed',None);s.pop('before_pause',None);s['signature']=service.signature();return s

def note(service,c,params):
    s=service.store.get(c,params['id'])
    if s['status'] not in ('completed','revealed','boundary') and not s.get('has_revealed'):raise ValueError('备注仅能在揭晓后补写')
    if not query(c, 'actions_15', (s['id'],params.get('revision'))).fetchone():raise ValueError('原操作点不存在')
    text=params.get('note','')
    if not isinstance(text,str) or len(text)>4000:raise ValueError('备注最多4000字')
    query(c, 'notes_13', (s['id'],params['revision'],text,int(bool(params.get('mistake')))))
    return {'saved':True,'post_hoc':True}
