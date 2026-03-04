from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import User, Admin, Attendance, TrainerClient, Trainer
from app.routers.auth import manager, pwd
from app.schemas.user_schema import (
    UserOut,
    SearchQuery,
    MemberProfileOut,
    MemberProfileUpdate,
    MemberVerifyOldPasswordIn,
    MemberChangePasswordIn,
)
from sqlalchemy import cast, String, text, func, or_
from pydantic import EmailStr
from datetime import date, timedelta, datetime, timezone
import uuid
import re
import io
import json
from urllib import error as urllib_error, request as urllib_request
from app.config import (
    CLOUDINARY_CLOUD_NAME,
    CLOUDINARY_API_KEY,
    CLOUDINARY_API_SECRET,
    MEMBER_PROFILE_CHANGE_COOLDOWN_MINUTES,
    RESEND_API_KEY,
    RESEND_FROM_EMAIL,
)

try:
    import cloudinary
    import cloudinary.uploader
except ImportError:
    cloudinary = None

router = APIRouter(prefix='/api', tags=["USERS"])
PHONE_REGEX = re.compile(r"^(?:(?:\+91|0)?)[6-9]\d{9}$")
MAX_PROFILE_PHOTO_BYTES = 5 * 1024 * 1024

if cloudinary and CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET:
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
        secure=True
    )


def _normalize_utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _enforce_member_profile_cooldown(member: User):
    if MEMBER_PROFILE_CHANGE_COOLDOWN_MINUTES <= 0:
        return

    profile_updated_at = _normalize_utc_datetime(member.profile_updated_at)
    if not profile_updated_at:
        return

    now_utc = datetime.now(timezone.utc)
    next_allowed_at = profile_updated_at + timedelta(minutes=MEMBER_PROFILE_CHANGE_COOLDOWN_MINUTES)
    if now_utc < next_allowed_at:
        remaining_seconds = int((next_allowed_at - now_utc).total_seconds())
        remaining_minutes = max(1, (remaining_seconds + 59) // 60)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Please wait {remaining_minutes} minute(s) before you can update your profile again."
        )


def _ensure_email_config():
    if not RESEND_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Email is not configured. Set RESEND_API_KEY in backend .env"
        )


def _send_email(recipient: str, subject: str, body: str):
    _ensure_email_config()
    payload = {
        "from": RESEND_FROM_EMAIL,
        "to": [recipient],
        "subject": subject,
        "text": body,
    }

    request_data = json.dumps(payload).encode("utf-8")
    request_obj = urllib_request.Request(
        url="https://api.resend.com/emails",
        data=request_data,
        method="POST",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib_request.urlopen(request_obj, timeout=20):
            return
    except urllib_error.HTTPError as error:
        error_payload = error.read().decode("utf-8", errors="ignore")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send email via Resend: {error_payload or f'HTTP {error.code}'}"
        ) from error
    except urllib_error.URLError as error:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send email via Resend: {error.reason}"
        ) from error


@router.get("/users", status_code=status.HTTP_200_OK)
def get_all_users(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1),
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

    skip = (page - 1) * limit
    stats = db.execute(
        text("SELECT count FROM site_statistics WHERE label = 'total_users'")).fetchone()
    total_users = stats[0] if stats else 0

    users = db.query(User).order_by(
        func.lower(User.name).asc(),
        User.created_at.desc(),
        User.user_id.asc()
    ).offset(skip).limit(limit).all()
    safe_users = [UserOut.model_validate(u) for u in users]

    return {
        "users": safe_users,
        "total_users": total_users,
        "page": page,
        "limit": limit
    }


@router.get("/member/profile", status_code=status.HTTP_200_OK)
def get_member_profile(
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

    member = db.query(User).filter(User.user_id == current_user.user_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    return {"profile": MemberProfileOut.model_validate(member)}


@router.patch("/member/profile", status_code=status.HTTP_200_OK)
def update_member_profile(
    data: MemberProfileUpdate,
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

    member = db.query(User).filter(User.user_id == current_user.user_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    _enforce_member_profile_cooldown(member)

    normalized_phone = data.phone.strip()
    if not PHONE_REGEX.fullmatch(normalized_phone):
        raise HTTPException(status_code=400, detail="Please enter a valid phone number.")

    member.name = data.name.strip()
    member.phone = normalized_phone
    member.address = data.address.strip()
    member.fitness_goal = data.fitness_goal.strip()
    member.experience_level = data.experience_level.strip()
    member.profile_updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(member)

    return {
        "message": "Profile updated successfully",
        "profile": MemberProfileOut.model_validate(member)
    }


@router.post("/member/verify-old-password", status_code=status.HTTP_200_OK)
def verify_member_old_password(
    data: MemberVerifyOldPasswordIn,
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

    member = db.query(User).filter(User.user_id == current_user.user_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    if not pwd.verify(data.old_password, member.password):
        raise HTTPException(status_code=400, detail="Old password is incorrect")

    return {"message": "Old password verified successfully"}


@router.post("/member/change-password", status_code=status.HTTP_200_OK)
def change_member_password(
    data: MemberChangePasswordIn,
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

    if data.new_password != data.confirm_password:
        raise HTTPException(status_code=400, detail="New password and confirm password do not match")

    member = db.query(User).filter(User.user_id == current_user.user_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Server-side verification blocks any UI bypass attempts.
    if not pwd.verify(data.old_password, member.password):
        raise HTTPException(status_code=400, detail="Old password is incorrect")

    if pwd.verify(data.new_password, member.password):
        raise HTTPException(status_code=400, detail="New password must be different from old password")

    member.password = pwd.hash(data.new_password)
    member.password_changes_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(member)

    try:
        _send_email(
            recipient=member.email,
            subject="FitPro Password Changed",
            body=(
                f"Hello {member.name},\n\n"
                "Your FitPro account password was changed successfully.\n"
                "If this was not you, contact support immediately.\n\n"
                f"Time (UTC): {member.password_changes_at}\n"
            )
        )
    except Exception:
        # Password is already updated; avoid rolling back credentials on email errors.
        pass

    return {"message": "Password updated successfully"}


@router.post("/member/profile/photo", status_code=status.HTTP_200_OK)
def upload_member_profile_photo(
    image: UploadFile = File(...),
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

    if not cloudinary:
        raise HTTPException(
            status_code=500,
            detail="Cloudinary SDK is not installed. Please install backend requirements."
        )

    if not (CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET):
        raise HTTPException(
            status_code=500,
            detail="Cloudinary is not fully configured."
        )

    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are allowed")

    image_bytes = image.file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Image file is empty")

    if len(image_bytes) > MAX_PROFILE_PHOTO_BYTES:
        raise HTTPException(status_code=400, detail="Image size must be 5 MB or smaller")

    member = db.query(User).filter(User.user_id == current_user.user_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    _enforce_member_profile_cooldown(member)

    try:
        upload_result = cloudinary.uploader.upload(
            io.BytesIO(image_bytes),
            folder="fitprogym/users",
            public_id=f"user_{member.user_id}_profile",
            overwrite=True,
            invalidate=True,
            resource_type="image"
        )
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Cloudinary upload failed: {error}") from error

    secure_url = upload_result.get("secure_url")
    if not secure_url:
        raise HTTPException(status_code=500, detail="Failed to upload image to Cloudinary")

    member.profile_photo = secure_url
    member.profile_updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(member)

    return {
        "message": "Profile photo updated successfully",
        "profile_photo": secure_url,
        "profile": MemberProfileOut.model_validate(member)
    }


@router.get("/user", status_code=status.HTTP_200_OK)
def get_user(query: SearchQuery = Depends(), db: Session = Depends(get_db), current_user: Admin = Depends(manager)):

    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )

    search_val = str(query.search).strip()
    user = db.query(User).filter(
        (User.email == search_val) | (
            cast(User.user_id, String) == search_val)
    ).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    safe_users = [UserOut.model_validate(user)]

    return {
        "user": safe_users
    }


@router.get("/users/searchByName", status_code=status.HTTP_200_OK)
def search_users_by_name(
    name: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(12, ge=1, le=50),
    offset: int = Query(0, ge=0),
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

    normalized_name = " ".join(name.split()).strip()
    if not normalized_name:
        raise HTTPException(status_code=400, detail="Name is required")

    matched_users = db.query(User).filter(
        User.name.ilike(f"%{normalized_name}%")
    ).order_by(
        User.name.asc(),
        User.created_at.desc(),
        User.user_id.asc()
    ).offset(offset).limit(limit + 1).all()

    has_more = len(matched_users) > limit
    visible_users = matched_users[:limit]

    users = [
        {
            "user_id": str(user.user_id),
            "name": user.name,
            "email": user.email,
            "is_active": user.is_active,
            "profile_photo": getattr(user, "profile_photo", None)
        }
        for user in visible_users
    ]

    return {
        "users": users,
        "count": len(users),
        "limit": limit,
        "offset": offset,
        "has_more": has_more,
        "next_offset": offset + len(users)
    }


@router.get("/users/previewByEmail", status_code=status.HTTP_200_OK)
def preview_user_by_email(
    email: EmailStr = Query(...),
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

    normalized_email = str(email).strip().lower()

    member = db.query(User).filter(User.email == normalized_email).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    already_checked_in = db.query(Attendance).filter(
        Attendance.user_id == member.user_id,
        func.date(Attendance.check_in_time) == func.current_date()
    ).first()

    return {
        "user_id": str(member.user_id),
        "name": member.name,
        "email": member.email,
        "is_active": member.is_active,
        "profile_photo": getattr(member, "profile_photo", None),
        "already_checked_in_today": bool(already_checked_in)
    }


@router.get("/admin/memberRecords/{user_id}", status_code=status.HTTP_200_OK)
def get_member_records_for_admin(
    user_id: str,
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
        valid_user_id = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid ID format, a valid UUID is required!"
        )

    member = db.query(User).filter(User.user_id == valid_user_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    active_assignment = db.query(TrainerClient).filter(
        TrainerClient.user_id == member.user_id,
        TrainerClient.is_active.is_(True)
    ).order_by(TrainerClient.assign_at.desc()).first()

    trainer_payload = None
    if active_assignment:
        trainer = db.query(Trainer).filter(
            Trainer.trainer_id == active_assignment.trainer_id
        ).first()

        trainer_payload = {
            "trainer_id": str(active_assignment.trainer_id),
            "name": trainer.name if trainer else "Unknown Trainer",
            "assigned_at": active_assignment.assign_at,
            "trainer_available": bool(trainer and trainer.is_active)
        }

    attendance_rows = db.query(Attendance).filter(
        Attendance.user_id == member.user_id
    ).order_by(
        Attendance.check_in_time.desc()
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
            "verified_by_admin": bool(attendance.verified_by_admin),
            "auto_checkout": bool(attendance.auto_checkout)
        })

    return {
        "member_user_id": str(member.user_id),
        "personal_trainer": trainer_payload,
        "attendance_history": attendance_history,
        "attendance_count": len(attendance_history)
    }


@router.get("/memberDashboardInsights", status_code=status.HTTP_200_OK)
def get_member_dashboard_insights(
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

    today = date.today()
    start_of_7_days = today - timedelta(days=6)
    start_of_30_days = today - timedelta(days=30)

    attendance_this_month = db.query(
        func.count(func.distinct(func.date(Attendance.check_in_time)))
    ).filter(
        Attendance.user_id == current_user.user_id,
        Attendance.check_in_time >= func.date_trunc("month", func.now())
    ).scalar() or 0

    checkins_last_7_days = db.query(
        func.count(func.distinct(func.date(Attendance.check_in_time)))
    ).filter(
        Attendance.user_id == current_user.user_id,
        func.date(Attendance.check_in_time) >= start_of_7_days
    ).scalar() or 0

    total_checkins = db.query(func.count(Attendance.id)).filter(
        Attendance.user_id == current_user.user_id
    ).scalar() or 0

    avg_session_minutes_30_days = db.query(
        func.avg(
            func.extract("epoch", Attendance.check_out_time - Attendance.check_in_time) / 60.0
        )
    ).filter(
        Attendance.user_id == current_user.user_id,
        func.date(Attendance.check_in_time) >= start_of_30_days,
        Attendance.check_out_time.isnot(None),
        Attendance.check_out_time >= Attendance.check_in_time,
        or_(
            Attendance.auto_checkout.is_(False),
            Attendance.check_out_time <= func.now()
        )
    ).scalar()

    distinct_checkin_dates_rows = db.query(
        func.date(Attendance.check_in_time).label("day")
    ).filter(
        Attendance.user_id == current_user.user_id,
        func.date(Attendance.check_in_time) >= today - timedelta(days=120)
    ).group_by(
        func.date(Attendance.check_in_time)
    ).all()

    distinct_checkin_dates = {row.day for row in distinct_checkin_dates_rows}
    streak_cursor = today if today in distinct_checkin_dates else today - timedelta(days=1)
    workout_streak_days = 0
    while streak_cursor in distinct_checkin_dates:
        workout_streak_days += 1
        streak_cursor -= timedelta(days=1)

    last_check_in = db.query(func.max(Attendance.check_in_time)).filter(
        Attendance.user_id == current_user.user_id
    ).scalar()

    active_assignment = db.query(TrainerClient).filter(
        TrainerClient.user_id == current_user.user_id,
        TrainerClient.is_active.is_(True)
    ).order_by(
        TrainerClient.assign_at.desc()
    ).first()

    assigned_trainer = None
    if active_assignment:
        trainer = db.query(Trainer).filter(
            Trainer.trainer_id == active_assignment.trainer_id
        ).first()

        if trainer:
            assigned_trainer = {
                "trainer_id": str(trainer.trainer_id),
                "name": trainer.name,
                "specializations": trainer.specializations,
                "experience_years": trainer.experience_years,
                "short_bio": trainer.short_bio,
                "certifications": trainer.certifications or [],
                "assigned_at": active_assignment.assign_at
            }

    return {
        "summary": {
            "attendance_this_month": int(attendance_this_month),
            "attendance_goal_days": today.day,
            "checkins_last_7_days": int(checkins_last_7_days),
            "total_checkins": int(total_checkins),
            "workout_streak_days": int(workout_streak_days),
            "avg_session_minutes_30_days": round(float(avg_session_minutes_30_days), 1) if avg_session_minutes_30_days else 0.0,
            "last_check_in": last_check_in
        },
        "trainer": assigned_trainer
    }


@router.patch("/blockUnblock", status_code=status.HTTP_200_OK)
def block_user(user_id: str, db: Session = Depends(get_db), current_user: Admin = Depends(manager)):
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )

    try:
        valid_user_id = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid ID format, a valid UUID is required!")
    
    user = db.query(User).filter(User.user_id==valid_user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found!")
    
    user.is_active = not user.is_active
    db.commit()
    return {"message": "Status updated successfully"}


@router.get("/totalMembers", status_code=status.HTTP_200_OK)
def get_total_users(db:Session=Depends(get_db), current_user: Admin = Depends(manager)):
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )
    
    stats = db.execute(
        text("SELECT count FROM site_statistics WHERE label = 'total_users'")).fetchone()
    total_users = stats[0] if stats else 0

    return {"total_users": total_users}
