from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import Trainer, Admin, User, TrainerClient, TrainersAttendance
from app.routers.auth import manager
from app.schemas.trainer_schema import TrainerOut, TrainerCreate, TrainerPublicOut
from app.schemas.user_schema import SearchQuery
from sqlalchemy import cast, String, text, func, or_
import uuid
from app.routers.auth import pwd
from datetime import datetime, timezone, timedelta


router = APIRouter(prefix='/api', tags=["TRAINERS"])


async def get_optional_user(request: Request):
    try:
        return await manager(request)
    except Exception:
        return None


@router.post("/register/trainers", status_code=status.HTTP_201_CREATED)
def create_trainers(data: TrainerCreate, db: Session = Depends(get_db), current_user: Admin = Depends(manager)):
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )

    email_normalized = data.email.strip().lower()
    existing_trainer = db.query(Trainer).filter(
        Trainer.email == email_normalized).first()
    if existing_trainer:
        raise HTTPException(status_code=400, detail="Email already exists")

    hashed_pass = pwd.hash(data.password)
    new_trainer = Trainer(
        name=data.name.strip(),
        email=email_normalized,
        phone=data.phone.strip(),
        address=data.address.strip(),
        specializations=data.specializations,
        short_bio=data.short_bio.strip(),
        password=hashed_pass,
        experience_years=data.experience_years,
        certifications=data.certifications
    )

    db.add(new_trainer)
    db.commit()
    db.refresh(new_trainer)

    return {"message": "Trainer registered successfully.", "id": new_trainer.id, "trainer_id": new_trainer.trainer_id, "is_active": new_trainer.is_active, "created_at": new_trainer.created_at, "updated_at": new_trainer.updated_at}


@router.get("/trainers", status_code=status.HTTP_200_OK)
def get_trainers(page: int = Query(1, ge=1),
                       limit: int = Query(50, ge=1),
                       db: Session = Depends(get_db),
                       current_user: User | None = Depends(get_optional_user)):

    skip = (page - 1) * limit
    is_admin = current_user and current_user.role == "admin" and current_user.is_active

    query = db.query(Trainer)

    if not is_admin:
        query = query.filter(Trainer.is_active.is_(True))

    total_trainers = query.count()
    if is_admin:
        trainers = query.order_by(
            func.lower(Trainer.name).asc(),
            Trainer.created_at.desc(),
            Trainer.trainer_id.asc()
        ).offset(skip).limit(limit).all()
    else:
        trainers = query.order_by(Trainer.created_at.desc()).offset(skip).limit(limit).all()

    trainer_ids = [trainer.trainer_id for trainer in trainers]
    client_count_rows = db.query(
        TrainerClient.trainer_id.label("trainer_id"),
        func.count(func.distinct(TrainerClient.user_id)).label("clients_count")
    ).filter(
        TrainerClient.trainer_id.in_(trainer_ids),
        TrainerClient.is_active.is_(True)
    ).group_by(
        TrainerClient.trainer_id
    ).all() if trainer_ids else []

    clients_count_map = {
        row.trainer_id: int(row.clients_count)
        for row in client_count_rows
    }

    today_attendance_rows = db.query(TrainersAttendance).filter(
        TrainersAttendance.trainer_id.in_(trainer_ids),
        func.date(TrainersAttendance.check_in_time) == func.current_date()
    ).order_by(
        TrainersAttendance.check_in_time.desc()
    ).all() if trainer_ids else []

    today_attendance_map = {}
    for attendance in today_attendance_rows:
        if attendance.trainer_id not in today_attendance_map:
            today_attendance_map[attendance.trainer_id] = attendance

    now_utc = datetime.now(timezone.utc)

    if is_admin:
        safe_trainers = []
        for trainer in trainers:
            payload = TrainerOut.model_validate(trainer).model_dump(mode="json")
            payload["clients_count"] = clients_count_map.get(trainer.trainer_id, 0)
            today_attendance = today_attendance_map.get(trainer.trainer_id)
            is_checked_in_today = bool(
                today_attendance and
                today_attendance.auto_checkout and
                (
                    today_attendance.check_out_time is None or
                    today_attendance.check_out_time > now_utc
                )
            )
            payload["has_attendance_today"] = bool(today_attendance)
            payload["is_checked_in_today"] = is_checked_in_today
            payload["today_check_in_time"] = today_attendance.check_in_time if today_attendance else None
            payload["today_check_out_time"] = today_attendance.check_out_time if today_attendance else None
            safe_trainers.append(payload)
    else:
        safe_trainers = []
        for trainer in trainers:
            payload = TrainerPublicOut.model_validate(trainer).model_dump(mode="json")
            payload["clients_count"] = clients_count_map.get(trainer.trainer_id, 0)
            safe_trainers.append(payload)

    return {
        "trainers": safe_trainers,
        "total_trainers": total_trainers,
        "page": page,
        "limit": limit,
        "access": "admin" if is_admin else "public"
    }


@router.get("/member/myTrainer", status_code=status.HTTP_200_OK)
def get_member_trainer(
    db: Session = Depends(get_db),
    current_user: User = Depends(manager)
):
    if not current_user or current_user.role != "member":
        raise HTTPException(status_code=403, detail="Member role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )

    assignment = db.query(TrainerClient).filter(
        TrainerClient.user_id == current_user.user_id,
        TrainerClient.is_active.is_(True)
    ).order_by(TrainerClient.assign_at.desc()).first()

    if not assignment:
        return {"has_trainer": False, "trainer": None}

    trainer = db.query(Trainer).filter(
        Trainer.trainer_id == assignment.trainer_id
    ).first()

    if not trainer or not trainer.is_active:
        assignment.is_active = False
        db.commit()
        return {"has_trainer": False, "trainer": None}

    return {
        "has_trainer": True,
        "trainer": {
            "trainer_id": str(trainer.trainer_id),
            "name": trainer.name,
            "phone": trainer.phone,
            "specializations": trainer.specializations,
            "short_bio": trainer.short_bio,
            "experience_years": trainer.experience_years,
            "certifications": trainer.certifications or [],
            "profile_photo": getattr(trainer, "profile_photo", None),
            "assigned_at": assignment.assign_at,
            "is_active": trainer.is_active
        }
    }


@router.get("/member/trainerHistory", status_code=status.HTTP_200_OK)
def get_member_trainer_history(
    db: Session = Depends(get_db),
    current_user: User = Depends(manager)
):
    if not current_user or current_user.role != "member":
        raise HTTPException(status_code=403, detail="Member role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )

    assignments = db.query(TrainerClient).filter(
        TrainerClient.user_id == current_user.user_id
    ).order_by(
        TrainerClient.assign_at.desc()
    ).all()

    if not assignments:
        return {"history": []}

    trainer_ids = list({assignment.trainer_id for assignment in assignments if assignment.trainer_id})
    trainers = db.query(Trainer).filter(
        Trainer.trainer_id.in_(trainer_ids)
    ).all() if trainer_ids else []
    trainer_map = {trainer.trainer_id: trainer for trainer in trainers}

    history = []
    for assignment in assignments:
        trainer = trainer_map.get(assignment.trainer_id)
        history.append({
            "trainer_id": str(assignment.trainer_id),
            "name": trainer.name if trainer else "Unknown Trainer",
            "specializations": trainer.specializations if trainer else [],
            "experience_years": trainer.experience_years if trainer else None,
            "assigned_at": assignment.assign_at,
            "is_active": bool(assignment.is_active),
            "trainer_available": bool(trainer and trainer.is_active)
        })

    return {"history": history}


@router.post("/admin/trainerCheckin/{trainer_id}")
def admin_checkin_trainer(
    trainer_id: str,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(manager)
):
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )

    try:
        valid_trainer_id = uuid.UUID(trainer_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid trainer ID format"
        )

    trainer = db.query(Trainer).filter(Trainer.trainer_id == valid_trainer_id).first()
    if not trainer:
        raise HTTPException(status_code=404, detail="Trainer not found")

    if not trainer.is_active:
        raise HTTPException(status_code=403, detail="Trainer account is inactive")

    existing_today = db.query(TrainersAttendance).filter(
        TrainersAttendance.trainer_id == valid_trainer_id,
        func.date(TrainersAttendance.check_in_time) == func.current_date()
    ).first()

    if existing_today:
        return {
            "message": "Trainer already checked in today",
            "check_in_time": existing_today.check_in_time
        }

    check_out_time = datetime.now(timezone.utc) + timedelta(hours=8)
    trainer_attendance = TrainersAttendance(
        trainer_id=valid_trainer_id,
        check_out_time=check_out_time
    )

    db.add(trainer_attendance)
    db.commit()
    db.refresh(trainer_attendance)

    return {
        "message": f"{trainer.name} checked in successfully",
        "check_in_time": trainer_attendance.check_in_time
    }


@router.post("/admin/trainerCheckout/{trainer_id}", status_code=status.HTTP_200_OK)
def admin_checkout_trainer(
    trainer_id: str,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(manager)
):
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )

    try:
        valid_trainer_id = uuid.UUID(trainer_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid trainer ID format"
        )

    trainer = db.query(Trainer).filter(Trainer.trainer_id == valid_trainer_id).first()
    if not trainer:
        raise HTTPException(status_code=404, detail="Trainer not found")

    active_attendance = db.query(TrainersAttendance).filter(
        TrainersAttendance.trainer_id == valid_trainer_id,
        func.date(TrainersAttendance.check_in_time) == func.current_date(),
        TrainersAttendance.auto_checkout.is_(True),
        or_(
            TrainersAttendance.check_out_time.is_(None),
            TrainersAttendance.check_out_time > func.now()
        )
    ).order_by(
        TrainersAttendance.check_in_time.desc()
    ).first()

    if not active_attendance:
        raise HTTPException(status_code=400, detail="Trainer is not currently checked in")

    active_attendance.check_out_time = datetime.now(timezone.utc)
    active_attendance.auto_checkout = False
    db.commit()
    db.refresh(active_attendance)

    return {
        "message": f"{trainer.name} checked out successfully",
        "check_out_time": active_attendance.check_out_time
    }


@router.get("/admin/trainerRecords/{trainer_id}", status_code=status.HTTP_200_OK)
def get_trainer_records_for_admin(
    trainer_id: str,
    attendance_limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: Admin = Depends(manager)
):
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )

    try:
        valid_trainer_id = uuid.UUID(trainer_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid trainer ID format"
        )

    trainer = db.query(Trainer).filter(Trainer.trainer_id == valid_trainer_id).first()
    if not trainer:
        raise HTTPException(status_code=404, detail="Trainer not found")

    assigned_rows = db.query(TrainerClient, User).join(
        User, User.user_id == TrainerClient.user_id
    ).filter(
        TrainerClient.trainer_id == valid_trainer_id,
        TrainerClient.is_active.is_(True)
    ).order_by(
        TrainerClient.assign_at.desc()
    ).all()

    assigned_members = []
    for assignment, member in assigned_rows:
        assigned_members.append({
            "user_id": str(member.user_id),
            "name": member.name,
            "email": member.email,
            "phone": member.phone,
            "assigned_at": assignment.assign_at,
            "is_active": member.is_active
        })

    attendance_rows = db.query(TrainersAttendance).filter(
        TrainersAttendance.trainer_id == valid_trainer_id
    ).order_by(
        TrainersAttendance.check_in_time.desc()
    ).limit(attendance_limit).all()

    attendance_history = []
    for attendance in attendance_rows:
        duration_minutes = None
        if attendance.check_in_time and attendance.check_out_time and attendance.check_out_time >= attendance.check_in_time:
            duration_minutes = int(
                (attendance.check_out_time - attendance.check_in_time).total_seconds() // 60
            )

        attendance_history.append({
            "attendance_id": attendance.id,
            "check_in_time": attendance.check_in_time,
            "check_out_time": attendance.check_out_time,
            "duration_minutes": duration_minutes,
            "auto_checkout": bool(attendance.auto_checkout)
        })

    return {
        "trainer_id": str(trainer.trainer_id),
        "trainer_name": trainer.name,
        "assigned_members": assigned_members,
        "assigned_members_count": len(assigned_members),
        "attendance_history": attendance_history,
        "attendance_count": len(attendance_history)
    }


@router.post("/member/bookTrainer/{trainer_id}", status_code=status.HTTP_201_CREATED)
def book_personal_trainer(
    trainer_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(manager)
):
    if not current_user or current_user.role != "member":
        raise HTTPException(status_code=403, detail="Member role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )

    try:
        valid_trainer_id = uuid.UUID(trainer_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid trainer ID format"
        )

    trainer = db.query(Trainer).filter(
        Trainer.trainer_id == valid_trainer_id,
        Trainer.is_active.is_(True)
    ).first()

    if not trainer:
        raise HTTPException(status_code=404, detail="Trainer not found or inactive")

    existing_assignment = db.query(TrainerClient).filter(
        TrainerClient.user_id == current_user.user_id,
        TrainerClient.is_active.is_(True)
    ).order_by(TrainerClient.assign_at.desc()).first()

    if existing_assignment:
        assigned_trainer = db.query(Trainer).filter(
            Trainer.trainer_id == existing_assignment.trainer_id
        ).first()

        if not assigned_trainer or not assigned_trainer.is_active:
            existing_assignment.is_active = False
            db.commit()
            existing_assignment = None

    if existing_assignment:
        if existing_assignment.trainer_id == valid_trainer_id:
            return {
                "message": "This trainer is already assigned to you",
                "trainer_id": str(valid_trainer_id),
                "trainer_name": trainer.name
            }
        raise HTTPException(
            status_code=409,
            detail="You already have a personal trainer assigned"
        )

    new_assignment = TrainerClient(
        trainer_id=valid_trainer_id,
        user_id=current_user.user_id
    )

    db.add(new_assignment)
    db.commit()
    db.refresh(new_assignment)

    return {
        "message": "Personal trainer booked successfully",
        "trainer_id": str(trainer.trainer_id),
        "trainer_name": trainer.name,
        "assigned_at": new_assignment.assign_at
    }


@router.post("/member/changeTrainer/{trainer_id}", status_code=status.HTTP_201_CREATED)
def change_personal_trainer(
    trainer_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(manager)
):
    if not current_user or current_user.role != "member":
        raise HTTPException(status_code=403, detail="Member role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )

    try:
        valid_trainer_id = uuid.UUID(trainer_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid trainer ID format"
        )

    trainer = db.query(Trainer).filter(
        Trainer.trainer_id == valid_trainer_id,
        Trainer.is_active.is_(True)
    ).first()
    if not trainer:
        raise HTTPException(status_code=404, detail="Trainer not found or inactive")

    active_assignments = db.query(TrainerClient).filter(
        TrainerClient.user_id == current_user.user_id,
        TrainerClient.is_active.is_(True)
    ).all()

    if len(active_assignments) == 1 and active_assignments[0].trainer_id == valid_trainer_id:
        return {
            "message": "This trainer is already assigned to you",
            "trainer_id": str(valid_trainer_id),
            "trainer_name": trainer.name
        }

    for assignment in active_assignments:
        assignment.is_active = False

    new_assignment = TrainerClient(
        trainer_id=valid_trainer_id,
        user_id=current_user.user_id
    )
    db.add(new_assignment)
    db.commit()
    db.refresh(new_assignment)

    return {
        "message": "Personal trainer changed successfully",
        "trainer_id": str(trainer.trainer_id),
        "trainer_name": trainer.name,
        "assigned_at": new_assignment.assign_at
    }


@router.patch("/member/removeTrainer", status_code=status.HTTP_200_OK)
def remove_personal_trainer(
    db: Session = Depends(get_db),
    current_user: User = Depends(manager)
):
    if not current_user or current_user.role != "member":
        raise HTTPException(status_code=403, detail="Member role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )

    active_assignments = db.query(TrainerClient).filter(
        TrainerClient.user_id == current_user.user_id,
        TrainerClient.is_active.is_(True)
    ).all()

    if not active_assignments:
        return {"message": "No active trainer assigned", "updated": 0}

    for assignment in active_assignments:
        assignment.is_active = False

    db.commit()

    return {"message": "Trainer removed successfully", "updated": len(active_assignments)}


@router.get("/trainer", status_code=status.HTTP_200_OK)
def get_user(query: SearchQuery = Depends(), db: Session = Depends(get_db), current_user: Admin = Depends(manager)):

    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )

    search_val = str(query.search).strip()
    trainer = db.query(Trainer).filter(
        (Trainer.email == search_val) | (
            cast(Trainer.trainer_id, String) == search_val)
    ).first()

    if not trainer:
        raise HTTPException(status_code=404, detail="Trainer not found")

    clients_count = db.query(
        func.count(func.distinct(TrainerClient.user_id))
    ).filter(
        TrainerClient.trainer_id == trainer.trainer_id,
        TrainerClient.is_active.is_(True)
    ).scalar() or 0

    today_attendance = db.query(TrainersAttendance).filter(
        TrainersAttendance.trainer_id == trainer.trainer_id,
        func.date(TrainersAttendance.check_in_time) == func.current_date()
    ).order_by(
        TrainersAttendance.check_in_time.desc()
    ).first()

    now_utc = datetime.now(timezone.utc)
    is_checked_in_today = bool(
        today_attendance and
        today_attendance.auto_checkout and
        (
            today_attendance.check_out_time is None or
            today_attendance.check_out_time > now_utc
        )
    )

    payload = TrainerOut.model_validate(trainer).model_dump(mode="json")
    payload["clients_count"] = int(clients_count)
    payload["has_attendance_today"] = bool(today_attendance)
    payload["is_checked_in_today"] = is_checked_in_today
    payload["today_check_in_time"] = today_attendance.check_in_time if today_attendance else None
    payload["today_check_out_time"] = today_attendance.check_out_time if today_attendance else None

    return {
        "trainer": [payload]
    }


@router.patch("/trainer/toggleBlock", status_code=status.HTTP_200_OK)
def block_user(trainer_id: str, db: Session = Depends(get_db), current_user: Admin = Depends(manager)):
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )

    try:
        valid_trainer_id = uuid.UUID(trainer_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Invalid ID format, a valid UUID is required!")

    trainer = db.query(Trainer).filter(
        Trainer.trainer_id == valid_trainer_id).first()
    if not trainer:
        raise HTTPException(status_code=404, detail="Trainer not found!")

    trainer.is_active = not trainer.is_active
    db.commit()
    return {"message": "Status updated successfully"}


@router.get("/totalTrainers", status_code=status.HTTP_200_OK)
def get_total_trainers(db:Session=Depends(get_db), current_user: Admin = Depends(manager)):
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )
    
    stats = db.execute(
        text("SELECT count FROM site_statistics WHERE label = 'active_trainers'")).fetchone()
    active_trainers = stats[0] if stats else 0

    return {"active_trainers": active_trainers}
