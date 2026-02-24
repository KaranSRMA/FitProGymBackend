from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import Trainer, Admin, User
from app.routers.auth import manager
from app.schemas.trainer_schema import TrainerOut, TrainerCreate, TrainerPublicOut
from app.schemas.user_schema import SearchQuery
from sqlalchemy import cast, String
import uuid
from app.routers.auth import pwd


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
        query = query.filter(Trainer.is_active == True)

    trainers = query.offset(skip).limit(limit).all()

    if is_admin:
        safe_trainers = [TrainerOut.model_validate(t) for t in trainers]
    else:
        safe_trainers = [TrainerPublicOut.model_validate(t) for t in trainers]

    return {
        "trainers": safe_trainers,
        "total_trainers": len(safe_trainers),
        "page": page,
        "limit": limit,
        "access": "admin" if is_admin else "public"
    }


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

    safe_trainer = [TrainerOut.model_validate(trainer)]

    return {
        "trainer": safe_trainer
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
