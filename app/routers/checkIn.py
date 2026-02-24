from sqlalchemy import text, func
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.orm import Session
from app.db.database import get_db, SessionLocal
from app.db.models import Attendance, QrSessions, Admin
from app.routers.auth import manager
import uuid


router = APIRouter(prefix='/api', tags=["CHECKINS"])


def cleanup_old_tokens():
    db = SessionLocal()
    try:
        db.query(QrSessions).filter(
            QrSessions.is_used.is_(False),
            QrSessions.created_at < func.now() - text("interval '5 minutes'")
        ).delete(synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


@router.post("/generateQrToken", status_code=status.HTTP_201_CREATED)
def generate_qr_token(background_tasks: BackgroundTasks, db: Session = Depends(get_db), current_user: Admin = Depends(manager)):
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )

    new_entry = QrSessions()

    today_checkins = db.query(Attendance).filter(
        func.date(Attendance.check_in_time) == func.current_date()).all()

    try:
        db.add(new_entry)
        db.commit()
        db.refresh(new_entry)
        background_tasks.add_task(cleanup_old_tokens)
        return {"token": new_entry.token_id, "today_checkins": len(today_checkins)}
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Database error")


@router.post("/verifyCheckin/{scanned_token}")
def verify_checkin(
    scanned_token: str,
    db: Session = Depends(get_db),
    current_user=Depends(manager)
):

    if not current_user or not current_user.is_active:
        raise HTTPException(
            status_code=403, detail="Unauthorized or inactive user")

    try:
        valid_uuid = uuid.UUID(scanned_token)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="That is not a valid QR token format")

    session_record = db.query(QrSessions).filter(
        QrSessions.token_id == valid_uuid,
        QrSessions.is_used.is_(False),
        QrSessions.expires_at > text("now()")
    ).first()

    if not session_record:
        raise HTTPException(
            status_code=400,
            detail="Invalid, expired, or already used QR code."
        )

    already_checked_in = db.query(Attendance).filter(
        Attendance.user_id == current_user.user_id,
        func.date(Attendance.check_in_time) == func.current_date()
    ).first()

    if already_checked_in:
        raise HTTPException(
            status_code=400, detail="You have already checked in today!")

    session_record.is_used = True

    new_attendance = Attendance(
        user_id=current_user.user_id,
        token_used=session_record.token_id
    )

    try:
        db.add(new_attendance)
        db.commit()
        db.refresh(new_attendance)
        return {"message": f"Welcome, {current_user.name}!"}
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500, detail="Could not record attendance")
