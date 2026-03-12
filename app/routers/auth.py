from urllib import request as urllib_request
import json
import html
import mailtrap as mt
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from urllib import error as urllib_error, parse as urllib_parse, request as urllib_request

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from fastapi_login import LoginManager
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from app.config import (
    FRONTEND_APP_URL,
    GOOGLE_OAUTH_CLIENT_ID,
    GOOGLE_OAUTH_CLIENT_SECRET,
    GOOGLE_OAUTH_REDIRECT_URI,
    PASSWORD_RESET_TOKEN_HOURS,
    MAILTRAP_API_KEY,
    SECRET_KEY,
    TOKEN_URL,
)
from app.db.database import SessionLocal, get_db
from app.db.models import Admin, Notifications, Trainer, User, UserPasswordResetToken
from app.schemas.user_schema import UserCreate


manager = LoginManager(SECRET_KEY, token_url=TOKEN_URL, use_cookie=True)
router = APIRouter(prefix="/auth", tags=["AUTHENTICATION"])

pwd = CryptContext(schemes=["argon2"], deprecated="auto")

expire_duration = timedelta(hours=15)
seconds = int(expire_duration.total_seconds())

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_OAUTH_STATE_COOKIE = "google_oauth_state"
GOOGLE_OAUTH_MODE_COOKIE = "google_oauth_mode"
VALID_OAUTH_MODES = {"login", "signup"}
MAILTRAP_INBOX_ID = 4433988

client = mt.MailtrapClient(
  token=MAILTRAP_API_KEY,
  sandbox=True,
  inbox_id=MAILTRAP_INBOX_ID,
)


def _append_partitioned_cookie_flag(response: Response):
    cookie_header = response.headers.get("set-cookie")
    if cookie_header and "Partitioned" not in cookie_header:
        response.headers["set-cookie"] = f"{cookie_header}; Partitioned"


def _clear_auth_cookie(response: Response):
    response.delete_cookie(key=manager.cookie_name, path="/")


def _set_auth_cookie(response: Response, subject_id: str, role: str, is_active: bool):
    access_token = manager.create_access_token(
        data={"sub": subject_id, "role": role, "is_active": is_active},
        expires=expire_duration,
    )
    response.set_cookie(
        key=manager.cookie_name,
        value=access_token,
        httponly=True,
        samesite="none",
        secure=True,
        path="/",
        expires=seconds,
    )
    _append_partitioned_cookie_flag(response)


def _normalize_oauth_mode(mode: str | None) -> str:
    normalized_mode = (mode or "login").strip().lower()
    if normalized_mode not in VALID_OAUTH_MODES:
        return "login"
    return normalized_mode


def _oauth_entry_path(mode: str) -> str:
    return "/signup" if mode == "signup" else "/login"


def _build_frontend_url(path: str, params: dict[str, str] | None = None) -> str:
    base = (FRONTEND_APP_URL or "").rstrip("/")
    if not base:
        raise HTTPException(
            status_code=500, detail="FRONTEND_APP_URL is not configured.")

    normalized_path = path if path.startswith("/") else f"/{path}"
    if not params:
        return f"{base}{normalized_path}"

    return f"{base}{normalized_path}?{urllib_parse.urlencode(params)}"


def _clear_google_oauth_cookies(response: Response):
    response.delete_cookie(key=GOOGLE_OAUTH_STATE_COOKIE, path="/")
    response.delete_cookie(key=GOOGLE_OAUTH_MODE_COOKIE, path="/")


def _oauth_error_redirect(mode: str, message: str) -> RedirectResponse:
    response = RedirectResponse(
        url=_build_frontend_url(_oauth_entry_path(mode), {
                                "oauth_error": message}),
        status_code=status.HTTP_302_FOUND,
    )
    _clear_auth_cookie(response)
    _clear_google_oauth_cookies(response)
    return response


def _ensure_email_config():
    if not MAILTRAP_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Email is not configured.",
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
    mail = mt.Mail(
        sender=mt.Address(email="support@fitpro.com", name="Fitpro GYM"),
        to=[mt.Address(email=recipient)],
        subject=subject,
        text=body,
        html=_render_html_body(body),
    )

    client.send(mail)



def _notify_member_login(db: Session, member: User, source: str):
    try:
        db.add(
            Notifications(
                message=f"Successful login via {source}. If this wasn't you, change your password immediately.",
                recipient_id=member.user_id,
                recipient_role="member",
            )
        )
    except Exception:
        pass

    try:
        _send_email(
            recipient=member.email,
            subject="FitPro Login Alert",
            body=(
                f"Hello {member.name},\n\n"
                f"A successful login to your FitPro account just occurred via {source}.\n"
                "If this wasn't you, reset your password immediately.\n"
            ),
        )
    except Exception:
        pass


def _issue_member_password_reset_token(member: User, db: Session):
    now_utc = datetime.now(timezone.utc)
    db.query(UserPasswordResetToken).filter(
        UserPasswordResetToken.user_id == member.user_id,
        UserPasswordResetToken.used.is_(False),
    ).update({UserPasswordResetToken.used: True}, synchronize_session=False)

    plain_token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(plain_token.encode("utf-8")).hexdigest()
    token_row = UserPasswordResetToken(
        user_id=member.user_id,
        token_hash=token_hash,
        expires_at=now_utc + timedelta(hours=PASSWORD_RESET_TOKEN_HOURS),
        used=False,
    )
    db.add(token_row)
    db.commit()
    db.refresh(token_row)

    reset_link = f"{FRONTEND_APP_URL.rstrip('/')}/reset-password?token={plain_token}"
    return reset_link


def _ensure_google_oauth_config():
    if not GOOGLE_OAUTH_CLIENT_ID or not GOOGLE_OAUTH_CLIENT_SECRET or not GOOGLE_OAUTH_REDIRECT_URI:
        raise HTTPException(
            status_code=500,
            detail="Google OAuth is not configured. Set GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET and GOOGLE_OAUTH_REDIRECT_URI in backend .env",
        )


def _exchange_google_code_for_access_token(code: str) -> str:
    payload = urllib_parse.urlencode(
        {
            "client_id": GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
        }
    ).encode("utf-8")

    request_obj = urllib_request.Request(
        url=GOOGLE_TOKEN_ENDPOINT,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    try:
        with urllib_request.urlopen(request_obj, timeout=20) as response:
            token_payload = json.loads(response.read().decode("utf-8"))
    except urllib_error.HTTPError as error:
        response_payload = error.read().decode("utf-8", errors="ignore")
        raise HTTPException(
            status_code=502,
            detail=f"Google token exchange failed: {response_payload or f'HTTP {error.code}'}",
        ) from error
    except urllib_error.URLError as error:
        raise HTTPException(
            status_code=502,
            detail=f"Google token exchange failed: {error.reason}",
        ) from error

    access_token = token_payload.get("access_token")
    if not access_token:
        raise HTTPException(
            status_code=502, detail="Google token exchange failed: Missing access token")
    return access_token


def _fetch_google_profile(access_token: str) -> dict:
    request_obj = urllib_request.Request(
        url=GOOGLE_USERINFO_ENDPOINT,
        method="GET",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    try:
        with urllib_request.urlopen(request_obj, timeout=20) as response:
            profile = json.loads(response.read().decode("utf-8"))
    except urllib_error.HTTPError as error:
        response_payload = error.read().decode("utf-8", errors="ignore")
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch Google profile: {response_payload or f'HTTP {error.code}'}",
        ) from error
    except urllib_error.URLError as error:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch Google profile: {error.reason}",
        ) from error

    return profile


def _normalize_provider(value: str | None) -> str:
    provider = (value or "password").strip().lower()
    if provider not in {"password", "google"}:
        return "password"
    return provider


def _sanitize_member_name(raw_name: str | None, fallback_email: str) -> str:
    fallback_name = fallback_email.split("@", 1)[0]
    normalized_name = (raw_name or fallback_name or "Member").strip()
    if len(normalized_name) < 2:
        normalized_name = "Member"
    return normalized_name[:255]


@manager.user_loader()
def load_user(id: str):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.user_id == id).first()
        if user:
            return user

        trainer = db.query(Trainer).filter(Trainer.trainer_id == id).first()
        if trainer:
            return trainer

        admin = db.query(Admin).filter(Admin.admin_id == id).first()
        if admin:
            return admin

        return None
    finally:
        db.close()


@router.get("/google/login", status_code=status.HTTP_302_FOUND)
def google_oauth_login(mode: str = "login"):
    _ensure_google_oauth_config()
    normalized_mode = _normalize_oauth_mode(mode)
    state_token = secrets.token_urlsafe(32)

    authorize_query = urllib_parse.urlencode(
        {
            "client_id": GOOGLE_OAUTH_CLIENT_ID,
            "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state_token,
            "prompt": "select_account",
        }
    )
    response = RedirectResponse(
        url=f"{GOOGLE_AUTH_ENDPOINT}?{authorize_query}",
        status_code=status.HTTP_302_FOUND,
    )
    _clear_auth_cookie(response)
    response.set_cookie(
        key=GOOGLE_OAUTH_STATE_COOKIE,
        value=state_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=600,
        path="/",
    )
    response.set_cookie(
        key=GOOGLE_OAUTH_MODE_COOKIE,
        value=normalized_mode,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=600,
        path="/",
    )
    return response


@router.get("/google/callback", status_code=status.HTTP_302_FOUND)
def google_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    mode = _normalize_oauth_mode(request.cookies.get(GOOGLE_OAUTH_MODE_COOKIE))

    if error:
        return _oauth_error_redirect(mode, "Google sign-in failed or was cancelled.")

    expected_state = request.cookies.get(GOOGLE_OAUTH_STATE_COOKIE)
    if not state or not expected_state or state != expected_state:
        return _oauth_error_redirect(mode, "Google sign-in failed due to invalid state. Please try again.")

    if not code:
        return _oauth_error_redirect(mode, "Google sign-in failed because the authorization code is missing.")

    try:
        _ensure_google_oauth_config()
        access_token = _exchange_google_code_for_access_token(code)
        google_profile = _fetch_google_profile(access_token)
    except HTTPException:
        return _oauth_error_redirect(mode, "Google sign-in failed. Please try again.")

    google_sub = (google_profile.get("sub") or "").strip()
    email = (google_profile.get("email") or "").strip().lower()
    email_verified = bool(google_profile.get("email_verified"))
    google_name = google_profile.get("name")

    if not google_sub or not email:
        return _oauth_error_redirect(mode, "Google did not provide a valid account identity.")

    if not email_verified:
        return _oauth_error_redirect(mode, "Google account email is not verified. Use a verified Google account.")

    # Prevent cross-role collisions on the same email.
    if db.query(Admin).filter(Admin.email == email).first():
        return _oauth_error_redirect(mode, "This email belongs to an admin account. Continue with email login.")
    if db.query(Trainer).filter(Trainer.email == email).first():
        return _oauth_error_redirect(mode, "This email belongs to a trainer account. Continue with email login.")

    member = db.query(User).filter(User.email == email).first()
    if member:
        provider = _normalize_provider(member.auth_provider)
        if member.google_sub and member.google_sub != google_sub:
            return _oauth_error_redirect(mode, "This email is linked to a different Google account.")

        # Critical vulnerability fix:
        # Never auto-link a verified password account to OAuth. This blocks
        # attacker-created password credentials from gaining access later.
        if provider == "password" and not member.google_sub:
            if member.email_verified is False:
                # Pending/unverified password accounts can be safely claimed by verified Google ownership.
                member.auth_provider = "google"
                member.google_sub = google_sub
                member.password_login_enabled = False
                member.password = pwd.hash(secrets.token_urlsafe(48))
                member.email_verified = True
            else:
                return _oauth_error_redirect(
                    mode,
                    "This email is already registered with password login. Continue with Email to sign in.",
                )
        else:
            member.auth_provider = "google"
            member.google_sub = member.google_sub or google_sub
            member.password_login_enabled = False
            member.email_verified = True

        if not member.is_active:
            return _oauth_error_redirect(mode, "Your account is deactivated.")

        if not member.name or len(member.name.strip()) < 2:
            member.name = _sanitize_member_name(google_name, email)

        member.last_login = func.now()
        _notify_member_login(db, member, "Google")
        db.commit()
        db.refresh(member)

        success_response = RedirectResponse(
            url=_build_frontend_url("/dashboard"),
            status_code=status.HTTP_302_FOUND,
        )
        _set_auth_cookie(success_response, str(member.user_id),
                         "member", bool(member.is_active))
        _clear_google_oauth_cookies(success_response)
        return success_response

    new_member = User(
        name=_sanitize_member_name(google_name, email),
        email=email,
        phone="9000000000",
        address="Update your address from profile settings",
        fitness_goal="general_fitness",
        experience_level="beginner",
        password=pwd.hash(secrets.token_urlsafe(48)),
        auth_provider="google",
        google_sub=google_sub,
        password_login_enabled=False,
        email_verified=True,
    )
    db.add(new_member)
    db.commit()
    db.refresh(new_member)
    _notify_member_login(db, new_member, "Google")
    db.commit()

    success_response = RedirectResponse(
        url=_build_frontend_url("/dashboard"),
        status_code=status.HTTP_302_FOUND,
    )
    _set_auth_cookie(success_response, str(new_member.user_id),
                     "member", bool(new_member.is_active))
    _clear_google_oauth_cookies(success_response)
    return success_response


# user registration
@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_user(data: UserCreate, response: Response, db: Session = Depends(get_db)):
    email_normalized = data.email.strip().lower()
    existing_user = db.query(User).filter(
        User.email == email_normalized).first()
    if existing_user:
        if _normalize_provider(existing_user.auth_provider) == "google" or not bool(existing_user.password_login_enabled):
            raise HTTPException(
                status_code=400,
                detail="This email is already registered with Google sign-in. Please continue with Google.",
            )
        raise HTTPException(status_code=400, detail="Email already exists")

    if db.query(Admin).filter(Admin.email == email_normalized).first() or db.query(Trainer).filter(
        Trainer.email == email_normalized
    ).first():
        raise HTTPException(
            status_code=400, detail="This email cannot be used for member registration")

    new_user = User(
        name=data.name.strip(),
        email=email_normalized,
        phone=data.phone.strip(),
        address=data.address.strip(),
        fitness_goal=data.fitnessGoal.strip(),
        experience_level=data.experienceLevel.strip(),
        password=pwd.hash(data.password),
        auth_provider="password",
        google_sub=None,
        password_login_enabled=True,
        email_verified=True,
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    _set_auth_cookie(response, str(new_user.user_id),
                     "member", bool(new_user.is_active))
    return {"message": "User registered successfully."}


# user login
@router.post("/login", status_code=status.HTTP_200_OK)
async def login(request: Request, response: Response, db: Session = Depends(get_db)):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body")

    email = body.get("email", "").strip().lower()
    password = body.get("password", "")

    if email and password:
        user = db.query(User).filter(User.email == email).first()
        trainer = db.query(Trainer).filter(Trainer.email == email).first()
        admin = db.query(Admin).filter(Admin.email == email).first()

        if not user and not trainer and not admin:
            raise HTTPException(
                status_code=401, detail="Invalid email or password")

        if user:
            provider = _normalize_provider(user.auth_provider)
            if provider == "google" or not bool(user.password_login_enabled):
                raise HTTPException(
                    status_code=400,
                    detail="This account uses Google sign-in. Please continue with Google.",
                )

            if user.email_verified is False:
                raise HTTPException(
                    status_code=403, detail="Please verify your email before logging in.")

            if not pwd.verify(password, user.password):
                raise HTTPException(
                    status_code=401, detail="Invalid email or password")

            if not user.is_active:
                raise HTTPException(
                    status_code=403, detail="Account is deactiated!")

            user.last_login = func.now()
            _notify_member_login(db, user, "Email/Password")
            db.commit()
            _set_auth_cookie(response, str(user.user_id),
                             "member", bool(user.is_active))
            return {"message": "Login successful", "role": "member", "is_active": user.is_active, "valid": True}

        if trainer:
            if not pwd.verify(password, trainer.password):
                raise HTTPException(
                    status_code=401, detail="Invalid email or password")
            if not trainer.is_active:
                raise HTTPException(
                    status_code=403, detail="Account is deactiated!")

            trainer.last_login = func.now()
            db.commit()
            _set_auth_cookie(response, str(trainer.trainer_id),
                             "trainer", bool(trainer.is_active))
            return {"message": "Login successful", "role": "trainer", "is_active": trainer.is_active, "valid": True}

        if admin:
            if not pwd.verify(password, admin.password):
                raise HTTPException(
                    status_code=401, detail="Invalid email or password")
            if not admin.is_active:
                raise HTTPException(
                    status_code=403, detail="Account is deactiated!")

            admin.last_login = func.now()
            db.commit()
            _set_auth_cookie(response, str(admin.admin_id),
                             "admin", bool(admin.is_active))
            return {
                "message": "Login successful",
                "role": "admin",
                "is_active": admin.is_active,
                "valid": True,
                "is_super_admin": bool(admin.is_super_admin),
            }

    raise HTTPException(
        status_code=400, detail="Email and password are required")


@router.post("/forgot-password", status_code=status.HTTP_200_OK)
async def forgot_password(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body")

    email = str(body.get("email", "")).strip().lower()
    if not email:
        print("noemail")
        raise HTTPException(status_code=400, detail="Email is required")

    member = db.query(User).filter(User.email == email).first()
    generic_response = {
        "message": "If an account exists for this email, a reset link has been sent."
    }

    if not member:
        return generic_response

    if _normalize_provider(member.auth_provider) == "google" or not bool(member.password_login_enabled):
        return generic_response

    try:
        reset_link = _issue_member_password_reset_token(member, db)
        _send_email(
            recipient=member.email,
            subject="FitPro Password Reset Link",
            body=(
                f"Hello {member.name},\n\n"
                "A password reset was requested for your FitPro account.\n"
                "Use this secure link to reset your password:\n"
                f"{reset_link}\n\n"
                f"This link expires in {PASSWORD_RESET_TOKEN_HOURS} hour(s).\n"
                "If you did not request this, you can ignore this email.\n"
            ),
        )
    except Exception:
        import traceback
        traceback.print_exc()
        return generic_response

    return generic_response


@router.post("/reset-password/confirm", status_code=status.HTTP_200_OK)
async def confirm_password_reset(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body")

    token = str(body.get("token", "")).strip()
    new_password = str(body.get("new_password", ""))
    confirm_password = str(body.get("confirm_password", ""))

    if not token:
        raise HTTPException(status_code=400, detail="Reset token is required")
    if len(new_password) < 6 or len(confirm_password) < 6:
        raise HTTPException(
            status_code=400, detail="Password must be at least 6 characters")
    if new_password != confirm_password:
        raise HTTPException(
            status_code=400, detail="New password and confirm password do not match")

    now_utc = datetime.now(timezone.utc)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    token_row = db.query(UserPasswordResetToken).filter(
        UserPasswordResetToken.token_hash == token_hash
    ).first()

    if not token_row:
        raise HTTPException(
            status_code=400, detail="Invalid or expired reset token")
    if token_row.used:
        raise HTTPException(status_code=400, detail="Reset token already used")
    if token_row.expires_at < now_utc:
        token_row.used = True
        db.commit()
        raise HTTPException(status_code=400, detail="Reset token expired")

    member = db.query(User).filter(User.user_id == token_row.user_id).first()
    if not member:
        token_row.used = True
        db.commit()
        raise HTTPException(
            status_code=404, detail="Member not found for this token")

    if _normalize_provider(member.auth_provider) == "google" or not bool(member.password_login_enabled):
        token_row.used = True
        db.commit()
        raise HTTPException(
            status_code=400, detail="This account uses Google sign-in")

    member.password = pwd.hash(new_password)
    member.password_changes_at = now_utc
    token_row.used = True
    db.query(UserPasswordResetToken).filter(
        UserPasswordResetToken.user_id == member.user_id,
        UserPasswordResetToken.used.is_(False)
    ).update({UserPasswordResetToken.used: True}, synchronize_session=False)
    db.commit()

    try:
        _send_email(
            recipient=member.email,
            subject="FitPro Password Reset Successful",
            body=(
                f"Hello {member.name},\n\n"
                "Your FitPro password has been reset successfully.\n"
                "If this was not you, contact support immediately.\n"
            ),
        )
    except Exception:
        pass

    return {"message": "Password reset successful"}


@router.post("/tokenVerification", status_code=status.HTTP_200_OK)
async def token(request: Request, response: Response, db: Session = Depends(get_db)):
    user = await manager.optional(request)
    if user:
        user.last_login = func.now()
        db.commit()
        if user.role == "member":
            return {
                "valid": True,
                "role": user.role,
                "is_active": user.is_active,
                "user_id": user.user_id,
                "is_super_admin": False,
            }
        if user.role == "trainer":
            return {
                "valid": True,
                "role": user.role,
                "is_active": user.is_active,
                "user_id": user.trainer_id,
                "is_super_admin": False,
            }
        if user.role == "admin":
            return {
                "valid": True,
                "role": user.role,
                "is_active": user.is_active,
                "user_id": user.admin_id,
                "is_super_admin": bool(user.is_super_admin),
            }
        response.delete_cookie(manager.cookie_name)
        return

    response.delete_cookie(manager.cookie_name)
    return {"valid": False, "role": "user", "is_active": False, "user_id": None, "is_super_admin": False}


@router.post("/logout")
def logout(response: Response):
    _clear_auth_cookie(response)
    _clear_google_oauth_cookies(response)
    return {"message": "Logged out successfully"}
