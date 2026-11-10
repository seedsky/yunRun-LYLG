"""仅启动临时 loopback 演示服务，验证 HTTP 边界与产品禁用约束。"""
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from workbench.server import Application,make_handler
from workbench.storage import Store


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='yunrun-http-')
        self.app=Application(Store(Path(self.temp.name)),demo=True)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.app))
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.guard=patch('requests.sessions.Session.request',side_effect=AssertionError('Real HTTP forbidden'))
        self.guard.start()

    def tearDown(self):
        self.guard.stop();self.server.shutdown();self.server.server_close();self.app.family.close();self.temp.cleanup()

    def request(self,path,data=None,headers=None,authenticated=True):
        h={'X-Workbench-Mode':'online'}
        if authenticated:h['X-Workbench-Token']=self.app.key
        h.update(headers or {})
        body=None if data is None else json.dumps(data).encode()
        req=urllib.request.Request(f'http://127.0.0.1:{self.server.server_port}'+path,data=body,headers=h)
        try:
            response=self.opener.open(req,timeout=10)
            return response.status,response.read()
        except urllib.error.HTTPError as exc:return exc.code,exc.read()

    def test_unauthorized_api_denied(self):
        self.assertEqual(self.request('/api/jobs',authenticated=False)[0],403)

    def test_cross_origin_mutation_denied(self):
        self.assertEqual(self.request('/api/routes/generate',{},headers={'Origin':'https://untrusted.example'})[0],403)

    def test_demo_rejects_real_execution_and_school_requests(self):
        for path,data in [('/api/jobs/start',{'dry':False}),('/api/auth/login',{'username':'DEMO','password':'fake'}),('/api/history/pull',{})]:
            self.assertEqual(self.request(path,data)[0],400)

    def test_snapshot_restored_after_page_refresh(self):
        status,raw=self.request('/api/routes/generate',{'source':'synth','km':2,'pace':5.5,'cadence':170,'seed':7})
        self.assertEqual(status,200);preview=json.loads(raw)
        _,raw=self.request('/api/state');state=json.loads(raw)
        self.assertEqual(state['preview']['digest'],preview['digest'])
        self.assertEqual(state['preview']['params']['seed'],7)

    def test_static_and_shell_resources(self):
        self.assertEqual(self.request('/',authenticated=False)[0],200)
        self.assertEqual(self.request('/offline',authenticated=False)[0],200)
        self.assertEqual(self.request('/static/vendor/leaflet.js',authenticated=False)[0],200)
        self.assertNotEqual(self.request('/static/../config.ini',authenticated=False)[0],200)

    def test_backup_without_account_keeps_current_session(self):
        _,raw=self.request('/api/backup/export',{})
        _,raw=self.request('/api/backup/inspect',{'content':json.loads(raw)})
        inspection=json.loads(raw)
        status,_=self.request('/api/backup/import',{'id':inspection['id'],'choices':{'account':True,'settings':False,'routes':False}})
        self.assertEqual(status,200)
        self.assertTrue(self.app.auth.authenticated)


if __name__=='__main__':unittest.main()
