from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, EmailStr, Field


class AdminProfileOut(BaseModel):
    admin_id: UUID
    name: str
    email: EmailStr
    phone: str
    is_active: bool
    is_super_admin: bool
    profile_photo: str | None = None
    password_updated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    last_login: datetime | None = None

    class Config:
        from_attributes = True


class AdminProfileUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    phone: str = Field(min_length=10, max_length=12)


class AdminCreateBySuperAdmin(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    email: EmailStr
    phone: str = Field(min_length=10, max_length=12)
    password: str = Field(min_length=6, max_length=100)


class AdminChangePasswordIn(BaseModel):
    old_password: str = Field(min_length=6, max_length=100)
    new_password: str = Field(min_length=6, max_length=100)
    confirm_password: str = Field(min_length=6, max_length=100)


class AdminResetPasswordConfirmIn(BaseModel):
    token: str = Field(min_length=20, max_length=500)
    new_password: str = Field(min_length=6, max_length=100)
    confirm_password: str = Field(min_length=6, max_length=100)
