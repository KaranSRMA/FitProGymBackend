from typing import Union
from pydantic import BaseModel, EmailStr, Field
from uuid import UUID
from datetime import datetime


class UserCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    email: EmailStr
    phone: str = Field(min_length=10, max_length=12)
    address: str = Field(min_length=10, max_length=500)
    fitnessGoal: str = Field(min_length=1, max_length=50)
    experienceLevel: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=6, max_length=100)


class UserOut(BaseModel):
    id: int
    name: str
    email: EmailStr
    phone: str
    address: str
    fitness_goal: str
    experience_level: str
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_login: datetime
    user_id: UUID

    class Config:
        from_attributes = True




class SearchQuery(BaseModel):
    search: Union[EmailStr,UUID]

    class Config:
        extra = "ignore"
