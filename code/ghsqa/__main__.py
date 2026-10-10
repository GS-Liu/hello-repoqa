"""命令行入口：`python -m ghsqa <子命令>`。

子命令
------
* ``snapshot`` —— 只做采集，把固定快照落盘（可复现的中间产物）。
* ``check``    —— 采集 + 三级自动质检 + 单轮人工质检 + 报告。
* ``init-review`` —— 生成人工质检模板 JSON，交给评审人填写。
* ``list-checks`` —— 打印当前 rubric 的全部检查项（自文档化）。
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from . import __version__
from . import manual as manual_mod
from .collector import CollectError
from .pipeline import run_pipeline
from .report import render_junit, render_markdown, render_summary_line
from .rubric import DEFAULT_RUBRIC
from .util import ensure_dir, write_json
from .models import SnapshotRequest


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ghsqa",
        description="GitHub 仓库固定快照与 Milestone 采集 + 三级质检 + 单轮人工质检",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  # 离线演示（使用内置 fixtures，无需网络）
  python -m ghsqa check --repo datawhalechina/hello-agents --source fixtures \\
      --fixtures fixtures --milestone 3 --out out

  # 在线采集某个 commit
  python -m ghsqa snapshot --repo psf/requests --ref <sha> --out out

  # 生成人工质检模板 -> 填写 -> 带结论重新质检
  python -m ghsqa init-review --repo psf/requests --reviewer "Your Name" --out out/review.json
  python -m ghsqa check --repo psf/requests --review out/review.json --out out
""",
    )
    parser.add_argument("--version", action="version", version="hello-repoqa %s" % __version__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--repo", required=True, help="仓库标识，形如 owner/repo")
        sp.add_argument("--ref", default=None, help="要固定的分支/标签/commit sha（默认默认分支）")
        sp.add_argument("--milestone", default=None, help="目标 Milestone 编号或标题")
        sp.add_argument("--source", choices=["api", "fixtures"], default="api",
                        help="数据源：api=在线采集，fixtures=离线事实包")
        sp.add_argument("--fixtures", default=None, help="fixtures 目录（source=fixtures 时必需）")
        sp.add_argument("--out", default="out", help="输出目录（默认 out）")
        sp.add_argument("--max-pages", type=int, default=5, help="分页上限，防止大仓库拉取过久")
        sp.add_argument("--no-archive", action="store_true", help="跳过 tarball 归档下载")

    snap = sub.add_parser("snapshot", help="采集固定快照并落盘")
    add_common(snap)

    check = sub.add_parser("check", help="执行完整质检流水线并生成报告")
    add_common(check)
    check.add_argument("--review", default=None, help="人工质检结论 JSON（省略则最终判定为 REVIEW）")
    check.add_argument("--junit", action="store_true", help="额外输出 JUnit XML（供 CI 使用）")
    check.add_argument("--quiet", action="store_true", help="仅输出摘要行")

    init = sub.add_parser("init-review", help="生成人工质检模板")
    init.add_argument("--repo", required=True, help="仓库标识，形如 owner/repo")
    init.add_argument("--reviewer", default="", help="评审人署名")
    init.add_argument("--out", default="review.json", help="模板输出路径")

    sub.add_parser("list-checks", help="打印 rubric 中的全部检查项")
    return parser


def _make_request(args: argparse.Namespace) -> SnapshotRequest:
    return SnapshotRequest(repo=args.repo, ref=args.ref, milestone=args.milestone)


def _run(args: argparse.Namespace) -> int:
    if args.command == "list-checks":
        print("rubric: %s v%s (pass>=%.0f, review>=%.0f)"
              % (DEFAULT_RUBRIC.name, DEFAULT_RUBRIC.version,
                 DEFAULT_RUBRIC.pass_score, DEFAULT_RUBRIC.review_score))
        current = None
        for check in DEFAULT_RUBRIC.checks:
            if check.gate != current:
                current = check.gate
                print("\n[%s]" % current)
            print("  %-9s %-22s weight=%.1f%s pass_at=%.2f"
                  % (check.id, check.title, check.weight,
                     " HARD" if check.hard else "", check.pass_at))
            if check.rationale:
                print("            └─ %s" % check.rationale)
        return 0

    if args.command == "init-review":
        template = manual_mod.blank_review_template(reviewer=args.reviewer)
        payload = template.to_dict()
        payload["_instructions"] = [
            "把每条 decisions[].verdict 改为 accept / reject / followup，并在 note 写明理由。",
            "把 overall 改为 approve / reject / rework，然后运行：",
            "  python -m ghsqa check --repo <owner/repo> --review <本文件> --out out",
        ]
        payload["_question_bank"] = manual_mod.MANUAL_QUESTIONS
        path = write_json(args.out, payload)
        print("已生成人工质检模板：%s" % path)
        print("需裁决 %d 个问题（对应自动质检的能力边界）" % len(manual_mod.MANUAL_QUESTIONS))
        return 0

    request = _make_request(args)
    fixtures_dir = args.fixtures or ("fixtures" if args.source == "fixtures" else None)

    review = None
    if args.command == "check" and args.review:
        try:
            review = manual_mod.load_review(args.review)
        except Exception as exc:
            print("读取人工质检结论失败：%s" % exc, file=sys.stderr)
            return 2

    archive_dir = None if args.no_archive else os.path.join(args.out, "archive")

    result = run_pipeline(
        request,
        source=args.source,
        fixtures_dir=fixtures_dir,
        review=review,
        with_archive=not args.no_archive,
        archive_dir=archive_dir,
        max_pages=args.max_pages,
    )

    ensure_dir(args.out)
    slug = args.repo.replace("/", "__")

    snap_path = os.path.join(args.out, "%s.snapshot.json" % slug)
    write_json(snap_path, result.snapshot.to_dict())

    if args.command == "snapshot":
        print("快照已保存：%s" % snap_path)
        print(render_summary_line(result))
        return 0

    report_path = os.path.join(args.out, "%s.report.md" % slug)
    with open(report_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_markdown(result))

    json_path = os.path.join(args.out, "%s.report.json" % slug)
    write_json(json_path, result.to_dict())

    if args.junit:
        junit_path = os.path.join(args.out, "%s.junit.xml" % slug)
        with open(junit_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(render_junit(result))
        if not args.quiet:
            print("JUnit 报告：%s" % junit_path)

    if args.quiet:
        print(render_summary_line(result))
    else:
        print(render_markdown(result, with_details=False))
        print("报告已保存：")
        print("  - %s" % report_path)
        print("  - %s" % json_path)

    if result.final_verdict == "PASS":
        return 0
    if result.final_verdict == "REVIEW":
        return 1
    return 2


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except CollectError as exc:
        print("采集失败：%s" % exc, file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
