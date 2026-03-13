from sqlalchemy import Column, Integer, String, Boolean, text, DateTime, Text, ForeignKey
from .database import Base
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.dialects.postgresql import ARRAY


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    phone = Column(String, nullable=False)
    address = Column(String, nullable=False)
    fitness_goal = Column(String, nullable=False)
    experience_level = Column(String, nullable=False)
    password = Column(String, nullable=False)
    auth_provider = Column(String, nullable=False, server_default="'password'")
    google_sub = Column(String, unique=True)
    password_login_enabled = Column(Boolean, nullable=False, server_default="true")
    email_verified = Column(Boolean, nullable=False, server_default="true")
    profile_photo = Column(Text)
    role = Column(String, server_default="'member'")
    is_active = Column(Boolean, server_default="true")
    token_version = Column(Integer, nullable=False, server_default="0")
    user_id = Column(UUID(as_uuid=True),
                     server_default=text("gen_random_uuid()"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True),
                        server_default=func.now(), onupdate=func.now())
    profile_updated_at = Column(DateTime(timezone=True))
    last_login = Column(DateTime(timezone=True), server_default=func.now())
    password_changes_at = Column(DateTime(timezone=True))


class Trainer(Base):
    __tablename__ = "trainers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    password = Column(String, nullable=False)
    email_verified = Column(Boolean, nullable=False, server_default="true")
    phone = Column(String, nullable=False)
    address = Column(String, nullable=False)
    short_bio = Column(String, nullable=False)
    experience_years = Column(Integer, nullable=False)
    is_active = Column(Boolean, server_default="true")
    token_version = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True),
                        server_default=func.now(), onupdate=func.now())
    last_login = Column(DateTime(timezone=True))
    trainer_id = Column(UUID(as_uuid=True),
                        server_default=text("gen_random_uuid()"))
    role = Column(String, nullable=False, server_default="'trainer'")
    specializations = Column(ARRAY(Text), nullable=False)
    certifications = Column(ARRAY(Text))
    password_changes_at = Column(DateTime(timezone=True))
    password_updated_at = Column(DateTime(timezone=True))
    profile_photo = Column(Text)
    profile_updated_at = Column(DateTime(timezone=True))
    base_salary = Column(Integer, nullable=False, server_default="0")
    bonus_per_client = Column(Integer, nullable=False, server_default="0")
    compensation_notes = Column(Text)


class Admin(Base):
    __tablename__ = "admins"
    id = Column(Integer, primary_key=True, index=True)
    admin_id = Column(UUID(as_uuid=True),
                      server_default=text("gen_random_uuid()"))
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    password = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    profile_photo = Column(Text)
    is_active = Column(Boolean,  server_default="true")
    token_version = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True),
                        server_default=func.now(), onupdate=func.now())
    last_login = Column(DateTime(timezone=True))
    password_updated_at = Column(DateTime(timezone=True))
    profile_updated_at = Column(DateTime(timezone=True))
    is_super_admin = Column(Boolean, server_default="false")
    role = Column(String, server_default="'admin'")




class Plans(Base):
    __tablename__ = "plans"
    id = Column(Integer, primary_key=True, index=True)
    plan_name = Column(String, nullable=False)
    price = Column(Integer, nullable=False)
    description = Column(String, nullable=False)
    features = Column(ARRAY(Text), nullable=False)
    popular = Column(Boolean, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True),
                        server_default=func.now(), onupdate=func.now())
    duration = Column(String, nullable=False, server_default="'month'")


class Notifications(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True, index=True)
    message = Column(String, nullable=False)
    recipient_id = Column(UUID(as_uuid=True))
    recipient_role = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class NotificationStatus(Base):
    __tablename__ = "notification_status"
    id = Column(Integer, primary_key=True, index=True)
    notification_id = Column(Integer, ForeignKey("notifications.id"))
    recipient_id = Column(UUID(as_uuid=True))
    recipient_role = Column(String)
    is_read = Column(Boolean, server_default="false")
    is_deleted = Column(Boolean, server_default="false")




class QrSessions(Base):
    __tablename__ = "qr_sessions"
    id = Column(Integer, primary_key=True, index=True)
    token_id = Column(UUID(as_uuid=True),
                      server_default=text("gen_random_uuid()"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), server_default=text(
        "now() + interval '30 seconds'"))
    is_used = Column(Boolean, server_default="false")


class Attendance(Base):
    __tablename__ = "attendances"
    id = Column(Integer, primary_key=True, index=True)
    check_in_time = Column(DateTime(timezone=True), server_default=func.now())
    check_out_time = Column(DateTime(timezone=True))
    verified_by_admin = Column(Boolean, server_default="false")
    user_id = Column(UUID(as_uuid=True), nullable=False)
    token_used = Column(UUID(as_uuid=True))
    auto_checkout = Column(Boolean, server_default="true")


class TrainerClient(Base):
    __tablename__ = "trainers_client"
    id = Column(Integer, primary_key=True)
    trainer_id = Column(UUID(as_uuid=True), ForeignKey("trainers.trainer_id"))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.user_id"))
    assign_at = Column(DateTime(timezone=True), server_default=func.now())
    is_active = Column(Boolean, server_default="true")

class TrainersAttendance(Base):
    __tablename__ = "trainers_attendances"
    id = Column(Integer, primary_key=True, index=True)
    check_in_time = Column(DateTime(timezone=True), server_default=func.now())
    check_out_time = Column(DateTime(timezone=True))
    trainer_id = Column(UUID(as_uuid=True), nullable=False)
    auto_checkout = Column(Boolean, server_default="true")


class AdminPasswordResetToken(Base):
    __tablename__ = "admin_password_reset_tokens"
    id = Column(Integer, primary_key=True, index=True)
    admin_id = Column(UUID(as_uuid=True), nullable=False)
    token_hash = Column(String, nullable=False, unique=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used = Column(Boolean, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class UserPasswordResetToken(Base):
    __tablename__ = "user_password_reset_tokens"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    token_hash = Column(String, nullable=False, unique=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used = Column(Boolean, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class TrainerPasswordResetToken(Base):
    __tablename__ = "trainer_password_reset_tokens"
    id = Column(Integer, primary_key=True, index=True)
    trainer_id = Column(UUID(as_uuid=True), nullable=False)
    token_hash = Column(String, nullable=False, unique=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used = Column(Boolean, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class UserEmailVerificationToken(Base):
    __tablename__ = "user_email_verification_tokens"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    token_hash = Column(String, nullable=False, unique=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used = Column(Boolean, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class TrainerEmailVerificationToken(Base):
    __tablename__ = "trainer_email_verification_tokens"
    id = Column(Integer, primary_key=True, index=True)
    trainer_id = Column(UUID(as_uuid=True), nullable=False)
    token_hash = Column(String, nullable=False, unique=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used = Column(Boolean, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
