# P1 Proposal — 模型可信度提升：Brier 分层 + Margin 重校准

**提出时间**: 2026-06-05
**状态**: proposal（待讨论，不可执行）
**依赖**: P0（sigma 现场算 + fact-lock 统一）已完成

---

## 问题陈述

当前模型通过了 `holdout_pass`，但这个"达标"几乎没含金量：

1. **Brier 0.509 只赢了均匀随机（0.667）**——任何没坏的模型都能过
2. **国际赛 holdout 被大量悬殊比赛灌水**（强 vs 鱼腩），预测 "巴西吊打小国" 很容易，Brier 自然低，但完全不说明模型在**竞争性比赛**上的准度
3. **模型 margin（净胜球）分布对强打弱有系统性过度自信**——导致 AH/大小球的模型结算 EV 方向可信但量级夸大，具体表现为模型给瑞士 -1.75 算出 +8.9% EV，但真实数据可能只兑现 28%

结果：`holdout_pass` 目前只证明"不是随机"，没证明"好"。系统的 diagnostic 处置是对的，但为了将来能升级到 pass，必须先修这两个基础设施。

---

## P1-A：Brier 按竞争度分层

**已确认决策 (2026-06-05):**
- 桶范围：**40-75%** — 窄桶，高含金量。目标是抓住真正的好机会，不是每场下注
- 阈值：**方案B — Pinnacle 市场参考基线** — 模型竞争桶 Brier 与 Pinnacle 竞争桶 Brier 的差距决定 pass/fail，不拍绝对数字

### 当前实现

```
holdout_brier = Brier(all_matches)  # 含大量悬殊比赛
holdout_pass  = holdout_brier < uniform_random_brier  # 门槛≈0.667
```

### 修改方案

不再用全体 holdout 的总 Brier，改为按 **热门隐含概率** 分桶：

| 桶 | 热门 implied | 含义 | 包含比赛 | 用途 |
|----|------------|------|---------|------|
| blowout | >75% | 强vs鱼腩，模型预测容易 | 大量 | 记录但不用于 pass 判定 |
| **competitive** | **40–75%** | **你会真正下注的比赛** | **中等** | **决定 pass 的唯一依据** |
| tossup | <40% | 接近公平 | 少 | 记录 |

**已确认决策：方案B — 以 Elo 为市场参考基线（Pinnacle 数据不可得）**

无法获取历史 Pinnacle 开盘数据（the-odds-api/oddspapi 无批量历史接口），改为用 Elo 评分系统（已实现在历史 49,306 场数据上训练）作为市场代理基线。

Elo 基线比均匀随机好得多，且和模型使用相同的历史赛果数据——公平对比：

```
elo_brier       = 竞争桶上 Elo 评分的 Brier
model_brier     = 竞争桶上模型 predict 的 Brier
brier_gap       = model_brier - elo_brier
model_pass      = brier_gap < 容忍值
```

- Elo 不是 Pinnacle（Pinnacle 会更好），因此 brier_gap 不是"模型 vs sharp 市场"的精确值，而是"模型 vs 免费基线"的保守估计
- 如果模型 **不能** 稳定打败 Elo（brier_gap > 0），则模型连免费基线都不如 → 永久 info-only
- 如果模型稳定打败 Elo（brier_gap < -0.02），则模型有信息量，但离 Pinnacle 仍有差距

### 所需数据

- 已有：全部国际赛历史 holdout 预测 + 赛果
- 新增：每场比赛的**市场隐含热门概率**（因为用模型 predict 会循环自证，必须用 market implied）
  - 数据源：历史国际赛盘口（the-odds-api 或 football-data.org 赔率历史）
  - 如果历史盘口不完整：球队 elo/fifa 积分差作为替代代理

### 改动范围

- `model_runner.py`（校准模块）：Brier 计算改为分桶
- `calibration_thresholds` 配置：新增 `brier_competitive_threshold`、`favorite_range` 参数
- 报告模板：`校准状态` 行拆为 `holdout(Brier=0.509, all/competitive)`

### 验收条件

1. ✅ 已验证：竞争桶 Brier (0.587) 比碾压桶 (0.243) 差 34.4pp，全样本 0.509 是误导性数字——**分层假说被确认**
2. ✅ Elo 基线 Brier 已算得：竞争桶 0.597（n=2,508），模型 vs Elo gap = **-0.98pp**（模型略优于 Elo，但几乎打平）
3. ❌ Pinnacle 基线不可计算（缺历史数据），已换 Elo 代理——模型仅 1pp 优于免费基线，不足以从 info 升级为 actionable
4. 当前状态：**模型在竞争桶上应保持 info-only**，直至 margin 校准或其他改进将 brier_gap 扩大到有意义水平（至少 -3pp 以上）

---

## P1-B：Margin 分布对历史结果重新校准

### 当前实现

模型的净胜球分布直接从 Dixon-Coles/双 Poisson 的 xG 参数生成，没有经过历史赛果的校准：

```
margin_probs = dc_model.simulate_margin_distribution(home_xg, away_xg)
          ↓                                           ↓
      模型          ←——— 原始 DC 输出，没和实际净胜球比对过
```

已知偏差：强队 vs 摆大巴弱队时，模型高估大胜概率（见 MEMORY.md）。

### 修改方案

拿历史净胜球结果对 margin 分桶做校准，**以对历史校准为主，以向市场回归为辅**。

**已确认决策 (2026-06-05):** 市场收缩 cap = **30%**——安全绳，不是主导。目的是防止小样本桶极端偏离，不是让模型贴市场。

**主路径（历史校准）：**

```
1. 收集历史国际赛结果 → {home_goals, away_goals} → net_margin
2. 按 model 预测 margin 概率向量分桶（如 margin=-3,-2,-1,0,+1,+2,+3,...）
3. 对每个 margin 桶做 isotonic regression 或 Platt scaling
4. 输出 calibrated_margin_probs
```

这样校准后的 margin 分布就诚实了——模型计算 AH/大小球结算 EV 时用 `calibrated_margin_probs` 替代原始 `margin_probs`。

**辅助路径（市场回归），有上限：**

- 如果历史校准后的 margin 分布和市场 implied margin 分布差距仍然很大（>15pp on a single bucket）
- 对校准分布做**有上限的收缩**（max shrinkage = 30%）向市场靠拢
- 目的是防止校准数据不足时的极端偏离，不是让模型贴市场
- **不做无上限的市场回归**——那会让 p_model 退化为市场回声

### 风险与限制

| 风险 | 缓释 |
|------|------|
| 历史国际赛样本量不足（特别是特定 margin 桶如赢4+球） | 用 Bayesian 平滑：小样本桶向 Poisson 先验收缩 |
| isotonic regression 过拟合 | 用 Platt 或交叉验证选模型复杂度 |
| 市场回归辅助路径可能引入循环依赖 | 明确 cap=30%，且只在差距>15pp时才启用 |

### 实现顺序

1. 先建 margin 校准（主路径）——只依赖已有赛果数据，自洽
2. 验证校准后 margin 分布 AH 结算 EV 是否更合理（瑞士-1.75 这类极端 case 应显著回落）
3. 再视需要加市场回归辅助（次级路径）

### 改动范围

- `model_runner.py`: 新增 `margin_calibrator.py` 或模块内校准函数
- 新增数据依赖：历史国际赛最终赛果（football-data.org 已有，按 `score` 字段解析）
- 报告模板：AH 结算 EV 行标注 `margin_calibrated` vs `raw_margin`
- 铁律表新增一行：margin 校准状态（`未校准/已校准`）影响 p_adj 对 AH/totals 市场的调整权限

### 验收条件

1. 对已知有偏的 case（瑞士-1.75 / 巴西-0.75）运行校准后，AH 结算 EV 量级显著回落
2. 校准后 margin 分布的 PIT 直方图更接近 uniform
3. 不降低 1X2 层级的 predict 表现（校准不应破坏你已经对的地方）

---

## 互相关系

P1-A 和 P1-B 是独立的，可以并行实现：

- P1-A 影响 **1X2 层级的模型可信度**——决定 `p_model` 能否从 info 升级为 actionable
- P1-B 影响 **AH/大小球层级的模型可信度**——确保 margin 分布的计算 EV 不会被过度自信放大

两条都到位后，`holdout_pass` 才真正证明"模型好"，而不再是"模型不是随机"。

---

## 否决条件

- 如果 competitive Brier 分层后发现也未能通过合理阈值（>0.30），说明 DC 模型在国际赛上真的不够好——应当接受，将模型永久锁定为 info-only，edge 主路径完全交给 cross-book 价差（已有）
- 如果 margin 校准后 AH 结算 EV 不收敛（依然极端），说明 Poisson 框架不适合国际赛 margin 分布——接受，AH/totals 永远只走市场价，模型不参与

---

## 附录 A：实际数据验证结果（2026-06-05）

### 数据来源

- 赛果数据：martj42 国际赛数据库，49,306 场（1872-2026）
- 模型：Dixon-Coles (penaltyblog)，训练 2018-2022（7,393 场加权），预测 2023+（3,484 场）
- Elo 基线：标准 Elo（K=32, 初始 1500, 主场优势 +100 Elo），全部 49,306 场训练

### 分桶 Brier（竞争桶 40-75%）

| 桶 | 场数 | 占比 | 模型 Brier | Elo 基线 Brier | 均匀随机 |
|----|------|------|-----------|----------------|---------|
| 竞争 40-75% | 2,165 | 62.1% | **0.587** | 0.597 | 0.667 |
| 碾压 >75% | 891 | 25.6% | **0.243** | 0.242 | 0.667 |
| 抛硬币 <40% | 428 | 12.3% | **0.666** | 0.663 | 0.667 |
| **全体** | 3,484 | 100% | **0.509** | — | 0.667 |

### 关键数据点

- **模型 vs Elo 竞争桶差距：-0.98pp**（模型略好，但几乎打平）
- 模型 vs 均匀随机竞争桶差距：-8.0pp（明显好于随机，但这是最低门槛）
- 竞争桶 Brier 0.587：会 FAIL 当前的 0.55 threshold
- 竞争桶 Brier 0.587：会 PASS 一个 0.60 threshold，但仅差 1.3pp，贴着边

### 分层假说验证

✅ **强烈确认**：竞争桶 Brier（0.587）比碾压桶（0.243）差 **34.4pp**。整体 Brier 0.509 是一个将被"巴西吊打小国"类比赛灌水的误导数字。

### 结论

模型在竞争性比赛上：

1. **不是随机**（好 8pp vs 均匀随机）✅
2. **略优于 Elo**（好 0.98pp，几乎可忽略）⚠️
3. **离 Pinnacle 级 market 很远**（保守估计差距 ~25pp）❌
4. **当前应保持 info-only/diagnostic**，不可升级为 actionable

全部数据文件：
- `reports/artifacts/p1-stratification-analysis.json`（分层分析）
- `reports/artifacts/p1-elo-threshold-analysis.json`（Elo 对比 + 阈值分析）
