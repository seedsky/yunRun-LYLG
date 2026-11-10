"""向已克隆的目标仓库导出源码白名单；保留 .git 与用户创建的许可证。

不复制 Python、APK、账号、抓包 CA、开发报告或临时文件。提交／推送由独立 Git 步骤完成。
"""
from pathlib import Path
import argparse
import configparser
import hashlib
import json
import shutil
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from build_release import FILES,TOOLS,LAUNCHERS
from workbench.storage import Store

ROOT=Path(__file__).resolve().parents[1]


def source_paths():
    names=list(FILES)+list(TOOLS)+['.gitignore','启动联网面板.bat','启动独立工具.bat',
        '0-检查环境.bat','1-抓token.bat','2-打开面板.bat','extras/README.md']
    paths={ROOT/name for name in names}
    for name in ['workbench','static','docs','examples','tasks_lylg','tests','LICENSES','launchers']:
        paths.update(p for p in (ROOT/name).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo'))
    return sorted(paths)


def audit(paths):
    cp=configparser.ConfigParser();cp.read(ROOT/'config.ini',encoding='utf-8-sig')
    assert not cp.get('User','token',fallback='') and not cp.get('Login','username',fallback='') and not cp.get('Login','password',fallback=''),'配置中存在个人凭据。'
    private=Store().credentials()
    needles=[v.encode() for k,v in private.items() if k in ('username','password','token') and isinstance(v,str) and len(v)>=6]
    total=0
    for path in paths:
        relative=path.relative_to(ROOT)
        assert path.is_file(),f'缺少源码文件：{relative}'
        assert not set(relative.parts)&{'data','用户数据','云运动用户数据','runtime','.test-data','.demo-data','phase1','distribution','.build'}
        assert path.suffix.lower() not in ('.apk','.exe','.dpapi','.pem','.cer','.p12')
        raw=path.read_bytes();total+=len(raw)
        if any(secret in raw for secret in needles):raise ValueError(f'文件包含本机凭据，已阻止导出：{relative}')
    return total


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('destination',type=Path);args=parser.parse_args()
    destination=args.destination.resolve()
    assert (destination/'.git').exists(),'请先克隆目标仓库，再导出，确保保留初始提交。'
    canonical=lambda p:p.read_text(encoding='utf-8-sig').replace('\r\n','\n')
    assert canonical(destination/'LICENSE')==canonical(ROOT/'LICENSE'),'目标仓库许可证有变化，未覆盖。'
    paths=source_paths();size=audit(paths)
    for source in paths:
        if source.name=='LICENSE' and source.parent==ROOT:continue  # 保留仓库已有文件，包括其本地换行方式。
        target=destination/source.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    manifest={'files':len(paths),'bytes':size,'credentials_included':False,'runtime_included':False,'apk_included':False,
        'license_sha256':hashlib.sha256((destination/'LICENSE').read_bytes()).hexdigest()}
    print(json.dumps(manifest,ensure_ascii=False))


if __name__=='__main__':main()
