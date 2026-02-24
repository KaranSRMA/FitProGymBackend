from pydantic import BaseModel
from datetime import datetime



class PlansCreate(BaseModel):
    plan_name:str
    price : int
    description: str
    features: list[str]
    popular: bool
    duration: str

    class Config:
        from_attributes = True


class PlansOut(BaseModel):
    id:int
    plan_name:str
    price : int
    description: str
    features: list[str]
    popular: bool
    created_at: datetime
    updated_at: datetime
    duration: str

    class Config:
        from_attributes = True


class PlansPublicOut(BaseModel):
    id:int
    plan_name:str
    price : int
    description: str
    features: list[str]
    popular: bool
    duration: str

    class Config:
        from_attributes = True