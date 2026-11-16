from fastapi import APIRouter
from pydantic import BaseModel

from app.services import apply_service as svc
from app.services import clock_service

router = APIRouter(prefix="/apply", tags=["apply"])


class ClockIn(BaseModel):
    business_clock: str


@router.get("/clock")
def get_clock():
    return svc.get_clock()


@router.put("/clock")
def set_clock(body: ClockIn):
    return svc.set_clock(body.business_clock)


@router.get("/clock/events")
def clock_events():
    """业务钟拨动/回拨留痕（只追加，不提供删除或改写）。"""
    return {"events": clock_service.list_events()}


@router.get("/batches")
def list_batches():
    return svc.list_batches()


@router.post("/due")
def apply_due():
    return svc.apply_due()
