"""候选抓包会话的身份隔离与密码保留；只使用虚构身份和假客户端。"""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from workbench.storage import Store,read_json
from workbench.auth import Auth
from tools.capture_bridge import CaptureToken


class CaptureAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name));self.auth=Auth(self.store)
        self.old={'username':'DEMO001','password':'test-secret','token':'old-token'}
        self.store.save_credentials(self.old)
        self.store.stage_capture({'token':'captured-token','device_id':'test-device'})
        self.calls=[]
        guard=patch('requests.sessions.Session.request',side_effect=AssertionError('Real HTTP forbidden'))
        guard.start();self.addCleanup(guard.stop)

    def fake(self,username='DEMO001',school=197,fail=False):
        owner=self
        class Client:
            def post_json(self,path,*args,**kwargs):
                owner.calls.append(path)
                if fail:raise ValueError('模拟认证失败')
                if path=='/login/getStudentInfo':return {'code':200,'data':{'userName':username,'schoolId':school,'password':'never-persist-profile-secret','realName':'private-name'}}
                return {'code':200,'data':{'cralist':[{'id':619}]}}
        return Client()

    def test_same_account_keeps_password_after_readonly_verification(self):
        with patch.object(self.auth,'client',return_value=self.fake()):result=self.auth.accept_capture()
        self.assertTrue(result['password_retained'])
        self.assertEqual(self.store.credentials()['password'],'test-secret')
        self.assertEqual(self.store.credentials()['token'],'captured-token')
        self.assertFalse(self.store.pending_capture())
        self.assertEqual(self.calls,['/login/getStudentInfo','/run/getHomeRunInfo'])
        self.assertNotIn('never-persist-profile-secret',json.dumps(read_json(self.store.data/'rules.json')))

    def test_other_account_does_not_inherit_password(self):
        with patch.object(self.auth,'client',return_value=self.fake('DEMO002')):result=self.auth.accept_capture()
        self.assertFalse(result['password_retained']);self.assertFalse(result['same_account'])
        self.assertNotIn('password',self.store.credentials())
        self.assertEqual(self.store.settings()['account_name'],'DEMO002')

    def test_invalid_capture_preserves_old_login(self):
        self.auth.authenticated=True
        with patch.object(self.auth,'client',return_value=self.fake(fail=True)):
            with self.assertRaises(ValueError):self.auth.accept_capture()
        self.assertEqual(self.store.credentials(),self.old);self.assertTrue(self.auth.authenticated)
        self.assertTrue(self.store.pending_capture())

    def test_other_school_rejected_before_switch(self):
        with patch.object(self.auth,'client',return_value=self.fake(school=999)):
            with self.assertRaises(ValueError):self.auth.accept_capture()
        self.assertEqual(self.store.credentials(),self.old)
        self.assertEqual(self.calls,['/login/getStudentInfo'])

    def test_bridge_stages_identical_token_and_reports_success(self):
        flow=SimpleNamespace(request=SimpleNamespace(pretty_host='sports.aiyyd.com',headers={'token':'old-token'}))
        with patch('tools.capture_bridge.Store',return_value=self.store):CaptureToken().request(flow)
        self.assertEqual(self.store.credentials(),self.old)
        self.assertEqual(self.store.pending_capture()['token'],'old-token')
        self.assertTrue(read_json(self.store.data/'capture-state.json')['captured'])
        self.assertNotIn(b'old-token',(self.store.data/'captured-session.dpapi').read_bytes())
        self.assertNotIn('old-token',self.store.safe_message('old-token'))
        self.store.clear_credentials();self.assertFalse(self.store.pending_capture())
