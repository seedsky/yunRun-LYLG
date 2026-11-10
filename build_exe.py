"""构建 Windows 单文件 EXE。只收录明确资源，不扫描或复制本机用户数据。"""
from pathlib import Path
import configparser
import hashlib
import json
import subprocess
import sys
from workbench import __version__

ROOT=Path(__file__).resolve().parent
NAME=f'yunRun-LYLG-v{__version__}'


def main():
    if sys.platform!='win32':raise SystemExit('请在 Windows 上构建 Windows EXE。')
    cp=configparser.ConfigParser();cp.read(ROOT/'config.ini',encoding='utf-8-sig')
    assert not cp.get('User','token',fallback='') and not cp.get('Login','username',fallback='') and not cp.get('Login','password',fallback=''),'构建仅接受空白账号配置。'
    output=ROOT/'distribution'/'exe';work=ROOT/'.build'/'exe'
    output.mkdir(parents=True,exist_ok=True);work.mkdir(parents=True,exist_ok=True)
    version=tuple(int(x) for x in __version__.split('.'))+(0,)
    version_file=work/'version-info.txt'
    version_file.write_text(f'''VSVersionInfo(ffi=FixedFileInfo(filevers={version!r},prodvers={version!r},mask=0x3f,flags=0x0,OS=0x40004,fileType=0x1,subtype=0x0,date=(0,0)),kids=[StringFileInfo([StringTable('080404B0',[StringStruct('CompanyName','SpringSoap / seedsky'),StringStruct('FileDescription','云运动 · 洛阳理工本地工作台'),StringStruct('FileVersion','{__version__}'),StringStruct('ProductVersion','{__version__}'),StringStruct('ProductName','yunRun-LYLG'),StringStruct('OriginalFilename','{NAME}.exe')])]),VarFileInfo([VarStruct('Translation',[2052,1200])])])''',encoding='utf-8')
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--onefile','--console','--noupx',
        '--name',NAME,'--distpath',str(output),'--workpath',str(work/'work'),'--specpath',str(work),
        '--version-file',str(version_file),'--collect-all','mitmproxy','--collect-all','gmssl','--collect-all','Crypto',
        '--hidden-import','mitmproxy_rs','--recursive-copy-metadata','mitmproxy','--copy-metadata','gmssl',
        '--copy-metadata','pycryptodome','--copy-metadata','requests','--copy-metadata','Pillow']
    for item in ['static','examples','tasks_lylg','extras','config.ini','README.md','LICENSE','NOTICE.md','LICENSES']:
        path=ROOT/item
        if path.exists():command+=['--add-data',str(path)+':'+(item if path.is_dir() else '.')]
    command+=['--add-data',str(ROOT/'tools/capture_bridge.py')+':tools',str(ROOT/'web.py')]
    subprocess.run(command,cwd=ROOT,check=True)
    exe=output/(NAME+'.exe')
    report={'version':__version__,'file':exe.name,'bytes':exe.stat().st_size,'sha256':hashlib.sha256(exe.read_bytes()).hexdigest(),'personal_data_included':False}
    (output/'exe-manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
