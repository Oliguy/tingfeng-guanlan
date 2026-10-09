"""Read-only ETF projections; owns no writer, source gateway or job lifecycle."""
from __future__ import annotations
import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from itertools import combinations
from statistics import median
from pathlib import Path
from typing import Any, Mapping, Sequence
import guanlan_data.repositories.guanlan_backend.market_etf.observer_data as observer_data
from guanlan_domain.guanlan_backend.market_etf.domain.primitives import continuous_adjust_bars
from guanlan_data.repositories.guanlan_backend.market_etf.calendar_repository import CalendarRepository
from guanlan_domain.observer_math.zhixing import ZHIXING_Z_PARAMS; from guanlan_domain.observer_math.zhixing import calculate_zhixing_trend_series
PUBLIC_HISTORY_START = '2024-01-01'
LEADER_RULE_VERSION = 'monthly_complete_calendar_min_60_weeks_v3'
LEADER_MIN_HISTORY_WEEKS = 60
AGGREGATE_RULE_VERSION = observer_data.RULE_VERSION
DISPLAY_GAP_THRESHOLD = 0.2

def load_latest_stock_returns(*args, **kwargs):
    from guanlan_data.market import load_latest_stock_returns as load
    return load(*args, **kwargs)

def attach_zhixing_trend_lines(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Attach canonical Zhixing main-chart values without creating a parallel store."""
    payload = [dict(row) for row in rows]
    if not payload:
        return payload
    if any((row.get('close') is None for row in payload)):
        for row in payload:
            row['z_zhixing_short_trend'] = None
            row['z_zhixing_bull_bear'] = None
        return payload
    closes = [float(row['close']) for row in payload]
    short_trend, bull_bear = calculate_zhixing_trend_series(closes)
    for index, row in enumerate(payload):
        row['z_zhixing_short_trend'] = short_trend[index]
        row['z_zhixing_bull_bear'] = bull_bear[index]
    return payload

def _csv_values(value: Any) -> list[str]:
    if value is None:
        return []
    return [item for item in str(value).split(',') if item]

class ObserverReadRepository:

    def __init__(self, connect, equity_raw_db, *, calendar=None):
        self._connect = connect
        self.equity_raw_db = Path(equity_raw_db)
        self.calendar = calendar or CalendarRepository(lambda: self.equity_raw_db, self.connect)

    def connect(self, *, readonly=True):
        if not readonly:
            raise RuntimeError('ObserverReadRepository is read-only')
        return self._connect(readonly=True)

    def revision_state(self) -> dict[str, Any]:
        conn = self.connect(readonly=True)
        try:
            return observer_data.revision_state(conn)
        finally:
            conn.close()

    def apply_revision_quality(self, rows: Sequence[Mapping[str, Any]], revision: Mapping[str, Any] | None=None) -> list[dict[str, Any]]:
        conn = self.connect(readonly=True)
        try:
            return observer_data.overlay_quality(conn, rows, revision)
        finally:
            conn.close()

    def share_change_intervals(self, etf_code: str) -> list[dict[str, Any]]:
        conn = self.connect(readonly=True)
        try:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='observer_share_observations'").fetchone():
                return []
            return observer_data.share_intervals(conn, str(etf_code).zfill(6))
        finally:
            conn.close()

    def focus_dashboard(self) -> dict[str, Any]:
        conn = self.connect(readonly=True)
        try:
            rows = [dict(row) for row in conn.execute('SELECT f.*,m.fund_name,m.tracking_index_code,m.tracking_index_name,\n                              d.trade_date,d.close,d.volume,d.amount,\n                              CASE\n                                WHEN d.close IS NULL OR COALESCE(d.prev_close,prior.close) IS NULL\n                                  OR COALESCE(d.prev_close,prior.close)=0\n                                  THEN NULL\n                                ELSE d.close/COALESCE(d.prev_close,prior.close)-1.0\n                              END AS daily_return\n                       FROM etf_focus_watchlist f\n                       JOIN etf_master m ON m.etf_code=f.etf_code\n                       LEFT JOIN etf_daily d ON d.etf_code=f.etf_code\n                         AND d.trade_date=(SELECT MAX(x.trade_date) FROM etf_daily x\n                                            WHERE x.etf_code=f.etf_code AND x.close IS NOT NULL)\n                       LEFT JOIN etf_daily prior ON prior.etf_code=f.etf_code\n                         AND prior.trade_date=(SELECT MAX(p.trade_date) FROM etf_daily p\n                                                WHERE p.etf_code=f.etf_code\n                                                  AND p.trade_date<d.trade_date\n                                                  AND p.close IS NOT NULL)\n                       WHERE f.is_active=1\n                       ORDER BY f.display_order,f.etf_code')]
            latest = max((row.get('trade_date') or '' for row in rows), default='') or None
            return {'as_of': latest, 'items': rows}
        finally:
            conn.close()

    def focus_detail(self, etf_code: str, *, limit: int=260, include_warmup: bool=False, skip_stock_returns: bool=False) -> dict[str, Any] | None:
        """Return a focus ETF in the same chart contract as an industry detail."""
        code = str(etf_code).strip().zfill(6)
        conn = self.connect(readonly=True)
        try:
            focus = conn.execute('SELECT f.*,m.instrument_id,m.exchange,m.fund_name,m.fund_type,\n                          m.tracking_index_code,m.tracking_index_name,m.listing_date\n                   FROM etf_focus_watchlist f JOIN etf_master m ON m.etf_code=f.etf_code\n                   WHERE f.etf_code=? AND f.is_active=1', (code,)).fetchone()
            if not focus:
                return None
            history_limit = limit + max(ZHIXING_Z_PARAMS.values())
            bars_raw = [dict(row) for row in conn.execute('SELECT * FROM etf_daily WHERE etf_code=? ORDER BY trade_date DESC LIMIT ?', (code, history_limit))][::-1]
            latest_report = conn.execute('SELECT max(report_date) FROM etf_holdings WHERE etf_code=?', (code,)).fetchone()[0]
            holdings = []
            if latest_report:
                holdings = [dict(row) for row in conn.execute('SELECT * FROM etf_holdings WHERE etf_code=? AND report_date=? ORDER BY rank', (code, latest_report))]
                market_returns = {} if skip_stock_returns else load_latest_stock_returns([row['stock_code'] for row in holdings], kline_db=self.equity_raw_db)
                for holding in holdings:
                    market_row = market_returns.get(str(holding['stock_code']).zfill(6), {})
                    holding['daily_return'] = market_row.get('daily_return')
                    holding['return_trade_date'] = market_row.get('trade_date')
        finally:
            conn.close()
        reference_prices = {row['trade_date']: dict(row) for row in bars_raw}
        bars, adjustment_events = continuous_adjust_bars(bars_raw)
        bars = attach_zhixing_trend_lines(bars)
        bars_raw = attach_zhixing_trend_lines(bars_raw)
        if include_warmup:
            bars = bars[-limit:]
            bars_raw = bars_raw[-limit:]
        else:
            bars = [row for row in bars if row['trade_date'] >= PUBLIC_HISTORY_START][-limit:]
            bars_raw = [row for row in bars_raw if row['trade_date'] >= PUBLIC_HISTORY_START][-limit:]
        focus_conn = self.connect(readonly=True)
        try:
            focus_observations = observer_data.share_observations(focus_conn, [code]).get(code, {}) if focus_conn.execute("SELECT 1 FROM sqlite_master WHERE name='observer_share_observations'").fetchone() else {}
        finally:
            focus_conn.close()
        focus_calendar, _ = observer_data.calendar_dates(self, (date.fromisoformat(min(reference_prices)) - timedelta(days=10)).isoformat(), bars_raw[-1]['trade_date']) if bars_raw else ([], 'MISSING')
        focus_previous_days = {day: focus_calendar[index - 1] if index else None for index, day in enumerate(focus_calendar)}
        series: list[dict[str, Any]] = []
        synthetic_index = 1000.0
        prior_close = None
        prior_shares = None
        continuity_lost, index_started = (False, False)
        recent_amounts: list[float] = []
        for row in bars_raw:
            close = float(row['close']) if row.get('close') is not None else None
            prior_day = focus_previous_days.get(row['trade_date'])
            prior_bar = reference_prices.get(prior_day, {})
            comparison_close = row.get('prev_close') or prior_bar.get('close') if prior_bar.get('close') is not None else None
            daily_return = close / float(comparison_close) - 1.0 if close is not None and comparison_close not in {None, 0} else None
            if daily_return is not None and (not continuity_lost):
                synthetic_index *= 1.0 + daily_return
                index_started = True
                visible_index = synthetic_index
            else:
                if index_started:
                    continuity_lost = True
                visible_index = None
            amount = float(row['amount']) if row.get('amount') is not None else None
            relative = amount / median(recent_amounts[-20:]) if amount is not None and len(recent_amounts) >= 20 and median(recent_amounts[-20:]) else None
            shares = row.get('shares_outstanding')
            nav = row.get('nav')
            estimated, flow_reason = observer_data.daily_flow(focus_observations.get(row['trade_date']), focus_observations.get(focus_previous_days.get(row['trade_date'])))
            series.append({'trade_date': row['trade_date'], 'group_id': f'focus-{code}', 'leader_etf_code': code, 'aggregate_volume': row.get('volume'), 'aggregate_amount': row.get('amount'), 'relative_amount_20d': relative, 'estimated_net_subscription': estimated, 'flow_status': 'PASS' if estimated is not None else 'UNAVAILABLE', 'flow_reason': flow_reason, 'group_return': daily_return, 'synthetic_index': visible_index, 'calendar_status': 'PASS' if row['trade_date'] in focus_previous_days else 'MISSING', 'membership_status': 'PASS', 'quote_status': 'PASS' if close is not None and amount is not None else 'MISSING', 'return_status': 'PASS' if daily_return is not None else 'MISSING', 'leader_status': 'PASS', 'history_status': 'PASS' if visible_index is not None else 'MISSING', 'membership_basis': 'single_etf', 'expected_member_count': 1, 'covered_member_count': 1 if close is not None else 0, 'coverage_ratio': 1.0 if close is not None else 0.0, 'quality_status': 'PASS' if close is not None and amount is not None and (daily_return is not None) and (visible_index is not None) and (estimated is not None) and (row['trade_date'] in focus_previous_days) else 'PARTIAL', 'reason_codes': [flow_reason] if flow_reason != 'PASS' else []})
            if amount is not None:
                recent_amounts.append(amount)
            if close is not None:
                prior_close = close
            if shares is not None:
                prior_shares = shares
        leader = dict(focus)
        latest_bar = bars_raw[-1] if bars_raw else None
        if latest_bar:
            previous_close = latest_bar.get('prev_close')
            if previous_close in {None, 0} and len(bars_raw) > 1:
                previous_close = bars_raw[-2].get('close')
            leader['daily_return'] = float(latest_bar['close']) / float(previous_close) - 1.0 if latest_bar.get('close') is not None and previous_close not in {None, 0} else None
            leader['return_trade_date'] = latest_bar.get('trade_date')
        group = {'group_id': f'focus-{code}', 'group_name': focus['focus_name'], 'group_type': 'focus', 'is_active': 1}
        return {'group': group, 'leader': leader, 'series': series[-limit:], 'leader_bars': bars, 'leader_bars_raw': bars_raw, 'leader_bar_mode': 'single_focus_etf', 'zhixing_overlay': {'method_version': 'technical_v1', 'white_line': 'z_zhixing_short_trend', 'yellow_line': 'z_zhixing_bull_bear', 'bull_bear_periods': list(ZHIXING_Z_PARAMS.values())}, 'adjustment_events': adjustment_events, 'holdings': holdings, 'members': [{'etf_code': code, 'fund_name': focus['fund_name'], 'tracking_index_code': focus['tracking_index_code'], 'tracking_index_name': focus['tracking_index_name'], 'confidence': 1.0, 'evidence_json': json.dumps({'focus_reason': focus['focus_reason']}, ensure_ascii=False), 'effective_from': focus['created_at'][:10]}], 'leader_history': [], 'stock_returns_status': 'not_requested' if skip_stock_returns else 'queried', 'share_change_intervals': self.share_change_intervals(code)}

    def dashboard(self) -> dict[str, Any]:
        conn = self.connect(readonly=True)
        try:
            latest = conn.execute('SELECT max(trade_date) FROM industry_daily').fetchone()[0]
            if not latest:
                return {'as_of': None, 'items': []}
            rows = [dict(row) for row in conn.execute('SELECT i.*,g.group_name,g.group_type,m.fund_name AS leader_name,\n                              CASE\n                                WHEN leader_day.close IS NULL THEN NULL\n                                WHEN COALESCE(\n                                    leader_day.prev_close,\n                                    (SELECT prior.close FROM etf_daily prior\n                                      WHERE prior.etf_code=i.leader_etf_code\n                                        AND prior.trade_date<i.trade_date\n                                        AND prior.close IS NOT NULL\n                                      ORDER BY prior.trade_date DESC LIMIT 1)\n                                ) IS NULL THEN NULL\n                                WHEN COALESCE(\n                                    leader_day.prev_close,\n                                    (SELECT prior.close FROM etf_daily prior\n                                      WHERE prior.etf_code=i.leader_etf_code\n                                        AND prior.trade_date<i.trade_date\n                                        AND prior.close IS NOT NULL\n                                      ORDER BY prior.trade_date DESC LIMIT 1)\n                                ) = 0 THEN NULL\n                                ELSE leader_day.close / COALESCE(\n                                    leader_day.prev_close,\n                                    (SELECT prior.close FROM etf_daily prior\n                                      WHERE prior.etf_code=i.leader_etf_code\n                                        AND prior.trade_date<i.trade_date\n                                        AND prior.close IS NOT NULL\n                                      ORDER BY prior.trade_date DESC LIMIT 1)\n                                ) - 1.0\n                              END AS leader_return\n                       FROM industry_daily i JOIN industry_groups g ON g.group_id=i.group_id\n                       LEFT JOIN etf_master m ON m.etf_code=i.leader_etf_code\n                       LEFT JOIN etf_daily leader_day\n                         ON leader_day.etf_code=i.leader_etf_code\n                        AND leader_day.trade_date=i.trade_date\n                       WHERE g.is_active=1\n                         AND i.trade_date=(\n                             SELECT MAX(latest_i.trade_date) FROM industry_daily latest_i\n                              WHERE latest_i.group_id=i.group_id\n                         )\n                       ORDER BY COALESCE(i.relative_amount_20d,0) DESC,g.display_order')]
            return {'as_of': latest, 'items': observer_data.overlay_quality(conn, rows)}
        finally:
            conn.close()

    def group_detail(self, group_id: str, *, limit: int=260, include_warmup: bool=False, skip_stock_returns: bool=False) -> dict[str, Any] | None:
        conn = self.connect(readonly=True)
        try:
            group = conn.execute('SELECT * FROM industry_groups WHERE group_id=?', (group_id,)).fetchone()
            if not group:
                return None
            series = [dict(row) for row in conn.execute('SELECT * FROM industry_daily WHERE group_id=? AND trade_date>=? ORDER BY trade_date DESC LIMIT ?', (group_id, PUBLIC_HISTORY_START, limit))][::-1]
            latest_day = conn.execute('SELECT max(trade_date) FROM industry_daily WHERE group_id=?', (group_id,)).fetchone()[0]
            if latest_day is None:
                latest_day = conn.execute('SELECT max(trade_date) FROM etf_daily').fetchone()[0]
            leader_month = latest_day[:7] if latest_day else date.today().strftime('%Y-%m')
            leader = conn.execute('SELECT l.*,m.fund_name,m.tracking_index_name FROM monthly_leaders l\n                   JOIN etf_master m ON m.etf_code=l.etf_code\n                   WHERE l.group_id=? AND l.leader_month=? AND l.is_current=1 LIMIT 1', (group_id, leader_month)).fetchone()
            leader_code = leader['etf_code'] if leader else None
            bars_raw = []
            holdings = []
            if leader_code:
                history_limit = limit + max(ZHIXING_Z_PARAMS.values())
                bars_raw = [dict(row) for row in conn.execute('SELECT * FROM etf_daily WHERE etf_code=? ORDER BY trade_date DESC LIMIT ?', (leader_code, history_limit))][::-1]
                latest_report = conn.execute('SELECT max(report_date) FROM etf_holdings WHERE etf_code=?', (leader_code,)).fetchone()[0]
                if latest_report:
                    holdings = [dict(row) for row in conn.execute('SELECT * FROM etf_holdings WHERE etf_code=? AND report_date=? ORDER BY rank', (leader_code, latest_report))]
                    market_returns = {} if skip_stock_returns else load_latest_stock_returns([row['stock_code'] for row in holdings], kline_db=self.equity_raw_db)
                    for holding in holdings:
                        market_row = market_returns.get(str(holding['stock_code']).zfill(6), {})
                        holding['daily_return'] = market_row.get('daily_return')
                        holding['return_trade_date'] = market_row.get('trade_date')
            members = [dict(row) for row in conn.execute("SELECT m.etf_code,e.fund_name,e.tracking_index_code,e.tracking_index_name,\n                              m.confidence,m.evidence_json,m.effective_from\n                       FROM etf_group_membership m JOIN etf_master e ON e.etf_code=m.etf_code\n                       WHERE m.group_id=? AND m.status='active' ORDER BY e.fund_name,m.etf_code", (group_id,))]
            history = [dict(row) for row in conn.execute('SELECT l.*,m.fund_name FROM monthly_leaders l\n                       JOIN etf_master m ON m.etf_code=l.etf_code\n                       WHERE l.group_id=? AND l.is_current=1 ORDER BY l.leader_month DESC', (group_id,))]
            bars, adjustment_events = continuous_adjust_bars(bars_raw)
            bars = attach_zhixing_trend_lines(bars)
            bars_raw = attach_zhixing_trend_lines(bars_raw)
            if include_warmup:
                bars = bars[-limit:]
                bars_raw = bars_raw[-limit:]
            else:
                bars = [row for row in bars if row['trade_date'] >= PUBLIC_HISTORY_START][-limit:]
                bars_raw = [row for row in bars_raw if row['trade_date'] >= PUBLIC_HISTORY_START][-limit:]
            leader_payload = dict(leader) if leader else None
            if leader_payload and bars_raw:
                latest_bar = bars_raw[-1]
                previous_close = latest_bar.get('prev_close')
                if previous_close in {None, 0} and len(bars_raw) > 1:
                    previous_close = bars_raw[-2].get('close')
                leader_payload['daily_return'] = float(latest_bar['close']) / float(previous_close) - 1.0 if latest_bar.get('close') is not None and previous_close not in {None, 0} else None
                leader_payload['return_trade_date'] = latest_bar.get('trade_date')
            return {'group': dict(group), 'leader': leader_payload, 'series': observer_data.overlay_quality(conn, series), 'leader_bars': bars, 'leader_bars_raw': bars_raw, 'stock_returns_status': 'not_requested' if skip_stock_returns else 'queried', 'share_change_intervals': observer_data.share_intervals(conn, leader_code) if leader else [], 'leader_bar_mode': 'continuous_current_leader', 'zhixing_overlay': {'method_version': 'technical_v1', 'white_line': 'z_zhixing_short_trend', 'yellow_line': 'z_zhixing_bull_bear', 'bull_bear_periods': list(ZHIXING_Z_PARAMS.values())}, 'adjustment_events': adjustment_events, 'holdings': holdings, 'members': members, 'leader_history': history}
        finally:
            conn.close()

    def coverage_audit(self) -> dict[str, Any]:
        """Audit classification coverage and cross-group exposure overlap.

        The audit distinguishes member exclusivity from economic exposure:
        one ETF can belong to exactly one observer while two representative
        ETFs still own many of the same stocks.
        """
        conn = self.connect(readonly=True)
        try:
            state_counts = {str(row['classification_state']): int(row['item_count']) for row in conn.execute('SELECT classification_state,count(*) AS item_count\n                       FROM etf_master GROUP BY classification_state')}
            for state in ('classified', 'excluded', 'unclassified'):
                state_counts.setdefault(state, 0)
            total_etfs = sum(state_counts.values())
            resolved_etfs = state_counts['classified'] + state_counts['excluded']
            unclassified_with_index = int(conn.execute("SELECT count(*) FROM etf_master\n                       WHERE classification_state='unclassified'\n                         AND tracking_index_name IS NOT NULL\n                         AND TRIM(tracking_index_name)<>''").fetchone()[0])
            groups = [dict(row) for row in conn.execute("SELECT g.group_id,g.group_name,g.group_type,\n                              count(DISTINCT m.etf_code) AS active_member_count,\n                              (SELECT l.etf_code FROM monthly_leaders l\n                                WHERE l.group_id=g.group_id AND l.is_current=1\n                                ORDER BY l.leader_month DESC LIMIT 1) AS leader_etf_code\n                       FROM industry_groups g\n                       LEFT JOIN etf_group_membership m\n                         ON m.group_id=g.group_id AND m.status='active'\n                       WHERE g.is_active=1\n                       GROUP BY g.group_id,g.group_name,g.group_type\n                       ORDER BY g.group_type,g.group_name")]
            active_member_count = int(conn.execute("SELECT count(DISTINCT etf_code) FROM etf_group_membership WHERE status='active'").fetchone()[0])
            duplicate_memberships = [dict(row) for row in conn.execute("SELECT etf_code,count(DISTINCT group_id) AS group_count\n                       FROM etf_group_membership WHERE status='active'\n                       GROUP BY etf_code HAVING count(DISTINCT group_id)>1\n                       ORDER BY group_count DESC,etf_code")]
            exact_index_cross_groups = [dict(row) for row in conn.execute("SELECT e.tracking_index_code,e.tracking_index_name,\n                              count(DISTINCT m.group_id) AS group_count,\n                              group_concat(DISTINCT m.group_id) AS group_ids\n                       FROM etf_group_membership m\n                       JOIN etf_master e ON e.etf_code=m.etf_code\n                       WHERE m.status='active'\n                         AND e.tracking_index_code IS NOT NULL\n                         AND TRIM(e.tracking_index_code)<>''\n                       GROUP BY e.tracking_index_code,e.tracking_index_name\n                       HAVING count(DISTINCT m.group_id)>1\n                       ORDER BY group_count DESC,e.tracking_index_code")]
            exposures: dict[str, dict[str, float]] = {}
            exposure_meta: dict[str, dict[str, Any]] = {}
            group_names = {row['group_id']: row['group_name'] for row in groups}
            for group in groups:
                leader_code = group.get('leader_etf_code')
                if not leader_code:
                    continue
                report_date = conn.execute('SELECT max(report_date) FROM etf_holdings WHERE etf_code=?', (leader_code,)).fetchone()[0]
                if not report_date:
                    continue
                holdings = [dict(row) for row in conn.execute('SELECT stock_code,stock_name,weight_pct FROM etf_holdings\n                           WHERE etf_code=? AND report_date=?\n                           ORDER BY rank LIMIT 10', (leader_code, report_date))]
                exposures[group['group_id']] = {str(row['stock_code']).zfill(6): float(row['weight_pct'] or 0) for row in holdings}
                exposure_meta[group['group_id']] = {'leader_etf_code': leader_code, 'report_date': report_date, 'holding_count': len(holdings), 'top10_weight_pct': sum((float(row['weight_pct'] or 0) for row in holdings))}
            overlap_pairs: list[dict[str, Any]] = []
            for left_id, right_id in combinations(sorted(exposures), 2):
                left, right = (exposures[left_id], exposures[right_id])
                left_codes, right_codes = (set(left), set(right))
                shared = sorted(left_codes & right_codes)
                union = left_codes | right_codes
                if not union:
                    continue
                overlap_pairs.append({'left_group_id': left_id, 'left_group_name': group_names.get(left_id, left_id), 'right_group_id': right_id, 'right_group_name': group_names.get(right_id, right_id), 'shared_holding_count': len(shared), 'shared_stock_codes': shared, 'jaccard': len(shared) / len(union), 'overlap_coefficient': len(shared) / min(len(left_codes), len(right_codes)) if left_codes and right_codes else 0.0, 'weighted_overlap_pct': sum((min(left[code], right[code]) for code in shared))})
            overlap_pairs.sort(key=lambda row: (row['weighted_overlap_pct'], row['jaccard']), reverse=True)
            overlap_values = sorted((float(row['weighted_overlap_pct']) for row in overlap_pairs))

            def percentile(values: list[float], fraction: float) -> float | None:
                if not values:
                    return None
                return values[min(len(values) - 1, round((len(values) - 1) * fraction))]
            maximum_overlap = overlap_values[-1] if overlap_values else 0.0
            maximum_jaccard = max((float(row['jaccard']) for row in overlap_pairs), default=0.0)
            overlap_level = 'HIGH' if maximum_overlap >= 30 or maximum_jaccard >= 0.6 else 'MODERATE' if maximum_overlap >= 15 or maximum_jaccard >= 0.35 else 'LOW'
            scope_status = 'PROVEN' if state_counts['unclassified'] == 0 else 'NOT_PROVEN'
            return {'schema_version': 'industry_etf_coverage_audit_v1', 'as_of': conn.execute('SELECT max(trade_date) FROM etf_daily').fetchone()[0], 'universe': {'total_etfs': total_etfs, 'classification_states': state_counts, 'resolved_etfs': resolved_etfs, 'resolved_ratio': resolved_etfs / total_etfs if total_etfs else None, 'unclassified_with_tracking_index': unclassified_with_index, 'active_classified_members': active_member_count}, 'groups': {'active_group_count': len(groups), 'industry_count': sum((row['group_type'] == 'industry' for row in groups)), 'theme_count': sum((row['group_type'] == 'theme' for row in groups)), 'items': groups}, 'exclusivity': {'duplicate_active_membership_count': len(duplicate_memberships), 'duplicate_active_memberships': duplicate_memberships[:20], 'exact_index_cross_group_count': len(exact_index_cross_groups), 'exact_index_cross_groups': exact_index_cross_groups[:20]}, 'representative_exposure_overlap': {'method': 'latest_leader_top10_minimum_weight_overlap_v1', 'groups_with_holdings': len(exposures), 'group_pair_count': len(overlap_pairs), 'median_weighted_overlap_pct': percentile(overlap_values, 0.5), 'p90_weighted_overlap_pct': percentile(overlap_values, 0.9), 'maximum_weighted_overlap_pct': maximum_overlap, 'maximum_jaccard': maximum_jaccard, 'pairs_at_or_above_20pct': sum((row['weighted_overlap_pct'] >= 20 for row in overlap_pairs)), 'top_pairs': overlap_pairs[:15], 'leader_holding_coverage': exposure_meta}, 'conclusion': {'market_scope_coverage': scope_status, 'member_exclusivity': 'PASS' if not duplicate_memberships else 'FAIL', 'economic_exposure_overlap': overlap_level, 'overall': 'PASS' if scope_status == 'PROVEN' and (not duplicate_memberships) and (overlap_level == 'LOW') else 'REVIEW_REQUIRED'}}
        finally:
            conn.close()

    def list_runs(self, limit: int=20) -> list[dict[str, Any]]:
        conn = self.connect(readonly=True)
        try:
            rows = [dict(row) for row in conn.execute('SELECT * FROM collection_runs ORDER BY started_at DESC LIMIT ?', (limit,))]
            for row in rows:
                checkpoint = json.loads(row.get('checkpoint_json') or '{}')
                row['execution_status'] = checkpoint.get('execution_status', row['status'])
            return rows
        finally:
            conn.close()
