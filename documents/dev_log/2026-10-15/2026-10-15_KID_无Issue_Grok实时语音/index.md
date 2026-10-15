# Grok 实时语音 Application

- 创建日期：2026-10-15，Asia/Shanghai；建档作者：KID（SDK 有效 Git 作者）；参与者：用户、Codex、设计与产品子代理。
- 无 Issue；独立 Application 位于 WatcheRobot-Applications/grok_voice；状态：进行中，本地未提交。
- 目标：实现可启动停止的实时语音应用，参考 Grok 界面，使用 测试设备串口 实机验证。
- 边界：WatcheRobot Python SDK 为唯一 Daemon 来源，ApplicationContext 注入通道管理设备；不复制 Daemon，不改变路由，不自动烧录固件。
- 初步设计：Python WebSocket 接入 xAI；SDK RTC + aiortc 承载设备全双工音频，网页只提供控制与状态。默认不开麦，密钥只存内存或由环境提供。
- 验收：音频流与采样率合同；暂停/静音/打断/旧回应隔离；缓冲边界；失败释放；网页来源保护；浏览器 UI 操作；测试设备串口 身份、收音、播放、停止证据；真实云端验证需要 API 凭据。
- 设计子代理负责 web 与 icon；产品子代理负责需求、协议与实机验收调查。界面参考不是声称官方出品。

## 调查与依据

- SDK 现有 voice 模板是 ASR/LLM/TTS 三段式；audio.play_pcm 要求完整长度与哈希，故不直接复用为无限时长云音频流。
- https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech ：WebSocket 双向 PCM、server_vad；API Key 认证。尚未完成真实调用。
- https://github.com/JosssphZhou/groki-bot/blob/main/docs/architecture.md ：Stack-chan 案例使用 Gemini Live 语音 + 社区 gbot CLI 转交任务，不能称为 Grok 原生实时语音。
- https://github.com/EvanBacon/grok-voice-demo ：直接 Grok Voice API 的开源参考，依赖 XAI_API_KEY 签发短期令牌。
- 本机 Grok Bot 配置目录存在，未读取或导出登录凭据；当前进程未配置 XAI_API_KEY。

## TDD 与验证

待记录真实 Red/Green 执行结果；模拟服务与实机验证分开。

## 遗留事项

- 云端 API 凭据尚未提供；安装 Grok Bot 不等价于 API 认证。
- 设备当前运行版本、SDK 与 测试设备串口 身份需重新验证，不继承其他串口历史结果。

## 2026-10-15 用户明确调整方案：复用 JoyInside + 本机 Grok Bot

用户确认 STT/TTS 复用机器人现有 JoyInside，整个 Agent 接入已登录的 Grok Bot；前述 xAI 原生实时音频路线保留为调查历史，不再实施。Application 使用 SDK speech.open/recognize/say，半双工连续轮次；Bot 通过社区 grok-bot-cli 桥接，保持其上下文及工具能力。不要求 XAI_API_KEY，也不声称这是 xAI 官方 Agent SDK。

用户已授权持续实现、TDD、测试设备串口 实机验证，并在机器人旁放置喇叭允许自动声学测试。方案验收改为：Bot 选择及登录诊断、单次送达和回执游标隔离、STT → Bot → TTS、取消与 SDK 线程释放、Desktop 快捷键经 Application、网页来源保护、真实机器人身份与声学轮次。任何登录或硬件失败须单列，不能由模拟验证替代。

初始 realtime/audio/service 三份测试在实现前执行得到 3 个 ModuleNotFoundError（真实 Red）；因需求改变，这些未实现架构的测试被新方案测试替代。Grok Bot 桥接测试先 Red（gbot 模块不存在），后 10 passed，测试使用假 CLI 合同，不代表真实云端已通过。

已启动本机 Grok Bot 0.70.2，desktop-status 显示 signedIn=true；社区 CLI 0.12.4 需要的 gateway-descriptor.json 不存在，bots list --gateway 失败。正在核对安装版本的实际入口，不读取或公开登录令牌。测试设备串口 有周期 MCU READY 日志，但初次 LF/CRLF 调试命令均无响应；实际原因待核实。

## 2026-10-15 03:36 KID / Codex：真实桥接、网页验收与硬件门禁

软件实现完成，整体验收待 测试设备串口 声学闭环。最新验证见 [交付结果](../../../../RESULTS.md)；真实网页截图（本机私有截图，不随源码发布）。SDK Daemon 0.1.11 管理 Application 正常启动，app check 通过；Python tests 48 passed，Node语法通过。无提交、推送或固件改动。

新增真实 Red/Green：入口2缺模块失败后3通过；service缺模块失败后Green；长回复超SDK字节限制失败后900字节分段Green；新版accepted回执5项失败后唯一envelope→server requestId关联Green。Windows凭据读取用假存储进行TDD，真实调用使用内部CurrentUser安全存储，未公开令牌或修改认证。

Bot实际列出9项，首个测试得到“你好，机器人。”；CLI旧版本缺messageId导致unknown，但已先读原记录确认送达，没有补发。适配后不同测试返回“语音应用的 Agent 已连接。”。网页真实选择Bot/保存/发送返回“网页已接通 Grok Bot。”（6453ms）；取消等待后新测试返回“恢复正常。”（11422ms），没有采纳已取消请求的回复。此为真实文字端到端，页面语音轮数0，不能当作声学验收。

测试设备串口正常重启日志确认v0.5.8与launcher，MCU READY无CRC/ACK异常；LF/CRLF调试入口无响应，默认正式配置DEBUG_CLI关闭。设备未进入Desktop Link/桌面互联，SDK robot status未连接。实际声学验收core执行exit1“机器人未连接”，未播放喇叭或调用start；/api/start真实HTTP409。私有证据位于Workspace忽略目录.codex/local/grok-voice-20261015的serial-reset.log、serial-crlf.log、acoustic-result.json。待屏幕进入Desktop Link并读取配对码后，继续真实两轮声音、停止恢复、SDK能力与听感验收。

## 2026-10-15 03:38 KID / Codex：脚本入口收尾

双平台声学入口合同补测与PowerShell失败退出码修正后，最终全套52 passed（3.28s）。Node语法与SDK app check再次通过。前述48项结果保留为当时实测记录；声学硬件门禁仍未解除，未把新增脚本单元测试算作测试设备串口闭环。

双平台入口真实执行补充：PowerShell 与本机 Git Bash 的 --help 输出一致且exit0；非法 --turns均exit2；不存在的PYTHON均exit127。声学core先4缺实现失败，再补齐实现与入口合同通过。工具从未绕过设备连接前置门禁，当前测试设备串口未配对时没有播放测试WAV。

## 2026-10-15 10:02 KID / Codex：用户截图中的失败提示调查

用户报告“你好”未收到回复，以及左侧竖排错误条。旧版仅显示统一CLI失败提示，未保留退出原因，因此无法可靠追溯该次调用究竟是网关超时、认证拒绝还是其他失败，不能把猜测写成根因。机器人未连接是独立的设备状态，不影响独立文字验证。

现场只读 Bot 列表返回9项；独立新测试收到“连接检查正常。”，没有重发用户原始“你好”。同文案独立页面夹具在1280视口实测提示宽430、高66.78，无水平溢出，未复现截图中的窄条；源码提示元素不含截图中的关闭按钮，不能断言窄条一定由该元素产生。布局仍加固显式宽度、横向书写及禁止flex收缩。

TDD新增6项先失败（缺少分类code与读取恢复），实现后通过；再补发送故障不重试和读取重试上限，最终60 passed（3.98s）。新增安全错误分类、失败阶段和退出码日志，禁止记录原始stderr、令牌、消息正文；只在已送达后的读取阶段对临时网络/网关错误重试一次，保持原回执。发送失败不自动重试。Node语法与SDK app check通过。Application正常停止后重新启动，继续真实页面验证。未修改Daemon、SDK或固件，声学门禁不变。

10:04 补充：重启后的真实页面问答收到“页面连接正常。”，耗时21593ms。证据：真实回复（本机私有截图，不随源码发布）、错误提示布局（本机私有截图，不随源码发布）。此为文字验证，语音轮数仍为0。

## 2026-10-15 10:49 KID / Codex：实机已连接后的语音清理故障

用户报告语音资源未确认释放，并要求自主端到端调试。现场Daemon 0.1.11仍运行同一Application；设备已连接python_sdk（身份旧设备身份（已脱敏）），Application enabled=false、cleanup_pending=true、phase=error、语音轮数0。通过Application现有/api/stop两次重试均失败；测试设备串口只读25秒日志仅见MCU READY周期统计，无链路CRC/ACK异常，不能据此证明语音协议正常。未绕过Application直连设备业务。

发现明确产品缺陷：清理失败后enabled=false导致主按钮仍为开始对话，无法从页面重试stop；成功释放后旧error未清空。恢复用例补断言先Red，修复后Green；新增安全清理诊断用例先KeyError失败，再补speech_failure和阶段/错误类别/有限协议reason日志通过。页面补“重试释放语音”，在线时不再误导恢复连接。全套61 passed（3.13s）、JS语法通过。接下来由Daemon正常停止并拉起Application，采集真实语音open/close错误，继续硬件闭环；不能把此单元测试当作声学通过。

## 2026-10-15 11:21 KID / Codex：固件与设备身份纠正，真实两轮闭环完成

更正前述现场身份判断：早期Daemon连接旧设备身份（已脱敏）（旧IP），不是测试设备串口。测试设备串口刷入测试镜像后串口为v0.5.6，独立Daemon-managed协议探针却仍收到v0.5.8；检查pair.log发现CLI只返回Robot is already connected，没有切换设备。通过Daemon管理disconnect后，使用测试设备串口串口配对码重新配对，实际目标为目标设备身份（已脱敏），IP与测试设备串口启动日志一致、固件版本v0.5.6。旧ID上的语音超时不能算测试设备串口验收结果；这些失败证据保留。没有修改Daemon路由，也未建立第二条设备业务连接。

测试设备串口原OTA_1完整6MiB现场备份确有speech.joyinside.v1声明，但无ctrl.speech.、conversation_asr和Speech worker closed字符串。对应备份SHA256=d7fb108c0954d56bb600b6e1491a1cdcb873583890222590cd778455a614f3a2。原有历史备份中语音实现位于OTA_0。使用项目flash-monitor BuildOnly重新构建现有未提交语音代码，未改固件源码；旧缓存镜像缺新增官方更新公钥，未绕过NoBuild公钥门禁。新测试镜像6190896字节，SHA256=322f0f5bb17b4f22c96770491c030fc4c443076839cfa54743a3ebebbe665f2b。AppOnly写OTA_0并校验，切换启动；保留OTA_1和otadata备份用于恢复，未烧写Wi-Fi/JoyInside NVS、存储、模型或STM32。最终使用开发固件，不代表正式v0.5.8发布已修复。

真实声学测试始于11:16:10：显式选Insta360 Wave USB物理扬声器，避免默认蓝牙耳机。两轮真实STT均为“这是机器人语音测试，请只回答语音连接正常”，真实所选Bot两次回答“语音连接正常”，两次设备TTS完成后轮数递增。轮次分别46.515秒、24秒，总72.765秒含结束。报告success=true、stop_completed=true。再通过真实页面开始，观察listening，点击结束，确认idle、enabled=false、cleanup_pending=false、error为空。麦克风最终关闭，Application仍运行且测试设备串口保持连接。此为合成喇叭输入的真实声学闭环，不代表人工听感、回声或长稳验收。

TDD补充：清理未确认仍返回成功、声学工具只凭HTTP停止响应判定成功，两个用例先失败后修复；身份门禁先TypeError失败，再补expected-device-id与运行期身份检查通过。两平台入口原样透传新增参数，共用核心；实际CLI用错误身份退出1，未启动或放音。真实app.js的清理恢复按钮用Node执行状态机验证（事后回归，不冒称先Red）。最终64 passed（3.34秒）；JS语法与SDK app check通过。

私有证据：Workspace忽略目录grok-voice-20261015内的backup-manifest.json、speech-candidate-manifest.json、speech-build.log、speech-flash.log、post-speech-boot.log、com11-pair.log、speaker-e2e-result.json、speaker-failed-wrong-device.json、wrong-device-guard.json、final-voice-e2e.jpg。serial-reset.py复用时覆盖了旧serial-reset.log；旧v0.5.8启动信息仍在pr270-hil/original-boot.log中，新启动记录已另存post-speech-boot.log，不能把覆盖后的文件当早期证据。真实测试数据不纳入源码提交。

状态：本地修改与台架部署，未提交、推送或创建PR。原“声学尚未完成”的历史记录已由本次实测更新；当前交付概览见RESULTS.md。正式固件语音集成发布、长期稳定性及人工听感留作后续。

固件配套档案：[设备JoyInside语音SDK](../../../../../../WatcheRobot-Workspace/WatcheRobot_esp32/docs/development/2026-10-13/2026-10-13_234500_KID_无Issue_设备JoyInside语音SDK/index.md)。


## 后续变更：2026-10-15 持久连接与遗漏结果

[持久连接档案](../2026-10-15_KID_Grok持久连接/index.md)补充原桥接性能和多条回复处理：常驻 Python HTTPS 连接池替代每次 CLI，按原始 requestId 继续接收后续结果；JoyInside 与 Daemon 边界保持。


## 后续交付

2026-10-15｜KID｜补充：[应用商店提交](../2026-10-15_KID_无Issue_应用商店提交/index.md)。历史记录中的未提交状态仅代表当时状态；当前发布状态见新档案。公开前已脱敏台架串口和设备身份，原始正文与截图保留本机私有备份，不改变历史验证结论。
