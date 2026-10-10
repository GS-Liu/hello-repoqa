"""数据模型：把 GitHub 上的仓库与 Milestone 固化成不可变的事实层。

设计原则
--------
采集器（collector）只负责产出 *事实*（facts），质检规则（rubric）只负责 *读事实*。
两者之间通过本模块的数据类解耦，因此同一套规则既可以在线打 GitHub API，
也可以离线跑 fixtures，规则代码零修改。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

__all__ = [
    "RepoFacts",
    "FileFacts",
    "MilestoneFacts",
    "IssueFacts",
    "Snapshot",
    "SnapshotRequest",
]

# 这些扩展名被视为"测试相关文件"，用于测试覆盖率信号估算。
TEST_HINT_PARTS = ("test", "spec", "_test.", ".test.", "/tests/", "\\tests\\", "__tests__")
# 这些文件名被视为 CI / 自动化配置。
CI_HINT_PATHS = (
    ".github/workflows/",
    ".gitlab-ci.yml",
    ".travis.yml",
    "azure-pipelines.yml",
    "Jenkinsfile",
    ".circleci/config.yml",
    "appveyor.yml",
    ".drone.yml",
)
# 这些文件名被视为文档。
DOC_HINT_NAMES = (
    "readme.md",
    "readme.rst",
    "readme.txt",
    "readme",
    "contributing.md",
    "code_of_conduct.md",
    "changelog.md",
    "license",
    "license.txt",
    "license.md",
    "docs/",
)
# 这些文件名被视为依赖 / 构建清单。
MANIFEST_HINT_NAMES = (
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "package.json",
    "pom.xml",
    "build.gradle",
    "cargo.toml",
    "go.mod",
    "gemfile",
    "composer.json",
    "cmakelists.txt",
    "makefile",
    "dockerfile",
    "environment.yml",
    "poetry.lock",
)


@dataclass
class FileFacts:
    """仓库内单个文件的精简事实（不保存文件内容，只保存判断所需的元信息）。"""

    path: str
    size: int = 0
    sha: Optional[str] = None
    is_dir: bool = False

    @property
    def lower(self) -> str:
        return self.path.lower().replace("\\", "/")

    @property
    def depth(self) -> int:
        return len([seg for seg in self.path.replace("\\", "/").split("/") if seg])

    @property
    def ext(self) -> str:
        name = self.path.rsplit("/", 1)[-1]
        if "." not in name:
            return ""
        return "." + name.rsplit(".", 1)[-1].lower()

    @property
    def looks_like_test(self) -> bool:
        low = self.lower
        if "/test/" in low or "/tests/" in low or low.startswith("tests/") or low.startswith("test/"):
            return True
        if "__tests__" in low:
            return True
        name = low.rsplit("/", 1)[-1]
        return name.startswith("test_") or name.endswith("_test.py") or ".test." in name or ".spec." in name

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RepoFacts:
    """仓库级事实：来自 `/repos/{owner}/{repo}`，外加由文件清单推导的结构信号。"""

    full_name: str
    owner: str = ""
    name: str = ""
    description: Optional[str] = None
    homepage: Optional[str] = None
    license_spdx: Optional[str] = None
    license_name: Optional[str] = None
    default_branch: str = "main"
    language: Optional[str] = None
    topics: List[str] = field(default_factory=list)
    stars: int = 0
    forks: int = 0
    watchers: int = 0
    open_issues: int = 0
    size_kb: int = 0
    is_fork: bool = False
    is_archived: bool = False
    is_disabled: bool = False
    is_template: bool = False
    has_issues: bool = True
    has_wiki: bool = False
    has_pages: bool = False
    has_discussions: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    pushed_at: Optional[str] = None

    # ---- 由文件清单 / 提交历史推导的信号（采集阶段填充） ----
    commit_sha: Optional[str] = None
    commit_date: Optional[str] = None
    commit_count_recent: int = 0
    contributor_count: int = 0
    file_count: int = 0
    code_file_count: int = 0
    test_file_count: int = 0
    doc_file_count: int = 0
    max_depth: int = 0
    total_loc: int = 0
    has_ci: bool = False
    has_manifest: bool = False
    has_readme: bool = False
    has_license_file: bool = False
    has_tests_dir: bool = False
    has_docker: bool = False
    has_gitignore: bool = False
    top_level_entries: List[str] = field(default_factory=list)
    languages: Dict[str, int] = field(default_factory=dict)
    files: List[FileFacts] = field(default_factory=list)

    @property
    def test_ratio(self) -> float:
        if not self.code_file_count:
            return 0.0
        return float(self.test_file_count) / float(self.code_file_count)

    @property
    def doc_ratio(self) -> float:
        if not self.file_count:
            return 0.0
        return float(self.doc_file_count) / float(self.file_count)

    def to_dict(self, with_files: bool = False) -> Dict[str, Any]:
        data = asdict(self)
        if not with_files:
            data.pop("files", None)
        return data


@dataclass
class IssueFacts:
    """Milestone 下的单个 issue / PR 事实。"""

    number: int
    title: str = ""
    state: str = "open"
    labels: List[str] = field(default_factory=list)
    assignees: List[str] = field(default_factory=list)
    body_length: int = 0
    comments: int = 0
    is_pull_request: bool = False
    created_at: Optional[str] = None
    closed_at: Optional[str] = None
    milestone: Optional[str] = None

    @property
    def is_closed(self) -> bool:
        return self.state == "closed"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MilestoneFacts:
    """Milestone 级事实，含由 issue 列表推导的任务边界信号。"""

    number: int
    title: str = ""
    description: Optional[str] = None
    state: str = "open"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    due_on: Optional[str] = None
    closed_at: Optional[str] = None
    creator: Optional[str] = None
    open_issues: int = 0
    closed_issues: int = 0
    issues: List[IssueFacts] = field(default_factory=list)

    @property
    def total_issues(self) -> int:
        if self.issues:
            return len(self.issues)
        return self.open_issues + self.closed_issues

    @property
    def completion(self) -> float:
        total = self.total_issues
        if not total:
            return 0.0
        closed = self.closed_issues or sum(1 for i in self.issues if i.is_closed)
        return float(closed) / float(total)

    @property
    def labeled_ratio(self) -> float:
        if not self.issues:
            return 0.0
        labeled = sum(1 for i in self.issues if i.labels)
        return float(labeled) / float(len(self.issues))

    @property
    def assigned_ratio(self) -> float:
        if not self.issues:
            return 0.0
        assigned = sum(1 for i in self.issues if i.assignees)
        return float(assigned) / float(len(self.issues))

    @property
    def detailed_ratio(self) -> float:
        """issue 正文达到最小长度的比例，用于判断任务边界是否写清楚。"""
        if not self.issues:
            return 0.0
        detailed = sum(1 for i in self.issues if i.body_length >= 120)
        return float(detailed) / float(len(self.issues))

    def to_dict(self, with_issues: bool = False) -> Dict[str, Any]:
        data = asdict(self)
        if not with_issues:
            data.pop("issues", None)
        return data


@dataclass
class SnapshotRequest:
    """一次采集请求：把"哪个仓库、钉在哪个版本"完整描述下来。"""

    repo: str
    ref: Optional[str] = None
    milestone: Optional[str] = None

    @property
    def owner(self) -> str:
        return self.repo.split("/", 1)[0]

    @property
    def name(self) -> str:
        parts = self.repo.split("/", 1)
        return parts[1] if len(parts) > 1 else ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Snapshot:
    """固定快照：一个仓库在某个 commit 上的完整可复现采集结果。"""

    request: SnapshotRequest
    collected_at: str
    source: str = "api"  # api | fixtures
    repo: Optional[RepoFacts] = None
    milestones: List[MilestoneFacts] = field(default_factory=list)
    archive_sha256: Optional[str] = None
    archive_bytes: int = 0
    warnings: List[str] = field(default_factory=list)

    @property
    def pinned_ref(self) -> Optional[str]:
        if self.repo and self.repo.commit_sha:
            return self.repo.commit_sha
        return self.request.ref

    def selected_milestone(self) -> Optional[MilestoneFacts]:
        want = self.request.milestone
        if want is None:
            return None
        for ms in self.milestones:
            if str(ms.number) == str(want) or ms.title == want:
                return ms
        return None

    def to_dict(self, with_children: bool = False) -> Dict[str, Any]:
        return {
            "schema": "hello-repoqa/snapshot@1",
            "request": self.request.to_dict(),
            "collected_at": self.collected_at,
            "source": self.source,
            "pinned_ref": self.pinned_ref,
            "archive": {"sha256": self.archive_sha256, "bytes": self.archive_bytes},
            "warnings": list(self.warnings),
            "repo": self.repo.to_dict(with_files=with_children) if self.repo else None,
            "milestones": [m.to_dict(with_issues=with_children) for m in self.milestones],
        }
