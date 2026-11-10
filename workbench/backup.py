"""显式白名单备份：账号/token/设备、偏好、路线；永不导出密码或 CA 私钥。"""
from copy import deepcopy
from datetime import datetime, timezone
import uuid
from pathlib import Path
import tempfile
from .storage import read_json, write_json, atomic_bytes
from .routes import coordinates, label


class Backups:
    def __init__(self,store,routes): self.store,self.routes,self.pending=store,routes,{}

    def export(self):
        cred=self.store.credentials()
        account={k:cred[k] for k in ('username','token','device_id','device_name','sys_version') if k in cred}
        items=[]
        for item in self.routes.sources():
            if item['kind'] not in ('library','history'): continue
            body=read_json(item['path'])
            # 仅导出规范化任务，旧导入文件的多余身份字段不能随包流出。
            with self.routes.geometry(): task,_=self.routes.normalize(body)
            items.append({'name':item['name'],'kind':item['kind'],'task':task})
        return {'format':'yunrun-backup','version':1,'created_at':datetime.now(timezone.utc).isoformat(),
                'account':account,'settings':self.store.settings(),'advanced':read_json(self.store.data/'advanced.json',{}),
                'routes':items}

    def inspect(self,obj):
        if not isinstance(obj,dict) or obj.get('format')!='yunrun-backup' or obj.get('version')!=1:
            raise ValueError('不是受支持的工作台备份文件。')
        if not isinstance(obj.get('routes',[]),list) or len(obj.get('routes',[]))>1000:
            raise ValueError('备份路线数量过大或格式不正确。')
        existing={s['name']:s['id'] for s in self.routes.sources() if s['kind'] in ('library','history')}
        for item in obj.get('routes',[]): coordinates(item.get('task'))
        key=uuid.uuid4().hex
        self.pending[key]=deepcopy(obj)
        while len(self.pending)>3: self.pending.pop(next(iter(self.pending)))
        username=str((obj.get('account') or {}).get('username',''))
        return {'id':key,'account':(username[:1]+'***'+username[-2:]) if username else '未包含账号',
                'has_token':bool((obj.get('account') or {}).get('token')),
                'routes':[{'index':i,'name':label(v.get('name')),'conflict':label(v.get('name')) in existing}
                          for i,v in enumerate(obj.get('routes',[]))]}

    def restore(self,key,choices):
        # 用户可选择覆盖；任何写入中途失败时恢复整次导入前状态。
        data=self.store.data.resolve()
        def affected():
            paths=[data/name for name in ('credentials.dpapi','settings.json','advanced.json','rules.json','records.json')]
            for name in ('routes','history'):paths.extend((data/name).glob('*.json'))
            return paths
        with tempfile.TemporaryDirectory(prefix='.restore-',dir=data) as temporary:
            staging=Path(temporary).resolve()
            assert staging.is_relative_to(data)
            previous={}
            for path in affected():
                if path.is_file():
                    relative=path.relative_to(data)
                    target=staging/relative;target.parent.mkdir(parents=True,exist_ok=True)
                    target.write_bytes(path.read_bytes());previous[relative]=target
            try:return self._restore_apply(key,choices)
            except Exception:
                for path in affected():
                    assert path.resolve().is_relative_to(data)
                    path.unlink(missing_ok=True)
                for relative,saved in previous.items():atomic_bytes(data/relative,saved.read_bytes())
                raise

    def _restore_apply(self,key,choices):
        obj=self.pending.get(key)
        if not obj: raise ValueError('导入预览已过期，请重新选择备份文件。')
        # 所有内容先验证，再写入；凭据不从旧账号继承 password。
        imported=[]
        for index,item in enumerate(obj.get('routes',[])):
            if not choices.get('routes',True): break
            action=choices.get('conflicts',{}).get(str(index),'keep')
            if action not in ('keep','overwrite','skip'): raise ValueError('未知的重名处理方式。')
            if action=='skip': continue
            with self.routes.geometry(): task,_=self.routes.normalize(item['task'])
            imported.append((item,action,task))
        if choices.get('settings',True):
            settings=obj.get('settings') or {}
            self.store.save_settings(settings)
            advanced=obj.get('advanced') or {}
            allowed=self.store.config()
            cleaned={sec:{k:str(v) for k,v in values.items() if allowed.has_option(sec,k) and k.lower() not in ('password','token','username')}
                     for sec,values in advanced.items() if sec in ('Yun','User','Run','Panel') and isinstance(values,dict)}
            write_json(self.store.data/'advanced.json',cleaned)
        if choices.get('account',True) and obj.get('account'):
            cred={k:str(v) for k,v in obj['account'].items() if k in ('username','token','device_id','device_name','sys_version')}
            self.store.clear_credentials()
            self.store.save_credentials(cred)
            self.store.save_settings({'account_name':cred.get('username','')})
            if not choices.get('settings',True):self.store.save_settings({'task_id':''})
        count=0
        for item,action,task in imported:
            name=label(item.get('name'))
            matches=[s for s in self.routes.sources() if s['kind'] in ('library','history') and s['name']==name]
            if matches and action=='overwrite':
                write_json(matches[0]['path'],{'version':1,'name':name,'task':task})
            else:
                original=name; suffix=2; names={s['name'] for s in self.routes.sources()}
                while name in names: name=f'{original} ({suffix})'; suffix+=1
                self.routes.import_route(task,name,item.get('kind','library'))
            count+=1
        self.pending.pop(key,None)
        return {'imported_routes':count,'account_imported':bool(choices.get('account',True) and obj.get('account'))}
