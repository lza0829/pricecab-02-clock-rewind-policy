"""业务钟回拨策略：全局唯一、互斥的两种模式。

系统在任意时刻只贯彻其中一种策略，由 settings.clock_rewind_policy 决定，
不允许两种回拨语义并存：

- FORBID（forbid_rewind）：禁止回拨。新钟点早于当前业务钟一律拒绝，
  已应用批次与价签本保持不动。
- RESCHEDULE（allow_rewind_reschedule）：允许回拨，但作废回拨点之后的生效
  结果。effective_at 大于新钟点的已应用改价单统一改回「可再调度」
  （scheduled）态，并按价格历史把价签本恢复到新钟点应有的现行价。
"""

from __future__ import annotations

from fastapi import HTTPException

# 禁止回拨：钟点只能向前
FORBID = "forbid_rewind"
# 允许回拨并作废回拨点之后的生效结果；被作废改价单固定回到可再调度态
RESCHEDULE = "allow_rewind_reschedule"

POLICIES = (FORBID, RESCHEDULE)
DEFAULT_POLICY = FORBID

# 被回拨作废的改价单固定恢复为该状态（可再调度，而非作废态）
REWIND_TICKET_STATUS = "scheduled"
# 价格历史条目的生命周期状态
HISTORY_ACTIVE = "active"
HISTORY_VOIDED = "voided"
# 回拨波及的应用批次终态
BATCH_SUPERSEDED = "superseded"

POLICY_LABELS = {
    FORBID: "禁止回拨：新钟点早于当前业务钟一律拒绝，已应用批次与价签本不动",
    RESCHEDULE: "允许回拨：作废回拨点之后的生效结果，改价单回到可再调度态并恢复价签",
}


def normalize(policy: str | None) -> str:
    """校验策略取值，非法值直接拒绝（防止两种策略被混入或拼写漂移）。"""
    value = (policy or "").strip()
    if value not in POLICIES:
        raise HTTPException(
            400,
            f"非法回拨策略 {value!r}，仅支持：{', '.join(POLICIES)}",
        )
    return value
