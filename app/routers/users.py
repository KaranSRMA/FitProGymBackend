from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import User, Admin
from app.routers.auth import manager
from app.schemas.user_schema import UserOut
from app.schemas.user_schema import SearchQuery
from sqlalchemy import cast, String, text
import uuid

router = APIRouter(prefix='/api', tags=["USERS"])


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

    users = db.query(User).offset(skip).limit(limit).all()
    safe_users = [UserOut.model_validate(u) for u in users]

    return {
        "users": safe_users,
        "total_users": total_users,
        "page": page,
        "limit": limit
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
