import io
import re
import uuid
import math
import html
import mailtrap as mt
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from urllib import error as urllib_error, request as urllib_request
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlalchemy.orm import Session

from app.config import (
    CLOUDINARY_CLOUD_NAME,
    CLOUDINARY_API_KEY,
    CLOUDINARY_API_SECRET,
    FRONTEND_APP_URL,
    MAILTRAP_API_KEY,
    PASSWORD_RESET_TOKEN_HOURS,
    ADMIN_PROFILE_CHANGE_COOLDOWN_MINUTES,
    ADMIN_PASSWORD_CHANGE_COOLDOWN_MINUTES,
)
from app.db.database import get_db
from app.db.models import Admin, AdminPasswordResetToken
from app.routers.auth import manager, pwd
from app.schemas.admin_schema import (
    AdminProfileOut,
    AdminProfileUpdate,
    AdminCreateBySuperAdmin,
    AdminChangePasswordIn,
    AdminResetPasswordConfirmIn,
)

try:
    import cloudinary
    import cloudinary.uploader
except ImportError:
    cloudinary = None


router = APIRouter(prefix="/api", tags=["ADMINS"])

PHONE_REGEX = re.compile(r"^(?:(?:\+91|0)?)[6-9]\d{9}$")
MAX_PROFILE_PHOTO_BYTES = 5 * 1024 * 1024
MAILTRAP_INBOX_ID = 4433988

if cloudinary and CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET:
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
        secure=True
    )

client = mt.MailtrapClient(
  token=MAILTRAP_API_KEY,
  sandbox=True,
  inbox_id=MAILTRAP_INBOX_ID,
)


def _require_active_admin(current_user: Admin):
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )


def _require_super_admin(current_user: Admin):
    _require_active_admin(current_user)
    if not current_user.is_super_admin:
        raise HTTPException(status_code=403, detail="Super admin access required")


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
        remaining_minutes = max(1, math.ceil(remaining_seconds / 60))
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



def _issue_admin_password_reset_token(admin: Admin, db: Session):
    now_utc = datetime.now(timezone.utc)
    db.query(AdminPasswordResetToken).filter(
        AdminPasswordResetToken.admin_id == admin.admin_id,
        AdminPasswordResetToken.used.is_(False)
    ).update({AdminPasswordResetToken.used: True}, synchronize_session=False)

    plain_token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(plain_token.encode("utf-8")).hexdigest()
    expires_at = now_utc + timedelta(hours=PASSWORD_RESET_TOKEN_HOURS)

    token_row = AdminPasswordResetToken(
        admin_id=admin.admin_id,
        token_hash=token_hash,
        expires_at=expires_at,
        used=False,
    )
    db.add(token_row)
    db.commit()
    db.refresh(token_row)

    reset_link = f"{FRONTEND_APP_URL.rstrip('/')}/admin/reset-password?token={plain_token}"
    return reset_link, expires_at


@router.get("/admin/profile", status_code=status.HTTP_200_OK)
def get_admin_profile(
    db: Session = Depends(get_db),
    current_user: Admin = Depends(manager)
):
    _require_active_admin(current_user)

    admin = db.query(Admin).filter(Admin.admin_id == current_user.admin_id).first()
    if not admin:
        raise HTTPException(status_code=404, detail="Admin profile not found")

    return {"profile": AdminProfileOut.model_validate(admin)}


@router.patch("/admin/profile", status_code=status.HTTP_200_OK)
def update_admin_profile(
    data: AdminProfileUpdate,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(manager)
):
    _require_active_admin(current_user)

    normalized_phone = data.phone.strip()
    if not PHONE_REGEX.fullmatch(normalized_phone):
        raise HTTPException(status_code=400, detail="Please enter a valid phone number.")

    admin = db.query(Admin).filter(Admin.admin_id == current_user.admin_id).first()
    if not admin:
        raise HTTPException(status_code=404, detail="Admin profile not found")

    _enforce_change_cooldown(
        admin.profile_updated_at,
        ADMIN_PROFILE_CHANGE_COOLDOWN_MINUTES,
        "update your admin profile"
    )

    admin.name = data.name.strip()
    admin.phone = normalized_phone
    admin.profile_updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(admin)

    return {
        "message": "Admin profile updated successfully",
        "profile": AdminProfileOut.model_validate(admin)
    }


@router.post("/admin/change-password", status_code=status.HTTP_200_OK)
def change_admin_password(
    data: AdminChangePasswordIn,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(manager)
):
    _require_active_admin(current_user)

    if data.new_password != data.confirm_password:
        raise HTTPException(status_code=400, detail="New password and confirm password do not match")

    admin = db.query(Admin).filter(Admin.admin_id == current_user.admin_id).first()
    if not admin:
        raise HTTPException(status_code=404, detail="Admin profile not found")

    # Server-side verification blocks any UI bypass attempts.
    if not pwd.verify(data.old_password, admin.password):
        raise HTTPException(status_code=400, detail="Old password is incorrect")

    if pwd.verify(data.new_password, admin.password):
        raise HTTPException(status_code=400, detail="New password must be different from old password")

    _enforce_change_cooldown(
        admin.password_updated_at,
        ADMIN_PASSWORD_CHANGE_COOLDOWN_MINUTES,
        "change your password"
    )

    admin.password = pwd.hash(data.new_password)
    admin.password_updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(admin)

    try:
        _send_email(
            recipient=admin.email,
            subject="FitPro Admin Password Changed",
            body=(
                f"Hello {admin.name},\n\n"
                "Your FitPro admin account password was changed successfully.\n"
                "If this was not you, contact support immediately.\n\n"
                f"Time (UTC): {admin.password_updated_at}\n"
            )
        )
    except Exception:
        # Password is already updated; avoid rolling back credentials on email errors.
        pass

    return {"message": "Password updated successfully"}


@router.post("/admin/profile/photo", status_code=status.HTTP_200_OK)
def upload_admin_profile_photo(
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: Admin = Depends(manager)
):
    _require_active_admin(current_user)

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

    admin = db.query(Admin).filter(Admin.admin_id == current_user.admin_id).first()
    if not admin:
        raise HTTPException(status_code=404, detail="Admin profile not found")

    _enforce_change_cooldown(
        admin.profile_updated_at,
        ADMIN_PROFILE_CHANGE_COOLDOWN_MINUTES,
        "update your admin profile"
    )

    try:
        upload_result = cloudinary.uploader.upload(
            io.BytesIO(image_bytes),
            folder="fitprogym/admins",
            public_id=f"admin_{admin.admin_id}_profile",
            overwrite=True,
            invalidate=True,
            resource_type="image"
        )
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Cloudinary upload failed: {error}") from error

    secure_url = upload_result.get("secure_url")
    if not secure_url:
        raise HTTPException(status_code=500, detail="Failed to upload image to Cloudinary")

    admin.profile_photo = secure_url
    admin.profile_updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(admin)

    return {
        "message": "Admin profile photo updated successfully",
        "profile_photo": secure_url,
        "profile": AdminProfileOut.model_validate(admin)
    }


@router.get("/super-admin/admins", status_code=status.HTTP_200_OK)
def get_all_admins_for_super_admin(
    db: Session = Depends(get_db),
    current_user: Admin = Depends(manager)
):
    _require_super_admin(current_user)

    admins = db.query(Admin).order_by(Admin.created_at.desc()).all()
    all_admins = [AdminProfileOut.model_validate(admin).model_dump(mode="json") for admin in admins]

    total_admins = len(all_admins)
    active_admins = sum(1 for admin in all_admins if admin.get("is_active"))
    super_admins = sum(1 for admin in all_admins if admin.get("is_super_admin"))

    return {
        "summary": {
            "total_admins": total_admins,
            "active_admins": active_admins,
            "inactive_admins": max(total_admins - active_admins, 0),
            "super_admins": super_admins
        },
        "admins": all_admins
    }


@router.post("/super-admin/admins", status_code=status.HTTP_201_CREATED)
def create_admin_by_super_admin(
    data: AdminCreateBySuperAdmin,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(manager)
):
    _require_super_admin(current_user)

    email_normalized = data.email.strip().lower()
    phone_normalized = data.phone.strip()

    if not PHONE_REGEX.fullmatch(phone_normalized):
        raise HTTPException(status_code=400, detail="Please enter a valid phone number.")

    existing_admin = db.query(Admin).filter(Admin.email == email_normalized).first()
    if existing_admin:
        raise HTTPException(status_code=400, detail="Admin email already exists")

    hashed_password = pwd.hash(data.password)
    new_admin = Admin(
        name=data.name.strip(),
        email=email_normalized,
        phone=phone_normalized,
        password=hashed_password,
        password_updated_at=datetime.now(timezone.utc),
        is_super_admin=False
    )

    db.add(new_admin)
    db.commit()
    db.refresh(new_admin)

    return {
        "message": "Admin created successfully",
        "admin": AdminProfileOut.model_validate(new_admin)
    }


@router.post("/super-admin/admins/{admin_id}/force-password-reset", status_code=status.HTTP_200_OK)
def force_admin_password_reset_by_super_admin(
    admin_id: str,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(manager)
):
    _require_super_admin(current_user)

    try:
        valid_admin_id = uuid.UUID(admin_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid admin ID format")

    target_admin = db.query(Admin).filter(Admin.admin_id == valid_admin_id).first()
    if not target_admin:
        raise HTTPException(status_code=404, detail="Admin not found")

    try:
        reset_link, expires_at = _issue_admin_password_reset_token(target_admin, db)
        _send_email(
            recipient=target_admin.email,
            subject="FitPro Admin Password Reset Link",
            body=(
                f"Hello {target_admin.name},\n\n"
                "A password reset was requested for your FitPro admin account.\n"
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


@router.post("/admin/reset-password/confirm", status_code=status.HTTP_200_OK)
def confirm_admin_password_reset(
    data: AdminResetPasswordConfirmIn,
    db: Session = Depends(get_db)
):
    if data.new_password != data.confirm_password:
        raise HTTPException(status_code=400, detail="New password and confirm password do not match")

    now_utc = datetime.now(timezone.utc)
    token_hash = hashlib.sha256(data.token.encode("utf-8")).hexdigest()
    token_row = db.query(AdminPasswordResetToken).filter(
        AdminPasswordResetToken.token_hash == token_hash
    ).first()

    if not token_row:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    if token_row.used:
        raise HTTPException(status_code=400, detail="Reset token already used")

    if token_row.expires_at < now_utc:
        token_row.used = True
        db.commit()
        raise HTTPException(status_code=400, detail="Reset token expired")

    admin = db.query(Admin).filter(Admin.admin_id == token_row.admin_id).first()
    if not admin:
        token_row.used = True
        db.commit()
        raise HTTPException(status_code=404, detail="Admin not found for this token")

    admin.password = pwd.hash(data.new_password)
    admin.password_updated_at = now_utc
    token_row.used = True

    db.query(AdminPasswordResetToken).filter(
        AdminPasswordResetToken.admin_id == admin.admin_id,
        AdminPasswordResetToken.used.is_(False)
    ).update({AdminPasswordResetToken.used: True}, synchronize_session=False)

    db.commit()
    db.refresh(admin)

    try:
        _send_email(
            recipient=admin.email,
            subject="FitPro Admin Password Reset Successful",
            body=(
                f"Hello {admin.name},\n\n"
                "Your FitPro admin password has been reset successfully.\n"
                "If this was not you, contact support immediately.\n\n"
                f"Time (UTC): {admin.password_updated_at}\n"
            )
        )
    except Exception:
        pass

    return {"message": "Password reset successful"}


@router.patch("/super-admin/admins/{admin_id}/restore-access", status_code=status.HTTP_200_OK)
def restore_admin_access_by_super_admin(
    admin_id: str,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(manager)
):
    _require_super_admin(current_user)

    try:
        valid_admin_id = uuid.UUID(admin_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid admin ID format")

    target_admin = db.query(Admin).filter(Admin.admin_id == valid_admin_id).first()
    if not target_admin:
        raise HTTPException(status_code=404, detail="Admin not found")

    if target_admin.is_active:
        return {
            "message": "Admin already active",
            "admin": AdminProfileOut.model_validate(target_admin)
        }

    target_admin.is_active = True
    db.commit()
    db.refresh(target_admin)

    return {
        "message": "Admin access restored successfully",
        "admin": AdminProfileOut.model_validate(target_admin)
    }


@router.patch("/super-admin/admins/{admin_id}/remove-access", status_code=status.HTTP_200_OK)
def remove_admin_access_by_super_admin(
    admin_id: str,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(manager)
):
    _require_super_admin(current_user)

    try:
        valid_admin_id = uuid.UUID(admin_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid admin ID format")

    target_admin = db.query(Admin).filter(Admin.admin_id == valid_admin_id).first()
    if not target_admin:
        raise HTTPException(status_code=404, detail="Admin not found")

    if target_admin.admin_id == current_user.admin_id:
        raise HTTPException(status_code=400, detail="You cannot remove your own admin access")

    if target_admin.is_super_admin:
        raise HTTPException(status_code=400, detail="Cannot remove access for super admin account")

    if not target_admin.is_active:
        return {
            "message": "Admin access already removed",
            "admin": AdminProfileOut.model_validate(target_admin)
        }

    target_admin.is_active = False
    db.commit()
    db.refresh(target_admin)

    return {
        "message": "Admin access removed successfully",
        "admin": AdminProfileOut.model_validate(target_admin)
    }
