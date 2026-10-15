# 自然语音交互调研（2026-10-15）

本任务使用一级资料核对，不以演示视频推断完整协议能力。

| 路线 | 可参考实现 | 对本项目的意义 |
|---|---|---|
| 原生实时语音 | [xAI Speech to Speech](https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech)、[WebRTC cookbook](https://docs.x.ai/cookbook/examples/voice-agent-webrtc) | 长 WebSocket、服务端 VAD、音频事件与中断。需要单独 xAI API 认证；不能把已登录 Grok Bot 桌面当作该 API。 |
| StackChan 实时后端 | [stackchan-ai-server](https://github.com/rudyll/stackchan-ai-server) | OPUS/WebSocket 到持续模型会话，服务端 VAD 和流式回复。仓库自述延迟不构成本机指标。 |
| 可替换 Agent 的语音管线 | [dotty-stackchan](https://github.com/BrettKinny/dotty-stackchan) | ASR、Agent、TTS 分层，Agent 输出流接入语音，设备状态机独立；与本项目保留 Grok Bot 的方向更接近。 |

建议优先级：

1. 连接池常驻，减少重复鉴权和进程启动；本次已实施。
2. 首条回复与任务完成分开，后续结果能继续显示和播报，支持显式取消；本次已实施 bounded follow-up。
3. 等待期间持续识别、语音插话、清空未播放音频，需要设备音频调度、SDK 事件和 AEC 一起核对，不在 Application 中假装并行调用 recognize/say。现有 SDK SpeechSession 只允许一个活动操作，接口为 recognize/say/cancel/close。
4. 真正边生成边播报需要 Agent 文本增量或音频帧接口。当前网关拿到的是完整 send-message，不能用 1 秒轮询包装成 token streaming。
5. 长任务最好“前台短答复 + 后台执行 + 完成后补充”，用户继续说话时先接收新输入，结果在合适间隙播报，避免重复朗读。当前实现保留已提交任务，不自动改 Bot 指令或替其授权工具。

产品验收应分别量测断句时间、Agent 首条耗时、TTS 首声、完整播放时间；本次仅沿用已有 Agent 总耗时与设备完成事件，未声称已测首声或人工自然度。
