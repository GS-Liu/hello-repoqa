"""报告渲染：把 PipelineResult 输出为 Markdown 与 JUnit 风格 XML。

Markdown 面向人工评审与 PR 留档；XML 面向 CI（GitHub Actions 可直接展示）。
"""

from __future__ import annotations

from typing import Any, Dict, List
from xml.sax.saxutils import escape, quoteattr

from .pipeline import PipelineResult
from .util import human_bytes, truncate

__all__ = ["render_markdown", "render_junit", "render_summary_line"]

VERDICT_ICON = {"PASS": "✅ PASS", "REVIEW": "🟡 REVIEW", "REJECT": "❌ REJECT"}


def _icon(verdict: str) -> str:
    return VERDICT_ICON.get(verdict, verdict)


def render_summary_line(result: PipelineResult) -> str:
    return "[%s] %s | 自动 %s | 综合 %.2f/100" % (
        result.final_verdict, result.snapshot.request.repo,
        result.auto_verdict, result.overall_score,
    )


def render_markdown(result: PipelineResult, with_details: bool = True) -> str:
    snap = result.snapshot
    repo = snap.repo
    lines: List[str] = []

    lines.append("# GitHub 仓库采集质检报告")
    lines.append("")
    lines.append("> 由 `hello-repoqa` 生成 · rubric `%s` · 采集时间 `%s`"
                 % (result.to_dict()["rubric"]["version"], snap.collected_at))
    lines.append("")

    # ---- 结论卡片 ----
    lines.append("## 一、总览")
    lines.append("")
    lines.append("| 项目 | 结果 |")
    lines.append("| --- | --- |")
    lines.append("| 仓库 | `%s` |" % snap.request.repo)
    lines.append("| 固定版本 (commit) | `%s` |" % (snap.pinned_ref or "**未固定**"))
    lines.append("| 数据源 | %s |" % snap.source)
    lines.append("| 目标 Milestone | %s |" % (snap.request.milestone or "未指定"))
    lines.append("| 自动质检结论 | %s |" % _icon(result.auto_verdict))
    lines.append("| **最终结论** | **%s** |" % _icon(result.final_verdict))
    lines.append("| 综合得分 | %.2f / 100 |" % result.overall_score)
    if snap.archive_sha256:
        lines.append("| 归档 SHA256 | `%s` |" % snap.archive_sha256)
        lines.append("| 归档体积 | %s |" % human_bytes(snap.archive_bytes))
    lines.append("")

    if snap.warnings:
        lines.append("> ⚠️ 采集告警：")
        for warn in snap.warnings:
            lines.append("> - %s" % warn)
        lines.append("")

    # ---- 仓库事实 ----
    if repo is not None:
        lines.append("## 二、仓库事实（快照）")
        lines.append("")
        lines.append("| 指标 | 值 |")
        lines.append("| --- | --- |")
        lines.append("| 简介 | %s |" % (truncate(repo.description, 80) or "—"))
        lines.append("| 主语言 | %s |" % (repo.language or "—"))
        lines.append("| 许可证 | %s |" % (repo.license_spdx or repo.license_name or "未识别"))
        lines.append("| Stars / Forks | %d / %d |" % (repo.stars, repo.forks))
        lines.append("| 贡献者 | %d 人 |" % repo.contributor_count)
        lines.append("| 文件数 | %d（代码 %d / 测试 %d / 文档 %d） |"
                     % (repo.file_count, repo.code_file_count,
                        repo.test_file_count, repo.doc_file_count))
        lines.append("| 目录最大深度 | %d |" % repo.max_depth)
        lines.append("| 最近提交 | %s |" % (repo.pushed_at or repo.commit_date or "—"))
        lines.append("| CI / 依赖清单 / 测试目录 | %s / %s / %s |"
                     % (_yn(repo.has_ci), _yn(repo.has_manifest), _yn(repo.has_tests_dir)))
        if repo.topics:
            lines.append("| Topics | %s |" % ", ".join("`%s`" % t for t in repo.topics[:8]))
        lines.append("")

    # ---- Milestone 事实 ----
    if snap.milestones:
        lines.append("## 三、Milestone 事实")
        lines.append("")
        lines.append("| # | 标题 | 状态 | 任务数 | 完成度 | 截止 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for ms in snap.milestones:
            lines.append("| %d | %s | %s | %d | %.1f%% | %s |"
                         % (ms.number, truncate(ms.title, 40), ms.state, ms.total_issues,
                            ms.completion * 100, (ms.due_on or "—")[:10]))
        lines.append("")

    # ---- 三级闸门得分 ----
    lines.append("## 四、三级自动质检")
    lines.append("")
    lines.append("| 闸门 | 得分 | 判定 | 硬性阻断 | 说明 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for gate in result.gates:
        lines.append("| %s | %.2f | %s | %s | %s |"
                     % (gate.title, gate.score, _icon(gate.verdict),
                        "、".join(gate.hard_failures) or "—", gate.summary))
    for key in result.skipped_gates:
        lines.append("| %s | — | ⏭ 跳过 | — | 上一阻断型闸门未通过 |" % key)
    lines.append("")

    if with_details:
        for gate in result.gates:
            lines.append("### %s（%s）" % (gate.title, gate.gate))
            lines.append("")
            lines.append("| 检查项 | 权重 | 得分 | 判定 | 实测 |")
            lines.append("| --- | --- | --- | --- | --- |")
            for check in gate.checks:
                mark = "✅" if check["passed"] else "❌"
                if check["hard"]:
                    mark += " 🔒"
                lines.append("| `%s` %s | %.1f | %.2f | %s | %s |"
                             % (check["id"], check["title"], check["weight"],
                                check["score"], mark, truncate(check.get("detail"), 70)))
            lines.append("")

    # ---- 人工质检 ----
    lines.append("## 五、单轮人工质检")
    lines.append("")
    manual_block = result.manual or {}
    merge = manual_block.get("merge") or {}
    review = manual_block.get("review")
    lines.append("- 人工状态：**%s**" % merge.get("manual_state", "未知"))
    if merge.get("blocking"):
        for item in merge["blocking"]:
            lines.append("- 🚫 阻断原因：%s" % item)
    if review:
        lines.append("- 评审人：%s" % (review.get("reviewer") or "未署名"))
        lines.append("- 评审时间：%s" % (review.get("reviewed_at") or "—"))
        lines.append("")
        lines.append("| 问题 | 裁决 | 备注 |")
        lines.append("| --- | --- | --- |")
        for decision in review.get("decisions") or []:
            lines.append("| `%s` | %s | %s |"
                         % (decision["question_id"], decision["verdict"],
                            truncate(decision.get("note"), 50) or "—"))
    else:
        lines.append("- 尚未提交人工质检结论 → 最终判定最多为 REVIEW，不得放行。")
        lines.append("")
        lines.append("需人工裁决的问题清单：")
        lines.append("")
        lines.append("| 问题 | 说明 | 为何机器判不准 |")
        lines.append("| --- | --- | --- |")
        for question in manual_block.get("questions") or []:
            lines.append("| `%s` | %s | %s |"
                         % (question["id"], question["question"], question["why"]))
    lines.append("")

    # ---- 人工复核队列 ----
    if result.review_queue:
        lines.append("## 六、需人工重点复核项")
        lines.append("")
        lines.append("| 检查项 | 得分 | 触发原因 | 建议动作 |")
        lines.append("| --- | --- | --- | --- |")
        for item in result.review_queue:
            lines.append("| `%s` %s | %.2f | %s | %s |"
                         % (item["check_id"], item["title"], item["score"],
                            item["reason"], truncate(item.get("fix_hint"), 50) or "—"))
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*本报告由自动质检流水线生成，人工结论部分需评审人签字确认后方可归档。*")
    lines.append("")
    return "\n".join(lines)


def _yn(value: bool) -> str:
    return "有" if value else "无"


def render_junit(result: PipelineResult) -> str:
    """输出 JUnit XML，便于 GitHub Actions / Jenkins 直接渲染测试结果。"""
    failures = sum(1 for g in result.gates for c in g.checks if not c["passed"])
    tests = sum(len(g.checks) for g in result.gates) + len(result.skipped_gates)
    lines: List[str] = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append('<testsuites name="hello-repoqa" tests="%d" failures="%d">' % (tests, failures))
    for gate in result.gates:
        lines.append('  <testsuite name=%s tests="%d" failures="%d">'
                     % (quoteattr("%s (%s)" % (gate.title, gate.gate)),
                        len(gate.checks),
                        sum(1 for c in gate.checks if not c["passed"])))
        for check in gate.checks:
            name = escape("%s %s" % (check["id"], check["title"]))
            if check["passed"]:
                lines.append('    <testcase name=%s classname=%s/>'
                             % (quoteattr(name), quoteattr(gate.gate)))
            else:
                lines.append('    <testcase name=%s classname=%s>' % (quoteattr(name), quoteattr(gate.gate)))
                lines.append('      <failure message=%s>%s</failure>'
                             % (quoteattr("score=%.2f" % check["score"]),
                                escape(check.get("detail") or "")))
                lines.append('    </testcase>')
        lines.append('  </testsuite>')
    for key in result.skipped_gates:
        lines.append('  <testsuite name=%s tests="1" failures="0">' % quoteattr(key))
        lines.append('    <testcase name=%s classname=%s><skipped/></testcase>'
                     % (quoteattr("gate skipped"), quoteattr(key)))
        lines.append('  </testsuite>')
    lines.append('</testsuites>')
    lines.append('')
    return "\n".join(lines)
