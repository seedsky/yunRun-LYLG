"""记录与成绩判定回归，只使用虚构记录和假客户端。"""
import tempfile
from pathlib import Path
import unittest
from workbench.storage import Store
from workbench.records import Records,grade


class RecordTests(unittest.TestCase):
    def test_explicit_status_and_unknown_numeric(self):
        self.assertEqual(grade({'isStandard':'Y'})['state'],'passed')
        self.assertEqual(grade({'isStandard':'Y','isCheat':'Y'})['state'],'failed')
        self.assertEqual(grade({'isQualified':'1'})['state'],'passed')
        self.assertEqual(grade({'isQualified':'2'})['state'],'failed')
        self.assertEqual(grade({'isQualified':'9'})['state'],'unknown')
        self.assertEqual(grade({'isStandard':'1'})['state'],'unknown')
        self.assertEqual(grade({'isQualified':'9'},{'9':'pending'})['state'],'pending')

    def test_demo_has_pass_fail_unknown_and_times(self):
        with tempfile.TemporaryDirectory() as tmp:
            records=Records(Store(Path(tmp)),None,True).query()
            self.assertEqual({r['grade']['state'] for r in records['records']},{'passed','failed','unknown'})
            self.assertEqual(records['records'][0]['duration'],660)
            self.assertEqual(records['records'][1]['noQualifiedReason'],'演示：用时超出限制')

    def test_detail_whitelist_and_readonly_endpoints(self):
        calls=[]
        class Client:
            def post_json(self,path,*args,**kwargs):
                calls.append(path)
                if path.endswith('listXnYearXqByStudentId'):return {'data':[{'key':'测试学期','value':'test'}]}
                if path.endswith('crsReocordInfoList'):return {'data':{'rank':[{'rankList':[{'id':'example','duration':0}]}]}}
                return {'data':{'id':'example','duration':600,'recordStartTime':'2026-11-10 08:00:00','recordEndTime':'2026-11-10 08:00:04','isQualified':'2','noQualifiedReason':'测试：跑步时间不在允许时段内','studentName':'禁止展示的姓名','token':'禁止展示的凭据','pointsList':[]}}
        class Auth:
            authenticated=True
            def client(self):return Client()
        with tempfile.TemporaryDirectory() as tmp:
            records=Records(Store(Path(tmp)),Auth())
            records.query();detail=records.detail('example','test')
            self.assertEqual(detail['duration'],600)
            self.assertEqual(detail['grade']['state'],'failed')
            self.assertEqual(detail['recordEndTime'],'2026-11-10 08:00:04')
            self.assertEqual(detail['noQualifiedReason'],'测试：跑步时间不在允许时段内')
            self.assertNotIn('studentName',detail);self.assertNotIn('token',detail)
            self.assertTrue(all(p in ['/run/listXnYearXqByStudentId','/run/crsReocordInfoList','/run/crsReocordInfo'] for p in calls))

    def test_detail_must_belong_to_current_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):Records(Store(Path(tmp)),None,True).detail('arbitrary','other')
