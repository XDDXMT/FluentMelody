# 联机服务器与 token

联机服务器和 AI 编曲服务器分别运行，分别设置地址、数据库和 token。客户端单机演奏不消耗联机授权。此版本提供发行与核销工具，不连接支付平台；售价、收款和 token 交付由运营者决定。

以下命令在本目录运行。首次安装按主说明安装 Python 依赖；也可使用发布包提供的启动脚本。

## 启动联机服务器

```powershell
python -m fluentmelody.servers.multiplayer --db data/multiplayer.sqlite3 --host 127.0.0.1 --port 8765
```

本机测试时，客户端的联机地址填写 `http://127.0.0.1:8765`。局域网使用 `--host 0.0.0.0` 监听，客户端填写服务器局域网地址。公网部署在 HTTPS 反向代理后，客户端填写 HTTPS 地址。每份数据库只启动一个联机进程，因为房间状态保存在进程内存；重启会关闭现有房间，授权余额仍保留。

## 发行、查看、停用

生成十个“一人一天”的联机 token：

```powershell
python -m fluentmelody.servers.admin --db data/multiplayer.sqlite3 --kind multiplayer issue --days 1 --count 10 --output data/一天十人.txt
```

`--days` 支持小数，测试时可使用 `0.01`。明文 token 只在发行时输出，数据库只保存摘要；请保存发行结果。`--output` 不覆盖已有文件。一个 token 发给一位用户，六人合奏分别需要六位用户各自的授权。

```powershell
python -m fluentmelody.servers.admin --db data/multiplayer.sqlite3 --kind multiplayer tokens
python -m fluentmelody.servers.admin --db data/multiplayer.sqlite3 --kind multiplayer devices
python -m fluentmelody.servers.admin --db data/multiplayer.sqlite3 --kind multiplayer revoke --device 设备ID
```

也可用 `revoke --token 原token` 停用兑换码；已兑换时长的停用应使用 `--device`。管理命令只在服务器本机操作数据库，没有对外管理接口。

AI 额度 token 使用另一份数据库，例如：

```powershell
python -m fluentmelody.servers.admin --db data/ai.sqlite3 --kind ai issue --credits 20 --count 1
```

AI 服务器配置的数据库路径必须与发行命令相同。两种服务器不能共用一份数据库，程序会拒绝混用。

## 计时与绑定规则

- 第一次兑换立刻开始倒计时，离线、关闭程序、未演奏时仍计时。
- 新 token 兑换到同一机器时，在“当前时间”和“原到期时间”较晚的一项后追加时长，避免浪费已有余额。
- 已兑换 token 再次在原机器兑换，只恢复访问凭据，不重新开始计时，也不重复增加 AI 额度。其他机器兑换同一 token 会失败。
- 请求需要设备私钥签名，并包含一次性随机数和时间戳。复制访问凭据无法直接在别的设备使用。机器绑定由客户端本机凭据保护与服务端公钥绑定共同实现；重装系统或丢失私钥不会自动恢复旧设备身份，需运营者处理。
- 服务器时间决定到期时间，不使用客户端上报的余额。应保持服务器系统时间准确。

## 合奏流程

1. 每个人分别兑换自己的联机 token。
2. 房主创建房间，其他人用六位连接码加入，最多八人。
3. 房主上传 MIDI 或 NBS。服务器按照当前人数分配每人的单音轨，检查相邻音符在松开后至少间隔 0.1 秒。
4. 每个人按 F1 准备。全部准备后，服务器下发同一开始时间，倒计时三秒统一开始。
5. 房主按 F4 暂停或继续全房间，继续也有统一倒计时。
6. 成员心跳超过三秒或授权失效时，全房间暂停并显示原因。恢复连接不会自动继续；房主确认后再继续。客户端还可因本地故障请求全房间暂停。

成员加入、退出会重新分配歌曲并清除准备状态。演奏中需先暂停才能加入新成员。房主主动退出时，房主身份交给房间中下一位成员。房间所有成员一小时没有心跳则自动清理。

上传仅接收 MIDI/NBS，单文件最大 4 MB，最多八人、歌曲最多三十分钟、最多五万个源音符；编曲核心会进一步验证文件。房间默认最多三十二个。接口限流及内容大小上限用于控制资源消耗。

部署时请备份两份 SQLite 数据库及运营配置，不要把数据库、明文 token 清单、私钥或 API 密钥放进公开下载目录。跨电脑网络延迟与游戏实际按键处理仍会影响最终听感；共同时间轴和断线暂停用于减少漂移，无法代替游戏本身的同步机制。
