"""抓包桥接：仅提取目标服务的 token/设备字段，直接进行用户级加密保存。

不保存完整 headers、请求体、密码或流量文件；证书与端口由 Capture 管理。
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workbench.storage import Store, write_json


class CaptureToken:
    def request(self,flow):
        host=flow.request.pretty_host.lower()
        if not (host=='aiyyd.com' or host.endswith('.aiyyd.com') or host=='yzhiee.com' or host.endswith('.yzhiee.com')):return
        token=flow.request.headers.get('token','')
        if not token:return
        store=Store()
        # 即使与当前 token 相同也要报告捕获成功。候选先隔离加密，身份由后台只读验证。
        store.stage_capture({'token':token,
              'device_id':flow.request.headers.get('deviceId',''),
              'device_name':flow.request.headers.get('deviceName','Android'),
              'sys_version':flow.request.headers.get('sysVersion','14')})
        write_json(store.data/'capture-state.json',{'captured':True})


addons=[CaptureToken()]
