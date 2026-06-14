---
title: "PAMCL 正式开源：为物理 AI 打造的声明式多智能体控制元层"
date: "2026-06"
author: "ThinkMachine Labs"
tags: ["物理AI", "多智能体系统", "工业控制", "开源", "YAML"]
excerpt: "PAMCL（Physical AI Meta-Control Layer）是一个 vendor-agnostic 的控制编排框架。通过 YAML 声明 Agent 组合、约束规则与调度逻辑，让任何团队的智能体都能零侵入接入，同时提供完整的结构化审计能力。"
---
# PAMCL 正式开源：为工业物理 AI 打造的声明式多智能体控制元层

**Physical AI Meta-Control Layer**（简称 PAMCL）今天正式在 GitHub 开源。

这是一个**完全 vendor-agnostic** 的框架，目标是解决工业过程控制中多智能体闭环调度的最大痛点：

> 调度逻辑硬编码、约束参数写死在代码里、setpoint 变更完全不可追溯。

我们希望工艺工程师能够**只改 YAML**，就能完成新 Agent 接入、约束调整、甚至运行时热更新，而不需要动一行 Python 调度代码。

---

## 背景：工业物理世界里的多 Agent 控制有多难？

在渣选磨浮、选矿、水泥、化工等连续过程工业中，工厂通常会部署多个专业 Agent：

- Coordinator（工艺协调器）
- Grinding / Mill Agent（磨矿）
- Flotation Agent（浮选）
- 后续可能还要增加 Hydrocyclone、Sump、Thickener 等

这些 Agent 有不同的决策周期（Coordinator 可能是 5 分钟一次，底层设备 Agent 30 秒一次），还必须遵守大量**物理约束**（P80 粒度上限、回收率下限、功率硬限、矿浆液位范围……）。

传统做法是把所有协调、约束判断、setpoint 分发逻辑全部硬编码在一个 `MultiAgentPlantEnv.step()` 里。结果就是：

- 想加一个新设备 Agent，就要改调度签名和合并逻辑
- 想把 P80 软限从 67 改成 66.5，就要重新部署代码
- 工艺工程师想知道“上周谁把给矿量从 60 调到 66”，却只能翻 episode history

PAMCL 正是为解决这三个问题而生。

---

## PAMCL 是什么？

PAMCL 是一个**元控制层**（Meta-Control Layer），它不替代具体的设备 Agent，而是负责：

1. **通过 YAML 声明整个控制组合**（Composition）
2. **按照声明自动完成调度、分发、约束评估、限幅**
3. **把所有关键变更完整记录到结构化审计日志**

核心承诺只有一条：

> **任何满足 `observe()` / `act()` / `reset()` 方法签名的 Python 类，都可以零依赖、零继承地成为 PAMCL 的 Agent。**

你甚至不需要 `import pamcl`。

---

## 核心特性

### 1. 声明式约束引擎（已完整支持 CAUTION）

```yaml
constraints:
  warmup_steps: 60
  rules:
    - id: p80_upper
      variable: P80
      type: max
      soft_limit: 67.0
      hard_limit: 72.0

    - id: recovery_lower
      variable: recovery
      type: min
      soft_limit: 0.765
      warmup_exempt: true     # 冷启动期间不考核

    - id: sump_level
      variable: sump_level
      type: range
      min: 0.8
      max: 3.0
```

PAMCL 的 `ConstraintEvaluator` 支持三种规则类型，并对所有类型都实现了完整的四级严重程度：

- **NOMINAL** → **CAUTION**（逼近软限）→ **ALERT**（超过软限）→ **CRITICAL**（超过硬限）

v0.4.0 特别补全了 `min` 和 `range` 的 CAUTION 逻辑，使工艺工程师在冷启动保护和正常运行阶段都能获得一致的“逼近预警”体验。

### 2. 真正的热重载（Warmup 进度不丢失）

```python
scheduler.reload_constraints()   # 或传新 manifest 路径
```

重载后，约束评估器（`ConstraintEvaluator`）内部的 warmup 计步器会**保留**，不会让系统突然又进入"冷启动保护模式"。这是我们在代码审查后重点强化的工业级可靠性细节。

### 3. Shadow Mode（影子模式）

```python
scheduler = CompositionScheduler(..., shadow_mode=True)
```

影子模式下所有 Agent 正常决策、约束正常评估、审计正常写入，但 `step()` 返回空字典，**完全不向现场下发控制**。非常适合新策略上线前的并行验证。

### 4. 结构化审计 + 可视化 Dashboard

每一次 setpoint 变化、模式切换、约束违规、配置重载都会以 JSON Lines 格式落盘：

```jsonl
{"timestamp_iso":"2026-06-14T11:30:00+0800","event_type":"setpoint_change","agent_id":"coordinator","variable":"feed_rate_target","old_value":60.0,"new_value":66.0,"reason":"mode=1"}
{"timestamp_iso":"2026-06-14T11:35:30","event_type":"constraint_violation","severity":"CAUTION","violations":["P80=66.75 approaching max=67.0"]}
{"timestamp_iso":"2026-06-14T11:36:00","event_type":"config_reload","old_rules":6,"new_rules":7,"source":"manifest.yaml"}
```

自带一个零依赖的 Web Dashboard，一条命令即可启动：

```bash
python -m pamcl dashboard logs/audit.jsonl
```

### 5. 零侵入的 Agent 集成

一个第三方 Agent 只要长这样就能直接用：

```python
class VendorXMillAgent:
    def __init__(self, speed_rpm=1200):
        self.speed_rpm = speed_rpm

    def observe(self, state):
        return {"power": state.get("mill_power_kW")}

    def act(self, obs):
        return {"mill_speed_rpm": self.speed_rpm}

    def reset(self):
        pass

    # 可选：接收 Coordinator 下发的 setpoint
    def update_setpoints(self, speed_rpm=None):
        if speed_rpm is not None:
            self.speed_rpm = speed_rpm
```

在 YAML 里注册即可：

```yaml
agents:
  - id: grinding
    role: equipment
    class: vendor_x.mill_agent.VendorXMillAgent
    config:
      speed_rpm: 1200
```

---

## 快速开始

```bash
git clone https://github.com/thinkmachine/PAMCL   # 假设仓库地址
cd PAMCL
pip install -e ".[dev]"
```

使用示例（完整代码见仓库 README）：

```python
from pamcl import load_composition, CompositionScheduler, AuditLogger

comp = load_composition("compositions/slag_grinding_flotation.yaml")

scheduler = CompositionScheduler(
    agents=comp["agents"],
    scheduling=comp["scheduling"],
    constraint_evaluator=comp["constraint_evaluator"],
    control_clamper=comp["control_clamper"],
    audit_logger=AuditLogger("logs/audit.jsonl"),
    manifest_path=comp["manifest_path"],
)

# 你的现场/仿真接口
for _ in range(480):
    controls = scheduler.step(plant.get_obs(), metrics)
    # ... 下发给真实设备
```

---

## 为什么选择 PAMCL？

| 维度         | 传统硬编码方式    | PAMCL                               |
| ------------ | ----------------- | ----------------------------------- |
| 新增 Agent   | 修改调度代码      | 只加 5 行 YAML                      |
| 修改约束参数 | 改代码 + 重新部署 | 编辑 YAML +`reload_constraints()` |
| 审计追溯     | 几乎没有          | 结构化 JSONL + Dashboard            |
| 多厂商集成   | 困难              | 零依赖 Protocol                     |
| 冷启动保护   | 各处散落          | 统一 `warmup_exempt`              |
| 影子验证     | 需要单独分支      | `shadow_mode=True` 一键开启       |

---

## 设计演进与成熟度

PAMCL 起源于 PICCS-SF（Physical Intelligent Control & Cyber-Physical Systems）项目。早期版本（v1）与特定仿真器紧耦合。v2 版本（当前主线）完成了彻底解耦：

- 核心 `pamcl/` 包**零 simulator 依赖**
- 使用 `typing.Protocol + @runtime_checkable`
- 完整的约束引擎 + 控制限幅器
- 生产级的热重载与审计健壮性

我们在发布前进行了多轮代码审查，针对约束 CAUTION 完整性、热重载语义、Manifest 验证深度、审计容错、Dashboard 安全性等多个方面完成了系统性修复与加固。

---

## 开源与未来计划

仓库地址（已开放）：

**https://github.com/thinkmachine2023/PAMCL**

我们欢迎以下形式的贡献：

- 提交新的约束规则类型或更精细的 CAUTION 策略
- 增加 OPC-UA / MQTT 等现场接口适配器示例
- 完善 Dashboard（目前是单文件自包含实现）
- 提供更多真实工业场景的 Composition 示例
- 帮助梳理更多 audit 查询与合规报告工具

**短期路线图**：

- 更丰富的 CLI（支持直接运行影子验证）
- 约束规则的表达式扩展（支持复合条件）
- 更好的多 manifest 组合与版本管理

---

## 结语

物理 AI 的落地，核心瓶颈往往不是单个 Agent 有多聪明，而是**如何把多个专业智能体安全、可控、可追溯地编排在一起**。

PAMCL 希望成为这个“编排层”的一个轻量、可靠、可演进的答案。

PAMCL开源，是希望能和更多做过程工业智能化、做具身智能、做物理世界闭环控制的伙伴一起把这个事情做得更好。

欢迎 Star、Fork、提 Issue，也非常欢迎直接在 GitHub Discussion 里聊你的场景和需求。

**让工艺知识重新回到 YAML，让变更永远有迹可循。**

---

**相关链接**（发布时更新）：

- GitHub: https://github.com/thinkmachine2023/PAMCL
- 文档: 仓库内 README + docs/
- 物理AI实验室: https://www.thinkmachine.cn/labs/

如果你正在做类似方向的研究或工程落地，欢迎联系我们交流。
