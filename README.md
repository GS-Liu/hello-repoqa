<div align="center">

# hello-repoqa

### 🔍 GitHub 仓库固定快照与 Milestone 采集 · 三级质检工具

<p><em>把「这个仓库值不值得做成 AI Coding 任务」从主观经验，变成可自动执行、可审计、可回归的流程</em></p>

<img src="https://img.shields.io/badge/Python-3.8%2B-blue?style=flat&logo=python" alt="Python"/>
<img src="https://img.shields.io/badge/dependencies-0-brightgreen?style=flat" alt="Zero Dependencies"/>
<img src="https://img.shields.io/badge/tests-58%20passed-success?style=flat" alt="Tests"/>
<img src="https://img.shields.io/badge/rubric-21%20checks-orange?style=flat" alt="Rubric"/>
<img src="https://img.shields.io/badge/license-MIT-green?style=flat" alt="License"/>

</div>

---

## 🎯 项目介绍

&emsp;&emsp;为 AI Coding 构建任务数据集时，真正昂贵的不是写代码，而是**判断**：这个仓库值不值得采集？这个 Milestone 能不能拆成边界清晰、可验收的任务？绝大多数团队靠人工翻仓库、凭经验拍板，结果是口径不一致、结论不可复现、而且很容易踩坑——归档仓库的里程碑并不反映真实开发节奏，许可证为 `NONE` 的仓库默认保留全部权利，一个 Milestone 下可能一个任务都没有。

&emsp;&emsp;`hello-repoqa` 把这些判断固化下来。它做三件事：

1. **采集固定快照** —— 把仓库钉死在不可变的 commit sha 上，连同 Milestone 与任务明细一起落盘，附带 tarball 的 SHA256 字节级凭据；
2. **执行三级自动质检** —— 准入 → Repo 质量 → Milestone 质量，共 **21 个检查项**，其中 4 项具备**一票否决**能力；
3. **进入单轮人工质检** —— 机器判不准的部分（法务、领域、价值判断）交给人工做**一次**终审，并强制留痕。

&emsp;&emsp;整个项目**只依赖 Python 标准库**，可以完全离线运行。

---

## ✨ 核心特性

| 特性 | 说明 |
| --- | --- |
| 🔒 **固定快照** | 钉在 commit sha 而非分支名；记录 tarball SHA256，可复现、可审计 |
| 🧩 **事实层与规则层解耦** | 采集器只产出事实，规则只读事实 → 同一套规则同时跑在线 API 与离线 fixtures |
| 📋 **声明式 rubric** | 规则是数据不是 `if/else`：稳定 ID、可调权重、可回归测试 |
| 🚦 **硬性阻断机制** | `ADM-01/02`、`MS-01/03` 一票否决，无论总分多高 |
| ⏭️ **阻断型闸门** | 准入不通过则跳过后续闸门，不浪费质检预算 |
| 👤 **单轮人工质检** | 5 个固定问题对应机器的能力边界；未提交人工结论**不得放行** |
| 📊 **双格式报告** | Markdown 供人读与 PR 留档，JUnit XML 供 CI 直接渲染 |
| 🔌 **零依赖 + 离线可跑** | 纯标准库；内置三个 fixtures 场景，断网也能完整演示与回归 |

---

## 📚 文档导航

| 章节 | 关键内容 | 状态 |
| --- | --- | --- |
| [前言](./docs/前言.md) | 项目缘起、目标读者、环境要求与快速开始 | ✅ |
| [第一章 为什么需要仓库质检](./docs/chapter1/第一章%20为什么需要仓库质检.md) | 真实痛点、整体架构、三级闸门与数据流 | ✅ |
| [第二章 固定快照采集原理](./docs/chapter2/第二章%20固定快照采集原理.md) | commit 锚点、双数据源、结构信号推导、归档校验 | ✅ |
| [第三章 三级自动质检设计](./docs/chapter3/第三章%20三级自动质检设计.md) | 声明式 rubric、加权打分、21 个检查项逐条解析 | ✅ |
| [第四章 单轮人工质检](./docs/chapter4/第四章%20单轮人工质检.md) | 能力边界、复核队列、结论合并规则、审计留痕 | ✅ |
| [第五章 报告与 CI 集成](./docs/chapter5/第五章%20报告与%20CI%20集成.md) | 报告结构、退出码约定、GitHub Actions 实战 | ✅ |
| [第六章 扩展与二次开发](./docs/chapter6/第六章%20扩展与二次开发.md) | 自定义规则、接入新数据源、扩充 fixtures | ✅ |

---

## 🚀 快速开始

### 环境要求

&emsp;&emsp;只需要 **Python 3.8+**。没有 `pip install` 这一步——项目零第三方依赖。

```bash
git clone https://github.com/<your-name>/hello-repoqa.git
cd hello-repoqa/code
```

### 5 分钟跑通（离线，无需网络）

&emsp;&emsp;使用内置 fixtures 跑完整流水线：

```bash
python -m ghsqa check \
  --repo datawhalechina/hello-agents \
  --source fixtures --fixtures ../fixtures \
  --milestone 3 \
  --out ../out/demo
```

&emsp;&emsp;你会得到一份完整报告，结论是 **🟡 REVIEW**——自动质检已经给了 PASS，但因为还没提交人工结论，流程**有意**不放行：

| 闸门 | 得分 | 判定 |
| --- | --- | --- |
| 准入质检 (ADM) | 93.33 | ✅ PASS |
| Repo 质量质检 (REPO) | 97.33 | ✅ PASS |
| Milestone 质量质检 (MS) | 93.24 | ✅ PASS |
| **综合** | **94.90 / 100** | **🟡 REVIEW（待人工）** |

### 在线采集真实仓库

```bash
# 强烈建议：把快照钉在具体的 commit sha 上
export GITHUB_TOKEN=<your_token>   # 可选，用于提升 API 配额
python -m ghsqa snapshot --repo psf/requests --ref <commit-sha> --out ../out/requests
```

### 完成人工质检

```bash
# 1) 生成待填写模板
python -m ghsqa init-review --repo acme/widget-toolkit --reviewer "Your Name" --out out/review.json

# 2) 填写 decisions[].verdict 与 overall 后，带结论重跑
python -m ghsqa check --repo acme/widget-toolkit \
  --source fixtures --fixtures ../fixtures --milestone 7 \
  --review out/review.json --out out
```

---

## 🧪 运行测试

&emsp;&emsp;回归测试同样零依赖，也无需网络：

```bash
cd code
python -m tests.run_tests
```

&emsp;&emsp;覆盖 **58 项断言**，包括 rubric 自检、事实推导、四条判定路径（通过 / 归档阻断 / 无 Milestone 拦截 / 指定里程碑不存在）、人工质检语义、报告渲染与 CLI 退出码：

```
全部通过：58 项断言
```

---

## 📦 内置 fixtures 场景

&emsp;&emsp;三个 fixtures 既是演示样例，也是回归用例，覆盖不同的判定路径：

| 场景 | 类型 | 预期判定 | 验证的规则 |
| --- | --- | --- | --- |
| `datawhalechina/hello-agents` | 教程型仓库 | ✅ PASS（待人工） | 教程型豁免通道、活跃度、任务边界 |
| `acme/widget-toolkit` | 常规工程仓库 | ✅ PASS（待人工） | 测试护栏、CI、协作痕迹 |
| `legacycorp/abandoned-metrics-lib` | 已归档仓库 | ❌ REJECT | `ADM-02` 硬性阻断 + 跳过后续闸门 |

---

## 🏗️ 架构与数据流

```
                    ┌──────────────────────────────────────┐
                    │  collector  采集层                    │
                    │  api ──┐                             │
                    │        ├──▶ Snapshot（事实层，不可变） │
                    │  fixtures ──┘                        │
                    └────────────────┬─────────────────────┘
                                     │  只产出事实
                                     ▼
                    ┌──────────────────────────────────────┐
                    │  rubric + rules  规则层（只读事实）    │
                    │  ADM ──▶ REPO ──▶ MS                 │
                    │  21 checks · 加权打分 · 硬性阻断       │
                    └────────────────┬─────────────────────┘
                                     ▼
                    ┌──────────────────────────────────────┐
                    │  manual  单轮人工质检                 │
                    │  5 问裁决 · 结论合并 · 审计留痕        │
                    └────────────────┬─────────────────────┘
                                     ▼
                    ┌──────────────────────────────────────┐
                    │  report  报告渲染                     │
                    │  Markdown · JSON · JUnit XML          │
                    └──────────────────────────────────────┘
```

&emsp;&emsp;**关键设计**：`api` 与 `fixtures` 两种数据源产出**同一个** `Snapshot` 结构，因此下游的 21 条质检规则完全不需要区分数据来源——这正是规则能在 CI 里稳定回归的前提。

---

## 🚦 退出码约定

&emsp;&emsp;退出码是 CI 判定的基础，语义严格固定：

| 退出码 | 含义 | CI 建议动作 |
| --- | --- | --- |
| `0` | **PASS** —— 人工已确认放行 | 通过 |
| `1` | **REVIEW** —— 需人工介入 | 标记待审，不阻塞合并 |
| `2` | **REJECT** —— 不合格 | 失败并输出报告 |
| `3` | 采集失败（网络/权限/仓库不存在） | 重试或修正参数 |
| `130` | 被用户中断 | 忽略 |

---

## 📖 质检规则一览

&emsp;&emsp;用一条命令查看当前 rubric 的全部 21 个检查项：

```bash
python -m ghsqa list-checks
```

### 闸门一：准入（ADM）—— *阻断型，不过则跳过后续*

| ID | 检查项 | 权重 | 硬性 |
| --- | --- | --- | :---: |
| `ADM-01` | 仓库公开且可访问 | 1.0 | 🔒 |
| `ADM-02` | 仓库未归档 | 1.0 | 🔒 |
| `ADM-03` | 快照固定到不可变 commit | 1.5 | |
| `ADM-04` | 许可证明确 | 1.0 | |
| `ADM-05` | 非 fork 仓库 | 0.5 | |
| `ADM-06` | Issue 功能开启 | 0.5 | |
| `ADM-07` | 快照内容非空 | 0.5 | |

### 闸门二：Repo 质量（REPO）—— *权重 0.4*

| ID | 检查项 | 权重 | 硬性 |
| --- | --- | --- | :---: |
| `REPO-01` | 文档完备度 | 1.0 | |
| `REPO-02` | 目录结构清晰 | 1.0 | |
| `REPO-03` | 测试与回归护栏 | 1.5 | |
| `REPO-04` | 自动化与构建配置 | 1.0 | |
| `REPO-05` | 依赖与体积治理 | 0.8 | |
| `REPO-06` | 社区活跃度 | 1.2 | |
| `REPO-07` | 任务可切分性 | 1.0 | |

### 闸门三：Milestone 质量（MS）—— *权重 0.4*

| ID | 检查项 | 权重 | 硬性 |
| --- | --- | --- | :---: |
| `MS-01` | 存在 Milestone | 1.0 | 🔒 |
| `MS-02` | 目标 Milestone 可定位 | 1.0 | |
| `MS-03` | 任务边界可界定 | 1.4 | 🔒 |
| `MS-04` | 任务可验收信息 | 1.3 | |
| `MS-05` | 结果可验证 | 1.2 | |
| `MS-06` | 任务体量健康 | 0.9 | |
| `MS-07` | 协作痕迹 | 0.8 | |

> 🔒 = 硬性阻断项，未达标则**无论总分多高**直接 REJECT。

&emsp;&emsp;**为什么恰好是这 4 项？** 因为它们都是"一票否决"性质的问题：仓库不可访问、仓库已归档、根本没有 Milestone、Milestone 无法界定边界——这些情况下，后续的质量分再高也没有意义。

---

## 🔍 设计要点：两个容易做错的地方

&emsp;&emsp;**其一，教程型仓库不能用"文档占比高"来识别。** 否则带大型 `docs/` 目录的常规库（如 requests、django）会被误判成教程。正确判据是**文档文件数超过代码文件数**——内容本身就是交付物，代码只是配套示例。这类仓库走测试豁免通道，改以示例代码可运行性验收。

&emsp;&emsp;**其二，许可证的三种取值法务含义完全不同**，必须分开处理：

| GitHub 取值 | 含义 | 判定 |
| --- | --- | --- |
| 标准 SPDX（如 `MIT`） | 授权明确 | ✅ 满分 |
| `NOASSERTION` | 有许可证文件但非标准模板 | 🟡 0.6，需人工确认边界 |
| `NONE` | **根本没有许可证文件**，默认保留全部权利 | ❌ 0 分，不可采集 |

---

## 📁 项目结构

```
hello-repoqa/
├── README.md
├── LICENSE
├── CONTRIBUTING.md
├── .gitignore
├── .github/
│   └── workflows/
│       └── qa.yml                  # CI：回归测试 + 自检质检
├── docs/                           # 教程正文（Datawhale 风格）
│   ├── 前言.md
│   ├── chapter1/ ... chapter6/
│   └── _sidebar.md
├── code/
│   ├── ghsqa/
│   │   ├── __main__.py             # CLI：snapshot / check / init-review / list-checks
│   │   ├── collector.py            # 采集层：api + fixtures 双数据源
│   │   ├── models.py               # 事实层：RepoFacts / MilestoneFacts / Snapshot
│   │   ├── rubric.py               # 检索引擎：Check / Rubric / run_gate
│   │   ├── rules.py                # 21 条质检规则实现
│   │   ├── manual.py               # 单轮人工质检
│   │   ├── pipeline.py             # 流水线编排
│   │   ├── report.py               # Markdown / JUnit 渲染
│   │   └── util.py
│   ├── tests/
│   │   └── run_tests.py            # 58 项断言的零依赖回归测试
│   └── examples/
│       └── quickstart.py
└── fixtures/                       # 离线事实包（3 个场景）
    ├── datawhalechina__hello-agents/
    ├── acme__widget-toolkit/
    └── legacycorp__abandoned-metrics-lib/
```

---

## 🤝 如何贡献

&emsp;&emsp;欢迎任何形式的贡献：

- 🐛 **报告问题** —— 提交 Issue 说明规则误判或采集异常
- 📐 **贡献规则** —— 新增 `Check` 并附上判定依据与回归用例
- 📦 **补充 fixtures** —— 提供新的场景覆盖（尤其是边界情况）
- 📝 **完善文档** —— 改进教程与示例

&emsp;&emsp;提交前请确保 `python -m tests.run_tests` 全部通过。详见 [CONTRIBUTING.md](./CONTRIBUTING.md)。

---

## 📜 开源协议

&emsp;&emsp;本项目采用 [MIT License](./LICENSE) 开源。

---

<div align="center">
  <p>⭐ 如果这个项目对你有帮助，请给它一个 Star！</p>
</div>
