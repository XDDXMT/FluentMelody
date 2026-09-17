"""Build portable Windows releases from this source directory."""
from pathlib import Path
import os
import shutil
import sys
import PyInstaller.__main__
from fluentmelody.client.config import VERSION

ROOT=Path(__file__).resolve().parent
DIST=ROOT/f'release-{VERSION}'
WORK=ROOT/'build'/'pyinstaller'

def build():
    assert DIST.resolve().is_relative_to(ROOT.resolve()) and DIST!=ROOT
    DIST.mkdir(exist_ok=True)
    # Bundled PDF tools may put a different ICU on PATH. Qt must resolve the
    # Windows system ICU used by the installed PySide6, not Poppler's ICU.
    system_root=Path(os.environ.get('SystemRoot',r'C:\Windows'))
    os.environ['PATH']=os.pathsep.join((str(Path(sys.executable).parent),
                                      str(system_root/'System32'),str(system_root)))
    common=['--noconfirm','--clean','--paths',str(ROOT),'--distpath',str(DIST),
            '--workpath',str(WORK),'--specpath',str(WORK)]
    PyInstaller.__main__.run([*common,'--name','FluentMelody','--onedir','--windowed','--uac-admin',
        '--hidden-import','fluentpy.assets','--hidden-import','fluentpy.assets.icons',
        '--add-data',str(ROOT/'fluentpy'/'assets')+';fluentpy/assets',
        '--add-data',str(ROOT/'assets')+';assets',
        '--exclude-module','numpy.f2py','--exclude-module','numpy.testing',
        '--exclude-module','matplotlib','--exclude-module','torch',
        str(ROOT/'启动客户端.py')])
    PyInstaller.__main__.run([*common,'--name','FluentMelodyServer','--onefile','--console',
        '--collect-submodules','uvicorn','--exclude-module','PySide6',
        '--add-data',str(ROOT/'assets'/'models')+';assets/models',
        '--exclude-module','numpy.f2py','--exclude-module','numpy.testing',
        '--exclude-module','matplotlib','--exclude-module','torch',str(ROOT/'服务器.py')])
    for p in ('README.md','服务器与token说明.md','AI服务器说明.md','ai.example.json',
              'LICENSE','FluentPy改动说明.md','本地旋律识别说明.md','dependency-versions.txt'):
        shutil.copy2(ROOT/p,DIST/p)
    shutil.copytree(ROOT/'licenses',DIST/'licenses',dirs_exist_ok=True)
    shutil.copytree(ROOT/'assets',DIST/'示例歌曲',dirs_exist_ok=True,ignore=shutil.ignore_patterns('models'))
    for p in ('验证记录.md','更新说明.md'):
        if (ROOT/p).exists():
            shutil.copy2(ROOT/p,DIST/p)
    scripts={
       '启动联机服务.bat':'FluentMelodyServer.exe multiplayer --db data/multiplayer.sqlite3 --host 127.0.0.1 --port 8765',
       '启动AI服务.bat':'FluentMelodyServer.exe ai --config ai.example.json',
       '生成一天联机token.bat':'FluentMelodyServer.exe admin --db data/multiplayer.sqlite3 --kind multiplayer issue --days 1 --count 1',
       '生成AI额度token.bat':'FluentMelodyServer.exe admin --db data/ai.sqlite3 --kind ai issue --credits 20 --count 1',
    }
    for name,command in scripts.items():
        (DIST/name).write_text('@echo off\nchcp 65001 >nul\ncd /d "%~dp0"\n'+command+'\npause\n',encoding='utf-8')
    size = sum(p.stat().st_size for p in DIST.rglob('*') if p.is_file())
    if size > 200_000_000:
        raise RuntimeError(f'发布目录 {size:,} 字节，超过 200 MB 限制。')
    print('Built:',DIST,'bytes:',size)

if __name__=='__main__':
    build()
