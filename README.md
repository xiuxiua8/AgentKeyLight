# AgentKeyLight

让 NuPhy Air60 HE（61 键）的**按键背光**显示本机 Codex 与 Claude Code 的状态。后台服务根据两者的 hooks 汇总所有会话，把逐键动画实时推送到键盘。它**不改动键盘里保存的灯光设置**：没有任务时停止推送，键盘约 2 秒内自动回到你自己的灯效。

## 灯效

| 状态 | 默认动画 | 什么时候出现 |
| --- | --- | --- |
| 运行中 | 蓝色流光从左到右扫过；每个运行中的任务一道，最多 3 道 | 提交提示、工具调用完成 |
| 等待你操作 | 琥珀色急促双闪 | 权限请求；Claude 用 AskUserQuestion 提问或请你批准计划；MCP 表单；Codex 用 `request_user_input` 提问 |
| 完成 | 绿色涟漪从中心扩散，随后柔和常亮，保持 12 秒 | 正常结束一次回复 |
| 出错或中断 | 红色三连爆闪后脉动，保持 20 秒 | Claude Code 的 API 错误、Codex 的中断 |
| 空闲 | 停止推送，显示你自己的灯效 | 没有需要显示的任务 |

显示灯效时，**一组按键以呼吸方式显示来源色**，表示是哪个工具的任务：Claude Code 为橙色，Codex 为白色；其余按键显示状态动画。这组按键有三种位置可选：

| 位置 | 按键 | 状态动画所在 |
| --- | --- | --- |
| 碗状（默认） | esc、delete、两侧各 3 个键（tab、caps、左 shift；\\、return、右 shift）和底行，共 16 个 | 数字行 1 到 = 和中间的键 |
| 外圈 | 顶行整行、两侧各 3 个键和底行，共 28 个 | 中间 33 个键 |
| 底行 | ctrl 到 fn 的 8 个键 | 其余所有键 |

来源色有三种样式，在调色板里选择，并可调整速度：

- **静态**：常亮。
- **呼吸**（默认）：从最亮开始呼吸，最暗时仍保留微光。
- **滚动**：这组键铺一层暗的来源色作为背景，一个高亮点带着拖尾在键与键之间移动。外圈顺时针绕圈；碗状从 esc 经左侧、底行、右侧到 delete 后折返；底行在 ctrl 与 fn 之间来回。多个会话同时存在时，优先级为 **等待你操作 > 出错 > 运行中 > 完成**。

## 调整灯效

```bash
agent-keylight palette
```

打开网页调色板（只在本机 `127.0.0.1:47631` 开放，可以加入书签）。页面上方实时显示键盘此刻的真实颜色；在下方为每种状态选择动画、主色、背景色和速度，也可以设置来源色及其位置和样式（呼吸或常亮）、保持时间、整体亮度和颜色校正。所有改动都会立即在键盘上预览，点「保存」后才写入设置。

**颜色校正**（默认开启）：屏幕颜色按 sRGB 编码，LED 的亮度却与数值成正比，直接发送 `#ffad00` 这样的值，中间色在键盘上会偏亮偏淡。开启后，程序先把你选的颜色换算成 LED 需要的亮度，让键盘颜色接近屏幕上看到的颜色；页面上的键盘画面也按 LED 实际发出的光换算回屏幕颜色。关闭时直接按数值点亮，与 NuPhyIO 的做法相同。可以对着键盘开关比较，选你觉得更准的一种。

设置保存在 `~/Library/Application Support/AgentKeyLight/config.toml`，文件里有中文注释，也可以直接编辑，或让 Codex / Claude 帮你改。保存后后台服务会自动载入；内容有误时继续使用上一次的设置，并把原因写进日志。

可选动画：流光 `comet`、急促双闪 `flash`、涟漪后常亮 `bloom`、警报 `alarm`、呼吸 `breathe`、波浪 `wave`、涟漪 `ripple`、扫描 `scanner`、星光 `sparkle`、彩虹 `rainbow`、常亮 `solid`。

```bash
agent-keylight preview waiting   # 在键盘上预览一种状态约 8 秒
agent-keylight demo              # 依次预览四种状态
```

## 安装与维护

需要 macOS、用 USB 连接的 Air60 HE，以及本机的 `uv`、Codex 和 Claude Code。

```bash
cd /Users/zilong/coding/AgentKeyLight
uv sync
uv run agent-keylight install
uv run agent-keylight doctor
```

`install` 会备份并合并 `~/.codex/hooks.json` 和 `~/.claude/settings.json`（只增改本程序自己的条目），再安装并启动用户级登录服务 `com.zilong.agent-keylight`。重复运行是安全的，升级后也用它更新。**Codex 会跳过未经信任的 hook**：安装或更新后，在 Codex CLI 中打开 `/hooks`，确认命令指向本项目后选择信任。

```bash
uv run agent-keylight status      # 键盘此刻显示什么，以及每个会话的状态
tail -f ~/Library/Logs/AgentKeyLight.log
uv run agent-keylight uninstall   # 移除 hooks 和登录服务
```

hook 本身出错时不会影响 Codex 或 Claude Code，原因记录在 `~/Library/Application Support/AgentKeyLight/hook-errors.log`。

## 状态如何识别

每个 hook 事件把所属会话的状态写入一个小文件，后台服务每 0.2 秒汇总一次。为了避免灯效卡住，有几条专门的规则：

- **代理进程退出即撤下。** hook 会记下启动它的 Codex / Claude Code 进程；进程崩溃、被结束或终端被直接关闭时，它的「运行中」和「等待」立即消失。「完成」和「出错」仍会按保持时间显示，所以一次性的命令行调用也能看到结果。
- **Claude Code 的权限请求需要确认。** Claude Code 在权限提示约 6 秒仍未处理时会发出 `permission_prompt` 通知。10 秒内没有这个通知，说明你已经处理了，灯效转回「运行中」，不会在批准后的长命令期间一直闪「等待」。
- **按 Esc 打断。** Claude Code 被打断时不会触发 `Stop`；约 60 秒后的 `idle_prompt` 通知会清除这个会话（前提是你这段时间没有打字）。
- **上下文压缩不算新会话。** `SessionStart` 的来源为 `compact` 时保持原状态。
- **子代理的 API 错误不算失败。** 带 `agent_id` 的 `StopFailure` 被忽略，主任务仍在运行。
- 没有任何事件超过 6 小时的「运行中」或「等待」会被视为过期。

## 设备与协议边界

- 只识别 USB VID `0x19f5`、PID `0xfee0`、产品名 `NuPhy Air60 HE`、usage page `1` / usage `0` 的控制接口，其它型号不会被写入。
- 协议取自 NuPhyIO（drive.nuphy.io）对 HE 系列的实现。本程序只发送读取命令和 `LedSyncDownload`（`0xDD`，逐键颜色帧）。固件把每一帧保留约 1.6 秒，之后回到自己的灯效；推送前后读回三个模式保存的灯光设置，内容不变。`LedSyncUpload`（`0xDE`）读回键盘此刻显示的颜色，调色板的实时画面依赖它。
- 灯效在任何模式（游戏 / Windows / Mac）下都生效，颜色会乘以当前模式的背光亮度。**当前模式亮度为 0 时看不到灯效**，`doctor` 和调色板会提示。
- 10 颗侧灯不接受逐键帧，始终显示键盘自己的侧灯效果；侧灯条上的 Caps Lock 指示在动画期间照常工作（已在固件 1.12 上实测）。
- 设备以共享方式打开，NuPhyIO 可以同时连接。两者同时控制背光时，画面会互相覆盖。

## 已知限制

- Codex 没有「用户已批准」的 hook：批准权限后，灯效会停在「等待你操作」，直到这条命令执行完。
- Codex 是否对 `request_user_input` 触发 `PreToolUse`，官方文档没有保证（部分工具可能不走 hook 路径）。如果触发，Codex 向你提问时会显示「等待你操作」。
- Esc 打断 Claude Code 后，如果你一直在打字，`idle_prompt` 不会发出，「运行中」会保持到你提交下一条提示。

## 资料

- [NuPhy Air60 HE 官方产品页](https://nuphy.com/products/nuphy-air60-he-magnetic-switch-gaming-keyboard)
- [Codex Hooks 官方文档](https://learn.chatgpt.com/docs/hooks)
- [Claude Code Hooks 官方文档](https://code.claude.com/docs/en/hooks)
- [CaseLight 的 NuPhy HE 插件](https://github.com/Wa1den/CaseLight/tree/main/plugins/NuPhy)：`LedSyncDownload` 帧格式与 1.6 秒保持时间的实测记录
