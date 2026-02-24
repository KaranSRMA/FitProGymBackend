from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.db import models
from app.db.database import engine
from app.routers import auth, users, trainers, plans, notifications, checkIn
from app.config import FRONTEND_URL


models.Base.metadata.create_all(bind=engine)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL],
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