"""班报 API：模型严重延误概率校验、实绩登记/更正、班报签发。"""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import shift_report as service
from .security import current_user, get_db, require_roles

router = APIRouter(prefix="/api/shift-report", tags=["模型交班班报"])


class ActualIn(BaseModel):
    severe_actual: Literal[0, 1]
    note: str = Field(min_length=3, max_length=500)


class SignoffIn(BaseModel):
    terminal: str = Field(min_length=1, max_length=60)
    shift_key: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}-[ABC]$")
    note: str = Field(min_length=3, max_length=500)


@router.get("")
def report(db: Session = Depends(get_db), user=Depends(current_user)):
    return service.build_report(db)


@router.post("/actuals/{case_id}", status_code=201)
def record_actual(case_id: int, payload: ActualIn,
                  db: Session = Depends(get_db),
                  user=Depends(require_roles("supervisor"))):
    return service.record_actual(db, user, case_id, payload.severe_actual, payload.note)


@router.post("/signoffs", status_code=201)
def sign_off(payload: SignoffIn,
             db: Session = Depends(get_db),
             user=Depends(require_roles("supervisor"))):
    return service.sign_off(db, user, payload.terminal, payload.shift_key, payload.note)
