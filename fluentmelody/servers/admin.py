"""Local-only token administration: no management API is exposed to clients."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from fluentmelody.common.auth import Store, Error


def main(argv=None):
    parser = argparse.ArgumentParser(description="FluentMelody 服务端 token 管理（在服务器本机运行）")
    parser.add_argument("--db", required=True, help="与目标服务器配置一致的 SQLite 数据库路径")
    parser.add_argument("--kind", choices=["multiplayer", "ai"], required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    issue = commands.add_parser("issue", help="生成 token；明文只在生成时展示")
    issue.add_argument("--days", type=float, default=1)
    issue.add_argument("--credits", type=int, default=1)
    issue.add_argument("--count", type=int, default=1)
    issue.add_argument("--output", help="可选：保存 token 到指定文本文件（拒绝覆盖已有文件）")
    commands.add_parser("tokens", help="列出发行记录，隐藏 token 明文")
    commands.add_parser("devices", help="列出设备及余额")
    revoke = commands.add_parser("revoke", help="停用设备或尚未兑换的 token")
    choice = revoke.add_mutually_exclusive_group(required=True)
    choice.add_argument("--device")
    choice.add_argument("--token")
    args = parser.parse_args(argv)
    try:
        store = Store(args.db, args.kind)
        if args.command == "issue":
            if args.output and Path(args.output).exists():
                raise Error("输出文件已存在，请换一个文件名")
            codes = store.issue(args.days, args.credits, args.count)
            text = "\n".join(codes) + "\n"
            if args.output:
                with Path(args.output).open("x", encoding="utf-8") as file:
                    file.write(text)
            print(text, end="")
            return 0
        if args.command == "revoke":
            store.revoke(device_id=args.device, token=args.token)
            print("已停用。撤销已经兑换的授权，请使用 --device。")
            return 0
        data = store.list_tokens() if args.command == "tokens" else store.list_devices()
        for row in data:
            for key in ("expires_at", "created_at", "redeemed_at"):
                if row.get(key):
                    row[key + "_utc"] = datetime.fromtimestamp(row[key], timezone.utc).isoformat()
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    except (Error, ValueError) as error:
        parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
