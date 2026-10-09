"""Atomic phase commands. Anonymous views are whitelisted, never session dumps."""
import secrets,threading,uuid
from guanlan_data.errors import DatabaseError
from pathlib import Path
from guanlan_data.training_queries import query
from guanlan_data.repositories.training.data import Data
from guanlan_data.repositories.training.store import Store
from guanlan_app.features.training.projection import project; from guanlan_app.features.training.projection import next_date
from guanlan_domain.training.rules import VERSION; from guanlan_domain.training.rules import digest
from guanlan_domain.training.account import initial; from guanlan_domain.training.account import numbers; from guanlan_domain.training.account import reasons; from guanlan_domain.training.account import execute; from guanlan_domain.training.account import D
from guanlan_app.features.training.review import public_receipt; from guanlan_app.features.training.review import summary; from guanlan_app.features.training.review import at; from guanlan_app.features.training.review import replay; from guanlan_app.features.training.review import note
from guanlan_app.features.training.records import record; from guanlan_app.features.training.records import timestamp

TERMINAL={'completed','revealed','abandoned','boundary'}
FLOW_VERSION='manual_rounds_20261007_v2'

class Service:
    def __init__(self,data=None,store=None):
        self.data=data or Data();self.store=store or Store(self.data.paths['store']);self.lock=threading.RLock()
        self._source_epochs={}
    def close(self):
        with self.lock:
            if hasattr(self.data,'close'):self.data.close()
            if hasattr(self.store,'close'):self.store.close()
    def name(self,code):
        try:return self.data.identity(code)['name']
        except (DatabaseError,OSError,ValueError):return '名称暂不可读'
    def signature(self):
        if hasattr(self.data,'source_signature'):return self.data.source_signature()
        result={}
        for key,path in self.data.paths.items():
            if key=='store':continue
            for suffix in ('','-wal'):
                p=Path(str(path)+suffix)
                result[key+suffix]=[p.stat().st_size,p.stat().st_mtime_ns] if p.exists() else None
        return result
    def options(self,params):
        length=params.get('length',150)
        if length not in (60,150):raise ValueError('训练长度仅支持60或150交易日')
        market=params.get('market','all')
        if market not in ('all','主板','创业板','科创板'):raise ValueError('市场筛选无效')
        fee,slip=params.get('fee_bps',0),params.get('slippage_bps',0)
        if not isinstance(fee,(int,float)) or not 0<=fee<=100 or not isinstance(slip,(int,float)) or not 0<=slip<=100:raise ValueError('费用或滑点需在0至100基点内')
        from datetime import date
        start=params.get('from','2018-08-20');end=params.get('to')
        for value in (start,end):
            if value:date.fromisoformat(value)
        if end and start and start>end:raise ValueError('起止范围无效')
        return {'length':length,'market':market,'from':start,'to':end,'exclude_st':bool(params.get('exclude_st',False)),
                'fee_bps':fee,'slippage_bps':slip}
    def guard(self,s):
        if s['rule_version']!=VERSION:raise ValueError('训练规则版本已变化，旧局暂停')
        signature=self.signature()
        epoch=self.data.windows().sources.epoch if hasattr(self.data,'windows') else 0
        observed=self._source_epochs.get(s['id'],epoch)
        if signature!=s.get('signature') or epoch!=observed:
            for used in s['adopted']:
                old=dict(s,date=used['date'],phase=used['phase'],day=used['day'])
                p=self.data.prepare(old).fingerprints(old) if hasattr(self.data,'prepare') else project(self.data,old)
                if any(p[k]!=used[k] for k in ('point_hash','history_hash','background_hash','indicator_hash') if k in used):
                    raise ValueError('已使用的行情、因子或限制价来源变化，旧局暂停')
            s['signature']=signature
        self._source_epochs[s['id']]=epoch
        return signature
    def view(self,c,s,period='daily',*,include_point=False,compact=False,persist=True):
        # Terminal review also refuses silently rewritten historical inputs.
        try:
            before=self.guard(s)
            source=self.data.windows().prepare(s,checked=True) if hasattr(self.data,'windows') else self.data
            p=project(source,s,period=period)
            if before!=self.signature():raise ValueError('行情来源正在变化，请稍后重试')
            key=(s['date'],s['phase'])
            old=next((u for u in s['adopted'] if (u['date'],u['phase'])==key),None)
            if old and any(p[k]!=old[k] for k in ('point_hash','history_hash','background_hash','indicator_hash') if k in old):raise ValueError('已使用的行情来源变化，旧局暂停')
            if not old:s['adopted'].append({k:p[k] for k in ('point_hash','history_hash','background_hash','indicator_hash') if k in p}|{'date':s['date'],'phase':s['phase'],'day':s['day']})
            elif 'indicator_hash' in p:old.setdefault('indicator_hash',p['indicator_hash'])
            if s['status']=='paused':s['status']=s.pop('before_pause','active')
            if p['rule_kind']=='unknown':s['restricted']=True
            s['mark']=p['price'];s['signature']=before;s['pause_reason']=''
        except (ValueError,OSError,DatabaseError) as exc:
            if s['status'] in TERMINAL:
                s['review_invalid']=True;s['restricted']=True
            else:
                if s['status']!='paused':s['before_pause']=s['status']
                s['status']='paused'
            s['pause_reason']=str(exc) if isinstance(exc,ValueError) else '原始来源暂不可读，已暂停'
            p=None
        # Range recognition includes already revealed background context, and is
        # checked as the visible horizon grows, not from the unseen tail.
        for row in query(c, 'sessions_1', (s['id'],)):
            prior=__import__('json').loads(row[0])
            if prior['code']==s['code'] and (prior['status'] in ('completed','revealed','boundary') or prior.get('has_revealed')) and prior.get('visible_from',prior['start'])<=s['date'] and prior['date']>=s.get('visible_from',s['start']):s['seen']=True
        if s['status'] in ('completed','revealed','boundary','abandoned'):s['has_revealed']=True
        metadata=record(self.store,c,s)
        if persist or s['status']!='active':self.store.save(c,s)
        terminal=s['status'] in ('completed','revealed','boundary') or s.get('has_revealed')
        events=self.store.actions(c,s['id']) if not compact or terminal else []
        last_row=query(c, 'actions_6', (s['id'],)).fetchone() if compact else None
        latest=__import__('json').loads(last_row[0]) if last_row else events[-1] if events else None
        action_count=query(c, 'actions_9', (s['id'],)).fetchone()[0] if compact else len(events)
        stats=numbers(s['account'],s.get('mark',100),s['day'])
        public={'id':s['id'],'revision':s['revision'],'phase':s['phase'],'day':s['day'],'length':s['settings']['length'],
                'status':s['status'],'pause_reason':s.get('pause_reason',''),'kind':s['kind'],'seen':s['seen'],'restricted':s['restricted'],
                'account':stats,'settings':s['settings'],'record':metadata,'action_count':action_count,'actions':[public_receipt(e) for e in events],'last_receipt':public_receipt(latest) if latest else None}
        current_round=(s['day']-1)*2+(1 if s['phase']=='OPEN' else 2)
        public.update(round=current_round,total_rounds=s['settings']['length']*2,
                      rounds_completed=current_round if s['status']=='completed' else current_round-1,
                      flow_version=FLOW_VERSION)
        if p:
            public.update({k:p[k] for k in ('bars','quotes','opening','price','period','rule_kind','rule_origin','indicator_status') if k in p})
            public['reasons']=reasons(s['account'],p,s['day']) if s['status']=='active' else {k:'练习已结束' for k in ('BUY','SELL','HOLD')}
            public['account']['currently_sellable']=stats['unlocked'] if not public['reasons']['SELL'] else 0
        else:
            public.update(bars=[],quotes={},opening=None,price=s.get('mark',100),period=period,reasons={k:'暂停 · 请重试或结束' for k in ('BUY','SELL','HOLD')})
            public['account']['currently_sellable']=0
        if terminal:
            public['reveal']={'code':s['code'],'name':self.name(s['code']),'start':s['start'],'end':s['date']}
            public['review']=summary(s,events)
        return (public,p) if include_point else public
    def sync(self,c,s,period):
        result=self.view(c,s,period,compact=True)
        # Trade markers are small; complete HOLD history is loaded on demand.
        result['actions']=[public_receipt(__import__('json').loads(r[0])) for r in query(c, 'actions_4', (s['id'],))]
        result.update(response_kind='sync',transport_version='known_delta_v1')
        return result
    @staticmethod
    def delta(before,after):
        old,new=before['bars'],after.pop('bars')
        common=0
        while common<min(len(old),len(new)) and old[common]==new[common]:common+=1
        after.pop('actions',None)
        after.update(response_kind='delta',transport_version='known_delta_v1',base_revision=before['revision'],
            bar_delta={'remove_from':common,'append':new[common:],'total':len(new)} if after['status']!='paused' else None,
            action_delta=[after['last_receipt']] if after['last_receipt'] and after['revision']>before['revision'] else [])
        return after
    def list(self,c):
        items=[];groups={k:0 for k in ('complete_blind','replay','seen','revealed','abandoned','restricted','active','skipped','interrupted')}
        for row in query(c, 'sessions_2'):
            s=__import__('json').loads(row[0])
            item={k:s[k] for k in ('id','day','phase','status','kind','seen','restricted','revision')}
            item.update(length=s['settings']['length'],created_at=row[1],return_percent=numbers(s['account'],s.get('mark',100),s['day'])['return'])
            item.update(ended_at=s.get('ended_at'),end_reason=s.get('end_reason'),action_count=query(c, 'actions_9', (s['id'],)).fetchone()[0])
            item['mistakes']=query(c, 'notes_10', (s['id'],)).fetchone()[0]
            item['eligible']=s['status']=='completed' and not s['seen'] and not s['restricted'] and s['kind']=='blind'
            group='restricted' if s['restricted'] else 'replay' if s['kind']=='replay' else 'abandoned' if s['status']=='abandoned' else 'seen' if s['seen'] else 'complete_blind' if item['eligible'] else 'revealed' if s['status'] in ('revealed','boundary') else 'active'
            groups[group]+=1
            if s['status']=='abandoned':groups['skipped' if s.get('end_reason')=='skipped' else 'interrupted']+=1
            if s['status'] in ('completed','revealed','boundary','abandoned') or s.get('has_revealed'):item['name']=self.name(s['code'])
            items.append(item)
        return {'items':items[:100],'groups':groups,'rule_version':VERSION,'message':'日线竞价近似 · 未知规则仅可观望；北交所仅支持有当日直接限制价的样本。历史ST状态以所连接数据库的覆盖范围和逐日证据为准。'}
    def check_revision(self,s,params):
        if params.get('expected_revision')!=s['revision'] or params.get('expected_phase')!=s['phase']:raise ValueError('页面状态已更新，请读取最新进度；本次没有成交或推进')
    def invoke(self,op,params,request_id):
        if not isinstance(request_id,str) or not 8<=len(request_id)<=100:raise ValueError('请求身份无效')
        allowed={'training.list','training.start','training.state','training.act','training.finish','training.review','training.note','training.replay','training.abandon','training.history'}
        if op not in allowed:raise ValueError('训练操作无效')
        period=params.get('period','daily')
        if period not in ('daily','weekly','monthly'):raise ValueError('图表周期无效')
        compact=params.get('response_mode')=='delta'
        with self.lock,self.store.transaction() as c:
            if op=='training.list':return self.list(c)
            if op=='training.state':return (self.sync if compact else self.view)(c,self.store.get(c,params['id']),period)
            if op=='training.history':
                self.store.get(c,params['id'])
                offset=params.get('offset',0);limit=params.get('limit',100)
                if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:raise ValueError('流水分页无效')
                rows=query(c, 'actions_5', (params['id'],limit,offset))
                return {'items':[public_receipt(__import__('json').loads(r[0]))|{'note':r[1] or '', 'mistake':bool(r[2])} for r in rows],'offset':offset,'total':query(c, 'actions_9', (params['id'],)).fetchone()[0]}
            if op=='training.review':return at(self,c,params)
            if op=='training.note':return note(self,c,params)
            prior=query(c, 'requests_7', (request_id,)).fetchone()
            if prior:
                if prior['operation']!=op or prior['params_hash']!=digest(params):raise ValueError('请求身份冲突，本次没有执行')
                return (self.sync if compact else self.view)(c,self.store.get(c,prior['session_id']),period)
            if op=='training.replay':
                s=replay(self,c,params)
                for key in ('_record_stats','last_activity_at','ended_at','end_reason','previous_session','carry_method'):s.pop(key,None)
                s['started_at']=timestamp();result=(self.sync if compact else self.view)(c,s,period)
            elif op=='training.start':
                settings=self.options(params);previous=None
                if params.get('after_id'):
                    previous=self.store.get(c,params['after_id'])
                    if previous['status'] not in TERMINAL:raise ValueError('请先结束当前训练，再换下一只股票')
                # A stock switch transfers the saved marked value, not shares or
                # a fabricated SELL. Keep the old account/receipts immutable.
                account=initial()
                if previous:
                    old=previous['account'];capital=D(old['cash'])+D(old['units'])*D(previous.get('mark',100))
                    account=initial(capital,origin_capital=old.get('origin_capital',100000),
                                    prior_fees=D(old.get('prior_fees',0))+D(old['fees']))
                seed=secrets.randbits(63);before=self.signature();chosen=self.data.choose(seed,dict(settings,_exclude_code=previous['code']) if previous else settings)
                if previous and chosen['code']==previous['code']:raise ValueError('当前范围没有其他可用股票，请调整训练设置后开始')
                if before!=self.signature():raise ValueError('行情来源正在变化，请稍后重试')
                s={'id':uuid.uuid4().hex,'code':chosen['code'],'start':chosen['start'],'date':chosen['start'],'phase':'OPEN','day':1,
                   'revision':0,'status':'active','settings':settings,'seed':seed,'rule_version':VERSION,'account':account,'benchmark':initial(account['initial_capital'],origin_capital=account['origin_capital']),
                   'restricted':False,'seen':False,'kind':'blind','adopted':[],'position_days':[],'mark':100,'signature':self.signature()}
                s.update(flow_version=FLOW_VERSION,window_end=chosen.get('end'),sampling_version=chosen.get('sampling_version','fixture'))
                s['started_at']=timestamp()
                if previous:s.update(previous_session=previous['id'],carry_method='last_known_nav')
                s['visible_from']=self.data.background(s['code'],s['start'])[0]['trade_date']
                # Repeated/overlapping revealed stock periods are marked seen.
                for row in query(c, 'sessions_8'):
                    previous=__import__('json').loads(row[0])
                    if previous['code']==s['code'] and previous['status'] in ('completed','revealed','boundary') and previous['start']<=s['start']<=previous['date']:s['seen']=True
                result=(self.sync if compact else self.view)(c,s,period)
            else:
                s=self.store.get(c,params['id']);self.check_revision(s,params)
                if op=='training.act':
                    state,p=self.view(c,s,period,include_point=True,compact=compact,persist=False)
                    if s['status']!='active':return self.sync(c,s,period) if compact else state
                    action=params.get('action')
                    benchmark_before=dict(s['benchmark']);position_days_before=list(s['position_days'])
                    account,receipt=execute(s['account'],action,p,s['day'],fee_bps=s['settings']['fee_bps'],slippage_bps=s['settings']['slippage_bps'])
                    if D(s['account']['units'])>0 or D(account['units'])>0:
                        if s['day'] not in s['position_days']:s['position_days'].append(s['day'])
                    # B&H follows the same first executable quote and cost settings.
                    if D(s['benchmark']['units'])==0 and not state['reasons']['BUY']:
                        s['benchmark'],_=execute(s['benchmark'],'BUY',p,s['day'],fee_bps=s['settings']['fee_bps'],slippage_bps=s['settings']['slippage_bps'])
                    receipt.update(_account_before=dict(s['account']),_benchmark_before=benchmark_before,
                                   _account_after=dict(account),recorded_at=timestamp(),
                                   _position_days_before=position_days_before,day=s['day'],phase=s['phase'],revision=s['revision'],request_id=request_id,
                                   round=state['round'],advanced=action=='HOLD',flow_version=FLOW_VERSION,
                                   reasons=state['reasons'],rule_kind=state['rule_kind'],rule_origin=state['rule_origin'])
                    query(c, 'actions_11', (s['id'],s['revision'],request_id,__import__('json').dumps(receipt,ensure_ascii=False)))
                    s['account']=account;s['revision']+=1
                    s['_record_stats'][{'BUY':'buys','SELL':'sells','HOLD':'holds'}[action]]+=1
                    s['last_activity_at']=receipt['recorded_at']
                    s['flow_version']=FLOW_VERSION
                    if action=='HOLD':
                        if s['phase']=='OPEN':s['phase']='CLOSE'
                        elif s['day']>=s['settings']['length']:s['status']='completed';s['ended_at']=timestamp();s['end_reason']='completed'
                        else:
                            date=self.data.windows().prepare(s,checked=True).next_date(s['date']) if hasattr(self.data,'windows') else next_date(self.data,s['date'])
                            if not date:s['status']='boundary';s['restricted']=True;s['ended_at']=timestamp();s['end_reason']='source_boundary'
                            else:s['date']=date;s['day']+=1;s['phase']='OPEN'
                    result=self.view(c,s,period,compact=compact)
                    if compact:result=self.delta(state,result)
                else:
                    if op=='training.abandon':
                        if s['status']!='revealed':raise ValueError('仅提前揭晓局可标记弃局')
                        s['status']='abandoned'
                    else:
                        if s['status'] not in ('active','paused'):raise ValueError('练习已经结束')
                        s['status']='abandoned' if params.get('abandon') else 'revealed'
                    s.setdefault('ended_at',timestamp())
                    s['end_reason']=('skipped' if query(c, 'actions_9', (s['id'],)).fetchone()[0]==0 else 'interrupted') if s['status']=='abandoned' else 'revealed'
                    s.pop('before_pause',None);s['revision']+=1
                    result=(self.sync if compact else self.view)(c,s,period)
            query(c, 'requests_3', (request_id,op,digest(params),s['id']))
            return result
