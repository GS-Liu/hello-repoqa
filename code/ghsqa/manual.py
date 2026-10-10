"""单轮人工质检：把自动结论交给人工做一次终审，并固化决策留痕。

为什么是"单轮"
--------------
自动质检负责**可量化**的部分，但有三类问题机器判不准：
1. 许可证/授权边界是否真的允许采集（法务判断）；
2. 任务描述里的隐含前提是否成立（领域判断）；
3. 自动分处于阈值边缘时该不该放行（价值判断）。

因此流程设计为 **自动三级质检 → 人工单轮终审**，人工只做一次，
但必须对每条存疑项给出明确裁决与理由，形成可追溯的审计记录。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .util import now_iso, read_json, write_json

__all__ = [
    "HumanDecision",
    "ManualReview",
    "ReviewQueueItem",
    "build_review_queue",
    "load_review",
    "apply_review",
    "blank_review_template",
]

# 人工必须逐条裁决的默认问题清单（对应自动质检的能力边界）
MANUAL_QUESTIONS: List[Dict[str, str]] = [
    {
        "id": "H-01",
        "question": "许可证是否真的允许我们把该仓库的代码/文档用作采集素材并再分发？",
        "why": "自动质检只能识别 SPDX 标识，无法判断附加条款、第三方素材与商标限制。",
    },
    {
        "id": "H-02",
        "question": "仓库的目录结构是否真实反映了任务边界？是否存在大量生成代码/二进制/归档文件干扰判断？",
        "why": "机器只能统计层级与文件数，无法区分手写代码与自动生成产物。",
    },
    {
        "id": "H-03",
        "question": "目标 Milestone 的任务描述是否包含足够的隐含前提（环境、数据、外部依赖）？",
        "why": "正文长度只是代理指标，真正的可执行性需要领域知识判断。",
    },
    {
        "id": "H-04",
        "question": "该里程碑产出的任务，其验收标准能否由第三方独立复现？",
        "why": "测试与 CI 存在不等于验收路径清晰，需要人工确认复现步骤。",
    },
    {
        "id": "H-05",
        "question": "快照是否遗漏了影响任务的关键要素（子模块、LFS、私有依赖、数据文件）？",
        "why": "API 采集的文件树无法反映 submodule 内容与 Git LFS 指针的真实大小。",
    },
]


@dataclass
class ReviewQueueItem:
    """需要人工重点关注的一条存疑项（来自自动质检的边界结论）。"""

    check_id: str
    title: str
    score: float
    detail: str
    reason: str
    fix_hint: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HumanDecision:
    """人工对某条问题的单轮裁决。"""

    question_id: str
    verdict: str = "pending"  # pending | accept | reject | followup
    note: str = ""
    reviewer: str = ""
    decided_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ManualReview:
    """一次人工终审的完整记录。"""

    reviewer: str = ""
    reviewed_at: Optional[str] = None
    overall: str = "pending"  # pending | approve | reject | rework
    decisions: List[HumanDecision] = field(default_factory=list)
    notes: str = ""

    def decision_map(self) -> Dict[str, HumanDecision]:
        return {d.question_id: d for d in self.decisions}

    def unresolved(self) -> List[HumanDecision]:
        return [d for d in self.decisions if d.verdict in ("pending", "followup")]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reviewer": self.reviewer,
            "reviewed_at": self.reviewed_at,
            "overall": self.overall,
            "notes": self.notes,
            "decisions": [d.to_dict() for d in self.decisions],
        }


def build_review_queue(gate_results: List[Any]) -> List[ReviewQueueItem]:
    """从自动质检结果里挑出「需要人工看一眼」的条目。

    触发条件（任一）：
    * 硬性检查项得分低于 1.0（哪怕通过了也要看）；
    * 普通检查项得分落在 [0.4, 0.75) 的模糊区间；
    * 检查项未通过门槛。
    """
    queue: List[ReviewQueueItem] = []
    for gate in gate_results:
        for check in gate.checks:
            score = float(check["score"])
            passed = bool(check["passed"])
            is_hard = bool(check["hard"])
            borderline = 0.4 <= score < 0.75
            if (is_hard and score < 1.0) or borderline or not passed:
                if is_hard and score < 1.0:
                    reason = "硬性检查项未满分，需确认是否可放行"
                elif not passed:
                    reason = "未达通过门槛（pass_at=%.2f）" % check["pass_at"]
                else:
                    reason = "得分处于模糊区间，机器难以定论"
                queue.append(ReviewQueueItem(
                    check_id=check["id"],
                    title=check["title"],
                    score=score,
                    detail=check.get("detail", ""),
                    reason=reason,
                    fix_hint=check.get("fix_hint", ""),
                ))
    return queue


def blank_review_template(reviewer: str = "") -> ManualReview:
    """生成待填写的人工质检模板（所有问题默认 pending）。"""
    return ManualReview(
        reviewer=reviewer,
        reviewed_at=None,
        overall="pending",
        decisions=[HumanDecision(question_id=q["id"], verdict="pending", note="")
                   for q in MANUAL_QUESTIONS],
        notes="",
    )


def load_review(path: str) -> ManualReview:
    """读取人工质检结果 JSON。"""
    payload = read_json(path)
    decisions = [
        HumanDecision(
            question_id=d.get("question_id", ""),
            verdict=d.get("verdict", "pending"),
            note=d.get("note", ""),
            reviewer=d.get("reviewer", ""),
            decided_at=d.get("decided_at"),
        )
        for d in payload.get("decisions") or []
    ]
    return ManualReview(
        reviewer=payload.get("reviewer", ""),
        reviewed_at=payload.get("reviewed_at"),
        overall=payload.get("overall", "pending"),
        decisions=decisions,
        notes=payload.get("notes", ""),
    )


def apply_review(final_verdict: str, review: Optional[ManualReview],
                 has_hard_failure: bool) -> Dict[str, Any]:
    """把人工结论合并到自动结论上，产出最终放行判定。

    规则：
    * 人工未提供 -> 只能给到 REVIEW（需人工确认），不得直接放行；
    * 人工 reject / rework -> 最终 REJECT；
    * 人工 approve 但仍有未裁决问题 -> 保持 REVIEW；
    * 人工 approve 且全部裁决完毕 -> 依据自动分给 PASS/REVIEW，
      但硬性阻断未被人工显式豁免时仍为 REJECT。
    """
    if review is None or review.overall == "pending":
        return {
            "final_verdict": "REVIEW" if final_verdict != "REJECT" else "REJECT",
            "manual_state": "未执行人工质检",
            "blocking": ["缺少单轮人工质检结论"] if final_verdict != "REJECT" else [],
            "unresolved": [q["id"] for q in MANUAL_QUESTIONS] if final_verdict != "REJECT" else [],
        }

    unresolved = [d.question_id for d in review.unresolved()]
    blocking: List[str] = []

    if review.overall == "reject":
        return {"final_verdict": "REJECT", "manual_state": "人工判定拒绝",
                "blocking": ["人工终审结论：reject"], "unresolved": unresolved}
    if review.overall == "rework":
        return {"final_verdict": "REJECT", "manual_state": "人工判定需返工",
                "blocking": ["人工终审结论：rework"], "unresolved": unresolved}

    if unresolved:
        blocking.append("人工质检仍有 %d 条未裁决" % len(unresolved))
        return {"final_verdict": "REVIEW", "manual_state": "人工质检未完成",
                "blocking": blocking, "unresolved": unresolved}

    if review.overall == "approve":
        if has_hard_failure:
            blocking.append("存在硬性阻断且未被显式豁免")
            return {"final_verdict": "REJECT", "manual_state": "人工通过但硬性阻断未豁免",
                    "blocking": blocking, "unresolved": []}
        return {"final_verdict": final_verdict if final_verdict != "REJECT" else "REVIEW",
                "manual_state": "人工终审通过", "blocking": [], "unresolved": []}

    return {"final_verdict": "REVIEW", "manual_state": "人工状态未知：%s" % review.overall,
            "blocking": ["人工结论状态不可识别"], "unresolved": unresolved}


def sync_review_status(review: ManualReview) -> ManualReview:
    """把 pending 的裁决统一打上时间戳，方便审计。"""
    stamp = now_iso()
    for decision in review.decisions:
        if decision.verdict != "pending" and not decision.decided_at:
            decision.decided_at = stamp
            if not decision.reviewer:
                decision.reviewer = review.reviewer
    if review.overall != "pending" and not review.reviewed_at:
        review.reviewed_at = stamp
    return review
