"""任务生命周期与子进程隔离。

前端断开不影响任务；控制台进程退出时，Windows Job Object 关闭并结束子进程。
持久化状态只保存确认后的进度，重启发现 running/stopping 时标记 interrupted。
"""
import atexit
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
from .storage import ROOT, read_json, write_json
from .routes import digest, coordinates, RouteConflict
from . import rules


class ProcessFamily:
    def __init__(self):
        self.handle=None
        if os.name!='nt': return
        class Limits(ctypes.Structure):
            _fields_=[('a',ctypes.c_longlong),('b',ctypes.c_longlong),('flags',wintypes.DWORD),
                      ('min',ctypes.c_size_t),('max',ctypes.c_size_t),('count',wintypes.DWORD),
                      ('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
        class Io(ctypes.Structure): _fields_=[(f'v{i}',ctypes.c_ulonglong) for i in range(6)]
        class Extended(ctypes.Structure):
            _fields_=[('limits',Limits),('io',Io)]+[(f'm{i}',ctypes.c_size_t) for i in range(4)]
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype=wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        self.handle=self.kernel.CreateJobObjectW(None,None)
        limits=Extended(); limits.limits.flags=0x2000
        if not self.handle or not self.kernel.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        atexit.register(self.close)

    def attach(self,proc):
        if self.handle and not self.kernel.AssignProcessToJobObject(self.handle,wintypes.HANDLE(int(proc._handle))):
            proc.terminate()
            raise RuntimeError('无法将执行进程绑定到控制台生命周期。')

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle=None


class Jobs:
    def __init__(self,store,auth,routes,family):
        self.store,self.auth,self.routes,self.family=store,auth,routes,family
        self.lock=threading.RLock(); self.proc=None
        self.path=store.data/'jobs'/'current.json'
        self.state=read_json(self.path,{'status':'idle','logs':[]})
        if self.state.get('status') in ('starting','running','stopping'):
            self.state['status']='interrupted'
            self.state.setdefault('logs',[]).append('上次任务已中断，不自动续跑；请查询线上记录核对。')
            self.persist()

    @property
    def active(self): return self.state.get('status') in ('starting','running','stopping')

    def guard(self):
        if self.active: raise ValueError('任务执行中，请先完成或停止当前任务。')

    def persist(self): write_json(self.path,self.state)

    def status(self):
        with self.lock: return json.loads(json.dumps(self.state))

    def start(self,preview_id,dry=True,fast=False,mode='online'):
        with self.lock:
            self.guard()
            if mode=='offline' and not dry: raise ValueError('独立工具只支持本地演练。')
            snapshot=self.routes.get(preview_id)
            if not dry:
                if not self.auth.authenticated: raise ValueError('请先完成登录。')
                self.auth.refresh()  # 必须成功取得最新规则，不能回退缓存启动。
                if snapshot['task_id']!=rules.view(self.store)['selected_id']:
                    raise ValueError('任务选择已变化，请按当前任务重新预览。')
                if snapshot.get('reference_cards') and digest(snapshot['reference_cards'])!=digest(rules.view(self.store)['points']):
                    raise ValueError('服务器打卡点已变化，请重新生成或预览当前路线。')
            metrics=self.routes.metrics(snapshot['task'])
            issues=rules.validate(self.store,metrics,formal=not dry)
            if issues: raise RouteConflict(issues)
            with self.routes.geometry():
                import taskgen
                pts=coordinates(snapshot['task'])
                if any(not taskgen.inside(p,taskgen.FENCE) for p in pts): raise ValueError('预览路线存在围栏外坐标，不能执行。')
                selected=rules.selected(self.store) or {}
                required=max(int(selected.get('raDislikes') or 0),int(selected.get('raMinDislikes') or 0))
                visited=taskgen.visited_cards(pts)
                if not dry and len(visited)<required:
                    raise ValueError(f'最新打卡点校验：命中 {len(visited)} 个，现有执行引擎要求 {required} 个，请重新生成。')
                cards='|'.join(f'{x},{y}' for x,y in taskgen.CARDS)
            home_task=rules.selected(self.store)
            if dry:
                home_task={'id':619,'schoolId':197,'raType':'T3','raRunArea':'offline:fixture',
                           'raDislikes':0,'raMinDislikes':0,'raSingleMileageMin':0.001,'raSingleMileageMax':100,
                           'raCadenceMin':1,'raCadenceMax':1000,'points':cards,'runFaceStatus':'N','passPointNum':60}
            job_id=uuid.uuid4().hex
            folder=self.store.data/'jobs'/job_id
            write_json(folder/'snapshot.json',dict(snapshot,home=home_task,dry=bool(dry),fast=bool(fast)))
            write_json(folder/'task_pick'/'tasklist_0.json',snapshot['task'])
            self.state={'id':job_id,'status':'starting','dry':bool(dry),'fast':bool(fast),
                        'duration':metrics['duration'],'elapsed':0,'confirmed_m':0,'record_id':None,
                        'preview_id':preview_id,'digest':snapshot['digest'],'logs':['本地演练准备中。' if dry else '最新规则校验通过，准备执行当前预览快照。'],
                        'started_at':datetime.now(timezone.utc).isoformat()}
            self.persist()
            env=os.environ.copy(); env['PYTHONIOENCODING']='utf-8'; env['YUNRUN_DATA_DIR']=str(self.store.data)
            argv=[sys.executable,'--worker',str(folder)] if getattr(sys,'frozen',False) else [sys.executable,str(ROOT/'web.py'),'--worker',str(folder)]
            try:
                self.proc=subprocess.Popen(argv,cwd=str(ROOT),env=env,stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                self.family.attach(self.proc)
            except Exception:
                self.state['status']='failed'
                self.state['logs'].append('执行进程未能启动，请检查运行环境；未自动重试。')
                self.persist()
                raise
            threading.Thread(target=self._read,args=(self.proc,),daemon=True).start()
            return self.status()

    def _read(self,proc):
        try:
            for line in proc.stdout:
                if not line.startswith('@@EVENT '): continue
                try: event=json.loads(line[8:])
                except ValueError: continue
                with self.lock:
                    message=event.pop('message',None)
                    self.state.update({k:v for k,v in event.items() if k in ('status','elapsed','confirmed_m','record_id','stage','submission','grade')})
                    if message:
                        message=self.store.safe_message(message)
                        self.state['logs'].append(message)
                        self.state.setdefault('log_entries',[]).append({'message':message,'level':event.get('level','info'),
                            'time':datetime.now(timezone.utc).isoformat()})
                        self.state['log_entries']=self.state['log_entries'][-500:]
                    self.state['logs']=self.state['logs'][-500:]
                    self.persist()
            code=proc.wait()
            with self.lock:
                if self.active:
                    self.state['status']='stopped' if self.state['status']=='stopping' else 'failed'
                    self.state['logs'].append(f'执行进程已退出（{code}）；结果未确认，请保留日志并核对线上记录。')
                    self.persist()
        finally:
            proc.stdout.close()

    def stop(self):
        with self.lock:
            if not self.active: return self.status()
            folder=self.store.data/'jobs'/self.state['id']
            (folder/'stop').touch()
            self.state['status']='stopping'
            self.state['logs'].append('停止请求已发出；正在进行的请求可能已经到达服务器，已发送数据不会自动撤销。')
            self.persist()
            threading.Thread(target=self._terminate_after_grace,args=(self.proc,),daemon=True).start()
            return self.status()

    def _terminate_after_grace(self,proc):
        try: proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            if proc.poll() is None: proc.terminate()


def run_worker(folder):
    """子进程只执行已经落盘且哈希匹配的一份任务；不重新生成几何。"""
    import contextlib
    from dataclasses import replace
    import main as engine
    import yun_http
    from .auth import Auth
    from .storage import Store
    store=Store(); folder=Path(folder).resolve()
    if folder.parent!=store.data/'jobs': raise ValueError('非法执行快照目录。')
    snapshot=read_json(folder/'snapshot.json')
    task=read_json(folder/'task_pick'/'tasklist_0.json')
    if digest(task)!=snapshot['digest']: raise ValueError('快照校验失败。')
    def emit(**event):
        sys.__stdout__.write('@@EVENT '+json.dumps(event,ensure_ascii=False)+'\n'); sys.__stdout__.flush()
    def check_stop():
        if (folder/'stop').exists(): raise InterruptedError('任务已按用户请求停止。')
    import io
    from .storage import atomic_bytes
    runtime_config=store.config()
    for section in ('User','Login'):
        if runtime_config.has_section(section):
            for key in ('username','password','token'):
                if runtime_config.has_option(section,key):runtime_config.set(section,key,'')
    for key in ('single_mileage_min_offset','single_mileage_max_offset','cadence_min_offset','cadence_max_offset'):
        runtime_config.set('Run',key,'0')
    config_text=io.StringIO();runtime_config.write(config_text)
    config_path=folder/'runtime.ini'
    atomic_bytes(config_path,config_text.getvalue().encode('utf-8'))
    engine.set_args(str(config_path))
    # 服务器下发的原始范围直接使用，不沿用旧配置中的收缩偏移。
    engine.single_mileage_min_offset=engine.single_mileage_max_offset=0
    engine.cadence_min_offset=engine.cadence_max_offset=0
    dry=snapshot['dry']
    started=time.monotonic(); finished=threading.Event()
    if dry:
        # 演练的强约束：即使将来误引入真实客户端，也不能建立外部连接。
        import socket
        def forbidden_connect(*args,**kwargs):
            raise RuntimeError('本地演练禁止建立网络连接。')
        socket.socket.connect=forbidden_connect
        socket.create_connection=forbidden_connect
        profile=replace(yun_http.profile_from_conf(store.config()),token='LOCAL-DRILL')
        home={'code':200,'data':{'cralist':[snapshot['home']]}}
        fake=yun_http.FakeTransport(engine.dry_run_responder(home),sm2box=profile.sm2box(True),
            fixed_pair=(profile.cipherkey_encrypted,profile.cipherkey),fallback_key_b64=profile.cipherkey)
        client=yun_http.YunClient(profile,base_url='https://offline.invalid',transport=fake,sleep=lambda s:check_stop())
    else:
        client=Auth(store).client()
        def wait(seconds):
            deadline=time.monotonic()+seconds
            while time.monotonic()<deadline:
                check_stop()
                if snapshot['fast']: return
                time.sleep(min(.2,max(0,deadline-time.monotonic())))
            check_stop()
        client.sleep=wait
    engine.my_sys_edition=client.profile.sys_version
    engine.my_sys_version=client.profile.sys_version
    original_transport=client.transport
    def transport(**kw):
        check_stop()
        return original_transport(**kw)
    client.transport=transport
    # 保留白名单判定字段；不再因抑制原始输出而丢失提交结果。
    from .records import grade
    original_json=client.post_json
    def post_json(router,*args,**kwargs):
        response=original_json(router,*args,**kwargs)
        if router.endswith('/run/isStandard'):
            verdict=grade(response.get('data') or {})
            verdict['message']=store.safe_message((response.get('data') or {}).get('msg') or (response.get('data') or {}).get('warnContent') or '')
            emit(grade=verdict,level='success' if verdict['state']=='passed' else 'warning',
                 message=('演练判定：' if dry else '服务器预检判定：')+verdict['label'])
        if router.endswith('/run/finish'):
            accepted=response.get('code')==200
            submit={'state':'simulated' if dry else ('accepted' if accepted else 'rejected'),
                    'code':response.get('code'),'message':store.safe_message(response.get('msg',''))}
            emit(submission=submit,level='success' if accepted else 'error',
                 message=('模拟结束请求' if dry else '结束提交')+('成功：' if accepted else '失败：')+submit['message'])
        return response
    client.post_json=post_json
    def tick():
        while not finished.wait(1): emit(elapsed=round(time.monotonic()-started,1))
    threading.Thread(target=tick,daemon=True).start()
    try:
        emit(status='running',message='本地演练已启动，所有业务请求使用假传输。' if dry else '执行已启动。')
        # 兼容引擎输出不直接进入日志，防止完整响应或账号身份意外回显。
        with open(os.devnull,'w') as null,contextlib.redirect_stdout(null),contextlib.redirect_stderr(null):
            yun=engine.Yun_For_New(client=client,home_info={'code':200,'data':{'cralist':[snapshot['home']]}})
            yun.validate_generated_route(task)
            check_stop(); yun.start()
            emit(stage='running',record_id=yun.crsRunRecordId,message='本地开始步骤已模拟。' if dry else '服务器已创建开始记录。')
            original_split=yun.split_by_points_map
            def split(points):
                check_stop(); original_split(points)
                emit(confirmed_m=float(points[-1]['runMileage']),message=f'已确认处理至 {float(points[-1]["runMileage"])/1000:.2f} 公里。')
            yun.split_by_points_map=split
            yun.do_by_points_map(str(folder/'task_pick'),random_choose=True,isDrift=False)
            check_stop(); yun.finish_by_points_map()
        emit(status='completed',elapsed=task['data']['duration'],confirmed_m=task['data']['recordMileage']*1000,
             level='success',message='本地演练通过；模拟判定不代表真实成绩。' if dry else '提交成功，服务器已受理；请查看成绩判定或查询跑步记录核对。')
    except InterruptedError:
        emit(status='stopped',level='warning',message='任务已停止；已发送的数据不会自动撤销。')
    except Exception as exc:
        emit(status='failed',level='error',message=store.safe_message(f'{type(exc).__name__}: {exc}；已停止，不自动重发。'))
    finally: finished.set()
