from fastapi import APIRouter, Depends, HTTPException, status, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, desc
import uuid

from app.db.database import get_db
from app.db.models import Notifications, User, Trainer, Admin
from app.routers.auth import manager
from app.schemas.notification_schema import NotificationCreate, NotificationReadRequest

router = APIRouter(prefix='/api', tags=["NOTIFICATIONS"])


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, dict] = {}

    async def connect(self, websocket: WebSocket, recipient_id: str, recipient_role: str):
        if recipient_id in self.active_connections:
            await self.active_connections[recipient_id]["ws"].close()

        self.active_connections[recipient_id] = {
            "ws": websocket,
            "recipient_role": recipient_role
        }

    def disconnect(self, recipient_id: str):
        if recipient_id in self.active_connections:
            del self.active_connections[recipient_id]

    async def send_personal_message(self, message: dict, recipient_id: str):
        connection = self.active_connections.get(recipient_id)
        if connection:
            try:
                await connection["ws"].send_json(message)
            except Exception:
                pass

    async def broadcast(self, message: dict, recipient_role: str):
        for connection in self.active_connections.values():
            should_send = False
            target_role = connection["recipient_role"]

            if recipient_role == 'all':
                should_send = True
            elif recipient_role == 'allMembers' and target_role == 'member':
                should_send = True
            elif recipient_role == 'allTrainers' and target_role == 'trainer':
                should_send = True
            elif recipient_role == 'allAdmins' and target_role == 'admin':
                should_send = True

            if should_send:
                try:
                    await connection["ws"].send_json(message)
                except Exception:
                    pass


ws_manager = ConnectionManager()


@router.websocket("/ws/notifications/{recipient_id}/{recipient_role}")
async def websocket_endpoint(
    websocket: WebSocket,
    recipient_id: str,
    recipient_role: str,
    db: Session = Depends(get_db)
):
    token = websocket.cookies.get(manager.cookie_name)

    user = await manager.get_current_user(token) if token else None

    if not user or not user.is_active:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    is_target_valid = False
    try:
        uid = uuid.UUID(recipient_id)
        if recipient_role == 'member':
            target = db.query(User).filter(User.user_id == uid).first()
        elif recipient_role == 'trainer':
            target = db.query(Trainer).filter(
                Trainer.trainer_id == uid).first()

        if target and target.is_active:
            is_target_valid = True
    except ValueError:
        pass

    if not is_target_valid:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    await ws_manager.connect(websocket, recipient_id, recipient_role)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(recipient_id)


@router.post("/sendNotification", status_code=status.HTTP_201_CREATED)
async def send_notification(data: NotificationCreate, db: Session = Depends(get_db), current_user: Admin = Depends(manager)):
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=403, detail="Forbidden: Account is inactive")

    recipient_id_val = None

    if data.recipient_role in ['all', 'allMembers', 'allTrainers']:
        recipient_id_val = None
    else:
        if data.recipient_role not in ['member', 'trainer']:
            raise HTTPException(
                status_code=400, detail="Specific recipient role must be member or trainer")

        if not data.recipient_id:
            raise HTTPException(
                status_code=400, detail="Recipient ID is required for specific role")

        try:
            recipient_id_val = uuid.UUID(data.recipient_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid UUID format")

        # Verify existence and active status
        exists = False
        if data.recipient_role == 'member':
            exists = db.query(User).filter(
                User.user_id == recipient_id_val, User.is_active == True).first()
        elif data.recipient_role == 'trainer':
            exists = db.query(Trainer).filter(
                Trainer.trainer_id == recipient_id_val, Trainer.is_active == True).first()

        if not exists:
            raise HTTPException(
                status_code=404, detail="Recipient not found or inactive")

    # Create DB Entry
    new_notification = Notifications(
        message=data.message,
        recipient_id=recipient_id_val,
        recipient_role=data.recipient_role
    )
    db.add(new_notification)
    db.commit()
    db.refresh(new_notification)

    # Real-time Send
    ws_payload = {
        "id": new_notification.id,
        "message": new_notification.message,
        "created_at": str(new_notification.created_at),
        "is_read": new_notification.is_read
    }

    if recipient_id_val:
        await ws_manager.send_personal_message(ws_payload, str(recipient_id_val))
    else:
        await ws_manager.broadcast(ws_payload, data.recipient_role)

    return {"message": "Notification sent successfully"}


@router.get("/notifications", status_code=status.HTTP_200_OK)
def get_notifications(
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db),
    current_user=Depends(manager)
):
    if not current_user or not current_user.is_active:
        raise HTTPException(status_code=403, detail="Active account required")

    limit = 10
    skip = (page - 1) * limit

    query = db.query(Notifications)

    if current_user.role != 'admin':
        user_role = current_user.role
        user_id = current_user.user_id if user_role == 'member' else current_user.trainer_id

        filters = [
            and_(
                Notifications.recipient_id == user_id,
                Notifications.recipient_role == user_role
            ),
            Notifications.recipient_role == 'all'
        ]

        if user_role == 'member':
            filters.append(Notifications.recipient_role == 'allMembers')
        elif user_role == 'trainer':
            filters.append(Notifications.recipient_role == 'allTrainers')

        query = query.filter(or_(*filters))

    notifications = query.order_by(desc(Notifications.created_at)).offset(
        skip).limit(limit + 1).all()

    hasMore = len(notifications) > limit

    if hasMore:
        notifications = notifications[:-1]

    return {
        "notifications": notifications,
        "page": page,
        "hasMore": hasMore
    }


@router.patch("/notifications/read", status_code=status.HTTP_200_OK)
def mark_notifications_as_read(
    request: NotificationReadRequest,
    db: Session = Depends(get_db),
    current_user=Depends(manager)
):
    if not current_user or not current_user.is_active:
        raise HTTPException(status_code=403, detail="Active account required")

    affected_rows = db.query(Notifications).filter(Notifications.id.in_(
        request.notification_ids)).update({Notifications.is_read: True}, synchronize_session=False)

    db.commit()

    return {"message": f"Successfully marked {affected_rows} notifications as read"}
