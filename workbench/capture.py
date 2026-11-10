"""手机连通性页、每机 CA 与短时抓包进程。LAN 端只提供测试页和公开附件。"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
from urllib.parse import urlsplit
from .storage import ROOT, read_json, write_json


def free_port(start):
    for port in range(start,start+30):
        try:
            with socket.socket() as sock: sock.bind(('0.0.0.0',port))
            return port
        except OSError: continue
    raise ValueError('没有可用端口，请关闭旧抓包进程后重试。')


class Capture:
    def __init__(self,store,auth,family):
        self.store,self.auth,self.family=store,auth,family
        self.proc=None; self.gateway=None; self.seen=False; self.phase='idle'
        self.nonce=secrets.token_urlsafe(18)
        self.socks=free_port(8088); self.http=free_port(self.socks+1)
        self.ca=store.data/'capture-ca'
        self.statefile=store.data/'capture-state.json'
        self.lock=threading.RLock()

    def ensure_gateway(self):
        if self.gateway: return
        owner=self
        class MobileHandler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                path=urlsplit(self.path).path
                prefix='/'+owner.nonce
                if path==prefix or path==prefix+'/':
                    owner.seen=True
                    text=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>手机连接检测</title><body style="font:17px system-ui;background:#f3f8f5;color:#153b2a;padding:28px;line-height:1.8"><h1>手机已连接电脑 ✓</h1><p>第一步检测通过。回到电脑面板启动抓包，再按页面填写 Super Proxy。</p><p>SOCKS5 端口：<b>{owner.socks}</b>；HTTP 端口：<b>{owner.http}</b></p><p><a href="{prefix}/apk">下载 Super Proxy（Android ARM64）</a></p><p><a href="{prefix}/certificate">下载当前电脑的 CA 证书</a></p><p>证书链接需先在电脑启动抓包；仅适用于当前抓包环境。抓包完成后关闭手机代理。</p></body></html>'''
                    self.send(200,text.encode(),'text/html; charset=utf-8')
                elif path==prefix+'/certificate':
                    cert=owner.ca/'mitmproxy-ca-cert.cer'
                    if cert.exists(): self.send(200,cert.read_bytes(),'application/x-x509-ca-cert','yunrun-current-ca.cer')
                    else: self.send(404,'请先在电脑上启动抓包，证书尚未生成。'.encode(),'text/plain; charset=utf-8')
                elif path==prefix+'/apk':
                    apk=ROOT/'extras/SuperProxy-3.2.3.apk'
                    if apk.exists():self.send(200,apk.read_bytes(),'application/vnd.android.package-archive','SuperProxy-3.2.3.apk')
                    else:self.send(404,'此源码构建未附 APK，请从 Super Proxy 官方网站获取手机软件。'.encode(),'text/plain; charset=utf-8')
                else: self.send(404,b'Not found','text/plain')
            def send(self,status,body,mime,filename=None):
                self.send_response(status); self.send_header('Content-Type',mime)
                self.send_header('Content-Length',str(len(body))); self.send_header('Cache-Control','no-store')
                if filename:self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
                self.end_headers(); self.wfile.write(body)
        self.gateway=ThreadingHTTPServer(('0.0.0.0',free_port(8092)),MobileHandler)
        threading.Thread(target=self.gateway.serve_forever,daemon=True).start()

    def status(self):
        with self.lock:
            self.ensure_gateway()
            captured=read_json(self.statefile,{})
            if self.phase=='waiting' and captured.get('captured'):
                self.phase='captured'
                if self.proc and self.proc.poll() is None:self.proc.terminate()
                # 捕获只是获得候选会话。完整验证前不改变当前登录状态，也不触发密码重登。
            elif self.phase=='waiting' and self.proc and self.proc.poll() is not None:self.phase='failed'
            try:addresses=sorted(set(a[4][0] for a in socket.getaddrinfo(socket.gethostname(),None,socket.AF_INET)))
            except OSError:addresses=[]
            addresses=[a for a in addresses if not a.startswith(('127.','169.254.'))]
            return {'phase':self.phase,'network_seen':self.seen,'addresses':addresses,
                    'gateway_port':self.gateway.server_port,'prefix':'/'+self.nonce,
                    'socks_port':self.socks,'http_port':self.http,
                    'certificate_ready':(self.ca/'mitmproxy-ca-cert.cer').exists()}

    def start(self):
        with self.lock:
            if self.proc and self.proc.poll() is None:return self.status()
            if not self.seen:raise ValueError('请先用手机打开连接测试页，确认手机能访问电脑。')
            self.ca.mkdir(parents=True,exist_ok=True)
            write_json(self.statefile,{'captured':False})
            (self.store.data/'captured-session.dpapi').unlink(missing_ok=True)
            env=os.environ.copy();env['YUNRUN_DATA_DIR']=str(self.store.data);env['PYTHONIOENCODING']='utf-8'
            argv=[sys.executable,'--capture-worker'] if getattr(sys,'frozen',False) else [sys.executable,'-c','from mitmproxy.tools.main import mitmdump; mitmdump()']
            argv+=['--mode',f'socks5@{self.socks}','--mode',f'regular@{self.http}',
                   '--set',f'confdir={self.ca}','--set','block_global=false','--set','flow_detail=0',
                   '--set','allow_hosts=^'+re.escape(urlsplit(self.store.config().get('Yun','school_host')).hostname)+r'(?::\d+)?$',
                   '-s',str(ROOT/'tools/capture_bridge.py')]
            self.proc=subprocess.Popen(argv,cwd=str(ROOT),env=env,stdin=subprocess.DEVNULL,
                 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.family.attach(self.proc);self.phase='waiting'
            return self.status()

    def stop(self):
        with self.lock:
            if self.proc and self.proc.poll() is None:self.proc.terminate()
            if self.phase!='captured':self.phase='stopped'
            return self.status()

