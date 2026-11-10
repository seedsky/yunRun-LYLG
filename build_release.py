"""可审查的发行白名单：只打包代码、公开资源、空白配置和内置环境。

data/、阶段一报告、历史记录、抓包 CA、构建目录均无入口进入发行包。
"""
from pathlib import Path
import hashlib
import json
import zipfile
from workbench import __version__

ROOT=Path(__file__).resolve().parent
VERSION=__version__
FILES=['web.py','main.py','history.py','taskgen.py','yun_route.py','yun_http.py','yun_face.py',
       'requirements.txt','requirements-build.txt','config.ini','README.md','AGENTS.md','build_release.py','build_exe.py','LICENSE','NOTICE.md','dry_run_home.json',
       '_launch.bat','.gitignore','启动联网面板.bat','启动独立工具.bat','0-检查环境.bat','1-抓token.bat','2-打开面板.bat']
DIRECTORIES=['workbench','static','docs','examples','tasks_lylg','runtime','extras','tests','LICENSES']
TOOLS=['tools/capture_bridge.py','tools/check_environment.py','tools/verify_exe.py','tools/verify_portable.py','tools/export_source.py','tools/__init__.py']
LAUNCHERS=['启动联网面板.bat','启动独立工具.bat','抓包备用登录.bat','检查运行环境.bat']


def main():
    assert (ROOT/'runtime/python.exe').is_file(),'缺少内置环境；请先准备 runtime/python.exe，避免生成不完整的免安装包。'
    target=ROOT/'distribution'/f'yunRun-LYLG-v{VERSION}-内置环境.zip'
    target.parent.mkdir(exist_ok=True)
    paths=[ROOT/name for name in FILES+TOOLS]
    for name in DIRECTORIES:
        paths.extend(path for path in (ROOT/name).rglob('*') if path.is_file()
                     and '__pycache__' not in path.parts and path.suffix not in ('.pyc','.pyo'))
    paths=sorted(set(paths))
    import configparser
    config=configparser.ConfigParser();config.read(ROOT/'config.ini',encoding='utf-8-sig')
    assert not config.get('User','token',fallback='')
    assert not config.get('Login','username',fallback='')
    assert not config.get('Login','password',fallback='')
    assert all(path.is_file() for path in paths),'发行文件缺失。'
    entries={Path('程序文件')/path.relative_to(ROOT):path for path in paths}
    entries.update({Path('启动入口')/name:ROOT/'launchers'/name for name in LAUNCHERS})
    entries[Path('使用说明.txt')]=ROOT/'docs'/'PORTABLE_START.txt'
    # 保留用于自行重新打包的源启动脚本；不在用户启动入口里暴露内部辅助脚本。
    entries.update({Path('程序文件')/'launchers'/name:ROOT/'launchers'/name for name in LAUNCHERS})
    assert all(path.is_file() for path in entries.values()),'发行入口缺失。'
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name,path in sorted(entries.items()):
            relative=path.relative_to(ROOT)
            assert not any(part in ('data','.demo-data','phase1','captures','history_routes','work_dir') for part in relative.parts)
            if path.suffix.lower() in ('.bat','.cmd'):
                # 批处理统一 UTF-8 无 BOM 与 CRLF，避免 Windows cmd 对换行的兼容问题。
                body=path.read_text(encoding='utf-8-sig').replace('\r\n','\n').replace('\n','\r\n')
                archive.writestr(name.as_posix(),body.encode('utf-8'))
            else:archive.write(path,name.as_posix())
    digest=hashlib.sha256(target.read_bytes()).hexdigest()
    manifest={'version':VERSION,'file':target.name,'bytes':target.stat().st_size,'sha256':digest,'files':len(entries),
              'personal_data_included':False}
    (target.parent/'release-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(manifest,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
