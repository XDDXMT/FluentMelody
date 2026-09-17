"""Small signed HTTP client. Each request uses a new nonce; redirects are disabled."""
from __future__ import annotations
import base64
import hashlib
import ipaddress
import json
import threading
import time
import uuid
from urllib.parse import urlsplit
import httpx

class ServiceError(Exception):
    pass

def normalize_url(value):
    value = value.strip().rstrip('/')
    parsed = urlsplit(value)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('服务器地址需要完整的 http:// 或 https:// 地址，不能包含账号密码。')
    if parsed.path not in ('','/') or parsed.query or parsed.fragment:
        raise ValueError('服务器地址请只填写域名和端口，不要填写接口路径。')
    if parsed.scheme == 'http' and parsed.hostname not in ('localhost',):
        try:
            local = ipaddress.ip_address(parsed.hostname).is_private
        except ValueError:
            local = False
        if not local:
            raise ValueError('公网服务器请使用 HTTPS；HTTP 只用于本机和内网。')
    return value

class API:
    def __init__(self, url, identity):
        self.url = normalize_url(url)
        self.identity = identity
        self.offset = 0.0
        self.rtt = 0.0
        self.clock_lock = threading.Lock()
        self.client = httpx.Client(timeout=httpx.Timeout(8, connect=3), follow_redirects=False, trust_env=False)

    def close(self):
        self.client.close()

    def request(self, method, path, data=None, *, binary=False, headers=None, signed=True):
        body = b'' if data is None else json.dumps(data, ensure_ascii=False, separators=(',',':'), allow_nan=False).encode()
        req_headers = {'Content-Type':'application/json'}
        if signed:
            stamp, nonce = str(time.time()+self.offset), uuid.uuid4().hex
            canonical = '\n'.join((method.upper(),urlsplit(path).path,stamp,nonce,hashlib.sha256(body).hexdigest())).encode()
            req_headers.update({'X-Device-ID':self.identity.device_id, 'X-Timestamp':stamp, 'X-Nonce':nonce,
                                'X-Signature':base64.b64encode(self.identity.key.sign(canonical)).decode()})
            token = self.identity.session(self.url)
            if token:
                req_headers['Authorization'] = 'Bearer '+token
        req_headers.update(headers or {})
        try:
            with self.client.stream(method, self.url+path, content=body, headers=req_headers) as response:
                chunks, total = [], 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > 12*1024*1024:
                        raise ServiceError('服务器响应超过大小限制。')
                    chunks.append(chunk)
                raw = b''.join(chunks)
                if not 200 <= response.status_code < 300:
                    try:
                        obj = json.loads(raw)
                        detail = obj.get('detail', obj.get('error','请求失败'))
                        if not isinstance(detail,str):
                            detail = '请求参数不正确'
                    except (ValueError, AttributeError):
                        detail = '服务器返回了异常响应'
                    raise ServiceError(f'{detail[:240]}（{response.status_code}）')
                return raw if binary else json.loads(raw)
        except httpx.HTTPError as exc:
            raise ServiceError('无法连接服务器，请检查地址和网络。') from exc
        except ValueError as exc:
            raise ServiceError('服务器返回格式不正确。') from exc

    def sync_clock(self):
        best = None
        for _ in range(3):
            begin = time.time()
            result = self.request('GET','/time',signed=False)
            end = time.time()
            item = (end-begin, float(result['server_time'])-(begin+end)/2)
            if best is None or item[0] < best[0]:
                best = item
        with self.clock_lock:
            self.rtt, self.offset = best
        return {'latency_ms':round(self.rtt*500), 'offset':self.offset}

    def redeem(self, token):
        self.sync_clock()
        result = self.request('POST','/redeem', {'token':token.strip(), 'device_id':self.identity.device_id,
                                               'public_key':self.identity.public_key})
        self.identity.set_session(self.url, result['access_token'])
        return result

    def account(self):
        self.sync_clock()
        return self.request('GET','/me')
