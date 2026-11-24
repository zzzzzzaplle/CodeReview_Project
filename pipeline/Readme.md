### 脚本承担角色
trigger_pr_event.py	测试演员（假装是 GitHub）	用来一键演练 4 个测试场景（正常审查、重复提交跳过、假签名拦截、越界行号降级）
webhook_gateway.py	大管家（中转网关）	

### 详细操作:
#### 一.在真正调用openclaw之前,有三道门槛
1. 真假闸门（HMAC 验签）：不是自己人发的请求，不叫醒 AI。
2. 有效闸门（事件过滤）：没改代码的事件，不叫醒 AI。
3. 去重闸门（Commit SHA 幂等）：审过的代码，绝不再叫醒一次

#### 二.锚定上下文
1.先用传统命令跑完` git diff `和 `npm test` ,拿到客观代码差异和代码报错(Prompt 里包含了 PR 基本信息、变更文件列表（changed_files）和 npm test 报错日志)
2.再用`sessionMode: "isolated"`（隔离会话）把这些事实连同指令一起交给 OpenClaw

....
(openclaw内部审查代码)
....


#### 三.查验agent工具历史
先去 OpenClaw SQLite 里捞执行记录和最终回复（拿 raw_final_reply 和子 Agent 证据）
存进 `subagent-evidence.json`

#### 四.输出校验

```text
OpenClaw 原始回复（一大段 Markdown 文本）
        │
        ▼  第 1 关：parse_structured_findings（管“格式/结构”）
  (summary, findings)  ← 纯净的 Python 结构化列表
        │
        ▼  第 2 关：diff_mapper.py（管“行号/GitHub 规则”）
  github-pr-review-payload.json ← 100% 符合 GitHub API 规范的最终请求体
```






### prompt_template
OpenClaw 的 Prompt 就在 webhook_gateway.py#L137-L186 的 build_review_prompt() 函数里；
在 webhook_gateway.py#L640-L649 通过 call_openclaw_hook() 作为 message 字段发给 OpenClaw 的 POST /hooks/agent 接口
内容如下
```
Use the installed `code-review` skill to review this Pull Request.

【第 1 块：PR 基本上下文】
Repository (relative to workspace): {repo_rel_path}
PR #{pr_number}: {pr_title}
PR Description: {pr_body or '(none)'}
Fixed point: {base_ref} ({base_sha})
Target: {head_ref} ({head_sha})
Changed files: {changed_files}
Standards: CODING_STANDARDS.md
Spec: specs/inventory.md

【第 2 块：前置信息的情况】
Pre-review unit test output (`npm test`):
{repo_ctx['test_summary']}

【第 3 块：5 条硬性执行指令 + 约定的 JSON 输出】
Instructions (follow the `code-review` skill strictly):
1. Validate the fixed point and run `git diff ...` and `git log ...` inside `{repo_rel_path}`.
2. Read `CODING_STANDARDS.md`, `specs/inventory.md`, and `docs/agents/issue-tracker.md` inside `{repo_rel_path}`.
3. You MUST call `sessions_spawn` twice in parallel to launch two independent read-only sub-agents:
   - Sub-agent 1 (Standards): review against `CODING_STANDARDS.md` and the Fowler smell baseline from the `code-review` skill.
   - Sub-agent 2 (Spec): review against `specs/inventory.md` (and note the failing unit test evidence).
   Do not start nested review agents inside those two sub-agents. Do not modify files, commit, or push.
4. Wait for both sub-agents to finish, then output separate `## Standards` and `## Spec` sections ..., plus a one-line summary per axis.
5. At the very end of your final response, append a fenced ```json block with this exact schema ...
```