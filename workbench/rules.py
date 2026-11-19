"""任务规则与场地适配。

几何只有一个来源：当前所选任务（venue）。服务器下发值、本地覆盖值和未确认字段
始终保持来源区别；本地检查可以关闭，关闭时越界只提示、不拦截。

不在这里写死任何学校的点位或围栏：没有服务器任务时（独立工具、演示）才使用
examples/routes/fence_lylg.geojson 与 taskgen.CARDS 作为示例几何。
"""
from datetime import datetime, timedelta, timezone
import math

from .storage import ROOT, read_json

CHINA = timezone(timedelta(hours=8))
MAX_POINTS = 10000
MAX_FENCE = 512
LIMIT_FIELDS = ('km_min', 'km_max', 'pace_min', 'pace_max',
                'cadence_min', 'cadence_max', 'start', 'end')
RANGES = (('km', 'km_min', 'km_max', '里程', '公里'),
          ('pace', 'pace_min', 'pace_max', '平均配速', '分钟/公里'),
          ('cadence', 'cadence_min', 'cadence_max', '步频', '步/分'))
SERVER_KEYS = {'km_min': 'raSingleMileageMin', 'km_max': 'raSingleMileageMax',
               'pace_min': 'raPaceMin', 'pace_max': 'raPaceMax',
               'cadence_min': 'raCadenceMin', 'cadence_max': 'raCadenceMax',
               'start': 'dayStartTime', 'end': 'dayEndTime'}
TEXT_KEYS = ('requirementText', 'requirements', 'raRequirement', 'ruleText', 'runRequirement')
RAW_DENY = {'token', 'password', 'username', 'studentid', 'realname', 'phone', 'idcard',
            'points', 'fence', 'schoolid', 'userid', 'deviceid'}


def raw_fields(task):
    """服务器原始字段（原文速查）：跳过身份与坐标，只展示标量值，供对照官方 App。"""
    if not isinstance(task, dict):
        return []
    rows = []
    for key, value in task.items():
        if not isinstance(key, str) or key.lower() in RAW_DENY:
            continue
        if isinstance(value, (dict, list)) or value is None or value == '':
            continue
        rows.append({'key': key, 'value': str(value)[:160]})
    return rows[:80]


def cached(store):
    return read_json(store.data / 'rules.json', {'fetched_at': None, 'tasks': []})


def selected(store, required=False):
    tasks = cached(store).get('tasks') or []
    choice = str(store.settings().get('task_id', ''))
    task = next((t for t in tasks if str(t.get('id')) == choice), None)
    if not task and len(tasks) == 1: task = tasks[0]
    if required and not task:
        raise ValueError('请先选择一个有效的服务器任务。')
    return task


# ---------------------------------------------------------------- 几何解析
def _number(value):
    if type(value) not in (int, float, str):
        raise ValueError
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise ValueError from None
    if not math.isfinite(number):
        raise ValueError
    return number


def _pairs(raw, limit):
    """任务下发的坐标：文本 "lon,lat|lon,lat" 或二维数组。"""
    if raw is None or raw == [] or (isinstance(raw, str) and not raw.strip()):
        return [], 'missing'
    try:
        if isinstance(raw, str):
            raw = [part.split(',') for part in raw.strip().removesuffix('|').split('|')]
        if not isinstance(raw, list) or len(raw) > limit:
            raise ValueError
        # 只有一个点时也可能是 [经度, 纬度] 本身。
        if len(raw) == 2 and all(type(v) in (int, float) for v in raw):
            raw = [raw]
        pairs = []
        for item in raw:
            if not isinstance(item, list) or len(item) < 2:
                raise ValueError
            lon, lat = _number(item[0]), _number(item[1])
            if abs(lon) > 180 or abs(lat) > 90:
                raise ValueError
            pairs.append([lon, lat])
    except (ValueError, TypeError):
        return [], 'invalid'
    return pairs, 'available' if pairs else 'missing'


def _closed(pairs):
    """去掉首尾重复的闭合点；顶点重复或面积为零视为无效。"""
    if pairs and pairs[0] == pairs[-1]:
        pairs = pairs[:-1]
    if len(pairs) < 3 or len({tuple(p) for p in pairs}) != len(pairs):
        return []
    x, y = pairs[0]
    twice = sum((a[0] - x) * (b[1] - y) - (a[1] - y) * (b[0] - x)
                for a, b in zip(pairs, pairs[1:] + pairs[:1]))
    return pairs if abs(twice) > 1e-18 else []


def _simple(ring):
    """拒绝自相交的围栏：无法确定内外时不用它做越界校验。"""
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    edges = list(zip(ring, ring[1:] + ring[:1]))
    for i, (a, b) in enumerate(edges):
        for j in range(i + 2, len(edges)):
            if i == 0 and j == len(edges) - 1:
                continue
            c, d = edges[j]
            d1, d2 = cross(a, b, c), cross(a, b, d)
            d3, d4 = cross(c, d, a), cross(c, d, b)
            if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
                return False
    return True


def _fence(raw):
    pairs, status = _pairs(raw, MAX_FENCE)
    if status != 'available':
        return [], status
    ring = _closed(pairs)
    if not ring or not _simple(ring):
        return [], 'invalid'
    return ring, 'available'


def _points(raw):
    return _pairs(raw, MAX_POINTS)


def _inside(point, fence):
    import taskgen
    return taskgen.inside(point, fence)


def _sample():
    """没有服务器任务时的示例几何（独立工具、演示模式）。"""
    import taskgen
    fence = []
    try:
        obj = read_json(ROOT / 'examples/routes/fence_lylg.geojson')
        geom = obj.get('geometry') or obj
        if obj.get('type') == 'FeatureCollection':
            geom = obj['features'][0]['geometry']
        ring = [[float(x), float(y)] for x, y in geom['coordinates'][0]]
        if ring and ring[0] == ring[-1]: ring = ring[:-1]
        fence = ring
    except (ValueError, KeyError, IndexError, TypeError):
        fence = []
    return {'source': 'sample', 'task_id': '', 'name': '本地示例', 'campus': '',
            'cards': [list(p) for p in taskgen.CARDS], 'fence': fence,
            'points_status': 'sample', 'fence_status': 'sample' if fence else 'missing',
            'warnings': [], 'requirements': '', 'task': None}


def venue(store):
    """当前任务对应的场地。source = server / sample / unselected。

    unselected 表示账号有任务但还没选：此时不猜几何，由生成入口提示用户先选任务。
    """
    tasks = cached(store).get('tasks') or []
    task = selected(store)
    if not task:
        if tasks:
            return {'source': 'unselected', 'task_id': '', 'name': '', 'campus': '',
                    'cards': [], 'fence': [], 'points_status': 'missing', 'fence_status': 'missing',
                    'warnings': ['当前账号有多个打卡区域，尚未选择。'], 'requirements': '', 'task': None}
        return _sample()
    cards, points_status = _points(task.get('points'))
    fence, fence_status = _fence(task.get('fence'))
    warnings = []
    if points_status == 'invalid':
        warnings.append('打卡点格式无法识别，本地不做打卡点校验。')
    if fence_status == 'invalid':
        warnings.append('围栏格式无法识别，本地不做越界校验。')
    if cards and fence and any(not _inside(point, fence) for point in cards):
        cards, points_status = [], 'ambiguous'
        warnings.append('打卡点包含围栏外的点，归属需要核对。')
    return {'source': 'server', 'task_id': str(task.get('id', '')),
            'name': str(task.get('raName') or task.get('raRunName') or '').strip(),
            'campus': str(task.get('campusName') or '').strip(),
            'cards': cards, 'fence': fence,
            'points_status': points_status, 'fence_status': fence_status,
            'warnings': warnings, 'requirements': _requirement_text(task), 'task': task}


def _requirement_text(task):
    for key in TEXT_KEYS:
        value = task.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:4000]
    return ''


# ---------------------------------------------------------------- 本地检查
def _clean_limit(field, value):
    if value is None or value == '':
        return None
    if field in ('start', 'end'):
        import re
        if not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?', value.strip()):
            raise ValueError('时间请填写 HH:MM 或 HH:MM:SS。')
        return value.strip()
    number = _number(value)
    if number < 0:
        raise ValueError('检查范围不能为负。')
    return number


def _server_values(task):
    values = {}
    if not task: return values
    for field, key in SERVER_KEYS.items():
        try:
            clean = _clean_limit(field, task.get(key))
        except ValueError:
            clean = None
        if clean is not None: values[field] = clean
    return values


def _stored(store):
    checks = store.settings().get('checks')
    return checks if isinstance(checks, dict) else {}


def limits(store, task_id=None):
    """当前任务的检查范围：本地覆盖优先，其次服务器下发值。

    只保存用户改过的字段；没有服务器值也没有覆盖的字段不参与检查。
    """
    task = selected(store)
    key = str(task_id or (task or {}).get('id') or '')
    server = _server_values(task)
    saved = _stored(store).get(key) if key else None
    saved = saved if isinstance(saved, dict) else {}
    overrides = saved.get('values') if isinstance(saved.get('values'), dict) else {}
    values = dict(server)
    for field in LIMIT_FIELDS:
        if field in overrides:
            values[field] = overrides[field]
    missing = [field for field in LIMIT_FIELDS if field not in values]
    customized = any(field in overrides and overrides[field] != server.get(field) for field in LIMIT_FIELDS)
    return {'task_id': key, 'enabled': saved.get('enabled', True) is not False,
            'values': values, 'server': server, 'overrides': dict(overrides),
            'missing': missing, 'customized': customized}


def _validated_limits(values):
    if not isinstance(values, dict) or set(values) - set(LIMIT_FIELDS):
        raise ValueError('检查范围包含未支持的字段。')
    clean = {}
    for field in LIMIT_FIELDS:
        value = _clean_limit(field, values.get(field))
        if value is not None: clean[field] = value
    for _, lo, hi, label, _unit in RANGES:
        if lo in clean and hi in clean and clean[lo] > clean[hi]:
            raise ValueError(f'{label}下限不能大于上限。')
    return clean


def save_limits(store, task_id, enabled, values):
    """按任务保存本地检查：开关与改过的范围。"""
    task_id = str(task_id or '').strip()
    tasks = cached(store).get('tasks') or []
    if not task_id or not any(str(t.get('id')) == task_id for t in tasks):
        raise ValueError('任务已失效，请重新同步规则。')
    if not isinstance(enabled, bool):
        raise ValueError('开关状态不正确。')
    clean = _validated_limits(values or {})
    server = _server_values(next(t for t in tasks if str(t.get('id')) == task_id))
    overrides = {k: v for k, v in clean.items() if server.get(k) != v}
    checks = dict(_stored(store))
    checks[task_id] = {'enabled': enabled, 'values': overrides}
    store.save_settings({'checks': checks})
    return limits(store, task_id)


# ---------------------------------------------------------------- 视图与校验
def view(store):
    cache = cached(store)
    task = selected(store) or {}
    space = venue(store)
    check = limits(store)
    source = {'server': '服务器', 'sample': '本地示例', 'unselected': '未选择任务'}[space['source']]

    def value(key):
        raw = task.get(key)
        return {'value': raw if raw is not None else None,
                'source': '服务器' if raw is not None else '未确认'}

    tasks = []
    for item in cache.get('tasks', []):
        cards, _ = _points(item.get('points'))
        fence, _ = _fence(item.get('fence'))
        tasks.append({'id': str(item.get('id')),
                      'name': str(item.get('raName') or item.get('raRunName') or '校园跑步任务'),
                      'type': item.get('raType', ''), 'campus': str(item.get('campusName') or ''),
                      'points': len(cards), 'fence': bool(fence)})
    return {'fetched_at': cache.get('fetched_at'), 'selected_id': str(task.get('id', '')),
            'tasks': tasks,
            'fields': {'km_min': value('raSingleMileageMin'), 'km_max': value('raSingleMileageMax'),
                       'pace_min': value('raPaceMin'), 'pace_max': value('raPaceMax'),
                       'cadence_min': value('raCadenceMin'), 'cadence_max': value('raCadenceMax'),
                       'start': value('dayStartTime'), 'end': value('dayEndTime'),
                       'face': value('runFaceStatus'), 'batch': value('passPointNum'),
                       'max_minutes': {'value': store.settings()['max_minutes'], 'source': '本地配置'}},
            'points': [list(p) for p in space['cards']],
            'fence': [list(p) for p in space['fence']],
            'source': space['source'],
            'points_source': source, 'fence_source': source,
            'points_status': space['points_status'], 'fence_status': space['fence_status'],
            'campus': space['campus'], 'requirements': space['requirements'],
            'limits': check, 'venue_warnings': space['warnings'], 'raw': raw_fields(task),
            'minimum_cards': task.get('raMinDislikes'), 'declared_cards': task.get('raDislikes')}


def validate(store, metrics, formal=False):
    """返回 [{field, message, suggested?, level}]；level=error 拦截，notice 只提示。"""
    check = limits(store)
    values = check['values']
    level = 'error' if check['enabled'] else 'notice'
    issues = []

    def add(field, message, suggested=None, lvl=None):
        issues.append({'field': field, 'message': message, 'suggested': suggested, 'level': lvl or level})

    for field, lo, hi, label, unit in RANGES:
        number = float(metrics[field])
        lower, upper = values.get(lo), values.get(hi)
        if lower is None and upper is None: continue
        if not math.isfinite(number):
            add(field, f'{label}必须是有效数值。'); continue
        if lower is not None and number < lower - 0.015:
            add(field, f'{label}需不低于 {lower:g} {unit}。', min(upper, max(lower, number)) if upper is not None else lower)
        elif upper is not None and number > upper + 0.015:
            add(field, f'{label}需不高于 {upper:g} {unit}。', min(upper, max(lower, number)) if lower is not None else upper)
    max_seconds = float(store.settings()['max_minutes']) * 60
    if float(metrics['duration']) > max_seconds + 1:
        add('pace', f'预计时长超过本地配置的 {max_seconds/60:g} 分钟上限。', max_seconds/60/float(metrics['km']))
    if formal:
        task = selected(store, True)
        for key in ('raSingleMileageMin', 'raSingleMileageMax', 'raCadenceMin', 'raCadenceMax'):
            if task.get(key) is None:
                add('rules', f'服务器缺少必要字段 {key}，无法正式执行。', lvl='error')
        if not venue(store)['cards']:
            add('rules', '当前任务没有可用的打卡点，无法正式执行。', lvl='error')
        if str(task.get('runFaceStatus', '')).upper() == 'Y':
            add('rules', '当前任务要求人脸核验，请使用官方 App 完成。', lvl='error')
        start, end = values.get('start'), values.get('end')
        if start and end:
            now = datetime.now(CHINA).strftime('%H:%M')
            head = lambda value: value[:5]
            allowed = head(start) <= now <= head(end) if head(start) <= head(end) else now >= head(start) or now <= head(end)
            if not allowed:
                add('rules', f'当前不在可跑时间 {start}～{end} 内。')
    return issues
