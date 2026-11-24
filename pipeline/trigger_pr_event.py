"""
OpenClaw PR 代码审查流水线的端到端触发与验证脚本。

针对本地 Webhook 中转网关依次执行 4 个验证场景：
1. 合法 HMAC 签名的 pull_request Webhook -> OpenClaw 隔离会话审查 + 双子 Agent 派生 + 产物落盘。
2. 重复 head_sha 的 Webhook -> 幂等跳过，不重复调用大模型。
3. 非法 HMAC 签名 -> 返回 HTTP 401 拒绝。
4. 超出 Diff Hunk 范围的行号 -> 自动降级至顶层 PR 汇总正文（避免 422 报错）。
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
    payload: dict[str, Any], #假的github通知
    signature: str, #已经算好的HMAC签名
    event: str = "pull_request", #事件类型
    timeout: int = 300,
) -> tuple[int, dict[str, Any]]: #返回状态码——响应字典
    raw = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/webhook/github",
        data=raw,
        headers={
            "Content-Type": "application/json", #告诉网关按照json解析
            "X-GitHub-Event": event, #github标准头说明这是哪类事件,真实github也发这个
            "X-Hub-Signature-256": signature, #github 标准签名头
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp: #发出
            return resp.status, json.loads(resp.read().decode("utf-8")) #正确就返回200
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8")) #错误就返回401


def _get_sha(repo: pathlib.Path, ref: str) -> str:
    res = subprocess.run(
        ["git", "rev-parse", ref],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip()


def _reset_idempotency_state() -> None:  #排除已经审查过的
    for d in (PRIMARY_ARTIFACTS_DIR, MIRROR_ARTIFACTS_DIR): #主目录+镜像目录。d是文件夹
        p = d / "reviewed_commits.json"                     #reviewedxxx.json记录那些commit已经审查过
        if p.exists():                                      #如果存在就删除 
            p.unlink()                                      #删除文件


def main() -> int:
    _reset_idempotency_state()

    repo = DEFAULT_REPO_PATH
    base_sha = _get_sha(repo, "HEAD~1") #HEAD ~1 就是上一次的版本
    head_sha = _get_sha(repo, "HEAD") #现在的版本
    secret = get_webhook_secret() #双方握手暗号

    server = create_server("127.0.0.1", 0) #这里设置为0(不容易遇到端口占用问题),是建立一个服务器 等别人敲门
    port = server.server_address[1] #端口是系统分配的,这里需要问一下服务器要回来
    base_url = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True) #daemon 守护线程,等程序结束 他也死亡
    thread.start() #serve_forever 是一个死循环,把这个死循环丢到另一个thread去跑

    try:
        # 健康检查
        with urllib.request.urlopen(f"{base_url}/health", timeout=5) as resp: #发送get请求,服务器返回后得到响应对象叫resp
            assert resp.status == 200

        pr_payload = { #这段在伪造一个github webhook请求体,手抄一份假的github通知 payload就是载荷,记录谁,在那个仓库,那个pr,干了什么的一坨json
            "action": "opened",
            "number": 1,
            "repository": {
                "full_name": "demo/review-case",
                "local_path": str(repo), #告诉网关仓库在本地的哪里
            },
            "pull_request": { #(标题,描述,base,head的版本与SHA)
                "number": 1,
                "title": "feat: implement inventory reservation",
                "body": "Implement Inventory.reserve according to specs/inventory.md and CODING_STANDARDS.md.",
                "base": {"ref": "HEAD~1", "sha": base_sha},
                "head": {"ref": "HEAD", "sha": head_sha},
            },
        }
        raw_pr = json.dumps(pr_payload).encode("utf-8") #json就是把python字段变成一段json文字
        valid_sig = _sign(raw_pr, secret) #用 hmac 把字节和暗号搅在一起，算出一串 "sha256=..."

        print(f"[Scenario 1] Sending signed PR webhook for commit {head_sha[:12]} to {base_url}/webhook/github ...")
        code1, res1 = _post_webhook(base_url, pr_payload, valid_sig, timeout=300)#把信寄出去
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

        # 场景 2：重复 Commit SHA 幂等性校验
        print(f"[Scenario 2] Re-sending duplicate commit {head_sha[:12]} ...")
        code2, res2 = _post_webhook(base_url, pr_payload, valid_sig, timeout=30)
        print("[Scenario 2] Response:", code2, json.dumps(res2, ensure_ascii=False))
        assert code2 == 200 and res2.get("status") == "skipped_duplicate_sha", f"Scenario 2 failed: {res2}"

        # 场景 3：非法 HMAC 签名拦截
        print("[Scenario 3] Sending invalid HMAC signature ...")
        code3, res3 = _post_webhook(base_url, pr_payload, "sha256=" + "0" * 64, timeout=15)
        print("[Scenario 3] Response:", code3, json.dumps(res3, ensure_ascii=False))
        assert code3 == 401 and res3.get("error") == "invalid_hmac_signature", f"Scenario 3 failed: {res3}"

        # 场景 4：基于真实仓库 Diff 验证越界行号自动降级
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
    finally: #不管成功与失败都要吧服务器关掉
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
