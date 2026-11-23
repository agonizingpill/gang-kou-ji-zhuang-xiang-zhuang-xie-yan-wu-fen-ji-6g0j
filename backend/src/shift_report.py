"""交班模型校验：按码头 × 首单派工班次评估严重延误概率的均方误差。

业务口径：
- 严重延误对应模型等级 critical，取派单瞬间快照的 severe_probability。
- 班次按首条调度处置单的派工时间（UTC+8）划分，夜归开始日。
- 主管确认的实绩按版本追加；迟到实绩补回原班次，更正按新实绩重新计分。
- 未报实绩的案件单列，不计入 MSE。
- 班报签发是一次性快照；签发后入账的更正只改变当前结果，已签发数字原样保留。
"""

from datetime import datetime, timedelta, timezone
from threading import Lock

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .models import AuditLog, DelayCase, DispatchAction, SevereDelayActual, ShiftReportSignoff

CST = timezone(timedelta(hours=8))
SEVERE_LEVEL = "critical"
_schema_lock = Lock()
_schema_ready = False


def _is_postgres(db: Session) -> bool:
    return db.get_bind().dialect.name == "postgresql"


def _serialize_writes(db: Session):
    """实绩登记与班报签发必须串行：Postgres 用表锁，SQLite 写事务本身互斥。"""
    if _is_postgres(db):
        db.execute(text("LOCK TABLE shift_report_signoffs IN EXCLUSIVE MODE"))


def ensure_schema(db: Session):
    """对既有 dispatch_actions 补列（新库由建表语句直接生成）。"""
    global _schema_ready
    with _schema_lock:
        if _schema_ready:
            return
        bind = db.get_bind()
        dialect = bind.dialect.name
        if dialect == "postgresql":
            db.execute(text("ALTER TABLE dispatch_actions ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE"))
            db.execute(text("ALTER TABLE dispatch_actions ADD COLUMN IF NOT EXISTS severe_probability FLOAT"))
            backfill = (
                "UPDATE dispatch_actions SET severe_probability = "
                "CAST(model_output->'probabilities'->>'critical' AS DOUBLE PRECISION) "
                "FROM delay_cases WHERE dispatch_actions.case_id = delay_cases.id "
                "AND dispatch_actions.severe_probability IS NULL"
            )
            db.execute(text(backfill))
            db.execute(text(
                "UPDATE dispatch_actions SET created_at = COALESCE(completed_at, due_at, CURRENT_TIMESTAMP) "
                "WHERE created_at IS NULL"
            ))
        else:  # sqlite —— 仅用于本地测试
            cols = {row[1] for row in db.execute(text("PRAGMA table_info(dispatch_actions)"))}
            if "created_at" not in cols:
                db.execute(text("ALTER TABLE dispatch_actions ADD COLUMN created_at DATETIME"))
            if "severe_probability" not in cols:
                db.execute(text("ALTER TABLE dispatch_actions ADD COLUMN severe_probability FLOAT"))
            db.execute(text(
                "UPDATE dispatch_actions SET created_at = COALESCE(completed_at, due_at, CURRENT_TIMESTAMP) "
                "WHERE created_at IS NULL"
            ))
        db.commit()
        _schema_ready = True


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def shift_of(moment: datetime) -> tuple[str, str]:
    """按北京时间（UTC+8）把派工时刻归入 (shift_key, 展示标签)。

    早班 06:00–14:00、中班 14:00–22:00、夜班 22:00–次日 06:00；
    凌晨 00:00–06:00 属于前一日 22:00 开始的夜班，夜班归属开始日。
    """
    local = _as_utc(moment).astimezone(CST)
    if 6 <= local.hour < 14:
        code, name = "A", "早班"
    elif 14 <= local.hour < 22:
        code, name = "B", "中班"
    else:
        code, name = "C", "夜班"
    shift_date = local.date() - timedelta(days=1) if local.hour < 6 else local.date()
    day = shift_date.isoformat()
    return f"{day}-{code}", f"{day} {name}"


def _latest_actuals(db: Session) -> dict[int, SevereDelayActual]:
    rows = db.execute(select(SevereDelayActual).order_by(SevereDelayActual.id)).scalars().all()
    latest: dict[int, SevereDelayActual] = {}
    for row in rows:  # 追加表，后写入即最新版本
        latest[row.case_id] = row
    return latest


def _dispatched_cases(db: Session):
    """返回每个已派工案件的首单派工时间、概率快照与码头。"""
    first_action_id = select(
        func.min(DispatchAction.id)
    ).group_by(DispatchAction.case_id).scalar_subquery()
    actions = db.execute(
        select(DispatchAction).where(DispatchAction.id.in_(first_action_id))
    ).scalars().all()
    result = []
    for action in actions:
        case = db.get(DelayCase, action.case_id)
        if not case or not case.vessel:
            continue
        created = _as_utc(action.created_at or action.completed_at or action.due_at)
        result.append({
            "case_id": case.id,
            "case_no": case.case_no,
            "terminal": case.vessel.terminal,
            "call_no": case.vessel.call_no,
            "dispatched_at": created,
            "severe_probability": action.severe_probability,
            "action_id": action.id,
        })
    return result


def _round(value, digits=4):
    return round(float(value), digits)


def build_report(db: Session) -> dict:
    ensure_schema(db)
    latest = _latest_actuals(db)
    signoffs = {
        (row.terminal, row.shift_key): row
        for row in db.execute(select(ShiftReportSignoff)).scalars().all()
    }
    groups: dict[tuple[str, str], dict] = {}

    def bucket(terminal, shift_key, shift_label):
        key = (terminal, shift_key)
        group = groups.get(key)
        if group is None:
            group = {
                "terminal": terminal,
                "shift_key": shift_key,
                "shift_label": shift_label,
                "scored": [],
                "pending": [],
                "without_probability": [],
            }
            groups[key] = group
        return group

    for item in _dispatched_cases(db):
        shift_key, shift_label = shift_of(item["dispatched_at"])
        group = bucket(item["terminal"], shift_key, shift_label)
        actual = latest.get(item["case_id"])
        entry = {
            "case_id": item["case_id"],
            "case_no": item["case_no"],
            "call_no": item["call_no"],
            "dispatched_at": item["dispatched_at"].astimezone(CST).isoformat(),
            "severe_probability": item["severe_probability"],
        }
        if item["severe_probability"] is None:
            group["without_probability"].append(entry)
        elif actual is None:
            group["pending"].append(entry)  # 未报实绩，单列且不计误差
        else:
            error = (item["severe_probability"] - float(actual.severe_actual)) ** 2
            entry.update({
                "severe_actual": actual.severe_actual,
                "squared_error": _round(error),
                "actual_revision": actual.revision,
                "recorded_by": actual.recorded_by,
                "recorded_at": _as_utc(actual.created_at).astimezone(CST).isoformat(),
            })
            group["scored"].append(entry)

    summary = []
    for (terminal, shift_key), group in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        scored = group["scored"]
        mse = _round(sum(row["squared_error"] for row in scored) / len(scored)) if scored else None
        signoff = signoffs.get((terminal, shift_key))
        summary.append({
            "terminal": terminal,
            "shift_key": shift_key,
            "shift_label": group["shift_label"],
            "scored_count": len(scored),
            "pending_count": len(group["pending"]),
            "without_probability_count": len(group["without_probability"]),
            "mse": mse,
            "signoff": None if signoff is None else {
                "signed_by": signoff.signed_by,
                "note": signoff.note,
                "signed_at": _as_utc(signoff.signed_at).astimezone(CST).isoformat(),
                "snapshot": signoff.snapshot,
            },
        })

    return {
        "severe_level": SEVERE_LEVEL,
        "timezone": "UTC+8",
        "shift_rules": "早班 06:00–14:00，中班 14:00–22:00，夜班 22:00–次日 06:00（归属开始日）",
        "groups": summary,
        "details": [groups[key] for key in sorted(groups, key=lambda k: (k[1], k[0]))],
    }


def _group_snapshot(group: dict) -> dict:
    scored = group["scored"]
    return {
        "scored_count": len(scored),
        "pending_count": len(group["pending"]),
        "without_probability_count": len(group["without_probability"]),
        "mse": _round(sum(row["squared_error"] for row in scored) / len(scored)) if scored else None,
        "scored_cases": [
            {
                "case_no": row["case_no"],
                "severe_probability": row["severe_probability"],
                "severe_actual": row["severe_actual"],
                "squared_error": row["squared_error"],
                "actual_revision": row["actual_revision"],
            }
            for row in scored
        ],
        "pending_cases": [row["case_no"] for row in group["pending"]],
    }


def record_actual(db: Session, user, case_id: int, severe_actual: int, note: str) -> dict:
    """主管登记或更正实绩；更正以新版本追加，修订号冲突时拒绝。"""
    ensure_schema(db)
    case = db.get(DelayCase, case_id)
    if not case:
        raise HTTPException(404, "延误案件不存在")
    if not case.actions:
        raise HTTPException(409, "案件尚未派发处置单，无法按派工班次归集实绩")
    previous = db.execute(
        select(SevereDelayActual)
        .where(SevereDelayActual.case_id == case_id)
        .order_by(SevereDelayActual.revision.desc())
    ).scalars().first()
    revision = (previous.revision + 1) if previous else 1
    row = SevereDelayActual(
        case_id=case_id,
        revision=revision,
        severe_actual=severe_actual,
        note=note.strip()[:500],
        recorded_by=user.username,
    )
    db.add(row)
    action = "correct_severe_actual" if previous else "record_severe_actual"
    db.add(AuditLog(
        actor=user.username, role=user.role, action=action,
        entity="severe_delay_actual", entity_id=case_id,
        detail=f"{case.case_no} rev={revision} actual={severe_actual} {note.strip()[:200]}",
    ))
    # 与班报签发串行：先拿到写锁的更正先入账，签发只能看到提交后的状态
    _serialize_writes(db)
    db.commit()
    return {"case_id": case_id, "revision": revision, "severe_actual": severe_actual}


def sign_off(db: Session, user, terminal: str, shift_key: str, note: str) -> dict:
    """签发班报：快照当前结果。与实绩写入共用表锁，保证提交顺序即入账顺序。"""
    ensure_schema(db)
    _serialize_writes(db)
    existing = db.execute(
        select(ShiftReportSignoff).where(
            ShiftReportSignoff.terminal == terminal,
            ShiftReportSignoff.shift_key == shift_key,
        )
    ).scalar_one_or_none()
    if existing:
        db.rollback()
        raise HTTPException(409, "该班报已签发，已签发数字不可覆盖；更正后的结果请看当前结果列")
    report = build_report(db)
    target_group = next(
        (g for g in report["details"] if g["terminal"] == terminal and g["shift_key"] == shift_key),
        None,
    )
    target_summary = next(
        (s for s in report["groups"] if s["terminal"] == terminal and s["shift_key"] == shift_key),
        None,
    )
    if not target_group:
        db.rollback()
        raise HTTPException(404, "该码头 / 班次下没有已派工案件，无可签发班报")
    snapshot = _group_snapshot(target_group)
    row = ShiftReportSignoff(
        terminal=terminal,
        shift_key=shift_key,
        shift_label=target_summary["shift_label"],
        snapshot=snapshot,
        signed_by=user.username,
        note=note.strip()[:500],
    )
    db.add(row)
    db.add(AuditLog(
        actor=user.username, role=user.role, action="sign_shift_report",
        entity="shift_report_signoff", entity_id=None,
        detail=f"{terminal} {shift_key} mse={snapshot['mse']} scored={snapshot['scored_count']}",
    ))
    db.commit()
    return {"terminal": terminal, "shift_key": shift_key, "snapshot": snapshot}
