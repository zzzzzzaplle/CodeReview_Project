# CodeReview_Project｜下一对话交接文档

> 用途：在新对话中继续推进“实习经历包装 + OpenClaw 自动代码审查 Agent 原型还原 + 求职准备”。
> 当前目标不是做学术研究，而是尽快把这段经历整理成能用于 **AI Agent / 智能体开发实习** 投递和面试的完整项目故事。

---

## 1. 求职目标

- **下一段实习方向**：AI Agent / 智能体开发
- **开始投递时间**：近期立即投递，不等项目全部完成
- **项目推进方式**：还原实习细节 + 做可运行 Demo，用真实实现和结果补强 STAR
- **最终希望形成的叙事**：

> 我发现问题 → 分析原因 → 实际搭东西验证 → 得到结果和局限

---

## 2. 实习背景与时间线

- 时间：**2026 年 1 月～2 月左右**
- 用户当时位于公司 **软件质量组**
- 带教老师 / mentor 是公司 **代码质量方向的牵头人**
- 公司内部已经存在：
  - CI/CD 流水线上的自动 AI Code Review
  - GitLab 代码提交流程中的 AI Review
  - 开发侧使用通义灵码等 AI 编码工具
  - 内部还有基于 DeepSeek-R1 + Continue 的代码/稳定性 Review 方案

当时 mentor 提出的核心困境：

> 现有 AI Code Review 能发现的问题比较表层，希望进一步探索 AI 是否能够发现更深层次的问题。

---

## 3. 企业内部资料已经确认的事实

目前已提供两份企业内部文档：

### 文档 A
**《代码编程和代码review稳定性问题发现和修复AI小助手》**

已确认内容：

- 内部部署 DeepSeek-R1，用于：
  - 代码/单测生成
  - 稳定性问题发现和修复
  - 代码质量评审和拦截
- Review Prompt 重点关注：
  - crash
  - memory leak
  - index out of range
  - recursive call
  - null pointer 等
- 使用 Likelihood / Impact / Handle Result 做 1～5 级风险评分
- 历史稳定性问题回归中记录过：
  - **15 / 18**
  - Recall ≈ **83%**
- 流水线思路：
  - 大模型 Review 报出高风险问题
  - 再结合少量人工确认

注意：
**15/18、83% 是企业内部已有方案的历史测试结果，不应直接说成用户个人实验结果。**

---

### 文档 B
**《CodeReview AI赋能》**

这份文档对项目定位非常关键。

文档将 Code Review 问题分为：

1. 浅层问题：语法、代码风格
2. 潜在问题：运行时数据风险
3. 深层问题：业务逻辑合理性
4. 结构问题：架构改动合理性

文档明确认为：

- 越往后问题价值越高
- 发现难度也越高
- 当时 AI 更擅长前两类
- 如果能获取足够上下文、积累更多 Bug 案例，未来可能逐步处理深层和结构问题

同时文档还确认：

- 在线 AI Review 主要看到代码变更切片
- 本地/智能体评审可以看到更多项目上下文
- Prompt 已经要求关注：
  - 本次变更
  - 受影响调用链
  - 测试
  - 配置
  - 并发
  - 事务
  - 数据一致性
  - API 约定
  - 安全
  - 依赖
- 如果 VS Code 打开海量代码直接执行 Review，可能“Cover 不住”
- 因此只能限制在具体模块或目录
- 公司持续维护 AI CodeReview 案例库
- 严重漏检会被总结成新的 Prompt 规则

这些事实共同支持一个核心判断：

> 当时企业已经意识到，仅靠 diff + 固定 Prompt 很难发现深层问题；真正的瓶颈之一是如何获取并选择有效上下文。

---

## 4. 当前对实习任务的最新确认

用户已明确回忆：

> **当时 mentor 实际让用户做的原型，就是：用 OpenClaw 搭一个能够自主审查 Git Diff 的 Agent。**

并且用户确认：

- OpenClaw 本身是现成 Agent 框架 / 工具
- 安装即可使用
- 当时社区已经有人做过类似的：
  - 自动获取 / 审查 Git Diff
  - 自动进行代码审批 / Review
- 用户当时的做法更接近：
  - 找到社区已有方案 / workflow / skill
  - 直接复用
  - 再用于企业的 Git Diff Review 原型
- 用户确认当时原型属于 **A 类**：
  - 已经能读取 Git Diff
  - 已经能自动分析代码
  - 已经能输出 Review 意见

目前还没有完全找回：

- 当时具体复用了哪个社区项目 / Skill / workflow
- OpenClaw 的具体版本
- 是通过 GitHub、ClawHub、社区文章还是其他渠道找到的
- 用户具体做了哪些：
  - 配置
  - Prompt 调整
  - Tool 接入
  - Git 命令
  - workflow 修改
  - 输出格式适配
- 是否做过：
  - caller / callee 检索
  - 调用链探索
  - 测试执行
  - 历史 Bug 检索
- 原型最终的量化效果
- mentor 对结果的具体反馈
- 原型的主要局限

这些是下一步最需要还原的内容。

---

## 5. 已确认的简历项目方向

已经确认，简历先按照下面这个方向准备：

> **基于 OpenClaw 搭建自动化代码审查 Agent 原型，复用社区审查工作流，实现 Git Diff 的自动分析与 Review 输出，探索 Agent 在代码质量审查场景中的应用。**

这句话是当前的 **简历经历基准版**。

后续不再反复修改项目定位，只补：

1. 技术实现细节
2. 个人贡献边界
3. 真实结果
4. 局限

---

## 6. STAR 当前进度

### S — Situation（已比较完整）

企业已有 AI Code Review，但主要擅长：

- 语法
- 风格
- 空指针
- 越界
- 异常处理
- 一些局部稳定性问题

更难处理：

- 跨文件影响
- 调用链问题
- 状态一致性
- 业务逻辑合理性
- 架构改动合理性

---

### T — Task（已明确）

mentor 希望探索：

> 能否利用 OpenClaw 这样的 Agent，让 AI 不只是被动看 diff，而是能够自主完成 Git Diff Review，进一步辅助发现更深层次的问题。

---

### A — Action（当前最需要补）

目前只确认：

- 用户确实搭过 OpenClaw 原型
- 用户确实复用了社区已有的自动 Review 工作流
- 原型能读取 Git Diff
- 原型能自动分析并输出 Review

还需要继续还原：

- 社区方案来源
- 安装过程
- 配置方式
- Agent 使用的 tools
- Git Diff 如何获取
- Prompt / Skill 怎么组织
- 是否自动读取相关文件
- 是否调用 git status / git diff / git show / grep 等
- 是否有多轮 Agent 探索
- 如何约束输出
- 如何减少误报

---

### R — Result（尚未补全）

当前不能虚构：

- “提升 xx%”
- “上线生产”
- “被公司正式采纳”
- “Recall 提升到 xx”
- “发现 xx 个深层 Bug”

除非后续恢复出真实证据。

如果最终找不到量化数据，可以用：

- 成功跑通的功能
- Demo 结果
- 真实案例
- mentor 反馈
- 发现的工程局限

来构成 Result。

---

## 7. 当前口述稿的核心逻辑

目前已经形成的核心叙事：

> 公司已经有 AI Review，但不够深  
> ↓  
> 我把原因拆成“模型能力”和“模型视野/上下文”两个方向  
> ↓  
> 短期更可控的是提升上下文获取能力  
> ↓  
> mentor 让我用 OpenClaw 搭一个能自主 Review Git Diff 的 Agent  
> ↓  
> 我复用了社区已有审查工作流并跑通原型  
> ↓  
> 下一步需要还原具体实现、验证结果和局限

可保留的一句面试表达：

> “我当时把问题拆成两个方向：一个是模型本身不够强，另一个是模型其实根本没看到足够的信息。模型能力短时间内很难改变，所以我更关注第二个问题——怎么让 Agent 主动拿到正确的上下文。”

---

## 8. GitHub 项目

当前仓库：

**zzzzzzaplle/CodeReview_Project**

仓库中目前已经有：

- `面试口头陈述.md`

当前仓库更多是求职材料整理，还没有恢复出完整原型代码。

后续希望补：

```text
CodeReview_Project/
├── README.md
├── 面试口头陈述.md
├── docs/
│   ├── internship_background.md
│   ├── architecture.md
│   └── evaluation.md
├── demo/
│   └── ...
└── examples/
    └── ...
```

是否真的需要完整实现，取决于历史原型能恢复多少。

---

## 9. 下一对话最高优先级任务

### Priority 1：找到当时复用的社区方案

优先搜索 2026 年 1～2 月已经存在的：

- OpenClaw code review
- OpenClaw git diff review
- OpenClaw pull request review
- OpenClaw GitHub review agent
- OpenClaw code reviewer skill
- OpenClaw GitLab review
- ClawHub code review
- Git diff agent OpenClaw

重点查看：

- GitHub
- ClawHub / OpenClaw Skill 社区
- 2026 年 1～2 月文章 / issue / repo
- Reddit / X / 博客 / 技术文章（如果需要）

必须注意时间：

> 不能拿 2026 年下半年才出现的项目，反向包装成 2026 年 1～2 月就已经使用的方案。

---

### Priority 2：恢复原型技术链

希望最终能回答：

```text
OpenClaw
   ↓
读取当前 Git 仓库
   ↓
git diff / git status / commit diff
   ↓
Agent 分析变更
   ↓
必要时读取相关文件
   ↓
Review Prompt / Skill
   ↓
输出问题：
- 文件
- 行号
- Severity
- 问题原因
- 修复建议
```

如果真实原型还有调用链、测试、历史 Bug 检索，再继续加。

---

### Priority 3：准备面试追问

最终至少要能回答：

1. 为什么用 OpenClaw？
2. 为什么不用单次 LLM Prompt？
3. Agent 相比普通 AI Review 多了什么？
4. Git Diff 如何获取？
5. Agent 怎么决定读哪些文件？
6. 上下文太大怎么办？
7. 怎么控制幻觉和误报？
8. Review 输出结构是什么？
9. 如何验证一个 Review 问题是真的？
10. OpenClaw 的局限是什么？
11. 为什么这是 Agent，而不是普通脚本？
12. 如果重新做一次，你会怎么升级？

---

## 10. Demo 的原则

Demo 的目的：

> **服务求职和面试，而不是做成一个完整产品或科研项目。**

如果历史原型已经足够：

- 优先恢复原型
- 补 README
- 补可运行示例

如果历史细节恢复不完整：

- 可以重新搭一个简化版
- 用于帮助自己重新掌握实现细节
- 但要区分：
  - “实习期间做过的”
  - “为了求职后续重新实现/完善的”

---

## 11. 事实边界

### 可以说

- 我在软件质量组实习
- mentor 是代码质量方向牵头人
- 企业已有 AI Code Review
- 企业发现现有 Review 偏浅层
- mentor 让我探索 OpenClaw 自动 Git Diff Review Agent
- 我基于社区已有工作流搭过原型
- 原型能读取 Git Diff 并自动输出 Review

### 暂时不要说

- 我从零开发了 OpenClaw Agent 框架
- 这个系统正式上线生产
- 公司采用了我的方案
- Recall / Precision 提升多少
- 发现了多少生产 Bug
- 我独立设计了完整调用链分析算法

除非后面有真实证据。

---

## 12. 下一对话建议启动语

可以直接把下面这段作为新对话第一条消息：

> 我在继续整理一段 2026 年 1～2 月的软件质量组实习经历，目标是近期投递 AI Agent / 智能体开发实习。  
> 当时公司的痛点是现有 AI Code Review 偏浅层，我的 mentor（公司代码质量方向牵头人）让我基于 OpenClaw 搭一个能够自主审查 Git Diff 的 Agent 原型。  
> 我现在已经确认：当时不是从零开发 Agent，而是安装 OpenClaw，找到社区已有的自动代码审查 workflow / skill 并复用；原型已经能读取 Git Diff、自动分析并输出 Review。  
> 现在最重要的任务是：**帮我从 GitHub / ClawHub / 社区资料中还原 2026 年 1～2 月当时可能复用的 OpenClaw Code Review 方案，并据此恢复原型技术链、个人贡献和面试细节。**
> 
> 简历当前基准描述是：
> **“基于 OpenClaw 搭建自动化代码审查 Agent 原型，复用社区审查工作流，实现 Git Diff 的自动分析与 Review 输出，探索 Agent 在代码质量审查场景中的应用。”**
> 
> 请先优先做“历史方案还原”，不要直接重新设计一个全新的 Agent。

---

## 13. 当前最重要的一句话

> **我们的目标不是把这个项目研究得最深，而是把这段真实企业经历恢复成一段有工程细节、有结果、有局限、能经得起 AI Agent 岗位技术面试追问的经历。**
