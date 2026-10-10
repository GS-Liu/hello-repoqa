"""质检流水线：串起采集 -> 三级自动质检 -> 单轮人工质检 -> 报告。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import manual
from .collector import collect
from .models import Snapshot, SnapshotRequest
from .rubric import DEFAULT_RUBRIC, GateResult, Rubric, run_gate
from .util import now_iso

__all__ = ["GateSpec", "PipelineResult", "DEFAULT_GATES", "run_pipeline"]

DEFAULT_GATES: List["GateSpec"] = []


@dataclass
class GateSpec:
    """闸门声明：ID + 展示名 + 在总分中的权重。"""

    key: str
    title: str
    weight: float
    blocking: bool = False  # 阻断型闸门：不过则不再进入下一闸门


@dataclass
class PipelineResult:
    """整轮质检的产出，可直接序列化为 JSON 报告。"""

    snapshot: Snapshot
    gates: List[GateResult] = field(default_factory=list)
    overall_score: float = 0.0
    auto_verdict: str = "REJECT"
    final_verdict: str = "REJECT"
    manual: Optional[Dict[str, Any]] = None
    review_queue: List[Dict[str, Any]] = field(default_factory=list)
    skipped_gates: List[str] = field(default_factory=list)
    generated_at: str = ""

    @property
    def passed(self) -> bool:
        return self.final_verdict == "PASS"

    def gate(self, key: str) -> Optional[GateResult]:
        for g in self.gates:
            if g.gate == key:
                return g
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": "hello-repoqa/report@1",
            "generated_at": self.generated_at,
            "rubric": {"name": DEFAULT_RUBRIC.name, "version": DEFAULT_RUBRIC.version},
            "request": self.snapshot.request.to_dict(),
            "snapshot": self.snapshot.to_dict(),
            "gates": [g.to_dict() for g in self.gates],
            "skipped_gates": list(self.skipped_gates),
            "overall_score": round(self.overall_score, 2),
            "auto_verdict": self.auto_verdict,
            "final_verdict": self.final_verdict,
            "manual": self.manual,
            "review_queue": list(self.review_queue),
        }


def run_pipeline(request: SnapshotRequest,
                 source: str = "fixtures",
                 fixtures_dir: Optional[str] = None,
                 client: Any = None,
                 rubric: Rubric = DEFAULT_RUBRIC,
                 review: Optional[manual.ManualReview] = None,
                 with_archive: bool = True,
                 archive_dir: Optional[str] = None,
                 max_pages: int = 5) -> PipelineResult:
    """执行完整流水线。

    闸门按顺序执行，阻断型闸门（准入）不通过时后续闸门会被标记为跳过，
    避免在明显不合格的仓库上浪费质检预算。
    """
    snapshot = collect(
        request, source=source, fixtures_dir=fixtures_dir, client=client,
        with_archive=with_archive, archive_dir=archive_dir, max_pages=max_pages,
    )

    gates: List[GateResult] = []
    skipped: List[str] = []
    blocked = False

    specs = [
        GateSpec("ADM", "准入质检", 0.2, blocking=True),
        GateSpec("REPO", "Repo 质量质检", 0.4, blocking=False),
        GateSpec("MS", "Milestone 质量质检", 0.4, blocking=False),
    ]

    for spec in specs:
        if blocked:
            skipped.append(spec.key)
            continue
        result = run_gate(rubric, snapshot, spec.key, spec.title)
        gates.append(result)
        if spec.blocking and result.verdict == "REJECT":
            blocked = True

    active_weight = sum(s.weight for s in specs if s.key not in skipped) or 1.0
    overall = sum(g.score * next((s.weight for s in specs if s.key == g.gate), 0.0)
                  for g in gates) / active_weight

    if any(g.verdict == "REJECT" for g in gates if g.gate == "ADM"):
        auto_verdict = "REJECT"
    elif skipped:
        auto_verdict = "REJECT"
    elif all(g.verdict == "PASS" for g in gates):
        auto_verdict = "PASS"
    elif any(g.verdict == "REJECT" for g in gates):
        auto_verdict = "REJECT"
    else:
        auto_verdict = "REVIEW"

    review_queue = manual.build_review_queue(gates)
    has_hard_failure = any(g.hard_failures for g in gates)

    manual_summary = None
    if review is not None:
        review = manual.sync_review_status(review)
        merged = manual.apply_review(auto_verdict, review, has_hard_failure)
        manual_summary = {
            "review": review.to_dict(),
            "merge": merged,
            "questions": manual.MANUAL_QUESTIONS,
        }
        final_verdict = merged["final_verdict"]
    else:
        merged = manual.apply_review(auto_verdict, None, has_hard_failure)
        final_verdict = merged["final_verdict"]
        manual_summary = {
            "review": None,
            "merge": merged,
            "questions": manual.MANUAL_QUESTIONS,
        }

    return PipelineResult(
        snapshot=snapshot,
        gates=gates,
        overall_score=overall,
        auto_verdict=auto_verdict,
        final_verdict=final_verdict,
        manual=manual_summary,
        review_queue=[item.to_dict() for item in review_queue],
        skipped_gates=skipped,
        generated_at=now_iso(),
    )
