from fastapi import APIRouter, Depends, HTTPException, status, Query, Request, UploadFile, File
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import Trainer, Admin, User, TrainerClient, TrainersAttendance, TrainerPasswordResetToken
from app.routers.auth import manager
from app.schemas.trainer_schema import (
    TrainerOut,
    TrainerCreate,
    TrainerPublicOut,
    TrainerCompensationUpdate,
    TrainerProfileOut,
    TrainerProfileUpdate,
    TrainerChangePasswordIn,
    TrainerResetPasswordConfirmIn,
)
from app.schemas.user_schema import SearchQuery
from sqlalchemy import cast, String, text, func, or_
import uuid
from app.routers.auth import pwd
from datetime import datetime, timezone, timedelta
import io
import re
import secrets
import hashlib
import html
import mailtrap as mt
from app.config import (
    CLOUDINARY_CLOUD_NAME,
    CLOUDINARY_API_KEY,
    CLOUDINARY_API_SECRET,
    FRONTEND_APP_URL,
    MAILTRAP_API_KEY,
    PASSWORD_RESET_TOKEN_HOURS,
    TRAINER_PROFILE_CHANGE_COOLDOWN_MINUTES,
    TRAINER_PASSWORD_CHANGE_COOLDOWN_MINUTES,
)

try:
    import cloudinary
    import cloudinary.uploader
except ImportError:
    cloudinary = None


router = APIRouter(prefix='/api', tags=["TRAINERS"])

PHONE_REGEX = re.compile(r"^(?:(?:\+91|0)?)[6-9]\d{9}$")
MAX_PROFILE_PHOTO_BYTES = 5 * 1024 * 1024
MAILTRAP_INBOX_ID = 4433988

client = mt.MailtrapClient(
  token=MAILTRAP_API_KEY,
  sandbox=True,
  inbox_id=MAILTRAP_INBOX_ID,
)

if cloudinary and CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET:
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
        secure=True
    )


def _require_active_trainer(current_user: Trainer):
    if not current_user or current_user.role != "trainer":
        raise HTTPException(status_code=403, detail="Trainer role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )


def _normalize_utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _enforce_change_cooldown(last_changed_at: datetime | None, cooldown_minutes: int, action: str):
    if cooldown_minutes <= 0:
        return

    normalized_last_changed = _normalize_utc_datetime(last_changed_at)
    if not normalized_last_changed:
        return

    now_utc = datetime.now(timezone.utc)
    next_allowed_at = normalized_last_changed + timedelta(minutes=cooldown_minutes)
    if now_utc < next_allowed_at:
        remaining_seconds = int((next_allowed_at - now_utc).total_seconds())
        remaining_minutes = max(1, (remaining_seconds + 59) // 60)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Please wait {remaining_minutes} minute(s) before you can {action} again."
        )


def _ensure_email_config():
    if not MAILTRAP_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Email is not configured."
        )


def _render_html_body(body: str) -> str:
    safe_body = html.escape(body or "")
    safe_body = safe_body.replace("\n", "<br />")
    return (
        "<div style=\"font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.6;color:#111;\">"
        f"{safe_body}"
        "</div>"
    )


def _send_email(recipient: str, subject: str, body: str):
    _ensure_email_config()
    try:
        mail = mt.Mail(
            sender=mt.Address(email="support@fitpro.com", name="Fitpro GYM"),
            to=[mt.Address(email=recipient)],
            subject=subject,
            text=body,
            html=_render_html_body(body),
        )

        client.send(mail)
    except Exception:
        raise HTTPException(status_code=500, detail=f"Failed to send email")


def _issue_trainer_password_reset_token(trainer: Trainer, db: Session):
    now_utc = datetime.now(timezone.utc)
    db.query(TrainerPasswordResetToken).filter(
        TrainerPasswordResetToken.trainer_id == trainer.trainer_id,
        TrainerPasswordResetToken.used.is_(False)
    ).update({TrainerPasswordResetToken.used: True}, synchronize_session=False)

    plain_token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(plain_token.encode("utf-8")).hexdigest()
    expires_at = now_utc + timedelta(hours=PASSWORD_RESET_TOKEN_HOURS)

    token_row = TrainerPasswordResetToken(
        trainer_id=trainer.trainer_id,
        token_hash=token_hash,
        expires_at=expires_at,
        used=False,
    )
    db.add(token_row)
    db.commit()
    db.refresh(token_row)

    reset_link = f"{FRONTEND_APP_URL.rstrip('/')}/trainer/reset-password?token={plain_token}"
    return reset_link, expires_at


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


@router.get("/trainer/summary", status_code=status.HTTP_200_OK)
def get_trainer_summary(
    db: Session = Depends(get_db),
    current_user: Trainer = Depends(manager)
):
    if not current_user or current_user.role != "trainer":
        raise HTTPException(status_code=403, detail="Trainer role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )

    trainer = db.query(Trainer).filter(
        Trainer.trainer_id == current_user.trainer_id
    ).first()
    if not trainer:
        raise HTTPException(status_code=404, detail="Trainer not found")

    active_clients_count = db.query(func.count(TrainerClient.id)).filter(
        TrainerClient.trainer_id == trainer.trainer_id,
        TrainerClient.is_active.is_(True)
    ).scalar() or 0

    total_clients_count = db.query(func.count(TrainerClient.id)).filter(
        TrainerClient.trainer_id == trainer.trainer_id
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

    start_7_days = now_utc.date() - timedelta(days=6)
    checkins_last_7_days = db.query(func.count(TrainersAttendance.id)).filter(
        TrainersAttendance.trainer_id == trainer.trainer_id,
        func.date(TrainersAttendance.check_in_time) >= start_7_days
    ).scalar() or 0

    attendance_trend_rows = db.query(
        func.date(TrainersAttendance.check_in_time).label("day"),
        func.count(TrainersAttendance.id).label("count")
    ).filter(
        TrainersAttendance.trainer_id == trainer.trainer_id,
        func.date(TrainersAttendance.check_in_time) >= start_7_days
    ).group_by(
        func.date(TrainersAttendance.check_in_time)
    ).all()

    attendance_trend_map = {row.day: int(row.count) for row in attendance_trend_rows}
    attendance_trend = []
    for i in range(7):
        day = start_7_days + timedelta(days=i)
        attendance_trend.append({
            "day": day.strftime("%a"),
            "date": day.isoformat(),
            "count": attendance_trend_map.get(day, 0)
        })

    month_start = datetime(now_utc.year, now_utc.month, 1, tzinfo=timezone.utc)
    sessions_this_month = db.query(func.count(TrainersAttendance.id)).filter(
        TrainersAttendance.trainer_id == trainer.trainer_id,
        TrainersAttendance.check_in_time >= month_start
    ).scalar() or 0

    avg_session_minutes_value = db.query(
        func.avg(
            func.extract(
                "epoch",
                TrainersAttendance.check_out_time - TrainersAttendance.check_in_time
            ) / 60.0
        )
    ).filter(
        TrainersAttendance.trainer_id == trainer.trainer_id,
        TrainersAttendance.check_out_time.isnot(None),
        TrainersAttendance.check_out_time >= TrainersAttendance.check_in_time
    ).scalar()

    avg_session_minutes = round(float(avg_session_minutes_value), 1) if avg_session_minutes_value else 0.0

    return {
        "trainer": {
            "trainer_id": str(trainer.trainer_id),
            "name": trainer.name,
            "email": trainer.email,
            "phone": trainer.phone,
            "specializations": trainer.specializations,
            "experience_years": trainer.experience_years,
            "short_bio": trainer.short_bio,
            "last_login": trainer.last_login,
            "created_at": trainer.created_at,
        },
        "metrics": {
            "active_clients": int(active_clients_count),
            "total_clients": int(total_clients_count),
            "sessions_this_month": int(sessions_this_month),
            "checkins_last_7_days": int(checkins_last_7_days),
            "avg_session_minutes": avg_session_minutes,
        },
        "attendance": {
            "has_attendance_today": bool(today_attendance),
            "is_checked_in_today": is_checked_in_today,
            "check_in_time": today_attendance.check_in_time if today_attendance else None,
            "check_out_time": today_attendance.check_out_time if today_attendance else None,
        },
        "attendance_trend": attendance_trend,
        "compensation": {
            "base_salary": int(trainer.base_salary or 0),
            "bonus_per_client": int(trainer.bonus_per_client or 0),
            "compensation_notes": trainer.compensation_notes
        }
    }


@router.get("/trainer/profile", status_code=status.HTTP_200_OK)
def get_trainer_profile(
    db: Session = Depends(get_db),
    current_user: Trainer = Depends(manager)
):
    _require_active_trainer(current_user)

    trainer = db.query(Trainer).filter(Trainer.trainer_id == current_user.trainer_id).first()
    if not trainer:
        raise HTTPException(status_code=404, detail="Trainer profile not found")

    return {"profile": TrainerProfileOut.model_validate(trainer)}


@router.patch("/trainer/profile", status_code=status.HTTP_200_OK)
def update_trainer_profile(
    data: TrainerProfileUpdate,
    db: Session = Depends(get_db),
    current_user: Trainer = Depends(manager)
):
    _require_active_trainer(current_user)

    normalized_phone = data.phone.strip()
    if not PHONE_REGEX.fullmatch(normalized_phone):
        raise HTTPException(status_code=400, detail="Please enter a valid phone number.")

    trainer = db.query(Trainer).filter(Trainer.trainer_id == current_user.trainer_id).first()
    if not trainer:
        raise HTTPException(status_code=404, detail="Trainer profile not found")

    _enforce_change_cooldown(
        trainer.profile_updated_at,
        TRAINER_PROFILE_CHANGE_COOLDOWN_MINUTES,
        "update your trainer profile"
    )

    trainer.name = data.name.strip()
    trainer.phone = normalized_phone
    trainer.profile_updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(trainer)

    return {
        "message": "Trainer profile updated successfully",
        "profile": TrainerProfileOut.model_validate(trainer)
    }


@router.post("/trainer/change-password", status_code=status.HTTP_200_OK)
def change_trainer_password(
    data: TrainerChangePasswordIn,
    db: Session = Depends(get_db),
    current_user: Trainer = Depends(manager)
):
    _require_active_trainer(current_user)

    if data.new_password != data.confirm_password:
        raise HTTPException(status_code=400, detail="New password and confirm password do not match")

    trainer = db.query(Trainer).filter(Trainer.trainer_id == current_user.trainer_id).first()
    if not trainer:
        raise HTTPException(status_code=404, detail="Trainer profile not found")

    if not pwd.verify(data.old_password, trainer.password):
        raise HTTPException(status_code=400, detail="Old password is incorrect")

    if pwd.verify(data.new_password, trainer.password):
        raise HTTPException(status_code=400, detail="New password must be different from old password")

    _enforce_change_cooldown(
        trainer.password_updated_at,
        TRAINER_PASSWORD_CHANGE_COOLDOWN_MINUTES,
        "change your password"
    )

    trainer.password = pwd.hash(data.new_password)
    trainer.password_updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(trainer)

    try:
        _send_email(
            recipient=trainer.email,
            subject="FitPro Trainer Password Changed",
            body=(
                f"Hello {trainer.name},\n\n"
                "Your FitPro trainer account password was changed successfully.\n"
                "If this was not you, contact support immediately.\n\n"
                f"Time (UTC): {trainer.password_updated_at}\n"
            )
        )
    except Exception:
        pass

    return {"message": "Password updated successfully"}


@router.post("/trainer/profile/photo", status_code=status.HTTP_200_OK)
def upload_trainer_profile_photo(
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: Trainer = Depends(manager)
):
    _require_active_trainer(current_user)

    if not cloudinary:
        raise HTTPException(
            status_code=500,
            detail="Cloudinary SDK is not installed. Please install backend requirements."
        )

    if not (CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET):
        raise HTTPException(
            status_code=500,
            detail="Cloudinary is not fully configured. Please set cloud name, api key and api secret in backend .env"
        )

    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are allowed")

    image_bytes = image.file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Image file is empty")

    if len(image_bytes) > MAX_PROFILE_PHOTO_BYTES:
        raise HTTPException(status_code=400, detail="Image size must be 5 MB or smaller")

    trainer = db.query(Trainer).filter(Trainer.trainer_id == current_user.trainer_id).first()
    if not trainer:
        raise HTTPException(status_code=404, detail="Trainer profile not found")

    _enforce_change_cooldown(
        trainer.profile_updated_at,
        TRAINER_PROFILE_CHANGE_COOLDOWN_MINUTES,
        "update your trainer profile"
    )

    try:
        upload_result = cloudinary.uploader.upload(
            io.BytesIO(image_bytes),
            folder="fitprogym/trainers",
            public_id=f"trainer_{trainer.trainer_id}_profile",
            overwrite=True,
            invalidate=True,
            resource_type="image"
        )
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Cloudinary upload failed: {error}") from error

    secure_url = upload_result.get("secure_url")
    if not secure_url:
        raise HTTPException(status_code=500, detail="Failed to upload image to Cloudinary")

    trainer.profile_photo = secure_url
    trainer.profile_updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(trainer)

    return {
        "message": "Trainer profile photo updated successfully",
        "profile_photo": secure_url,
        "profile": TrainerProfileOut.model_validate(trainer)
    }


@router.post("/trainer/reset-password/confirm", status_code=status.HTTP_200_OK)
def confirm_trainer_password_reset(
    data: TrainerResetPasswordConfirmIn,
    db: Session = Depends(get_db)
):
    if data.new_password != data.confirm_password:
        raise HTTPException(status_code=400, detail="New password and confirm password do not match")

    now_utc = datetime.now(timezone.utc)
    token_hash = hashlib.sha256(data.token.encode("utf-8")).hexdigest()
    token_row = db.query(TrainerPasswordResetToken).filter(
        TrainerPasswordResetToken.token_hash == token_hash
    ).first()

    if not token_row:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    if token_row.used:
        raise HTTPException(status_code=400, detail="Reset token already used")

    if token_row.expires_at < now_utc:
        token_row.used = True
        db.commit()
        raise HTTPException(status_code=400, detail="Reset token expired")

    trainer = db.query(Trainer).filter(Trainer.trainer_id == token_row.trainer_id).first()
    if not trainer:
        token_row.used = True
        db.commit()
        raise HTTPException(status_code=404, detail="Trainer not found for this token")

    trainer.password = pwd.hash(data.new_password)
    trainer.password_updated_at = now_utc
    token_row.used = True

    db.query(TrainerPasswordResetToken).filter(
        TrainerPasswordResetToken.trainer_id == trainer.trainer_id,
        TrainerPasswordResetToken.used.is_(False)
    ).update({TrainerPasswordResetToken.used: True}, synchronize_session=False)

    db.commit()
    db.refresh(trainer)

    try:
        _send_email(
            recipient=trainer.email,
            subject="FitPro Trainer Password Reset Successful",
            body=(
                f"Hello {trainer.name},\n\n"
                "Your FitPro trainer password has been reset successfully.\n"
                "If this was not you, contact support immediately.\n\n"
                f"Time (UTC): {trainer.password_updated_at}\n"
            )
        )
    except Exception:
        pass

    return {"message": "Password reset successful"}


@router.get("/trainer/clients", status_code=status.HTTP_200_OK)
def get_trainer_clients(
    db: Session = Depends(get_db),
    current_user: Trainer = Depends(manager)
):
    if not current_user or current_user.role != "trainer":
        raise HTTPException(status_code=403, detail="Trainer role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )

    assigned_rows = db.query(TrainerClient, User).join(
        User, User.user_id == TrainerClient.user_id
    ).filter(
        TrainerClient.trainer_id == current_user.trainer_id,
        TrainerClient.is_active.is_(True)
    ).order_by(
        TrainerClient.assign_at.desc()
    ).all()

    clients = []
    for assignment, member in assigned_rows:
        clients.append({
            "user_id": str(member.user_id),
            "name": member.name,
            "email": member.email,
            "phone": member.phone,
            "fitness_goal": member.fitness_goal,
            "experience_level": member.experience_level,
            "profile_photo": getattr(member, "profile_photo", None),
            "assigned_at": assignment.assign_at,
            "is_active": member.is_active,
        })

    return {
        "clients": clients,
        "total_clients": len(clients)
    }


@router.post("/trainer/checkin", status_code=status.HTTP_200_OK)
def trainer_checkin(
    db: Session = Depends(get_db),
    current_user: Trainer = Depends(manager)
):
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Self check-in is disabled. Please contact an admin for attendance."
    )


@router.post("/trainer/checkout", status_code=status.HTTP_200_OK)
def trainer_checkout(
    db: Session = Depends(get_db),
    current_user: Trainer = Depends(manager)
):
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Self check-out is disabled. Please contact an admin for attendance."
    )


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


@router.post("/admin/trainers/{trainer_id}/force-password-reset", status_code=status.HTTP_200_OK)
def force_trainer_password_reset(
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
        raise HTTPException(status_code=400, detail="Invalid trainer ID format")

    trainer = db.query(Trainer).filter(Trainer.trainer_id == valid_trainer_id).first()
    if not trainer:
        raise HTTPException(status_code=404, detail="Trainer not found")

    try:
        reset_link, expires_at = _issue_trainer_password_reset_token(trainer, db)
        _send_email(
            recipient=trainer.email,
            subject="FitPro Trainer Password Reset Link",
            body=(
                f"Hello {trainer.name},\n\n"
                "A password reset was requested for your FitPro trainer account.\n"
                "Use this secure link to reset your password:\n"
                f"{reset_link}\n\n"
                f"This link expires in {PASSWORD_RESET_TOKEN_HOURS} hour(s), at {expires_at} UTC.\n"
                "If you did not expect this, contact support immediately.\n"
            )
        )
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Failed to send reset email: {error}") from error

    return {"message": "Password reset link sent successfully"}


@router.patch("/admin/trainers/{trainer_id}/compensation", status_code=status.HTTP_200_OK)
def update_trainer_compensation(
    trainer_id: str,
    data: TrainerCompensationUpdate,
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

    if data.base_salary is None and data.bonus_per_client is None and data.compensation_notes is None:
        raise HTTPException(status_code=400, detail="No compensation updates provided")

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

    if data.base_salary is not None:
        trainer.base_salary = data.base_salary
    if data.bonus_per_client is not None:
        trainer.bonus_per_client = data.bonus_per_client
    if data.compensation_notes is not None:
        note = data.compensation_notes.strip()
        trainer.compensation_notes = note if note else None

    db.commit()
    db.refresh(trainer)

    return {
        "message": "Trainer compensation updated successfully",
        "trainer_id": str(trainer.trainer_id),
        "compensation": {
            "base_salary": int(trainer.base_salary or 0),
            "bonus_per_client": int(trainer.bonus_per_client or 0),
            "compensation_notes": trainer.compensation_notes
        }
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
