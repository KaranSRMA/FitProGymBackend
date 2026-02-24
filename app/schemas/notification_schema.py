from pydantic import BaseModel

class NotificationCreate(BaseModel):
    message: str
    recipient_id: str | None = None
    recipient_role: str



class NotificationReadRequest(BaseModel):
    notification_ids: list[int]