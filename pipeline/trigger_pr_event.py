"""End-to-end trigger and verification harness for the OpenClaw PR code review pipeline.

Runs 4 verification scenarios against the local webhook relay gateway:
1. Valid HMAC-signed pull_request webhook -> OpenClaw isolated review + dual sub-agents + artifacts.
2. Duplicate head_sha webhook -> idempotent skip without re-running the LLM.
3. Invalid HMAC signature -> HTTP 401 rejection.
4. Out-of-hunk line number -> automatic degradation to top-level PR summary body (prevents 422).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import pathlib
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from typing import Any

PIPELINE_DIR = pathlib.Path(__file__).resolve().parent
PROJECT_ROOT = PIPELINE_DIR.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from diff_mapper import build_github_review_payload, parse_unified_diff
from webhook_gateway import (
    DEFAULT_REPO_PATH,
    MIRROR_ARTIFACTS_DIR,
    PRIMARY_ARTIFACTS_DIR,
    collect_repo_context,
    create_server,
    get_webhook_secret,
)


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _post_webhook(
    base_url: str,
    payload: dict[str, Any],
    signature: str,
    event: str = "pull_request",
    timeout: int = 300,
) -> tuple[int, dict[str, Any]]:
    raw = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/webhook/github",
        data=raw,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": event,
            "X-Hub-Signature-256": signature,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _get_sha(repo: pathlib.Path, ref: str) -> str:
    res = subprocess.run(
        ["git", "rev-parse", ref],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip()


def _reset_idempotency_state() -> None:
    for d in (PRIMARY_ARTIFACTS_DIR, MIRROR_ARTIFACTS_DIR):
        p = d / "reviewed_commits.json"
        if p.exists():
            p.unlink()


def main() -> int:
    _reset_idempotency_state()

    repo = DEFAULT_REPO_PATH
    base_sha = _get_sha(repo, "HEAD~1")
    head_sha = _get_sha(repo, "HEAD")
    secret = get_webhook_secret()

    server = create_server("127.0.0.1", 0)
    port = server.server_address[1]
    base_url = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        # Health check
        with urllib.request.urlopen(f"{base_url}/health", timeout=5) as resp:
            assert resp.status == 200

        pr_payload = {
            "action": "opened",
            "number": 1,
            "repository": {
                "full_name": "demo/review-case",
                "local_path": str(repo),
            },
            "pull_request": {
                "number": 1,
                "title": "feat: implement inventory reservation",
                "body": "Implement Inventory.reserve according to specs/inventory.md and CODING_STANDARDS.md.",
                "base": {"ref": "HEAD~1", "sha": base_sha},
                "head": {"ref": "HEAD", "sha": head_sha},
            },
        }
        raw_pr = json.dumps(pr_payload).encode("utf-8")
        valid_sig = _sign(raw_pr, secret)

        print(f"[Scenario 1] Sending signed PR webhook for commit {head_sha[:12]} to {base_url}/webhook/github ...")
        code1, res1 = _post_webhook(base_url, pr_payload, valid_sig, timeout=300)
        print("[Scenario 1] Response:", code1, json.dumps(res1, ensure_ascii=False))
        assert code1 == 200 and res1.get("ok") is True, f"Scenario 1 failed: {res1}"
        assert res1.get("status") == "reviewed", f"Expected status='reviewed', got {res1}"
        assert res1.get("subagent_count", 0) >= 2, f"Expected >=2 subagent sessions, got {res1}"
        assert (
            res1.get("direct_spawn_tool_calls", 0) >= 2
            or res1.get("code_mode_spawn_exec_calls", 0) >= 1
        ), f"Expected sessions_spawn invocation evidence, got {res1}"

        review_json_path = PRIMARY_ARTIFACTS_DIR / "github-pr-review-payload.json"
        evidence_path = PRIMARY_ARTIFACTS_DIR / "subagent-evidence.json"
        report_path = PRIMARY_ARTIFACTS_DIR / "review-report.md"
        assert review_json_path.exists() and evidence_path.exists() and report_path.exists()

        review_payload = json.loads(review_json_path.read_text(encoding="utf-8"))
        repo_ctx = collect_repo_context(repo, "HEAD~1", "HEAD")
        valid_lines = parse_unified_diff(repo_ctx["diff_text"])
        for c in review_payload.get("comments", []):
            fpath = c["path"]
            line_no = c["line"]
            assert fpath in valid_lines and line_no in valid_lines[fpath], (
                f"Invalid inline comment line {fpath}:{line_no} not in {valid_lines}"
            )
        assert len(review_payload.get("comments", [])) >= 2, (
            f"Expected >=2 inline comments (Standards + Spec), got {review_payload.get('comments')}"
        )
        bodies_joined = "\n".join(c["body"] for c in review_payload["comments"])
        assert "[Standards]" in bodies_joined and "[Spec]" in bodies_joined, (
            f"Expected both [Standards] and [Spec] comments in {bodies_joined}"
        )
        print(
            f"[Scenario 1] Verified: inline_comments={len(review_payload['comments'])}, "
            f"subagents={res1['subagent_count']}, spawn_calls={res1['sessions_spawn_count']}"
        )

        # Scenario 2: Duplicate commit SHA idempotency check
        print(f"[Scenario 2] Re-sending duplicate commit {head_sha[:12]} ...")
        code2, res2 = _post_webhook(base_url, pr_payload, valid_sig, timeout=30)
        print("[Scenario 2] Response:", code2, json.dumps(res2, ensure_ascii=False))
        assert code2 == 200 and res2.get("status") == "skipped_duplicate_sha", f"Scenario 2 failed: {res2}"

        # Scenario 3: Invalid HMAC signature rejection
        print("[Scenario 3] Sending invalid HMAC signature ...")
        code3, res3 = _post_webhook(base_url, pr_payload, "sha256=" + "0" * 64, timeout=15)
        print("[Scenario 3] Response:", code3, json.dumps(res3, ensure_ascii=False))
        assert code3 == 401 and res3.get("error") == "invalid_hmac_signature", f"Scenario 3 failed: {res3}"

        # Scenario 4: Out-of-hunk line degradation check against real repo diff
        print("[Scenario 4] Verifying out-of-hunk line degradation (line 999 -> PR summary body) ...")
        injected_findings = [
            {
                "axis": "Standards",
                "file": "src/inventory.js",
                "line": 6,
                "severity": "MEDIUM",
                "title": "参数命名违反项目规范",
                "body": "reserve(q) 使用单字符参数名 q。",
                "suggestion": "重命名为 quantity。",
            },
            {
                "axis": "Spec",
                "file": "src/inventory.js",
                "line": 999,
                "severity": "HIGH",
                "title": "越界行号降级验证项",
                "body": "该行号超出 Unified Diff Hunk 范围，必须自动降级而非触发 422。",
                "suggestion": "在汇总区展示该条建议。",
            },
        ]
        degraded_payload = build_github_review_payload(
            commit_id=head_sha,
            summary="Diff 行号映射与降级自测",
            findings=injected_findings,
            valid_lines_by_file=valid_lines,
            test_summary=repo_ctx["test_summary"],
        )
        assert len(degraded_payload["comments"]) == 1
        assert degraded_payload["comments"][0]["line"] == 6
        assert degraded_payload["_mapping_stats"]["downgraded_count"] == 1
        assert "越界行号降级验证项" in degraded_payload["body"]
        for d in (PRIMARY_ARTIFACTS_DIR, MIRROR_ARTIFACTS_DIR):
            (d / "degradation-check-payload.json").write_text(
                json.dumps(degraded_payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        print("[Scenario 4] Verified: line 6 kept inline, line 999 downgraded into top-level body.")
        print("ALL 4 SCENARIOS PASSED.")
        return 0
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
