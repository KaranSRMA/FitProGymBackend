from dotenv import load_dotenv
import os
from pathlib import Path

# Always resolve backend/.env regardless of current working directory.
env_path = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=env_path)

SECRET_KEY = os.getenv("SECRET_KEY")
TOKEN_URL = os.getenv("TOKEN_URL")
DATABASE_URL = os.getenv("DATABASE_URL")
FRONTEND_URL= os.getenv("FRONTEND_URL")
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET")
FRONTEND_APP_URL = os.getenv("FRONTEND_APP_URL") or FRONTEND_URL
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
GOOGLE_OAUTH_REDIRECT_URI = os.getenv("GOOGLE_OAUTH_REDIRECT_URI")
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL") or SMTP_USER
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL") or "FitPro <onboarding@resend.dev>"
PASSWORD_RESET_TOKEN_HOURS = int(os.getenv("PASSWORD_RESET_TOKEN_HOURS", "1"))
ADMIN_PROFILE_CHANGE_COOLDOWN_MINUTES = int(os.getenv("ADMIN_PROFILE_CHANGE_COOLDOWN_MINUTES", "10"))
ADMIN_PASSWORD_CHANGE_COOLDOWN_MINUTES = int(os.getenv("ADMIN_PASSWORD_CHANGE_COOLDOWN_MINUTES", "10"))
MEMBER_PROFILE_CHANGE_COOLDOWN_MINUTES = int(os.getenv("MEMBER_PROFILE_CHANGE_COOLDOWN_MINUTES", "10"))
