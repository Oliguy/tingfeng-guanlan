"""Parse reviewed literal data from a verified archive; never execute upstream Python."""
import ast
import hashlib
import json
import os
import uuid
from datetime import date
from pathlib import Path
from guanlan_data.repositories.observer_calendar.repository import load; from guanlan_data.repositories.observer_calendar.repository import root


def atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    os.replace(temp, path)


def parse_holidays(payload):
    if len(payload) > 1048576:
        raise ValueError('上游日历文件超出大小限制')
    tree = ast.parse(payload.decode('utf-8-sig'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'precomputed_shanghai_holidays' for t in node.targets):
            if not isinstance(node.value, ast.Call) or len(node.value.args) != 1:
                break
            fn = node.value.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == 'to_datetime'
                    and isinstance(fn.value, ast.Name) and fn.value.id == 'pd') or node.value.keywords:
                break
            values = ast.literal_eval(node.value.args[0])
            if not isinstance(values, list) or not values or len(values) > 20000:
                break
            if any(not isinstance(d, str) or date.fromisoformat(d).isoformat() != d for d in values):
                break
            if len(values) != len(set(values)) or values != sorted(values):
                raise ValueError('上游休市日期重复或未排序，未发布日历')
            return values
    raise ValueError('上游日历格式改变，未执行代码、未发布新日历')


def publish_archive(snapshot, *, tiangong_root, library_id, directory=None, market=None, official_years=None):
    folder = Path(directory) if directory is not None else root()
    base = Path(tiangong_root).resolve()
    archive = (base / snapshot['archive_path']).resolve()
    if not archive.is_relative_to((base / 'outputs').resolve()) or snapshot['coverage'] != 'complete':
        raise ValueError('归藏日历未完整发布或路径无效')
    manifest_bytes = (archive / 'manifest.json').read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != snapshot['manifest_hash']:
        raise ValueError('归藏清单已变化')
    manifest = json.loads(manifest_bytes)
    if manifest['coverage'] != 'complete' or manifest['task_id'] != snapshot['task_id']:
        raise ValueError('日历归藏回执不一致')
    item = next(r for r in manifest['files'] if r['path'] == 'raw/repository/exchange_calendars/exchange_calendar_xshg.py')
    payload = (archive / item['path']).read_bytes()
    if len(payload) != item['bytes'] or hashlib.sha256(payload).hexdigest() != item['sha256']:
        raise ValueError('归藏日历源文件校验失败')
    holidays = parse_holidays(payload)
    first, last = int(holidays[0][:4]), int(holidays[-1][:4])
    previous = load(market, directory=folder)
    official_years = official_years if official_years is not None else previous.official_years
    raw = {'schema_version': 'observer-calendar.snapshot.v1', 'start': f'{first}-01-01', 'end': f'{last}-12-31',
           'holidays': holidays, 'official_years': official_years,
           'source': {'kind': 'archived_xshg_holidays', 'repository': 'gerrymanoim/exchange_calendars',
                      'commit': snapshot['source_revision'], 'file_sha256': item['sha256'], 'library_id': library_id,
                      'snapshot_id': snapshot['id'], 'task_id': snapshot['task_id'], 'archive_path': str(archive),
                      'manifest_sha256': snapshot['manifest_hash'], 'license': 'Apache-2.0'}}
    holiday_set = set(holidays)
    conflicts = [d for d, opened in previous.days.items() if raw['start'] <= d <= raw['end']
                 and opened is not None and opened != int(date.fromisoformat(d).weekday() < 5 and d not in holiday_set)]
    if conflicts:
        raise ValueError(f'新日历与既有历史冲突（{len(conflicts)}日），保留旧版；首日{conflicts[0]}')
    semantic = hashlib.sha256(json.dumps([raw['start'], raw['end'], holidays, official_years], sort_keys=True).encode()).hexdigest()
    target = folder / 'snapshots' / (semantic + '.json')
    if not target.exists():
        atomic_json(target, raw)
    registry = {'schema_version': 'observer-calendar.registry.v1', 'snapshot': 'snapshots/' + target.name,
                'snapshot_sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'library_id': library_id,
                'tiangong_root': str(base), 'semantic_revision': semantic}
    pointer = folder / 'current.json'
    if not pointer.exists() or json.loads(pointer.read_text('utf-8')) != registry:
        atomic_json(pointer, registry)
    return {'coverage_start': raw['start'], 'coverage_end': raw['end'], 'semantic_revision': semantic,
            'compared_days': sum(raw['start'] <= d <= raw['end'] for d in previous.days), 'conflicts': 0,
            'snapshot_id': snapshot['id'], 'production_sqlite_modified': False}
