"""快速上手示例：用内置 fixtures 跑一遍完整流水线。

运行方式（从 code/ 目录，无需网络）::

    python examples/quickstart.py

本脚本演示三件事：
1. 离线采集固定快照，并查看推导出的仓库结构信号；
2. 执行三级自动质检，打印每个闸门的得分与判定；
3. 演示单轮人工质检如何把 REVIEW 放行为 PASS。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ghsqa import manual
from ghsqa.models import SnapshotRequest
from ghsqa.pipeline import run_pipeline

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURES = os.path.join(ROOT, "fixtures")

VERDICT_MARK = {"PASS": "✅ PASS", "REVIEW": "🟡 REVIEW", "REJECT": "❌ REJECT"}


def line(char: str = "-", width: int = 68) -> None:
    print(char * width)


def demo_snapshot() -> None:
    print("\n【1】采集固定快照（离线 fixtures）")
    line()
    request = SnapshotRequest(repo="datawhalechina/hello-agents", milestone="3")
    result = run_pipeline(request, source="fixtures", fixtures_dir=FIXTURES)
    repo = result.snapshot.repo
    assert repo is not None

    print("仓库           : %s" % repo.full_name)
    print("固定 commit    : %s" % result.snapshot.pinned_ref)
    print("主语言         : %s" % repo.language)
    print("许可证         : %s" % repo.license_spdx)
    print("文件数         : %d（代码 %d / 测试 %d / 文档 %d）"
          % (repo.file_count, repo.code_file_count, repo.test_file_count, repo.doc_file_count))
    print("CI / 测试目录  : %s / %s" % (repo.has_ci, repo.has_tests_dir))
    print("目录最大深度   : %d" % repo.max_depth)
    print("采集到的里程碑 : %d 个" % len(result.snapshot.milestones))
    for ms in result.snapshot.milestones:
        print("   #%d %s —— %d 个任务，完成度 %.1f%%"
              % (ms.number, ms.title, ms.total_issues, ms.completion * 100))


def demo_gates() -> None:
    print("\n【2】三级自动质检")
    line()
    request = SnapshotRequest(repo="datawhalechina/hello-agents", milestone="3")
    result = run_pipeline(request, source="fixtures", fixtures_dir=FIXTURES)

    for gate in result.gates:
        print("%-14s %6.2f  %s" % (gate.title, gate.score, VERDICT_MARK[gate.verdict]))
        for check in gate.checks:
            flag = "🔒" if check["hard"] else "  "
            mark = "✓" if check["passed"] else "✗"
            print("   %s %s %-9s %-20s %.2f"
                  % (flag, mark, check["id"], check["title"], check["score"]))
    if result.skipped_gates:
        print("跳过的闸门：%s" % ", ".join(result.skipped_gates))

    print("\n综合得分       : %.2f / 100" % result.overall_score)
    print("自动质检结论   : %s" % VERDICT_MARK[result.auto_verdict])
    print("最终结论       : %s" % VERDICT_MARK[result.final_verdict])
    if result.review_queue:
        print("\n需人工重点复核 %d 项：" % len(result.review_queue))
        for item in result.review_queue:
            print("   %s %s —— %s" % (item["check_id"], item["title"], item["reason"]))


def demo_manual_review() -> None:
    print("\n【3】单轮人工质检：把 REVIEW 放行为 PASS")
    line()
    request = SnapshotRequest(repo="datawhalechina/hello-agents", milestone="3")

    before = run_pipeline(request, source="fixtures", fixtures_dir=FIXTURES)
    print("提交人工结论前 : %s（机器认为自动质检已 PASS，但仍需人工确认）"
          % VERDICT_MARK[before.final_verdict])

    review = manual.blank_review_template(reviewer="Demo Reviewer")
    review.overall = "approve"
    for decision in review.decisions:
        decision.verdict = "accept"
        decision.note = "已人工确认许可证为非标准模板但允许教学用途；结构与验收路径已复核。"
    after = run_pipeline(request, source="fixtures", fixtures_dir=FIXTURES, review=review)
    print("提交人工结论后 : %s" % VERDICT_MARK[after.final_verdict])

    # 反例：人工否决必须压过自动 PASS
    rejected = manual.blank_review_template(reviewer="Demo Reviewer")
    rejected.overall = "reject"
    for decision in rejected.decisions:
        decision.verdict = "reject"
        decision.note = "许可证存在附加限制，不可作为采集素材。"
    denied = run_pipeline(request, source="fixtures", fixtures_dir=FIXTURES, review=rejected)
    print("人工否决后     : %s（人工结论优先于自动结论）" % VERDICT_MARK[denied.final_verdict])


def demo_rejected_repo() -> None:
    print("\n【4】反面场景：已归档仓库被硬性阻断")
    line()
    request = SnapshotRequest(repo="legacycorp/abandoned-metrics-lib")
    result = run_pipeline(request, source="fixtures", fixtures_dir=FIXTURES)
    adm = result.gate("ADM")
    print("准入闸门判定   : %s" % VERDICT_MARK[adm.verdict if adm else "REJECT"])
    if adm and adm.hard_failures:
        print("硬性阻断项     : %s" % "、".join(adm.hard_failures))
    print("跳过后续闸门   : %s" % (", ".join(result.skipped_gates) or "无"))
    print("最终结论       : %s" % VERDICT_MARK[result.final_verdict])


def main() -> int:
    print("=" * 68)
    print("hello-repoqa 快速上手示例")
    print("fixtures: %s" % FIXTURES)
    print("=" * 68)
    demo_snapshot()
    demo_gates()
    demo_manual_review()
    demo_rejected_repo()
    print("\n" + "=" * 68)
    print("示例结束。生成可归档报告请运行：")
    print("  python -m ghsqa check --repo datawhalechina/hello-agents \\")
    print("      --source fixtures --fixtures ../fixtures --milestone 3 --out ../out/demo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
