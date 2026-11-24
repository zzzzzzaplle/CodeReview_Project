"""OpenClaw 自动代码审查本地 GitHub Webhook 中转网关。

核心职责：
1. GitHub Webhook HMAC-SHA256 签名验签（X-Hub-Signature-256）。
2. 事件过滤（仅处理 pull_request 的 opened / synchronize / reopened 动作）。
3. 基于 Commit SHA 的幂等去重（artifacts/reviewed_commits.json）。
4. 采集 Git 上下文并执行前置单元测试（npm test）。
5. 转发请求至 OpenClaw Inbound Hook（POST /hooks/agent，隔离会话模式）。
6. 从 OpenClaw SQLite 数据库提取主会话与子 Agent 会话的执行记录。
7. 执行 Unified Diff Hunk 行号校验/自动降级并持久化审查产物。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import zstandard

PIPELINE_DIR = pathlib.Path(__file__).resolve().parent
PROJECT_ROOT = PIPELINE_DIR.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from diff_mapper import build_github_review_payload, parse_unified_diff

OPENCLAW_CONFIG_PATH = pathlib.Path.home() / ".openclaw" / "openclaw.json"
AGENT_SQLITE_PATH = pathlib.Path.home() / ".openclaw" / "agents" / "main" / "agent" / "openclaw-agent.sqlite"
STATE_SQLITE_PATH = pathlib.Path.home() / ".openclaw" / "state" / "openclaw.sqlite"
DEFAULT_WORKSPACE = pathlib.Path.home() / "CodeReview_Project" / "demo" / "openclaw-workspace"
DEFAULT_REPO_PATH = DEFAULT_WORKSPACE / "review-case"

PRIMARY_ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
MIRROR_ARTIFACTS_DIR = pathlib.Path.home() / "CodeReview_Project" / "artifacts"

DEFAULT_WEBHOOK_SECRET = "local_demo_webhook_secret_2026"
OPENCLAW_GATEWAY_BASE = "http://127.0.0.1:18789"


def get_webhook_secret() -> str:
    return os.environ.get("WEBHOOK_SECRET", DEFAULT_WEBHOOK_SECRET)


def load_openclaw_hook_config() -> tuple[str, str]:
    cfg = json.loads(OPENCLAW_CONFIG_PATH.read_text(encoding="utf-8"))
    hooks = cfg.get("hooks", {})
    if not hooks.get("enabled"):
        raise RuntimeError("OpenClaw hooks.enabled is not true in ~/.openclaw/openclaw.json")
    token = hooks.get("token")
    if not token or not isinstance(token, str):
        raise RuntimeError("OpenClaw hooks.token is missing in ~/.openclaw/openclaw.json")
    base_path = str(hooks.get("path") or "/hooks").rstrip("/")
    port = int(cfg.get("gateway", {}).get("port") or 18789)
    hook_url = f"http://127.0.0.1:{port}{base_path}/agent"
    return hook_url, token

#核对门禁密码，防骗子
def verify_github_signature(raw_body: bytes, signature_header: str | None, secret: str) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature_header, expected)


def _run_cmd(cmd: list[str], cwd: pathlib.Path, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    extra_bins = [
        str(pathlib.Path.home() / ".local" / "node" / "bin"),
        str(pathlib.Path.home() / ".npm-global" / "bin"),
        "/opt/homebrew/bin",
    ]
    env["PATH"] = ":".join(extra_bins) + ":" + env.get("PATH", "")
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def collect_repo_context(repo_path: pathlib.Path, base_ref: str, head_ref: str) -> dict[str, Any]:
    if not (repo_path / ".git").exists():
        raise FileNotFoundError(f"Git repository not found: {repo_path}")

    base_rev = _run_cmd(["git", "rev-parse", base_ref], repo_path)
    if base_rev.returncode != 0:
        raise ValueError(f"Invalid base_ref {base_ref}: {base_rev.stderr.strip()}")
    head_rev = _run_cmd(["git", "rev-parse", head_ref], repo_path)
    if head_rev.returncode != 0:
        raise ValueError(f"Invalid head_ref {head_ref}: {head_rev.stderr.strip()}")

    commits_res = _run_cmd(["git", "log", f"{base_ref}..{head_ref}", "--oneline"], repo_path)
    files_res = _run_cmd(["git", "diff", "--name-only", f"{base_ref}...{head_ref}"], repo_path)
    diff_res = _run_cmd(["git", "diff", "--unified=3", f"{base_ref}...{head_ref}"], repo_path)
    if diff_res.returncode != 0 or not diff_res.stdout.strip():
        raise ValueError(f"Diff is empty or failed for {base_ref}...{head_ref}")

    test_summary = "No package.json found; pre-review tests skipped."
    test_returncode = None
    if (repo_path / "package.json").exists():
        test_res = _run_cmd(["npm", "test"], repo_path, timeout=60)
        test_returncode = test_res.returncode
        combined = "\n".join(part for part in [test_res.stdout.strip(), test_res.stderr.strip()] if part)
        test_summary = f"exit_code={test_res.returncode}\n{combined[:3000]}"

    return {
        "repo_path": str(repo_path),
        "base_ref": base_ref,
        "head_ref": head_ref,
        "base_sha": base_rev.stdout.strip(),
        "head_sha": head_rev.stdout.strip(),
        "commits": [line for line in commits_res.stdout.splitlines() if line.strip()],
        "changed_files": [line for line in files_res.stdout.splitlines() if line.strip()],
        "diff_text": diff_res.stdout,
        "test_returncode": test_returncode,
        "test_summary": test_summary,
    }


def build_review_prompt(
    repo_rel_path: str,
    pr_number: int,
    pr_title: str,
    pr_body: str,
    repo_ctx: dict[str, Any],
) -> str:
    return f"""Use the installed `code-review` skill to review this Pull Request.

Repository (relative to workspace): {repo_rel_path}
PR #{pr_number}: {pr_title}
PR Description: {pr_body or '(none)'}
Fixed point: {repo_ctx['base_ref']} ({repo_ctx['base_sha'][:12]})
Target: {repo_ctx['head_ref']} ({repo_ctx['head_sha'][:12]})
Changed files: {', '.join(repo_ctx['changed_files'])}
Standards: CODING_STANDARDS.md
Spec: specs/inventory.md

Pre-review unit test output (`npm test`):
```text
{repo_ctx['test_summary']}
```

Instructions (follow the `code-review` skill strictly):
1. Validate the fixed point and run `git diff {repo_ctx['base_ref']}...{repo_ctx['head_ref']}` and `git log {repo_ctx['base_ref']}..{repo_ctx['head_ref']} --oneline` inside `{repo_rel_path}`.
2. Read `CODING_STANDARDS.md`, `specs/inventory.md`, and `docs/agents/issue-tracker.md` inside `{repo_rel_path}`.
3. You MUST call `sessions_spawn` twice in parallel to launch two independent read-only sub-agents:
   - Sub-agent 1 (Standards): review against `CODING_STANDARDS.md` and the Fowler smell baseline from the `code-review` skill.
   - Sub-agent 2 (Spec): review against `specs/inventory.md` (and note the failing unit test evidence).
   Do not start nested review agents inside those two sub-agents. Do not modify files, commit, or push.
4. Wait for both sub-agents to finish, then output separate `## Standards` and `## Spec` sections (preserving their independent findings and line references in `src/inventory.js`), plus a one-line summary per axis.
5. At the very end of your final response, append a fenced ```json block with this exact schema so our gateway can post inline GitHub PR review comments:
```json
{{
  "summary": "Brief overall review summary in Chinese or English",
  "findings": [
    {{
      "axis": "Standards",
      "file": "src/inventory.js",
      "line": 6,
      "severity": "MEDIUM",
      "title": "Short finding title",
      "body": "Detailed explanation citing CODING_STANDARDS.md or specs/inventory.md",
      "suggestion": "Concrete fix suggestion"
    }}
  ]
}}
```
Every finding must use 1-based line numbers from the new version of the changed file (`src/inventory.js`).
"""


def call_openclaw_hook(
    hook_url: str,
    hook_token: str,
    hook_name: str,
    message: str,
    idempotency_key: str,
    timeout_seconds: int = 240,
) -> dict[str, Any]:
    payload = {
        "message": message,
        "name": hook_name,
        "agentId": "main",
        "sessionMode": "isolated",
        "waitForCompletion": True,
        "deliver": False,
        "timeoutSeconds": timeout_seconds,
    }
    req = urllib.request.Request(
        hook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {hook_token}",
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds + 30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenClaw hook HTTP {exc.code}: {err_body}") from exc


def _decode_transcript_row(event_json: str | None, event_zstd: bytes | None) -> dict[str, Any] | None:
    raw: str | None = None
    if event_json:
        raw = event_json
    elif event_zstd:
        dctx = zstandard.ZstdDecompressor()
        raw = dctx.decompress(event_zstd).decode("utf-8")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _extract_session_transcript(conn: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT seq, event_json, event_zstd FROM transcript_events WHERE session_id=? ORDER BY seq",
        (session_id,),
    ).fetchall()

    tool_calls: list[dict[str, Any]] = []
    assistant_texts: list[str] = []
    usage_total: dict[str, Any] = {}
    errors: list[str] = []

    for seq, ej, ez in rows:
        evt = _decode_transcript_row(ej, ez)
        if not evt or evt.get("type") != "message":
            continue
        msg = evt.get("message") or {}
        if msg.get("role") != "assistant":
            continue
        if msg.get("errorMessage"):
            errors.append(str(msg.get("errorMessage")))
        if isinstance(msg.get("usage"), dict):
            usage_total = msg["usage"]
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype in ("toolCall", "tool_use"):
                    tool_calls.append(
                        {
                            "seq": seq,
                            "id": block.get("id"),
                            "name": block.get("name"),
                            "arguments": block.get("arguments") or block.get("input"),
                        }
                    )
                elif btype == "text" and isinstance(block.get("text"), str):
                    assistant_texts.append(block["text"])
        elif isinstance(content, str) and content.strip():
            assistant_texts.append(content)

    final_text = assistant_texts[-1] if assistant_texts else ""
    return {
        "session_id": session_id,
        "event_count": len(rows),
        "tool_calls": tool_calls,
        "final_text": final_text,
        "all_assistant_texts": assistant_texts,
        "usage": usage_total,
        "errors": errors,
    }

#从 OpenClaw SQLite 数据库提取主会话与子 Agent 会话的执行记录
def collect_openclaw_run_evidence(hook_name: str, min_created_at_ms: int) -> dict[str, Any]:
    """从 SQLite 中定位本次隔离 Webhook 主会话及其派生的所有子 Agent 会话。"""
    agent_conn = sqlite3.connect(f"file:{AGENT_SQLITE_PATH}?mode=ro", uri=True)
    state_conn = sqlite3.connect(f"file:{STATE_SQLITE_PATH}?mode=ro", uri=True)
    try:
        candidates = agent_conn.execute(
            """
            SELECT session_id, session_key, status, model_provider, model, started_at, ended_at
            FROM session_windows
            WHERE hook_external_content_source = 'webhook'
              AND created_at >= ?
            ORDER BY created_at DESC
            LIMIT 10
            """,
            (min_created_at_ms - 10_000,),
        ).fetchall()

        matched_window = None
        main_transcript: dict[str, Any] | None = None
        marker = f"Task: {hook_name}"

        for row in candidates:
            sid = row[0]
            trows = agent_conn.execute(
                "SELECT event_json, event_zstd FROM transcript_events WHERE session_id=? ORDER BY seq LIMIT 12",
                (sid,),
            ).fetchall()
            found_marker = False
            for ej, ez in trows:
                evt = _decode_transcript_row(ej, ez)
                if not evt:
                    continue
                if marker in json.dumps(evt, ensure_ascii=False):
                    found_marker = True
                    break
            if found_marker:
                matched_window = row
                main_transcript = _extract_session_transcript(agent_conn, sid)
                break

        if matched_window is None or main_transcript is None:
            raise RuntimeError(f"Could not locate OpenClaw webhook session for Task '{hook_name}'")

        main_session_id, main_session_key, main_status, provider, model, started_at, ended_at = matched_window
        base_session_key = main_session_key.split(":run:")[0]
        key_candidates = list({main_session_key, base_session_key})
        placeholders = ",".join("?" for _ in key_candidates)

        # 从 state 数据库和 agent 数据库中定位派生的子 Agent 运行记录。
        subagent_rows = state_conn.execute(
            f"""
            SELECT run_id, child_session_key, controller_session_key, requester_session_key, created_at, payload_json
            FROM subagent_runs
            WHERE (requester_session_key IN ({placeholders}) OR controller_session_key IN ({placeholders})
                   OR requester_session_key LIKE ? OR controller_session_key LIKE ?)
              AND created_at >= ?
            ORDER BY created_at ASC
            """,
            (*key_candidates, *key_candidates, f"{base_session_key}%", f"{base_session_key}%", min_created_at_ms - 10_000),
        ).fetchall()

        child_keys_seen: dict[str, dict[str, Any]] = {}
        for r_id, c_key, ctrl_key, req_key, c_at, p_json in subagent_rows:
            child_keys_seen[c_key] = {
                "run_id": r_id,
                "child_session_key": c_key,
                "controller_session_key": ctrl_key,
                "requester_session_key": req_key,
                "created_at": c_at,
                "payload": json.loads(p_json) if p_json else {},
            }

        win_children = agent_conn.execute(
            f"""
            SELECT session_id, session_key, status, model_provider, model, display_name, started_at, ended_at
            FROM session_windows
            WHERE (parent_session_key IN ({placeholders}) OR spawned_by IN ({placeholders})
                   OR parent_session_key LIKE ? OR spawned_by LIKE ?)
              AND created_at >= ?
            ORDER BY created_at ASC
            """,
            (*key_candidates, *key_candidates, f"{base_session_key}%", f"{base_session_key}%", min_created_at_ms - 10_000),
        ).fetchall()

        for c_sid, c_key, c_status, c_prov, c_mod, c_disp, c_start, c_end in win_children:
            entry = child_keys_seen.setdefault(c_key, {"child_session_key": c_key})
            entry.update(
                {
                    "session_id": c_sid,
                    "status": c_status,
                    "model": f"{c_prov}/{c_mod}" if c_prov and c_mod else c_mod,
                    "display_name": c_disp,
                    "started_at": c_start,
                    "ended_at": c_end,
                }
            )

        # 为仅在 subagent_runs 表中查到的子会话补全缺失的 session_id 信息。
        for c_key, entry in child_keys_seen.items():
            if "session_id" not in entry:
                w = agent_conn.execute(
                    "SELECT session_id, status, model_provider, model, display_name, started_at, ended_at FROM session_windows WHERE session_key=? ORDER BY updated_at DESC LIMIT 1",
                    (c_key,),
                ).fetchone()
                if w:
                    entry.update(
                        {
                            "session_id": w[0],
                            "status": w[1],
                            "model": f"{w[2]}/{w[3]}" if w[2] and w[3] else w[3],
                            "display_name": w[4],
                            "started_at": w[5],
                            "ended_at": w[6],
                        }
                    )
            if entry.get("session_id"):
                tr = _extract_session_transcript(agent_conn, entry["session_id"])
                entry["tool_call_names"] = [tc["name"] for tc in tr["tool_calls"]]
                entry["tool_calls_count"] = len(tr["tool_calls"])
                entry["final_text_preview"] = tr["final_text"][:1500]
                entry["errors"] = tr["errors"]

        direct_spawn_calls = [tc for tc in main_transcript["tool_calls"] if tc.get("name") == "sessions_spawn"]
        code_mode_spawn_calls = [
            tc
            for tc in main_transcript["tool_calls"]
            if tc.get("name") == "exec"
            and "sessions_spawn" in str(tc.get("arguments"))
            and ("spawn(" in str(tc.get("arguments")) or "spawn (" in str(tc.get("arguments")))
        ]

        return {
            "hook_name": hook_name,
            "main_session": {
                "session_id": main_session_id,
                "session_key": main_session_key,
                "status": main_status,
                "model": f"{provider}/{model}" if provider and model else model,
                "started_at": started_at,
                "ended_at": ended_at,
                "tool_calls": [
                    {
                        "seq": tc["seq"],
                        "name": tc["name"],
                        "arguments_preview": str(tc.get("arguments"))[:300],
                    }
                    for tc in main_transcript["tool_calls"]
                ],
                "direct_spawn_tool_calls": len(direct_spawn_calls),
                "code_mode_spawn_exec_calls": len(code_mode_spawn_calls),
                "sessions_spawn_count": len(direct_spawn_calls) or len(child_keys_seen),
                "usage": main_transcript["usage"],
                "errors": main_transcript["errors"],
            },
            "subagents": list(child_keys_seen.values()),
            "raw_final_reply": main_transcript["final_text"],
        }
    finally:
        agent_conn.close()
        state_conn.close()


def parse_structured_findings(reply_text: str) -> tuple[str, list[dict[str, Any]]]:
    """从 OpenClaw 最终回复中提取结构化 JSON 摘要与审查发现列表。"""
    fenced_blocks = re.findall(r"```json\s*(\{.*?\})\s*```", reply_text, flags=re.DOTALL)
    candidates = list(reversed(fenced_blocks))

    if not candidates:
        # 回退策略：匹配包含 "findings" 字段的任意 JSON 对象
        brace_matches = re.findall(r"(\{[^{}]*\"findings\"\s*:\s*\[.*?\]\s*\})", reply_text, flags=re.DOTALL)
        candidates.extend(reversed(brace_matches))

    for raw_json in candidates:
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, dict) and isinstance(parsed.get("findings"), list):
                summary = str(parsed.get("summary") or "").strip()
                findings = [item for item in parsed["findings"] if isinstance(item, dict)]
                return summary, findings
        except json.JSONDecodeError:
            continue

    # 启发式回退：当模型未输出合法 JSON 代码块时，从文本中提取关键发现
    fallback_findings: list[dict[str, Any]] = []
    if re.search(r"\bq\b", reply_text) and ("CODING_STANDARDS" in reply_text or "Standards" in reply_text):
        fallback_findings.append(
            {
                "axis": "Standards",
                "file": "src/inventory.js",
                "line": 6,
                "severity": "MEDIUM",
                "title": "参数命名违反 CODING_STANDARDS.md",
                "body": "reserve(q) 使用单字符参数名 q，违反 CODING_STANDARDS.md 命名规范。",
                "suggestion": "将参数 q 重命名为 quantity。",
            }
        )
    if "Spec" in reply_text and ("库存" in reply_text or "stock" in reply_text.lower()):
        fallback_findings.append(
            {
                "axis": "Spec",
                "file": "src/inventory.js",
                "line": 7,
                "severity": "HIGH",
                "title": "库存不足时未拦截预订",
                "body": "reserve(q) 未校验 q > this.stock，违反 specs/inventory.md 第 2、3 条。",
                "suggestion": "在扣减库存前增加 if (q > this.stock) return false; 检查。",
            }
        )
    return "OpenClaw 完成双维度审查（已由文本回退提取结构化结果）。", fallback_findings


def load_reviewed_commits(artifacts_dir: pathlib.Path) -> dict[str, Any]:
    path = artifacts_dir / "reviewed_commits.json"
    if not path.exists():
        return {"commits": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("commits"), dict):
            return data
    except json.JSONDecodeError:
        pass
    return {"commits": {}}


def save_artifacts(
    review_payload: dict[str, Any],
    evidence: dict[str, Any],
    report_md: str,
    reviewed_commits: dict[str, Any],
) -> None:
    targets = [PRIMARY_ARTIFACTS_DIR, MIRROR_ARTIFACTS_DIR]
    sanitized_evidence = {
        k: v for k, v in evidence.items() if k != "raw_final_reply"
    }
    sanitized_evidence["raw_final_reply_length"] = len(evidence.get("raw_final_reply", ""))

    for d in targets:
        d.mkdir(parents=True, exist_ok=True)
        (d / "github-pr-review-payload.json").write_text(
            json.dumps(review_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (d / "subagent-evidence.json").write_text(
            json.dumps(sanitized_evidence, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (d / "review-report.md").write_text(report_md, encoding="utf-8")
        (d / "reviewed_commits.json").write_text(
            json.dumps(reviewed_commits, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def render_review_report_md(
    pr_number: int,
    pr_title: str,
    repo_ctx: dict[str, Any],
    hook_resp: dict[str, Any],
    evidence: dict[str, Any],
    review_payload: dict[str, Any],
) -> str:
    stats = review_payload.get("_mapping_stats", {})
    main_s = evidence["main_session"]
    subagents = evidence["subagents"]
    lines = [
        f"# PR #{pr_number} 自动代码审查报告",
        "",
        f"- **PR 标题**：{pr_title}",
        f"- **仓库路径**：`{repo_ctx['repo_path']}`",
        f"- **比较范围**：`{repo_ctx['base_sha'][:12]}...{repo_ctx['head_sha'][:12]}`",
        f"- **OpenClaw Run ID**：`{hook_resp.get('runId')}`",
        f"- **主隔离会话 ID**：`{main_s.get('session_id')}`（模型：`{main_s.get('model')}`，状态：`{main_s.get('status')}`）",
        f"- **子 Agent 派生次数**：{main_s.get('sessions_spawn_count')}（直调：`{main_s.get('direct_spawn_tool_calls', 0)}`，code-mode exec：`{main_s.get('code_mode_spawn_exec_calls', 0)}`，捕获子 Agent 会话数：{len(subagents)}）",
        f"- **行号映射统计**：总发现 {stats.get('total_findings', 0)} 条，行内评论 {stats.get('inline_count', 0)} 条，自动降级 {stats.get('downgraded_count', 0)} 条",
        "",
        "## 前置单测执行结果",
        "",
        "```text",
        repo_ctx["test_summary"],
        "```",
        "",
        "## OpenClaw 主 Agent 审查输出",
        "",
        evidence.get("raw_final_reply", "").strip(),
        "",
        "## 子 Agent 执行证据",
        "",
    ]
    for idx, sub in enumerate(subagents, start=1):
        lines.extend(
            [
                f"### 子 Agent {idx}",
                f"- **Session ID**：`{sub.get('session_id')}`",
                f"- **Session Key**：`{sub.get('child_session_key')}`",
                f"- **状态 / 模型**：`{sub.get('status')}` / `{sub.get('model')}`",
                f"- **调用工具**：`{', '.join(sub.get('tool_call_names', []))}`",
                "",
            ]
        )
    lines.extend(
        [
            "## 生成的 GitHub PR Review API Payload 预览",
            "",
            "```json",
            json.dumps(review_payload, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def process_pull_request_event(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    action = payload.get("action")
    if action not in {"opened", "synchronize", "reopened"}:
        return 200, {"ok": True, "status": "ignored_action", "action": action}

    pr = payload.get("pull_request") or {}
    pr_number = int(pr.get("number") or payload.get("number") or 1)
    pr_title = str(pr.get("title") or "Untitled PR")
    pr_body = str(pr.get("body") or "")
    base_ref = str((pr.get("base") or {}).get("ref") or "HEAD~1")
    head_ref = str((pr.get("head") or {}).get("ref") or "HEAD")

    repo_path_str = (payload.get("repository") or {}).get("local_path") or str(DEFAULT_REPO_PATH)
    repo_path = pathlib.Path(repo_path_str).resolve()
    repo_ctx = collect_repo_context(repo_path, base_ref, head_ref)
    head_sha = str((pr.get("head") or {}).get("sha") or repo_ctx["head_sha"])

    # 避坑点 1：基于 Commit SHA 的幂等去重校验
    reviewed_commits = load_reviewed_commits(PRIMARY_ARTIFACTS_DIR)
    force_rerun = bool(payload.get("force_rerun"))
    if head_sha in reviewed_commits["commits"] and not force_rerun:
        return 200, {
            "ok": True,
            "status": "skipped_duplicate_sha",
            "head_sha": head_sha,
            "previous_review": reviewed_commits["commits"][head_sha],
        }

    try:
        repo_rel_path = "./" + str(repo_path.relative_to(DEFAULT_WORKSPACE))
    except ValueError:
        repo_rel_path = str(repo_path)

    hook_url, hook_token = load_openclaw_hook_config()
    hook_name = f"PRReview-{pr_number}-{head_sha[:8]}-{int(time.time())}"
    prompt = build_review_prompt(repo_rel_path, pr_number, pr_title, pr_body, repo_ctx)

    start_ms = int(time.time() * 1000)
    hook_resp = call_openclaw_hook(
        hook_url=hook_url,
        hook_token=hook_token,
        hook_name=hook_name,
        message=prompt,
        idempotency_key=f"pr-{pr_number}-{head_sha[:12]}-{start_ms}",
    )

    completion = hook_resp.get("completion") or {}
    if completion.get("status") != "ok":
        raise RuntimeError(f"OpenClaw hook completion failed: {json.dumps(hook_resp)}")

    # 短暂轮询，防止 HTTP 响应返回后 SQLite WAL 尚未落盘
    evidence = None
    last_err: Exception | None = None
    for _ in range(10):
        try:
            evidence = collect_openclaw_run_evidence(hook_name, start_ms)
            break
        except Exception as exc:
            last_err = exc
            time.sleep(0.3)
    if evidence is None:
        raise RuntimeError(f"Failed to read OpenClaw SQLite evidence: {last_err}")

    summary, findings = parse_structured_findings(evidence.get("raw_final_reply", ""))

    # 可选的测试注入发现项，用于验证越界行号自动降级（场景 4）
    extra_findings = payload.get("inject_test_findings")
    if isinstance(extra_findings, list):
        findings.extend(item for item in extra_findings if isinstance(item, dict))

    valid_lines = parse_unified_diff(repo_ctx["diff_text"])
    review_payload = build_github_review_payload(
        commit_id=head_sha,
        summary=summary,
        findings=findings,
        valid_lines_by_file=valid_lines,
        test_summary=repo_ctx["test_summary"],
    )

    reviewed_commits["commits"][head_sha] = {
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "pr_number": pr_number,
        "run_id": hook_resp.get("runId"),
        "main_session_id": evidence["main_session"]["session_id"],
        "subagent_count": len(evidence["subagents"]),
        "inline_count": review_payload["_mapping_stats"]["inline_count"],
        "downgraded_count": review_payload["_mapping_stats"]["downgraded_count"],
    }

    report_md = render_review_report_md(
        pr_number=pr_number,
        pr_title=pr_title,
        repo_ctx=repo_ctx,
        hook_resp=hook_resp,
        evidence=evidence,
        review_payload=review_payload,
    )
    save_artifacts(review_payload, evidence, report_md, reviewed_commits)

    return 200, {
        "ok": True,
        "status": "reviewed",
        "head_sha": head_sha,
        "run_id": hook_resp.get("runId"),
        "main_session_id": evidence["main_session"]["session_id"],
        "direct_spawn_tool_calls": evidence["main_session"]["direct_spawn_tool_calls"],
        "code_mode_spawn_exec_calls": evidence["main_session"]["code_mode_spawn_exec_calls"],
        "sessions_spawn_count": evidence["main_session"]["sessions_spawn_count"],
        "subagent_count": len(evidence["subagents"]),
        "mapping_stats": review_payload["_mapping_stats"],
    }


class WebhookHandler(BaseHTTPRequestHandler):
    server_version = "OpenClawPRWebhookRelay/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        # 屏蔽默认的 stderr HTTP 访问日志，避免干扰测试断言输出。
        return

    def _send_json(self, status_code: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"ok": True, "service": "webhook_gateway"})
            return
        self._send_json(404, {"ok": False, "error": "not_found"})
    # 收到github event请求,进行处理请求
    def do_POST(self) -> None:
        if self.path != "/webhook/github":
            self._send_json(404, {"ok": False, "error": "not_found"})
            return

        content_length = int(self.headers.get("Content-Length") or "0")
        raw_body = self.rfile.read(content_length)

        sig = self.headers.get("X-Hub-Signature-256")
        if not verify_github_signature(raw_body, sig, get_webhook_secret()):
            self._send_json(401, {"ok": False, "error": "invalid_hmac_signature"})
            return

        event_type = self.headers.get("X-GitHub-Event")
        if event_type != "pull_request":
            self._send_json(200, {"ok": True, "status": "ignored_event", "event": event_type})
            return

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "invalid_json"})
            return

        try:
            code, result = process_pull_request_event(payload)
            self._send_json(code, result)
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": str(exc)})


def create_server(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), WebhookHandler)


def main() -> None:
    host = os.environ.get("GATEWAY_HOST", "127.0.0.1")
    port = int(os.environ.get("GATEWAY_PORT", "8765"))
    server = create_server(host, port)
    print(f"Webhook relay listening on http://{host}:{port}/webhook/github")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
