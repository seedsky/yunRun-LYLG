"""启动本地工作台；同一后台同时提供联网面板和独立工具。"""
import argparse
import json
import os
from pathlib import Path
import sys
import webbrowser
from http.server import ThreadingHTTPServer
from workbench import __version__


def main():
    sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    sys.stderr.reconfigure(encoding='utf-8',errors='replace')
    if '--capture-worker' in sys.argv:
        sys.argv.remove('--capture-worker')
        from mitmproxy.tools.main import mitmdump
        return mitmdump()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version',action='version',version=__version__)
    parser.add_argument('--mode',choices=['online','offline'],default='online')
    parser.add_argument('--port',type=int,default=8090)
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--page',choices=['work','capture'],default='work')
    parser.add_argument('--demo',action='store_true',help='本地演示与 UI 验证，不连接学校服务')
    parser.add_argument('--worker')
    parser.add_argument('--capture-worker',action='store_true')
    args,unknown=parser.parse_known_args()
    if args.worker:
        from workbench.jobs import run_worker
        return run_worker(args.worker)
    if args.capture_worker:
        sys.argv=[sys.argv[0]]+unknown
        from mitmproxy.tools.main import mitmdump
        return mitmdump()
    from workbench.storage import ROOT,APP_DIR,Store,read_json,write_json
    if args.demo and 'YUNRUN_DATA_DIR' not in os.environ:os.environ['YUNRUN_DATA_DIR']=str(APP_DIR/'.demo-data')
    # Store 默认参数在 import 时确定，演示模式必须显式传入目录，不能读取个人 data/。
    store=Store(Path(os.environ['YUNRUN_DATA_DIR'])) if os.environ.get('YUNRUN_DATA_DIR') else Store()
    lockfile=store.data/'server.json'
    existing=read_json(lockfile,{})
    if existing:
        try:
            import urllib.request
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            req=urllib.request.Request(f'http://127.0.0.1:{int(existing["port"])}/api/jobs',headers={'X-Workbench-Token':existing['key']})
            with opener.open(req,timeout=1) as response: valid=response.status==200
            if valid:
                url=f'http://127.0.0.1:{existing["port"]}'+('/offline' if args.mode=='offline' else ('/capture' if args.page=='capture' else '/'))
                print('后台已启动：'+url)
                if not args.no_browser:webbrowser.open(url)
                return
        except Exception:pass
    from workbench.server import Application,make_handler
    app=Application(store,demo=args.demo)
    server=None
    for port in range(args.port,args.port+30):
        try:server=ThreadingHTTPServer(('127.0.0.1',port),make_handler(app));break
        except OSError:continue
    if server is None:raise RuntimeError('本地面板端口均被占用。')
    write_json(lockfile,{'port':server.server_port,'key':app.key,'pid':os.getpid()})
    url=f'http://127.0.0.1:{server.server_port}'+('/offline' if args.mode=='offline' else ('/capture' if args.page=='capture' else '/'))
    print('云运动 · 洛阳理工 工作台\n'+url)
    print('关闭网页不会停止任务；关闭本控制台将退出程序并结束子进程。')
    if args.demo:print('当前为演示模式，禁止连接学校服务或正式上传。')
    if not args.no_browser:webbrowser.open(url)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        app.family.close();server.server_close();lockfile.unlink(missing_ok=True)


if __name__=='__main__':main()
