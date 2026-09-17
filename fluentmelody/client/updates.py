"""Optional release notifications. Download pages open only after a user click."""
import re
from urllib.parse import urlsplit
import httpx

def version_tuple(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}',value):
        raise ValueError('更新版本号格式不正确。')
    return tuple(map(int,value.split('.')))

def check_update(url,current):
    parsed = urlsplit(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('更新清单必须使用 HTTPS 地址。')
    with httpx.Client(timeout=5,follow_redirects=False,trust_env=False) as client:
        with client.stream('GET',url) as response:
            response.raise_for_status()
            raw=b''
            for chunk in response.iter_bytes():
                raw+=chunk
                if len(raw)>16384:
                    raise ValueError('更新清单太大。')
    import json
    data=json.loads(raw)
    if version_tuple(data['version'])<=version_tuple(current):
        return None
    target=urlsplit(data['url'])
    if target.scheme!='https' or not target.hostname or target.username or target.password:
        raise ValueError('更新下载页需要 HTTPS 地址。')
    return {'version':data['version'],'url':data['url'],'notes':str(data.get('notes',''))[:500]}
