"""三级闸门的具体检查项实现。

三个闸门的职责边界
------------------
* **准入（ADM）**：这个仓库"配不配进采集池"。只回答最粗的资格问题——
  是否公开可用、是否有许可、是否仍可运行、快照是否真的被钉住了。
  这一关不过，后面的质量分没有意义。
* **Repo 质量（REPO）**：工程本身是否值得作为任务来源。看结构、测试、
  文档、依赖治理与社区活跃度。
* **Milestone 质量（MS）**：这个里程碑能否被拆成可评估的 AI Coding 任务。
  看任务边界、可验收性、体量适中性与协作痕迹。

每条规则都是纯函数，输入 :class:`~ghsqa.models.Snapshot`，输出
:class:`~ghsqa.rubric.CheckOutcome`；不访问网络、不读环境变量。
"""

from __future__ import annotations

from typing import List

from .models import Snapshot
from .rubric import Check, CheckOutcome
from .util import days_since, ratio_str, truncate

__all__ = ["admission_checks", "repo_checks", "milestone_checks"]

# 与 `hello-agents` 同类的教程 / 文档型项目，代码占比天然偏低。
# 这类仓库不按"代码密度"一刀切，而是走结构化内容质量通道。
TUTORIAL_TOPIC_HINTS = ("tutorial", "course", "book", "education", "awesome", "guide", "docs")
TUTORIAL_DOC_RATIO = 0.5
TUTORIAL_MIN_DOCS = 5


def _is_tutorial_like(snapshot: Snapshot) -> bool:
    """判断仓库是否为教程/文档型项目。

    注意：不能只用「文档占比高」来判定，否则带大型 ``docs/`` 目录的常规库
    （如 requests、django）会被误判。真正的教程型项目特征是**文档文件数超过代码文件数**，
    即内容本身就是交付物，代码只是配套示例。
    """
    repo = snapshot.repo
    if repo is None:
        return False
    topics = {t.lower() for t in repo.topics}
    if topics & set(TUTORIAL_TOPIC_HINTS):
        return True
    if repo.doc_file_count >= TUTORIAL_MIN_DOCS and repo.doc_file_count > repo.code_file_count:
        return True
    return False


def _missing(snapshot: Snapshot) -> CheckOutcome:
    return CheckOutcome.fail("快照缺少 repo 事实（采集失败或 fixtures 不完整）")


# ==========================================================================
# 闸门一：准入（ADM）
# ==========================================================================

def admission_checks() -> List[Check]:
    def public_and_reachable(snapshot: Snapshot) -> CheckOutcome:
        repo = snapshot.repo
        if repo is None:
            return _missing(snapshot)
        if repo.is_disabled:
            return CheckOutcome.fail("仓库已被 GitHub 禁用", measured={"disabled": True})
        if not repo.default_branch:
            return CheckOutcome.fail("无法确定默认分支", measured={})
        return CheckOutcome.ok(
            "公开可访问，默认分支 %s，语言 %s" % (repo.default_branch, repo.language or "未标注"),
            measured={"default_branch": repo.default_branch, "language": repo.language},
        )

    def not_archived(snapshot: Snapshot) -> CheckOutcome:
        repo = snapshot.repo
        if repo is None:
            return _missing(snapshot)
        if repo.is_archived:
            return CheckOutcome.fail(
                "仓库处于归档状态，不会再产生真实开发里程碑",
                measured={"archived": True, "pushed_at": repo.pushed_at},
            )
        return CheckOutcome.ok("仓库未归档", measured={"archived": False})

    def snapshot_pinned(snapshot: Snapshot) -> CheckOutcome:
        """固定快照的核心要求：必须钉在不可变的 commit sha 上。"""
        pinned = snapshot.pinned_ref
        if not pinned:
            return CheckOutcome.fail("快照未被钉住：既没有 commit sha 也没有 ref")
        looks_like_sha = len(pinned) == 40 and all(ch in "0123456789abcdef" for ch in pinned.lower())
        if not looks_like_sha:
            return CheckOutcome.partial(
                0.4, "快照仅钉住可变 ref（%s），建议解析为 commit sha 后再采集" % pinned,
                measured={"pinned_ref": pinned, "immutable": False},
            )
        return CheckOutcome.ok("快照已固定在 commit %s" % pinned[:12],
                               measured={"pinned_ref": pinned, "immutable": True})

    def has_license(snapshot: Snapshot) -> CheckOutcome:
        """许可证判定。

        必须区分 GitHub 的三种取值，它们的法务含义完全不同：
        * ``NONE``        —— 仓库里根本没有许可证文件，默认保留全部权利，不可采集；
        * ``NOASSERTION`` —— 有许可证文件但不属于标准 SPDX 模板，需人工确认边界；
        * 标准 SPDX 标识   —— 可直接判定。
        """
        repo = snapshot.repo
        if repo is None:
            return _missing(snapshot)
        spdx = (repo.license_spdx or "").upper()
        if spdx == "NONE":
            return CheckOutcome.fail(
                "GitHub 判定仓库无任何许可证（NONE），默认保留全部权利，不可作为采集素材",
                measured={"license_spdx": "NONE", "license_file": repo.has_license_file},
            )
        if spdx and spdx != "NOASSERTION":
            return CheckOutcome.ok("许可证：%s" % spdx, measured={"license": spdx})
        if repo.has_license_file:
            return CheckOutcome.partial(
                0.6, "仓库含 LICENSE 文件但 GitHub 未能识别为标准 SPDX 标识（%s），需人工确认授权边界"
                     % (spdx or "未知"),
                measured={"license_spdx": repo.license_spdx, "license_file": True},
            )
        if spdx == "NOASSERTION":
            return CheckOutcome.partial(
                0.3, "许可证无法断言（NOASSERTION）且未在快照中发现 LICENSE 文件，需人工确认",
                measured={"license_spdx": spdx, "license_file": False},
            )
        return CheckOutcome.fail("未检测到任何许可证，含代码的仓库不宜作为任务来源",
                                 measured={"license_spdx": None, "license_file": False})

    def not_fork(snapshot: Snapshot) -> CheckOutcome:
        repo = snapshot.repo
        if repo is None:
            return _missing(snapshot)
        if repo.is_fork:
            return CheckOutcome.partial(
                0.5, "这是 fork 仓库，里程碑可能来自上游而非本仓库的独立开发",
                measured={"is_fork": True},
            )
        return CheckOutcome.ok("非 fork 仓库", measured={"is_fork": False})

    def has_issues_enabled(snapshot: Snapshot) -> CheckOutcome:
        repo = snapshot.repo
        if repo is None:
            return _missing(snapshot)
        if not repo.has_issues:
            return CheckOutcome.fail("Issue 功能被关闭，无法承载 Milestone 任务",
                                     measured={"has_issues": False})
        return CheckOutcome.ok("Issue 功能已开启", measured={"has_issues": True})

    def has_default_branch_content(snapshot: Snapshot) -> CheckOutcome:
        repo = snapshot.repo
        if repo is None:
            return _missing(snapshot)
        if repo.file_count == 0 and not repo.top_level_entries:
            return CheckOutcome.fail("快照内没有任何文件，仓库可能为空",
                                     measured={"file_count": 0})
        return CheckOutcome.ok("快照含 %d 个文件" % repo.file_count,
                               measured={"file_count": repo.file_count})

    return [
        Check(
            id="ADM-01", title="仓库公开且可访问", gate="ADM",
            fn=public_and_reachable, weight=1.0, hard=True, pass_at=0.5,
            rationale="无法访问或已被禁用的仓库不具备采集前提。",
            fix_hint="确认仓库 URL、可见性与默认分支；企业内网仓库需换用可匿名访问的等价源。",
        ),
        Check(
            id="ADM-02", title="仓库未归档", gate="ADM",
            fn=not_archived, weight=1.0, hard=True, pass_at=0.5,
            rationale="归档仓库停止演进，无法再产出真实的开发里程碑，任务会退化为练习题。",
            fix_hint="改选同领域中仍在活跃维护的仓库，或明确把任务降级为「复现型」并标注。",
        ),
        Check(
            id="ADM-03", title="快照固定到不可变 commit", gate="ADM",
            fn=snapshot_pinned, weight=1.5, hard=False, pass_at=0.5,
            rationale="只有钉在 commit sha 上，采集结果才可复现、可审计；分支名会随时间漂移。",
            fix_hint="传入 --ref <sha> 或先解析默认分支的真实 sha，再执行采集。",
        ),
        Check(
            id="ADM-04", title="许可证明确", gate="ADM",
            fn=has_license, weight=1.0, hard=False, pass_at=0.5,
            rationale="许可证决定采集素材能否被再分发与改造成任务，是法务前置条件。",
            fix_hint="优先选择 MIT / Apache-2.0 / BSD 等宽松许可；无法断言的许可证需人工确认。",
        ),
        Check(
            id="ADM-05", title="非 fork 仓库", gate="ADM",
            fn=not_fork, weight=0.5, hard=False, pass_at=0.5,
            rationale="fork 的 issue/milestone 常继承自上游，任务归属容易被误判。",
            fix_hint="回溯到上游原始仓库采集，或在快照中记录 fork 来源并注明。",
        ),
        Check(
            id="ADM-06", title="Issue 功能开启", gate="ADM",
            fn=has_issues_enabled, weight=0.5, hard=False, pass_at=0.5,
            rationale="Milestone 依附于 issue 体系，Issue 关闭则任务边界无从采集。",
            fix_hint="改选开启 Issue 的仓库，或改用 Projects/看板作为任务来源。",
        ),
        Check(
            id="ADM-07", title="快照内容非空", gate="ADM",
            fn=has_default_branch_content, weight=0.5, hard=False, pass_at=0.5,
            rationale="空仓库没有任何代码结构可评估，后续质量闸门必然失效。",
            fix_hint="确认采集的是正确的分支/ref，排除仅含 README 的占位仓库。",
        ),
    ]


# ==========================================================================
# 闸门二：Repo 质量（REPO）
# ==========================================================================

def repo_checks() -> List[Check]:
    def documented(snapshot: Snapshot) -> CheckOutcome:
        repo = snapshot.repo
        if repo is None:
            return _missing(snapshot)
        tutorial = _is_tutorial_like(snapshot)
        score = 0.0
        notes = []
        if repo.has_readme:
            score += 0.5
            notes.append("有 README")
        else:
            notes.append("缺 README")
        if repo.description:
            score += 0.2
            notes.append("有仓库简介")
        else:
            notes.append("缺仓库简介")
        threshold = 1 if tutorial else 3
        if repo.doc_file_count >= threshold:
            score += 0.3
            notes.append("文档文件 %d 个" % repo.doc_file_count)
        else:
            notes.append("文档文件仅 %d 个（阈值 %d）" % (repo.doc_file_count, threshold))
        return CheckOutcome.partial(score, "；".join(notes),
                                    measured={"doc_file_count": repo.doc_file_count,
                                              "tutorial_mode": tutorial})

    def structured(snapshot: Snapshot) -> CheckOutcome:
        """目录结构是否有意图：顶层条目既不该为 0，也不该是一坨平铺文件。"""
        repo = snapshot.repo
        if repo is None:
            return _missing(snapshot)
        tops = repo.top_level_entries
        if not tops:
            return CheckOutcome.fail("无法解析顶层结构", measured={"top_level": []})
        score = 0.0
        count = len(tops)
        if 3 <= count <= 40:
            score += 0.5
        elif count < 3:
            score += 0.2
        else:
            score += 0.3
        if repo.max_depth >= 2:
            score += 0.5
        elif repo.max_depth == 1:
            score += 0.25
        return CheckOutcome.partial(
            score, "顶层条目 %d 个，最大目录深度 %d" % (count, repo.max_depth),
            measured={"top_level_count": count, "max_depth": repo.max_depth,
                      "top_level": tops[:20]},
        )

    def test_coverage(snapshot: Snapshot) -> CheckOutcome:
        repo = snapshot.repo
        if repo is None:
            return _missing(snapshot)
        if _is_tutorial_like(snapshot):
            return CheckOutcome.ok(
                "教程型仓库以文档为主，测试不作为主要验收手段（文档 %d 个、代码 %d 个）"
                % (repo.doc_file_count, repo.code_file_count),
                measured={"tutorial_mode": True, "test_ratio": repo.test_ratio},
            )
        if not repo.code_file_count:
            return CheckOutcome.fail("未识别出任何代码文件", measured={"code_file_count": 0})
        ratio = repo.test_ratio
        if not repo.has_tests_dir and repo.test_file_count == 0:
            return CheckOutcome.fail("完全没有测试文件与测试目录，任务缺乏回归护栏",
                                     measured={"test_ratio": 0.0, "test_file_count": 0})
        score = min(1.0, ratio / 0.15)
        return CheckOutcome.partial(
            score,
            "测试/代码文件比 %s（%d/%d），测试目录%s"
            % (ratio_str(ratio), repo.test_file_count, repo.code_file_count,
               "存在" if repo.has_tests_dir else "不存在"),
            measured={"test_ratio": ratio, "test_file_count": repo.test_file_count,
                      "code_file_count": repo.code_file_count, "has_tests_dir": repo.has_tests_dir},
        )

    def automation(snapshot: Snapshot) -> CheckOutcome:
        repo = snapshot.repo
        if repo is None:
            return _missing(snapshot)
        score = 0.0
        notes = []
        if repo.has_ci:
            score += 0.6
            notes.append("配置了 CI")
        else:
            notes.append("未配置 CI")
        if repo.has_manifest:
            score += 0.25
            notes.append("有依赖/构建清单")
        else:
            notes.append("无依赖清单")
        if repo.has_docker:
            score += 0.15
            notes.append("有容器化配置")
        return CheckOutcome.partial(score, "；".join(notes),
                                    measured={"has_ci": repo.has_ci,
                                              "has_manifest": repo.has_manifest,
                                              "has_docker": repo.has_docker})

    def dependency_hygiene(snapshot: Snapshot) -> CheckOutcome:
        """依赖与版本治理：清单存在 + 语言构成可解析 + 未超大规模。"""
        repo = snapshot.repo
        if repo is None:
            return _missing(snapshot)
        score = 0.0
        notes = []
        if repo.has_manifest:
            score += 0.5
            notes.append("存在依赖清单")
        else:
            notes.append("缺少依赖清单")
        if repo.languages:
            score += 0.25
            top = max(repo.languages.items(), key=lambda kv: kv[1])[0]
            notes.append("主语言 %s" % top)
        else:
            notes.append("语言构成未知")
        if repo.size_kb:
            size_mb = repo.size_kb / 1024.0
            if size_mb <= 200:
                score += 0.25
                notes.append("体积 %.1f MB，适合完整快照" % size_mb)
            else:
                notes.append("体积 %.1f MB 偏大，快照与任务切分成本高" % size_mb)
        return CheckOutcome.partial(score, "；".join(notes),
                                    measured={"size_kb": repo.size_kb,
                                              "languages": repo.languages})

    def activity(snapshot: Snapshot) -> CheckOutcome:
        """活跃度：最近提交时间 + 近期提交量 + 贡献者规模。"""
        repo = snapshot.repo
        if repo is None:
            return _missing(snapshot)
        days = days_since(repo.pushed_at or repo.commit_date)
        if days is None:
            recency_score = 0.4
            recency_note = "无法判定最近提交时间"
        elif days <= 30:
            recency_score, recency_note = 1.0, "最近提交 %.0f 天前" % days
        elif days <= 90:
            recency_score, recency_note = 0.8, "最近提交 %.0f 天前" % days
        elif days <= 365:
            recency_score, recency_note = 0.5, "最近提交 %.0f 天前" % days
        else:
            recency_score, recency_note = 0.1, "最近提交 %.0f 天前（超过一年）" % days
        commits = repo.commit_count_recent
        commit_score = min(1.0, commits / 50.0) if commits else 0.2
        people = repo.contributor_count
        if people >= 10:
            people_score = 1.0
        elif people >= 3:
            people_score = 0.6
        elif people >= 1:
            people_score = 0.3
        else:
            people_score = 0.2
        score = 0.5 * recency_score + 0.3 * commit_score + 0.2 * people_score
        return CheckOutcome.partial(
            score,
            "%s；近 %d 次提交；贡献者 %d 人" % (recency_note, commits, people),
            measured={"days_since_push": days, "commit_count_recent": commits,
                      "contributor_count": people},
        )

    def task_granularity(snapshot: Snapshot) -> CheckOutcome:
        """任务可切分性：规模适中、层级不过深，便于界定单个任务的边界。"""
        repo = snapshot.repo
        if repo is None:
            return _missing(snapshot)
        score = 0.0
        notes = []
        if repo.file_count == 0:
            return CheckOutcome.fail("无可切分的文件", measured={"file_count": 0})
        if repo.file_count <= 2000:
            score += 0.5
            notes.append("文件数 %d，规模可控" % repo.file_count)
        else:
            notes.append("文件数 %d 偏多，任务边界易被稀释" % repo.file_count)
        if repo.max_depth <= 6:
            score += 0.3
            notes.append("目录深度 %d" % repo.max_depth)
        else:
            notes.append("目录深度 %d 过深" % repo.max_depth)
        has_entry = bool(repo.code_file_count or repo.doc_file_count)
        if has_entry:
            score += 0.2
            notes.append("含可定位的代码/文档入口")
        return CheckOutcome.partial(score, "；".join(notes),
                                    measured={"file_count": repo.file_count,
                                              "max_depth": repo.max_depth})

    return [
        Check(
            id="REPO-01", title="文档完备度", gate="REPO",
            fn=documented, weight=1.0, hard=False, pass_at=0.6,
            rationale="README 与文档是理解任务上下文的第一入口，缺文档会让任务成本不可估。",
            fix_hint="优先挑选 README 说明目标、结构、运行方式的仓库；教程型仓库看 docs 章节数。",
        ),
        Check(
            id="REPO-02", title="目录结构清晰", gate="REPO",
            fn=structured, weight=1.0, hard=False, pass_at=0.5,
            rationale="有意图的目录层级是「任务边界可界定」的结构前提，平铺仓库难以定位改动面。",
            fix_hint="选择顶层模块划分明确、有 src/tests/docs 等惯用布局的仓库。",
        ),
        Check(
            id="REPO-03", title="测试与回归护栏", gate="REPO",
            fn=test_coverage, weight=1.5, hard=False, pass_at=0.4,
            rationale="没有测试的仓库无法验证 AI 产出的正确性，任务验收会退化成主观判断。",
            fix_hint="优先选测试目录与测试文件齐全的仓库；教程型仓库可豁免，改以示例代码可运行性验收。",
        ),
        Check(
            id="REPO-04", title="自动化与构建配置", gate="REPO",
            fn=automation, weight=1.0, hard=False, pass_at=0.5,
            rationale="CI 与依赖清单决定了任务能否被自动化验证，是「可评测」的工程基础。",
            fix_hint="选择已有 CI workflow、requirements/pom/package 等清单的仓库。",
        ),
        Check(
            id="REPO-05", title="依赖与体积治理", gate="REPO",
            fn=dependency_hygiene, weight=0.8, hard=False, pass_at=0.5,
            rationale="体积过大或依赖缺失会让快照成本与复现难度失控。",
            fix_hint="控制仓库体积在数百 MB 内，确保依赖清单可被解析与安装。",
        ),
        Check(
            id="REPO-06", title="社区活跃度", gate="REPO",
            fn=activity, weight=1.2, hard=False, pass_at=0.5,
            rationale="活跃仓库的里程碑才反映真实开发节奏，也是任务多样性的来源。",
            fix_hint="优先选近 90 天有提交、贡献者不少于 3 人的仓库。",
        ),
        Check(
            id="REPO-07", title="任务可切分性", gate="REPO",
            fn=task_granularity, weight=1.0, hard=False, pass_at=0.5,
            rationale="规模与层级决定了能否把仓库拆成粒度合适、可独立验收的任务。",
            fix_hint="避免超大开仓与超深嵌套；选择模块边界清晰的仓库。",
        ),
    ]


# ==========================================================================
# 闸门三：Milestone 质量（MS）
# ==========================================================================

def milestone_checks() -> List[Check]:
    def milestone_selected(snapshot: Snapshot) -> CheckOutcome:
        if snapshot.request.milestone is None:
            return CheckOutcome.ok(
                "未指定目标 Milestone，本闸门按整体最优里程碑评估（信息性）",
                measured={"milestone_requested": None},
            )
        ms = snapshot.selected_milestone()
        if ms is None:
            return CheckOutcome.fail("指定的 Milestone %r 在该快照中不存在" % snapshot.request.milestone,
                                     measured={"milestone_requested": snapshot.request.milestone})
        return CheckOutcome.ok("已定位 Milestone #%s %s" % (ms.number, ms.title),
                               measured={"number": ms.number, "title": ms.title})

    def _target(snapshot: Snapshot):
        """取用于评估的里程碑：优先用户指定，否则取任务数最多的一个。"""
        ms = snapshot.selected_milestone()
        if ms is not None:
            return ms
        if not snapshot.milestones:
            return None
        return max(snapshot.milestones, key=lambda m: (m.total_issues, m.closed_issues))

    def has_milestone(snapshot: Snapshot) -> CheckOutcome:
        if not snapshot.milestones:
            return CheckOutcome.fail("该仓库没有任何 Milestone，只能退化为 issue 驱动",
                                     measured={"milestone_count": 0})
        return CheckOutcome.ok("采集到 %d 个 Milestone" % len(snapshot.milestones),
                               measured={"milestone_count": len(snapshot.milestones)})

    def definable_scope(snapshot: Snapshot) -> CheckOutcome:
        """任务边界：里程碑必须有描述，且任务数落在可评估区间。"""
        ms = _target(snapshot)
        if ms is None:
            return CheckOutcome.fail("没有可评估的 Milestone", measured={})
        total = ms.total_issues
        score = 0.0
        notes = []
        if ms.description and len(ms.description.strip()) >= 40:
            score += 0.4
            notes.append("有目标描述（%d 字）" % len(ms.description.strip()))
        else:
            notes.append("缺少足够的里程碑描述")
        if 3 <= total <= 30:
            score += 0.4
            notes.append("任务数 %d，粒度适中" % total)
        elif total == 0:
            notes.append("任务数为 0，无法构成开发里程碑")
        elif total < 3:
            score += 0.15
            notes.append("任务数仅 %d，边界过窄" % total)
        else:
            score += 0.15
            notes.append("任务数 %d 偏多，需拆分批次" % total)
        if ms.due_on:
            score += 0.1
            notes.append("设定了截止时间")
        if ms.total_issues and ms.total_issues == ms.open_issues:
            score += 0.1
            notes.append("尚在进行中")
        return CheckOutcome.partial(score, "；".join(notes),
                                    measured={"total_issues": total, "open": ms.open_issues,
                                              "closed": ms.closed_issues, "state": ms.state,
                                              "description_len": len(ms.description or "")})

    def task_detail(snapshot: Snapshot) -> CheckOutcome:
        """任务可验收性：issue 是否有正文、标签与负责人。"""
        ms = _target(snapshot)
        if ms is None:
            return CheckOutcome.fail("没有可评估的 Milestone", measured={})
        if not ms.issues:
            return CheckOutcome.partial(
                0.3, "未采集到里程碑下的 issue 明细（仅有聚合计数），无法评估验收信息",
                measured={"issues_collected": 0, "total": ms.total_issues},
            )
        detail = ms.detailed_ratio
        labeled = ms.labeled_ratio
        assigned = ms.assigned_ratio
        score = 0.5 * detail + 0.3 * labeled + 0.2 * assigned
        return CheckOutcome.partial(
            score,
            "正文充分率 %s；标签覆盖 %s；指派覆盖 %s（共 %d 个任务）"
            % (ratio_str(detail), ratio_str(labeled), ratio_str(assigned), len(ms.issues)),
            measured={"issue_count": len(ms.issues), "detail_ratio": detail,
                      "labeled_ratio": labeled, "assigned_ratio": assigned},
        )

    def verifiable(snapshot: Snapshot) -> CheckOutcome:
        """可验证性：仓库要有测试/CI 支撑，或里程碑处于可复现的完成态。"""
        repo = snapshot.repo
        ms = _target(snapshot)
        if repo is None or ms is None:
            return _missing(snapshot)
        score = 0.0
        notes = []
        if repo.has_ci:
            score += 0.4
            notes.append("仓库有 CI，可自动验证产出")
        else:
            notes.append("仓库无 CI，验证依赖人工")
        if repo.test_file_count or repo.has_tests_dir:
            score += 0.4
            notes.append("存在测试资产")
        elif _is_tutorial_like(snapshot):
            score += 0.4
            notes.append("教程型仓库：以示例代码可运行性代替单元测试验收")
        else:
            notes.append("缺少测试资产")
        completion = ms.completion
        if completion >= 0.8 or completion == 0.0:
            score += 0.2
            notes.append("完成度 %s" % ratio_str(completion))
        else:
            score += 0.1
            notes.append("完成度 %s（部分交付，验收口径需明确）" % ratio_str(completion))
        return CheckOutcome.partial(score, "；".join(notes),
                                    measured={"has_ci": repo.has_ci,
                                              "test_file_count": repo.test_file_count,
                                              "completion": completion})

    def healthy_backlog(snapshot: Snapshot) -> CheckOutcome:
        ms = _target(snapshot)
        if ms is None:
            return CheckOutcome.fail("没有可评估的 Milestone", measured={})
        total = ms.total_issues
        if total == 0:
            return CheckOutcome.fail("里程碑下没有任何任务，不构成开发里程碑",
                                     measured={"total_issues": 0})
        completion = ms.completion
        if completion >= 0.75:
            return CheckOutcome.partial(
                1.0, "完成度 %s，属于已交付的高质量里程碑" % ratio_str(completion),
                measured={"completion": completion})
        if completion >= 0.3:
            return CheckOutcome.partial(
                0.75, "完成度 %s，进行中的里程碑，任务信息鲜活" % ratio_str(completion),
                measured={"completion": completion})
        if completion > 0:
            return CheckOutcome.partial(
                0.5, "完成度 %s 偏低，可能停滞" % ratio_str(completion),
                measured={"completion": completion})
        return CheckOutcome.partial(
            0.6, "尚未开始（0% 完成），方向明确但无交付证据",
            measured={"completion": 0.0})

    def collaboration_signal(snapshot: Snapshot) -> CheckOutcome:
        """协作痕迹：非单一作者、且有讨论，说明任务经过了真实评审。"""
        ms = _target(snapshot)
        if ms is None or not ms.issues:
            return CheckOutcome.partial(0.4, "缺少 issue 明细，无法评估协作痕迹",
                                        measured={"issues_collected": 0})
        assignees = {a for i in ms.issues for a in i.assignees}
        comment_total = sum(i.comments for i in ms.issues)
        creators = {i.created_at for i in ms.issues if i.created_at}
        score = 0.0
        notes = []
        if len(assignees) >= 2:
            score += 0.4
            notes.append("参与人 %d 位" % len(assignees))
        elif len(assignees) == 1:
            score += 0.2
            notes.append("仅 1 位被指派人")
        else:
            notes.append("无指派信息")
        if comment_total >= 5:
            score += 0.3
            notes.append("讨论 %d 条" % comment_total)
        elif comment_total:
            score += 0.15
            notes.append("讨论 %d 条" % comment_total)
        else:
            notes.append("无讨论")
        if len(creators) >= 2:
            score += 0.3
            notes.append("跨 %d 个时间点创建" % len(creators))
        elif creators:
            score += 0.15
            notes.append("集中创建")
        return CheckOutcome.partial(score, "；".join(notes),
                                    measured={"assignee_count": len(assignees),
                                              "comments": comment_total})

    return [
        Check(
            id="MS-01", title="存在 Milestone", gate="MS",
            fn=has_milestone, weight=1.0, hard=True, pass_at=0.5,
            rationale="Milestone 是本流程的任务载体；完全没有里程碑的仓库无法产出结构化任务。",
            fix_hint="改用 issue 聚合或 Projects 看板作为替代任务源，并调整 rubric 的载体定义。",
        ),
        Check(
            id="MS-02", title="目标 Milestone 可定位", gate="MS",
            fn=milestone_selected, weight=1.0, hard=False, pass_at=0.5,
            rationale="指定的里程碑必须真实存在于该快照，否则整轮质检会评错对象。",
            fix_hint="核对 --milestone 的编号或标题是否与快照中的里程碑一致。",
        ),
        Check(
            id="MS-03", title="任务边界可界定", gate="MS",
            fn=definable_scope, weight=1.4, hard=True, pass_at=0.5,
            rationale="有描述、任务数适中的里程碑才能被切成边界清晰、可独立交付的工作单元。",
            fix_hint="目标是 3~30 个任务、有明确描述与截止时间的里程碑；过大则拆批次，过小则合并。",
        ),
        Check(
            id="MS-04", title="任务可验收信息", gate="MS",
            fn=task_detail, weight=1.3, hard=False, pass_at=0.4,
            rationale="issue 正文、标签与负责人共同构成验收标准的原材料，缺失会让评估失焦。",
            fix_hint="选择 issue 有详细正文、带分类标签并已指派负责人的里程碑。",
        ),
        Check(
            id="MS-05", title="结果可验证", gate="MS",
            fn=verifiable, weight=1.2, hard=False, pass_at=0.5,
            rationale="任务成果必须能被客观验证，测试与 CI 是成本最低的验证手段。",
            fix_hint="在仓库具备 CI/测试的前提下选题；教程型仓库以示例可运行替代。",
        ),
        Check(
            id="MS-06", title="任务体量健康", gate="MS",
            fn=healthy_backlog, weight=0.9, hard=False, pass_at=0.5,
            rationale="全空或长期停滞的里程碑缺少真实约束，难以反映工程判断力。",
            fix_hint="优先进行中或已高完成度的里程碑；避免 0 任务的空壳里程碑。",
        ),
        Check(
            id="MS-07", title="协作痕迹", gate="MS",
            fn=collaboration_signal, weight=0.8, hard=False, pass_at=0.5,
            rationale="多人参与与讨论记录说明任务经过真实评审，边界与验收更可信。",
            fix_hint="选择有多个被指派人与讨论记录的里程碑。",
        ),
    ]
