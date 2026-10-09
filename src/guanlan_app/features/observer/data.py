"""Read adapters. No updater imports, installation scans or source writes."""
import copy
import threading
from pathlib import Path
from guanlan_data.repositories.observer.config import settings; from guanlan_data.repositories.observer.config import result_path
from guanlan_data.repositories.observer_calendar import load as load_calendar
from guanlan_data.repositories.observer_collections.reader import query as collection_query

class DataModules:
    def __init__(self):
        self.lock = threading.RLock()
        self.etf = None
        self.industry_cache = None
        self.member_cache = {}
        self.quote_summaries = {}
        self.movers = None
        self.movers_lock=threading.Lock()

    def query(self, module, params):
        if module=='movers':
            with self.movers_lock:
                if self.movers is None:
                    from guanlan_data.repositories.observer_movers.reader import Reader
                    self.movers=Reader()
            return self.movers.query(params)
        # Collection reads own their connections. A slow member projection must
        # not hold the legacy ETF session lock and block chart navigation.
        if module in ('industry30','theme'):
            from guanlan_data.repositories.observer_collections.config import results_path
            if module=='theme' or results_path().exists():
                if not params.get('run_id') or params.get('publication_id'):return collection_query(module,params)
        with self.lock:
            if module == 'etf':
                if self.etf is None:
                    from guanlan_data.repositories.observer.etf_reader import EtfReader
                    self.etf = EtfReader(Path(settings()['data_root']))
                return self.etf.query(params)
            if module != 'industry30':
                raise ValueError('未知数据模块')
            return self.industry(params)

    def industry(self, params):
        from guanlan_data.repositories.industry_index.store import query; from guanlan_data.repositories.industry_index.store import readonly
        view = params.get('view')
        if view not in ('health', 'summary', 'detail'):
            raise ValueError('未知查询')
        if set(params) - {'view', 'if_revision', 'target', 'period', 'price_mode', 'limit', 'run_id', 'industry_id'}:
            raise ValueError('未知查询参数')
        if view == 'health':
            return {'available': result_path().is_file()}
        if view == 'summary':
            raw = query(result_path())
            revision = raw['run_id']
            if params.get('if_revision') == revision:
                return {'not_modified': True, 'data_revision': revision}
            if self.industry_cache and self.industry_cache['data_revision'] == revision:
                return copy.deepcopy(self.industry_cache)
            # The menu needs only precomputed weekly bars, never all members/daily points.
            from guanlan_data.repositories.industry_index.store import menu_weekly
            weekly=menu_weekly(result_path(),revision)
            items = []
            for row in raw['groups']:
                items.append({'group_id': row['id'], 'group_name': row['name'],
                              'group_return': (row['latest'] or {}).get('daily_return'),
                              'weekly_strength': strength(weekly[row['id']],load_calendar()), 'stats': row['stats']})
            self.industry_cache = {'data_revision': revision, 'as_of': raw['header']['actual_end'],
                                   'expected_trade_date': raw['header']['actual_end'],
                                   'focus_items': [], 'items': items, 'header': raw['header']}
            return copy.deepcopy(self.industry_cache)
        target = params.get('target', {})
        if isinstance(target,dict) and target.get('kind')=='stock':
            return self.stock(params)
        if set(target) != {'kind', 'id'} or target['kind'] != 'group':
            raise ValueError('板块目标无效')
        period = params.get('period', 'daily')
        if period not in ('daily', 'weekly') or params.get('price_mode', 'adjusted') != 'adjusted':
            raise ValueError('板块仅有连续等权指数口径')
        raw = query(result_path(), view='detail', industry=target['id'], run_id=params.get('run_id'))
        g = raw['group']
        from guanlan_data.repositories.industry_index.member_prices import summaries
        revision=raw['run_id']
        if revision not in self.quote_summaries:
            if len(self.quote_summaries)>=3:self.quote_summaries.pop(next(iter(self.quote_summaries)))
            self.quote_summaries[revision]=summaries(result_path(),revision)
        quotes=self.quote_summaries[revision]
        g['members']=[{**m,'quote':quotes.get(m['code'],{})} for m in g['members']]
        points = g['weekly_points'] if period == 'weekly' else g['points']
        bars = [{**p, 'volume': p.get('mean_volume')} for p in points]
        series = [{**p, 'synthetic_index': p.get('close'), 'aggregate_amount': p.get('amount')} for p in points]
        return {'module': 'industry30', 'data_revision': raw['run_id'], 'target': target,
                'price_mode': 'adjusted', 'period': period, 'chart_bars': bars, 'series': series,
                'group': {'group_id': g['id'], 'group_name': g['name'], 'group_type': 'industry30'},
                'weekly_strength': strength(g['weekly_points'],load_calendar()), 'price_date': raw['header']['actual_end'],
                'industry': g, 'header': raw['header']}

    def stock(self, params):
        from guanlan_data.repositories.industry_index.store import query
        from guanlan_data.repositories.industry_index.member_prices import bars
        target=params['target'];run_id=params.get('run_id');parent=params.get('industry_id')
        if set(target)!={'kind','id'} or not isinstance(target['id'],str) or not run_id:raise ValueError('成员行情需要固定行业版本与证券代码')
        raw=query(result_path(),view='members',industry=parent,run_id=run_id)
        member=next((m for m in raw['group']['members'] if m['code']==target['id']),None)
        if not member:raise ValueError('该股票不属于当前版本的行业')
        period=params.get('period','daily');mode=params.get('price_mode','adjusted')
        key=(run_id,parent,target['id'],period,mode)
        if key not in self.member_cache:
            points,payload,events=bars(result_path(),run_id,target['id'],raw['header']['start_date'],period,mode,raw['header'].get('display_dates'))
            result={'module':'industry30','data_revision':run_id,'target':target,'parent':{'kind':'group','id':parent,'name':raw['group']['name']},
                    'price_mode':mode,'period':period,'chart_bars':points,'series':[],
                    'price_date':payload['summary'].get('date'),'stock':{**member,'quote':payload['summary'],'events':events,'metadata':payload.get('metadata')},
                    'units':{'price':'元/股','volume':'股','amount':'元'},'header':raw['header']}
            if len(self.member_cache)>=80:self.member_cache.pop(next(iter(self.member_cache)))
            self.member_cache[key]=result
        return copy.deepcopy(self.member_cache[key])

    def close(self):
        if self.etf: self.etf.close()

from guanlan_domain.observer_math.signals import strength
