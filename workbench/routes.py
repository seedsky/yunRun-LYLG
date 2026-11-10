"""唯一的路线服务：生成、仅预览、保存、导入均输出同一种不可变快照。

执行只接受 preview_id，不接收浏览器提供的命令或任意文件路径。
本模块没有网络请求；独立工具与联网工作台完全共用此实现。
"""
from __future__ import annotations
import bisect
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import random
import threading
import uuid
import taskgen
import yun_route
from .storage import ROOT, read_json, write_json
from . import rules


class RouteConflict(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__('；'.join(issue['message'] for issue in issues))


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def label(value):
    return ''.join(c for c in str(value) if c.isprintable()).strip()[:100] or '未命名路线'


def coordinates(obj):
    """支持任务表、工作台路线及 GeoJSON；拒绝非法经纬度与过大的输入。"""
    if isinstance(obj, dict) and 'task' in obj: obj = obj['task']
    if isinstance(obj, dict) and obj.get('type') == 'FeatureCollection':
        obj = next((f.get('geometry') for f in obj.get('features', [])
                    if (f.get('geometry') or {}).get('type') == 'LineString'), {})
    if isinstance(obj, dict) and obj.get('type') == 'Feature': obj = obj.get('geometry') or {}
    if isinstance(obj, dict) and obj.get('type') == 'LineString': raw = obj.get('coordinates') or []
    elif isinstance(obj, dict):
        data = obj.get('data') or obj
        raw = data.get('pointsList') or data.get('points') or []
    elif isinstance(obj, list): raw = obj
    else: raw = []
    if not 2 <= len(raw) <= 50000: raise ValueError('路线需要 2～50000 个坐标点。')
    result = []
    for row in raw:
        point = row.get('point') if isinstance(row, dict) else row
        pair = point.split(',') if isinstance(point, str) else point
        x, y = float(pair[0]), float(pair[1])
        if not (math.isfinite(x) and math.isfinite(y) and -180 <= x <= 180 and -90 <= y <= 90):
            raise ValueError('路线包含非法经纬度。')
        result.append((x, y))
    return result


class Routes:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.previews = {}
        for name in ('routes', 'history', 'previews'): (store.data / name).mkdir(exist_ok=True)

    def sources(self):
        out = []
        for kind, folder, pattern in [('sample', ROOT/'tasks_lylg', '*.json'),
                                      ('library', self.store.data/'routes', '*.json'),
                                      ('history', self.store.data/'history', '*.json')]:
            for path in sorted(folder.glob(pattern)):
                try:
                    obj = read_json(path)
                    points = coordinates(obj)
                    name = obj.get('name') or path.stem
                    out.append({'id': kind+'/'+path.stem, 'name': label(name), 'kind': kind,
                                'points': len(points), 'path': path})
                except (ValueError, TypeError, KeyError): continue
        return out

    def public_sources(self):
        return [{k:v for k,v in item.items() if k != 'path'} for item in self.sources()]

    def source(self, source_id):
        match = next((item for item in self.sources() if item['id'] == source_id), None)
        if not match: raise ValueError('所选路线不存在，请刷新路线列表。')
        return read_json(match['path']), match['name']

    @contextmanager
    def geometry(self):
        """旧几何模块使用全局常量；串行设置并恢复，防止两个页面串用任务点。"""
        with self.lock:
            old_cards, old_fence = taskgen.CARDS, taskgen.FENCE
            latest = rules.view(self.store)['points']
            taskgen.CARDS = [tuple(p) for p in latest] or list(old_cards)
            fence = read_json(ROOT/'examples/routes/fence_lylg.geojson')
            geom = fence.get('geometry') or fence
            if fence.get('type') == 'FeatureCollection': geom = fence['features'][0]['geometry']
            taskgen.FENCE = [tuple(p) for p in geom['coordinates'][0]]
            if taskgen.FENCE[-1] == taskgen.FENCE[0]: taskgen.FENCE = taskgen.FENCE[:-1]
            try: yield
            finally: taskgen.CARDS, taskgen.FENCE = old_cards, old_fence

    def normalize(self, obj, pace=5.5, cadence=170):
        """保留原有坐标/时间轴，剔除记录 ID、学生资料等与路线无关的身份字段。"""
        if isinstance(obj, dict) and 'task' in obj: obj = obj['task']
        declared=(obj.get('metadata') or {}).get('coordinate_system') or obj.get('coordinate_system') if isinstance(obj,dict) else None
        if declared and declared!='GCJ-02':
            raise ValueError('本工具使用 GCJ-02；该文件声明为其他坐标系，未自动转换。')
        pts = coordinates(obj)
        data = obj.get('data', obj) if isinstance(obj, dict) else {}
        rows = data.get('pointsList') or []
        total = taskgen.plen(pts)
        if total <= 0: raise ValueError('路线长度必须大于零。')
        has_timeline = len(rows) == len(pts) and all('runTime' in row and 'runMileage' in row for row in rows)
        if not has_timeline:
            return taskgen.to_task(pts, total, pace, cadence, taskgen.visited_cards(pts), random.Random(0)), True
        clean, last_m, last_t, last_s = [], -1.0, -1, -1
        for point, row in zip(pts, rows):
            m, t, steps = float(row['runMileage']), int(row['runTime']), int(row.get('runStep', 0))
            speed = float(row.get('speed') or pace)
            if not all(math.isfinite(v) for v in (m, t, steps, speed)) or m < last_m or t < last_t or steps < last_s or min(m,t,steps) < 0:
                raise ValueError('路线的时间、里程或累计步数非法或回退。')
            clean.append({'id':0, 'point':f'{point[0]:.14f},{point[1]:.14f}', 'speed':str(speed),
                'runStatus':1, 'runRecordId':0, 'runTime':str(t), 'runMileage':str(m),
                'runStep':str(steps), 'isFence':'Y'})
            last_m,last_t,last_s = m,t,steps
        if last_t <= 0 or last_m <= 0: raise ValueError('路线缺少有效总时长或累计里程。')
        manage = [{'point':f'{c[0]:.6f},{c[1]:.6f}', 'marked':'Y','index':i}
                  for i,c in enumerate(taskgen.visited_cards(pts))]
        return {'code':200,'msg':'本地路线','data':{'pointsList':clean,
            'recordMileage':last_m/1000, 'duration':last_t, 'recodePace':last_t/60/(last_m/1000),
            'recodeCadence':last_s*60/last_t, 'manageList':manage,'recodeDislikes':len(manage),'schoolId':197}}, False

    def metrics(self, task):
        d = task['data']
        return {'km':float(d['recordMileage']), 'pace':float(d['recodePace']),
                'cadence':float(d['recodeCadence']), 'duration':int(d['duration']),
                'points':len(d['pointsList']), 'cards':len(d['manageList'])}

    def _sample(self, task, interval):
        rows = task['data']['pointsList']
        times = [int(p['runTime']) for p in rows]
        duration = times[-1]
        coords = coordinates(task)
        sampled = []
        for moment in list(range(0, duration, interval)) + [duration]:
            index = max(0, min(len(rows)-2, bisect.bisect_right(times, moment)-1))
            left, right = rows[index], rows[index+1]
            fraction = (moment-times[index])/max(1,times[index+1]-times[index])
            fraction = min(1,max(0,fraction))
            point = [coords[index][n]+(coords[index+1][n]-coords[index][n])*fraction for n in (0,1)]
            value = dict(left, point=f'{point[0]:.14f},{point[1]:.14f}', runTime=str(moment))
            for key in ('runMileage','runStep','speed'):
                number=float(left[key])+(float(right[key])-float(left[key]))*fraction
                value[key]=str(round(number) if key=='runStep' else round(number,6))
            sampled.append(value)
        task['data']['pointsList']=sampled
        return task

    def generate(self, params):
        with self.geometry():
            km, pace, cadence = (float(params.get(k, default)) for k,default in [('km',2),('pace',5.5),('cadence',170)])
            if not all(math.isfinite(n) and n>0 for n in (km,pace,cadence)): raise ValueError('里程、配速和步频必须为有效正数。')
            errors=rules.validate(self.store, {'km':km,'pace':pace,'cadence':cadence,'duration':km*pace*60})
            if errors: raise RouteConflict(errors)
            amp=float(params.get('amp',1))
            interval=int(params.get('sample_seconds',2))
            if not 0<=amp<=2 or not 1<=interval<=5: raise ValueError('起伏强度应为 0～2，点间隔应为 1～5 秒。')
            fixed_seed=str(params.get('seed','')).strip()
            seed=int(fixed_seed) if fixed_seed else random.SystemRandom().randrange(1,2**31)
            source=params.get('source','synth')
            if source=='synth':
                for attempt in range(12 if not fixed_seed else 1):
                    try:
                        pts,total,_,order=taskgen.synth(km*1000,pace,cadence,seed+attempt,cards_min=len(taskgen.CARDS))
                        seed+=attempt
                        break
                    except taskgen.TaskgenError:
                        if attempt==(11 if not fixed_seed else 0): raise
            else:
                if source=='draw':
                    pts=coordinates(params.get('drawn') or [])
                elif source in ('library','history'):
                    obj,_=self.source(params.get('source_id'))
                    pts=coordinates(obj)
                    if params.get('deform'): pts=yun_route.deform_geometry(pts,seed=seed)
                else: raise ValueError('未知路线来源。')
                if any(not taskgen.inside(p,taskgen.FENCE) for p in pts):
                    raise ValueError('路线包含围栏外的点，请修改手绘路线或关闭来源形变；没有自动移动坐标。')
                pts=taskgen.densify(taskgen.snap_cards(pts))
                if taskgen.plen(pts)<km*1000:
                    base=list(pts)
                    for i in range(40):
                        pts+=list(reversed(base))[1:] if i%2==0 else base[1:]
                        if taskgen.plen(pts)>=km*1000: break
                pts,total=taskgen.trim_at(pts,km*1000)
                if total<km*1000-1: raise ValueError('来源路线过短，请增加手绘长度或减小目标里程。')
                order=taskgen.visited_cards(pts)
                need=int((rules.selected(self.store) or {}).get('raMinDislikes') or 3)
                if len(order)<need: raise ValueError(f'当前长度仅经过 {len(order)} 个打卡点，需至少 {need} 个；请调整路线，未自动增加里程。')
            task=taskgen.to_task(pts,total,pace,cadence,order,random.Random(seed),amp=amp,cad_lo=cadence,cad_hi=cadence)
            task=self._sample(task,interval)
            return self._preview(task,dict(params,seed=seed),False)

    def preview(self, source_id=None, uploaded=None, params=None):
        with self.geometry():
            obj,name=(uploaded,'导入路线') if uploaded is not None else self.source(source_id)
            params=params or {}
            task,estimated=self.normalize(obj,float(params.get('pace',5.5)),float(params.get('cadence',170)))
            return self._preview(task,dict(params,source_id=source_id or '',name=name),estimated)

    def _preview(self, task, params, estimated):
        # 管理表 index 指向最终采样后的轨迹行，不能沿用打卡点的序号。
        pts=coordinates(task)
        manage=[]
        for card in taskgen.CARDS:
            index=min(range(len(pts)),key=lambda n:taskgen.hav(pts[n],card))
            if taskgen.hav(pts[index],card)<=20:
                manage.append({'point':f'{card[0]:.14f},{card[1]:.14f}','marked':'Y','index':index})
        task['data']['manageList']=manage
        task['data']['recodeDislikes']=len(manage)
        task.setdefault('metadata',{})['coordinate_system']='GCJ-02'
        preview_id=uuid.uuid4().hex
        item={'id':preview_id,'task':task,'params':params,'task_id':rules.view(self.store)['selected_id'],
              'reference_cards':list(taskgen.CARDS),
              'digest':digest(task),'created_at':datetime.now().isoformat(),'estimated':estimated}
        self.previews[preview_id]=item
        while len(self.previews)>24: self.previews.pop(next(iter(self.previews)))
        write_json(self.store.data/'previews'/f'{preview_id}.json',item)
        write_json(self.store.data/'last-preview.json', {'id': preview_id})
        return self.public(item)

    def public(self, item):
        task=item['task'];d=task['data']; points=coordinates(task)
        with self.geometry():
            fence, cards=list(taskgen.FENCE),list(taskgen.CARDS)
        return {'id':item['id'],'digest':item['digest'],'task_id':item['task_id'],'metrics':self.metrics(task),
            'coordinates':points,'paces':[float(p.get('speed') or 0) for p in d['pointsList']],
            'times':[int(p['runTime']) for p in d['pointsList']], 'params':item['params'],'estimated':item['estimated'],
            'issues':rules.validate(self.store,self.metrics(task)),
            'fence':fence,'cards':cards}

    def get(self, preview_id):
        with self.lock:
            if not isinstance(preview_id,str) or not len(preview_id)==32 or any(c not in '0123456789abcdef' for c in preview_id):
                raise ValueError('预览标识不正确。')
            obj=self.previews.get(preview_id) or read_json(self.store.data/'previews'/f'{preview_id}.json')
            if not obj or digest(obj['task'])!=obj['digest']: raise ValueError('预览已失效或被修改，请重新预览。')
            return deepcopy(obj)

    def save(self, preview_id, name):
        with self.lock:
            item=self.get(preview_id)
            name=label(name or ('路线 '+datetime.now().strftime('%m-%d %H:%M')))
            names={s['name'] for s in self.sources() if s['kind']=='library'}
            original=name; index=2
            while name in names: name=f'{original} ({index})'; index+=1
            route_id=uuid.uuid4().hex
            write_json(self.store.data/'routes'/f'{route_id}.json',
                       {'version':1,'name':name,'task':item['task'],'params':item['params']})
            return {'id':'library/'+route_id,'name':name}

    def import_route(self, obj, name, kind='library'):
        with self.geometry():
            task,_=self.normalize(obj)
            folder='routes' if kind=='library' else 'history'
            route_id=uuid.uuid4().hex
            write_json(self.store.data/folder/f'{route_id}.json',{'version':1,'name':label(name),'task':task})
            return {'id':('library' if folder=='routes' else 'history')+'/'+route_id,'name':label(name)}

    def export(self, source_id, geojson=False):
        obj,name=self.source(source_id)
        if geojson:
            return {'type':'FeatureCollection','features':[{'type':'Feature','properties':{'name':name},
                'geometry':{'type':'LineString','coordinates':coordinates(obj)}}]}
        return obj

