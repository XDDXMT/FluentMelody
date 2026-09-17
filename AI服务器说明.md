# AI 编曲服务器

AI 编曲服务器与联机服务器独立运行，使用独立数据库、兑换 token 和余额。无需接入支付平台：你自行定价、收款，再用本地管理员工具生成兑换码。AI 服务的次数不会扣联机时长。

## 启动

复制 `ai.example.json` 为 `ai.json`，在程序目录运行：

```powershell
python -m fluentmelody.servers.ai --config ai.json
```

默认仅监听当前电脑的 `127.0.0.1:8766`。客户端「设置」填写 AI 服务器地址，然后在 AI 页面兑换该服务器的 token。对外提供服务时，由管理员配置域名和 HTTPS 反向代理，再修改监听地址。不要把数据库、配置目录或 Codex 登录目录放在公开下载目录。

## 使用 API

在配置中将 `backend` 改为 `api`，`model` 填你账户实际可以使用的模型名称。模型必须支持 JSON Schema 结构化输出。

- `api_format: "responses"`：使用 Responses 接口。
- `api_format: "chat_completions"`：使用兼容 Chat Completions 的接口。服务需支持 `response_format.json_schema` 和 `max_completion_tokens`。
- `api_base`：仅管理员决定，示例为 `https://api.openai.com/v1`；不要填写 `/responses` 结尾。
- `api_key_env`：保存 API 密钥的环境变量名，默认 `FLUENTMELODY_AI_KEY`。密钥不写入客户端、任务描述或模型提示词。

在启动服务的同一终端设置环境变量（将占位内容替换为管理员自己的密钥）：

```powershell
$env:FLUENTMELODY_AI_KEY = '你的API密钥'
python -m fluentmelody.servers.ai --config ai.json
```

没有密钥或模型时，客户端会显示明确的未配置提示，不扣次数。该版本未预装任何付费 API 密钥，也没有借用个人登录凭据。

生成 5 个兑换码，每个兑换 10 次 AI 编曲额度：

```powershell
python -m fluentmelody.servers.admin --db data/ai.sqlite3 --kind ai issue --credits 10 --count 5
```

明文 token 只在生成时显示，请自行保存；可加 `--output ai-tokens.txt` 保存为文件。数据库路径必须与 AI 配置一致。客户端兑换一次后绑定该设备；同一设备重新兑换原码可恢复会话，但不会重复增加额度。

## 使用本机 Codex

将 `backend` 改为 `codex`，配置真实的 `codex.exe` 绝对路径；Linux 可填对应可执行文件。不会通过 `.cmd`、`.bat` 或 shell 拼接执行。

为服务创建独立的操作系统账号和独立 Codex 登录目录。先使用该服务账号，在一个单独终端内指定目录并完成正常登录：

```powershell
$env:CODEX_HOME = 'D:\FluentMelodyService\codex-auth'
codex login
```

此处 `CODEX_HOME` 是 Codex 官方环境变量，仅用于配置独立服务登录目录；不要设置为你日常使用的 `.codex` 目录。服务端 `codex_home` 填上面目录，`codex_service_account_confirmed` 设为 `true`。启动 AI 服务的终端无需设置 `CODEX_HOME`。

适配器调用真正的 `codex exec`：临时任务目录、只读沙箱、忽略个人配置与规则、关闭 shell、插件、hooks、浏览器、多智能体等功能，只接收结构化编曲数据。CLI 必须支持 `--ignore-user-config`、`--strict-config`、`--ephemeral`、`--output-schema` 及配置中的功能开关；不兼容时任务失败并退回次数，不自动降级到放开权限。

超时会终止本地进程树，输出文件和日志合计限制为 1 MB。**Codex CLI 当前适配器不能保证精确的模型 token 上限**，因此设置了单次时间上限、任务数量、并发和每日额度；API 模式另有明确的输出 token 限制。公网上的用户输入应在专用服务账号或隔离虚拟机中处理。只读沙箱本身不等于隐藏本机所有文件，独立账号是必要的隔离边界。

## 编曲和次数规则

上传 NBS/MIDI，选择 1—8 人，输入最多 500 字的音乐要求。AI 根据音轨、分段活动情况和音符样本，动态决定每人的旋律、伴奏、低音、和声，支持分段更换音轨、移调、节奏简化以及补写音符。服务器把方案应用到完整原曲时间线，保留前奏、间奏和尾奏，再生成每人一条单音声部的 NBS。

不会执行 AI 返回的 Python 或命令。返回内容只允许固定的音乐字段、枚举和数字，所有声部会再次检查音域、时长、重叠和至少 0.1 秒的松键间隔。导出的 NBS 可直接回到本软件加载；保留了本软件用来精确演奏的时值元数据。

- 默认每任务 1 次额度，`credits_per_job` 可调整。
- 提交时冻结/扣除次数；无效文件不扣，编曲失败自动退回。
- 同一请求标识重试不会重复扣费；修改文件或要求必须使用新标识。
- 修改也消耗一次额度，默认每首已完成编曲最多 3 次修改；失败修改不占这 3 次。修改须引用同一设备的已完成任务，并上传相同原始文件、选择相同人数。
- 每设备同时最多 1 个任务，最近 24 小时默认最多 20 个提交任务（包括失败），每分钟最多 5 次提交。管理员可修改每日额度。
- 默认服务器并行 2 个任务，待处理总数最多 16；文件最多 4 MB、5 万音符、64 个有效音轨、30 分钟。
- API 默认最多输出 8192 token，单次生成超时 120 秒。
- 启动/正常关闭服务时，未完成任务会被标记失败并退回次数。不要对同一数据库启动多个服务进程；程序使用文件锁拒绝第二个调度器。
- 默认保留文件 7 天，启动时和运行中每小时清理到期任务；同一修改链有近期任务时暂时保留原曲依赖。请及时下载结果。

防止无关提示词消耗资源主要依靠真实的额度、长度、并发和时间限制。提示词不是保险箱：本程序不承诺任何模型绝不泄露提示内容，因此模型上下文中没有服务器密钥，并且不把模型自由文本回传给用户。

## 客户端接口

请求使用与联机服务一致的设备签名和 Bearer 会话；请参考客户端网络模块与公共鉴权模块。没有公开的管理员发码接口。

| 接口 | 用途 |
| --- | --- |
| `GET /time` | 服务器时间，无需登录 |
| `POST /redeem` | 兑换此 AI 服务器 token，绑定设备 |
| `GET /me` | 查询当前次数、单任务扣费和额度 |
| `POST /jobs` | 提交 `{filename,data,players,instruction,parent_id?}`；`data` 为文件 Base64，需 `Idempotency-Key` 请求头 |
| `GET /jobs/{id}` | 查询 `queued/running/completed/failed` 状态，仅任务所属设备可查 |
| `GET /jobs/{id}/download` | 下载已完成 NBS，仅任务所属设备可下载 |

## 实现资料

API 字段按 [OpenAI 结构化输出官方文档](https://developers.openai.com/api/docs/guides/structured-outputs)实现；本机模式按 [Codex CLI 官方参考](https://learn.chatgpt.com/docs/developer-commands?surface=cli)和 [Codex 配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)接入。模型名称由服务器管理员填写，程序不替你选择账户模型或购买服务。
