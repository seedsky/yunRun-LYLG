"""单账号会话：有效 token 优先；明确失效后至多自动登录一次。

复用实测可用的学校 /login/appLogin，避开不必要的学校目录依赖。
传输错误不视为 token 过期，也不自动重放密码请求。
"""
from dataclasses import replace
from datetime import datetime, timezone
import json
import secrets
import threading
from urllib.parse import urlsplit
import requests
from requests.adapters import HTTPAdapter
import yun_http
from .storage import read_json, write_json


class Auth:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.restore_attempted = False
        self.relogin_attempt_token = None
        self.authenticated = False
        self.error = ''

    def profile(self, credentials=None):
        cp = self.store.config()
        cred = credentials if credentials is not None else self.store.credentials()
        profile = yun_http.profile_from_conf(cp)
        return replace(profile, token=cred.get('token', ''),
                       device_id=cred.get('device_id') or profile.device_id or str(secrets.randbelow(9*10**15)+10**15),
                       device_name=cred.get('device_name') or profile.device_name or 'Android',
                       sys_version=cred.get('sys_version') or profile.sys_version or '14')

    def client(self, credentials=None):
        cp = self.store.config()
        base = cp.get('Yun', 'school_host').rstrip('/')
        parsed = urlsplit(base)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username:
            raise ValueError('学校服务地址必须是有效的 HTTPS 地址。')
        session = requests.Session()
        session.trust_env = False
        session.headers.clear()
        session.headers.update({'User-Agent': 'okhttp/4.9.1', 'Accept-Encoding': 'gzip'})
        session.mount('https://', HTTPAdapter(max_retries=0))
        def transport(url, data, headers, timeout):
            if urlsplit(url).netloc != parsed.netloc or urlsplit(url).scheme != 'https':
                raise ValueError('请求不允许跨学校服务器转发。')
            return session.post(url, data=data, headers=headers, timeout=timeout, allow_redirects=False)
        return yun_http.YunClient(self.profile(credentials), base_url=base, transport=transport, timeout=(8, 20))

    def login(self, username, password):
        with self.lock:
            self.restore_attempted = True
            self.authenticated = False
            if not str(username).strip() or not password: raise ValueError('请填写学号和密码。')
            previous = self.store.credentials()
            identity = {k: previous[k] for k in ('device_id', 'device_name', 'sys_version') if previous.get(k)}
            client = self.client(identity)
            cp = self.store.config()
            route = cp.get('Yun', 'school_login_url', fallback='appLogin')
            if '/' in route or not route: raise ValueError('登录入口配置不正确。')
            response = client.post_json('/login/'+route, json.dumps({
                'userName': str(username).strip(), 'password': password,
                'schoolId': cp.get('Yun', 'school_id'), 'type': '1'}, ensure_ascii=False), fixed_envelope=True)
            data = response.get('data') or {}
            if response.get('code') != 200 or not isinstance(data, dict) or not data.get('token'):
                self.error = str(response.get('msg') or '登录失败，未获得 token。').replace(password, '[隐藏]').replace(str(username), '[隐藏]')
                raise ValueError(self.error)
            p = client.profile
            self.store.save_credentials({'schema_version': 1, 'username': str(username).strip(),
                'password': password, 'token': data['token'], 'device_id': p.device_id,
                'device_name': p.device_name, 'sys_version': p.sys_version,'session_source':'password'})
            self.store.save_settings({'account_name': str(username).strip()})
            if previous.get('username') != str(username).strip():
                (self.store.data / 'rules.json').unlink(missing_ok=True)
                (self.store.data / 'records.json').unlink(missing_ok=True)
                self.store.save_settings({'task_id': ''})
            self.authenticated = True
            self.error = ''

    def accept_capture(self):
        """只读验证候选 token 的学号及学校，再切换会话；同一账号保留已存密码。

        不信任抓包请求里声称的账号字段。个人资料响应含敏感信息，仅取身份键在内存比较，
        不保存原始资料或将其返回网页。失败保留旧凭据，且不以密码登录替代验证候选 token。
        """
        with self.lock:
            candidate=self.store.pending_capture()
            if not candidate.get('token'):raise ValueError('尚无待验证的抓包 token，请先完成捕获。')
            client=self.client(candidate)
            profile=client.post_json('/login/getStudentInfo','',raise_on_business_code=True).get('data') or {}
            username=str(profile.get('userName') or '').strip()
            school=str(profile.get('schoolId') or '')
            if not username or school!=self.store.config().get('Yun','school_id'):
                raise ValueError('捕获会话的学号或学校无法核实，原账号未更改。')
            home=client.post_json('/run/getHomeRunInfo','',raise_on_business_code=True)
            tasks=(home.get('data') or {}).get('cralist') or []
            if not isinstance(tasks,list):raise ValueError('服务器任务列表格式不正确，原账号未更改。')
            previous=self.store.credentials();same=previous.get('username')==username
            saved={k:candidate[k] for k in ('token','device_id','device_name','sys_version') if k in candidate}
            saved.update(schema_version=1,username=username,session_source='capture')
            if same and previous.get('password'):saved['password']=previous['password']
            self.store.save_credentials(saved)
            if not same:
                (self.store.data/'records.json').unlink(missing_ok=True)
                self.store.save_settings({'task_id':''})
            self.store.save_settings({'account_name':username})
            self._cache_rules(tasks,saved)
            (self.store.data/'captured-session.dpapi').unlink(missing_ok=True)
            self.authenticated=True;self.restore_attempted=True;self.relogin_attempt_token=None;self.error=''
            return {'authenticated':True,'same_account':same,'password_retained':bool(saved.get('password'))}

    def _cache_rules(self,tasks,credentials):
        cache={'fetched_at':datetime.now(timezone.utc).isoformat(),'account':credentials.get('username',''),'tasks':tasks}
        write_json(self.store.data/'rules.json',cache)
        selected=str(self.store.settings().get('task_id',''))
        if len(tasks)==1:self.store.save_settings({'task_id':str(tasks[0].get('id',''))})
        elif selected and not any(str(t.get('id'))==selected for t in tasks):self.store.save_settings({'task_id':''})
        return cache

    def refresh(self, allow_relogin=True):
        """只读任务查询。自动登录仅发生在服务器明确返回凭据失效时。"""
        with self.lock:
            credentials = self.store.credentials()
            if not credentials.get('token'): raise ValueError('请先登录或通过抓包导入 token。')
            try:
                response = self.client(credentials).post_json('/run/getHomeRunInfo', '')
            except yun_http.HttpStatusException as exc:
                if exc.status != 401: raise
                response={'code':401,'msg':'登录状态失效'}
            auth_message=str(response.get('msg') or '').strip()
            expired=response.get('code') in (401,'401') or any(
                hint in auth_message.lower() for hint in ('token失效','token过期','token已过期','请先登录','登录已过期','登录状态失效'))
            if expired:
                self.authenticated = False
                if allow_relogin and self.relogin_attempt_token != credentials.get('token') and credentials.get('password'):
                    self.relogin_attempt_token = credentials['token']
                    self.login(credentials.get('username', ''), credentials['password'])
                    return self.refresh(allow_relogin=False)
                self.error = '登录状态已失效，请重新登录或使用抓包入口。'
                raise ValueError(self.error)
            if response.get('code') != 200:
                raise ValueError(str(response.get('msg') or '无法获取服务器任务规则。'))
            tasks = (response.get('data') or {}).get('cralist') or []
            if not isinstance(tasks, list): raise ValueError('服务器任务列表格式不正确。')
            cache = self._cache_rules(tasks,credentials)
            self.authenticated = True
            self.error = ''
            return cache

    def restore(self):
        with self.lock:
            if self.restore_attempted: return
            try:
                cred = self.store.credentials()
                if cred.get('token'): self.refresh()
                elif cred.get('password'): self.login(cred.get('username', ''), cred['password'])
            except Exception as exc:
                self.authenticated = False
                self.error = self.store.safe_message(exc)
            finally:
                self.restore_attempted = True

    def logout(self):
        with self.lock:
            self.store.clear_credentials()
            self.authenticated = False
            self.restore_attempted = True
            self.error = ''

