"""发行布局实测：解压到含空格与中文的临时目录；只运行本地演示，禁止真实上传。"""
import json
import os
import re
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def main():
    manifest=json.loads((ROOT/'distribution/release-manifest.json').read_text(encoding='utf-8'))
    archive_path=ROOT/'distribution'/manifest['file']
    base=(ROOT/'.test-data').resolve();base.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='便携 验收 ',dir=base) as temp:
        folder=Path(temp).resolve()
        assert folder.is_relative_to(base)
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.testzip() is None
            names=archive.namelist()
            assert {p.split('/')[0] for p in names}=={'程序文件','启动入口','使用说明.txt'}
            assert all(not {'data','.test-data','.demo-data','phase1','captures','history_routes','work_dir','__pycache__'}.intersection(Path(p).parts) for p in names)
            archive.extractall(folder)
        program=folder/'程序文件';python=program/'runtime/python.exe';data=folder/'用户数据'
        env=os.environ.copy();env.pop('YUNRUN_DATA_DIR',None);env['PYTHONUTF8']='1'
        result=subprocess.run([str(python),'-X','utf8','-c',
            'from workbench.storage import Store; print(Store().data)'],cwd=program,env=env,
            capture_output=True,text=True,encoding='utf-8',check=True)
        assert Path(result.stdout.strip())==data
        assert data.is_dir() and not (program/'data').exists()
        # 使用空白、隔离的数据目录启动演示，不读取源工程个人凭据。
        env['YUNRUN_DATA_DIR']=str(data)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        output=folder/'smoke.log';controller=None;pid=None
        try:
            with output.open('wb') as log:
                launcher=folder/'启动入口/启动联网面板.bat'
                controller=subprocess.Popen(f'cmd.exe /d /s /c ""{launcher}" --demo --no-browser --port 8150"',
                    cwd=base,env=env,stdout=log,stderr=log,stdin=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW)
            deadline=time.monotonic()+20
            while not (data/'server.json').exists():
                if controller.poll() is not None:raise AssertionError(output.read_text(encoding='utf-8',errors='replace'))
                if time.monotonic()>deadline:raise AssertionError('启动超时')
                time.sleep(.1)
            server=json.loads((data/'server.json').read_text(encoding='utf-8'));pid=server['pid']
            url=f'http://127.0.0.1:{server["port"]}'
            def request(path,body=None,mode='online'):
                headers={'X-Workbench-Token':server['key'],'X-Workbench-Mode':mode}
                payload=None if body is None else json.dumps(body).encode()
                with opener.open(urllib.request.Request(url+path,data=payload,headers=headers),timeout=10) as response:
                    return json.loads(response.read())
            assert request('/api/state')['demo'] is True
            records=request('/api/records/query',{})['records']
            assert [r['grade']['state'] for r in records]==['passed','failed','unknown']
            assert [r['grade']['raw'] for r in records]==['1','2','9']
            assert records[1]['noQualifiedReason']=='演示：用时超出限制'
            for path,tail in [('启动独立工具.bat','/offline'),('抓包备用登录.bat','/capture')]:
                script=folder/'启动入口'/path
                proc=subprocess.run(f'cmd.exe /d /s /c ""{script}" --demo --no-browser --port 8150"',
                    cwd=base,env=env,capture_output=True,text=True,encoding='utf-8',timeout=15)
                assert proc.returncode==0,(path,proc.stdout,proc.stderr)
                assert url+tail in proc.stdout,(path,proc.stdout)
            script=folder/'启动入口/检查运行环境.bat'
            proc=subprocess.run(f'cmd.exe /d /s /c ""{script}""',cwd=base,env=env,
                input='\n',capture_output=True,text=True,encoding='utf-8',timeout=20)
            assert proc.returncode==0 and '环境完整。' in proc.stdout,proc.stdout+proc.stderr
            try:request('/api/jobs/start',{'dry':False})
            except urllib.error.HTTPError as exc:assert exc.code==400
            else:raise AssertionError('演示模式未拒绝正式执行')
            assert not (data/'credentials.dpapi').exists()
            assert not (program/'data').exists()
        finally:
            if pid:
                try:os.kill(pid,signal.SIGTERM)
                except ProcessLookupError:pass
            if controller:
                try:controller.wait(timeout=10)
                except subprocess.TimeoutExpired:controller.terminate();controller.wait(timeout=10)
        # 内置环境从新布局运行全部回归，尤其验证 worker 的源码和资源相对路径。
        proc=subprocess.run([str(python),'-X','utf8','-m','unittest','discover','-s','tests','-q'],
            cwd=program,env=env,capture_output=True,text=True,encoding='utf-8',timeout=60)
        assert proc.returncode==0,proc.stdout+proc.stderr
        print(json.dumps({'zip_crc':'通过','layout':'通过','launchers':4,
            'working_directory':'含中文与空格，且与程序目录不同','data_directory':'用户数据',
            'records':'1有效，2无效，9未确认','packaged_tests':int(re.search(r'Ran (\d+) tests',proc.stderr).group(1)),'real_uploads':0},ensure_ascii=False))


if __name__=='__main__':main()
