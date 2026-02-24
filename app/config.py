from dotenv import load_dotenv
import os

load_dotenv()  # load .env once

SECRET_KEY = os.getenv("SECRET_KEY")
TOKEN_URL = os.getenv("TOKEN_URL")
DATABASE_URL = os.getenv("DATABASE_URL")
FRONTEND_URL= os.getenv("FRONTEND_URL")