# AI Comic Studio

> **把小说自动改编成漫画的可复用开源方案。**
> 核心：定义一个受 Schema 约束的 **Comic IR**（漫画中间表示），
> 让 LLM 只负责创作决策、确定性引擎负责渲染 —— 从而解决 AI 生成漫画的
> **一致性、可编辑性与可复现性** 三大难题。

```
小说文本 ──[AI 改漫 Agent]──▶ Comic IR ──[渲染引擎]──▶ 漫画 PDF
                                 ▲
                                 │
                         [可视化工作台] 人工微调
```

---

## 为什么需要它

### 直接让 AI 画漫画，有三个必死问题

| 问题 | 现象 |
|---|---|
| **一致性崩溃** | 同一角色第 3 格换发色、第 8 格换衣服 |
| **不可控** | 想改一句台词，要重出整页图 |
| **不可复现** | 同样输入两次，结果完全不同 |

### 我们的解法：把「生成」拆成「决策」与「渲染」

**类比 Text-to-SQL**：
- 不让 LLM 直接查数据库，而是让它产出 **SQL**（受 schema 约束的中间层）
- 不让 LLM 直接画漫画，而是让它产出 **Comic IR**（受 schema 约束的中间层）

**收益**：

- ✅ **可校验** —— IR 有 12 条业务规则，渲染前就能发现 90% 的错误
- ✅ **可修复** —— 校验失败自动把结构化错误回喂给 LLM（Self-Repair）
- ✅ **可编辑** —— 改 IR 一句话 → 3 秒重渲染，不用重出图
- ✅ **可复现** —— 同 IR + 同素材 → 同输出

---

## Comic IR：四个文件定义一本漫画

```
project/
├── bible.json       角色 / 场景 / 技能 / 风格（一致性的唯一来源）
├── storyboard.json  每格画什么
├── dialogue.json    谁说哪句、气泡放哪（★ 与画面分离）
└── layout.json      页序与分卷（★ 页码冻结，永不重排）
```

### 关键设计

| 设计 | 为什么 |
|---|---|
| **引用而非内联** | `cast` 只写 `character_id`，外观从 bible 取 → 不可能出现「这格换了衣服」 |
| **状态外置** | 角色伤情/服装在 bible 里跨格追踪 → 长程一致性有据可依 |
| **可追溯** | 对白带 `source_span` 指回原文行号 → 校验器能抓出 LLM 编造的台词 |
| **页码冻结** | 删除某格只标 `empty`，其余页码不动 → 协作时「第 78 页」永远指同一页 |
| **锁定机制** | 人工调过的气泡 `locked: true` → 自动布局不再覆盖 |

---

## 12 条业务规则（Validator）

JSON Schema 只能挡格式错误，挡不住「格式正确但语义错误」。
这 12 条规则专门针对 AI 改漫最容易犯的错：

| 规则 | 内容 | 为什么重要 |
|---|---|---|
| **IR-001** | 引用完整性：cast/skill/background 必须存在于 bible | 否则出图会换人 |
| **IR-002** | 两人以上同框必须写明具体距离 | 否则模型把两人画得像在聊天 |
| **IR-003** | 同一角色相邻两格不得同表情 | 否则全程一个表情 |
| **IR-004** | 主角损伤等级不得无故回落 | 否则伤口自愈 |
| **IR-005** | 伤情必须闭环（有起始镜头/愈合镜头） | 否则连续性断裂 |
| **IR-006** | 对白引用合法且说话人已确认 | 防止张冠李戴 |
| **IR-007** | 对白必须能在原文里逐字找到 | ★ 防 LLM 编造台词 |
| **IR-008** | 气泡与箭头坐标不得重合或越界 | 否则指向错误 |
| **IR-009** | 页序引用的镜头必须存在且不重复 | 防错位 |
| **IR-010** | 每个镜头都必须被排进页面 | 防漏排 |
| **IR-011** | 有对白的镜头说话人应在场（否则须声明画外音） | 防指向空气 |
| **IR-012** | 避免连续 ≥4 格同景别 | 防节奏呆板 |

```bash
python -m packages.cli rules      # 查看全部规则
```

---

## Self-Repair：让 Agent 错了能改

不指望 LLM 一次做对，而是：

```
生成 ──▶ 校验 ──失败──▶ 把「结构化错误 + 修正建议」回喂 ──▶ 重产
          │                                                      │
          └──────────────通过──────────────◀──────────────────────┘
                          │
                    仍失败 → 标记 pending_human（交人工，不硬渲染）
```

**实测**（`examples/minimal/run_mock.py`）：

```
[repair] 第 1 轮失败：1 个错误 ['IR-002']     ← 两人同框没写距离
[repair] 第 2 轮通过
```

---

## 快速开始

### 离线演示（不需要 API key）

```bash
git clone <repo> && cd ai-comic-studio
pip install -e .

python examples/minimal/run_mock.py
```

输出：

```
▶ 处理第 2436 章《冰晶刹弓》（191 字）
   [repair] 第 1 轮失败：1 个错误 ['IR-002']
   [repair] 第 2 轮通过
   分镜：1 章 / 2 格   对白：2 条   页数：4
   项目级校验：校验通过
```

### 真实使用

```bash
# 1. 准备 bible.json（角色/场景/风格的标准块）
# 2. 跑 Agent：小说 → Comic IR
export DEEPSEEK_API_KEY=sk-...
python -m packages.cli adapt 原文/2436.txt \
    --bible examples/minimal/bible.json \
    --out project/ --chapters 1 --panels 10

# 3. 校验
python -m packages.cli validate project/ --source 原文/2436.txt
```

### 支持任意 OpenAI 兼容服务

```bash
python -m packages.cli providers
#   openai / deepseek / moonshot / dashscope / ollama / mock

# 本地模型也行
python -m packages.cli adapt 原文.txt --bible bible.json --out out/ \
    --provider ollama --base-url http://localhost:11434/v1
```

---

## 项目结构

```
ai-comic-studio/
├── packages/
│   ├── ir/                  ★ Comic IR 数据模型 + 12 条规则校验
│   │   ├── models.py            Pydantic v2 模型
│   │   └── validator.py         业务规则引擎
│   ├── agent/               ★ AI 改漫 Agent
│   │   ├── providers.py         LLM 适配器（可插拔）
│   │   ├── prompts.py           提示词模板（规则前置 + 引用式）
│   │   ├── skills.py            5 个技能 + 自修复循环
│   │   └── pipeline.py          端到端编排
│   └── cli.py               命令行
├── tests/                   51 个测试
├── examples/minimal/        最小可运行示例
├── docs/
│   ├── design.md            完整方案与架构
│   └── roadmap.md           路线图
└── scripts/
```

---

## 技术亮点（面试可深入）

1. **受约束生成** —— LLM 输出受 JSON Schema + 12 条业务规则双重约束
2. **自修复循环** —— 校验失败自动回喂结构化错误，而非直接失败
3. **跨格状态追踪** —— 角色伤情/服装/情绪在 bible 里持续演进
4. **纯函数渲染** —— 确定性、可缓存、可 Golden 测试
5. **可插拔架构** —— LLM 与生图服务都是适配器，不锁定厂商
6. **页码冻结机制** —— 从「删一格导致全书错位」的实战教训中来

---

## 测试

```bash
pytest -q
# 51 passed
```

覆盖：
- 12 条规则各自的触发条件（每条都有反例测试）
- 自修复循环：一轮修复 / 多轮耗尽 / 不可解析输出 / 错误回喂验证
- 端到端 pipeline：布局生成、页码冻结、pending 降级
- 状态追踪：新增伤情 / 去重 / 愈合

---

## 实战验证

本项目源于一次真实产出：**《全职法师》2426–2445 章改编，20 章 / 168 页 / 216 格**。
那次项目用散落脚本 + 人工校验完成，踩了五个结构性坑（对白烧进图、页码中途变动、
四层索引、改动只在图里、无阶段自检）—— **本项目的设计正是这些坑的解法**。

---

## 路线图

- [x] **Phase 1 · IR 层** —— 数据模型 + 12 条规则 + 51 测试
- [x] **Phase 2 · Agent 层** —— Provider 抽象 + 提示词 + 5 技能 + 自修复 + CLI
- [ ] **Phase 3 · 渲染层** —— 提示词组装 + 生图适配 + 气泡算法 + PDF 导出
- [ ] **Phase 4 · 工作台** —— React 可视化编辑（拖气泡 / 调箭头 / 一键重渲染）
- [ ] **Phase 5 · 部署** —— Docker Compose + 在线 Demo

详见 [docs/roadmap.md](docs/roadmap.md)

---

## License

MIT
