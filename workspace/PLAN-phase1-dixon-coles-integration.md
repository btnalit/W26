# Phase 1 — Dixon-Coles Model Integration Plan (v2)

**目标**：为 wc26-handicap-analyst 接入真正的 Dixon-Coles 模型，产出可验的 `p_model` + margin 分布，补全铁律第 1 条 "每张卡必须同时给出 p_model / p_market / p_adj"，同时确保模型不引入噪声 edge。

**审计基础**：
- 原始诊断：manifest 3 devig / 0 model / penaltyblog 未装 / synthetic_poisson.py 是硬编码稻草人
- Claude 四修正：数据源不是 penaltyblog 自带 / 模型保持干净基线 / p_model ≠ p_market 是调查信号不是偏离许可 / 校准达标前只显示不启用
- v2 五修订：neutral 过滤错误 / DC 主场项保留 / 校准加历史 holdout 路径 / 验收加三条硬断言 / C2 no_agent

---

## 总体架构

```
┌──────────────────────┐     ┌──────────────────────────┐
│  collector cron       │     │  per-match analyst       │
│  (每6h / 每24h)       │     │  (LLM task, read-only)   │
│                      │     │                          │
│  1. 拉国际赛数据      │     │  读 model artifact →     │
│  2. 拟合 DC           │────▶  填 Market Board         │
│  3. 对近期比赛预测    │     │  p_model 列              │
│  4. 写 model artifact │     │  标注 informational      │
│  5. 写校准分          │     │  p_adj 仍锁 p_market     │
└──────────────────────┘     └──────────────────────────┘
```

---

## 交付物清单

### A. 新文件

#### A1. `scripts/fetch_international_data.py`
**角色**：数据收集器（cron collector 模式，与 `_fetch_weather.py` 同级）

**行为**：
1. 拉取 `martj42/international_results` 仓（GitHub master 分支）的 `results.csv`
   ```
   URL: https://raw.githubusercontent.com/martj42/international_results/master/results.csv
   ```
2. 缓存到 `snapshots/international_results/results-{YYYY-MM-DDTHHMMSSZ}.csv`
3. 保留最近 2 份快照（新快照写入后，删最旧的）
4. 输出统计摘要：球队数、比赛数、日期范围
5. 退出码：0=成功，1=拉取失败

**数据规格**（已用真实验证）：
```
表头: date,home_team,away_team,home_score,away_score,tournament,city,country,neutral
neutral 列分布: FALSE 36306 / TRUE 13072
tournament 分布: Friendly 18301 / WC qual 8771 / FIFA World Cup 1036 / Copa América / AFCON ...
```

**注意事项**：
- 这是纯数据收集，不做任何拟合
- 设计为 cron 可执行：`python3 scripts/fetch_international_data.py`
- 不做 paid API 调用，配额影响=0

---

#### A2. `scripts/model_runner.py`
**角色**：DC 拟合 + 预测输出（cron 可执行，也支持单场调用）

**入口**：
```bash
# 批量模式（cron）：拟合并预测所有 upcoming 比赛
python3 scripts/model_runner.py \
  --data snapshots/international_results/results-*.csv \
  --mode batch

# 单场模式（分析师 debug/测试）
python3 scripts/model_runner.py \
  --data snapshots/international_results/results-*.csv \
  --mode match \
  --home "Mexico" --away "South Africa" \
  --match-id M001
```

**模型细节**：

##### 1. 数据筛选
- **保留全部比赛**。martj42 全是国际赛，无俱乐部赛可过滤。
- 友谊赛（`tournament == "Friendly"`）设置权重 0.3
- 世界杯（`tournament == "FIFA World Cup"`）权重 1.0
- 洲际杯赛（AFCON / Copa América / Asian Cup / Euros）权重 1.0
- 其他正式赛事（WCQ / ACQ / Nations League 等）权重 0.8
- 时间衰减：半衰期 2 年（730 天），`weight *= 0.5^(days_ago / 730)`
- 只取最近 8 年的比赛

##### 2. neutral 列处理（v2 修正 #1，v3 技术核对后备）
- **不按 `neutral != TRUE` 过滤** — 这会丢掉 13072 场中立场大赛（～26%），包括世界杯本身
- 对 `neutral == TRUE` 的行，预测时**关闭主场优势参数**。
- **技术约束**：stock `penaltyblog.models.DixonColesGoalModel` 只有一个全局主场参数（`home_advantage`），不一定支持按场开关。
  **后备方案（如不支持按场开关）**：
  - 拟合时对 `neutral==TRUE` 的每场比赛做**双向录入**：一次 home=A/away=B，一次 home=B/away=A
  - 这样双方的"主场"净效应在拟合中互相抵消，等价于不贡献主场偏置
  - 预测中立场比赛时，跑**正反两向平均**：一次 A主场vsB客场、一次 B主场vsA客场，取 p_home/p_draw/p_away 的均值
  - 验证：两向平均的 p_home ≈ p_away（对称），差值 < 0.005 视为通过
- **对 M001 无影响**：墨西哥在阿兹特克是真主场，`neutral=FALSE`，标准主场项正常开启

##### 3. 拟合方法
- 使用 `penaltyblog.models.DixonColesGoalModel`（v2 修正类名，Step 2 预确认）
- 输入：home_team, away_team, home_goals, away_goals, weight
- 拟合器参数：默认（maxiter=2000, tol=1e-6）
- **保留 DC 内建主场优势参数**（v2 修正 #2）—— 这是干净基线的一部分
- **排除在模型外的**：海拔 / 天气 / 具体球场 / 公众热度 / 大赛动机 —— 这些结构因子全部留在 adjustment ledger

##### 4. 预测输出（对指定比赛）
- 构建 score matrix (0-9 × 0-9)
- 从 matrix 计算：
  - `p_model_home`, `p_model_draw`, `p_model_away`
  - margin 分布（`margin_probabilities: { "-5": 0.0003, "-4": 0.0019, ... }`）
- 同时输出 Elo baseline 作为参考（使用 penaltyblog 的 Elo 类）

##### 5. 输出文件
```
reports/artifacts/model-{match_id}-{window}.json
```

**模型清晰度约束**：
- p_model 只包含 "纯实力基线" —— 基于双方历史进球率的预测
- 不包含：海拔 / 天气 / 球場 / 大赛动机 / 公众热度
- 这些结构因子全部留在 adjustment ledger，由分析师评估
- 模型 artifact 的 `model_contract` 字段固定为 `p_model_is_clean_strength_baseline`，帮助审计时识别是否混入了因子

---

#### A3. `scripts/calibration_check.py`
**角色**：校准验证器 — 判断模型产物是否已达标可影响决策

**行为**：
1. 读取 `grading/model_calibration.duckdb`（已有的校准数据库）
2. 查询已完赛的模型预测记录（`model_p_home`, `model_p_draw`, `model_p_away` vs 实际结果）
3. 计算：
   - **Brier score**: `(p_home - actual_home)^2 + (p_draw - actual_draw)^2 + (p_away - actual_away)^2`
   - **Log-loss**: `-sum(y_i * log(p_i))`
   - **Calibration curve**: 按概率分桶 (0-10%, 10-20%, ...) 看实际频率 vs 预测概率
4. 判断逻辑：
   ```
   - n_graded_live < 20 AND historical_holdout_not_done:
       → calibration_status: insufficient_data
   - historical_holdout_pass AND n_graded_live < 20:
       → calibration_status: holdout_pass   # 可显示，但 p_adj 仍锁市场
   - n_graded_live >= 20 AND brier <= brier_benchmark:
       → calibration_status: pass           # 可影响 p_adj
   - n_graded_live >= 20 AND brier > brier_benchmark:
       → calibration_status: fail
   ```
5. 额外的历史留出法校准路径（v2 修正 #3，v3 修正 benchmark）：
   - `--mode historical`：从 martj42 数据中留出 2023-至今作为 holdout 集
   - 使用 2018-2022 数据拟合，预测 holdout 比赛
   - 计算 **Brier / log-loss vs 实际赛果**（这是 martj42 能提供的，只有赛果没有赔率）
   - **不计算 "模型 vs 市场" benchmark** — 因为 martj42 和 football-data 都不含历史收盘赔率，算不出来
   - 输出 `historical_brier`, `historical_log_loss`
   - pass 判断标准：`historical_brier < 0.25`（比随机猜测 3 分类的基准 Brier ≈ 0.33 好），**不作为"跑赢 Pinnacle"的证据**
   - 结果写入校准数据库，供开赛前参考

6. 输出文件（或 stdout JSON）：
   ```json
   {
     "calibration_status": "insufficient_data | holdout_pass | pass | fail",
     "n_graded_live": 0,
     "n_graded_historical": 412,
     "brier_live": null,
     "brier_historical": 0.198,
     "brier_benchmark": 0.210,
     "log_loss_historical": 0.64,
     "calibration_status_as_of_utc": "2026-06-05T12:00:00Z"
   }
   ```

**安全规则**：
- 校准 **holdout_pass** 阶段：p_model 在 Market Board 中标注 `info, holdout_pass`，仍不可触发 adjustment
- 校准 **pass**（live 20+ 场且 Brier 优于基准）：p_model 成为一个可信的 **诊断输入**。
  可以被引用进一条**具名的结构性 ledger 调整**中作为佐证，但：
  - **永远不能单独把 p_adj 推离 p_market。** 任何 p_adj 偏离必须由具名结构因子（天气 / 海拔 / 伤病 / 轮换等）主导，p_model 分歧最多作为"这个方向值得调查"的补充论据。
  - **"模型 vs 市场分歧"本身永远不是 edge。** 校准通过只证明模型概率在总体上诚实，不证明它比 Pinnacle 收盘价准——Pinnacle 本身就是更 sharp 的校准估计。一个校准过的 DC 跟 Pinnacle 分歧，八成是模型缺了 Pinnacle 已知的东西。
- 校准 **fail**：p_model 在 Market Board 中标注 `info, uncalibrated`，且不显示具体数值以避免误用

**本届现实期待**（v2 明确记录）：
- 即使 `holdout_pass` 开赛前达标，T-72h 第一波仍保持 informational
- 最早 T-48h_update 窗口（约第 2-3 比赛日）让 p_model 进入 ledger
- 前 20 场 live 验证后才完全放开
- [架构决策] 如果这个节奏太慢，可以接受"本届 purely informational，为 2030 积累"——前提是明确选（b）且知道代价

---

### B. 修改文件

#### B1. `reports/artifacts/` — model artifact JSON 格式标准

**新格式**（DC 版本，替换旧 hardcoded Poisson 格式）：

```json
{
  "artifact_id": "model:M001:20260605T120000Z",
  "artifact_type": "model",
  "script": "model_runner.py",
  "model_name": "dixon_coles_v1",

  "home_team": "Mexico",
  "away_team": "South Africa",
  "match_id": "M001",

  "p_model": {
    "home": 0.734,
    "draw": 0.171,
    "away": 0.095
  },
  "margin_probabilities": {
    "-10": 2.5e-09,
    "-9": 3.6e-08,
    "0": 0.244,
    "1": 0.259,
    "10": 3.6e-06
  },

  "elo_reference": {
    "home_elo": 1790,
    "away_elo": 1512,
    "elo_p_home": 0.812
  },

  "model_params": {
    "time_decay_halflife_days": 730,
    "friendly_weight": 0.3,
    "neutral_ground_home_off": true,
    "data_date_range": ["2018-01-01", "2026-06-04"],
    "n_matches_used": 4283
  },

  "calibration": {
    "status": "holdout_pass",
    "n_graded_live": 0,
    "n_graded_historical": 412,
    "brier_historical": 0.198,
    "brier_benchmark": 0.210
  },

  "source_data_snapshot": "results-2026-06-04T120000Z.csv",
  "fitted_at_utc": "2026-06-05T12:00:00Z",
  "model_contract": "p_model_is_clean_strength_baseline"
}
```

关键字段说明：
- `model_name`: `dixon_coles_v1` — 留版本空间，后续可升级
- `neutral_ground_home_off`: true — 中立场预测时禁用主场优势（v2 修正 #1）
- `calibration.status`: `insufficient_data` / `holdout_pass` / `pass` / `fail`
- `model_contract`: 固定值，审计用

#### B2. Market Board 模板 — 补回 Model Fair 列

**当前**：
```
| Market | Line | Book | Source Unit | Current Decimal | Snapshot ID | Devig Artifact | No-Vig Market | p_adj | Edge | Note |
```

**修正后**：
```
| Market | Line | Book | Source Unit | Current Decimal | Snapshot ID | Devig Artifact | No-Vig Market | Model Fair | p_adj | Edge | Note |
```

Model Fair 列填充规则：
| calibration.status | 显示 | 含义 |
|---|---|---|
| `insufficient_data` | `— (info)` | 模型不可用，不显示数值 |
| `holdout_pass` | `0.734 (info)` | 可显示，仅供参考，不可触发调整 |
| `pass` | `0.734` | 可进入 ledger 作为参照 |
| `fail` | `— (info)` | 模型不可用 |

#### B3. devig-ah artifact 增强 — margin 分布引用（Phase 3 前置）

**当前**（devig-ah-m001-live.json）：
```json
{
  "no_vig_probabilities": [0.47194, 0.52806],
  "overround": 0.02363,
  "devig_method": "multiplicative"
}
```

**增强后**（仅加引用，不实现按腿结算 EV）：
```json
{
  "margin_distribution_ref": "model:M001:20260605T120000Z"
}
```

**重要更正**（v2 修正 #3）：例子中的 `cover_p` / `push_p` / `lose_p` 字段不在 Phase 1 范围内。
Phase 3 实现按腿结算时，cover/push/lose 必须来自模型 margin 分布，
**不是**市场 no-vig 概率。Phase 1 只架桥（引用 model artifact），不拆桥。

---

### C. Cron 任务

#### C1. 数据收集 cron（每 6h）
```yaml
name: wc26-international-data-collect
schedule: "0 */6 * * *"
script: /hermesdata/worldcup-2026-handicap/scripts/fetch_international_data.py
no_agent: true
deliver: local
```
- no_agent=true：纯脚本 cron，无 LLM 开销
- deliver=local：静默运行，只有失败时 cron 框架发告警
- pid 锁防止并发写入

#### C2. 模型拟合 cron（每 24h）
```yaml
name: wc26-dc-model-fit
schedule: "0 12 * * *"
script: |
  /hermesdata/worldcup-2026-handicap/scripts/model_runner.py --mode batch
  /hermesdata/worldcup-2026-handicap/scripts/calibration_check.py --update
no_agent: true
deliver: origin
context_from:
  - wc26-international-data-collect
```
**v2 修正 #5**：改为 no_agent=true。
- "判断哪些是 upcoming 比赛" = `kickoff_utc > now` 的确定性过滤，不需要 LLM
- 串联 `model_runner --mode batch && calibration_check --update`
- 消除 LLM 非确定性和超时风险
- 输出到 origin（你自己这里）做知晓

---

## 实施顺序

### Step 1 — 基础设施（30 分钟内可完成）
1. 装 penaltyblog: `pip install penaltyblog`
2. `python3 -c "from penaltyblog.models import DixonColesGoalModel; print('OK')"` 验证类名（v2: 确认实际类名）
3. 写 `fetch_international_data.py` 并跑一次拉数据
4. 验证数据：行数、球队数、日期范围、neutral/tournament 分布

### Step 2 — 模型拟合（1-2 小时）
1. **预确认**：`python3 -c "from penaltyblog.models import DixonColesGoalModel; m = DixonColesGoalModel(...)"` 验证类名 + 检查 `home_advantage` 参数结构
   - 如果 `DixonColesGoalModel` 不支持按场开关主场项，走后备方案（双向录入 + 正反平均）
   - 记录确认结果到 workspace README 或 `scripts/model_runner.py` 文件头
2. 写 `model_runner.py`
3. 在单场模式测试（Mexico vs South Africa / 选一个历史赛果已知的）
4. 验证输出：p_model 合理、margin 分布总和=1
5. 验证确定性：同输入跑两次，p_model 逐位一致（v2 新增验收）
6. 输出 model-M001 artifact 到 reports/artifacts/

### Step 3 — 校准检查（1 小时）
1. 写 `calibration_check.py`
2. 验证 duckdb 连接正常
3. 跑 `--mode historical` 留出法校准
4. 确认 `n_graded_live=0 → holdout_pass` 逻辑生效
5. 校准结果写入 duckdb

### Step 4 — 铁律硬测试（v2 新增，30 分钟）
1. **铁律测试**：
   - **(a) 默认状态**：用真实 DC 跑 M001（calibration=holdout_pass），验证输出为 `watch`、`p_adj=p_market`、`edge=0`
     即使 `p_model(~0.73) ≠ 市场(~0.67)`。如果输出 `lean 墨西哥` → 打回
   - **(b) 危险路径**（强化，v3）：**把 calibration_status 强制设成 `pass`**，喂一个 `p_model` 跟市场大分歧（例如 p_model_home=0.85 vs p_market=0.67）、
     ledger 无具名理由的场景，断言仍然输出 `watch`、`p_adj=p_market`、`edge=0`。
     如果(b)输出 `lean` 或 `qualified_play` → 打回，这证明"校准后纪律还在"
     这条(b)必须在开赛前就通过，因为开赛后校准到 pass 时已经没有回头路了
2. **确定性测试**：同输入跑两次，model artifact 逐位一致 → 写 CMT 脚本
3. **真数据测试**：验证 `n_matches_used > 1000` + 旧 `synthetic_poisson.py` 已删

### Step 5 — 报告模板更新（30 分钟）
1. 更新 Market Board 模板（加 Model Fair 列）
2. 更新 Market Board 列说明（calibration status 显示规则）
3. 更新 odds-analysis SKILL.md 中 p_model 的说明（标注 informational / calibration-gated）

### Step 6 — cron 上架（30 分钟）
1. 创建数据收集 cron（no_agent）
2. 创建模型拟合 cron（no_agent）
3. 验证第一次 cron 执行成功

### Step 7 — devig-ah 架桥（Phase 3 前置，30 分钟）
1. devig-ah artifact 增加 `margin_distribution_ref`
2. 不实现完整的按腿结算 EV（那属于 Phase 3）
3. 确认例子中**没有**用市场 no-vig 冒充模型 margin 分布（v2 修正）

---

## 验收标准

| # | 标准 | 验证方式 | 对应修正 |
|---|---|---|---|
| 1 | `pip show penaltyblog` 有输出 | 终端命令 | — |
| 2 | `snapshots/international_results/` 下有缓存 CSV | ls 确认 | ① |
| 3 | CSV 中 neutral=TRUE 的行未被过滤（应有约 13000 行） | `grep ',TRUE,' results*.csv | wc -l` | ① |
| 4 | `model_runner.py --match-id M001` 输出合法 model artifact | 验证 JSON 各字段 | — |
| 5 | model artifact 包含 `neutral_ground_home_off: true` | grep 确认 | ① |
| 6 | model artifact **不包含** 海拔/天气/球場字段 | grep 确认不在 JSON 中 | ② |
| 7 | **铁律硬测试**: M001 DC 输出为 `watch` + `p_adj=p_market` + `edge=0` | 运行测试脚本 | ④ |
| 8 | **确定性测试**: 同输入跑两次，artifact SHA 一致 | `sha256sum` 对比 | ④ |
| 9 | **真数据测试**: n_matches_used > 1000，synthetic_poisson.py 已删 | 数值检查 + file check | ④ |
| 10 | `calibration_check.py --mode historical` 输出合法 JSON | stdout 验证 | ③ |
| 11 | SKILL.md Market Board 模板有 Model Fair 列 | read_file 确认 | — |
| 12 | devig-ah artifact 有 `margin_distribution_ref` 但 **无** cover_p/push_p 等伪字段 | read_file 确认 | B3 修正 |
| 13 | C2 cron 为 `no_agent: true` | `cronjob list` 确认 | ⑤ |
| 14 | C2 cron `script` 包含 `model_runner --mode batch && calibration_check` | `cronjob list` 确认 | ⑤ |

---

## 安全边界检查表（v2 五修正的落地验证）

| 修正 | 内容 | 落地位置 | 验证方式（验收 #） |
|---|---|---|---|
| ① | neutral 不过滤，中立场关闭主场优势 | A2 数据筛选 + neutral 处理 | #3 (行数)、#5 (JSON 字段) |
| ② | 保留 DC 内建主场参数，排除结构因子 | A2 拟合方法 | #6 (grep 反查) |
| ③ | 校准加历史 holdout 路径，开赛前可达标 | A3 额外路径 + 本届现实期待段 | #10 (historical 输出) |
| ④ | 三条硬断言验收 | Step 4 铁律硬测试 | #7, #8, #9 |
| ⑤ | C2 no_agent | C2 cron 定义 | #13, #14 |

---

## 风险与边界条件

### 风险
1. **penaltyblog 的 `DixonColesGoalModel` 在大规模数据（4000+ 比赛）下的拟合时间** — 如果单次拟合 > 10 分钟，考虑限制历史窗口（最近 6 年）
2. **martj42 CSV 格式变更** — 写一个格式校验步骤（检查表头列数），格式异常时报警但不中断现有流程
3. **DuckDB 校准数据库不存在或空** — calibration_check.py 应当处理空表/无表的情况，优雅输出 `insufficient_data`
4. **penaltyblog 实际类名不叫 `DixonColesGoalModel`** — Step 1 先确认再写

### 明确不做的
- 不实现 AH 按腿结算 EV（Phase 3）
- 不实现 report_guard 管线（Phase 4）
- 不调整 ledger 规则（铁律不动）
- 不改动任何已存活的 cron 任务
- 不接入任何 paid API
- 不在 per-match LLM 任务中现场拟合 DC
- C2 cron 不做 LLM cron（已改为 no_agent）
