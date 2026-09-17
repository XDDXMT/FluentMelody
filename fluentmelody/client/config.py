"""Local settings and Windows account/machine protected device credentials."""
from __future__ import annotations
import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import threading
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

VERSION = '1.2.1'
DEFAULTS = {'multiplayer_url': 'http://127.0.0.1:8765', 'ai_url': 'http://127.0.0.1:8766',
            'name': '演奏者', 'dark': False, 'update_url': '', 'check_updates': True,
            'last_file': '', 'speed': 1.0, 'hotkey_start': 'F1', 'hotkey_pause': 'F4',
            'beta_mode': False, 'ai_auto_convert': True}

def data_dir():
    p = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.local/share'))) / 'FluentMelody'
    p.mkdir(parents=True, exist_ok=True)
    return p

def machine_id():
    if os.name == 'nt':
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography',
                            0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            return winreg.QueryValueEx(key, 'MachineGuid')[0]
    return platform.node()

def protect(data: bytes, decrypt=False):
    if os.name != 'nt':
        return data
    class Blob(ctypes.Structure):
        _fields_ = [('size', ctypes.c_uint32), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buf = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    if decrypt:
        ok = crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        ok = crypt.CryptProtectData(ctypes.byref(source), 'FluentMelody device', None, None, None, 1, ctypes.byref(target))
    if not ok:
        raise OSError('无法读取本机凭据；请使用兑换时的 Windows 账户和电脑。')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))

def atomic_write(path, data):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_bytes(data)
    if os.name != 'nt':
        tmp.chmod(0o600)
    tmp.replace(path)

class Config:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else data_dir()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'settings.json'
        self.values = dict(DEFAULTS)
        self.warnings = []
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text('utf-8'))
                if not isinstance(loaded,dict):
                    raise ValueError('设置文件必须为对象')
                self.values.update({k:v for k,v in loaded.items() if k in DEFAULTS})
            except (ValueError, OSError):
                self.warnings.append('设置文件无法读取，已使用默认设置。')
        if type(self.values['beta_mode']) is not bool:
            self.values['beta_mode'] = False
            self.warnings.append('Beta 模式设置无效，已关闭实验功能。')
        if type(self.values['ai_auto_convert']) is not bool:
            self.values['ai_auto_convert'] = True
            self.warnings.append('AI 自动改编转换设置无效，已恢复默认开启。')
        from ..core.hotkeys import validate_bindings
        try:
            bindings = validate_bindings({1:self.values['hotkey_start'],4:self.values['hotkey_pause']})
        except (ValueError,TypeError):
            bindings = {1:'F1',4:'F4'}
            self.warnings.append('保存的快捷键无效，已恢复 F1 / F4。')
        self.values.update(hotkey_start=bindings[1],hotkey_pause=bindings[4])

    def save(self):
        atomic_write(self.path, json.dumps(self.values, ensure_ascii=False, indent=2).encode('utf-8'))

class Identity:
    def __init__(self, directory=None):
        self.path = (Path(directory) if directory else data_dir()) / 'device.dat'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        fingerprint = hashlib.sha256(machine_id().encode()).hexdigest()
        if self.path.exists():
            self.vault = json.loads(protect(self.path.read_bytes(), True))
            if self.vault['machine'] != fingerprint:
                raise ValueError('凭据已绑定另一台电脑，不能迁移使用。')
        else:
            key = Ed25519PrivateKey.generate()
            private = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                        serialization.NoEncryption())
            self.vault = {'machine': fingerprint, 'private': base64.b64encode(private).decode(), 'sessions': {}}
            self.save()
        self.key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(self.vault['private']))
        pub = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.public_key = base64.b64encode(pub).decode()
        self.device_id = hashlib.sha256((fingerprint + self.public_key).encode()).hexdigest()

    def save(self):
        with self.lock:
            atomic_write(self.path, protect(json.dumps(self.vault).encode()))

    def session(self, url):
        with self.lock:
            return self.vault['sessions'].get(url, '')

    def set_session(self, url, token):
        with self.lock:
            self.vault['sessions'][url] = token
            self.save()
