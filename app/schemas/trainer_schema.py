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
    profile_photo: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_login: datetime | None = None
    trainer_id: UUID
    specializations: list[str]
    certifications: list[str]
    clients_count: int = 0
    base_salary: int = 0
    bonus_per_client: int = 0
    compensation_notes: str | None = None

    class Config:
        from_attributes = True


class TrainerPublicOut(BaseModel):
    id: int
    trainer_id: UUID
    name: str
    specializations: list[str]
    certifications: list[str]
    short_bio: str
    experience_years: int
    profile_photo: str | None = None
    clients_count: int = 0

    class Config:
        from_attributes = True


class TrainerCompensationUpdate(BaseModel):
    base_salary: int | None = Field(default=None, ge=0, le=500000)
    bonus_per_client: int | None = Field(default=None, ge=0, le=100000)
    compensation_notes: str | None = Field(default=None, max_length=500)


class TrainerProfileOut(BaseModel):
    trainer_id: UUID
    name: str
    email: EmailStr
    phone: str
    is_active: bool
    profile_photo: str | None = None
    password_updated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    last_login: datetime | None = None

    class Config:
        from_attributes = True


class TrainerProfileUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    phone: str = Field(min_length=10, max_length=12)


class TrainerChangePasswordIn(BaseModel):
    old_password: str = Field(min_length=6, max_length=100)
    new_password: str = Field(min_length=6, max_length=100)
    confirm_password: str = Field(min_length=6, max_length=100)


class TrainerResetPasswordConfirmIn(BaseModel):
    token: str = Field(min_length=20, max_length=500)
    new_password: str = Field(min_length=6, max_length=100)
    confirm_password: str = Field(min_length=6, max_length=100)
