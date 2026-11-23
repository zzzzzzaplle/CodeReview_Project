# OpenClaw Webhook 自动代码审查链路本机复现记录

日期：2026-11-23（Asia/Shanghai）。OpenClaw 版本：2026.10.5（`1b58cb9`），默认模型：`deepseek/deepseek-flash`。

## 1. 链路架构

本机复现了“Webhook 触发 → 上下文与单测前置采集 → OpenClaw 双子 Agent 审查 → Diff 行号校验与本地回写”四环链路：

```text
trigger_pr_event.py（模拟 GitHub pull_request Webhook + HMAC-SHA256 签名）
        │  POST /webhook/github (X-Hub-Signature-256)
        ▼
pipeline/webhook_gateway.py（本地中转网关）
  ├─ 验签：HMAC-SHA256 校验，非法请求返回 HTTP 401
  ├─ 去重：检查 artifacts/reviewed_commits.json，重复 head_sha 直接跳过
  ├─ 取数：执行 git rev-parse / git log / git diff --unified=3
  ├─ 前置单测：执行 npm test，捕获失败用例输出一并交给审查 Agent
  ├─ 触发 OpenClaw：POST http://127.0.0.1:18789/hooks/agent
  │    (Authorization: Bearer <hooks.token>, sessionMode: "isolated", waitForCompletion: true)
  ├─ 证据提取：从 OpenClaw SQLite 会话库提取主会话、toolCall 与 subagent_runs 记录
  └─ 回写校验：pipeline/diff_mapper.py 计算 Unified Diff RIGHT 侧合法行号，
               命中 Hunk 输出行内评论，越界行号降级写入 PR 顶层 body
```

## 2. 环境与 OpenClaw 配置

1. 子 Agent 并发与深度限制：
   - `agents.defaults.subagents.maxConcurrent = 2`
   - `agents.defaults.subagents.maxSpawnDepth = 1`
2. Inbound HTTP Hooks 配置（`~/.openclaw/openclaw.json`，实际 Token 保存在本机配置中，未提交至仓库）：
   - `hooks.enabled = true`
   - `hooks.path = "/hooks"`
   - `hooks.allowedAgentIds = ["main"]`
   - `hooks.allowRequestSessionKey = false`
3. 验证命令：
   - `openclaw config validate` 返回 `Config valid: ~/.openclaw/openclaw.json`
   - `openclaw gateway restart` 重启 LaunchAgent 后，`openclaw gateway status` 显示运行中且 `Connectivity check: ok`。

## 3. 代码与测试仓库组成

- 待审查样例仓库：`/Users/macbookair/CodeReview_Project/demo/openclaw-workspace/review-case`
  - 基准 Commit `d23d3b2`：包含 `specs/inventory.md`、`CODING_STANDARDS.md`、`docs/agents/issue-tracker.md`、`package.json`、`test/inventory.test.js`。
  - 待审 Commit `cabb59b`：新增 `src/inventory.js`（11 行），包含两类预设缺陷：
    1. 公共方法 `reserve(q)` 使用单字符参数名 `q`，违反 `CODING_STANDARDS.md`。
    2. 缺少 `q > this.stock` 检查，直接扣减库存并返回 `true`，导致单测 `库存不足时拒绝预订且库存不变` 失败。
- 本地链路脚本（位于仓库 `pipeline/`）：
  - [diff_mapper.py](file:///Users/macbookair/Desktop/CodeBase/CodeReview_Project/pipeline/diff_mapper.py)：解析 Unified Diff Hunk，计算 `RIGHT` 侧有效行号集合并执行越界降级。
  - [webhook_gateway.py](file:///Users/macbookair/Desktop/CodeBase/CodeReview_Project/pipeline/webhook_gateway.py)：本地 HTTP 中转网关，承担 HMAC 验签、SHA 幂等、Git 取数、前置单测、调用 `/hooks/agent`、读取 SQLite 轨迹（兼容 `event_json` 与 Zstandard 压缩的 `event_zstd`）及生成回写文件。
  - [trigger_pr_event.py](file:///Users/macbookair/Desktop/CodeBase/CodeReview_Project/pipeline/trigger_pr_event.py)：自动化触发并验收 4 个测试场景。

## 4. 四项实测验收结果

执行 `python3 pipeline/trigger_pr_event.py` 返回退出码 `0`，4 个场景结果如下：

| 场景 | 验证目标 | 实测结果 |
|---|---|---|
| 场景 1 | 合法 HMAC 签名触发完整审查链路 | HTTP `200`，`status="reviewed"`；OpenClaw Run ID `0eb18f58-f9e0-4d6d-b1ea-c22103698514`，主隔离会话 `c95b63fa-1a6f-4635-9970-b4fdfe416d69`；捕获 2 个子 Agent 会话，产出 4 条行内评论（`src/inventory.js` 第 6、7、8 行） |
| 场景 2 | 重复 Commit SHA 幂等去重 | 对同一 `cabb59b3b5fe870e5cb8c07df73f1e7700d464cc` 再次推送，返回 HTTP `200` 且 `status="skipped_duplicate_sha"`，跳过模型调用 |
| 场景 3 | 非法 HMAC 签名拦截 | 传入伪造 `X-Hub-Signature-256` 请求头，网关返回 HTTP `401`（`invalid_hmac_signature`） |
| 场景 4 | Unified Diff 行号越界降级 | 将 `src/inventory.js:6` 与越界测试项 `src/inventory.js:999` 传入 `build_github_review_payload`；第 6 行保留在 `comments` 数组，第 999 行自动移入顶层 `body` 降级区块（`line_not_in_diff_hunk`） |

### 本机实测的双子 Agent 与隔离运行记录

- 主隔离会话：`c95b63fa-1a6f-4635-9970-b4fdfe416d69`（`model: deepseek/deepseek-flash`，总消耗 `32518` tokens），在 code-mode `exec` 调用中读取 `code-review` Skill 与仓库文件后，通过 `sessions_spawn` 并行派生以下两个只读子 Agent：
  1. `Standards` 子 Agent（`run_id: 22f0d879-2574-4013-9c07-d2d05c3f5f0c`，`label: PR Standards review`，`status: ok`）：定位 `src/inventory.js:6` 参数 `q` 违反 `CODING_STANDARDS.md`，并标注 `Primitive Obsession` 主观建议项。
  2. `Spec` 子 Agent（`run_id: 088b0a75-af59-4ce1-a66d-bbf84e191acb`，`label: PR Spec review`，`status: ok`）：结合前置 `npm test` 失败堆栈，定位 `src/inventory.js:8` 缺失库存充足性校验与第 7 行正整数校验缺失。

## 5. 运行产物

产物保存在 `artifacts/`（并同步至 `/Users/macbookair/CodeReview_Project/artifacts/`）：

- [github-pr-review-payload.json](file:///Users/macbookair/Desktop/CodeBase/CodeReview_Project/artifacts/github-pr-review-payload.json)：符合 GitHub PR Reviews API 结构的最终回写 JSON（`event: REQUEST_CHANGES`，4 条行内评论全部命中 Hunk）。
- [review-report.md](file:///Users/macbookair/Desktop/CodeBase/CodeReview_Project/artifacts/review-report.md)：完整 Markdown 审查报告（含前置单测结果、双维度审查文本及子 Agent 摘要）。
- [subagent-evidence.json](file:///Users/macbookair/Desktop/CodeBase/CodeReview_Project/artifacts/subagent-evidence.json)：OpenClaw 主会话与 `subagent_runs` 轨迹取证记录。
- [reviewed_commits.json](file:///Users/macbookair/Desktop/CodeBase/CodeReview_Project/artifacts/reviewed_commits.json)：已审查 Commit SHA 幂等状态记录。
- [degradation-check-payload.json](file:///Users/macbookair/Desktop/CodeBase/CodeReview_Project/artifacts/degradation-check-payload.json)：场景 4 越界行号自动降级输出验证样本。

## 6. 复现命令

```bash
python3 pipeline/diff_mapper.py
python3 pipeline/trigger_pr_event.py
```

如需单独常驻启动本地网关接收外部 Webhook：

```bash
WEBHOOK_SECRET="local_demo_webhook_secret_2026" GATEWAY_PORT=8765 python3 pipeline/webhook_gateway.py
```
