"""只读跑步记录。未知状态码保留原值，允许用户对照官方 App 设置含义。"""
from datetime import datetime, timezone
import json
from .storage import read_json, write_json


def grade(data,mapping=None):
    if str(data.get('isCheat','')).upper()=='Y':return {'state':'failed','label':'未通过（服务器标记异常）'}
    for key in ('isStandard','isQualified','qualifiedStatus','statusName'):
        if data.get(key) is None:continue
        raw=str(data[key]).strip()
        # 用户已对照官方记录确认 isQualified：1 有效，2 无效；其他字段不套用此数字含义。
        default={'1':'passed','2':'failed'}.get(raw,'unknown') if key=='isQualified' else 'unknown'
        state=(mapping or {}).get(raw,default)
        if raw in ('Y','合格','通过','有效','成功'):state='passed'
        elif raw in ('N','不合格','未通过','无效','失败'):state='failed'
        labels={'passed':'有效（达标）','failed':'无效（不达标）','pending':'待审核','running':'进行中','unknown':f'未确认（状态码 {raw}）'}
        if state not in labels:state='unknown'
        return {'state':state,'label':labels.get(state,labels['unknown']),'field':key,'raw':raw}
    return {'state':'unknown','label':'服务器未提供明确成绩判定'}


FIELDS=['id','recordStartTime','recordEndTime','endTime','recordMileage','recodePace','recodeCadence','recodeDislikes',
        'duration','isQualified','isStandard','isCheat','statusName','qualifiedStatus','noQualifiedReason',
        'remake','remark','msg','warnContent','appealStatus','raName','raType']


class Records:
    def __init__(self,store,auth,demo=False):self.store,self.auth,self.demo=store,auth,demo

    def summary(self,row,term='',table=''):
        clean={k:(self.store.safe_message(row[k]) if isinstance(row[k],str) else row[k]) for k in FIELDS if isinstance(row.get(k),(str,int,float,bool))}
        clean['grade']=grade(clean,self.store.settings().get('record_status_map',{}))
        clean.update(term=term,table=table)
        return clean

    def query(self,term=None):
        if self.demo:
            terms=[{'key':'本地演示学期','value':'demo'}]
            rows=[{'id':'demo-pass','recordStartTime':'2026-11-10 07:00:00','recordEndTime':'2026-11-10 07:11:00','duration':660,'recordMileage':2,'recodePace':5.5,'recodeCadence':170,'isQualified':'1'},
                  {'id':'demo-fail','recordStartTime':'2026-11-09 08:00:00','recordEndTime':'2026-11-09 08:25:00','duration':1500,'recordMileage':2,'isQualified':'2','noQualifiedReason':'演示：用时超出限制'},
                  {'id':'demo-unknown','recordStartTime':'2026-11-08 09:00:00','recordMileage':2,'isQualified':'9'}]
            result={'terms':terms,'table':'demo','records':[self.summary(r,terms[0]['key'],'demo') for r in rows]}
        else:
            if not self.auth.authenticated:raise ValueError('请先登录再查询线上跑步记录。')
            client=self.auth.client()
            terms=client.post_json('/run/listXnYearXqByStudentId','',raise_on_business_code=True).get('data') or []
            chosen=next((t for t in terms if str(t.get('value'))==str(term)),None) if term else (terms[0] if terms else None)
            if term and not chosen:raise ValueError('学期已失效，请重新读取。')
            rows=[]
            if chosen:
                body=client.post_json('/run/crsReocordInfoList',json.dumps({'tableName':chosen['value']}),raise_on_business_code=True)
                for month in (body.get('data') or {}).get('rank') or []:rows.extend(month.get('rankList') or [])
            result={'terms':[{'key':t.get('key','未命名学期'),'value':t.get('value','')} for t in terms],
                'table':chosen.get('value','') if chosen else '',
                'records':[self.summary(r,chosen.get('key',''),chosen.get('value','')) for r in rows]}
        result['fetched_at']=datetime.now(timezone.utc).isoformat()
        result['records'].sort(key=lambda r:str(r.get('recordStartTime') or r.get('recordEndTime') or r.get('endTime') or ''),reverse=True)
        write_json(self.store.data/'records.json',result)
        return result

    def detail(self,record_id,table):
        cache=read_json(self.store.data/'records.json',{})
        item=next((r for r in cache.get('records',[]) if str(r.get('id'))==str(record_id) and r.get('table')==table),None)
        if not item:raise ValueError('请先刷新记录列表，再选择详情。')
        if self.demo:return self.summary(item,item.get('term',''),table)
        if not self.auth.authenticated:raise ValueError('请先登录。')
        data=self.auth.client().post_json('/run/crsReocordInfo',json.dumps({'id':record_id,'tableName':table}),raise_on_business_code=True).get('data') or {}
        result=self.summary(data,item.get('term',''),table);result['id']=item['id']
        result['points_count']=len(data.get('pointsList') or [])
        item.clear();item.update(result);write_json(self.store.data/'records.json',cache)
        return result
