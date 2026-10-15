# Watche Voice

**JoyInside 听说 · Grok Bot 思考。** 机器人用已有的 JoyInside 服务完成语音识别与朗读，电脑上的独立 Application 把识别文字交给当前用户登录的 Grok Bot，再将回复交给机器人朗读。

这是本机 Grok Bot 的实验桥接方案。它按社区 `grok-bot-cli` 0.12.4 已核对的网关合同复用桌面登录会话，在 Python 中保持 HTTP 连接池，未调用 xAI 实时语音 API，也无需在 Application 中提供 xAI API Key。Grok Bot 自身的上下文、工具与权限继续由它管理。

## 架构与会话

```text
机器人 JoyInside STT
    → Daemon → Application 的 Device channel
    → Application 常驻网关连接 → 所选 Grok Bot
    → 回复文字 → Application 的 Device channel → Daemon
    → 机器人 JoyInside TTS
```

本项目仅实现 Application。SDK 是 Daemon 的唯一实现，所有设备操作使用 SDK 注入的 Device channel；桌面麦克风快捷键由 Application 的 Desktop channel 接收处理。

采用半双工连续会话：聆听 → 识别完成后关麦 → Grok Bot 思考 → 机器人朗读 → 重新聆听。播放时不提供自然语音打断；网页的“停止回答，继续听”取消本地等待或播放并恢复聆听。已提交的 Grok Bot 工作可能仍在继续，取消本地操作不代表取消云端任务。音色沿用机器人 JoyInside 设置。

收到首条回答后，同一请求的后续消息会继续监听最多 5 分钟，按原始 requestId 关联并去重；在页面追加，在语音对话中排队播报。聆听中收到结果时先取消识别并确认结束，再播报，之后恢复聆听。结束或停止回答会丢弃本地待播结果，不会自动取消 Bot 云端任务。接口目前没有可信的任务结束事件，因此 5 分钟窗口和最多 64 项后台任务是明确限制；超过窗口请到 Grok Bot 查看。仍采用半双工，尚未实现音频流式播放与自然语音插话。

## 准备环境

1. 安装包含 `robot.speech` 的 WatcheRobot Python SDK，使用声明 `speech.joyinside.v1` 能力的固件。
2. 按 SDK 文档完成设备配对，并确认机器人已有可用的 JoyInside 配置及网络。
3. 在运行 Application 的同一台电脑、同一操作系统用户下打开 Grok Bot 并完成登录，确认至少有一个可用 Bot。
4. 社区 CLI 可选，用于独立诊断；Application 的语音收发不再启动 Node CLI。需要诊断工具时可安装：

```text
npm install --global grok-bot-cli@0.12.4
```

安装后，通过本 Application 设置页“刷新列表”检查连接。社区 CLI 的裸命令并不保证适配当前 Windows 桌面登录存储与客户端版本；不要以裸命令失败推断 Application 无法连接。

桥接使用 Grok Bot 桌面应用已有的本机登录会话。Windows 会话由 Windows 安全存储解密读取，认证仅用于 Application 内存及发往受限 HTTPS 域名的请求，不导出 token 或复制登录文件，也不启动 Codex/Grok 自动转发 relay。桥接根据桌面实际版本动态匹配客户端版本，不依赖社区 CLI 的旧默认版本。CLI 与桌面应用版本、登录状态或服务端接口变化均可能使桥接失效；发现错误时先检查本机 Grok Bot 和 CLI。请勿把超时消息自动重发。

SDK manifest 当前仅声明 Windows，其他系统需完成同等验证后再开放。社区 CLI 有其他系统支持，不代表本 Application 已完成跨系统验收。

## 应用商店与源码

应用 ID：`com.orulink.watche_voice`；版本：`0.1.0`；支持 Windows；SDK：`>=0.1.11,<0.2`。

完整开发仓库（含测试与发布规则）：[GitHub](https://github.com/orulink-ai/WatcherRobot-com.orulink.watche_voice)。完整应用已通过 [源码 PR #1](https://github.com/orulink-ai/WatcherRobot-com.orulink.watche_voice/pull/1) 合入 `main`（合并提交 `ed5b9ee`，两个 parent）。商店使用公开 Gitee 源码的固定 commit。

0.1.0 已提交[应用商店审核 PR #23](https://gitee.com/orulink-sz/watcherobot-app-store/pulls/23)，尚未合入：当前投稿账号只有官方目录读取权限，合并请求返回 HTTP 403，需有写入权限的维护者处理；固定源码为 [3dc71830](https://gitee.com/KID_gitee/WatcherRobot-com.orulink.watche_voice/tree/3dc71830d5adc8713e634f713261ce328e137a77)。提交审核不等于已上架。

应用商店收录后，在桌面端选择 Gitee 来源，刷新列表、安装并启动 **Watche Voice**。等待审核期间可按下文从源码启动。依赖由 SDK 根据 `app.json` 安装；语音仍需要机器人已有的 JoyInside 服务，本应用不包含 Grok Bot 订阅、云端服务额度或固件更新。

应用会读取当前 Windows 用户的 Grok Bot 登录会话，并把机器人识别文字发送给选中的 Bot。Bot 的工具可能继续执行任务；请在开始前检查所选 Bot 的权限。页面仅开放本机随机端口，默认启动不开麦。

发布维护者使用 `watcherobot app check .` 验证，`watcherobot app publish --provider gitee .` 发布源码，再以发布返回的完整 commit 执行 `watcherobot app submit --provider gitee . --commit <完整SHA>` 提交审核。发布器使用 `.watcherignore`，不读取 `.gitignore`；个人配置、日志、URL、台架截图均不得上传。开发测试不随商店运行时快照分发，请在 GitHub 开发仓库运行。

## 启动

在本 Application 的上级目录执行：

```text
watcherobot app run ./grok_voice
```

也可以将目录替换为 Application 的绝对路径。通过 SDK/桌面端选择并启动应用，由 Daemon 注入上下文；不要直接运行 `python app.py`。

启动后自动打开本机网页，默认待机不开麦。打开设置，选择实际的 Grok Bot 并保存，再点击“开始对话”。Bot 列表为空时，检查 Grok Bot 登录和 Bot 创建状态，再点“刷新列表”。待机时也可以在文字框发送“文字验证（不播报）”，无需设备在线；它只验证文字桥，不计入语音轮次，也不能替代麦克风与播报的真机验收。等待文字回复时可点击“取消文字验证”，已提交的云端工作不会自动撤回。页面显示真实设备状态与对话文字，不预填示例聊天。关闭对话使用网页“结束对话”；退出 Application 使用 SDK/桌面端停止操作。

网页服务只监听 `127.0.0.1` 的随机端口，不暴露局域网接口。页面刷新不等于结束会话。聊天记录保留在当前 Application 内存中；Bot 选择由服务保存在用户配置目录。

## 本机与测试配置

这些环境变量必须设置在启动 Daemon 的环境中，Application 会继承 Daemon 的环境。更改调用 CLI 的终端环境不能更新已经运行的 Daemon。

| 环境变量 | 用途 |
|---|---|
| `GBOT_ENTRY` | 指向社区 CLI 的 JavaScript 入口，由 Node.js 直接执行；适合非全局安装 |
| `GBOT_BIN` | 指定 `gbot` 可执行文件；未指定时从 PATH 查找 |
| `GROK_VOICE_OPEN_BROWSER=0` | 测试时禁止自动打开网页 |
| `GROK_VOICE_URL_FILE` | 将本次随机端口 URL 写入指定本地文件，正常退出时仅清理仍属于本次运行的文件 |

`GBOT_ENTRY` / `GBOT_BIN` 仅用于保留的 CLI 诊断适配器；正常收发与 Bot 列表使用 Python 连接池，不启动子进程。个人入口路径、设备地址、配对码、日志与 URL 文件不要提交到 Git。

开发验证：

```text
python -m pytest tests -q
node --check web/app.js
```

单元测试不替代真机验收。实际验收需要验证中文麦克风 → Grok Bot → JoyInside 播报、连续轮次、停止/恢复、设备断线及应用切换后的资源释放。具体已验证范围和结果记录在 `documents/dev_log/`，不预先宣称测试通过。

## 真实声学两轮测试

准备一段清晰的中文 **PCM WAV**，将电脑喇叭放在机器人麦克风附近，确认系统默认输出确实使用这只喇叭。机器人须进入 **Desktop Link / 桌面互联**，按该应用屏幕显示的六位码通过 SDK 配对并连接；本工具不会替你配对、选择 Bot、切换固件模式或烧录。先启动 Application，在设置中选好 Bot，并结束已有对话或文字验证。

从实际页面或 Application 日志取得本次随机端口 URL。下面变量分别填入这次的 URL、录音路径和本地结果 JSON 路径，不在仓库写死个人端口或路径。建议结果保存在忽略目录 `.local/`。

Windows PowerShell：

```powershell
.\scripts\acoustic-e2e.ps1 --url "$applicationUrl" --input-wav "$testWav" --turns 2 --output "$resultJson"
```

macOS/Linux Shell：

```sh
sh ./scripts/acoustic-e2e.sh --url "$application_url" --input-wav "$test_wav" --turns 2 --output "$result_json"
```

两个入口委托同一 Python 核心，参数、默认行为与退出码一致；需要时通过 `PYTHON` 指定解释器。Windows 使用 `winsound`，macOS 使用 `afplay`，Linux 使用 `aplay`；这是工具入口的平台支持，当前 SDK Application manifest 仍只声明 Windows。

工具先检查 Bot 已配置且机器人在线。没有连接时立即失败，**不会播放录音**。启动后，每轮等到真实 `listening` 状态，再播放 WAV，并要求后端语音轮次增加、本轮新出现的用户转写与 assistant 回复均非空。默认连续两轮，每轮最多 300 秒，可用 `--turn-timeout` 设置 1–300 秒；任何启动后的退出路径都会请求结束对话。停止清理最多另等 45 秒；必须返回 idle 且无待释放资源，不能只凭 HTTP 成功判定停止完成。

若页面显示“语音资源待释放”，保持原机器人连接并点击“重试释放语音”。确认关闭之前禁止重新开始或更换 Bot；关闭成功后错误提示自动清除。`/api/state` 的 `speech_failure` 和 Application 日志提供失败阶段、错误类别及有限协议原因，不记录原始异常或凭据。固件声明 `speech.joyinside.v1` 只是前置条件，还需实际响应 `ctrl.speech.*` 并返回对应完成事件。

JSON 标明 `synthetic speaker acoustic`：它记录喇叭放音经真实设备 STT、真实 Grok Bot 回复与设备 TTS 完成事件的证据，**不等于人工对话或人工听感验收**，也不单凭播放完成事件证明声音质量。工具不模拟 Bot，不保存凭据；结果含真实转写与回复，请留在本机并避免提交个人测试材料。

退出码：`0` 完整闭环通过；`1` 状态门禁、声学轮次或停止清理失败；`2` 参数错误；`3` 结果文件保存失败；平台入口找不到 Python 时为 `127`。

工具验证：

```text
python -m pytest tests/test_acoustic_e2e.py -q
```

测试使用注入播放器与 HTTP 状态检查断连门禁、两轮消息新鲜度、超时及停止清理；双入口测试实际调用 PowerShell 和 Shell 检查参数转发与退出码。这些测试不播放喇叭，也不构成真机语音验收。

## 参考来源

网关恢复：401/403/417 会作废对应旧会话；只读列表和回复请求最多重新建立会话后重试一次。发送消息始终不自动重试，因为失败时无法确定远端是否已经接收。仅 401/403 提示检查登录，其他网关状态不能直接判定退出登录。此处理不保证远端服务可用性。

浏览器出现 `Failed to fetch` 表示页面未取得本机接口响应，不能据此判断 Grok Bot 已退出登录。页面对 GET 网络失败在原有超时预算内最多再读取一次，持续失败时提示检查本机 Application 并重新从入口打开页面；POST 操作不自动重试。此恢复仅覆盖页面到本机服务的网络失败，不对服务端返回的 HTTP 错误重复请求。

Bot 列表网关读取遇到 502/503/504 或连接临时失败时，服务端最多再读取一次，继续复用现有登录会话。整个列表请求最多等待 10 秒（包括重试），超时返回明确的 504 提示，先于网页 12 秒的请求期限结束；不会因云端列表暂时不可用要求重新登录。

发送响应丢失时，Application 最多用 12 秒只读核对原始消息的唯一标识。找到对应的服务端 requestId 后继续等回复；没有确认则明确显示送达状态未知，不因内容相同而误认旧消息。日志仅记录操作、HTTP 状态及有限的连接/读取/写入超时类别；原先的无状态失败无法事后追溯具体异常类别。

- [Grok Bot 官方介绍](https://x.ai/bot)
- [社区 grok-bot-cli npm 包](https://www.npmjs.com/package/grok-bot-cli)
- [社区 CLI 源码与配置说明](https://github.com/ScriptedAlchemy/grok-bot-cli)
- SDK 的 `examples/joyinside_pi` 与 `docs/device-speech.md`

声学测试可附加参数 --expected-device-id 指定台架身份；不匹配时不会启动或播放音频，运行期间也检查设备是否改变。两个平台入口透传同一参数。
