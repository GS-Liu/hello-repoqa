"""质检引擎：声明式 rubric + 加权打分 + 硬性阻断。

为什么把 rubric 做成数据而不是 `if/else`
---------------------------------------
1. **可审计**：每个检查项都有稳定 ID（如 ``R-TEST-01``），报告里能逐条追溯。
2. **可调参**：权重与阈值集中在 :data:`DEFAULT_RUBRIC`，调口径不需要改逻辑。
3. **可回归**：检查项是纯函数 ``Snapshot -> CheckOutcome``，fixtures 一挂就能跑。

判定语义
--------
* 每个检查项输出 0~1 的得分：``1.0`` 完全达标，``0.0`` 完全不达标，中间为部分达标。
* ``score`` 按权重加权求和后乘以 100，得到该闸门的百分制得分。
* ``hard=True`` 的检查项若未达标（得分 < 其 ``pass_at``），**无论总分多高都直接 REJECT**，
  因为这类问题是"一票否决"性质（例如仓库已归档、Milestone 无任何任务）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from .models import Snapshot
from .util import clamp

__all__ = ["CheckOutcome", "Check", "GateResult", "Rubric", "run_gate", "DEFAULT_RUBRIC"]


@dataclass
class CheckOutcome:
    """单个检查项的结果。``score`` 必须落在 [0, 1]。"""

    score: float
    detail: str = ""
    measured: Any = None

    @classmethod
    def ok(cls, detail: str = "", measured: Any = None) -> "CheckOutcome":
        return cls(1.0, detail, measured)

    @classmethod
    def fail(cls, detail: str = "", measured: Any = None) -> "CheckOutcome":
        return cls(0.0, detail, measured)

    @classmethod
    def partial(cls, score: float, detail: str = "", measured: Any = None) -> "CheckOutcome":
        return cls(clamp(score, 0.0, 1.0), detail, measured)


@dataclass
class Check:
    """一个可独立计分的质检检查项。"""

    id: str
    title: str
    gate: str
    fn: Callable[[Snapshot], CheckOutcome]
    weight: float = 1.0
    hard: bool = False
    pass_at: float = 0.5
    rationale: str = ""
    fix_hint: str = ""

    def run(self, snapshot: Snapshot) -> CheckOutcome:
        try:
            outcome = self.fn(snapshot)
        except Exception as exc:  # 规则本身出错不应炸掉整轮质检
            return CheckOutcome.fail("检查项执行异常：%s: %s" % (type(exc).__name__, exc))
        if not isinstance(outcome, CheckOutcome):
            return CheckOutcome.fail("检查项返回值类型非法：%r" % type(outcome).__name__)
        return outcome


@dataclass
class GateResult:
    """一个闸门的完整结论。"""

    gate: str
    title: str
    score: float
    verdict: str  # PASS | REVIEW | REJECT
    checks: List[Dict[str, Any]] = field(default_factory=list)
    hard_failures: List[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gate": self.gate,
            "title": self.title,
            "score": round(self.score, 2),
            "verdict": self.verdict,
            "hard_failures": list(self.hard_failures),
            "summary": self.summary,
            "checks": self.checks,
        }


@dataclass
class Rubric:
    """一套 rubric：把闸门 -> 检查项 -> 阈值组织起来。"""

    name: str
    version: str
    pass_score: float = 75.0
    review_score: float = 60.0
    checks: List[Check] = field(default_factory=list)

    def by_gate(self, gate: str) -> List[Check]:
        return [c for c in self.checks if c.gate == gate]

    def gates(self) -> List[str]:
        seen: List[str] = []
        for check in self.checks:
            if check.gate not in seen:
                seen.append(check.gate)
        return seen


def run_gate(rubric: Rubric, snapshot: Snapshot, gate: str, title: str = "") -> GateResult:
    """对单个闸门执行全部检查项，产出加权得分与判定。"""
    checks = rubric.by_gate(gate)
    if not checks:
        return GateResult(gate=gate, title=title or gate, score=0.0, verdict="REJECT",
                          summary="该闸门没有任何检查项（rubric 配置错误）")

    total_weight = sum(c.weight for c in checks) or 1.0
    weighted = 0.0
    rows: List[Dict[str, Any]] = []
    hard_failures: List[str] = []

    for check in checks:
        outcome = check.run(snapshot)
        weighted += outcome.score * check.weight
        passed = outcome.score >= check.pass_at
        if check.hard and not passed:
            hard_failures.append("%s（%s）" % (check.id, check.title))
        rows.append({
            "id": check.id,
            "title": check.title,
            "weight": check.weight,
            "hard": check.hard,
            "pass_at": check.pass_at,
            "score": round(outcome.score, 4),
            "passed": passed,
            "detail": outcome.detail,
            "measured": outcome.measured,
            "rationale": check.rationale,
            "fix_hint": check.fix_hint,
        })

    score = clamp(weighted / total_weight * 100.0)
    if hard_failures:
        verdict = "REJECT"
    elif score >= rubric.pass_score:
        verdict = "PASS"
    elif score >= rubric.review_score:
        verdict = "REVIEW"
    else:
        verdict = "REJECT"

    hard_note = ("触发 %d 项硬性阻断" % len(hard_failures)) if hard_failures else "无硬性阻断"
    summary = "得分 %.2f/100，%s，判定 %s" % (score, hard_note, verdict)

    return GateResult(gate=gate, title=title or gate, score=score, verdict=verdict,
                      checks=rows, hard_failures=hard_failures, summary=summary)


# --------------------------------------------------------------------------
# 默认 rubric
# --------------------------------------------------------------------------

from .rules import admission_checks, milestone_checks, repo_checks  # noqa: E402


def _build_default_rubric() -> Rubric:
    return Rubric(
        name="hello-repoqa/default",
        version="1.0.0",
        pass_score=75.0,
        review_score=60.0,
        checks=admission_checks() + repo_checks() + milestone_checks(),
    )


DEFAULT_RUBRIC = _build_default_rubric()
