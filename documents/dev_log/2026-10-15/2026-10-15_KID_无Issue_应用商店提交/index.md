# Watche Voice 0.1.0 应用商店提交

- 日期及时区：2026-10-15，Asia/Shanghai；作者：KID（Codex 协作）。
- 需求：用户授权将现有应用提交远程仓库及应用商店。
- 范围：独立 GitHub 开发仓库、Gitee 商店源码快照、官方目录审核 PR；不改 Daemon、SDK、固件或业务协议，不自动合入审核 PR。
- 当前状态：GitHub 源码已合入 main；Gitee 商店 PR 因当前账号无官方目录写权限仍未合入，尚未上架。

## 调查与决策

原应用目录尚无 Git 仓库。采用独立仓库，不把源码展开提交到 Workspace。应用商店 SDK 当前支持 Gitee / Hugging Face，本机 Gitee 已登录，使用 Gitee 提交官方目录。GitHub 保存完整开发历史和测试，Gitee 保存可安装运行时快照。

SDK 发布器独立读取 `.watcherignore`，不会套用 `.gitignore`。原目录的 `.local/` 日志、私有截图和辅助文件存在被纳入发布快照的风险，必须先修复发布排除配置。保留本机原始证据，在公开文字中脱敏台架串口与设备 ID；历史测试与调查结论不变。

## TDD 与验证

- Red：新测试调用真实 SDK 源码收集器，在临时目录放置模拟私有配置、日志、URL、截图与辅助代码；未配置 `.watcherignore` 时确实失败（1 failed），没有使用真实凭据。
- Green：增加 `.watcherignore` 后同一测试通过（1 passed）。同时验证 app.py、app.json、网页、图标及文档仍然被保留。
- 本次未修改业务逻辑。全套测试、JavaScript 语法、SDK app check、实际发布文件审查及远端提交状态将在执行后追加。
- 本轮不重复发送真实 Bot 消息或播放声学测试音频；既有声学结果与本轮发布检查分别记录。

## 关联历史

- 延续：[原始开发与声学验收](../2026-10-15_KID_无Issue_Grok实时语音/index.md)。
- 补充：[持久连接及后续结果](../2026-10-15_KID_Grok持久连接/index.md)。
- 补充：[网关 417 与 Bot 列表恢复](../2026-10-15_122000_KID_无Issue_Grok网关417/index.md)。
- 上述档案已添加反向链接，保留原始历史正文；公开版仅脱敏个人台架信息，原件存于本机忽略目录。

## 限制

Windows 专用实验桥接，需同一用户的本机 Grok Bot 登录及可用 Bot；设备需实际支持 JoyInside 语音。当前保持半双工，不宣称自然插话、真正音频流式交互或长期稳定性。远端服务可能返回 503；发送消息不自动重试，避免重复执行。应用商店审核合入由维护者决定。

## 变更记录

2026-10-15｜KID：新增发布排除合同回归及配置，更新安装、隐私与验收文档，准备中文提交、源码发布及固定版本审核请求。


### 发布前最终验证

2026-10-15 15:43 Asia/Shanghai｜KID：深层私有截图匹配回归先失败（1 failed），改为与 SDK PurePath 匹配合同一致的后缀排除后，最终全套 **107 passed，19.75 秒**。JavaScript 语法、SDK app check 通过。真实发布收集器确认最终 25 个文件、176903 字节（后续文档追加会改变字节数），不含 .local、日志、URL、截图、缓存与开发测试；完整测试与 .watcherignore 进入 GitHub 开发仓库。

GitHub 已建立独立公开仓库，main 仅含初始化规则（4c7cf2e）；应用代码在 codex/publish-watche-voice 分支整理并将以 draft PR 审查。商店使用独立 Gitee 固定快照；发布使用 SDK 核心，传输适配层仅把自动生成的源码及目录提交说明改为详细中文，并补充审核说明，不修改 SDK 源码或发布校验。


### 实际交付与远端核验

2026-10-15 Asia/Shanghai｜KID：

- GitHub 开发源码提交：`b7599e240e4b46811848cc171e9f2ed9f2a52f63`，分支 `codex/publish-watche-voice`，已核对远端分支 SHA 一致。[Draft PR #1](https://github.com/orulink-ai/WatcherRobot-com.orulink.watche_voice/pull/1) 为 OPEN / draft，未合入。
- Gitee 源码已发布为[固定快照 3dc71830d5adc8713e634f713261ce328e137a77](https://gitee.com/KID_gitee/WatcherRobot-com.orulink.watche_voice/tree/3dc71830d5adc8713e634f713261ce328e137a77)，SDK 匿名下载和应用校验成功，25 个文件与发布时本机源文件逐字节一致。当前回填的交付文档继续在 GitHub 保存，不反复修改已提交审核的固定快照。
- 首次投稿因开发者 Fork 缺少官方目录提交 `ec1d1cf` 被 SDK 阻止，确认未创建投稿分支或 PR。干净工作树以 `git merge --no-ff upstream/master` 同步；配套目录仓库提交 `abe4686c4b68206ea0cb9f8427faf60a2e8a082a`，核验 parent 数量为 2，目录内容与上游一致。普通 Git 推送缺少交互凭据，随后在子进程内存中复用 SDK 已保存的同账号凭据推送成功，未写入 token 到文件、URL 或仓库配置。
- 重试创建[应用商店审核 PR #23](https://gitee.com/orulink-sz/watcherobot-app-store/pulls/23)，状态 `pending` / `open`。审核分支中文提交 `b65f79ba3d110f93ffdd9e02d05ab23f6f4333fb`。
- 独立远端检查确认 PR 只修改 `app-list.json`，保留原有 4 条记录，唯一新增本应用与完整固定源码 SHA；PR 描述包含需求、权限、测试及半双工限制。未向官方默认分支推送，未执行 PR 合入。
- GitHub PR 已关联当前 Codex 聊天；Codex 附件工具不支持 Gitee PR URL，因此商店链接保存在本档案及交付说明中，未把附件失败描述为投稿失败。

## 最终验收

- [x] 独立仓库中文提交与推送。
- [x] 完整开发测试、安装文档和历史档案随源码保存。
- [x] 107 项测试、JS 语法、SDK 清单校验通过。
- [x] 发布私有文件排除合同 Red / Green 与真实文件清单核验。
- [x] 公开固定快照匿名下载、文件逐字节校验通过。
- [x] 商店目录审核请求成功创建并检查差异。
- [ ] 维护者审核合入后才能认定正式上架；本次提交不自动合入。

本轮没有修改运行中的对话服务配置或重新发送消息，没有重复声学验收；后续只读验证不代表远端长期可用。


### 用户授权合并与权限检查（2026-10-15 16:32 Asia/Shanghai｜KID）

用户在投稿完成后明确要求处理合并，本次授权替代此前“仅提交不自动合入”的范围限制。沿用本交付档案，不重复建档。

- GitHub PR #1：合并前 `MERGEABLE` / `CLEAN`，未配置必需 CI 检查；head 为 `77d032d61cf27db965db6f2f04812a476807e606`。由 draft 转为 ready，使用 `gh pr merge --merge --match-head-commit` 合入，未使用管理员绕过。
- 已核实远端状态 `MERGED`，时间为 `2026-10-15T08:32:18Z`，合并提交 `ed5b9ee9f443851a91483d8533670943e8fa4604`。通过 gh API 检查 parent 恰好为 `4c7cf2e28591ee1d27c82fae4c9a059926b56ca4` 与 `77d032d61cf27db965db6f2f04812a476807e606`，不是 squash 或 rebase。源码已在默认分支可见，本机 main 已快进同步。
- 合并后的文件树与此前完成回归的 PR head 完全一致（`git diff --exit-code` 无差异）。本轮未修改业务实现，不重复运行同一测试或声学播放；沿用 107 项回归及已记录的下载验收。文档状态修正以差异、链接和远端状态检查替代 TDD。
- Gitee 官方 Swagger 核实 merge API 支持 `merge_method=merge`；PR #23 无冲突，head 仍为 `b65f79ba3d110f93ffdd9e02d05ab23f6f4333fb`，仅新增本应用固定源码且保留原有四条记录。执行普通 merge 请求返回 HTTP 403，未合并。
- 独立只读检查官方目录接口的 `permission` 为 `pull=true, push=false, admin=false`；当前账号为 SDK 已登录的 `KID_gitee`。这是真实的仓库权限阻塞，不能通过重复请求、修改目录或改用其他账号凭据绕过。PR 保持 open，需有官方目录写权限的维护者合入 [PR #23](https://gitee.com/orulink-sz/watcherobot-app-store/pulls/23)。
- 更新 README、RESULTS 和当前状态，明确区分源码已合入与商店尚未收录；保留上述历史待审核记录作为当时事实。固定商店源码不变，未重新投稿、重发 Bot 消息、改变机器人会话或配置。
