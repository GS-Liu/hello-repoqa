# 第五章 报告与 CI 集成

> **本章导读**
>
> 前几章你已经能把一条仓库从采集一路质检出结论了。但结论如果不落到**机器和人各自能读懂的产物**上，流水线就只是个玩具。
>
> 本章讲三件事：**产物形态**——`check` 写出哪几个文件，Markdown 报告的六个章节分别回答什么问题；**退出码约定**——CI 判定的唯一基础，必须精确掌握 0 / 1 / 2 / 3 / 130；**接进 CI**——一份可直接用的 GitHub Actions workflow，以及不联网也能复现整条链路的本地命令。最后还有一个实用小节：拿到一份 `REVIEW` 报告后，按什么顺序读、怎么决定 accept 还是 reject。

---

## 1. 为什么一种报告格式不够

`report.py` 的模块 docstring 交代了动机：**Markdown 面向人工评审与 PR 留档；XML 面向 CI（GitHub Actions 可直接展示）。** 这两类读者的需求几乎是正交的——人要读「为什么是这个结论」，CI 只关心「哪些检查项失败了」。Markdown 塞进 CI 的测试报告解析器里没法用；JUnit XML 贴到 PR 评论里没人愿意读。所以 `check` 默认产出 Markdown，加 `--junit` 额外产出 XML。

### 1.1 一个重要区别：JUnit 不表达最终判定

这一点必须提前说清，否则你会踩坑。`render_junit()` 里的这两行是全部逻辑：

```python
failures = sum(1 for g in result.gates for c in g.checks if not c["passed"])
tests = sum(len(g.checks) for g in result.gates) + len(result.skipped_gates)
```

`failures` 数的是**检查项级别的未通过数**，**完全不管** `final_verdict` 是 `PASS`、`REVIEW` 还是 `REJECT`。后果是：一个 21 个检查项全通过的仓库，自动结论 `PASS`，但没提交人工结论 → 最终结论 `REVIEW` → 退出码 `1`，而 JUnit 里是 `failures="0"`，**全绿**。实测确认（`acme/widget-toolkit`，未提交人工结论）：

```xml
<testsuites name="hello-repoqa" tests="21" failures="0">
  <testsuite name="准入质检 (ADM)" tests="7" failures="0">
    <testcase name="ADM-01 仓库公开且可访问" classname="ADM"/>
```

而同一轮的退出码是 `1`。所以正确用法是：**退出码**决定 job 成败（权威判定）；**JUnit XML** 提供检查项级别的可视化和历史趋势。不要指望 JUnit 替你表达「需人工介入」。

---

## 2. Markdown 报告的六个章节

`render_markdown(result, with_details=True)`：默认输出完整报告；`with_details=False` 用于终端打印，会略去各闸门的检查项明细表。六个章节与数据来源一一对应：

| 章节 | 回答什么问题 | 数据来源 |
| --- | --- | --- |
| 一、总览 | 最终结论是什么？分数多少？ | `PipelineResult` 顶层字段 |
| 二、仓库事实（快照） | 采集到了什么？ | `snapshot.repo` |
| 三、Milestone 事实 | 有哪些里程碑？选了哪个？ | `snapshot.milestones` |
| 四、三级自动质检 | 自动分怎么来的？哪项扣分了？ | `result.gates` |
| 五、单轮人工质检 | 人怎么看？签了没？ | `result.manual` |
| 六、需人工重点复核项 | 机器在纠结什么？ | `result.review_queue` |

下面逐个说清楚每节的关键设计。

**一、总览** —— 整份报告的结论卡片，列出仓库、固定版本 (commit)、数据源、目标 Milestone、自动质检结论、最终结论、综合得分。注意**「自动质检结论」和「最终结论」是两行**，这是有意分开的——机器说的和人签字的不是一回事。`固定版本 (commit)` 取自 `snap.pinned_ref`，未固定时显示 `**未固定**`。**只有在线采集且下载了 tarball 时**才会多出「归档 SHA256」和「归档体积」两行。紧接着若有采集告警（`snap.warnings`），会以 `> ⚠️ 采集告警：` 引用块逐条列出。

**二、仓库事实（快照）** —— 字段与 `RepoFacts` 对应：简介（`description`，截断 80 字符）、主语言（`language`）、许可证（`license_spdx` 或 `license_name`，都没有则「未识别」）、Stars / Forks、贡献者（`contributor_count`）、文件数（`file_count`，同时拆分代码 / 测试 / 文档）、目录最大深度（`max_depth`）、最近提交（`pushed_at` 或 `commit_date`）、CI / 依赖清单 / 测试目录（`has_ci` / `has_manifest` / `has_tests_dir`，渲染为「有 / 无」）、Topics（最多 8 个）。`repo is None` 时整节被跳过，这在采集异常时会发生。 —— 字段与 `RepoFacts` 对应：简介（`description`，截断 80 字符）、主语言（`language`）、许可证（`license_spdx` 或 `license_name`，都没有则「未识别」）、Stars / Forks、贡献者（`contributor_count`）、文件数（`file_count`，同时拆分代码 / 测试 / 文档）、目录最大深度（`max_depth`）、最近提交（`pushed_at` 或 `commit_date`）、CI / 依赖清单 / 测试目录（`has_ci` / `has_manifest` / `has_tests_dir`，渲染为「有 / 无」）、Topics（最多 8 个）。`repo is None` 时整节被跳过，这在采集异常时会发生。

**三、Milestone 事实** —— 列出快照里**全部**里程碑而不只是被评估的那个：`# | 标题 | 状态 | 任务数 | 完成度 | 截止`。标题截断 40 字符，截止日期取前 10 字符（`YYYY-MM-DD`）。这样你能一眼看出 `--milestone 7` 是不是合理选择。

**四、三级自动质检** —— 先一张按闸门汇总的总表（`闸门 | 得分 | 判定 | 硬性阻断 | 说明`），被跳过的闸门追加成 `| REPO | — | ⏭ 跳过 | — | 上一阻断型闸门未通过 |`。在归档仓库的例子里会出现 `REPO` 和 `MS` 两行「⏭ 跳过」——`ADM` 是阻断型闸门，它一 `REJECT`，后面的闸门就不执行了，避免在明显不合格的仓库上浪费预算。

总表之后是各闸门明细（仅 `with_details=True` 时输出），每行是一个检查项，列为 `检查项 | 权重 | 得分 | 判定 | 实测`。两个视觉标记要认得：**✅ / ❌** 表示是否达到自己的 `pass_at`；**🔒** 表示这是硬性（`hard=True`）检查项，一票否决性质。「实测」列取自 `check["detail"]`，截断到 70 字符，是自动规则给出的**人类可读解释**，比如「测试/代码文件比 50.0%（3/6），测试目录存在」。想知道某个分数怎么来的，就看这一列。

**五、单轮人工质检** —— 有两种形态。**已提交**时列出人工状态、评审人、评审时间，以及一张 `问题 | 裁决 | 备注` 表格。**未提交**时显示人工状态为「未执行人工质检」、阻断原因为「缺少单轮人工质检结论」，并附一段说明「尚未提交人工质检结论 → 最终判定最多为 REVIEW，不得放行」，最后列出「需人工裁决的问题清单」表格（`问题 | 说明 | 为何机器判不准`）。

注意未提交时报告会把 `MANUAL_QUESTIONS` 里每个问题的 `question` 和 `why` 都列出来，**评审人可以直接拿这份报告当工作底稿**，不必额外查文档。`blocking` 列表每条渲染成一行 `- 🚫 阻断原因：...`，可能出现的文案包括：`缺少单轮人工质检结论`、`人工判定拒绝`、`人工判定需返工`、`人工质检仍有 N 条未裁决`、`存在硬性阻断且未被显式豁免`、`人工结论状态不可识别`。

**六、需人工重点复核项** —— 第四章讲过的 `build_review_queue()` 的渲染结果，**只在队列非空时出现**：`检查项 | 得分 | 触发原因 | 建议动作`。其中「建议动作」来自 `fix_hint`，是每个检查项在 `rules.py` 里预先写好的整改提示，比如 `ADM-04` 得 0.60 时显示「得分处于模糊区间，机器难以定论」+「优先选择 MIT / Apache-2.0 / BSD 等宽松许可」。报告最后统一以固定尾注结尾：`*本报告由自动质检流水线生成，人工结论部分需评审人签字确认后方可归档。*`

---

## 3. 产物与 slug 规则

### 3.1 slug 规则：`owner__repo`

`__main__.py` 里只有一行 `slug = args.repo.replace("/", "__")`——把 `--repo` 里的 `/` 换成**双下划线**：

| `--repo` | slug | 报告文件名 |
| --- | --- | --- |
| `acme/widget-toolkit` | `acme__widget-toolkit` | `acme__widget-toolkit.report.md` |
| `datawhalechina/hello-agents` | `datawhalechina__hello-agents` | `datawhalechina__hello-agents.report.md` |

用双下划线而不是单下划线，是为了避免和仓库名本身的下划线混淆——`acme/widget_toolkit` 和 `acme_widget/toolkit` 不会撞名。这个规则**和 fixtures 目录命名完全一致**（`collector.py` 里用的是同一个替换），所以 fixture 目录名和产物名天然对齐。

### 3.2 三种核心产物

**`<slug>.snapshot.json` —— 固定快照。** 采集层的原始产出，内容就是 `result.snapshot.to_dict()`，记录**事实**而不含任何判定：仓库元数据、文件清单推导出的结构信号、里程碑与 issue 明细、采集时间、数据源、归档 sha256、warnings。单独存一份有三个理由：**可复现中间产物**（规则改了、权重调了，直接拿它重跑，不用重新联网采集）；**可审计**（配合 `pinned_ref` 的 commit sha 和 `archive_sha256`，能证明这轮质检评测的到底是哪份代码）；**可归档**（PR 里「我们当时看到的仓库长什么样」的证据）。

**`<slug>.report.json` —— 机器可读的完整结论。** 就是 `result.to_dict()`，顶层含 `schema` / `rubric` / `overall_score` / `auto_verdict` / `final_verdict` / `gates` / `skipped_gates` / `manual` / `review_queue`。`"schema": "hello-repoqa/report@1"` 是给你下游脚本做版本判断用的，注意它和 `rubric` 版本（`1.0.0`）是两码事：前者是**报告格式**的版本，后者是**评分规则**的版本；规则改了但格式没变，只需看 `rubric.version`。`manual` 是嵌套结构，含三部分：`review`（提交的人工结论，未提交时为 `null`）、`merge`（`apply_review()` 的结果）、`questions`（`MANUAL_QUESTIONS`）。想在下游拿到「最终判定 + 阻断原因」，直接读 `manual.merge.final_verdict` 和 `manual.merge.blocking`。

**`<slug>.report.md` —— 人读报告。** 上一节讲的六章节 Markdown，用 `newline="\n"` 写入，保证 Unix 换行。

**`<slug>.junit.xml` —— 可选，CI 用。** 只在加 `--junit` 时生成。结构是 `<testsuites>` 套三个 `<testsuite>`（一个闸门一个），每个 `<testcase>` 对应一个检查项：通过的用自闭合 `<testcase .../>`；未通过的带 `<failure message="score=0.60">`，内容是该检查项的 `detail`；被跳过的闸门单独一个 `<testsuite>`，里面是带 `<skipped/>` 的 testcase。`classname` 是闸门 key（`ADM` / `REPO` / `MS`），方便在 CI 里按闸门分组统计。

一次带 `--junit` 的完整运行之后，`--out` 目录会有四个 `<slug>.*` 文件：`.snapshot.json`、`.report.json`、`.report.md`、`.junit.xml`。另外 `archive/` 子目录仅在线采集且未加 `--no-archive` 时存在。

---

## 4. 退出码约定

这是全章最需要背下来的部分。`main()` 捕获 `CollectError` 返回 `3`、捕获 `KeyboardInterrupt` 返回 `130`；`_run()` 结尾则按最终判定返回：

```python
if result.final_verdict == "PASS":
    return 0
if result.final_verdict == "REVIEW":
    return 1
return 2
```

| 退出码 | 含义 | 触发条件 |
| --- | --- | --- |
| **0** | `PASS` | 最终判定 `PASS`，即人工已确认放行 |
| **1** | `REVIEW` | 最终判定 `REVIEW`，需人工介入 |
| **2** | `REJECT` | 最终判定 `REJECT`；**以及读取 `--review` 文件失败** |
| **3** | 采集失败 | 抛出 `CollectError`（仓库不存在、网络失败、fixtures 缺失等） |
| **130** | 被中断 | 捕获到 `KeyboardInterrupt`（Ctrl+C），POSIX 惯例 `128+SIGINT(2)` |

### 4.1 五个必须注意的细节

**第一，`0` 意味着「人工已签字」，不只是「机器说好」。** 上一章的合并规则说过：只要没提交人工结论，最终判定最高只能是 `REVIEW`。所以同样的数据、同样的自动分，退出码完全不同：

```bash
# 自动 PASS，但没人签字
$ python -m ghsqa check --repo acme/widget-toolkit --source fixtures \
    --fixtures ../fixtures --milestone 7 --out out --quiet
[REVIEW] acme/widget-toolkit | 自动 PASS | 综合 96.14/100
$ echo $?    # 1

# 同一份数据，带上人工 approve 的结论
$ python -m ghsqa check --repo acme/widget-toolkit --source fixtures \
    --fixtures ../fixtures --milestone 7 --review out/review.json --out out --quiet
[PASS] acme/widget-toolkit | 自动 PASS | 综合 96.14/100
$ echo $?    # 0
```

**第二，`2` 还有一个隐藏含义：`--review` 文件读不出来。** `_run()` 在加载人工结论时用 `try/except` 包住，路径写错、JSON 语法错误都会 `print("读取人工质检结论失败：%s")` 到 stderr 并返回 `2`。CI 里看到 `2` 时有两种可能：仓库被拒，或 `--review` 参数有问题。**排查时先看 stderr**——前者不会有那行提示。

**第三，`snapshot` 子命令永远返回 `0`。** 它只做采集、不算判定，`_run()` 在 `snapshot` 分支里直接 `return 0`，所以无论摘要行显示什么退出码都是 `0`（除非采集抛 `CollectError` → `3`）。**不要用 `snapshot` 的退出码做 CI 门禁。**

**第四，`3` 优先于一切判定。** `CollectError` 在 `collect()` 里抛出，发生在任何闸门执行之前，所以采集失败时不会有报告产生，也谈不上 `PASS`/`REVIEW`/`REJECT`——只有 `3`。

**第五，`130` 在 CI 里正常不会看到**，除非 job 被取消。

### 4.2 对应的 shell 判定写法

因为退出码是分层的，bash 里可以直接分流：

```bash
python -m ghsqa check --repo "$REPO" --source fixtures --fixtures fixtures --out out --quiet
case $? in
  0) echo "PASS：可放行" ;;
  1) echo "REVIEW：需要人工评审" ;;
  2) echo "REJECT：不放行（或 --review 文件有问题，查 stderr）" ;;
  3) echo "采集失败：检查仓库名、网络或 fixtures 路径" ;;
  130) echo "被中断" ;;
  *) echo "未知退出码" ;;
esac
```

在 GitHub Actions 里你甚至不需要显式判断——**shell 步骤的退出码就是 job 的成败**。

---

## 5. CLI 全貌

`__main__.py` 的 docstring 已经把四个子命令的职责写清楚了：

| 子命令 | 做什么 | 产出 | 退出码 |
| --- | --- | --- | --- |
| `snapshot` | 只做采集，把固定快照落盘 | `<slug>.snapshot.json` | `0` / `3` / `130` |
| `check` | 采集 + 三级自动质检 + 单轮人工质检 + 报告 | snapshot + report.json + report.md（+ junit.xml） | `0` / `1` / `2` / `3` / `130` |
| `init-review` | 生成人工质检模板 JSON | 指定路径的 JSON | `0` |
| `list-checks` | 打印当前 rubric 的全部检查项（自文档化） | stdout | `0` |

`init-review` 和 `list-checks` **没有走 `add_common()`**：`init-review` 只有 `--repo`（必填）/ `--reviewer`（默认 `""`）/ `--out`（默认 `review.json`），`list-checks` 一个参数都没有。

### 5.1 公共参数（`snapshot` 与 `check` 共有）

| 参数 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--repo` | ✅ | — | 仓库标识，形如 `owner/repo` |
| `--ref` | ❌ | `None` | 要固定的分支 / 标签 / commit sha（默认用默认分支） |
| `--milestone` | ❌ | `None` | 目标 Milestone 编号或标题 |
| `--source` | ❌ | `"api"` | 数据源：`api`（在线）或 `fixtures`（离线事实包） |
| `--fixtures` | ❌ | `None` | fixtures 目录（`source=fixtures` 时必需） |
| `--out` | ❌ | `"out"` | 输出目录（默认 `out`） |
| `--max-pages` | ❌ | `5` | 分页上限，防止大仓库拉取过久 |
| `--no-archive` | ❌ | `False` | 跳过 tarball 归档下载 |

两个便利行为值得记住。其一，`--fixtures` 的默认值是 `args.fixtures or ("fixtures" if args.source == "fixtures" else None)`——用 `--source fixtures` 时不给 `--fixtures`，它会**默认去找当前目录下的 `fixtures/`**。其二，`--milestone` 同时支持编号和标题匹配，所以 `--milestone 7` 和 `--milestone "v2.0 响应式布局引擎"` 都能定位到同一个里程碑；**写了但快照里不存在时不会报错**——`MS-02 目标 Milestone 可定位` 会判不通过，报告里能看到。

### 5.2 `check` 的专属参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--review` | `None` | 人工质检结论 JSON；**省略则最终判定为 `REVIEW`** |
| `--junit` | `False` | 额外输出 JUnit XML（供 CI 使用） |
| `--quiet` | `False` | 仅输出摘要行 |

**`--quiet`** 时不打印完整 Markdown，只 `print(render_summary_line(result))`；否则会打印 `render_markdown(result, with_details=False)` 的精简报告加两行产物路径。摘要行格式是 `[PASS] acme/widget-toolkit | 自动 PASS | 综合 96.14/100`——同时含**最终判定**（方括号里）和**自动判定**（`自动 XXX`），一眼看出两者是否分叉。

**`--junit`** 写 XML 文件，并且只在**非 quiet** 模式下打印它的路径。这说明 `--quiet` 和 `--junit` 是**可以且推荐一起用**的：CI 里既想要 XML 产物，又不想让日志被整份报告淹没。

### 5.3 `list-checks`：把 rubric 当文档读

`python -m ghsqa list-checks` 不需要任何参数，先打印 rubric 的名称、版本与两个阈值，再按闸门分组列出检查项，每项一行：

```
rubric: hello-repoqa/default v1.0.0 (pass>=75, review>=60)

[ADM]
  ADM-01    仓库公开且可访问             weight=1.0 HARD pass_at=0.50
            └─ 无法访问或已被禁用的仓库不具备采集前提。
  ADM-02    仓库未归档                   weight=1.0 HARD pass_at=0.50
            └─ 归档仓库停止演进，无法再产出真实的开发里程碑，任务会退化为练习题。
```

也就是每个检查项的 ID、标题、权重、是否 HARD、`pass_at` 和 `rationale` 全都有。**这是排查「为什么这项扣分了」的第一站**——比翻源码快得多。

---

## 6. GitHub Actions 集成

先把几个决策点想清楚：PR 场景用 `fixtures`（离线、快、无网络抖动、结果确定）；PR 里要跑回归（改 rubric 后必须验证判定路径没坏）；**靠退出码控制 job 成败**（`0` 通过，`1`/`2`/`3` 失败，`REVIEW` 也算失败）；JUnit 用于上传和渲染检查项明细；日志用 `--quiet` 收敛。下面是一份完整可用的 workflow：

```yaml
name: repoqa-check

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

concurrency:                        # 同一分支只保留最新一次运行
  group: repoqa-${{ github.ref }}
  cancel-in-progress: true

jobs:
  check:
    runs-on: ubuntu-latest
    env:
      TARGET_REPO: acme/widget-toolkit
      TARGET_MILESTONE: "7"
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
```

逐步解读。**Checkout + Setup Python** 是标准两步；项目本身无第三方依赖（`collector.py` 用的是标准库 `urllib`），所以**不需要 `pip install`**。**Configure UTF-8** 是实战经验：报告里有大量中文，本地 Windows 上我们已经遇到 `UnicodeEncodeError: 'gbk' codec can't encode character`——测试脚本里 `print("  ✓ %s")` 直接就炸了，设 `PYTHONIOENCODING=utf-8` 可以避免。

**Run regression tests** 跑 `python -m tests.run_tests`，不需要 pytest、不需要网络。这一步的价值在于：如果你改了 `rubric.py` 的权重或 `manual.py` 的合并规则，58 项断言会立刻告诉你判定路径是否被改坏。**先跑回归、再跑质检**的顺序很重要——回归失败时就没必要浪费时间去采集了。

**Run repoqa check** 是核心步骤，注意四个选择：`--source fixtures --fixtures ../fixtures` 保证离线确定（`working-directory: code`，所以 fixtures 在 `../fixtures`）；`--junit` 产出 `out/*.junit.xml`；`--quiet` 只输出一行摘要——不加的话整份 Markdown 会刷满日志，把真正的错误信息淹没；`--out ../out` 写到仓库根下的 `out/`，方便下一步上传。这一步**没有 `continue-on-error: true`**，这是有意的：退出码非零会让 job 失败，这就是门禁本身。

**Upload reports** 用 `if: always()`，因为质检失败时你**最需要**看报告。**Publish test report** 把 JUnit XML 渲染成 PR 注释，`include_passed: true` 让通过的检查项也显示出来。若想失败时再补一条人话解释，可以加一个 `if: failure()` 的步骤，把「1=REVIEW(需人工介入)，2=REJECT，3=采集失败」直接 echo 到日志。

### 6.1 PR 场景下 `--quiet` 与 `--junit` 的配合

两者分工明确：`--junit` 生成结构化结果，因为 PR 界面需要检查项级别的展开/折叠视图（Markdown 做不到按检查项折叠）；`--quiet` 抑制冗长 stdout，因为 PR 的 job 日志会被反复打开，一行摘要足够定位问题，明细去 artifact 或 JUnit 注释里看。

不加 `--quiet` 的话，日志里会有完整的 Markdown（`with_details=False` 版本，仍含总表和人工质检节），再叠加 JUnit 注释，信息就重复了。**关于 `--junit` 的重要提醒**（呼应 §1.1）：JUnit 的 `failures` 只统计检查项，不反映最终判定。PR 里可能出现「JUnit 全绿但 job 红」——那是因为所有检查项都通过了，只是**缺人工签字**，最终判定是 `REVIEW`（退出码 `1`）。正确处理是：看 job 日志里的摘要行，`[REVIEW]` 就是它；去 artifact 里下 `report.md`，看「五、单轮人工质检」；如果确实应该放行，走 `init-review` → 填写 → 用 `--review` 重跑。

### 6.2 如果要在 CI 里走在线采集

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
            --junit --quiet --out ../out
```

三个要点：**`GITHUB_TOKEN`**——`GitHubClient` 会依次查 `GITHUB_TOKEN` 和 `GH_TOKEN`，有 token 才能提升 API 配额；**`--ref "${{ github.sha }}"`**——显式把快照钉在不可变 commit 上，不指定的话默认解析默认分支，`ADM-03` 只会拿 0.4 的部分分并提示「快照仅钉住可变 ref」；**在线采集受网络影响**，`CollectError` 会返回退出码 `3`，如果网络抖动频繁建议对 `3` 做重试，而不要重试 `1`/`2`（那两种情况重试也是一样的结论）。

---

## 7. 本地离线复现整条流水线

CI 里能跑通的东西，本地必须能一行不改地跑通。这也是为什么项目内置了 fixtures——`__main__.py` 的 epilog 第一个示例就是离线演示。前置条件：Python 3.8+（项目实测环境 3.8.6，CI 示例用 3.11）；**无第三方依赖**，不需要 `pip install`；不需要网络。

假设当前目录是 `hello-repoqa/`，代码在 `code/`：

```bash
cd code

# 1) 回归测试（确认环境正常），期望结尾「全部通过：58 项断言」
python -m tests.run_tests

# 2) 看一眼 rubric 全貌（可选，但建议）
python -m ghsqa list-checks

# 3) 只采集快照 → 退出码 0（snapshot 不做判定）
python -m ghsqa snapshot \
    --repo acme/widget-toolkit --source fixtures --fixtures ../fixtures \
    --milestone 7 --out ../out/demo

# 4) 完整质检，不带人工结论 → 退出码 1（REVIEW），产出四个文件
python -m ghsqa check \
    --repo acme/widget-toolkit --source fixtures --fixtures ../fixtures \
    --milestone 7 --junit --out ../out/demo

# 5) 生成人工质检模板
python -m ghsqa init-review \
    --repo acme/widget-toolkit --reviewer "Your Name" --out ../out/demo/review.json

# 6) 把 5 条 verdict 改成 accept、overall 改成 approve，然后重跑 → 退出码 0（PASS）
python -m ghsqa check \
    --repo acme/widget-toolkit --source fixtures --fixtures ../fixtures \
    --milestone 7 --review ../out/demo/review.json --junit --quiet --out ../out/demo
```

第三步的输出是 `快照已保存：../out/demo/acme__widget-toolkit.snapshot.json`，之后跟一行 `[REVIEW] acme/widget-toolkit | 自动 PASS | 综合 96.14/100`。注意摘要行显示 `[REVIEW]` 但退出码是 `0`——因为 `snapshot` 不做判定（见 §4.1 第三条）。第六步输出 `[PASS] acme/widget-toolkit | 自动 PASS | 综合 96.14/100`。想反复验证的话，把上面六步串成一个脚本即可；注意用 `|| exit 1` 只挡第一步的回归测试，质检步骤刻意**不挡**——因为我们要观察它的退出码。

### 7.1 三个 fixture 的用途

| fixture | 自动结论 | 演示的路径 |
| --- | --- | --- |
| `acme/widget-toolkit` | `PASS` | 常规工程仓库，全绿路径；含 `MS-03` 硬性项未满分触发复核队列 |
| `datawhalechina/hello-agents` | `PASS` | 教程型仓库；`ADM-04` 因 `NOASSERTION` 只得 0.60，进复核队列 |
| `legacycorp/abandoned-metrics-lib` | `REJECT` | 归档仓库被 `ADM-02` 硬性阻断，`REPO`/`MS` 被跳过 |

把第四步里的 `--repo` 换成这三个，就能把三条主要路径都走一遍。特别是第三个，能让你直接看到「⏭ 跳过」在报告里长什么样，以及退出码 `2`。

---

## 8. 如何阅读一份报告

假设你拿到一份最终判定是 `REVIEW` 的报告。人的注意力有限，六节不该按顺序读，推荐这个顺序：

```
一、总览  →  六、复核项  →  四、自动质检明细  →  五、人工质检  →  二、三（按需）
```

**第一步：一、总览——先确认「分叉」在哪。** 只看两行：

| 自动 | 最终 | 说明 | 你的动作 |
| --- | --- | --- | --- |
| `PASS` | `PASS` | 人已签字，无异议 | 一般无需再看 |
| `PASS` | `REVIEW` | **典型情况**：机器觉得没问题，但没人签字 | 去第五节，开始评审 |
| `PASS` | `REJECT` | 人工否决了，或存在硬性阻断 | 看第五节的人工状态和阻断原因 |
| `REJECT` | `REJECT` | 机器和人都说不行 | 看第五节确认是否有硬性阻断 |

同时扫一眼 `固定版本 (commit)` 和 `归档 SHA256`——确认评的是**正确的那份代码**。这一眼能挡住很多低级错误。

**第二步：六、需人工重点复核项——机器在纠结什么。** 这一节（如果存在）直接告诉你机器拿不准的具体检查项，每条都有「触发原因」和「建议动作」。这是你的**工作清单**——重点关注这些，其余的检查项既然没进队列，说明机器判得比较有把握。特别留意触发原因是「硬性检查项未满分，需确认是否可放行」的条目，这些是第四章讲过的、需要你为一票否决项背书的点。

**第三步：四、三级自动质检明细——核查「实测」列。** 对复核队列里提到的检查项，翻到对应闸门的明细表看「实测」列。比如 `ADM-04` 得 0.60 时显示「仓库含 LICENSE 文件但 GitHub 未识别出标准 SPDX 标识」，你立刻知道这是 `has_license_file` 那条分支给的（第四章讲过：`NOASSERTION` + 有 LICENSE 文件 = 0.60 部分分），于是 H-01 就有了具体切入点。**不要只看分数，一定要看 detail。**

**第四步：五、单轮人工质检——逐条回答 H-01 ~ H-05。** 未提交时这一节会列出 5 个问题及其 `why`。对照报告里的证据：

| 问题 | 从报告哪里找证据 |
| --- | --- |
| H-01 许可证 | 「二、仓库事实」的「许可证」行 + `ADM-04` 的 detail |
| H-02 目录结构 | 「二」的「文件数」「目录最大深度」+ `REPO-02` 的 detail |
| H-03 隐含前提 | 「三、Milestone 事实」的标题 + `MS-03` 的 detail（描述长度、任务数） |
| H-04 可复现性 | `MS-05` 的 detail + 「二」里的「CI / 依赖清单 / 测试目录」行 |
| H-05 快照完整性 | **报告里没有直接证据**——必须去仓库实地看 submodule / LFS / 私有依赖 |

H-05 是唯一一条必须离开报告去看仓库本身的问题，这也是它最有价值的地方。**第五步：二、三——按需回查**，比如 `--milestone 7` 是不是个好选择，就看第三节里其他里程碑的任务数。

### 8.1 怎么决定 accept 还是 reject

对每条问题，问自己一个问题：

| 问题 | `accept` 的条件 | `reject` / `followup` 的条件 |
| --- | --- | --- |
| H-01 | 许可证标准且宽松（MIT / Apache-2.0 / BSD），且确认无附加条款、无第三方素材 | 许可证无法断言、有附加限制、或确认混入第三方受保护素材 |
| H-02 | 结构清晰，无大量生成/二进制产物干扰任务切分 | 生成产物占主导，任务边界被稀释 → `reject`；不确定 → `followup` |
| H-03 | 里程碑描述交代了环境、数据、外部依赖等执行前提 | 关键前提缺失、任务无法直接开工 → `reject` |
| H-04 | 能指出一条第三方可独立走通的复现路径（含依赖获取方式） | 验收依赖内网资源或未文档化的隐性知识 → `followup` |
| H-05 | 确认无 submodule / LFS / 私有依赖，或已知且不影响 | 关键资产不在快照内 → `reject`；待确认清单 → `followup` |

然后按这个规则决定 `overall`：5 条全 `accept` → `overall = approve`；有 1 条以上 `reject` → `overall = reject`（或 `rework`）；有 `followup` 但无 `reject` → `overall = pending`，留在 `REVIEW` 等补充。

**特别提醒**（第四章讲过的坑）：`followup` 会让最终判定停在 `REVIEW`，而单条 `reject` **不会**——它必须靠 `overall` 生效。所以判了 `reject`，一定要把 `overall` 也设成 `reject` / `rework`。

### 8.2 三种典型场景的处理

**场景 A：自动 PASS + 最终 REVIEW + 复核队列只有一条「硬性项未满分」。** 最常见的形态（`acme/widget-toolkit` 就是这样）。看那条硬性项的 detail，确认缺失的分数是否无关紧要。如果无关紧要，5 条全 `accept` + `overall=approve`，重跑即得 `PASS`。

**场景 B：报告里有「🚫 阻断原因：存在硬性阻断且未被显式豁免」。** 不要试图在 `review.json` 里把它改掉——第四章讲过，实现里**没有豁免字段**，任何 `approve` 都救不回来。去看「四」的总表里「硬性阻断」列非 `—` 的行，回到上游修数据。

**场景 C：JUnit 全绿但 CI job 红。** 99% 是缺少人工签字。看日志摘要行是不是 `[REVIEW]`，然后走 `init-review` → 填写 → `--review` 重跑（见 §6.1）。

---

## 本章小结

- **两种报告格式各司其职**：Markdown（`render_markdown()`）面向人工评审与 PR 留档，JUnit XML（`render_junit()`）面向 CI 渲染。**关键区别**：JUnit 的 `failures` 只统计检查项是否通过，**完全不反映最终判定**——JUnit 全绿而退出码为 `1` 是完全正常的组合。
- **Markdown 六个章节**：一、总览（结论卡片，自动与最终判定分列两行）；二、仓库事实；三、Milestone 事实；四、三级自动质检（总表 + 各闸门明细，✅/❌ 与 🔒 标记）；五、单轮人工质检（已提交表格 / 未提交问答清单两种形态）；六、需人工重点复核项（队列为空时不出现）。
- **三种核心产物 + 一个可选产物**：`<slug>.snapshot.json`（固定快照，可复现的中间产物）、`<slug>.report.json`（机器可读完整结论，含 `schema: "hello-repoqa/report@1"`）、`<slug>.report.md`（人读报告），加 `--junit` 时多出 `<slug>.junit.xml`。**slug 规则是 `owner__repo`**（把 `/` 替换为双下划线），与 fixtures 目录命名一致。
- **退出码约定**：`0` = PASS（人工已确认放行）；`1` = REVIEW（需人工介入）；`2` = REJECT（**同时也是 `--review` 文件读取失败**）；`3` = 采集失败（`CollectError`）；`130` = 被中断（`KeyboardInterrupt`）。注意 `snapshot` 子命令永远返回 `0`，不能用作门禁。
- **四个子命令**：`snapshot` / `check` / `init-review` / `list-checks`。公共参数包括 `--repo` `--ref` `--milestone` `--source` `--fixtures` `--out` `--max-pages` `--no-archive`，`check` 专属 `--review` `--junit` `--quiet`。`--quiet` 与 `--junit` 推荐一起用：前者抑制冗长日志，后者产出结构化结果。
- **CI 集成的核心思路**：先跑 `python -m tests.run_tests` 保证判定路径没坏，再跑 `python -m ghsqa check --junit --quiet`，**靠退出码控制 job 成败**，用 `if: always()` 上传产物确保失败时也能排查。PR 场景用 fixtures 保证离线与确定性；在线采集记得注入 `GITHUB_TOKEN` 并用 `--ref` 钉住 commit。
- **本地离线复现**：`fixtures/` 下三个仓库覆盖三条判定路径（`acme/widget-toolkit` 常规 PASS、`datawhalechina/hello-agents` 教程型 + 许可证模糊、`legacycorp/abandoned-metrics-lib` 归档阻断 + 闸门跳过），无需网络即可走完全流程。
- **读报告的顺序**：先看「一、总览」确认自动与最终判定是否分叉，再看「六、复核项」拿到机器纠结的清单，然后回到「四」核对「实测」列的具体依据，最后在「五」逐条回答 H-01 ~ H-05。决策时记住：**判了 `reject` 必须同时改 `overall`；判了 `followup` 会停在 `REVIEW` 等补材料；硬性阻断不能靠签字绕过。**
