from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import Plans, Admin, User
from app.routers.auth import manager
from app.schemas.plans_schema import PlansCreate, PlansOut, PlansPublicOut


router = APIRouter(prefix='/api', tags=["PLANS"])


async def get_optional_user(request: Request):
    try:
        return await manager(request)
    except Exception:
        return None


@router.post("/createPlans", status_code=status.HTTP_201_CREATED)
def create_plans(data: PlansCreate, db: Session = Depends(get_db), current_user: Admin = Depends(manager)):
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )
    plan = data.plan_name.strip()
    existing_plan = db.query(Plans).filter(Plans.plan_name == plan).first()
    if existing_plan:
        raise HTTPException(
            status_code=400, detail="Plan with this name already exists")

    if data.popular is True:
        db.query(Plans).update({Plans.popular: False})

    new_plan = Plans(
        plan_name=data.plan_name.strip(),
        price=data.price,
        description=data.description.strip(),
        features=data.features,
        popular=data.popular,
        duration=data.duration
    )
    db.add(new_plan)
    db.commit()
    db.refresh(new_plan)

    return {"message": "Plan added successfully.", "id": new_plan.id}


@router.get("/plans", status_code=status.HTTP_200_OK)
def get_plans(db: Session = Depends(get_db), current_user= Depends(get_optional_user)):
    is_admin = current_user and current_user.role == "admin" and current_user.is_active
    plans = db.query(Plans)

    if is_admin:
        plan = [PlansOut.model_validate(p) for p in plans]
    else:
        plan = [PlansPublicOut.model_validate(p) for p in plans]

    return {"plans": plan}


@router.put("/editPlan", status_code=status.HTTP_200_OK)
def edit_plan(id: int, data: PlansCreate, db: Session = Depends(get_db), current_user: Admin = Depends(manager)):
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )

    plan = db.query(Plans).filter(Plans.id == id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found!")

    if data.popular is True:
        db.query(Plans).filter(Plans.id != id).update({Plans.popular: False})

    plan.plan_name = data.plan_name
    plan.price = data.price
    plan.description = data.description
    plan.features = data.features
    plan.popular = data.popular
    plan.duration = data.duration
    db.commit()
    db.refresh(plan)
    return {"message": "Plan updated successfully."}


@router.delete("/deletePlan", status_code=status.HTTP_200_OK)
def delete_plan(id: int, db: Session = Depends(get_db), current_user: Admin = Depends(manager)):
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Account is inactive"
        )

    plan = db.query(Plans).filter(Plans.id == id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found!")
    try:
        db.delete(plan)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500, detail="Database error during deletion")

    return {"message": "Plan deleted successfully"}
