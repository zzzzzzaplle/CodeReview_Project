# code-review Skill 安装记录

日期：2026-10-19（Asia/Shanghai）。OpenClaw：2026.10.5。

## 安装结果

`main` 工作区已确认切换到 `/Users/macbookair/CodeReview_Project/demo/openclaw-workspace`。

Skill 已安装至：

```text
/Users/macbookair/CodeReview_Project/demo/openclaw-workspace/skills/code-review
```

`openclaw skills info code-review --agent main --json` 返回：

```json
{
  "name": "code-review",
  "eligible": true,
  "modelVisible": true,
  "commandVisible": true,
  "disabled": false,
  "blockedByAllowlist": false,
  "blockedByAgentFilter": false
}
```

`skills check --agent main --json` 中，该 Skill 同时出现在 eligible、modelVisible、commandVisible 清单中，缺失依赖为空。

## 来源与文件核对

- 用户指定条目：[ClawHub code-review](https://clawhub.ai/skills-sh/mattpocock/skills/code-review)。
- 作者仓库：[mattpocock/skills](https://github.com/mattpocock/skills)。
- 固定 Commit：`4588b32ecab9ecc9fc8cc6b6c5e7d675b6004b0d`。
- 仓库内目录：`skills/engineering/code-review`。
- 原始文件：`SKILL.md`、`agents/openai.yaml`，均为 Git 模式 100644 的普通文件。
- `SKILL.md` SHA-256：`47f4e52c21694def9c7c11cbfbf891ca35eac7a93e395797515be3c8a409ae50`。

已读取上述两个文件。Skill 描述要求主 Agent 检查比较基准、收集 Git Diff 和 Commit List、确定规范与需求来源，分别启动 Standards 和 Spec 子 Agent，再分组汇总。它还要求 `docs/agents/issue-tracker.md` 提供需求来源；后续样例会按复现方案创建此文件。安装文件与固定 Commit 的源文件经 `cmp` 比对一致。

## 安装器兼容问题与处理

直接执行用户指定的命令时：

```bash
openclaw skills install skills-sh:mattpocock/skills/code-review --agent main
```

ClawHub 将其解析到上述 Commit，并标记 `Not scanned by ClawHub`。下载后，OpenClaw 在检查整个仓库压缩包时因根目录 `AGENTS.md` 是符号链接而失败：

```text
ArchiveSecurityError: zip entry is a link: skills-4588b32ecab9ecc9fc8cc6b6c5e7d675b6004b0d/AGENTS.md
```

随后通过 Git 获取同一个 Commit，只检出 `skills/engineering/code-review` 目录，检查文件内容及文件类型，再使用本地目录安装：

```bash
openclaw skills install /private/tmp/code-review-source.6KFeYw/skills/engineering/code-review --agent main
```

命令成功，安装后的文件已复制到工作区；日常使用不依赖该临时源目录。OpenClaw 将这次安装记录为本地 path 来源；它不受 ClawHub 自动更新管理。后续升级应重新固定来源 Commit、检查文件并安装。

## 证据与后续阶段

安装证据保存于当前仓库的 `artifacts/`，并同步至方案指定的 `/Users/macbookair/CodeReview_Project/artifacts`：

- `skill-info.json`：安装成功后的 Skill 信息，替换原有 not found 记录。
- `skill-source.json`：固定 Commit、文件 SHA-256 和安装方式。
- `skill-check-code-review.json`：可用性及可见性检查结果。

Skill 安装验收已通过。模型认证、子 Agent 配置与实际两次子 Agent 调用，仍须按后续方案验收。模型 Key 不影响本次 Skill 的安装与静态可用性检查。
