"""采集层：把 GitHub 仓库钉在固定 commit 上，并采集 Milestone 事实。

两种数据源
----------
* ``api``      —— 走 GitHub REST API v3（需要网络；可选 GITHUB_TOKEN 提升配额）
* ``fixtures`` —— 读取本地 JSON 事实包（离线、可复现，用于 CI 回归与演示）

无论哪种数据源，产出的都是同一个 :class:`~ghsqa.models.Snapshot`，
因此下游质检规则完全不需要区分数据来源。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .models import (
    CI_HINT_PATHS,
    DOC_HINT_NAMES,
    MANIFEST_HINT_NAMES,
    FileFacts,
    IssueFacts,
    MilestoneFacts,
    RepoFacts,
    Snapshot,
    SnapshotRequest,
)
from .util import now_iso, parse_iso, read_json, sha256_file

CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".go", ".rs", ".c", ".h",
    ".cc", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".scala", ".sh", ".pl",
    ".lua", ".r", ".m", ".mm", ".vue", ".sql", ".ipynb",
}
DOC_EXTENSIONS = {".md", ".rst", ".txt", ".adoc"}


class CollectError(RuntimeError):
    """采集失败（网络、权限或数据源结构不符合预期）。"""


class GitHubClient:
    """极简 GitHub REST 客户端，只实现采集快照所需的最小接口集。"""

    API = "https://api.github.com"

    def __init__(self, token: Optional[str] = None, timeout: int = 30) -> None:
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "hello-repoqa/0.1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = "Bearer %s" % self.token
        return headers

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = path if path.startswith("http") else self.API + path
        if params:
            query = "&".join("%s=%s" % (k, v) for k, v in params.items() if v is not None)
            if query:
                url = url + ("&" if "?" in url else "?") + query
        request = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:  # pragma: no cover - 需要网络
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise CollectError("GitHub API %s -> HTTP %s: %s" % (url, exc.code, detail))
        except urllib.error.URLError as exc:  # pragma: no cover - 需要网络
            raise CollectError("无法连接 GitHub API（%s）：%s" % (url, exc.reason))
        if not payload.strip():
            return None
        return json.loads(payload)

    def get_paged(self, path: str, params: Optional[Dict[str, Any]] = None,
                  max_pages: int = 5, per_page: int = 100) -> List[Any]:
        params = dict(params or {})
        params["per_page"] = per_page
        collected: List[Any] = []
        for page in range(1, max_pages + 1):
            params["page"] = page
            chunk = self.get(path, params)
            if not chunk:
                break
            collected.extend(chunk)
            if len(chunk) < per_page:
                break
        return collected


# --------------------------------------------------------------------------
# 事实推导：从文件清单与提交史推导结构信号
# --------------------------------------------------------------------------

def _is_ci_path(low: str) -> bool:
    return any(low == hint or low.startswith(hint) or low.endswith(hint) for hint in CI_HINT_PATHS)


def _is_doc_path(low: str) -> bool:
    if low.startswith("docs/") or "/docs/" in low:
        return True
    name = low.rsplit("/", 1)[-1]
    if name in DOC_HINT_NAMES:
        return True
    ext = os.path.splitext(name)[1]
    return ext in DOC_EXTENSIONS


def _is_manifest_path(low: str) -> bool:
    name = low.rsplit("/", 1)[-1]
    return name in MANIFEST_HINT_NAMES


def derive_structure(repo: RepoFacts, files: Iterable[FileFacts]) -> RepoFacts:
    """根据文件清单填满 RepoFacts 上的结构类信号字段。"""
    file_list = [f for f in files if not f.is_dir]
    repo.files = file_list
    repo.file_count = len(file_list)
    repo.code_file_count = 0
    repo.test_file_count = 0
    repo.doc_file_count = 0
    repo.max_depth = 0
    repo.has_ci = False
    repo.has_manifest = False
    repo.has_readme = False
    repo.has_license_file = False
    repo.has_tests_dir = False
    repo.has_docker = False
    repo.has_gitignore = False

    tops = set()
    for item in file_list:
        low = item.lower
        if item.depth:
            tops.add(item.path.replace("\\", "/").split("/")[0])
        repo.max_depth = max(repo.max_depth, item.depth)
        if _is_ci_path(low):
            repo.has_ci = True
        if _is_manifest_path(low):
            repo.has_manifest = True
        if low.split("/")[-1].startswith("readme"):
            repo.has_readme = True
        if low.split("/")[-1].startswith("license") or low.split("/")[-1].startswith("licence"):
            repo.has_license_file = True
        if low.split("/")[-1].startswith("dockerfile") or low.startswith("docker/"):
            repo.has_docker = True
        if low.split("/")[-1] == ".gitignore":
            repo.has_gitignore = True
        if item.looks_like_test:
            repo.test_file_count += 1
        elif item.ext in CODE_EXTENSIONS:
            repo.code_file_count += 1
        if _is_doc_path(low):
            repo.doc_file_count += 1

    repo.has_tests_dir = any(
        seg in ("test", "tests", "__tests__", "spec")
        for f in file_list
        for seg in f.path.replace("\\", "/").split("/")[:-1]
    )
    repo.top_level_entries = sorted(tops)
    return repo


# --------------------------------------------------------------------------
# API 采集
# --------------------------------------------------------------------------

def collect_from_api(request: SnapshotRequest, client: Optional[GitHubClient] = None,
                     max_pages: int = 5, with_archive: bool = True,
                     archive_dir: Optional[str] = None) -> Snapshot:
    """在线采集：解析 ref -> commit -> 文件树 -> Milestone 事实。"""
    client = client or GitHubClient()
    owner, name = request.owner, request.name
    if not owner or not name:
        raise CollectError("仓库标识必须是 owner/repo 形式：%r" % request.repo)

    raw_repo = client.get("/repos/%s/%s" % (owner, name))
    if not raw_repo:
        raise CollectError("仓库不存在或无权访问：%s" % request.repo)

    default_branch = raw_repo.get("default_branch") or "main"
    ref = request.ref or default_branch
    commit = client.get("/repos/%s/%s/commits/%s" % (owner, name, ref))
    commit_sha = (commit or {}).get("sha")
    if not commit_sha:
        raise CollectError("无法把 ref %r 解析为 commit" % ref)
    commit_date = (((commit or {}).get("commit") or {}).get("committer") or {}).get("date")

    tree = client.get("/repos/%s/%s/git/trees/%s" % (owner, name, commit_sha), {"recursive": "1"})
    files = [
        FileFacts(path=node.get("path", ""), size=node.get("size") or 0,
                  sha=node.get("sha"), is_dir=node.get("type") == "tree")
        for node in (tree or {}).get("tree", [])
        if node.get("type") in ("blob", "tree")
    ]

    license_info = raw_repo.get("license") or {}
    repo = RepoFacts(
        full_name=raw_repo.get("full_name", request.repo),
        owner=owner,
        name=name,
        description=raw_repo.get("description"),
        homepage=raw_repo.get("homepage"),
        license_spdx=license_info.get("spdx_id"),
        license_name=license_info.get("name"),
        default_branch=default_branch,
        language=raw_repo.get("language"),
        topics=list(raw_repo.get("topics") or []),
        stars=raw_repo.get("stargazers_count") or 0,
        forks=raw_repo.get("forks_count") or 0,
        watchers=raw_repo.get("subscribers_count") or raw_repo.get("watchers_count") or 0,
        open_issues=raw_repo.get("open_issues_count") or 0,
        size_kb=raw_repo.get("size") or 0,
        is_fork=bool(raw_repo.get("fork")),
        is_archived=bool(raw_repo.get("archived")),
        is_disabled=bool(raw_repo.get("disabled")),
        is_template=bool(raw_repo.get("is_template")),
        has_issues=bool(raw_repo.get("has_issues")),
        has_wiki=bool(raw_repo.get("has_wiki")),
        has_pages=bool(raw_repo.get("has_pages")),
        has_discussions=bool(raw_repo.get("has_discussions")),
        created_at=raw_repo.get("created_at"),
        updated_at=raw_repo.get("updated_at"),
        pushed_at=raw_repo.get("pushed_at"),
        commit_sha=commit_sha,
        commit_date=commit_date,
    )
    derive_structure(repo, files)

    contributors = client.get_paged("/repos/%s/%s/contributors" % (owner, name),
                                    max_pages=1, per_page=100)
    repo.contributor_count = len(contributors)
    try:
        repo.languages = client.get("/repos/%s/%s/languages" % (owner, name)) or {}
    except CollectError:
        repo.languages = {}

    try:
        recent_commits = client.get("/repos/%s/%s/commits" % (owner, name),
                                    {"sha": commit_sha, "per_page": 100})
        repo.commit_count_recent = len(recent_commits or [])
    except CollectError:
        repo.commit_count_recent = 0

    milestones = _collect_milestones(client, owner, name, request, max_pages)

    snapshot = Snapshot(
        request=request,
        collected_at=now_iso(),
        source="api",
        repo=repo,
        milestones=milestones,
    )

    if with_archive:
        _attach_archive(snapshot, client, owner, name, commit_sha, archive_dir)
    return snapshot


def _collect_milestones(client: GitHubClient, owner: str, name: str,
                        request: SnapshotRequest, max_pages: int) -> List[MilestoneFacts]:
    raw = client.get_paged("/repos/%s/%s/milestones" % (owner, name),
                           {"state": "all", "sort": "due_on", "direction": "asc"},
                           max_pages=max_pages)
    milestones: List[MilestoneFacts] = []
    for item in raw:
        ms = MilestoneFacts(
            number=item.get("number", 0),
            title=item.get("title") or "",
            description=item.get("description"),
            state=item.get("state") or "open",
            created_at=item.get("created_at"),
            updated_at=item.get("updated_at"),
            due_on=item.get("due_on"),
            closed_at=item.get("closed_at"),
            creator=(item.get("creator") or {}).get("login"),
            open_issues=item.get("open_issues") or 0,
            closed_issues=item.get("closed_issues") or 0,
        )
        want = request.milestone
        if want is None or str(ms.number) == str(want) or ms.title == want:
            ms.issues = _collect_issues(client, owner, name, ms.number, max_pages)
        milestones.append(ms)
    return milestones


def _collect_issues(client: GitHubClient, owner: str, name: str, number: int,
                    max_pages: int) -> List[IssueFacts]:
    raw = client.get_paged("/repos/%s/%s/issues" % (owner, name),
                           {"milestone": number, "state": "all"}, max_pages=max_pages)
    issues: List[IssueFacts] = []
    for item in raw:
        body = item.get("body") or ""
        issues.append(IssueFacts(
            number=item.get("number", 0),
            title=item.get("title") or "",
            state=item.get("state") or "open",
            labels=[lab.get("name", "") for lab in (item.get("labels") or [])],
            assignees=[a.get("login", "") for a in (item.get("assignees") or [])],
            body_length=len(body),
            comments=item.get("comments") or 0,
            is_pull_request="pull_request" in item,
            created_at=item.get("created_at"),
            closed_at=item.get("closed_at"),
            milestone=((item.get("milestone") or {}) or {}).get("title"),
        ))
    return issues


def _attach_archive(snapshot: Snapshot, client: GitHubClient, owner: str, name: str,
                    commit_sha: str, archive_dir: Optional[str]) -> None:
    """下载 tarball 并记录 sha256，让"固定快照"具备可验证的字节级凭据。"""
    if not archive_dir:
        return
    os.makedirs(archive_dir, exist_ok=True)
    target = os.path.join(archive_dir, "%s-%s.tar.gz" % (name, commit_sha[:12]))
    url = "%s/repos/%s/%s/tarball/%s" % (client.API, owner, name, commit_sha)
    request = urllib.request.Request(url, headers=client._headers())
    try:  # pragma: no cover - 需要网络
        with urllib.request.urlopen(request, timeout=client.timeout) as response:
            with open(target, "wb") as fh:
                while True:
                    chunk = response.read(1 << 16)
                    if not chunk:
                        break
                    fh.write(chunk)
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:  # pragma: no cover
        snapshot.warnings.append("归档下载失败，快照仍以 commit sha 固定：%s" % exc)
        return
    snapshot.archive_bytes = os.path.getsize(target)
    snapshot.archive_sha256 = sha256_file(target)


# --------------------------------------------------------------------------
# fixtures 采集（离线 / 可复现）
# --------------------------------------------------------------------------

def collect_from_fixtures(request: SnapshotRequest, fixtures_dir: str) -> Snapshot:
    """从本地 fixtures 目录读取事实包，产出与 API 采集同构的 Snapshot。"""
    slug = request.repo.replace("/", "__")
    candidate = os.path.join(fixtures_dir, slug)
    if os.path.isdir(candidate):
        payload = _load_fixture_bundle(candidate)
    else:
        path = os.path.join(fixtures_dir, slug + ".json")
        if not os.path.isfile(path):
            raise CollectError("找不到 fixtures：%s（尝试过目录与 %s.json）" % (candidate, slug))
        payload = read_json(path)

    raw_repo = payload.get("repo") or {}
    warnings: List[str] = list(payload.get("warnings") or [])

    files = [
        FileFacts(path=str(node.get("path", "")), size=int(node.get("size") or 0),
                  sha=node.get("sha"), is_dir=bool(node.get("is_dir")))
        for node in (raw_repo.get("files") or [])
    ]

    repo = RepoFacts(
        full_name=raw_repo.get("full_name") or request.repo,
        owner=request.owner,
        name=request.name,
        description=raw_repo.get("description"),
        homepage=raw_repo.get("homepage"),
        license_spdx=raw_repo.get("license_spdx"),
        license_name=raw_repo.get("license_name"),
        default_branch=raw_repo.get("default_branch") or "main",
        language=raw_repo.get("language"),
        topics=list(raw_repo.get("topics") or []),
        stars=int(raw_repo.get("stars") or 0),
        forks=int(raw_repo.get("forks") or 0),
        watchers=int(raw_repo.get("watchers") or 0),
        open_issues=int(raw_repo.get("open_issues") or 0),
        size_kb=int(raw_repo.get("size_kb") or 0),
        is_fork=bool(raw_repo.get("is_fork")),
        is_archived=bool(raw_repo.get("is_archived")),
        is_disabled=bool(raw_repo.get("is_disabled")),
        is_template=bool(raw_repo.get("is_template")),
        has_issues=bool(raw_repo.get("has_issues", True)),
        has_wiki=bool(raw_repo.get("has_wiki")),
        has_pages=bool(raw_repo.get("has_pages")),
        has_discussions=bool(raw_repo.get("has_discussions")),
        created_at=raw_repo.get("created_at"),
        updated_at=raw_repo.get("updated_at"),
        pushed_at=raw_repo.get("pushed_at"),
        commit_sha=raw_repo.get("commit_sha"),
        commit_date=raw_repo.get("commit_date"),
        commit_count_recent=int(raw_repo.get("commit_count_recent") or 0),
        contributor_count=int(raw_repo.get("contributor_count") or 0),
        total_loc=int(raw_repo.get("total_loc") or 0),
        languages=dict(raw_repo.get("languages") or {}),
    )
    if files:
        derive_structure(repo, files)
    else:
        # 文件清单缺失时退化为直接使用 fixtures 提供的聚合信号
        repo.file_count = int(raw_repo.get("file_count") or 0)
        repo.code_file_count = int(raw_repo.get("code_file_count") or 0)
        repo.test_file_count = int(raw_repo.get("test_file_count") or 0)
        repo.doc_file_count = int(raw_repo.get("doc_file_count") or 0)
        repo.max_depth = int(raw_repo.get("max_depth") or 0)
        repo.has_ci = bool(raw_repo.get("has_ci"))
        repo.has_manifest = bool(raw_repo.get("has_manifest"))
        repo.has_readme = bool(raw_repo.get("has_readme"))
        repo.has_license_file = bool(raw_repo.get("has_license_file"))
        repo.has_tests_dir = bool(raw_repo.get("has_tests_dir"))
        repo.has_docker = bool(raw_repo.get("has_docker"))
        repo.has_gitignore = bool(raw_repo.get("has_gitignore"))
        repo.top_level_entries = list(raw_repo.get("top_level_entries") or [])
        warnings.append("fixtures 未提供 files 清单，已退化为使用预聚合结构信号")

    milestones: List[MilestoneFacts] = []
    for item in payload.get("milestones") or []:
        issues = [
            IssueFacts(
                number=int(i.get("number") or 0),
                title=i.get("title") or "",
                state=i.get("state") or "open",
                labels=list(i.get("labels") or []),
                assignees=list(i.get("assignees") or []),
                body_length=int(i.get("body_length") or 0),
                comments=int(i.get("comments") or 0),
                is_pull_request=bool(i.get("is_pull_request")),
                created_at=i.get("created_at"),
                closed_at=i.get("closed_at"),
                milestone=i.get("milestone"),
            )
            for i in (item.get("issues") or [])
        ]
        milestones.append(MilestoneFacts(
            number=int(item.get("number") or 0),
            title=item.get("title") or "",
            description=item.get("description"),
            state=item.get("state") or "open",
            created_at=item.get("created_at"),
            updated_at=item.get("updated_at"),
            due_on=item.get("due_on"),
            closed_at=item.get("closed_at"),
            creator=item.get("creator"),
            open_issues=int(item.get("open_issues") or 0),
            closed_issues=int(item.get("closed_issues") or 0),
            issues=issues,
        ))

    return Snapshot(
        request=request,
        collected_at=now_iso(),
        source="fixtures",
        repo=repo,
        milestones=milestones,
        archive_sha256=payload.get("archive_sha256"),
        archive_bytes=int(payload.get("archive_bytes") or 0),
        warnings=warnings,
    )


def _load_fixture_bundle(directory: str) -> Dict[str, Any]:
    """fixtures 目录形式：repo.json + milestones.json 分开存放，便于人工审阅。"""
    repo_path = os.path.join(directory, "repo.json")
    ms_path = os.path.join(directory, "milestones.json")
    if not os.path.isfile(repo_path):
        raise CollectError("fixtures 目录缺少 repo.json：%s" % directory)
    bundle: Dict[str, Any] = {"repo": read_json(repo_path)}
    if os.path.isfile(ms_path):
        bundle["milestones"] = read_json(ms_path)
    for extra in ("archive.json", "warnings.json"):
        path = os.path.join(directory, extra)
        if os.path.isfile(path):
            bundle.update(read_json(path))
    return bundle


def collect(request: SnapshotRequest, source: str = "api", fixtures_dir: Optional[str] = None,
            client: Optional[GitHubClient] = None, with_archive: bool = True,
            archive_dir: Optional[str] = None, max_pages: int = 5) -> Snapshot:
    """统一入口：按 source 分派到 API 或 fixtures 采集。"""
    if source == "fixtures":
        if not fixtures_dir:
            raise CollectError("source=fixtures 时必须提供 fixtures_dir")
        return collect_from_fixtures(request, fixtures_dir)
    if source != "api":
        raise CollectError("未知数据源：%r（可选 api / fixtures）" % source)
    return collect_from_api(request, client=client, max_pages=max_pages,
                            with_archive=with_archive, archive_dir=archive_dir)
