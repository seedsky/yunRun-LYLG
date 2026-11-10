"""薄 HTTP 适配层。页面不能提交命令、任意路径或凭据读取请求。"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import secrets
import threading
from urllib.parse import urlsplit
import uuid
from .storage import ROOT, Store, read_json, write_json
from .auth import Auth
from .routes import Routes, RouteConflict
from .jobs import Jobs, ProcessFamily
from .backup import Backups
from .capture import Capture
from .records import Records
from . import rules


class Application:
    def __init__(self,store=None,demo=False):
        self.store=store or Store();self.demo=demo;self.key=secrets.token_urlsafe(32)
        self.auth=Auth(self.store);self.routes=Routes(self.store);self.family=ProcessFamily()
        self.jobs=Jobs(self.store,self.auth,self.routes,self.family)
        self.backups=Backups(self.store,self.routes);self.capture=Capture(self.store,self.auth,self.family)
        self.records=Records(self.store,self.auth,demo)
        self.mutation_lock=threading.RLock()
        if demo:
            import taskgen
            write_json(self.store.data/'rules.json',{'fetched_at':'2026-11-10T00:00:00+08:00',
              'tasks':[{'id':619,'schoolId':197,'raType':'T3','raName':'校园跑步 · 本地演示',
                'raRunArea':'offline:demo','raSingleMileageMin':2,'raSingleMileageMax':5,
                'raPaceMin':3,'raPaceMax':10,'raCadenceMin':35,'raCadenceMax':350,
                'dayStartTime':'06:00','dayEndTime':'22:00','raDislikes':5,'raMinDislikes':3,
                'points':'|'.join(f'{x},{y}' for x,y in taskgen.CARDS)}]})
            self.store.save_settings({'task_id':'619'})
            self.auth.authenticated=True;self.auth.restore_attempted=True

    def state(self,mode):
        if mode=='online' and not self.demo:self.auth.restore()
        settings=self.store.settings();name=settings.get('account_name','')
        job=self.jobs.status()
        preview_id=(job.get('preview_id') if self.jobs.active else read_json(self.store.data/'last-preview.json',{}).get('id')) or job.get('preview_id')
        preview=None
        if preview_id:
            try:preview=self.routes.public(self.routes.get(preview_id))
            except ValueError:pass
        visible_rules=rules.view(self.store)
        if self.demo:
            for field in visible_rules['fields'].values():
                if field['source']=='服务器':field['source']='演示数据'
            visible_rules['points_source']='演示数据'
        credentials=self.store.credentials()
        return {'mode':mode,'demo':self.demo,'settings':settings,'authenticated':self.auth.authenticated,
                'session':{'has_token':bool(credentials.get('token')),'has_password':bool(credentials.get('password')),
                           'source':credentials.get('session_source','existing')},
                'account':(name[:1]+'***'+name[-2:]) if name else ('演示账号' if self.demo else '抓包登录'),
                'auth_error':self.auth.error,'rules':visible_rules,
                'sources':self.routes.public_sources(),'job':job,'preview':preview}

    def network(self,mode):
        if mode=='offline':raise ValueError('独立工具不连接学校服务，请打开联网面板。')
        if self.demo:raise ValueError('演示模式禁止连接学校服务，请使用正常启动入口。')

    def action(self,path,data,mode):
        with self.mutation_lock:
            if path=='/api/settings':
                if self.jobs.active and set(data)-{'theme'}:self.jobs.guard()
                return self.store.save_settings(data)
            if path in ('/api/records/query','/api/records/detail'):
                if mode=='offline':raise ValueError('独立工具不查询线上记录，请打开联网面板。')
                return self.records.query(data.get('term')) if path.endswith('query') else self.records.detail(data.get('id'),data.get('table'))
            self.jobs.guard() if path not in ('/api/jobs/stop',) else None
            if path=='/api/auth/login':
                self.network(mode);self.auth.login(data.get('username',''),data.get('password',''))
                try:self.auth.refresh();warning=''
                except Exception as exc:warning=self.store.safe_message(exc)
                return {'authenticated':True,'warning':warning}
            if path=='/api/auth/retry':
                self.network(mode);self.auth.restore_attempted=False;self.auth.relogin_attempt_token=None;self.auth.restore()
                if not self.auth.authenticated:raise ValueError(self.auth.error or '自动登录失败。')
                return {'authenticated':True}
            if path=='/api/auth/logout':self.auth.logout();return {'ok':True}
            if path=='/api/rules/refresh':self.network(mode);self.auth.refresh();return rules.view(self.store)
            if path=='/api/tasks/select':
                task_id=str(data.get('id',''))
                if not any(str(t.get('id'))==task_id for t in rules.cached(self.store)['tasks']):raise ValueError('任务已失效，请重新获取。')
                self.store.save_settings({'task_id':task_id});return rules.view(self.store)
            if path=='/api/routes/generate':
                result=self.routes.generate(data)
                safe={k:v for k,v in data.items() if k in ('source','km','pace','cadence','amp','sample_seconds','seed','deform')}
                self.store.save_settings({'defaults':safe})
                return result
            if path=='/api/routes/preview':return self.routes.preview(data.get('source_id'),data.get('uploaded'),data.get('params'))
            if path=='/api/routes/save':return self.routes.save(data.get('id'),data.get('name'))
            if path=='/api/routes/import':return self.routes.import_route(data.get('content'),data.get('name','导入路线'))
            if path=='/api/routes/export':return self.routes.export(data.get('source_id'),bool(data.get('geojson')))
            if path=='/api/previews/export':
                item=self.routes.get(data.get('id'))
                if data.get('geojson'):
                    from .routes import coordinates
                    return {'type':'LineString','coordinates':coordinates(item['task'])}
                return {'version':1,'name':'临时路线','task':item['task'],'params':item['params']}
            if path=='/api/routes/delete':
                match=next((s for s in self.routes.sources() if s['id']==data.get('id') and s['kind']!='sample'),None)
                if not match:raise ValueError('只能删除个人路线或已下载的历史路线。')
                trash=self.store.data/'trash';trash.mkdir(exist_ok=True)
                match['path'].replace(trash/(uuid.uuid4().hex+'.json'));return {'ok':True}
            if path=='/api/jobs/start':
                if self.demo and not data.get('dry',True):raise ValueError('演示模式只允许本地演练。')
                return self.jobs.start(data.get('id'),bool(data.get('dry',True)),bool(data.get('fast',False)),mode)
            if path=='/api/jobs/stop':return self.jobs.stop()
            if path=='/api/backup/export':return self.backups.export()
            if path=='/api/backup/inspect':return self.backups.inspect(data.get('content'))
            if path=='/api/backup/import':
                result=self.backups.restore(data.get('id'),data.get('choices') or {})
                if result.get('account_imported'):
                    self.auth.authenticated=False;self.auth.restore_attempted=False
                return result
            if path=='/api/advanced/save':
                cfg=self.store.config();cleaned={}
                for section,values in data.items():
                    if section not in ('Yun','User','Run','Panel') or not isinstance(values,dict):continue
                    for key,value in values.items():
                        if not cfg.has_option(section,key) or key.lower() in ('token','password','username'):continue
                        if str(value)=='__KEEP__':continue
                        cleaned.setdefault(section,{})[key]=str(value)
                previous=read_json(self.store.data/'advanced.json',{})
                for section,values in cleaned.items():previous.setdefault(section,{}).update(values)
                write_json(self.store.data/'advanced.json',previous)
                return {'ok':True}
            if path=='/api/capture/start':self.network(mode);return self.capture.start()
            if path=='/api/capture/stop':return self.capture.stop()
            if path=='/api/capture/complete':
                self.network(mode)
                return self.auth.accept_capture()
            if path=='/api/history/pull':return self.history(data,mode)
            raise ValueError('未知操作。')

    def history(self,data,mode):
        self.network(mode)
        if not self.auth.authenticated:raise ValueError('请先登录。')
        client=self.auth.client();limit=max(1,min(200,int(data.get('limit',20))))
        terms=client.post_json('/run/listXnYearXqByStudentId','',raise_on_business_code=True).get('data') or []
        total=0
        for term in terms:
            table=term.get('value')
            response=client.post_json('/run/crsReocordInfoList',json.dumps({'tableName':table}),raise_on_business_code=True)
            for month in (response.get('data') or {}).get('rank') or []:
                for run in month.get('rankList') or []:
                    detail=client.post_json('/run/crsReocordInfo',json.dumps({'id':run.get('id'),'tableName':table}),raise_on_business_code=True)
                    from .routes import label
                    name=label('历史 '+str((detail.get('data') or {}).get('recordStartTime') or run.get('id')))
                    # 规范化后再存盘，避免把详情中的个人资料写进路线库与备份。
                    self.routes.import_route(detail,name,'history');total+=1
                    if total>=limit:return {'count':total}
        return {'count':total}


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,status,body,mime='application/json; charset=utf-8'):
            self.send_response(status);self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://*.is.autonavi.com; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            try:self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):pass
        def result(self,value,status=200):self.send(status,json.dumps(value,ensure_ascii=False,allow_nan=False).encode())
        def authorized(self):
            host=self.headers.get('Host','').split(':')[0]
            if host not in ('127.0.0.1','localhost'):return False
            origin=self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'):return False
            return secrets.compare_digest(self.headers.get('X-Workbench-Token',''),app.key)
        def mode(self):return 'offline' if self.headers.get('X-Workbench-Mode')=='offline' else 'online'
        def do_GET(self):
            path=urlsplit(self.path).path
            try:
                if path in ('/','/offline','/capture'):
                    if self.headers.get('Host','').split(':')[0] not in ('127.0.0.1','localhost'):
                        return self.result({'error':'本地面板仅允许本机访问。'},403)
                    mode='offline' if path=='/offline' else 'online'
                    html=(ROOT/'static/index.html').read_text(encoding='utf-8').replace('__BOOTSTRAP__',json.dumps({'token':app.key,'mode':mode,'demo':app.demo,'capture':path=='/capture'}))
                    return self.send(200,html.encode(),'text/html; charset=utf-8')
                if path.startswith('/static/'):
                    target=(ROOT/path.lstrip('/')).resolve()
                    if not target.is_relative_to(ROOT/'static') or not target.is_file():return self.result({'error':'资源不存在。'},404)
                    return self.send(200,target.read_bytes(),mimetypes.guess_type(str(target))[0] or 'application/octet-stream')
                if not self.authorized():return self.result({'error':'页面验证已失效，请刷新页面。'},403)
                if path=='/api/state':return self.result(app.state(self.mode()))
                if path=='/api/jobs':return self.result(app.jobs.status())
                if path=='/api/capture':
                    app.network(self.mode());return self.result(app.capture.status())
                if path=='/api/advanced':
                    cp=app.store.config();out={}
                    private={'publickey','privatekey','cipherkey','cipherkeyencrypted','md5key'}
                    for sec in cp.sections():
                        if sec=='Login':continue
                        out[sec]={k:{'value':'' if k.lower() in private else v,'hidden':k.lower() in private,'configured':bool(v)}
                                  for k,v in cp.items(sec) if k.lower() not in ('token','password','username')}
                    return self.result(out)
                return self.result({'error':'接口不存在。'},404)
            except Exception as exc:return self.result({'error':app.store.safe_message(exc)},400)
        def do_POST(self):
            if not self.authorized():
                # 拒绝前仅排空小请求体，避免 Windows 因未读正文重置连接，导致浏览器收不到 403。
                # 大体积或迟迟不发送正文的请求不会无限占用线程，也不解析未授权数据。
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if 0<length<=65536:
                        self.connection.settimeout(1);self.rfile.read(length)
                except (ValueError,OSError):pass
                return self.result({'error':'页面验证失败，请刷新页面。'},403)
            try:
                length=int(self.headers.get('Content-Length','0'))
                if length<0 or length>50*1024*1024:raise ValueError('文件或请求超过 50 MB 限制。')
                body=json.loads(self.rfile.read(length) or b'{}')
                if not isinstance(body,dict):raise ValueError('请求格式不正确。')
                return self.result(app.action(urlsplit(self.path).path,body,self.mode()))
            except RouteConflict as exc:return self.result({'error':str(exc),'issues':exc.issues},422)
            except Exception as exc:return self.result({'error':app.store.safe_message(exc)},400)
    return Handler
