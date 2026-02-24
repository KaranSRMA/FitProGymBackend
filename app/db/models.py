from sqlalchemy import Column, Integer, String, Boolean, text, DateTime, Text
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
    role = Column(String, server_default="'member'")
    is_active = Column(Boolean, server_default="true")
    user_id = Column(UUID(as_uuid=True),
                     server_default=text("gen_random_uuid()"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True),
                        server_default=func.now(), onupdate=func.now())
    last_login = Column(DateTime(timezone=True), server_default=func.now())


class Trainer(Base):
    __tablename__ = "trainers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    password = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    address = Column(String, nullable=False)
    short_bio = Column(String, nullable=False)
    experience_years = Column(Integer, nullable=False)
    is_active = Column(Boolean, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True),
                        server_default=func.now(), onupdate=func.now())
    last_login = Column(DateTime(timezone=True))
    trainer_id = Column(UUID(as_uuid=True),
                        server_default=text("gen_random_uuid()"))
    role = Column(String, nullable=False, server_default="'trainer'")
    specializations = Column(ARRAY(Text), nullable=False)
    certifications = Column(ARRAY(Text))


class Admin(Base):
    __tablename__ = "admins"
    id = Column(Integer, primary_key=True, index=True)
    admin_id = Column(UUID(as_uuid=True),
                      server_default=text("gen_random_uuid()"))
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    password = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    is_active = Column(Boolean,  server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True),
                        server_default=func.now(), onupdate=func.now())
    last_login = Column(DateTime(timezone=True))
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
    is_read = Column(Boolean, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


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
    token_used = Column(UUID(as_uuid=True), nullable=False)