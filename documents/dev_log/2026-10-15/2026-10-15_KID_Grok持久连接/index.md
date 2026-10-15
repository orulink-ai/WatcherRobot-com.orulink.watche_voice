# Grok Bot 持久连接

后续变更：[HTTP 417 恢复](../2026-10-15_122000_KID_无Issue_Grok网关417/index.md)，补充只读恢复及错误分类，保留发送不重试。

- 作者：KID / Codex；日期：2026-10-15，Asia/Shanghai。
- 状态：本地实现与台架验证完成，未提交、推送或合入。
- 背景：用户实测每轮弹 Node 终端，要求复用连接，如 Codex。
- 已确认：gbot._run_json 每次 send/thread 都 spawn CLI，未设置 Windows 隐藏窗口；Bot 历史保留不等于连接常驻。
- 方案：复用已安装 grok-bot-cli 0.12.4 提供的 stdio MCP 服务，用 gbot_send/manual 和 gbot_thread/full；保留消息唯一关联、不重发、取消后拒绝旧回复。Application 生命周期持有进程，不改 Daemon/设备路由。
- 验收：多轮同 PID、无终端弹窗、连续上下文、取消/崩溃恢复、退出释放、真实两轮问答。
- 调查：真实 MCP initialize 与 tools/list 成功；协议版本 2025-03-26。工具 schema 已核对本地安装物。
- 历史：[Grok 实时语音](../2026-10-15_KID_无Issue_Grok实时语音/index.md)。本任务补充其性能实现，不改变 JoyInside STT/TTS 路线。


## 方案修订与 TDD

- 实测安装物 MCP `gbot_thread/full` 返回 text、id、kind 等摘要字段，但省略原始 requestId。多任务交错时无法满足严格关联要求，撤回 MCP 常驻进程方案；未修改安装物，也没有通过解析消息 id 前缀猜测归属。
- 最终 Gateway 直接复用 grok-bot-cli 0.12.4 的 HTTPS 网关合同：一次 EnsureSandBox 后保留认证会话和 httpx 连接池；listAgents/sendPrompt/getAgentTranscriptTail 为允许的方法。请求前校验 credential host，禁止重定向，限制响应大小；令牌不进入命令行、日志、网页状态或配置文件。无需每轮启动 Node。
- Gateway 测试先因模块不存在 Red；实现后验证两次读取只握手一次、原始 requestId 保留、发送错误不自动重试、错误不泄露正文、拒绝非受信域名。
- Agent 后续回复测试先因模块不存在 Red；实现后同 requestId 后续结果只交付一次，其他请求结果不混入；取消监视丢弃迟到结果。
- service 测试先缺 agent 字段失败；实现后收到首条回复继续监听，并在识别线程结束后补播后续结果。新一轮文字输入不撤销之前的后台结果；明确停止或打断会清空待播和作废监听回调。
- 持续聆听现场另遇 WatcheRobotError 导致退出。固件使用 operation_timeout 表示无识别结果；补假设备 operation_timeout 用例先失败，再仅在识别阶段对该精确错误继续聆听，其他错误仍上报。补充有限协议原因诊断，不打印任意异常正文。
- 首条消息后监视最长 300 秒、最多 64 个待后续任务，每条按原始 requestId + entry id 去重。没有可信任务结束事件，不声称已知云端工作结束，也不宣称真正音频流式或全双工。

## 现场验证记录（按实际范围）

- Python 常驻会话两轮真实 Bot 测试：第一轮记住“蓝色月亮”，第二轮正确答出暗号，分别 12.94/9.05 秒，EnsureSandBox 计数均为 1。不能据此承诺固定延迟。
- 首次独立网关发送测试遇失败，未重发；后续新编号问题通过。错误分类随后补 HTTP 状态，原失败无状态不能猜测原因。
- 11:41 测试设备串口 两轮喇叭声学闭环均通过：23.468/23.469 秒，总 47.140 秒含 stop；最后确认麦克风释放。真实 Bot 耗时 5718 ms。报告为 Workspace 私有忽略目录 speaker-persistent-e2e-result.json，保留旧报告不覆盖。
- 真实页面多消息测试第一次两条在首个快照内一起返回，因此只算聚合验证，不能当作异步回调通过。
- 随后页面提交真实工具任务：先发“我先查一下官方语音文档”，查阅公开 xAI 文档后另发“查询完成，支持自动断句”。页面新增两条独立 assistant 记录，后续补播完成并回到 listening。报告 persistent-delayed-speech.json；这是文字输入加设备 TTS 验证，非麦克风声学输入。
- 保持聆听约一轮超时后遇错误，故继续修复并重启，不把前述短时成功写成长稳通过。最终版本的静默恢复及回归结果待补。

## 自然交互调研

[调研和后续边界](interaction-design.md)。保持用户已选 JoyInside STT/TTS + Grok Bot，不切换到收费 xAI 原生语音接口。


## 静默与最终回归

- Gateway + Agent + service 累计新增测试：当前 74 passed（13.38 秒）；随后补并发读取共享测试先 Red（5 个请求），实现同 Bot 同轮询周期共享原始快照后待最后全量结果。后台任务上限调整为 64，避免正常连续对话过早触及 8 项限制；网关快照缓存最多 16 个 Bot，取消时清空。
- 最终静默设备验证：43.14 秒始终 enabled，无错误；32.55 秒连接恢复、34.31 秒重新 listening，结束后 idle。persistent-quiet-test.json。可确认精确 operation_timeout 恢复有效，不能扩展成任意故障自动重试。
- 11:48 实机回归：两轮 17.375 / 17.422 秒，总 36.797 秒含 stop，两轮真实转写和 Bot 回复、TTS 完成，stop_completed=true。speaker-persistent-final-e2e-result.json。此结果在共享快照优化前完成，最终快照路径另外执行真实后续消息验证，不冒充该声学报告包含后加代码。
- 所有私有报告位于 Workspace .codex/local/grok-voice-20261015；未提交个人端口、音频或日志。


## 最终交付

- 全套 **76 passed，12.36 秒**，JS 语法与 SDK app check 通过。共享快照用例先 5 != 1 Red，再 Green；读取 503 用例先失败，再补只读重试一次通过。发送始终不自动重试。
- 快照优化后的第一次真实语音尝试收到环境中的“怎么了？”并开始请求，随后出现 HTTP 503，未收到回复；保留 persistent-final-delayed-speech.json 失败报告，没有重发该句话。该次未保留 method，无法断言 503 出在发送还是读取；新增有限 method/status 日志用于后续定位。
- 最终运行版通过真实页面文字验证，独立两条 assistant：“正在核对语音接口”“核对完成，支持断句与打断”，报告 persistent-final-web.json，语音轮数 0；本报告明确只是文字桥验证。上一版服务侧真实后续 TTS 成功和 11:48 声学闭环结果保留，不混为同一次测试。
- 后台监视任务已通过正常 /api/stop 结束，最后 idle、enabled=false、cleanup_pending=false、error 为空，麦克风关闭，Application 与设备连接保留。
- 新增 gateway.py、agent.py，service 接入持久网关、后续队列和精确 ASR 超时恢复；保留 gbot.py 的 CLI 合同适配器供诊断和历史合同测试，运行时正常收发不调用它的 subprocess 路径。没有 Daemon、SDK、固件源码或跨平台入口改动。
- 剩余限制：半双工；后续监听 5 分钟；网络错误仍可能中断；没有首声时间、人工自然度或长期稳定性结论。自然语音插话和流式音频尚未实施。


## 后续交付

2026-10-15｜KID｜补充：[应用商店提交](../2026-10-15_KID_无Issue_应用商店提交/index.md)。历史记录中的未提交状态仅代表当时状态；当前发布状态见新档案。公开前已脱敏台架串口和设备身份，原始正文与截图保留本机私有备份，不改变历史验证结论。
