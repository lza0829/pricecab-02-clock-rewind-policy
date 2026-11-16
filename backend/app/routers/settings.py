from fastapi import APIRouter
from pydantic import BaseModel

from app.db import connect
from app.repositories import settings as settings_repo
from app.services import clock_policy as policy
from app.services import clock_service

router = APIRouter(prefix="/settings", tags=["settings"])


class RewindPolicyIn(BaseModel):
    clock_rewind_policy: str


@router.get("")
def list_settings():
    conn = connect()
    try:
        return settings_repo.list_all(conn)
    finally:
        conn.close()


@router.get("/clock-rewind-policy")
def get_rewind_policy():
    return clock_service.get_clock()


@router.put("/clock-rewind-policy")
def set_rewind_policy(body: RewindPolicyIn):
    # 两种策略互斥、全局唯一；非法取值在 normalize 处被拒。
    return clock_service.set_policy(body.clock_rewind_policy)


@router.get("/clock-rewind-policy/options")
def rewind_policy_options():
    return {
        "policies": list(policy.POLICIES),
        "labels": policy.POLICY_LABELS,
        "default": policy.DEFAULT_POLICY,
    }
