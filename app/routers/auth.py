from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from fastapi_login import LoginManager
from app.db.models import User, Trainer, Admin
from app.db.database import get_db
from app.db.database import SessionLocal
from app.schemas.user_schema import UserCreate
from datetime import timedelta
from sqlalchemy.sql import func
from app.config import SECRET_KEY, TOKEN_URL


manager = LoginManager(SECRET_KEY, token_url=TOKEN_URL, use_cookie=True)
router = APIRouter(prefix="/auth", tags=["AUTHENTICATION"])

pwd = CryptContext(schemes=["argon2"], deprecated="auto")

expire_duration = timedelta(hours=15)
seconds = int(expire_duration.total_seconds())


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


# user registration
@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_user(data: UserCreate, response: Response, db: Session = Depends(get_db)):
    email_normalized = data.email.strip().lower()
    existing_user = db.query(User).filter(
        User.email == email_normalized).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already exists")

    hashed_pass = pwd.hash(data.password)

    new_user = User(
        name=data.name.strip(),
        email=email_normalized,
        phone=data.phone.strip(),
        address=data.address.strip(),
        fitness_goal=data.fitnessGoal.strip(),
        experience_level=data.experienceLevel.strip(),
        password=hashed_pass
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    access_token = manager.create_access_token(data={"sub": str(
        new_user.user_id), "role": "member", "is_active": new_user.is_active}, expires=expire_duration)
    response.set_cookie(key="access-token", value=access_token, httponly=True,
                        samesite='none', secure=True, path='/', expires=seconds)

    cookie_header = response.headers.get("set-cookie")
    if cookie_header:
        response.headers["set-cookie"] = f"{cookie_header}; Partitioned"

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
        # Validate user
        user = db.query(User).filter(User.email == email).first()
        trainer = db.query(Trainer).filter(Trainer.email == email).first()
        admin = db.query(Admin).filter(Admin.email == email).first()

        if not user and not trainer and not admin:
            raise HTTPException(
                status_code=401, detail="Invalid email or password")
        # -------------------------------------------------------------------------------
        if user:
            if not pwd.verify(password, user.password):
                raise HTTPException(
                    status_code=401, detail="Invalid email or password")

            if not user.is_active:
                raise HTTPException(
                    status_code=403, detail="Account is deactiated!")

            user.last_login = func.now()
            db.commit()
            access_token = manager.create_access_token(data={"sub": str(
                user.user_id), "role": "member", "is_active": user.is_active}, expires=expire_duration)
            response.set_cookie(key="access-token", value=access_token, httponly=True,
                                samesite='none', secure=True, path='/', expires=seconds)
            cookie_header = response.headers.get("set-cookie")
            if cookie_header:
                response.headers["set-cookie"] = f"{cookie_header}; Partitioned"
            return {"message": "Login successful", "role": "member", "is_active": user.is_active, "valid": True}

        # -------------------------------------------------------------------------------

        if trainer:
            if not pwd.verify(password, trainer.password):
                raise HTTPException(
                    status_code=401, detail="Invalid email or password")
            if not trainer.is_active:
                raise HTTPException(
                    status_code=403, detail="Account is deactiated!")

            trainer.last_login = func.now()
            db.commit()
            access_token = manager.create_access_token(data={"sub": str(
                trainer.trainer_id), "role": "trainer", "is_active": trainer.is_active}, expires=expire_duration)
            response.set_cookie(key="access-token", value=access_token, httponly=True,
                                samesite='none', secure=True, path='/', expires=seconds)
            cookie_header = response.headers.get("set-cookie")
            if cookie_header:
                response.headers["set-cookie"] = f"{cookie_header}; Partitioned"
            return {"message": "Login successful", "role": "trainer", "is_active": trainer.is_active, "valid": True}
        # -------------------------------------------------------------------------------

        if admin:
            if not pwd.verify(password, admin.password):
                raise HTTPException(
                    status_code=401, detail="Invalid email or password")
            if not admin.is_active:
                raise HTTPException(
                    status_code=403, detail="Account is deactiated!")

            admin.last_login = func.now()
            db.commit()
            access_token = manager.create_access_token(data={"sub": str(
                admin.admin_id), "role": "admin", "is_active": admin.is_active}, expires=expire_duration)
            response.set_cookie(key="access-token", value=access_token, httponly=True,
                                samesite='none', secure=True, path='/', expires=seconds)
            
            cookie_header = response.headers.get("set-cookie")
            if cookie_header:
                response.headers["set-cookie"] = f"{cookie_header}; Partitioned"
            return {"message": "Login successful", "role": "admin", "is_active": admin.is_active, "valid": True}
        # -------------------------------------------------------------------------------


@router.post('/tokenVerification', status_code=status.HTTP_200_OK)
async def token(request: Request, response: Response, db: Session = Depends(get_db)):
    # If token valid -> auto login
    user = await manager.optional(request)
    if user:
        user.last_login = func.now()
        db.commit()
        if user.role == "member":
            return {"valid": True, "role": user.role, "is_active": user.is_active, "user_id": user.user_id}
        elif user.role == "trainer":
            return {"valid": True, "role": user.role, "is_active": user.is_active, "user_id": user.trainer_id}
        elif user.role == "admin":
            return {"valid": True, "role": user.role, "is_active": user.is_active, "user_id": user.admin_id}
        else:
            response.delete_cookie(manager.cookie_name)
            return

    else:
        response.delete_cookie(manager.cookie_name)
        return {"valid": False, "role": "user", "is_active": False, "user_id": None}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(manager.cookie_name)
    return {"message": "Logged out successfully"}
