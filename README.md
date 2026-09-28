# AgentKeyLight

让 NuPhy Air60 HE (61 键) 的**按键背光**显示本机 Codex 与 Claude Code 的状态。程序在 macOS 登录后运行，使用两者的生命周期 hooks 汇总多个会话，只向已验证的 Air60 HE USB 控制接口发送灯光指令。

| 状态 | 灯效 | 触发事件 |
| --- | --- | --- |
| 运行中 | 蓝色呼吸 | 提交新提示、工具继续运行 |
| 等待你操作 | 琥珀色快速呼吸 | 权限请求、Claude Code 的交互请求 |
| 完成 | 绿色常亮 12 秒 | 正常结束一个回复 |
| 出错或中断 | 红色快速呼吸 20 秒 | Claude Code API 错误、Codex 中断 |
| 空闲 | 恢复原来的琥珀色钢琴灯效 | 完成提示结束、所有会话退出 |

多个会话同时存在时，优先级为 **等待操作 > 出错 > 运行中 > 完成 > 空闲**。完成和错误灯效只保留短时间，然后回到仍在运行的其他任务或空闲灯效。颜色、亮度、效果与持续时间都在 `config.json` 中配置。

## 安装

需要 macOS、USB 连接的 Air60 HE，以及本机已安装的 `uv`、Codex、Claude Code。

```bash
cd /Users/zilong/Coding/AgentKeyLight
uv sync
uv run agent-keylight doctor
uv run agent-keylight demo --seconds 2
uv run agent-keylight install
```

`install` 会备份并合并 `~/.codex/hooks.json` 和 `~/.claude/settings.json`，再创建一个用户级 macOS 登录服务。它只增加自己的 hook 条目，不改动现有条目。Codex 对新 hook 有信任审核：在 Codex CLI 中打开 `/hooks`，检查命令指向本项目后设为信任。**未经信任时，Codex 会跳过这些 hook**；Claude Code 的 hook 可直接运行。

程序状态：

```bash
uv run agent-keylight status
tail -f ~/Library/Logs/AgentKeyLight.log
```

手动停止：

```bash
launchctl bootout gui/$(id -u)/com.zilong.agent-keylight
```

程序收到正常停止信号时，会恢复 `config.json` 的空闲灯效。运行资料存放在 `~/Library/Application Support/AgentKeyLight`，不会读取按键内容。

## 设备与协议边界

- 仅识别 USB VID `0x19f5`、PID `0xfee0`、产品名 `NuPhy Air60 HE`、usage page `1` / usage `0` 的控制接口。其它 NuPhy 型号不会被写入。
- 灯光报文采用 NuPhyIO 对这块实机发送的 64 字节主背光命令。已对原有“钢琴”效果、呼吸效果与 RGB 颜色变化做报文对比，并用实机读回验证绿色常亮。它不会改键位、触发点或侧灯。
- 原灯效是安装时在 NuPhyIO 读到的 **钢琴、`#ffbf00`、亮度 100**。如果之后你在 NuPhyIO 更改常用灯效，也应同步更新 `config.json` 的 `idle` 字段。
- 官方 hooks 能可靠指示权限请求、开始与正常结束。Codex 的“等待文字答复”或任务失败未必会发出独立 hook，因此这些情况不能保证都被识别为等待或出错。
- NuPhyIO 网页和本程序同时控制背光时，后发送的设置会覆盖先前的显示。调好键盘后可关闭 NuPhyIO 页面。
- 拔出后重新接入键盘，后台服务会重新应用当前灯效。请保留项目目录路径；如果移动项目，需要更新 hook 命令和登录服务。

## 资料

- [NuPhy Air60 HE 官方产品页](https://nuphy.com/products/nuphy-air60-he-magnetic-switch-gaming-keyboard)
- [Codex Hooks 官方文档](https://learn.chatgpt.com/docs/hooks)
- [Claude Code Hooks 官方文档](https://code.claude.com/docs/en/hooks)
- [nuphyctl 的灯效编号参考](https://github.com/fldc/nuphyctl/blob/master/src/nuphy_protocol.rs)
