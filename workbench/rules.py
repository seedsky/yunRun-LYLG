"""任务规则适配。服务器值、本地配置和未知字段保持明确的来源区别。"""
from datetime import datetime, timedelta, timezone
import math
from .storage import read_json

CHINA = timezone(timedelta(hours=8))


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


def view(store):
    import taskgen
    cache = cached(store)
    task = selected(store) or {}
    def value(key, fallback=None):
        raw = task.get(key)
        return {'value': raw if raw is not None else fallback,
                'source': '服务器' if raw is not None else ('本地配置' if fallback is not None else '未确认')}
    return {'fetched_at': cache.get('fetched_at'), 'selected_id': str(task.get('id', '')),
        'tasks': [{'id': str(t.get('id')), 'name': str(t.get('raName') or t.get('raRunName') or '校园跑步任务'),
                   'type': t.get('raType', '')} for t in cache.get('tasks', [])],
        'fields': {'km_min': value('raSingleMileageMin', 2), 'km_max': value('raSingleMileageMax', 5),
                   'pace_min': value('raPaceMin', 3), 'pace_max': value('raPaceMax', 10),
                   'cadence_min': value('raCadenceMin', 50), 'cadence_max': value('raCadenceMax', 250),
                   'start': value('dayStartTime'), 'end': value('dayEndTime'),
                   'face': value('runFaceStatus'), 'batch': value('passPointNum'),
                   'max_minutes': {'value': store.settings()['max_minutes'], 'source': '本地配置'}},
        'points': [list(map(float, s.split(','))) for s in str(task.get('points') or '').split('|') if s.strip()] or list(taskgen.CARDS),
        'points_source': '服务器' if task.get('points') else '本地示例',
        'minimum_cards': task.get('raMinDislikes'), 'declared_cards': task.get('raDislikes')}


def validate(store, metrics, formal=False):
    values = view(store)['fields']
    errors = []
    for field, lo, hi, label, unit in [('km','km_min','km_max','里程','公里'),
            ('pace','pace_min','pace_max','平均配速','分钟/公里'),
            ('cadence','cadence_min','cadence_max','步频','步/分')]:
        number = float(metrics[field])
        lower, upper = float(values[lo]['value']), float(values[hi]['value'])
        if not math.isfinite(number) or not lower - 0.015 <= number <= upper + 0.015:
            suggestion = min(upper, max(lower, number)) if math.isfinite(number) else lower
            errors.append({'field': field, 'message': f'{label}需在 {lower:g}～{upper:g} {unit}之间。', 'suggested': suggestion})
    max_seconds = float(values['max_minutes']['value']) * 60
    if float(metrics['duration']) > max_seconds + 1:
        errors.append({'field':'pace', 'message': f'预计时长超过本地配置的 {max_seconds/60:g} 分钟上限。',
                       'suggested': max_seconds/60/float(metrics['km'])})
    if formal:
        task = selected(store, True)
        for key in ('raSingleMileageMin','raSingleMileageMax','raCadenceMin','raCadenceMax','points'):
            if task.get(key) is None: errors.append({'field':'rules','message':f'服务器缺少必要字段 {key}，无法正式执行。'})
        if str(task.get('runFaceStatus', '')).upper() == 'Y':
            errors.append({'field':'rules','message':'当前任务要求人脸核验，请使用官方 App 完成。'})
        start, end = str(task.get('dayStartTime') or '')[:5], str(task.get('dayEndTime') or '')[:5]
        if start and end:
            now = datetime.now(CHINA).strftime('%H:%M')
            allowed = start <= now <= end if start <= end else now >= start or now <= end
            if not allowed: errors.append({'field':'rules','message':f'当前不在服务器可跑时间 {start}～{end} 内。'})
    return errors
