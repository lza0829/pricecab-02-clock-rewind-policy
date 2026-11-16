# 06-pricecab（价签柜）

Pricecab — 改价单按预约时刻生效，现行价签可查，并与销售夹具对账。

与 Feedstitch（多源归并）、Batchgate（作业编排）、Syncledger（双端对账）、Changecast（CDC 投影）、Proofpack（采样放行）主链路不同。  
不是电商订单/购物车，不是仓库进出库 CRUD，不是考勤 OA。夹具全本地。

## 启动

```bash
docker compose up --build
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://localhost:5600 |
| API | http://localhost:10600 |

## 主链（0-1）

选商品建改价单（写明生效时刻）→ 拨业务钟或点「应用到期」→ 现行价签更新 → 销售对账看一致/不一致

## 业务钟回拨策略（全局唯一、互斥）

业务钟的回拨行为由设置项 `clock_rewind_policy` 决定，任一时刻只贯彻一种，两种语义不并存：

| 策略值 | 语义 |
| --- | --- |
| `forbid_rewind`（默认） | **禁止回拨**：新钟点早于当前业务钟一律拒绝（409），已应用批次与价签本保持不动。 |
| `allow_rewind_reschedule` | **允许回拨但作废回拨点之后的生效结果**：把 `effective_at` 大于新钟点的已应用改价单统一改回**可再调度（scheduled）态**（固定为该态，而非作废态），相关应用批次标记为 `superseded`；并按价格历史把价签本恢复到新钟点应有的现行价，回拨后不残留以被作废应用为来源的现行价。 |

两种策略下的共同约束：

- **应用到期进行中禁止改钟**：存在 `running` 批次时，任何前拨/回拨都返回 409。
- **价格历史不静默删除**：回拨只把对应历史行标注为 `voided` 并追加作废/恢复说明（只追加），不改价、不删行；对账（`price_at`）只取 `active` 历史，因此回拨后销售对账与恢复后的历史链一致。
- **回拨留痕**：每次拨钟写入 `clock_events`（前拨/回拨/初始化），回拨事件记录被作废单据、被取代批次与作废历史行数。
- 策略配置接口：`GET/PUT /api/settings/clock-rewind-policy`，非法取值返回 400。

## 测试

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest httpx
pytest -q
```

覆盖：禁止策略下回拨被拒且价签/批次不动；允许策略下回拨后价签与对账一致、历史只追加；回拨早于一切改价时回落挂牌价；应用进行中改钟被拒；两种策略互斥校验。

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。
