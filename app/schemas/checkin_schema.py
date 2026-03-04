from pydantic import BaseModel, EmailStr


class ManualCheckInRequest(BaseModel):
    email: EmailStr
