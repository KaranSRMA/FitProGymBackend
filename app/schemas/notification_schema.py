from pydantic import BaseModel
from uuid import UUID

class NotificationCreate(BaseModel):
    message: str
    recipient_id: str | None = None
    recipient_role: str



class NotificationRequest(BaseModel):
    notification_ids: list[int]

class NotificationSoftDelete(BaseModel):
    notification_ids: list[int]
    recipient_role: list[str]
    recipient_id: list[UUID | None]