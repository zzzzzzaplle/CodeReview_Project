# OpenClaw 环境检查记录

检查日期：2026-10-19（Asia/Shanghai）。

后续进展：同日已完成工作区切换和 Skill 安装，详见 [安装记录](skill-installation.md)。下文保留首次检查时的状态。

依据：当前仓库的 `复现指导方案.md`，以及 `/Users/macbookair/CodeReview_Project/docs/environment-setup.md`。

## 结论

OpenClaw 安装、初始化和 Gateway 服务已完成。方案第 2 节的模型对话验收待认证后执行；第 3 节工作区配置和 Skill 安装尚未完成。当前还不能执行双子 Agent 审查验收。

## 本机实测

| 检查项 | 实际结果 | 判定 |
|---|---|---|
| Node | `/Users/macbookair/.local/node/bin/node`，v26.12.0 | 已就绪 |
| npm | 11.20.0 | 已就绪 |
| OpenClaw | 用户级安装，2026.10.5，构建 1b58cb9 | 已就绪 |
| 旧版安装 | `/usr/local/bin/openclaw`、`/usr/local/lib/node_modules/openclaw` 均不存在 | 已清理 |
| 配置文件 | `openclaw config validate` 返回 valid | 通过 |
| Gateway | LaunchAgent loaded；运行版本 2026.10.5；连接检查 ok | 通过 |
| Gateway 地址 | `127.0.0.1:18789`，local / loopback / token 认证 | 适用于本机演示 |
| 默认工作区 | `/Users/macbookair/.openclaw/workspace` | 待切换 |
| main 实际工作区 | `/Users/macbookair/.openclaw/workspace`，配置中显式指定 | 待切换 |
| 目标工作区 | `/Users/macbookair/CodeReview_Project/demo/openclaw-workspace` 已存在，为空目录 | 待准备 |
| 子 Agent 配置 | 有效 `maxConcurrent=8`；`maxSpawnDepth` 未显式设置 | 待按方案设置为 2 / 1 |
| 工具配置 | `tools.profile=full` | 已读取配置；会话中的实际工具调用待实测 |
| code-review | `skills info` 返回 `Skill "code-review" not found.`；`skills check` 中也未发现 | 尚未安装 |
| 已保存 skill-info.json | 内容是上述 not found 错误对象 | 不能作为安装成功证据 |
| 模型认证 | 默认解析为 `openai/gpt-6-astra`；`missingProvidersInUse=["openai"]`，认证 profiles 为空 | 待配置 |
| 模型运行时 | `models status` 同时报告 codex harness 的 `owner-plugin-not-activatable` | 选定模型并认证后复查 |
| 相关插件 | openai、anthropic 均 enabled / loaded；插件诊断为空 | provider 已加载；不代表模型调用通过 |

Gateway 状态还显示 `connected-no-operator-scope`。本次连接及技能查询成功，完整操作权限待后续实际调用验证。

`doctor --lint --json` 完成 36 项检查，返回 3 条 warning：浏览器系统 profile 导入开启、loopback 无公网节点接入地址、Gateway token 以明文配置保存。这些结果未显示 Gateway 启动失败；当前本机演示无需为公网节点接入修改监听地址。报告不记录 token 值。

## 与方案的差异

1. `main` 已设置 `agents.entries.main.workspace`。仅设置 `agents.defaults.workspace` 不会覆盖它，后续应同时配置，并用 `openclaw agents list --json` 核对实际路径。复现方案第 3.1 节已补充。
2. 当前 Git 仓库位于 `/Users/macbookair/Desktop/CodeBase/CodeReview_Project`；setup 文档和目标运行目录位于 `/Users/macbookair/CodeReview_Project`。它们是两个独立目录。后续执行原方案时仍使用后一个目录；本检查记录保存在当前 Git 仓库中。
3. setup 文档包含历史状态和待执行步骤，例如“`~/.openclaw` 从未创建”，不能用作当前状态证明。当前配置目录及 Gateway 已存在，Skill 尚未安装。

## 后续操作顺序

1. 记录原有 workspace、main.workspace 和 subagents 配置；按方案第 3.1 节切换工作区、设置并发与深度、重启 Gateway。
2. 将 `skills-sh:mattpocock/skills/code-review` 安装到 main 工作区，核查实际 `SKILL.md` 和来源 Commit，再重存成功的 `skill-info.json`。
3. 按第 4 节建立独立 Git 样例，保存两次 Commit、非空 Diff 和预期失败的测试日志。这三步可在未设置模型 Key 时进行。
4. 在本机通过 `openclaw configure` 配置所选模型服务商及认证；检查 `openclaw models status --json`，解决所选模型的认证及运行时问题，完成一次模型对话。
5. 执行第 5 节审查，核实实际两次 `sessions_spawn`、两个子会话及两组 Findings，再开展 T2～T4。

## 检查方法与限制

执行了版本与路径检查、配置校验、Gateway 状态、模型状态、插件清单、Agent 清单、Skill 查询及只读 Doctor。OpenClaw 检查使用沙箱外授权执行：会话沙箱最初阻止专用临时目录访问和本机 Gateway 连接，重试后配置校验及连接通过。

本次检查未执行模型对话和子 Agent 调用，二者尚无运行证据。此记录中的检查结果不能替代最终复现验收。

## 官方资料核对

- [Agent workspace](https://docs.openclaw.ai/concepts/agent-workspace)：逐 Agent 的 workspace 会覆盖默认值。
- [Skills CLI](https://docs.openclaw.ai/cli/skills)：技能默认安装至所选工作区；`skills-sh:` 引用由 ClawHub 解析至同步 Commit。

配置命令及 CLI 参数同时以本机 2026.10.5 的帮助和随包文档核对。
