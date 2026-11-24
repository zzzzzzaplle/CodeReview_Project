"""Unified Diff Hunk 解析器与 GitHub PR Review 评论行号映射器。

基于 Unified Diff 输出计算合法的 RIGHT（新文件）侧行号集合，避免行内审查评论
因行号超出 Hunk 范围而触发 GitHub 422 校验错误。
超出范围的审查发现会自动降级到顶层 PR Review 汇总正文中。
"""

from __future__ import annotations

import re
from typing import Any

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_unified_diff(diff_text: str) -> dict[str, set[int]]:
    """解析 Unified Diff 文本，返回每个文件合法的 RIGHT（新文件）侧行号集合。"""
    valid_lines: dict[str, set[int]] = {}
    current_file: str | None = None
    in_hunk = False
    new_line_no = 0

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git "):
            current_file = None
            in_hunk = False
            continue

        if raw_line.startswith("+++ "):
            target = raw_line[4:].strip()
            if target == "/dev/null":
                current_file = None
            elif target.startswith("b/"):
                current_file = target[2:]
                valid_lines.setdefault(current_file, set())
            else:
                current_file = target
                valid_lines.setdefault(current_file, set())
            in_hunk = False
            continue

        if raw_line.startswith("--- "):
            continue

        hunk_match = _HUNK_RE.match(raw_line)
        if hunk_match and current_file is not None:
            new_line_no = int(hunk_match.group(3))
            in_hunk = True
            continue

        if not in_hunk or current_file is None:
            continue

        if raw_line.startswith("\\"):
            # "\ No newline at end of file" 标记不推进新文件行号。
            continue

        if raw_line.startswith("+"):
            valid_lines[current_file].add(new_line_no)
            new_line_no += 1
        elif raw_line.startswith(" "):
            valid_lines[current_file].add(new_line_no)
            new_line_no += 1
        elif raw_line.startswith("-"):
            # 删除行属于 LEFT（旧文件）侧，RIGHT（新文件）侧行号计数器保持不变。
            continue
        else:
            in_hunk = False

    return valid_lines


def _format_inline_body(finding: dict[str, Any]) -> str:
    severity = str(finding.get("severity") or "INFO").upper()
    axis = str(finding.get("axis") or "General")
    title = str(finding.get("title") or "Code Review Finding")
    body = str(finding.get("body") or "").strip()
    suggestion = str(finding.get("suggestion") or "").strip()

    parts = [f"**[{severity}][{axis}] {title}**"]
    if body:
        parts.append(body)
    if suggestion:
        parts.append(f"**修改建议**：{suggestion}")
    return "\n\n".join(parts)


def build_github_review_payload(
    commit_id: str,
    summary: str,
    findings: list[dict[str, Any]],
    valid_lines_by_file: dict[str, set[int]],
    test_summary: str = "",
) -> dict[str, Any]:
    """构建支持行号自动降级的 GitHub PR Review API 请求体。

    Args:
        commit_id: 本次审查目标的 Head Commit SHA。
        summary: PR Review 顶层汇总正文摘要。
        findings: 结构化审查发现列表，包含 axis、file、line、severity、title、body、suggestion 字段。
        valid_lines_by_file: ``parse_unified_diff`` 返回的各文件合法行号映射。
        test_summary: 可选的前置单元测试/代码检查执行摘要。

    Returns:
        符合 GitHub ``POST /repos/{owner}/{repo}/pulls/{pull_number}/reviews`` 接口规范的字典，
        并附加 ``_mapping_stats`` 元数据字段用于本地验证。
    """
    inline_comments: list[dict[str, Any]] = []
    downgraded_findings: list[dict[str, Any]] = []

    for item in findings:
        file_path = item.get("file")
        line_no = item.get("line")
        is_valid_int = isinstance(line_no, int) and not isinstance(line_no, bool)

        if (
            isinstance(file_path, str)
            and file_path in valid_lines_by_file
            and is_valid_int
            and line_no in valid_lines_by_file[file_path]
        ):
            inline_comments.append(
                {
                    "path": file_path,
                    "line": line_no,
                    "side": "RIGHT",
                    "body": _format_inline_body(item),
                }
            )
        else:
            if not isinstance(file_path, str) or file_path not in valid_lines_by_file:
                reason = "file_not_in_diff"
            elif not is_valid_int:
                reason = "missing_or_invalid_line_number"
            else:
                reason = "line_not_in_diff_hunk"
            downgraded_findings.append({"reason": reason, "finding": item})

    body_sections = [
        "## OpenClaw 自动代码审查报告",
        "",
        summary.strip() if summary else "审查已完成，请查看以下详情。",
    ]

    if test_summary.strip():
        body_sections.extend(
            [
                "",
                "### 前置单测执行结果",
                "",
                "```text",
                test_summary.strip(),
                "```",
            ]
        )

    if downgraded_findings:
        body_sections.extend(
            [
                "",
                "### 降级评论（行号不在本次 Diff Hunk 内，自动转为文件/汇总级评论）",
                "",
            ]
        )
        for idx, entry in enumerate(downgraded_findings, start=1):
            reason = entry["reason"]
            f = entry["finding"]
            severity = str(f.get("severity") or "INFO").upper()
            axis = str(f.get("axis") or "General")
            title = str(f.get("title") or "Code Review Finding")
            target_file = f.get("file") or "<unknown>"
            target_line = f.get("line")
            detail = str(f.get("body") or "").strip()
            suggestion = str(f.get("suggestion") or "").strip()
            loc = f"`{target_file}:{target_line}`" if target_line is not None else f"`{target_file}`"
            body_sections.append(
                f"{idx}. **[{severity}][{axis}] {title}**（位置：{loc}，降级原因：`{reason}`）"
            )
            if detail:
                body_sections.append(f"   - 说明：{detail}")
            if suggestion:
                body_sections.append(f"   - 建议：{suggestion}")

    has_blocking = any(
        str(f.get("severity") or "").upper() in {"HIGH", "CRITICAL", "ERROR"}
        for f in findings
    )
    event = "REQUEST_CHANGES" if has_blocking else "COMMENT"

    return {
        "commit_id": commit_id,
        "event": event,
        "body": "\n".join(body_sections),
        "comments": inline_comments,
        "_mapping_stats": {
            "total_findings": len(findings),
            "inline_count": len(inline_comments),
            "downgraded_count": len(downgraded_findings),
            "downgraded_details": [
                {
                    "file": e["finding"].get("file"),
                    "line": e["finding"].get("line"),
                    "reason": e["reason"],
                    "title": e["finding"].get("title"),
                }
                for e in downgraded_findings
            ],
        },
    }


def _self_test() -> None:
    sample_diff = """diff --git a/src/inventory.js b/src/inventory.js
new file mode 100644
index 0000000..faad694
--- /dev/null
+++ b/src/inventory.js
@@ -0,0 +1,11 @@
+export class Inventory {
+  constructor(stock) {
+    this.stock = stock;
+  }
+
+  reserve(q) {
+    if (q <= 0) return false;
+    this.stock -= q;
+    return true;
+  }
+}
"""
    valid = parse_unified_diff(sample_diff)
    assert valid == {"src/inventory.js": set(range(1, 12))}, f"unexpected lines: {valid}"

    findings = [
        {
            "axis": "Standards",
            "file": "src/inventory.js",
            "line": 6,
            "severity": "MEDIUM",
            "title": "参数命名不明确",
            "body": "使用单字符参数 q",
            "suggestion": "改为 quantity",
        },
        {
            "axis": "Spec",
            "file": "src/inventory.js",
            "line": 999,
            "severity": "HIGH",
            "title": "越界行号测试项",
            "body": "该行不在 Diff Hunk 中",
            "suggestion": "应自动降级到 body",
        },
    ]
    payload = build_github_review_payload("abc123", "Summary", findings, valid, "1 failed")
    assert len(payload["comments"]) == 1
    assert payload["comments"][0]["line"] == 6
    assert payload["_mapping_stats"]["downgraded_count"] == 1
    assert "越界行号测试项" in payload["body"]
    print("diff_mapper self-test passed.")


if __name__ == "__main__":
    _self_test()
