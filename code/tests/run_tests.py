"""hello-repoqa 回归测试。

运行方式（无需 pytest，也无需网络）::

    python -m tests.run_tests

覆盖三类断言
------------
1. **rubric 自检**：检查项 ID 唯一、权重为正、闸门齐备——防止规则表被改坏。
2. **判定路径**：正常仓库判 PASS、归档仓库被硬性阻断、无 Milestone 仓库被拦截。
3. **人工质检语义**：未提交人工结论不得直接 PASS；人工 reject 必须覆盖自动 PASS。
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ghsqa import manual  # noqa: E402
from ghsqa.collector import collect  # noqa: E402
from ghsqa.models import SnapshotRequest  # noqa: E402
from ghsqa.pipeline import run_pipeline  # noqa: E402
from ghsqa.report import render_junit, render_markdown  # noqa: E402
from ghsqa.rubric import DEFAULT_RUBRIC, run_gate  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(ROOT), "fixtures")
FAILURES = []
CHECKS_RUN = 0


def check(condition: bool, label: str) -> None:
    global CHECKS_RUN
    CHECKS_RUN += 1
    if condition:
        print("  ✓ %s" % label)
    else:
        print("  ✗ %s" % label)
        FAILURES.append(label)


def section(title: str) -> None:
    print("\n%s" % title)


# --------------------------------------------------------------------------

def test_rubric_integrity() -> None:
    section("[1] rubric 自检")
    ids = [c.id for c in DEFAULT_RUBRIC.checks]
    check(len(ids) == len(set(ids)), "检查项 ID 全局唯一（%d 项）" % len(ids))
    check(all(c.weight > 0 for c in DEFAULT_RUBRIC.checks), "所有检查项权重为正")
    check(all(0.0 <= c.pass_at <= 1.0 for c in DEFAULT_RUBRIC.checks), "pass_at 落在 [0,1]")
    check(DEFAULT_RUBRIC.gates() == ["ADM", "REPO", "MS"], "三个闸门按 ADM/REPO/MS 注册")
    check(DEFAULT_RUBRIC.review_score < DEFAULT_RUBRIC.pass_score, "REVIEW 阈值低于 PASS 阈值")
    hard = [c.id for c in DEFAULT_RUBRIC.checks if c.hard]
    check(set(hard) == {"ADM-01", "ADM-02", "MS-01", "MS-03"},
          "硬性阻断项符合设计（%s）" % ", ".join(hard))
    check(all(c.rationale for c in DEFAULT_RUBRIC.checks), "每个检查项都有判定依据说明")


def test_fixture_collection() -> None:
    section("[2] fixtures 采集与事实推导")
    request = SnapshotRequest(repo="datawhalechina/hello-agents", milestone="3")
    snap = collect(request, source="fixtures", fixtures_dir=FIXTURES)
    repo = snap.repo
    check(repo is not None and repo.full_name == "datawhalechina/hello-agents", "仓库事实解析成功")
    check(repo.file_count == 52, "文件数推导正确（%d）" % repo.file_count)
    check(repo.code_file_count == 11, "代码文件数推导正确（%d）" % repo.code_file_count)
    check(repo.test_file_count == 3, "测试文件数推导正确（%d）" % repo.test_file_count)
    check(repo.has_tests_dir, "识别出测试目录")
    check(repo.doc_file_count == 33, "文档文件数推导正确（%d）" % repo.doc_file_count)
    check(repo.has_ci, "识别出 CI 配置")
    check(repo.has_manifest, "识别出依赖清单")
    check(repo.max_depth == 4, "目录深度推导正确（%d）" % repo.max_depth)
    check(len(snap.milestones) == 3, "采集到 3 个 Milestone")
    milestone = snap.selected_milestone()
    check(milestone is not None and milestone.number == 3, "--milestone 3 定位成功")
    check(milestone.total_issues == 9, "Milestone #3 任务数为 9（%d）" % milestone.total_issues)
    check(abs(milestone.completion - 5 / 9.0) < 1e-6,
          "完成度计算正确（%.3f）" % milestone.completion)
    check(milestone.labeled_ratio == 1.0, "标签覆盖率 100%")


def test_positive_path() -> None:
    section("[3] 正常仓库：应通过自动三级质检")
    result = run_pipeline(
        SnapshotRequest(repo="datawhalechina/hello-agents", milestone="3"),
        source="fixtures", fixtures_dir=FIXTURES,
    )
    check(result.auto_verdict == "PASS", "自动结论为 PASS（实际 %s）" % result.auto_verdict)
    check(result.final_verdict == "REVIEW",
          "未提交人工结论时最终判定降级为 REVIEW（实际 %s）" % result.final_verdict)
    check(result.overall_score >= 90, "综合得分 >= 90（%.2f）" % result.overall_score)
    adm = result.gate("ADM")
    check(adm is not None and not adm.hard_failures, "准入闸门无硬性阻断")
    ms = result.gate("MS")
    check(ms is not None and ms.verdict == "PASS", "Milestone 闸门判定 PASS")
    check(len(result.review_queue) >= 1, "生成了人工复核队列（%d 项）" % len(result.review_queue))
    check(not result.skipped_gates, "没有闸门被跳过")


def test_archived_blocks_early() -> None:
    section("[4] 归档仓库：应被准入硬性阻断且跳过后续闸门")
    result = run_pipeline(
        SnapshotRequest(repo="legacycorp/abandoned-metrics-lib"),
        source="fixtures", fixtures_dir=FIXTURES,
    )
    check(result.auto_verdict == "REJECT", "自动结论为 REJECT（实际 %s）" % result.auto_verdict)
    adm = result.gate("ADM")
    check(adm is not None and adm.verdict == "REJECT", "准入闸门判定 REJECT")
    check(any("ADM-02" in f for f in (adm.hard_failures if adm else [])),
          "命中 ADM-02 归档硬性阻断")
    check(result.skipped_gates == ["REPO", "MS"],
          "后续闸门被跳过（%s）" % result.skipped_gates)
    check(result.gate("REPO") is None, "跳过的闸门不产生结果对象")
    lic = next((c for c in adm.checks if c["id"] == "ADM-04"), None) if adm else None
    check(lic is not None and lic["score"] == 0.0, "无许可证判定为 0 分")


def test_missing_milestone_blocks() -> None:
    section("[5] 无 Milestone 仓库：应被 Milestone 硬性阻断")
    result = run_pipeline(
        SnapshotRequest(repo="legacycorp/abandoned-metrics-lib"),
        source="fixtures", fixtures_dir=FIXTURES,
    )
    # 该 fixtures 有 milestone 定义但无 issue 明细，准入已先阻断；
    # 单独验证 MS 闸门的规则行为，绕过准入阻断。
    snap = collect(SnapshotRequest(repo="datawhalechina/hello-agents"),
                   source="fixtures", fixtures_dir=FIXTURES)
    snap.milestones = []
    gate = run_gate(DEFAULT_RUBRIC, snap, "MS", "Milestone 质量质检")
    check(gate.verdict == "REJECT", "无 Milestone 时 MS 闸门 REJECT")
    check(any("MS-01" in f for f in gate.hard_failures), "命中 MS-01 硬性阻断")
    check(result.auto_verdict == "REJECT", "归档仓库整轮仍为 REJECT")


def test_missing_target_milestone() -> None:
    section("[6] 指定不存在的 Milestone：应被识别")
    result = run_pipeline(
        SnapshotRequest(repo="acme/widget-toolkit", milestone="999"),
        source="fixtures", fixtures_dir=FIXTURES,
    )
    ms = result.gate("MS")
    check(ms is not None, "MS 闸门正常执行")
    target = next((c for c in ms.checks if c["id"] == "MS-02"), None) if ms else None
    check(target is not None and not target["passed"], "MS-02 未通过（指定的里程碑不存在）")


def test_normal_repo_scores_high() -> None:
    section("[7] 常规工程仓库：应判定 PASS")
    result = run_pipeline(
        SnapshotRequest(repo="acme/widget-toolkit", milestone="7"),
        source="fixtures", fixtures_dir=FIXTURES,
    )
    check(result.auto_verdict == "PASS", "自动结论为 PASS（实际 %s）" % result.auto_verdict)
    repo_gate = result.gate("REPO")
    check(repo_gate is not None and repo_gate.verdict == "PASS",
          "Repo 质量闸门 PASS（%.2f）" % (repo_gate.score if repo_gate else -1))
    test_check = next((c for c in repo_gate.checks if c["id"] == "REPO-03"), None) if repo_gate else None
    check(test_check is not None and test_check["score"] > 0.8,
          "测试护栏得分良好（%.2f）" % (test_check["score"] if test_check else -1))


def test_human_review_semantics() -> None:
    section("[8] 单轮人工质检语义")
    request = SnapshotRequest(repo="datawhalechina/hello-agents", milestone="3")

    # 8a. 人工通过 -> 恢复自动结论
    approved = manual.blank_review_template(reviewer="Reviewer A")
    approved.overall = "approve"
    for decision in approved.decisions:
        decision.verdict = "accept"
        decision.note = "已人工确认"
    result = run_pipeline(request, source="fixtures", fixtures_dir=FIXTURES, review=approved)
    check(result.final_verdict == "PASS",
          "人工 approve + 全部裁决 -> PASS（实际 %s）" % result.final_verdict)

    # 8b. 人工否决 -> 必须压过自动 PASS
    rejected = manual.blank_review_template(reviewer="Reviewer B")
    rejected.overall = "reject"
    for decision in rejected.decisions:
        decision.verdict = "reject"
        decision.note = "许可证存在附加限制"
    result_rej = run_pipeline(request, source="fixtures", fixtures_dir=FIXTURES, review=rejected)
    check(result_rej.final_verdict == "REJECT",
          "人工 reject 覆盖自动 PASS（实际 %s）" % result_rej.final_verdict)

    # 8c. 人工 approve 但仍有 pending -> 保持 REVIEW
    partial = manual.blank_review_template(reviewer="Reviewer C")
    partial.overall = "approve"
    partial.decisions[0].verdict = "accept"
    for decision in partial.decisions[1:]:
        decision.verdict = "pending"
    result_partial = run_pipeline(request, source="fixtures", fixtures_dir=FIXTURES, review=partial)
    check(result_partial.final_verdict == "REVIEW",
          "存在未裁决问题时保持 REVIEW（实际 %s）" % result_partial.final_verdict)

    # 8d. 人工未署名执行 -> REVIEW
    none_review = run_pipeline(request, source="fixtures", fixtures_dir=FIXTURES, review=None)
    check(none_review.final_verdict == "REVIEW", "无人工结论时不得 PASS")


def test_report_rendering() -> None:
    section("[9] 报告渲染")
    result = run_pipeline(
        SnapshotRequest(repo="datawhalechina/hello-agents", milestone="3"),
        source="fixtures", fixtures_dir=FIXTURES,
    )
    md = render_markdown(result)
    check("# GitHub 仓库采集质检报告" in md, "Markdown 含标题")
    check("## 四、三级自动质检" in md, "Markdown 含三级质检章节")
    check("## 五、单轮人工质检" in md, "Markdown 含人工质检章节")
    check("61819beeeaf757d8ae6faeb7963c3d0e320877ed" in md, "Markdown 记录了固定 commit")
    xml = render_junit(result)
    check(xml.startswith("<?xml"), "JUnit XML 头部正确")
    check("<testsuites" in xml and "</testsuites>" in xml, "JUnit XML 结构闭合")
    check(xml.count("<testcase") == sum(len(g.checks) for g in result.gates),
          "JUnit testcase 数与检查项数一致")


def test_cli_smoke() -> None:
    section("[10] CLI 冒烟测试")
    from ghsqa.__main__ import main
    code = main([
        "check", "--repo", "acme/widget-toolkit", "--source", "fixtures",
        "--fixtures", FIXTURES, "--milestone", "7",
        "--out", os.path.join(ROOT, "..", "out", "selftest"), "--quiet",
    ])
    check(code == 1, "CLI 返回 REVIEW 退出码 1（实际 %d）" % code)
    list_code = main(["list-checks"])
    check(list_code == 0, "list-checks 子命令正常退出")
    missing = main([
        "check", "--repo", "no-such/repo", "--source", "fixtures",
        "--fixtures", FIXTURES, "--out", os.path.join(ROOT, "..", "out", "selftest"),
    ])
    check(missing == 3, "采集失败返回退出码 3（实际 %d）" % missing)


def test_exit_codes() -> None:
    section("[11] 退出码约定（供 CI 判定）")
    from ghsqa.__main__ import main
    out = os.path.join(ROOT, "..", "out", "selftest")
    pass_code = main([
        "check", "--repo", "acme/widget-toolkit", "--source", "fixtures",
        "--fixtures", FIXTURES, "--milestone", "7",
        "--out", out, "--quiet",
    ])
    check(pass_code == 1, "未人工确认 -> 退出码 1（实际 %d）" % pass_code)
    import json
    review_path = os.path.join(out, "review-approved.json")
    os.makedirs(out, exist_ok=True)
    payload = manual.blank_review_template(reviewer="CI Bot").to_dict()
    payload["overall"] = "approve"
    for decision in payload["decisions"]:
        decision["verdict"] = "accept"
        decision["note"] = "自动回归用例"
    with open(review_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    approve_code = main([
        "check", "--repo", "acme/widget-toolkit", "--source", "fixtures",
        "--fixtures", FIXTURES, "--milestone", "7", "--review", review_path,
        "--out", out, "--quiet",
    ])
    check(approve_code == 0, "人工确认后 -> 退出码 0（实际 %d）" % approve_code)


# --------------------------------------------------------------------------

def main() -> int:
    print("hello-repoqa 回归测试")
    print("fixtures: %s" % FIXTURES)
    tests = [
        test_rubric_integrity,
        test_fixture_collection,
        test_positive_path,
        test_archived_blocks_early,
        test_missing_milestone_blocks,
        test_missing_target_milestone,
        test_normal_repo_scores_high,
        test_human_review_semantics,
        test_report_rendering,
        test_cli_smoke,
        test_exit_codes,
    ]
    for test in tests:
        test()
    print("\n" + "=" * 60)
    if FAILURES:
        print("失败 %d / %d 项断言：" % (len(FAILURES), CHECKS_RUN))
        for item in FAILURES:
            print("  - %s" % item)
        return 1
    print("全部通过：%d 项断言" % CHECKS_RUN)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
