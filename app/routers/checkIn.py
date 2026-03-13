from sqlalchemy import text, func, or_
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Query
from sqlalchemy.orm import Session
from app.db.database import get_db, SessionLocal
from app.db.models import Attendance, QrSessions, Admin, User
from app.schemas.checkin_schema import ManualCheckInRequest
from app.routers.auth import manager
from datetime import date, timedelta, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
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


def format_hour_window(hour_value: int) -> str:
    start_hour = hour_value % 24
    end_hour = (start_hour + 1) % 24
    start_label = datetime(2000, 1, 1, start_hour).strftime("%I %p").lstrip("0")
    end_label = datetime(2000, 1, 1, end_hour).strftime("%I %p").lstrip("0")
    return f"{start_label} - {end_label}"


def normalize_timezone(tz: str) -> str:
    cleaned_tz = (tz or "UTC").strip()
    try:
        ZoneInfo(cleaned_tz)
    except ZoneInfoNotFoundError:
        return "UTC"
    return cleaned_tz


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

    if current_user.role == "member" and getattr(current_user, "email_verified", True) is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email not verified. Please verify your email to continue."
        )

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

    checkoutTime = datetime.now(timezone.utc) + timedelta(hours=6)
    new_attendance = Attendance(
        user_id=current_user.user_id,
        token_used=session_record.token_id,
        check_out_time=checkoutTime
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


@router.post("/manualCheckinByEmail", status_code=status.HTTP_201_CREATED)
def manual_checkin_by_email(
    payload: ManualCheckInRequest,
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

    if getattr(current_user, "email_verified", True) is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email not verified. Please verify your email to continue."
        )

    member_email = payload.email.strip().lower()

    member = db.query(User).filter(User.email == member_email).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    if not member.is_active:
        raise HTTPException(
            status_code=403, detail="Member account is inactive")

    already_checked_in = db.query(Attendance).filter(
        Attendance.user_id == member.user_id,
        func.date(Attendance.check_in_time) == func.current_date()
    ).first()

    if already_checked_in:
        raise HTTPException(
            status_code=400, detail="This member has already checked in today"
        )

    checkoutTime = datetime.now(timezone.utc) + timedelta(hours=6)

    manual_attendance = Attendance(
        user_id=member.user_id,
        verified_by_admin=True,
        check_out_time=checkoutTime
    )

    try:
        db.add(manual_attendance)
        db.commit()
        db.refresh(manual_attendance)

        today_checkins = db.query(Attendance).filter(
            func.date(Attendance.check_in_time) == func.current_date()
        ).count()

        return {
            "message": f"{member.name} checked in successfully",
            "member_user_id": str(member.user_id),
            "member_name": member.name,
            "member_email": member.email,
            "member_profile_photo": getattr(member, "profile_photo", None),
            "checked_in_at": manual_attendance.check_in_time,
            "today_checkins": today_checkins
        }
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500, detail="Could not record attendance")


@router.get("/todayCheckins", status_code=status.HTTP_200_OK)
def get_today_checkins(
    db: Session = Depends(get_db),
    current_user=Depends(manager)
):
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )

    if getattr(current_user, "email_verified", True) is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email not verified. Please verify your email to continue."
        )

    today_checkins = db.query(Attendance).filter(
        func.date(Attendance.check_in_time) == func.current_date()).all()

    return {"today_checkins": len(today_checkins)}


@router.get("/weeklyAttendance", status_code=status.HTTP_200_OK)
def get_weekly_attendance(
    db: Session = Depends(get_db),
    current_user=Depends(manager)
):
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )

    today = date.today()
    start_of_week = today - timedelta(days=today.weekday())

    weekly_attendance = []
    for day_offset in range(7):
        day_date = start_of_week + timedelta(days=day_offset)
        checkins_count = db.query(func.count(Attendance.id)).filter(
            func.date(Attendance.check_in_time) == day_date
        ).scalar() or 0

        weekly_attendance.append({
            "day": day_date.strftime("%a"),
            "date": day_date.isoformat(),
            "count": int(checkins_count)
        })

    return {"weekly_attendance": weekly_attendance}


@router.get("/dashboardInsights", status_code=status.HTTP_200_OK)
def get_dashboard_insights(
    db: Session = Depends(get_db),
    tz: str = Query("UTC", min_length=1, max_length=64),
    current_user: Admin = Depends(manager)
):
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )

    client_tz = normalize_timezone(tz)
    local_today = datetime.now(ZoneInfo(client_tz)).date()
    start_of_7_day_window = local_today - timedelta(days=6)
    localized_now = func.timezone(client_tz, func.now())
    localized_check_in_time = func.timezone(client_tz, Attendance.check_in_time)
    localized_30_day_cutoff = localized_now - text("interval '30 days'")

    total_members = db.query(func.count(User.id)).scalar() or 0

    hour_bucket = func.extract("hour", localized_check_in_time)
    peak_hour_rows = db.query(
        hour_bucket.label("hour"),
        func.count(Attendance.id).label("count")
    ).filter(
        localized_check_in_time >= localized_30_day_cutoff
    ).group_by(
        hour_bucket
    ).order_by(
        func.count(Attendance.id).desc(),
        hour_bucket.asc()
    ).all()

    if peak_hour_rows:
        peak_hour_value = int(float(peak_hour_rows[0].hour))
        peak_hour_count = int(peak_hour_rows[0].count)
        peak_hour_label = format_hour_window(peak_hour_value)
    else:
        peak_hour_value = None
        peak_hour_count = 0
        peak_hour_label = "N/A"

    avg_active_minutes_value = db.query(
        func.avg(
            func.extract("epoch", Attendance.check_out_time - Attendance.check_in_time) / 60.0
        )
    ).filter(
        localized_check_in_time >= localized_30_day_cutoff,
        Attendance.check_out_time.isnot(None),
        Attendance.check_out_time >= Attendance.check_in_time,
        or_(
            Attendance.auto_checkout.is_(False),
            Attendance.check_out_time <= func.now()
        )
    ).scalar()

    avg_active_minutes = round(float(avg_active_minutes_value), 1) if avg_active_minutes_value else 0.0

    active_members_last_30_days = db.query(
        func.count(func.distinct(Attendance.user_id))
    ).filter(
        localized_check_in_time >= localized_30_day_cutoff
    ).scalar() or 0

    inactive_members_last_30_days = max(int(total_members) - int(active_members_last_30_days), 0)
    member_engagement_rate = round(
        (active_members_last_30_days / total_members) * 100, 1
    ) if total_members else 0.0

    new_members_this_month = db.query(
        func.count(User.id)
    ).filter(
        func.timezone(client_tz, User.created_at) >= func.date_trunc("month", localized_now)
    ).scalar() or 0

    sessions_last_30_days = db.query(
        func.count(Attendance.id)
    ).filter(
        localized_check_in_time >= localized_30_day_cutoff
    ).scalar() or 0

    manual_checkins_last_30_days = db.query(
        func.count(Attendance.id)
    ).filter(
        localized_check_in_time >= localized_30_day_cutoff,
        Attendance.verified_by_admin.is_(True)
    ).scalar() or 0

    manual_checkin_rate = round(
        (manual_checkins_last_30_days / sessions_last_30_days) * 100, 1
    ) if sessions_last_30_days else 0.0

    avg_visits_per_active_member = round(
        sessions_last_30_days / active_members_last_30_days, 1
    ) if active_members_last_30_days else 0.0

    today_hour_bucket = func.extract("hour", localized_check_in_time)
    today_hour_rows = db.query(
        today_hour_bucket.label("hour"),
        func.count(Attendance.id).label("count")
    ).filter(
        func.date(localized_check_in_time) == local_today
    ).group_by(
        today_hour_bucket
    ).all()

    today_hour_map = {
        int(float(row.hour)): int(row.count)
        for row in today_hour_rows
    }
    hourly_checkins_today = [
        {
            "hour": f"{hour:02d}:00",
            "count": today_hour_map.get(hour, 0)
        }
        for hour in range(24)
    ]

    daily_duration_rows = db.query(
        func.date(localized_check_in_time).label("day"),
        func.avg(
            func.extract("epoch", Attendance.check_out_time - Attendance.check_in_time) / 60.0
        ).label("avg_minutes")
    ).filter(
        func.date(localized_check_in_time) >= start_of_7_day_window,
        Attendance.check_out_time.isnot(None),
        Attendance.check_out_time >= Attendance.check_in_time,
        or_(
            Attendance.auto_checkout.is_(False),
            Attendance.check_out_time <= func.now()
        )
    ).group_by(
        func.date(localized_check_in_time)
    ).all()

    duration_map = {
        row.day: round(float(row.avg_minutes), 1)
        for row in daily_duration_rows
        if row.avg_minutes is not None
    }

    daily_avg_session_duration = []
    for day_offset in range(7):
        day_date = start_of_7_day_window + timedelta(days=day_offset)
        daily_avg_session_duration.append({
            "day": day_date.strftime("%a"),
            "date": day_date.isoformat(),
            "avg_minutes": duration_map.get(day_date, 0)
        })

    return {
        "timezone": client_tz,
        "summary": {
            "peak_hour": peak_hour_value,
            "peak_hour_label": peak_hour_label,
            "peak_hour_count": peak_hour_count,
            "avg_active_minutes": avg_active_minutes,
            "active_members_last_30_days": int(active_members_last_30_days),
            "inactive_members_last_30_days": inactive_members_last_30_days,
            "member_engagement_rate": member_engagement_rate,
            "new_members_this_month": int(new_members_this_month),
            "manual_checkin_rate": manual_checkin_rate,
            "avg_visits_per_active_member": avg_visits_per_active_member
        },
        "hourly_checkins_today": hourly_checkins_today,
        "daily_avg_session_duration": daily_avg_session_duration
    }


@router.get('/isCheckedIn', status_code=status.HTTP_200_OK)
def is_checked_in(db: Session = Depends(get_db), current_user: User = Depends(manager)):
    if not current_user or current_user.role != "member":
        raise HTTPException(status_code=403, detail="Member role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )

    member = db.query(User).filter(
        User.user_id == current_user.user_id).first()

    already_checked_in = db.query(Attendance).filter(
        Attendance.user_id == member.user_id,
        func.date(Attendance.check_in_time) == func.current_date()).first()

    if already_checked_in:
        if already_checked_in.auto_checkout and already_checked_in.check_out_time > datetime.now(timezone.utc):
            return {"checked_in": True, "check_in_time": already_checked_in.check_in_time}
        elif already_checked_in.auto_checkout and already_checked_in.check_out_time <= datetime.now(timezone.utc):
            return {"checked_in": False, "check_in_time":already_checked_in.check_in_time, "check_out_time":already_checked_in.check_out_time}
        elif not already_checked_in.auto_checkout:
            return {"checked_in": False, "check_in_time":already_checked_in.check_in_time, "check_out_time":already_checked_in.check_out_time}
        
    return {"checked_in":False}


@router.post('/checkout', status_code=status.HTTP_200_OK)
def checkout(db: Session = Depends(get_db), current_user: User = Depends(manager)):
    if not current_user or current_user.role != "member":
        raise HTTPException(status_code=403, detail="Member role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )

    checkoutTime = datetime.now(timezone.utc)
    attendance = db.query(Attendance).filter(Attendance.user_id == current_user.user_id,
                                             Attendance.auto_checkout == True, func.date(Attendance.check_in_time) == func.current_date()).first()

    attendance.check_out_time = checkoutTime
    attendance.auto_checkout = False
    try:
        db.commit()
        db.refresh(attendance)
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500, detail="Database error during checkout")
    return {"message": "Checked out successfully", "time":attendance.check_out_time}
