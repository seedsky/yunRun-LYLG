"""数据路径、原子写入、Windows 用户级凭据保护。

可分发资源位于项目根；开发数据位于 data/，便携包数据位于同级的“用户数据”。
两种布局的数据目录均由发行脚本明确排除。
密码和 token 只在内存及 credentials.dpapi 中存在，不写回 config.ini。
"""
from __future__ import annotations
import configparser
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import sys
import threading

FROZEN = bool(getattr(sys, 'frozen', False))
# 单文件 EXE 的资源由 PyInstaller 解压到 _MEIPASS，只读资源与持久数据必须分开。
ROOT = Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1])).resolve()
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else ROOT
def default_data_dir(root: Path) -> Path:
    """EXE 数据写在程序旁；便携源码包外置到顶层，均不进入临时资源目录。"""
    if FROZEN:return APP_DIR/'云运动用户数据'
    if root.name == '程序文件' and (root.parent / '启动入口').is_dir():
        return root.parent / '用户数据'
    return root / 'data'


DATA = Path(os.environ.get('YUNRUN_DATA_DIR') or default_data_dir(ROOT)).resolve()
LOCK = threading.RLock()
DEFAULTS = {'theme': 'system', 'account_name': '', 'task_id': '', 'max_minutes': 20, 'record_status_map': {},
            'defaults': {'source': 'synth', 'km': 2, 'pace': 5.5, 'cadence': 170,
                         'amp': 1, 'sample_seconds': 2, 'seed': '', 'deform': False}}


def atomic_bytes(path: Path, value: bytes):
    """同一文件的写入串行化，先落盘再替换，避免进程中断留下半个 JSON。"""
    with LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        pending = path.with_name(path.name + '.pending')
        pending.write_bytes(value)
        pending.replace(path)


def write_json(path: Path, obj):
    atomic_bytes(path, json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8'))


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (ValueError, OSError) as exc:
        raise ValueError(f'{path.name} 损坏或无法读取，请从备份恢复。') from exc


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(raw: bytes, decrypt=False) -> bytes:
    if os.name != 'nt':
        raise RuntimeError('本版本的凭据保存需要 Windows 用户加密服务。')
    buf = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    source = Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        fn = crypt.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p,
                       ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        second = None
    else:
        fn = crypt.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.POINTER(Blob), ctypes.c_void_p,
                       ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        second = 'yunrun-local-credentials'
    fn.restype = wintypes.BOOL
    # 仅 CRYPTPROTECT_UI_FORBIDDEN，不使用 LOCAL_MACHINE。
    if not fn(ctypes.byref(source), second, None, None, None, 1, ctypes.byref(target)):
        raise ValueError('无法读取本机凭据：可能已更换电脑或 Windows 用户，请重新登录。')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        if decrypt:
            ctypes.memset(target.data, 0, target.size)
        kernel.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))
        ctypes.memset(buf, 0, len(raw))


class Store:
    def __init__(self, data=DATA):
        self.data = Path(data)
        self.data.mkdir(parents=True, exist_ok=True)
        self.credentials_path = self.data / 'credentials.dpapi'

    def credentials(self):
        with LOCK:
            if not self.credentials_path.exists(): return {}
            return json.loads(_crypt(self.credentials_path.read_bytes(), True))

    def save_credentials(self, value):
        atomic_bytes(self.credentials_path, _crypt(json.dumps(value, ensure_ascii=False).encode('utf-8')))

    def pending_capture(self):
        """捕获的候选会话也使用 DPAPI；验证前不覆盖正在使用的账号。"""
        path=self.data/'captured-session.dpapi'
        with LOCK:
            return json.loads(_crypt(path.read_bytes(),True)) if path.exists() else {}

    def stage_capture(self,value):
        atomic_bytes(self.data/'captured-session.dpapi',_crypt(json.dumps(value,ensure_ascii=False).encode('utf-8')))

    def clear_credentials(self):
        with LOCK:
            self.credentials_path.unlink(missing_ok=True)
            (self.data/'captured-session.dpapi').unlink(missing_ok=True)
            (self.data / 'rules.json').unlink(missing_ok=True)
            (self.data / 'records.json').unlink(missing_ok=True)

    def settings(self):
        result = json.loads(json.dumps(DEFAULTS))
        saved = read_json(self.data / 'settings.json', {})
        result.update({k: v for k, v in saved.items() if k in DEFAULTS and k != 'defaults'})
        result['defaults'].update(saved.get('defaults', {}))
        return result

    def save_settings(self, changes):
        with LOCK:
            value = self.settings()
            value.update({k: v for k, v in changes.items() if k in DEFAULTS})
            if not isinstance(value.get('defaults'),dict): raise ValueError('默认路线参数格式不正确。')
            value['defaults']={k:v for k,v in value['defaults'].items() if k in DEFAULTS['defaults']}
            if value['theme'] not in ('system', 'light', 'dark'): raise ValueError('未知主题。')
            mapping=value.get('record_status_map',{})
            if not isinstance(mapping,dict) or any(v not in ('passed','failed','pending','running','unknown') for v in mapping.values()):raise ValueError('成绩状态码映射不正确。')
            limit = float(value['max_minutes'])
            if not 0 < limit <= 1440: raise ValueError('本地时长上限应在 0～1440 分钟之间。')
            value['account_name']=str(value.get('account_name',''))[:100]
            value['task_id']=str(value.get('task_id',''))[:100]
            write_json(self.data / 'settings.json', value)
            return value

    def config(self):
        cp = configparser.ConfigParser()
        cp.read(ROOT / 'config.ini', encoding='utf-8-sig')
        override = read_json(self.data / 'advanced.json', {})
        for sec, values in override.items():
            if sec not in ('Yun', 'User', 'Run', 'Panel'): continue
            for key, value in values.items():
                if key.lower() in ('token', 'password', 'username'): continue
                if not cp.has_section(sec): cp.add_section(sec)
                cp.set(sec, key, str(value))
        return cp

    def safe_message(self, value):
        text = str(value)
        try: secrets = list(self.credentials().values())+list(self.pending_capture().values())
        except ValueError: secrets = []
        cp = self.config()
        for section in cp.sections():
            for key, val in cp.items(section):
                if key.lower() in ('token', 'password', 'privatekey', 'cipherkey', 'cipherkeyencrypted', 'md5key'):
                    secrets.append(val)
        for secret in sorted((s for s in secrets if isinstance(s, str) and len(s) >= 4), key=len, reverse=True):
            text = text.replace(secret, '[已隐藏]')
        text = re.sub(r'\b1\d{10}\b', '[手机号已隐藏]', text)
        return text[:1800]

