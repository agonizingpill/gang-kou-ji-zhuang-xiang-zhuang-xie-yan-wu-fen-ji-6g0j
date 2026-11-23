from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from src.database import Base, SessionLocal, engine
from src.models import User, VesselCall, ContainerCheck, GateEvent, DelayCase, DispatchAction, SevereDelayActual, ShiftReportSignoff
from src.security import hash_password


def dt(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=timezone.utc)

def bj(day, hour, minute=0):
    """按北京墙上时间构造时刻，存储为 UTC（减 8 小时）。"""
    return datetime(2026, 11, day, hour, minute, tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)

def seed():
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        users = [("operator", "林岚", "operator123", "operator"), ("supervisor", "顾航", "supervisor123", "supervisor"), ("viewer", "周宁", "viewer123", "viewer")]
        for username, display, password, role in users:
            if not db.execute(select(User).where(User.username == username)).scalar_one_or_none():
                db.add(User(username=username, display_name=display, password_hash=hash_password(password), role=role))
        calls = [
            ("CALL-HAI-20261121", "海岚星", "海陵港CCT", "C12", dt(21, 6), dt(21, 14), "准班窗口，优先处理冷藏箱"),
            ("CALL-TRI-20261121", "天际三号", "海陵港CCT", "C08", dt(21, 8), dt(21, 13), "查验箱集中在上午窗口"),
            ("CALL-PEARL-20261121", "珠江门", "海陵港CCT", "C15", dt(21, 9), dt(21, 16), "危险品箱需单独排队"),
            ("CALL-NORTH-20261121", "北辰航运", "海陵港CCT", "C03", dt(21, 11), dt(21, 19), "晚靠泊，观察潮汐"),
        ]
        for call_no, name, terminal, berth, eta, etd, notes in calls:
            if not db.execute(select(VesselCall).where(VesselCall.call_no == call_no)).scalar_one_or_none():
                db.add(VesselCall(call_no=call_no, vessel_name=name, terminal=terminal, berth=berth, eta=eta, etd=etd, window_status="planned", state="open", notes=notes))
        db.flush()
        vessel_by_no = {v.call_no: v for v in db.execute(select(VesselCall)).scalars().all()}
        containers = [
            ("MSCU100001", "CALL-HAI-20261121", "22G1", "dry", 18.4, "finished"),
            ("MSCU100002", "CALL-HAI-20261121", "45R1", "reefer", 21.2, "finished"),
            ("MSCU100003", "CALL-HAI-20261121", "22G1", "dry", 16.1, "finished"),
            ("MSCU200001", "CALL-TRI-20261121", "45G1", "dry", 19.7, "in_progress"),
            ("MSCU200002", "CALL-TRI-20261121", "22G1", "dry", 17.9, "finished"),
            ("MSCU200003", "CALL-TRI-20261121", "45R1", "reefer", 23.1, "finished"),
            ("MSCU300001", "CALL-PEARL-20261121", "22G1", "hazmat", 20.2, "finished"),
            ("MSCU300002", "CALL-PEARL-20261121", "45R1", "reefer", 22.7, "finished"),
            ("MSCU300003", "CALL-PEARL-20261121", "22G1", "dry", 18.8, "finished"),
            ("MSCU400001", "CALL-NORTH-20261121", "22G1", "dry", 15.2, "finished"),
            ("MSCU400002", "CALL-NORTH-20261121", "45R1", "reefer", 22.2, "finished"),
        ]
        for no, call_no, iso, cargo, weight, customs in containers:
            if not db.execute(select(ContainerCheck).where(ContainerCheck.container_no == no)).scalar_one_or_none():
                db.add(ContainerCheck(vessel_id=vessel_by_no[call_no].id, container_no=no, iso_type=iso, cargo_class=cargo, weight_t=weight, customs_status=customs, seal_status="intact", check_state="verified" if customs == "finished" else "pending"))
        db.flush()
        c_by_no = {c.container_no: c for c in db.execute(select(ContainerCheck)).scalars().all()}
        gates = [
            ("MSCU100001", "GATE-H-001", "G12", "in", 8, dt(21, 6, 22)), ("MSCU100001", "GATE-H-002", "G12", "out", 12, dt(21, 7, 10)),
            ("MSCU100002", "GATE-H-003", "G14", "in", 18, dt(21, 6, 35)), ("MSCU100003", "GATE-H-004", "G12", "in", 10, dt(21, 6, 42)),
            ("MSCU200001", "GATE-T-001", "G08", "in", 31, dt(21, 8, 25)), ("MSCU200002", "GATE-T-002", "G08", "in", 28, dt(21, 8, 32)), ("MSCU200003", "GATE-T-003", "G09", "in", 24, dt(21, 8, 45)),
            ("MSCU300001", "GATE-P-001", "G15", "in", 42, dt(21, 9, 25)), ("MSCU300002", "GATE-P-002", "G15", "in", 38, dt(21, 9, 28)), ("MSCU300003", "GATE-P-003", "G15", "in", 35, dt(21, 9, 36)),
            ("MSCU400001", "GATE-N-001", "G03", "in", 16, dt(21, 11, 20)), ("MSCU400002", "GATE-ERR-77", "G03", "in", 23, dt(21, 11, 25)),
        ]
        for no, ref, gate, typ, dwell, when in gates:
            if not db.execute(select(GateEvent).where(GateEvent.event_ref == ref)).scalar_one_or_none():
                db.add(GateEvent(container_id=c_by_no[no].id, event_ref=ref, gate_code=gate, event_type=typ, event_time=when, dwell_min=dwell, status="active", note="闸口采集"))
        db.flush()
        if not db.execute(select(GateEvent).where(GateEvent.event_ref == "GATE-ERR-77-R2")).scalar_one_or_none():
            db.add(GateEvent(container_id=c_by_no["MSCU400002"].id, event_ref="GATE-ERR-77-R2", gate_code="G03", event_type="in", event_time=dt(21, 11, 25), dwell_min=23, status="active", note="重复上报，待主管冲正"))
        cases = [("CASE-HAI-001", "CALL-HAI-20261121", "C12卸船队列出现短时拥堵，优先保护冷藏箱"), ("CASE-TRI-002", "CALL-TRI-20261121", "查验箱未回执，不能直接按普通延误放行"), ("CASE-NORTH-004", "CALL-NORTH-20261121", "同箱闸口重复记录，需冲正后重新分级")]
        for case_no, call_no, reason in cases:
            if not db.execute(select(DelayCase).where(DelayCase.case_no == case_no)).scalar_one_or_none():
                db.add(DelayCase(vessel_id=vessel_by_no[call_no].id, case_no=case_no, requested_level="pending", status="draft", reason=reason))
        db.flush()
        if not db.execute(select(DispatchAction).where(DispatchAction.action_type == "冷藏箱优先卸船")).scalar_one_or_none():
            case = db.execute(select(DelayCase).where(DelayCase.case_no == "CASE-HAI-001")).scalar_one()
            case.status = "dispatched"; case.final_level = "major"
            case.model_output = {"label": "major", "confidence": 0.84, "seed": 42,
                                 "probabilities": {"normal": 0.04, "watch": 0.08, "major": 0.84, "critical": 0.04},
                                 "note": "seed演示结果"}
            db.add(DispatchAction(case_id=case.id, action_type="冷藏箱优先卸船", assignee="C12夜班桥吊组", due_at=bj(21, 8, 30), status="queued", instruction="将MSCU100002置于前两吊，完成后回报调度台", created_at=bj(21, 7, 15), severe_probability=0.04))
        if not db.execute(select(DispatchAction).where(DispatchAction.action_type == "查验单证补录")).scalar_one_or_none():
            case = db.execute(select(DelayCase).where(DelayCase.case_no == "CASE-TRI-002")).scalar_one()
            case.final_level = "watch"; case.status = "classified"
            case.model_output = {"label": "watch", "confidence": 0.71, "seed": 42,
                                 "probabilities": {"normal": 0.12, "watch": 0.71, "major": 0.14, "critical": 0.03}}
            db.add(DispatchAction(case_id=case.id, action_type="查验单证补录", assignee="海关联络岗", due_at=bj(21, 10), status="queued", instruction="等查验结束后补传查验回执", created_at=bj(21, 8, 40), severe_probability=0.03))
        db.flush()
        _seed_shift_report_demo(db, vessel_by_no)
        db.commit()
    finally:
        db.close()


def _model_output(label: str, critical: float):
    """构造带完整概率分布的演示模型输出。"""
    rest = round(1.0 - critical, 4)
    probs = {"normal": 0.0, "watch": 0.0, "major": 0.0, "critical": round(critical, 4)}
    probs[label if label != "critical" else "major"] = rest if label != "critical" else 0.0
    return {"label": label, "confidence": round(max(probs.values()), 4), "seed": 42,
            "probabilities": probs, "note": "班报演示数据"}


def _seed_shift_report_demo(db, vessel_by_no):
    """跨码头、跨班次的班报演示：含迟到实绩、更正与已签发快照对照。"""
    if db.execute(select(SevereDelayActual)).first() or db.execute(select(ShiftReportSignoff)).first():
        return
    demo_cases = [
        # 案件号, 船次, 模型等级, 严重概率, 派工时刻(北京墙上时间), 指令, 责任人
        ("CASE-HAI-S1", "CALL-HAI-20261121", "critical", 0.82, bj(21, 7, 20), "桥吊增援", "C12早班桥吊组"),
        ("CASE-HAI-S2", "CALL-HAI-20261121", "major", 0.35, bj(21, 9, 5), "堆场疏解", "C12早班堆场组"),
        ("CASE-HAI-S3", "CALL-HAI-20261121", "watch", 0.12, bj(21, 12, 40), "窗口监控", "C12早班调度岗"),
        ("CASE-TRI-S1", "CALL-TRI-20261121", "critical", 0.66, bj(21, 14, 25), "查验加急", "C08中班海关联络岗"),
        ("CASE-TRI-S2", "CALL-TRI-20261121", "major", 0.28, bj(21, 17, 10), "单证复核", "C08中班单证岗"),
        ("CASE-PEARL-N1", "CALL-PEARL-20261121", "critical", 0.74, bj(21, 22, 30), "危险品隔离", "C15夜班应急组"),
        ("CASE-NORTH-N2", "CALL-NORTH-20261121", "watch", 0.09, bj(22, 1, 15), "晚靠泊值守", "C03夜班调度岗"),
    ]
    case_by_no = {}
    for case_no, call_no, label, critical, dispatched, action_type, assignee in demo_cases:
        if db.execute(select(DelayCase).where(DelayCase.case_no == case_no)).scalar_one_or_none():
            continue
        case = DelayCase(vessel_id=vessel_by_no[call_no].id, case_no=case_no,
                         requested_level=label, final_level=label, status="dispatched",
                         reason="班报演示案件", model_output=_model_output(label, critical))
        db.add(case); db.flush()
        db.add(DispatchAction(case_id=case.id, action_type=action_type, assignee=assignee,
                              due_at=dispatched + timedelta(hours=2), status="queued",
                              instruction="班报演示处置指令", created_at=dispatched,
                              severe_probability=round(critical, 4)))
        case_by_no[case_no] = case
    db.flush()

    def add_actual(case_no, revision, value, when, note):
        db.add(SevereDelayActual(case_id=case_by_no[case_no].id, revision=revision,
                                 severe_actual=value, note=note, recorded_by="supervisor",
                                 created_at=when))

    # 海陵港CCT 早班（2026-11-21-A）：两条已报实绩 + 一条未报；S2 先报 0 后更正为 1
    add_actual("CASE-HAI-S1", 1, 1, bj(21, 9, 0), "现场确认严重延误")
    add_actual("CASE-HAI-S2", 1, 0, bj(21, 11, 0), "初判未达严重延误")
    add_actual("CASE-HAI-S2", 2, 1, bj(21, 13, 30), "主管复查后更正为严重延误")
    # HAI-S3 不报实绩，演示“未报实绩单列、不计入误差”
    # 中班：TRI-S1 为晚到的实绩（中班派工，次日才补录，仍归原班次）
    add_actual("CASE-TRI-S1", 1, 1, bj(22, 9, 12), "迟到补录：查验确实导致严重延误")
    add_actual("CASE-TRI-S2", 1, 0, bj(21, 18, 0), "未达严重延误")
    # 夜班两条实绩
    add_actual("CASE-PEARL-N1", 1, 1, bj(22, 2, 0), "危险品船期严重延误")
    add_actual("CASE-NORTH-N2", 1, 0, bj(22, 4, 30), "晚靠泊未构成严重延误")
    db.flush()

    # 中班在迟到实绩补录前签发：快照冻结签发时数字（TRI-S1 当时尚未补录，计为未报）
    if not db.execute(select(ShiftReportSignoff).where(
            ShiftReportSignoff.terminal == "海陵港CCT",
            ShiftReportSignoff.shift_key == "2026-11-21-B")).scalar_one_or_none():
        db.add(ShiftReportSignoff(
            terminal="海陵港CCT", shift_key="2026-11-21-B", shift_label="2026-11-21 中班",
            signed_by="supervisor", note="中班交班签发，TRI-S1 实绩待补录",
            snapshot={
                "scored_count": 1, "pending_count": 1, "without_probability_count": 0,
                "mse": round((0.28 - 0.0) ** 2, 4),
                "scored_cases": [{
                    "case_no": "CASE-TRI-S2", "severe_probability": 0.28,
                    "severe_actual": 0, "squared_error": round((0.28 - 0.0) ** 2, 4),
                    "actual_revision": 1,
                }],
                "pending_cases": ["CASE-TRI-S1"],
            },
            signed_at=bj(21, 22, 5),
        ))

if __name__ == "__main__":
    seed()
