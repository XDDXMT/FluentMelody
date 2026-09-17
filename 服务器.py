"""The same executable launches either independent server or the local admin tool."""
import sys

def main():
    if len(sys.argv)<2 or sys.argv[1] not in ('multiplayer','ai','admin'):
        print('用法：服务器.exe multiplayer [参数] | ai --config ai.json | admin --db 数据库 --kind multiplayer/ai issue [参数]')
        return 0
    command = sys.argv.pop(1)
    if command=='multiplayer':
        from fluentmelody.servers.multiplayer import main as start
    elif command=='ai':
        from fluentmelody.servers.ai import main as start
    else:
        from fluentmelody.servers.admin import main as start
    return start()

if __name__=='__main__':
    raise SystemExit(main())
