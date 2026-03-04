from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from app.db import models
from app.db.database import engine
from app.routers import auth, users, trainers, plans, notifications, checkIn, admins
from app.config import FRONTEND_URL


models.Base.metadata.create_all(bind=engine)
with engine.begin() as connection:
    connection.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_photo TEXT"))
    connection.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_updated_at TIMESTAMPTZ"))
    connection.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_provider VARCHAR(50) DEFAULT 'password'"))
    connection.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS google_sub VARCHAR(255)"))
    connection.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_login_enabled BOOLEAN DEFAULT true"))
    connection.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN DEFAULT true"))
    connection.execute(text("UPDATE users SET auth_provider = 'password' WHERE auth_provider IS NULL"))
    connection.execute(text("UPDATE users SET password_login_enabled = true WHERE password_login_enabled IS NULL"))
    connection.execute(text("UPDATE users SET email_verified = true WHERE email_verified IS NULL"))
    connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_google_sub ON users(google_sub) WHERE google_sub IS NOT NULL"))
    connection.execute(text("ALTER TABLE admins ADD COLUMN IF NOT EXISTS profile_photo TEXT"))
    connection.execute(text("ALTER TABLE admins ADD COLUMN IF NOT EXISTS password_updated_at TIMESTAMPTZ"))
    connection.execute(text("ALTER TABLE admins ADD COLUMN IF NOT EXISTS profile_updated_at TIMESTAMPTZ"))

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL,"http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth.router)
app.include_router(users.router)
app.include_router(trainers.router)
app.include_router(plans.router)
app.include_router(notifications.router)
app.include_router(checkIn.router)
app.include_router(admins.router)
