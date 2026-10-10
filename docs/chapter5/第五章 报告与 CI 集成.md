# 第五章 报告与 CI 集成

> **本章导读**
>
> 前面几章你已经能把一条仓库从采集一路质检出结论了。但结论如果不落到**机器和人各自能读懂的产物**上，流水线就只是个玩具。
>
> 本章讲三件事：
>
> 1. **产物形态**——`check` 到底写出哪几个文件，Markdown 报告的六个章节分别回答什么问题。
> 2. **退出码约定**——这是 CI 判定的唯一基础，必须精确掌握 0 / 1 / 2 / 3 / 130 的含义。
> 3. **接进 CI**——一份可直接用的 GitHub Actions workflow，以及不联网也能复现整条链路的本地命令。
>
> 最后还有一个很实用的小节：拿到一份 `REVIEW` 报告后，应该按什么顺序读、怎么决定 accept 还是 reject。

---

## 1. 为什么一种报告格式不够

### 1.1 两个读者，两种需求

`report.py` 的模块 docstring 用一句话交代了设计动机：

> Markdown 面向人工评审与 PR 留档；XML 面向 CI（GitHub Actions 可直接展示）。

展开说，这两类读者的需求几乎是正交的：

| | 人工评审 / PR 留档 | CI 系统 |
| --- | --- | --- |
| **首选格式** | Markdown | JUnit XML |
| **关注点** | 为什么是这个结论？依据是什么？ | 哪些检查项失败了？ |
| **可读性要求** | 高——要在 PR 里直接看 | 低——只要结构正确 |
| **典型动作** | 留档、签字、讨论 | 渲染注释、控制 job 成败 |
| **渲染方** | GitHub / GitLab 的 Mardown 渲染器 | Actions 的测试报告插件、Jenkins |

Markdown 塞进 CI 的测试报告解析器里没法用；JUnit XML 贴到 PR 评论里没人愿意读。所以 `check` 默认产出 Markdown，加 `--junit` 额外产出 XML。

### 1.2 一个重要区别：JUnit 不表达最终判定

这一点必须提前说清楚，否则你会在 CI 里踩坑。

看 `render_junit()` 怎么算 `failures`：

```python
failures = sum(1 for g in result.gates for c in g.checks if not c["passed"])
tests = sum(len(g.checks) for g in result.gates) + len(result.skipped_gates)
```

`failures` 数的是**检查项级别的未通过数**。它**完全不管** `final_verdict` 是 `PASS`、`REVIEW` 还是 `REJECT`。

后果是什么？考虑一个所有 21 个检查项都通过的仓库：

- 自动结论 `PASS`；
- 但没提交人工结论 → 最终结论 `REVIEW`；
- 退出码 `1`；
- 而 JUnit 里是 `failures="0"`，**全绿**。

实测确认（`acme/widget-toolkit` fixtures，未提交人工结论）：

```xml
<testsuites name="hello-repoqa" tests="21" failures="0">
  <testsuite name="准入质检 (ADM)" tests="7" failures="0">
    <testcase name="ADM-01 仓库公开且可访问" classname="ADM"/>
    ...
```

而同一轮的退出码是 `1`。

所以正确的用法是：

- **退出码**决定 job 成败（这是权威判定）；
- **JUnit XML** 提供检查项级别的可视化和历史趋势；
- 两者配合，不要指望 JUnit 替你表达「需人工介入」。

---

## 2. Markdown 报告的六个章节

`render_markdown()` 的签名是：

```python
def render_markdown(result: PipelineResult, with_details: bool = True) -> str:
```

`with_details=True`（默认）输出完整报告；`with_details=False` 用于终端打印，会略去各闸门的检查项明细表，得到一份精简版。

下面按顺序看六个章节。

### 2.1 一、总览

对应 `lines.append("## 一、总览")` 之后的那张表，是整份报告的**结论卡片**：

```markdown
## 一、总览

| 项目 | 结果 |
| --- | --- |
| 仓库 | `acme/widget-toolkit` |
| 固定版本 (commit) | `aa11bb22cc33dd44ee55ff66aa77bb88cc99dd00` |
| 数据源 | fixtures |
| 目标 Milestone | 7 |
| 自动质检结论 | ✅ PASS |
| **最终结论** | **✅ PASS** |
| 综合得分 | 96.14 / 100 |
```

几个值得注意的点：

- **「自动质检结论」和「最终结论」是两行**。这是有意分开的——重复强调上一章的核心设计：机器说的和人签字的不是一回事。
- **`固定版本 (commit)`** 取自 `snap.pinned_ref`，未固定时显示 `**未固定**`。
- **只有在线采集且下载了 tarball 时**，才会多出两行：

  ```python
  if snap.archive_sha256:
      lines.append("| 归档 SHA256 | `%s` |" % snap.archive_sha256)
      lines.append("| 归档体积 | %s |" % human_bytes(snap.archive_bytes))
  ```

  用 `--no-archive` 或走 fixtures 时不会有这两行。

紧接着，如果有采集告警（`snap.warnings`），会以引用块形式列出：

```markdown
> ⚠️ 采集告警：
> - fixtures 未提供 files 清单，已退化为使用预聚合结构信号
```

### 2.2 二、仓库事实（快照）

这一节回答「我们到底采集到了什么」。表格字段与 `RepoFacts` 对应：

| 指标 | 来源字段 | 说明 |
| --- | --- | --- |
| 简介 | `repo.description` | 截断到 80 字符 |
| 主语言 | `repo.language` | — |
| 许可证 | `repo.license_spdx` 或 `license_name` | 都没有则显示「未识别」 |
| Stars / Forks | `repo.stars` / `repo.forks` | — |
| 贡献者 | `repo.contributor_count` | — |
| 文件数 | `repo.file_count` | 同时拆分代码 / 测试 / 文档 |
| 目录最大深度 | `repo.max_depth` | — |
| 最近提交 | `repo.pushed_at` 或 `commit_date` | — |
| CI / 依赖清单 / 测试目录 | `has_ci` / `has_manifest` / `has_tests_dir` | 渲染为 有 / 无 |
| Topics | `repo.topics` | 最多 8 个，带反引号 |

注意 `repo is None` 时整节会被跳过（`if repo is not None:`），这在采集异常时会发生。

### 2.3 三、Milestone 事实

如果快照里有里程碑（`snap.milestones` 非空），列出全部：

```markdown
| # | 标题 | 状态 | 任务数 | 完成度 | 截止 |
| --- | --- | --- | --- | --- | --- |
| 7 | v2.0 响应式布局引擎 | open | 10 | 60.0% | 2026-11-30 |
```

标题截断到 40 字符，截止日期取前 10 个字符（即 `YYYY-MM-DD`），没有则显示 `—`。完成度来自 `ms.completion` 乘以 100。

这一节的价值在于：**它列出了所有里程碑，而不只是被评估的那个**。这样你能一眼看出 `--milestone 7` 是不是一个合理的选择（比如旁边还有个任务数更多的里程碑）。

### 2.4 四、三级自动质检

先是一张总表，每个闸门一行：

```markdown
| 闸门 | 得分 | 判定 | 硬性阻断 | 说明 |
| --- | --- | --- | --- | --- |
| 准入质检 | 100.00 | ✅ PASS | — | 得分 100.00/100，无硬性阻断，判定 PASS |
| Repo 质量质检 | 96.72 | ✅ PASS | — | 得分 96.72/100，无硬性阻断，判定 PASS |
| Milestone 质量质检 | 93.62 | ✅ PASS | — | 得分 93.62/100，无硬性阻断，判定 PASS |
```

被跳过的闸门会追加在下面：

```python
for key in result.skipped_gates:
    lines.append("| %s | — | ⏭ 跳过 | — | 上一阻断型闸门未通过 |" % key)
```

在归档仓库的例子里（`legacycorp/abandoned-metrics-lib`），这里会出现 `REPO` 和 `MS` 两行「⏭ 跳过」——因为 `ADM` 是阻断型闸门，它一 `REJECT`，后面的闸门就不执行了，避免在明显不合格的仓库上浪费预算。

总表之后是每个闸门的明细（仅在 `with_details=True` 时输出）：

```markdown
### 准入质检（ADM）

| 检查项 | 权重 | 得分 | 判定 | 实测 |
| --- | --- | --- | --- | --- |
| `ADM-01` 仓库公开且可访问 | 1.0 | 1.00 | ✅ 🔒 | 公开可访问，默认分支 main，语言 TypeScript |
| `ADM-04` 许可证明确 | 1.0 | 1.00 | ✅ | 许可证：MIT |
```

两个视觉标记要认得：

- **✅ / ❌** 表示该项是否达到自己的 `pass_at`；
- **🔒** 表示这是硬性（`hard=True`）检查项，一票否决性质。

「实测」列取自 `check["detail"]`，截断到 70 字符——这是自动规则给出的**人类可读解释**，比如「测试/代码文件比 50.0%（3/6），测试目录存在」。想知道某个分数是怎么来的，就看这一列。

### 2.5 五、单轮人工质检

这一节有两种形态，取决于有没有提交人工结论。

**已提交**：

```markdown
## 五、单轮人工质检

- 人工状态：**人工终审通过**
- 评审人：Your Name
- 评审时间：2026-10-10T01:20:29Z

| 问题 | 裁决 | 备注 |
| --- | --- | --- |
| `H-01` | accept | MIT 许可证，LICENSE 全文无附加条款… |
```

**未提交**：

```markdown
## 五、单轮人工质检

- 人工状态：**未执行人工质检**
- 🚫 阻断原因：缺少单轮人工质检结论
- 尚未提交人工质检结论 → 最终判定最多为 REVIEW，不得放行。

需人工裁决的问题清单：

| 问题 | 说明 | 为何机器判不准 |
| --- | --- | --- |
| `H-01` | 许可证是否真的允许我们把该仓库的代码/文档用作采集素材并再分发？ | 自动质检只能识别 SPDX 标识，无法判断附加条款、第三方素材与商标限制。 |
```

注意未提交时，报告会把 `MANUAL_QUESTIONS` 里每个问题的 `question` 和 `why` 都列出来。**这意味着评审人可以直接拿这份报告当工作底稿**——不需要额外查文档就知道要回答什么、为什么机器答不了。

`blocking` 列表里的每一条都会渲染成一行 `- 🚫 阻断原因：...`。回顾第四章的合并规则，可能出现的文案包括：

- `缺少单轮人工质检结论`
- `人工判定拒绝`
- `人工判定需返工`
- `人工质检仍有 N 条未裁决`
- `存在硬性阻断且未被显式豁免`
- `人工结论状态不可识别`

### 2.6 六、需人工重点复核项

这是第四章讲过的 `build_review_queue()` 的渲染结果，**只在队列非空时出现**：

```markdown
## 六、需人工重点复核项

| 检查项 | 得分 | 触发原因 | 建议动作 |
| --- | --- | --- | --- |
| `ADM-04` 许可证明确 | 0.60 | 得分处于模糊区间，机器难以定论 | 优先选择 MIT / Apache-2.0 / BSD 等宽松许可… |
| `MS-03` 任务边界可界定 | 0.90 | 硬性检查项未满分，需确认是否可放行 | 目标是 3~30 个任务、有明确描述与截止时间的里程碑… |
```

「建议动作」列来自 `fix_hint`，是每个检查项在 `rules.py` 里预先写好的整改提示。

报告最后统一以固定尾注结尾：

```markdown
---

*本报告由自动质检流水线生成，人工结论部分需评审人签字确认后方可归档。*
```

### 2.7 六个章节速查

| 章节 | 回答的问题 | 数据来源 |
| --- | --- | --- |
| 一、总览 | 最终结论是什么？分数多少？ | `PipelineResult` 顶层字段 |
| 二、仓库事实 | 采集到了什么？ | `snapshot.repo` |
| 三、Milestone 事实 | 有哪些里程碑？选了哪个？ | `snapshot.milestones` |
| 四、三级自动质检 | 自动分怎么来的？哪项扣分了？ | `result.gates` |
| 五、单轮人工质检 | 人怎么看？签了没？ | `result.manual` |
| 六、需人工重点复核项 | 机器在纠结什么？ | `result.review_queue` |

---

## 3. 三种产物与 slug 规则

### 3.1 slug 规则：`owner__repo`

所有产物文件名都以 slug 开头，规则只有一行：

```python
slug = args.repo.replace("/", "__")
```

也就是把 `--repo` 里的 `/` 换成**双下划线** `__`：

| `--repo` | slug | 报告文件名 |
| --- | --- | --- |
| `acme/widget-toolkit` | `acme__widget-toolkit` | `acme__widget-toolkit.report.md` |
| `datawhalechina/hello-agents` | `datawhalechina__hello-agents` | `datawhalechina__hello-agents.report.md` |

用双下划线而不是单下划线，是为了避免和仓库名本身的下划线混淆——`acme/widget_toolkit` 和 `acme_widget/toolkit` 不会撞名。

这个 slug 规则**和 fixtures 目录的命名完全一致**（`code/ghsqa/collector.py` 里 `collect_from_fixtures()` 用的是同一个 `.replace("/", "__")`），所以 fixture 目录名和产物名天然对齐。

### 3.2 三个核心产物

`check` 命令落盘的东西（`__main__.py`）：

```python
snap_path = os.path.join(args.out, "%s.snapshot.json" % slug)
write_json(snap_path, result.snapshot.to_dict())

report_path = os.path.join(args.out, "%s.report.md" % slug)
with open(report_path, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(render_markdown(result))

json_path = os.path.join(args.out, "%s.report.json" % slug)
write_json(json_path, result.to_dict())

if args.junit:
    junit_path = os.path.join(args.out, "%s.junit.xml" % slug)
    with open(junit_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_junit(result))
```

逐个说：

#### `<slug>.snapshot.json` —— 固定快照

这是采集层的原始产出，内容就是 `result.snapshot.to_dict()`。它记录的是**事实**，不含任何判定：仓库元数据、文件清单推导出的结构信号、里程碑与 issue 明细、采集时间、数据源、归档 sha256（如果有）、warnings。

为什么单独存一份？三个理由：

1. **可复现中间产物**。质检规则改了、权重调了，可以直接拿这份快照重跑，不用重新联网采集。
2. **可审计**。配合 `pinned_ref` 里的 commit sha 和 `archive_sha256`，能证明这一轮质检评测的到底是哪份代码。
3. **可归档**。它是 PR 里「我们当时看到的仓库长什么样」的证据。

#### `<slug>.report.json` —— 机器可读的完整结论

这是 `result.to_dict()`，一份自描述的完整报告。它的顶层结构：

```json
{
  "schema": "hello-repoqa/report@1",
  "generated_at": "...",
  "rubric": {"name": "hello-repoqa/default", "version": "1.0.0"},
  "request": {...},
  "snapshot": {...},
  "gates": [...],
  "skipped_gates": [...],
  "overall_score": 96.14,
  "auto_verdict": "PASS",
  "final_verdict": "PASS",
  "manual": {...},
  "review_queue": [...]
}
```

`"schema": "hello-repoqa/report@1"` 这个字段很关键——它是给你下游脚本做版本判断用的。注意它和 `rubric` 版本（`1.0.0`）是两码事：前者是**报告格式**的版本，后者是**评分规则**的版本。规则改了但格式没变，只需看 `rubric.version`。

`manual` 字段是个嵌套结构，含 `review` / `merge` / `questions` 三部分：

```python
manual_summary = {
    "review": review.to_dict(),   # 提交的人工结论；未提交时为 null
    "merge": merged,              # apply_review() 的结果
    "questions": manual.MANUAL_QUESTIONS,
}
```

所以你想在下游拿到「最终判定 + 阻断原因」，直接读 `manual.merge.final_verdict` 和 `manual.merge.blocking` 就行。

#### `<slug>.report.md` —— 人读报告

就是上一节讲的六章节 Markdown。用 `newline="\n"` 写入，保证 Unix 换行。

#### `<slug>.junit.xml` —— 可选，CI 用

只在加 `--junit` 时生成。结构是 `<testsuites>` 套三个 `<testsuite>`（一个闸门一个），每个 `<testcase>` 对应一个检查项：

- 通过的检查项：自闭合的 `<testcase .../>`；
- 未通过的：带 `<failure message="score=0.60">`，内容是该检查项的 `detail`；
- 被跳过的闸门：单独一个 `<testsuite>`，里面是一个带 `<skipped/>` 的 testcase。

```xml
<testsuites name="hello-repoqa" tests="21" failures="0">
  <testsuite name="准入质检 (ADM)" tests="7" failures="0">
    <testcase name="ADM-01 仓库公开且可访问" classname="ADM"/>
```

`classname` 是闸门 key（`ADM` / `REPO` / `MS`），方便在 CI 里按闸门分组统计。

### 3.3 `--out` 目录长什么样

一次带 `--junit` 的完整运行之后：

```
out/
├── archive/                          # 仅在线采集且未加 --no-archive 时
│   └── widget-toolkit-aa11bb22cc33.tar.gz
├── acme__widget-toolkit.snapshot.json
├── acme__widget-toolkit.report.json
├── acme__widget-toolkit.report.md
└── acme__widget-toolkit.junit.xml
```

`archive/` 目录的路径是 `os.path.join(args.out, "archive")`，受 `--no-archive` 控制。走 fixtures 时不会有归档下载，这个目录不会生成。

---

## 4. 退出码约定

这是全章最需要背下来的部分。`main()` 和 `_run()` 两个函数共同决定了退出码：

```python
def main(argv=None) -> int:
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
```

以及 `_run()` 的结尾：

```python
if result.final_verdict == "PASS":
    return 0
if result.final_verdict == "REVIEW":
    return 1
return 2
```

### 4.1 五个退出码

| 退出码 | 含义 | 触发条件 |
| --- | --- | --- |
| **0** | `PASS` | 最终判定 `PASS`，即人工已确认放行 |
| **1** | `REVIEW` | 最终判定 `REVIEW`，需人工介入 |
| **2** | `REJECT` | 最终判定 `REJECT`；**以及读取 `--review` 文件失败** |
| **3** | 采集失败 | 抛出 `CollectError`（仓库不存在、网络失败、fixtures 缺失等） |
| **130** | 被中断 | 捕获到 `KeyboardInterrupt`（Ctrl+C） |

### 4.2 几个必须注意的细节

**第一，`0` 意味着「人工已签字」，不只是「机器说好」。**

这是最容易误解的一条。回顾上一章的合并规则：只要没提交人工结论，最终判定最高只能是 `REVIEW`。所以：

```bash
# 自动 PASS，但没人签字
$ python -m ghsqa check --repo acme/widget-toolkit --source fixtures \
    --fixtures ../fixtures --milestone 7 --out out --quiet
[REVIEW] acme/widget-toolkit | 自动 PASS | 综合 96.14/100
$ echo $?
1

# 同一份数据，带上人工 approve 的结论
$ python -m ghsqa check --repo acme/widget-toolkit --source fixtures \
    --fixtures ../fixtures --milestone 7 --review out/review.json --out out --quiet
[PASS] acme/widget-toolkit | 自动 PASS | 综合 96.14/100
$ echo $?
0
```

两次自动分都是 `96.14`，退出码却是 `1` 和 `0`。差别完全在人工那一步。

**第二，`2` 还有一个隐藏含义：`--review` 文件读不出来。**

```python
if args.command == "check" and args.review:
    try:
        review = manual_mod.load_review(args.review)
    except Exception as exc:
        print("读取人工质检结论失败：%s" % exc, file=sys.stderr)
        return 2
```

路径写错、JSON 语法错误，都会走到这里。这意味着 CI 里看到 `2` 时，有两种完全不同的可能：仓库被拒，或者你的 `--review` 参数有问题。**排查时先看 stderr**——前者不会有「读取人工质检结论失败」这行。

**第三，`snapshot` 子命令永远返回 `0`。**

看 `_run()` 里 `snapshot` 分支：

```python
if args.command == "snapshot":
    print("快照已保存：%s" % snap_path)
    print(render_summary_line(result))
    return 0
```

它只做采集、不算判定，所以无论摘要行显示什么，退出码都是 `0`（除非采集抛 `CollectError` → `3`）。**不要用 `snapshot` 的退出码做 CI 门禁。**

**第四，`3` 优先于一切判定。**

`CollectError` 是在 `collect()` 里抛的，发生在任何闸门执行之前。所以采集失败时不会有报告产生，也谈不上 `PASS`/`REVIEW`/`REJECT`——只有 `3`。

**第五，`130` 是 POSIX 惯例。**

`128 + SIGINT(2) = 130`。CI 里正常不会看到这个值，除非 job 被取消。

### 4.3 对应的 shell 判定写法

因为退出码是分层的，bash 里可以直接这样分流：

```bash
python -m ghsqa check --repo "$REPO" --source fixtures --fixtures fixtures --out out --quiet
code=$?
case "$code" in
  0)   echo "PASS：可放行" ;;
  1)   echo "REVIEW：需要人工评审" ;;
  2)   echo "REJECT：不放行（或 --review 文件有问题，查 stderr）" ;;
  3)   echo "采集失败：检查仓库名、网络或 fixtures 路径" ;;
  130) echo "被中断" ;;
  *)   echo "未知退出码 $code" ;;
esac
```

而在 GitHub Actions 里，你甚至不需要显式判断——**shell 步骤的退出码就是 job 的成败**（下一节展开）。

---

## 5. CLI 全貌

### 5.1 四个子命令

`__main__.py` 的 docstring 已经把职责写清楚了：

| 子命令 | 做什么 | 产出 | 退出码 |
| --- | --- | --- | --- |
| `snapshot` | 只做采集，把固定快照落盘 | `<slug>.snapshot.json` | `0` / `3` / `130` |
| `check` | 采集 + 三级自动质检 + 单轮人工质检 + 报告 | snapshot + report.json + report.md（+ junit.xml） | `0` / `1` / `2` / `3` / `130` |
| `init-review` | 生成人工质检模板 JSON | 指定路径的 JSON | `0` |
| `list-checks` | 打印当前 rubric 的全部检查项（自文档化） | stdout | `0` |

`init-review` 和 `list-checks` **没有 `add_common()`**，它们的参数是独立的（`init-review` 只有 `--repo` / `--reviewer` / `--out`，`list-checks` 一个参数都没有）。

### 5.2 公共参数（`snapshot` 与 `check` 共有）

`add_common()` 定义了 7 个参数：

| 参数 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--repo` | ✅ | — | 仓库标识，形如 `owner/repo` |
| `--ref` | ❌ | `None` | 要固定的分支 / 标签 / commit sha（默认用默认分支） |
| `--milestone` | ❌ | `None` | 目标 Milestone 编号或标题 |
| `--source` | ❌ | `"api"` | 数据源：`api`（在线）或 `fixtures`（离线事实包） |
| `--fixtures` | ❌ | `None` | fixtures 目录（`source=fixtures` 时必需） |
| `--out` | ❌ | `"out"` | 输出目录 |
| `--max-pages` | ❌ | `5` | 分页上限，防止大仓库拉取过久 |
| `--no-archive` | ❌ | `False` | 跳过 tarball 归档下载 |

关于 `--fixtures` 有个便利行为：

```python
fixtures_dir = args.fixtures or ("fixtures" if args.source == "fixtures" else None)
```

也就是说，用 `--source fixtures` 时如果不给 `--fixtures`，它会**默认去找当前目录下的 `fixtures/`**。显式传 `--fixtures ../fixtures` 只是为了让路径清楚。

关于 `--milestone`，它在 `collector.py` 里同时支持编号和标题匹配：

```python
if want is None or str(ms.number) == str(want) or ms.title == want:
```

所以 `--milestone 7` 和 `--milestone "v2.0 响应式布局引擎"` 都能定位到同一个里程碑。**写了 `--milestone` 但快照里不存在时不会报错**——`MS-02 目标 Milestone 可定位` 会判不通过，报告里能看到。

### 5.3 `check` 的专属参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--review` | `None` | 人工质检结论 JSON；**省略则最终判定为 `REVIEW`** |
| `--junit` | `False` | 额外输出 JUnit XML（供 CI 使用） |
| `--quiet` | `False` | 仅输出摘要行 |

这三个参数在 `_run()` 里的行为：

**`--quiet`**：不打印完整 Markdown，只打印一行摘要：

```python
if args.quiet:
    print(render_summary_line(result))
else:
    print(render_markdown(result, with_details=False))
    print("报告已保存：")
    print("  - %s" % report_path)
    print("  - %s" % json_path)
```

摘要行的格式（`render_summary_line()`）：

```
[PASS] acme/widget-toolkit | 自动 PASS | 综合 96.14/100
```

注意里面同时有**最终判定**（方括号里）和**自动判定**（`自动 XXX`），一眼就能看出两者是否分叉。

**`--junit`**：写 XML 文件，并且只在**非 quiet** 模式下打印它的路径：

```python
if args.junit:
    junit_path = os.path.join(args.out, "%s.junit.xml" % slug)
    with open(junit_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_junit(result))
    if not args.quiet:
        print("JUnit 报告：%s" % junit_path)
```

这说明 `--quiet` 和 `--junit` 是**可以且推荐一起用**的：CI 里既想要 XML 产物，又不想让日志被整份报告淹没。

### 5.4 `list-checks`：把 rubric 当文档读

这个子命令不需要任何参数：

```bash
python -m ghsqa list-checks
```

输出形如：

```
rubric: hello-repoqa/default v1.0.0 (pass>=75, review>=60)

[ADM]
  ADM-01    仓库公开且可访问             weight=1.0 HARD pass_at=0.50
            └─ 无法访问或已被禁用的仓库不具备采集前提。
  ADM-02    仓库未归档                   weight=1.0 HARD pass_at=0.50
            └─ 归档仓库停止演进，无法再产出真实的开发里程碑，任务会退化为练习题。
  ...
[REPO]
  REPO-01   文档完备度                   weight=1.0 pass_at=0.60
            └─ README 与文档是理解任务上下文的第一入口，缺文档会让任务成本不可估。
```

它把 `DEFAULT_RUBRIC` 的名称、版本、两个阈值，以及每个闸门下所有检查项的 ID、标题、权重、是否 HARD、`pass_at` 和 `rationale` 全列出来。**这是排查「为什么这项扣分了」的第一站**——比翻源码快得多。

---

## 6. GitHub Actions 集成

### 6.1 设计要点

在写 YAML 之前，先把几个决策点想清楚：

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| 用哪个数据源？ | PR 场景用 `fixtures` | 离线、快、无网络抖动、结果确定 |
| 要在 PR 里跑回归吗？ | 要 | 改 rubric 后必须验证判定路径没坏 |
| 怎么控制 job 成败？ | 靠退出码 | `0` 通过，`1`/`2`/`3` 失败 |
| `1`（REVIEW）算失败吗？ | 默认算 | 需要人工介入本身就是门禁信号 |
| JUnit 怎么用？ | 上传 + 渲染 | 提供检查项级别的可视化 |
| 日志要多啰嗦？ | `--quiet` | 避免整份报告刷屏，正文去 artifact 里看 |

### 6.2 完整 workflow

假设你要在 `hello-repoqa` 这个仓库里，对 fixtures 中的目标仓库做质检（这样每次结果确定、无需 `GITHUB_TOKEN`）：

```yaml
name: repoqa-check

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  workflow_dispatch:
    inputs:
      repo:
        description: 目标仓库（owner/repo）
        required: true
        default: acme/widget-toolkit
      milestone:
        description: 目标 Milestone 编号或标题
        required: false
        default: "7"

# 同一分支的多次推送只保留最新一次
concurrency:
  group: repoqa-${{ github.ref }}
  cancel-in-progress: true

jobs:
  check:
    runs-on: ubuntu-latest

    env:
      TARGET_REPO: ${{ github.event.inputs.repo || 'acme/widget-toolkit' }}
      TARGET_MILESTONE: ${{ github.event.inputs.milestone || '7' }}

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      # 让中文报告在 runner 上正常输出，避免 UnicodeEncodeError
      - name: Configure UTF-8
        run: echo "PYTHONIOENCODING=utf-8" >> "$GITHUB_ENV"

      # 1) 回归测试：改规则/rubric 后必须保证判定路径没坏
      - name: Run regression tests
        working-directory: code
        run: python -m tests.run_tests

      # 2) 质检：--quiet 只留一行摘要，--junit 产出 CI 可渲染的 XML
      #    该步骤的退出码即 job 成败：0=PASS, 1=REVIEW, 2=REJECT, 3=采集失败
      - name: Run repoqa check
        id: check
        working-directory: code
        run: |
          python -m ghsqa check \
            --repo "$TARGET_REPO" \
            --source fixtures \
            --fixtures ../fixtures \
            --milestone "$TARGET_MILESTONE" \
            --junit \
            --quiet \
            --out ../out

      # 3) 无论上一步成败都上传产物，方便人工排查
      - name: Upload reports
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: repoqa-reports
          path: out/
          if-no-files-found: warn
          retention-days: 30

      # 4) 把 JUnit XML 渲染到 PR 注释 / Checks 里
      - name: Publish test report
        if: always()
        uses: mikepenz/action-junit-report@v4
        with:
          report_paths: "out/*.junit.xml"
          check_name: "RepoQA 检查项明细"
          include_passed: true
          detailed_summary: true

      # 5) 失败时给出人话解释
      - name: Explain failure
        if: failure()
        run: |
          echo "::error::质检未通过。退出码含义：1=REVIEW(需人工介入)，2=REJECT，3=采集失败。"
          echo "请下载 repoqa-reports artifact 查看 report.md 与 report.json。"
```

### 6.3 逐步解读

**Checkout + Setup Python** 是标准两步。项目本身无第三方依赖（`collector.py` 用的是标准库 `urllib`），所以**不需要 `pip install`**。

**Configure UTF-8** 这一步是实战经验。报告里有大量中文，本地 Windows 上我们已经遇到 `UnicodeEncodeError: 'gbk' codec can't encode character`——直接在源码里 `print("  ✓ %s")` 时炸掉。设 `PYTHONIOENCODING=utf-8` 可以避免。Ubuntu runner 默认通常是 UTF-8，但显式设置更稳妥。

**Run regression tests** 跑的是 `python -m tests.run_tests`，它不需要 pytest、不需要网络。这一步的价值在于：如果你改了 `rubric.py` 的权重或 `manual.py` 的合并规则，58 项断言会立刻告诉你判定路径是否被改坏。**先跑回归、再跑质检**的顺序很重要——回归失败时就没必要浪费时间去采集了。

**Run repoqa check** 是核心步骤，注意四个选择：

- `--source fixtures --fixtures ../fixtures`：离线、确定。`working-directory: code`，所以 fixtures 在 `../fixtures`。
- `--junit`：产出 `out/*.junit.xml`。
- `--quiet`：只输出一行 `[PASS] acme/widget-toolkit | 自动 PASS | 综合 96.14/100`。不加 `--quiet` 的话，整份 Markdown（含三个闸门的明细表）会刷满日志，把真正的错误信息淹没。
- `--out ../out`：写到仓库根下的 `out/`，方便下一步上传。

这一步**没有 `continue-on-error: true`**——这是有意的。退出码非零会让 job 失败，这就是门禁本身。

**Upload reports** 用 `if: always()`，因为质检失败时你**最需要**看报告。`path: out/` 会把三个（或四个）产物一起打包。

**Publish test report** 把 JUnit XML 渲染成 PR 注释。`include_passed: true` 让通过的检查项也显示出来，这样你看到的是完整清单而不是只有失败项。

**Explain failure** 用 `if: failure()`，把退出码含义直接写进 job 日志和 GitHub 的注解里，减少沟通成本。

### 6.4 PR 场景下 `--quiet` 与 `--junit` 的配合

这两个参数配合起来有一个明确的分工，值得单独强调：

| | 作用 | PR 场景下的理由 |
| --- | --- | --- |
| `--junit` | 生成结构化结果 | PR 界面需要检查项级别的展开/折叠视图；Markdown 做不到按检查项折叠 |
| `--quiet` | 抑制冗长 stdout | PR 的 job 日志会被反复打开，一行摘要足够定位问题；明细去 artifact 或 JUnit 注释里看 |

如果不加 `--quiet`，日志里会有完整的 Markdown（`with_details=False` 版本，仍含总表和人工质检节），再叠加 JUnit 注释，信息就重复了。

**一个关于 `--junit` 的重要提醒**（呼应 §1.2）：JUnit 的 `failures` 只统计检查项，不反映最终判定。在 PR 里可能出现「JUnit 全绿但 job 红」的情况——那是因为所有检查项都通过了，只是**缺人工签字**，最终判定是 `REVIEW`（退出码 `1`）。

这种时候不要以为是工具坏了。正确的处理是：

1. 看 job 日志里的摘要行，`[REVIEW]` 就是它；
2. 去 artifact 里下 `report.md`，看「五、单轮人工质检」；
3. 如果确实应该放行，走 `init-review` → 填写 → 用 `--review` 重跑。

### 6.5 如果要在 CI 里走在线采集

把 `--source fixtures --fixtures ../fixtures` 换成默认的 `api`，并注入 token：

```yaml
      - name: Run repoqa check (online)
        working-directory: code
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          python -m ghsqa check \
            --repo "$TARGET_REPO" \
            --ref "${{ github.sha }}" \
            --milestone "$TARGET_MILESTONE" \
            --junit --quiet \
            --out ../out
```

三个要点：

- **`GITHUB_TOKEN`**：`GitHubClient` 会依次查 `GITHUB_TOKEN` 和 `GH_TOKEN`（`collector.py` 里的 `__init__`），有 token 才能提升 API 配额。
- **`--ref "${{ github.sha }}"`**：显式把快照钉在不可变 commit 上。不指定的话默认解析默认分支，`ADM-03` 只会拿 0.4 的部分分并提示「快照仅钉住可变 ref」。
- **在线采集受网络影响**，`CollectError` 会返回退出码 `3`。如果网络抖动频繁，建议对 `3` 做重试，而不要重试 `1`/`2`（那两种情况重试也是一样的结论）。

---

## 7. 本地离线复现整条流水线

CI 里能跑通的东西，本地必须能一行不改地跑通。这也是为什么项目内置了 fixtures——`__main__.py` 的 epilog 第一个示例就是离线演示。

### 7.1 前置条件

- Python 3.8+（项目实测环境是 3.8.6，CI 示例里用 3.11）；
- **无第三方依赖**，不需要 `pip install`；
- 不需要网络。

### 7.2 逐条命令

假设当前目录是 `hello-repoqa/`，代码在 `code/`。

**第一步：跑回归测试**（确认环境正常）

```bash
cd code
python -m tests.run_tests
```

期望结尾：

```
============================================================
全部通过：58 项断言
```

**第二步：看一眼 rubric 全貌**（可选，但建议）

```bash
python -m ghsqa list-checks
```

**第三步：只采集快照**

```bash
python -m ghsqa snapshot \
    --repo acme/widget-toolkit \
    --source fixtures \
    --fixtures ../fixtures \
    --milestone 7 \
    --out ../out/demo
```

输出：

```
快照已保存：../out/demo/acme__widget-toolkit.snapshot.json
[REVIEW] acme/widget-toolkit | 自动 PASS | 综合 96.14/100
```

注意摘要行显示 `[REVIEW]` 但退出码是 `0`——因为 `snapshot` 不做判定（见 §4.2 第三条）。

**第四步：完整质检，不带人工结论**

```bash
python -m ghsqa check \
    --repo acme/widget-toolkit \
    --source fixtures \
    --fixtures ../fixtures \
    --milestone 7 \
    --junit \
    --out ../out/demo
```

退出码 `1`（`REVIEW`），产物四个文件。

**第五步：生成人工质检模板**

```bash
python -m ghsqa init-review \
    --repo acme/widget-toolkit \
    --reviewer "Your Name" \
    --out ../out/demo/review.json
```

**第六步：填写模板**（把 5 条 `verdict` 改成 `accept`，`overall` 改成 `approve`），然后重跑：

```bash
python -m ghsqa check \
    --repo acme/widget-toolkit \
    --source fixtures \
    --fixtures ../fixtures \
    --milestone 7 \
    --review ../out/demo/review.json \
    --junit \
    --quiet \
    --out ../out/demo
```

这次退出码是 `0`：

```
[PASS] acme/widget-toolkit | 自动 PASS | 综合 96.14/100
```

### 7.3 三个 fixture 的用途

`fixtures/` 下有三个仓库，它们分别覆盖了不同的判定路径：

| fixture | 自动结论 | 演示的路径 |
| --- | --- | --- |
| `acme/widget-toolkit` | `PASS` | 常规工程仓库，全绿路径；含 `MS-03` 硬性项未满分触发复核队列 |
| `datawhalechina/hello-agents` | `PASS` | 教程型仓库；`ADM-04` 因 `NOASSERTION` 只得 0.60，进复核队列 |
| `legacycorp/abandoned-metrics-lib` | `REJECT` | 归档仓库被 `ADM-02` 硬性阻断，`REPO`/`MS` 被跳过 |

把「第四步」里的 `--repo` 换成这三个，就能把三条主要路径都走一遍。特别是第三个，能让你直接看到「⏭ 跳过」在报告里长什么样，以及退出码 `2`。

### 7.4 一条命令搞定全流程的脚本

如果你想反复验证，可以写成一个小脚本：

```bash
#!/usr/bin/env bash
set -uo pipefail

REPO="${1:-acme/widget-toolkit}"
MS="${2:-7}"
OUT="../out/demo"

cd code

echo "==> [1/3] 回归测试"
python -m tests.run_tests || exit 1

echo "==> [2/3] 自动质检（无人工结论）"
python -m ghsqa check --repo "$REPO" --source fixtures --fixtures ../fixtures \
    --milestone "$MS" --junit --quiet --out "$OUT"
echo "    退出码=$?（1=需要人工介入）"

echo "==> [3/3] 带人工结论重跑"
python -m ghsqa init-review --repo "$REPO" --reviewer "Local Reviewer" \
    --out "$OUT/review.json"
python - "$OUT/review.json" <<'PY'
import json, sys
path = sys.argv[1]
data = json.load(open(path, encoding="utf-8"))
data["overall"] = "approve"
for d in data["decisions"]:
    d["verdict"] = "accept"
    d["note"] = "本地离线复现：已逐条确认"
json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("    已自动填写模板：", path)
PY
python -m ghsqa check --repo "$REPO" --source fixtures --fixtures ../fixtures \
    --milestone "$MS" --review "$OUT/review.json" --junit --quiet --out "$OUT"
echo "    退出码=$?（0=可放行）"
```

注意脚本里用 `|| exit 1` 只挡回归测试，质检步骤刻意**不挡**——因为我们要观察它的退出码。

---

## 8. 如何阅读一份报告

前面讲了报告怎么生成，这一节讲**作为评审人拿到报告后怎么用**。假设你现在拿到一份最终判定是 `REVIEW` 的报告。

### 8.1 推荐的阅读顺序

人的注意力有限，报告有六节但不该按顺序读。推荐这个顺序：

```
一、总览  →  六、复核项  →  四、自动质检明细  →  五、人工质检  →  二、三（按需）
```

**第一步：一、总览——先确认「分叉」在哪**

只看两行：

```markdown
| 自动质检结论 | ✅ PASS |
| **最终结论** | **🟡 REVIEW** |
```

三种情况，处理方式完全不同：

| 自动 | 最终 | 说明 | 你的动作 |
| --- | --- | --- | --- |
| `PASS` | `PASS` | 人已签字，无异议 | 一般无需再看 |
| `PASS` | `REVIEW` | **典型情况**：机器觉得没问题，但没人签字 | 去第五节，开始评审 |
| `PASS` | `REJECT` | 人工否决了，或存在硬性阻断 | 看第五节的人工状态和阻断原因 |
| `REJECT` | `REJECT` | 机器和人都说不行 | 看第五节确认是否有硬性阻断 |

同时扫一眼 `固定版本 (commit)` 和 `归档 SHA256`——确认评的是**正确的那份代码**。这一眼能挡住很多低级错误。

**第二步：六、需人工重点复核项——机器在纠结什么**

这一节（如果存在）直接告诉你机器拿不准的具体检查项，每条都有「触发原因」和「建议动作」。这是你的**工作清单**——重点关注这些，其余的检查项既然没进队列，说明机器判得比较有把握。

特别留意触发原因是「硬性检查项未满分，需确认是否可放行」的条目，这些是第四章讲过的、需要你为一票否决项背书的点。

**第三步：四、三级自动质检明细——核查「实测」列**

对于复核队列里提到的检查项，翻到对应闸门的明细表，看「实测」列。这一列是规则的自我解释，比如：

```
| `ADM-04` 许可证明确 | 1.0 | 0.60 | ✅ | 仓库含 LICENSE 文件但 GitHub 未识别出标准 SPDX 标识 |
| `REPO-05` 依赖与体积治理 | 0.8 | 0.75 | ✅ | 存在依赖清单；主语言 Python；体积 306.1 MB 偏大，快照与任务切分成本高 |
```

看到 `0.60` 你就知道它是 `has_license_file` 那条分支给的（第四章 §2.2 讲过：`NOASSERTION` + 有 LICENSE 文件 = 0.60 部分分），于是 H-01 就有了具体的切入点。**不要只看分数，一定要看 detail。**

**第四步：五、单轮人工质检——逐条回答 H-01 ~ H-05**

未提交时这一节会列出 5 个问题及其 `why`。逐条对照报告里的证据：

| 问题 | 从报告哪里找证据 |
| --- | --- |
| H-01 许可证 | 「二、仓库事实」的「许可证」行 + `ADM-04` 的 detail |
| H-02 目录结构 | 「二、仓库事实」的「文件数」「目录最大深度」+ `REPO-02` 的 detail |
| H-03 隐含前提 | 「三、Milestone 事实」的标题 + `MS-03` 的 detail（描述长度、任务数） |
| H-04 可复现性 | `MS-05` 的 detail + 「二」里的「CI / 依赖清单 / 测试目录」行 |
| H-05 快照完整性 | **报告里没有直接证据**——必须去仓库实地看 submodule / LFS / 私有依赖 |

H-05 是唯一一条必须离开报告去看仓库本身的问题，这也是它最有价值的地方。

**第五步：二、三——按需回查**

只有在需要确认上下文时才回看。比如 `--milestone 7` 是不是个好选择，就看第三节里其他里程碑的任务数。

### 8.2 怎么决定 accept 还是 reject

把决策简化成一张表。对每条问题，问自己一个问题：

| 问题 | `accept` 的条件 | `reject` / `followup` 的条件 |
| --- | --- | --- |
| H-01 | 许可证标准且宽松（MIT / Apache-2.0 / BSD），且确认无附加条款、无第三方素材 | 许可证无法断言、有附加限制、或确认混入第三方受保护素材 |
| H-02 | 结构清晰，无大量生成/二进制产物干扰任务切分 | 生成产物占主导，任务边界被稀释 → `reject`；不确定 → `followup` |
| H-03 | 里程碑描述交代了环境、数据、外部依赖等执行前提 | 关键前提缺失、任务无法直接开工 → `reject` |
| H-04 | 能指出一条第三方可独立走通的复现路径（含依赖获取方式） | 验收依赖内网资源或未文档化的隐性知识 → `followup` |
| H-05 | 确认无 submodule / LFS / 私有依赖，或已知且不影响 | 关键资产不在快照内 → `reject`；待确认清单 → `followup` |

然后按这个规则决定 `overall`：

```
5 条全部 accept                    →  overall = approve
有 1 条以上 reject                 →  overall = reject（或 rework）
有 1 条以上 followup，但无 reject  →  overall = pending，留在 REVIEW 等补充
```

**特别提醒**（这是第四章 §8.1 讲过的坑）：`followup` 会让最终判定停在 `REVIEW`，而单条 `reject` **不会**——它必须靠 `overall` 生效。所以：

- 判了 `reject`，一定要把 `overall` 也设成 `reject` / `rework`；
- 判了 `followup`，`overall` 保持 `pending` 或写 `approve` 都行（都会被未裁决路径挡在 `REVIEW`），但**推荐明确写清楚**，别让状态含糊。

### 8.3 三种典型场景的处理

**场景 A：自动 PASS + 最终 REVIEW + 复核队列只有一条「硬性项未满分」**

这是最常见的形态（`acme/widget-toolkit` 就是这样）。看那条硬性项的 detail，确认缺失的分数是否无关紧要。如果无关紧要，5 条全 `accept` + `overall=approve`，重跑即得 `PASS`。

**场景 B：报告里有「🚫 阻断原因：存在硬性阻断且未被显式豁免」**

不要试图在 `review.json` 里把它改掉——第四章讲过，实现里**没有豁免字段**，任何 `approve` 都救不回来。去看「四」的总表里「硬性阻断」列非 `—` 的行，回到上游修数据。

**场景 C：JUnit 全绿但 CI job 红**

99% 是缺少人工签字。看日志摘要行是不是 `[REVIEW]`，然后走 `init-review` → 填写 → `--review` 重跑。见 §6.4。

---

## 本章小结

- **两种报告格式各司其职**：Markdown（`render_markdown()`）面向人工评审与 PR 留档，JUnit XML（`render_junit()`）面向 CI 渲染。**关键区别**：JUnit 的 `failures` 只统计检查项是否通过，**完全不反映最终判定**——JUnit 全绿而退出码为 `1` 是完全正常的组合。
- **Markdown 六个章节**：一、总览（结论卡片，自动与最终判定分列两行）；二、仓库事实；三、Milestone 事实；四、三级自动质检（总表 + 各闸门明细，✅/❌ 与 🔒 标记）；五、单轮人工质检（两种形态：已提交表格 / 未提交问答清单）；六、需人工重点复核项（队列为空时不出现）。
- **三种核心产物 + 一个可选产物**：`<slug>.snapshot.json`（固定快照，可复现的中间产物）、`<slug>.report.json`（机器可读完整结论，含 `schema: "hello-repoqa/report@1"`）、`<slug>.report.md`（人读报告），加 `--junit` 时多出 `<slug>.junit.xml`。**slug 规则是 `owner__repo`**（把 `/` 替换为双下划线），与 fixtures 目录命名一致。
- **退出码约定**：`0` = PASS（人工已确认放行）；`1` = REVIEW（需人工介入）；`2` = REJECT（**同时也是 `--review` 文件读取失败**）；`3` = 采集失败（`CollectError`）；`130` = 被中断（`KeyboardInterrupt`）。注意 `snapshot` 子命令永远返回 `0`，不能用作门禁。
- **四个子命令**：`snapshot` / `check` / `init-review` / `list-checks`。公共参数 7 个（`--repo` `--ref` `--milestone` `--source` `--fixtures` `--out` `--max-pages` `--no-archive`），`check` 专属 3 个（`--review` `--junit` `--quiet`）。`--quiet` 与 `--junit` 推荐一起用：前者抑制冗长日志，后者产出结构化结果。
- **CI 集成的核心思路**：先跑 `python -m tests.run_tests` 保证判定路径没坏，再跑 `python -m ghsqa check --junit --quiet`，**靠退出码控制 job 成败**，用 `if: always()` 上传产物确保失败时也能排查。PR 场景用 fixtures 保证离线与确定性；在线采集记得注入 `GITHUB_TOKEN` 并用 `--ref` 钉住 commit。
- **本地离线复现**：`fixtures/` 下三个仓库覆盖三条判定路径（`acme/widget-toolkit` 常规 PASS、`datawhalechina/hello-agents` 教程型 + 许可证模糊、`legacycorp/abandoned-metrics-lib` 归档阻断 + 闸门跳过），无需网络即可走完全流程。
- **读报告的顺序**：先看「一、总览」确认自动与最终判定是否分叉，再看「六、复核项」拿到机器纠结的清单，然后回到「四」核对「实测」列的具体依据，最后在「五」逐条回答 H-01 ~ H-05。决策时记住：**判了 `reject` 必须同时改 `overall`；判了 `followup` 会停在 `REVIEW` 等补材料；硬性阻断不能靠签字绕过。**
