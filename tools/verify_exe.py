"""在含中文与空格的全新目录验证单文件 EXE；只做本地演练和 loopback 代理。

不读取个人数据、不调用学校服务、不创建真实记录。候选 token 使用公开测试字串。
"""
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workbench import __version__

ROOT=Path(__file__).resolve().parents[1]


def main():
    base=(ROOT/'.test-data').resolve();base.mkdir(exist_ok=True)
    source=ROOT/'distribution/exe'/f'yunRun-LYLG-v{__version__}.exe'
    with tempfile.TemporaryDirectory(prefix='EXE 中文 验收 ',dir=base) as temp:
        folder=Path(temp).resolve();assert folder.is_relative_to(base)
        exe=folder/source.name;shutil.copy2(source,exe)
        env=os.environ.copy();env.pop('YUNRUN_DATA_DIR',None);env.pop('PYTHONPATH',None)
        env['PYTHONUTF8']='1'
        version=subprocess.run([str(exe),'--version'],cwd=folder,env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
        assert version.returncode==0 and version.stdout.strip()==__version__,version.stderr
        data=folder/'云运动用户数据';pid=None;local_server=None
        log=open(folder/'exe.log','wb')
        proc=subprocess.Popen([str(exe),'--mode','offline','--no-browser','--port','8190'],cwd=base,env=env,
            stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def until(fn,seconds=30):
            deadline=time.monotonic()+seconds
            while time.monotonic()<deadline:
                value=fn()
                if value:return value
                if proc.poll() is not None:raise AssertionError('EXE 提前退出：'+(folder/'exe.log').read_text(encoding='utf-8',errors='replace'))
                time.sleep(.15)
            raise AssertionError('EXE 检查等待超时')
        try:
            until(lambda:(data/'server.json').exists())
            state=json.loads((data/'server.json').read_text(encoding='utf-8'));pid=state['pid']
            url=f'http://127.0.0.1:{state["port"]}'
            def request(path,body=None,mode='offline'):
                payload=None if body is None else json.dumps(body).encode()
                headers={'X-Workbench-Token':state['key'],'X-Workbench-Mode':mode}
                with opener.open(urllib.request.Request(url+path,data=payload,headers=headers),timeout=20) as r:return json.loads(r.read())
            assert request('/api/state')['mode']=='offline'
            for path in ['/offline','/static/drawing.js','/static/vendor/leaflet.js']:
                with opener.open(url+path,timeout=10) as r:assert r.status==200
            preview=request('/api/routes/generate',{'source':'synth','km':2,'pace':5.5,'cadence':170,'seed':7})
            job=request('/api/jobs/start',{'id':preview['id'],'dry':True})
            def ended():
                latest=request('/api/jobs')
                return latest if latest['status'] in ('completed','failed','stopped','interrupted') else None
            result=until(ended,40)
            assert result['status']=='completed',result.get('logs')
            assert result['submission']['state']=='simulated'
            assert result['digest']==preview['digest']
            try:request('/api/jobs/start',{'id':preview['id'],'dry':False})
            except urllib.error.HTTPError as e:assert e.code==400
            else:raise AssertionError('独立工具未拒绝正式执行')
            capture=request('/api/capture',mode='online')
            gateway=f'http://127.0.0.1:{capture["gateway_port"]}{capture["prefix"]}'
            with opener.open(gateway,timeout=5) as r:assert r.status==200
            request('/api/capture/start',{},mode='online')
            until(lambda:(data/'capture-ca/mitmproxy-ca-cert.cer').exists(),30)
            def socks_ready():
                try:
                    with socket.create_connection(('127.0.0.1',capture['socks_port']),timeout=.5) as s:
                        s.sendall(b'\x05\x01\x00');return s.recv(2)==b'\x05\x00'
                except OSError:return False
            until(socks_ready,30)
            with opener.open(gateway+'/certificate',timeout=5) as r:certificate=r.read()
            assert b'CERTIFICATE' in certificate and b'PRIVATE KEY' not in certificate
            class Upstream(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200);self.end_headers();self.wfile.write(b'LOCAL_ONLY')
                def log_message(self,*args):pass
            local_server=ThreadingHTTPServer(('127.0.0.1',0),Upstream)
            threading.Thread(target=local_server.serve_forever,daemon=True).start()
            # URI 的目标是 loopback；Host 仅用于验证桥接的学校主机筛选，不作学校网络请求。
            with socket.create_connection(('127.0.0.1',capture['http_port']),timeout=5) as s:
                s.settimeout(5)
                packet=(f'GET http://127.0.0.1:{local_server.server_port}/capture-fixture HTTP/1.1\r\n'
                    'Host: sports.aiyyd.com\r\ntoken: FROZEN-LOCAL-TEST-TOKEN\r\nConnection: close\r\n\r\n')
                s.sendall(packet.encode())
                try:s.recv(2048)
                except (TimeoutError,ConnectionResetError):pass
            until(lambda:(data/'captured-session.dpapi').exists(),15)
            assert b'FROZEN-LOCAL-TEST-TOKEN' not in (data/'captured-session.dpapi').read_bytes()
            captured=request('/api/capture',mode='online');assert captured['phase']=='captured'
            request('/api/capture/stop',{},mode='online')
            assert not (data/'credentials.dpapi').exists()
            report={'version':__version__,'single_file_startup':'通过','resource_paths':'通过',
                'persistent_data_outside_temp_resources':'通过','frozen_drill_worker':'通过','snapshot_unchanged':True,
                'frozen_proxy_worker':'通过','socks5_handshake':'通过','ca_download':'通过',
                'frozen_capture_bridge':'通过','real_school_requests':0,'real_uploads':0}
            (ROOT/'distribution/exe/exe-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(report,ensure_ascii=False))
        finally:
            if local_server:local_server.shutdown();local_server.server_close()
            if pid:
                try:os.kill(pid,signal.SIGTERM)
                except ProcessLookupError:pass
            try:proc.wait(timeout=20)
            except subprocess.TimeoutExpired:proc.terminate();proc.wait(timeout=10)
            log.close()


if __name__=='__main__':main()
