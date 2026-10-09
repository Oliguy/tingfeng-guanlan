"""Strict read-only contracts for the ETF desktop view and Codex."""
from __future__ import annotations
import re
import threading
from pathlib import Path
from typing import Any
from guanlan_domain.contracts import OperationSpec
from guanlan_domain.contracts import OperationError

def validate_observer_params(params: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(params, dict):
        raise OperationError('invalid_params', 'params must be an object')
    view = params.get('view')
    allowed = {'health': {'view'}, 'summary': {'view', 'if_revision'}, 'detail': {'view', 'target', 'limit', 'price_mode', 'period'}, 'coverage': {'view'}}
    if not isinstance(view, str) or view not in allowed or set(params) - allowed[view]:
        raise OperationError('invalid_params', 'unknown view or parameters for view')
    result = dict(params)
    if 'if_revision' in params and (not isinstance(params['if_revision'], str) or not 1 <= len(params['if_revision']) <= 256):
        raise OperationError('invalid_params', 'invalid if_revision')
    if view == 'detail':
        target = params.get('target')
        if not isinstance(target, dict) or set(target) != {'kind', 'id'}:
            raise OperationError('invalid_params', 'target requires kind and id')
        kind, identifier = (target['kind'], target['id'])
        pattern = '[0-9]{6}' if kind == 'etf' else '[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}'
        if not isinstance(kind, str) or kind not in {'group', 'etf'} or (not isinstance(identifier, str)) or (not re.fullmatch(pattern, identifier)):
            raise OperationError('invalid_params', 'invalid target')
        limit = params.get('limit', 1000)
        if isinstance(limit, bool) or not isinstance(limit, int) or (not 20 <= limit <= 10000):
            raise OperationError('invalid_params', 'limit must be an integer from 20 to 10000')
        if not isinstance(params.get('price_mode', 'adjusted'), str) or params.get('price_mode', 'adjusted') not in {'adjusted', 'raw'}:
            raise OperationError('invalid_params', 'invalid price_mode')
        if not isinstance(params.get('period', 'daily'), str) or params.get('period', 'daily') not in {'daily', 'weekly'}:
            raise OperationError('invalid_params', 'invalid period')
        result.update(limit=limit, price_mode=params.get('price_mode', 'adjusted'), period=params.get('period', 'daily'))
    return result

class ObserverQuerySession:
    """Own the view's lazy readonly resources and close them with the kernel."""

    def __init__(self, data_root: Path):
        self.data_root = Path(data_root)
        self._queries = None
        self._lock = threading.RLock()
        self._closed = False

    def query(self, params):
        with self._lock:
            if self._closed:
                raise OperationError('application_closed', 'ETF窗口已关闭')
            if self._queries is None:
                from guanlan_data.repositories.guanlan_backend.market_etf.observer_query import EtfObserverQueries
                self._queries = EtfObserverQueries(self.data_root)
            return self._queries.query(params)

    def close(self):
        with self._lock:
            self._closed = True
            if self._queries is not None:
                self._queries.close()

def register_etf_observer_operations(registry, data_root: Path) -> ObserverQuerySession:
    queries = ObserverQuerySession(data_root)
    registry.register(OperationSpec(operation_id='data.etf.observer.query', owner='data_management', request_schema='etf_observer_query_request_v1', response_schema='etf_observer_query_result_v2', execution_mode='query', risk='read_only', lock_scope=None, confirmation_required=False, confirmation_note_required=False, legacy_aliases=(), package_group='data'), lambda request: queries.query(validate_observer_params(request.params)))
    return queries
