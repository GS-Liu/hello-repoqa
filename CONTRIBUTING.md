# 贡献指南

感谢你对 `hello-repoqa` 的兴趣！本项目欢迎任何形式的贡献。

## 目录

- [开发环境](#开发环境)
- [提交前必做](#提交前必做)
- [贡献质检规则](#贡献质检规则)
- [贡献 fixtures 场景](#贡献-fixtures-场景)
- [改进文档](#改进文档)
- [提交 Pull Request](#提交-pull-request)

---

## 开发环境

项目**只依赖 Python 标准库**（Python 3.8+），无需创建虚拟环境也无需 `pip install`：

```bash
git clone https://github.com/<your-name>/hello-repoqa.git
cd hello-repoqa/code
python --version   # 需要 >= 3.8
```

快速验证环境是否可用（离线）：

```bash
python -m tests.run_tests
```

---

## 提交前必做

无论改动大小，提交 PR 前请确保：

```bash
cd code
python -m tests.run_tests          # 必须全部通过（58 项断言）
python -m ghsqa list-checks        # 确认 rubric 能正常加载
```

如果你的改动影响了判定结果，请**同时更新对应的 fixtures 与断言**，不要只改代码。

---

## 贡献质检规则

新增一条检查项时，请遵循以下约定。

### 1. 选择正确的闸门

| 闸门 | 回答的问题 | 放进这里的典型规则 |
| --- | --- | --- |
| `ADM` | 这个仓库**配不配**进采集池 | 可访问性、归档状态、许可证、快照锚点 |
| `REPO` | 工程本身**值不值得**作为任务来源 | 结构、测试、文档、依赖、活跃度 |
| `MS` | 这个里程碑**能否**拆成可评估的任务 | 任务边界、可验收性、体量、协作痕迹 |

### 2. 编写检查函数

检查函数必须是**纯函数**：输入 `Snapshot`，输出 `CheckOutcome`，**不得访问网络或读环境变量**。

```python
def has_security_policy(snapshot: Snapshot) -> CheckOutcome:
    repo = snapshot.repo
    if repo is None:
        return _missing(snapshot)
    if any(f.lower.endswith("security.md") for f in repo.files):
        return CheckOutcome.ok("存在 SECURITY.md", measured={"found": True})
    return CheckOutcome.fail("缺少安全策略，漏洞上报路径不明确", measured={"found": False})
```

### 3. 决定是否设为硬性阻断

请**极其克制**地使用 `hard=True`。只有满足以下全部条件的规则才配得上一票否决：

- 不通过意味着后续所有质量分**失去意义**；
- 不存在"可接受的例外"，或例外必须由人工显式豁免；
- 判定依据是**客观事实**，不依赖领域判断。

当前仅 4 项硬性阻断：`ADM-01`、`ADM-02`、`MS-01`、`MS-03`。

### 4. 注册检查项

把新规则加入 `rules.py` 中对应闸门的返回列表，并**填写全部字段**：

```python
Check(
    id="REPO-08",                       # 闸门前缀 + 两位序号，必须全局唯一
    title="安全策略",                    # 报告中的展示名
    gate="REPO",                        # ADM / REPO / MS
    fn=has_security_policy,
    weight=0.6,                         # 权重为正
    hard=False,
    pass_at=0.5,                        # 得分达到该值才算通过
    rationale="安全策略决定漏洞上报路径是否明确，影响任务的可维护性评估。",
    fix_hint="优先选择带有 SECURITY.md 的仓库；缺失时在报告中标注为已知风险。",
)
```

> ⚠️ `rationale` 是**必填**的：它是规则的判定依据，会被 `list-checks` 与报告一并输出，也是评审时的审计依据。测试会校验所有检查项都有该字段。

### 5. 补充测试

在 `code/tests/run_tests.py` 中增加对应断言。至少覆盖"达标"与"不达标"两条路径：

```python
check(outcome.score == 1.0, "存在 SECURITY.md 时满分")
check(outcome.score == 0.0, "缺失 SECURITY.md 时为 0 分")
```

---

## 贡献 fixtures 场景

fixtures 既是演示样例也是回归用例，**我们尤其欢迎边界场景**。

### 目录结构

```
fixtures/<owner>__<repo>/          # 注意是双下划线
├── repo.json                      # 仓库事实
└── milestones.json                # Milestone 与 issue 明细
```

### `repo.json` 关键字段

| 字段 | 说明 |
| --- | --- |
| `full_name` | `owner/repo` |
| `license_spdx` | `MIT` / `NOASSERTION` / `NONE` |
| `is_archived` / `is_fork` | 触发准入规则的开关 |
| `commit_sha` | 40 位十六进制，用于 `ADM-03` |
| `pushed_at` / `commit_count_recent` / `contributor_count` | 活跃度信号 |
| `description` / `topics` / `has_ci` / `has_manifest` | 质量信号 |
| `files[]` | 文件清单，每项含 `path`（必需）与 `size` |

> 💡 **务必提供 `files[]` 清单**。若缺失，采集层会退化为使用预聚合结构信号，并产生一条 warning——这会跳过 `derive_structure()`，覆盖不到结构推导逻辑。

### `milestones.json` 关键字段

每个 Milestone 含 `number` / `title` / `description` / `state` / `due_on` / `open_issues` / `closed_issues`，以及 `issues[]`。每个 issue 含 `labels[]` / `assignees[]` / `body_length` / `comments` / `state`。

> 💡 `body_length` 是 `MS-04` 判定"正文充分率"的依据（阈值 120 字符），请填写真实长度。

### 验证你的 fixtures

```bash
cd code
python -m ghsqa check --repo <owner>/<repo> --source fixtures --fixtures ../fixtures --out ../out/tmp
```

请确认判定结果符合你的预期，并在 PR 描述中说明**这个场景要验证什么规则**。

---

## 改进文档

文档位于 `docs/`，采用 Datawhale 教程风格：

- 中文，第二人称"你"，讲解式而非 API 文档式；
- 每章开头用 `> **本章导读**` 引用块；
- 每章结尾有 `## 本章小结`；
- 代码块标注语言；
- **诚实优先**：只写源码中真实存在的字段与函数名，不要杜撰。

---

## 提交 Pull Request

1. Fork 本仓库并创建特性分支：`git checkout -b feat/your-rule-name`
2. 完成改动，确保 `python -m tests.run_tests` 全部通过
3. 提交信息请说清**动机**而非只描述改动：

   ```
   feat(rules): 新增 REPO-08 安全策略检查项

   缺少 SECURITY.md 的仓库无法评估漏洞上报路径，
   在 AI Coding 任务场景下会导致维护责任不清。
   权重设为 0.6，非硬性阻断，避免误伤小型库。
   ```

4. 提交 PR，并在描述中说明：
   - 改动了哪些规则 / 文件；
   - 为什么这样设计（判定依据）；
   - 如何验证（附上命令与结果）。

&emsp;&emsp;我们会尽快 review。感谢你的贡献 ❤️
