from pydantic import BaseModel, EmailStr, Field
from uuid import UUID
from datetime import datetime


class TrainerCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    email: EmailStr
    password: str = Field(min_length=6, max_length=100)
    phone: str = Field(min_length=10, max_length=12)
    address: str = Field(min_length=10, max_length=500)
    specializations: list[str] = Field(min_length=1, max_length=3)
    short_bio: str = Field(min_length=5, max_length=100)
    experience_years: int = Field(ge=1, le=30)
    certifications: list[str] = Field(min_length=1, max_length=3)


class TrainerOut(BaseModel):
    id: int
    name: str
    email: EmailStr
    phone: str
    address: str
    role: str
    short_bio: str
    experience_years: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_login: datetime | None = None
    trainer_id: UUID
    specializations: list[str]
    certifications: list[str]

    class Config:
        from_attributes = True


class TrainerPublicOut(BaseModel):
    id: int
    name: str
    specializations: list[str]
    certifications: list[str]
    short_bio: str
    experience_years: int

    class Config:
        from_attributes = True
