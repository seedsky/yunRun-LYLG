"""只检查运行依赖，不联网、不读取账号、不安装证书。"""
import importlib
import sys
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
failed=[]
for name in ['requests','gmssl.sm2','gmssl.sm4','Crypto.Cipher.AES','mitmproxy','PIL']:
    try:importlib.import_module(name);print('通过：'+name)
    except ImportError:failed.append(name);print('缺失：'+name)
print('环境完整。' if not failed else '请按 README 的依赖安装步骤修复开发环境，或重新解压内置环境包。')
raise SystemExit(bool(failed))
