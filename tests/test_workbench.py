"""关键行为回归。全部使用虚构账号、临时数据目录及假传输，不连接学校服务。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import taskgen
from workbench.storage import Store, ROOT, write_json
from workbench.auth import Auth
from workbench.routes import Routes, RouteConflict, digest
from workbench.backup import Backups
from workbench import rules
from workbench.jobs import Jobs, ProcessFamily


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='yunrun-test-')
        self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name))
        self.task={'id':619,'schoolId':197,'campusId':'1890310131181043717','raType':'T3','raRunArea':'offline:test',
          'raSingleMileageMin':2,'raSingleMileageMax':5,'raPaceMin':3,'raPaceMax':10,
          'raCadenceMin':35,'raCadenceMax':350,'dayStartTime':'00:00','dayEndTime':'23:59',
          'raDislikes':5,'raMinDislikes':3,'points':'|'.join(f'{x},{y}' for x,y in taskgen.CARDS),
          'fence':'|'.join(f'{x},{y}' for x,y in taskgen.FENCE)}
        write_json(self.store.data/'rules.json',{'tasks':[self.task],'fetched_at':'2026-11-10T00:00:00+08:00'})
        self.store.save_settings({'task_id':'619'})
        self.routes=Routes(self.store)
        # 所有同进程 requests 请求一律失败，测试不能意外使用个人账号。
        guard=patch('requests.sessions.Session.request',side_effect=AssertionError('Test forbids real HTTP'))
        guard.start();self.addCleanup(guard.stop)

    def preview(self):
        return self.routes.generate({'source':'synth','km':2,'pace':5.5,'cadence':170,'seed':7,'amp':1})

    def test_dpapi_roundtrip_and_logout(self):
        original={'username':'DEMO001','password':'not-a-real-password','token':'NOT-A-REAL-TOKEN'}
        self.store.save_credentials(original)
        raw=self.store.credentials_path.read_bytes()
        self.assertNotIn(original['password'].encode(),raw)
        self.assertEqual(self.store.credentials(),original)
        self.store.save_settings({'account_name':'DEMO001'})
        Auth(self.store).logout()
        self.assertFalse(self.store.credentials_path.exists())
        self.assertEqual(self.store.settings()['account_name'],'DEMO001')

    def test_seed_reproducible_and_varying_pace(self):
        first=self.preview();second=self.preview()
        self.assertEqual(first['digest'],second['digest'])
        self.assertGreater(max(first['paces'])-min(first['paces']),.1)
        self.assertAlmostEqual(first['metrics']['km'],2)
        self.assertEqual(first['metrics']['duration'],660)
        self.assertEqual(first['metrics']['cards'],6)
        self.assertFalse(list((self.store.data/'routes').glob('*.json')))

    def test_invalid_parameters_not_silently_adjusted(self):
        params={'source':'synth','km':8,'pace':5.5,'cadence':170,'seed':7}
        with self.assertRaises(RouteConflict) as raised:self.routes.generate(params)
        self.assertEqual(params['km'],8)
        self.assertTrue(any(v['field']=='km' for v in raised.exception.issues))

    def test_dynamic_rules_override_old_generator_limits(self):
        self.task['raSingleMileageMin']=1;self.task['raPaceMin']=2
        write_json(self.store.data/'rules.json',{'tasks':[self.task]})
        self.assertFalse(rules.validate(self.store,{'km':1.5,'pace':2.5,'cadence':170,'duration':225}))

    def test_preview_save_export_does_not_change_source(self):
        preview=self.preview();saved=self.routes.save(preview['id'],'测试路线')
        before=self.routes.source(saved['id'])[0]
        self.routes.preview(saved['id'])
        self.assertEqual(before,self.routes.source(saved['id'])[0])
        self.assertEqual(len(self.routes.export(saved['id'],True)['features'][0]['geometry']['coordinates']),preview['metrics']['points'])

    def test_snapshot_tamper_rejected(self):
        preview=self.preview();self.routes.previews.clear()
        path=self.store.data/'previews'/(preview['id']+'.json')
        obj=json.loads(path.read_text(encoding='utf-8'));obj['task']['data']['duration']=999;write_json(path,obj)
        with self.assertRaises(ValueError):self.routes.get(preview['id'])

    def test_path_traversal_rejected(self):
        with self.assertRaises(ValueError):self.routes.source('../../config.ini')
        with self.assertRaises(ValueError):self.routes.get('../credentials.dpapi')

    def test_backup_excludes_password_and_keeps_duplicate_routes(self):
        self.store.save_credentials({'username':'DEMO001','password':'private-test-password','token':'dummy-token'})
        saved=self.routes.save(self.preview()['id'],'同名路线')
        backups=Backups(self.store,self.routes);obj=backups.export()
        self.assertNotIn('private-test-password',json.dumps(obj))
        self.assertNotIn('password',obj['account'])
        inspection=backups.inspect(obj)
        self.assertTrue(inspection['routes'][0]['conflict'])
        backups.restore(inspection['id'],{'account':True,'settings':False,'routes':True})
        names=[s['name'] for s in self.routes.sources() if s['kind']=='library']
        self.assertIn('同名路线 (2)',names)
        self.assertNotIn('password',self.store.credentials())

    def test_bad_backup_validated_before_write(self):
        backup={'format':'yunrun-backup','version':1,'routes':[{'name':'bad','task':{'invalid':True}}]}
        with self.assertRaises(ValueError):Backups(self.store,self.routes).inspect(backup)
        self.assertFalse(list((self.store.data/'routes').glob('*.json')))

    def test_import_failure_rolls_back_settings_and_credentials(self):
        self.store.save_credentials({'username':'DEMO001','password':'old-test-secret','token':'old-test-token'})
        self.store.save_settings({'theme':'dark'})
        self.routes.save(self.preview()['id'],'原路线')
        backups=Backups(self.store,self.routes);obj=backups.export();obj['settings']['theme']='light'
        inspection=backups.inspect(obj)
        with patch.object(self.routes,'import_route',side_effect=OSError('模拟写入失败')):
            with self.assertRaises(OSError):backups.restore(inspection['id'],{'account':True,'settings':True,'routes':True})
        self.assertEqual(self.store.settings()['theme'],'dark')
        self.assertEqual(self.store.credentials()['password'],'old-test-secret')
        self.assertEqual(len([s for s in self.routes.sources() if s['kind']=='library']),1)

    def test_deformation_and_drawn_sources_use_common_snapshot(self):
        obj=self.routes.get(self.preview()['id'])['task']
        points=[list(map(float,r['point'].split(','))) for r in obj['data']['pointsList']]
        result=self.routes.generate({'source':'draw','drawn':points,'km':2,'pace':5.5,'cadence':170,'seed':7})
        self.assertEqual(result['metrics']['duration'],660)
        self.assertGreaterEqual(result['metrics']['cards'],3)
        saved=self.routes.save(self.preview()['id'],'形变来源')
        changed=self.routes.generate({'source':'library','source_id':saved['id'],'deform':True,
                                     'km':2,'pace':5.5,'cadence':170,'seed':7})
        self.assertNotEqual(changed['coordinates'],points)

    def test_preview_card_indices_match_final_rows(self):
        obj=self.routes.get(self.preview()['id'])['task']
        for item in obj['data']['manageList']:
            card=tuple(map(float,item['point'].split(',')))
            row=tuple(map(float,obj['data']['pointsList'][int(item['index'])]['point'].split(',')))
            self.assertLessEqual(taskgen.hav(card,row),20)

    def test_auth_failure_not_retried(self):
        auth=Auth(self.store)
        class Fake:
            def __init__(self,profile):self.profile=profile;self.calls=[]
            def post_json(self,path,*args,**kwargs):self.calls.append(path);return {'code':500,'msg':'模拟密码错误'}
        fake=Fake(auth.profile({}))
        with patch.object(auth,'client',return_value=fake):
            with self.assertRaises(ValueError):auth.login('DEMO001','wrong-test-password')
        self.assertEqual(fake.calls,['/login/appLogin'])
        self.assertFalse(self.store.credentials_path.exists())

    def test_expired_token_relogin_only_once(self):
        self.store.save_credentials({'username':'DEMO001','password':'test-password','token':'expired'})
        auth=Auth(self.store);profile=auth.profile({})
        responses=[{'code':401},{'code':200,'data':{'token':'new-token'}},{'code':401}]
        calls=[]
        class Fake:
            def __init__(self):self.profile=profile
            def post_json(self,path,*args,**kwargs):calls.append(path);return responses.pop(0)
        with patch.object(auth,'client',return_value=Fake()):
            with self.assertRaises(ValueError):auth.refresh()
        self.assertEqual(calls,['/run/getHomeRunInfo','/login/appLogin','/run/getHomeRunInfo'])

    def test_network_error_does_not_replay_password(self):
        import yun_http
        self.store.save_credentials({'username':'DEMO001','password':'test-password','token':'old-token'})
        auth=Auth(self.store)
        class Fake:
            def post_json(self,*args,**kwargs):raise yun_http.TransportOutcomeUnknown('https://offline.invalid','模拟网络中断')
        with patch.object(auth,'client',return_value=Fake()) as client:
            with self.assertRaises(yun_http.TransportOutcomeUnknown):auth.refresh()
            self.assertEqual(client.call_count,1)

    def test_missing_rule_source_is_explicit(self):
        fields=rules.view(self.store)['fields']
        self.assertEqual(fields['face'],{'value':None,'source':'未确认'})
        self.assertEqual(fields['max_minutes']['source'],'本地配置')

    def test_task_selection_never_silently_uses_first(self):
        write_json(self.store.data/'rules.json',{'tasks':[self.task,dict(self.task,id=620)]})
        self.store.save_settings({'task_id':''})
        self.assertIsNone(rules.selected(self.store))
        with self.assertRaises(ValueError):rules.selected(self.store,True)

    def test_local_drill_exact_snapshot_in_child(self):
        preview=self.preview();family=ProcessFamily();self.addCleanup(family.close)
        jobs=Jobs(self.store,Auth(self.store),self.routes,family)
        jobs.start(preview['id'],dry=True,mode='offline')
        deadline=time.monotonic()+30
        while jobs.active and time.monotonic()<deadline:time.sleep(.1)
        self.assertEqual(jobs.state['status'],'completed',jobs.state['logs'])
        self.assertEqual(jobs.state['submission']['state'],'simulated')
        self.assertEqual(jobs.state['grade']['state'],'passed')
        self.assertTrue(any(e['level']=='success' for e in jobs.state['log_entries']))
        self.assertEqual(jobs.state['digest'],preview['digest'])
        job_dir=self.store.data/'jobs'/jobs.state['id']
        self.assertEqual(digest(json.loads((job_dir/'task_pick/tasklist_0.json').read_text(encoding='utf-8'))),preview['digest'])
        self.assertFalse(self.store.credentials_path.exists())

    def test_interruption_is_not_resumed(self):
        write_json(self.store.data/'jobs/current.json',{'status':'running','elapsed':12,'confirmed_m':50,'logs':[]})
        family=ProcessFamily();self.addCleanup(family.close)
        jobs=Jobs(self.store,Auth(self.store),self.routes,family)
        self.assertEqual(jobs.state['status'],'interrupted')
        self.assertIsNone(jobs.proc)

    def test_controller_lifetime_ends_children(self):
        family=ProcessFamily();self.addCleanup(family.close)
        proc=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.addCleanup(lambda:proc.terminate() if proc.poll() is None else None)
        family.attach(proc);family.close()
        self.assertIsNotNone(proc.wait(timeout=5))

    def test_history_routes_keep_their_account(self):
        preview=self.preview()
        task=self.routes.get(preview['id'])['task']
        self.routes.import_route(task,'历史 · DEMO001 · 2026-11-19 13:20','history',account='DEMO001')
        item=next(i for i in self.routes.sources() if i['kind']=='history')
        self.assertEqual(item['account'],'DEMO001')
        self.assertIn('DEMO001',item['name'])

    def test_multiple_accounts_are_kept_and_switchable(self):
        auth=Auth(self.store)
        self.store.save_credentials({'username':'DEMO001','password':'pw1','token':'t1'})
        self.store.save_credentials({'username':'DEMO002','password':'pw2','token':'t2'})
        self.assertEqual([item['username'] for item in self.store.accounts()],['DEMO002','DEMO001'])
        self.assertEqual(self.store.credentials()['username'],'DEMO002')
        self.assertEqual(self.store.credentials_for('DEMO001')['password'],'pw1')
        calls=[]
        with patch.object(auth,'login',side_effect=lambda u,p:calls.append((u,p))):
            auth.login_saved('DEMO001')
        self.assertEqual(calls,[('DEMO001','pw1')])
        with self.assertRaises(ValueError):
            auth.login_saved('DEMO404')

    def test_logout_keep_only_drops_current_session(self):
        auth=Auth(self.store)
        self.store.save_credentials({'username':'DEMO001','password':'pw1','token':'t1'})
        self.store.save_credentials({'username':'DEMO002','password':'pw2','token':'t2'})
        auth.logout(clear=False)
        self.assertNotIn('token',self.store.credentials_for('DEMO002'))
        self.assertEqual(self.store.credentials_for('DEMO002')['password'],'pw2')
        self.assertEqual(self.store.credentials_for('DEMO001')['token'],'t1')
        self.assertEqual(len(self.store.accounts()),2)

    def test_forget_removes_one_account(self):
        auth=Auth(self.store)
        self.store.save_credentials({'username':'DEMO001','password':'pw1','token':'t1'})
        self.store.save_credentials({'username':'DEMO002','password':'pw2'})
        auth.forget('DEMO001')
        self.assertEqual([item['username'] for item in self.store.accounts()],['DEMO002'])
        self.assertEqual(self.store.credentials()['username'],'DEMO002')
        auth.forget('DEMO002')
        self.assertEqual(self.store.accounts(),[])
        self.assertEqual(self.store.credentials(),{})

    def test_legacy_single_account_file_is_migrated(self):
        from workbench.storage import _crypt, atomic_bytes
        legacy={'username':'DEMO001','password':'pw1','token':'t1','device_id':'dev'}
        atomic_bytes(self.store.credentials_path,_crypt(json.dumps(legacy).encode()))
        self.assertEqual(self.store.credentials()['password'],'pw1')
        self.assertEqual(self.store.credentials()['device_id'],'dev')
        self.assertEqual([item['username'] for item in self.store.accounts()],['DEMO001'])

    def test_logout_can_keep_saved_password_for_quick_login(self):
        auth=Auth(self.store)
        self.store.save_credentials({'username':'DEMO001','password':'only-for-fixture','token':'NOT-A-REAL-TOKEN'})
        auth.authenticated=True
        auth.logout(clear=False)
        cred=self.store.credentials()
        self.assertNotIn('token',cred)
        self.assertEqual(cred['password'],'only-for-fixture')
        self.assertFalse(self.store.settings()['autologin'])
        # 退出后不会在下次刷新时自动登回去。
        auth.restore_attempted=False;auth.restore()
        self.assertFalse(auth.authenticated)
        # 点「重试自动登录」才会用保存的密码重新登录。
        calls=[]
        with patch.object(auth,'login',side_effect=lambda u,p:(calls.append((u,p)),setattr(auth,'authenticated',True))):
            self.store.save_settings({'autologin':True})
            auth.restore_attempted=False;auth.restore()
        self.assertEqual(calls,[('DEMO001','only-for-fixture')])

    def test_clear_logout_removes_everything(self):
        auth=Auth(self.store)
        self.store.save_credentials({'username':'DEMO001','password':'only-for-fixture'})
        auth.logout(clear=True)
        self.assertFalse(self.store.credentials_path.exists())
        self.assertFalse(self.store.settings()['autologin'])

    def test_view_exposes_raw_server_fields_without_identity(self):
        view=rules.view(self.store)
        keys={row['key'] for row in view['raw']}
        self.assertIn('raRunArea',keys)
        self.assertIn('raSingleMileageMin',keys)
        for hidden in ('points','fence','schoolId'):
            self.assertNotIn(hidden,keys)

    def test_venue_follows_selected_task(self):
        """王城场景：同校两个打卡区域各有围栏和点位，选中哪个就用哪个。"""
        def shift(dx):
            return ([[x+dx,y] for x,y in taskgen.CARDS],[[x+dx,y] for x,y in taskgen.FENCE])
        east_cards,east_fence=shift(.004);west_cards,west_fence=shift(-.004)
        east=dict(self.task,id=620,raName='东操场',campusName='王城',
                  points='|'.join(f'{x},{y}' for x,y in east_cards),fence='|'.join(f'{x},{y}' for x,y in east_fence))
        west=dict(self.task,id=622,raName='西操场',campusName='王城',
                  points=west_cards,fence=west_fence)   # 数组编码同样要能识别
        write_json(self.store.data/'rules.json',{'tasks':[east,west],'fetched_at':'2026-11-10T00:00:00+08:00'})
        self.store.save_settings({'task_id':''})
        # 多任务未选择：不猜场地，也不允许生成。
        self.assertEqual(rules.view(self.store)['source'],'unselected')
        self.assertEqual(rules.view(self.store)['fence'],[])
        with self.assertRaises(ValueError):self.routes.generate({'source':'synth','km':2,'seed':7})
        # 选中东操场：地图数据与预览围栏都换成东操场。
        self.store.save_settings({'task_id':'620'})
        view=rules.view(self.store)
        self.assertEqual(view['fence'],east_fence);self.assertEqual(view['points'],east_cards)
        self.assertEqual(view['campus'],'王城')
        east_preview=self.routes.generate({'source':'synth','km':2,'seed':7})
        self.assertEqual(east_preview['fence'],east_fence)
        self.assertTrue(all(taskgen.inside(p,east_fence) for p in east_preview['coordinates']))
        # 切到西操场：数组编码的围栏同样生效，旧预览不能再演练或执行。
        self.store.save_settings({'task_id':'622'})
        west_preview=self.routes.generate({'source':'synth','km':2,'seed':7})
        self.assertEqual(west_preview['fence'],west_fence)
        self.assertTrue(all(taskgen.inside(p,west_fence) for p in west_preview['coordinates']))
        self.store.save_settings({'task_id':'620'})
        family=ProcessFamily();self.addCleanup(family.close)
        with self.assertRaises(ValueError):
            Jobs(self.store,Auth(self.store),self.routes,family).start(west_preview['id'],dry=True,mode='offline')

    def test_local_check_is_per_task_and_switchable(self):
        self.store.save_settings({'task_id':'619'})
        limits=rules.save_limits(self.store,'619',True,{'km_min':3})
        self.assertTrue(limits['customized']);self.assertEqual(limits['values']['km_min'],3)
        issues=rules.validate(self.store,{'km':2,'pace':5.5,'cadence':170,'duration':660})
        self.assertEqual([i['level'] for i in issues],['error'])
        with self.assertRaises(RouteConflict):self.routes.generate({'source':'synth','km':2,'seed':7})
        # 关闭本地检查：同样的越界只给提示，不拦截生成。
        rules.save_limits(self.store,'619',False,{'km_min':3})
        preview=self.routes.generate({'source':'synth','km':2,'seed':7})
        self.assertEqual([i['level'] for i in preview['issues']],['notice'])
        # 另一个任务不受影响。
        write_json(self.store.data/'rules.json',{'tasks':[self.task,dict(self.task,id=620)]})
        self.assertEqual(rules.limits(self.store,'620')['values']['km_min'],2)
        self.assertFalse(rules.limits(self.store,'620')['customized'])

    def test_invalid_local_check_values_are_rejected(self):
        self.store.save_settings({'task_id':'619'})
        for values in ({'km_min':5,'km_max':2},{'start':'25:00'},{'pace_min':-1},{'unknown':1}):
            with self.subTest(values=values),self.assertRaises(ValueError):
                rules.save_limits(self.store,'619',True,values)
        with self.assertRaises(ValueError):
            rules.save_limits(self.store,'999',True,{'km_min':1})
        self.assertEqual(rules.limits(self.store,'619')['values']['km_min'],2)

    def test_routes_from_another_area_are_rejected(self):
        """东操场的路线不能在西操场的区域里生成或预览。"""
        east=dict(self.task,id=620,raName='东操场',
                  points='|'.join(f'{x+.004},{y}' for x,y in taskgen.CARDS),
                  fence='|'.join(f'{x+.004},{y}' for x,y in taskgen.FENCE))
        west=dict(self.task,id=622,raName='西操场',
                  points='|'.join(f'{x-.004},{y}' for x,y in taskgen.CARDS),
                  fence='|'.join(f'{x-.004},{y}' for x,y in taskgen.FENCE))
        write_json(self.store.data/'rules.json',{'tasks':[east,west]})
        self.store.save_settings({'task_id':'620'})
        saved=self.routes.save(self.routes.generate({'source':'synth','km':2,'seed':7})['id'],'东操场路线')
        self.store.save_settings({'task_id':'622'})
        with self.assertRaises(ValueError) as raised:
            self.routes.generate({'source':'library','source_id':saved['id'],'km':2,'seed':7})
        self.assertIn('围栏外',str(raised.exception))
        with self.assertRaises(ValueError):
            self.routes.preview(saved['id'])

    def test_missing_fence_is_reported_not_borrowed(self):
        task=dict(self.task);task.pop('fence')
        write_json(self.store.data/'rules.json',{'tasks':[task]})
        self.store.save_settings({'task_id':'619'})
        view=rules.view(self.store)
        self.assertEqual(view['fence'],[]);self.assertEqual(len(view['points']),6)
        with self.assertRaises(ValueError) as raised:self.routes.generate({'source':'synth','km':2,'seed':7})
        self.assertIn('没有下发围栏',str(raised.exception))

    def test_route_sources_have_no_builtin_samples(self):
        self.assertTrue(all(item['kind'] in ('library','history') for item in self.routes.sources()))

    def test_capture_generates_current_ca_and_serves_only_public_files(self):
        from workbench.capture import Capture
        import urllib.request,urllib.error
        family=ProcessFamily();self.addCleanup(family.close)
        capture=Capture(self.store,Auth(self.store),family)
        def cleanup():
            if capture.proc and capture.proc.poll() is None:capture.proc.terminate();capture.proc.wait(timeout=5)
            if capture.gateway:capture.gateway.shutdown();capture.gateway.server_close()
        self.addCleanup(cleanup)
        state=capture.status()
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        url=f'http://127.0.0.1:{state["gateway_port"]}{state["prefix"]}'
        with opener.open(url,timeout=5) as response:self.assertEqual(response.status,200)
        self.assertTrue(capture.status()['network_seen'])
        capture.start();deadline=time.monotonic()+20
        cert=capture.ca/'mitmproxy-ca-cert.cer'
        while not cert.exists() and time.monotonic()<deadline:
            if capture.proc.poll() is not None:break
            time.sleep(.1)
        self.assertTrue(cert.exists(),'抓包进程未能生成 CA')
        with opener.open(url+'/certificate',timeout=5) as response:body=response.read()
        self.assertIn(b'CERTIFICATE',body);self.assertNotIn(b'PRIVATE KEY',body)
        with self.assertRaises(urllib.error.HTTPError):opener.open(url+'/credentials.dpapi',timeout=5)
        self.assertFalse(self.store.credentials_path.exists())


if __name__=='__main__':unittest.main(verbosity=2)
