from fastapi import APIRouter, Depends, HTTPException, status, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, desc
from datetime import datetime, time
import uuid
from app.db.database import get_db
from app.db.models import Notifications, User, Trainer, Admin, NotificationStatus
from app.routers.auth import manager
from app.schemas.notification_schema import NotificationCreate, NotificationRequest, NotificationSoftDelete

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

    if user.role in {"member", "trainer"} and getattr(user, "email_verified", True) is False:
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
        "is_read": False,
        "recipient_id": str(new_notification.recipient_id),
        "recipient_role": new_notification.recipient_role
    }

    if recipient_id_val:
        await ws_manager.send_personal_message(ws_payload, str(recipient_id_val))
    else:
        await ws_manager.broadcast(ws_payload, data.recipient_role)

    return {"message": "Notification sent successfully"}


@router.get("/notifications", status_code=status.HTTP_200_OK)
def get_notifications(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
    recipient_role: str | None = Query(None),
    recipient_id: str | None = Query(None),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user=Depends(manager)
):
    if not current_user or not current_user.is_active:
        raise HTTPException(status_code=403, detail="Active account required")

    if current_user.role in {"member", "trainer"} and getattr(current_user, "email_verified", True) is False:
        raise HTTPException(status_code=403, detail="Email not verified. Please verify your email to continue.")

    if current_user.role in {"member", "trainer"} and getattr(current_user, "email_verified", True) is False:
        raise HTTPException(status_code=403, detail="Email not verified. Please verify your email to continue.")

    skip = (page - 1) * limit

    def parse_date(value: str, is_end: bool) -> datetime:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid date format. Use ISO 8601 (YYYY-MM-DD or YYYY-MM-DDTHH:MM)."
            )
        if "T" not in value and ":" not in value:
            parsed = datetime.combine(
                parsed.date(), time.max if is_end else time.min)
        return parsed

    start_dt = parse_date(start_date, False) if start_date else None
    end_dt = parse_date(end_date, True) if end_date else None
    if start_dt and end_dt and start_dt > end_dt:
        raise HTTPException(
            status_code=400, detail="start_date cannot be after end_date")

    query = db.query(Notifications)

    
    if current_user.role != 'admin':
        user_role = current_user.role
        user_id = current_user.user_id if user_role == 'member' else current_user.trainer_id

        query = db.query(Notifications, NotificationStatus).outerjoin(
            NotificationStatus,
            and_(
                NotificationStatus.notification_id == Notifications.id,
                NotificationStatus.recipient_id == user_id,
                NotificationStatus.recipient_role == user_role
            ))

        filters = [
            and_(
                Notifications.recipient_id == user_id,
                Notifications.recipient_role == user_role
            ),
            Notifications.recipient_role == 'all',
        ]

        if user_role == 'member':
            filters.append(Notifications.recipient_role == 'allMembers')
        elif user_role == 'trainer':
            filters.append(Notifications.recipient_role == 'allTrainers')

        query = query.filter(or_(*filters))
        query = query.filter(or_(
            NotificationStatus.id == None, 
            NotificationStatus.is_deleted == False))
    else:
        if recipient_role:
            query = query.filter(Notifications.recipient_role == recipient_role)
        if recipient_id:
            if recipient_role not in ['member', 'trainer']:
                raise HTTPException(
                    status_code=400, detail="recipient_id requires recipient_role of member or trainer")
            try:
                recipient_uuid = uuid.UUID(recipient_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid UUID format")
            query = query.filter(Notifications.recipient_id == recipient_uuid)

    if start_dt:
        query = query.filter(Notifications.created_at >= start_dt)
    if end_dt:
        query = query.filter(Notifications.created_at <= end_dt)

    notifications = query.order_by(desc(Notifications.created_at)).offset(
        skip).limit(limit + 1).all()
    
    hasMore = len(notifications) > limit
    if hasMore:
        notifications = notifications[:-1]
    
    def get_notification_row(row):
        if current_user.role == 'admin':
            return row, None
        return row

    normalized_rows = [get_notification_row(row) for row in notifications]

    member_ids = {
        n.recipient_id for n, _ in normalized_rows if n.recipient_role == 'member' and n.recipient_id}
    trainer_ids = {
        n.recipient_id for n, _ in normalized_rows if n.recipient_role == 'trainer' and n.recipient_id}

    user_map = {}
    if member_ids:
        users = db.query(User.user_id, User.name).filter(User.user_id.in_(member_ids)).all()
        user_map = {u.user_id: u.name for u in users}
    

    trainer_map = {}
    if trainer_ids:
        trainers = db.query(Trainer.trainer_id, Trainer.name).filter(Trainer.trainer_id.in_(trainer_ids)).all()
        trainer_map = {t.trainer_id: t.name for t in trainers}
    
    final_notifications = []
    for n, status_row in normalized_rows:
        if n.recipient_role == 'all':
            display_name = "Everyone"
        elif n.recipient_role == 'allMembers':
            display_name = "All Members"
        elif n.recipient_role == 'allTrainers':
            display_name = "All Trainers"
        elif n.recipient_role == 'member':
            display_name = user_map.get(n.recipient_id, f"Member Deleted ")
        elif n.recipient_role == 'trainer':
            display_name = trainer_map.get(n.recipient_id, f"Trainer Deleted")
        else:
            display_name = "Unknown"
        
        notif_data = {
        "id": n.id,
        "message": n.message,
        "recipient_id": n.recipient_id,
        "recipient_role": str(n.recipient_role),
        "recipient_name": display_name,
        "created_at": n.created_at,
        "is_read": bool(status_row.is_read) if status_row else False
        }

        final_notifications.append(notif_data)

    return {
        "notifications": final_notifications,
        "page": page,
        "hasMore": hasMore
    }


@router.patch("/notifications/read", status_code=status.HTTP_200_OK)
def mark_notifications_as_read(
    request: NotificationRequest,
    db: Session = Depends(get_db),
    current_user=Depends(manager)
):
    if not current_user or not current_user.is_active:
        raise HTTPException(status_code=403, detail="Active account required")

    if current_user.role not in ['member', 'trainer']:
        raise HTTPException(status_code=403, detail="Member or trainer access required")

    user_role = current_user.role
    user_id = current_user.user_id if user_role == 'member' else current_user.trainer_id

    query = db.query(NotificationStatus).filter(
        NotificationStatus.notification_id.in_(request.notification_ids),
        NotificationStatus.recipient_id == user_id,
        NotificationStatus.recipient_role == user_role
    )

    existing_records = query.all()
    existing_ids = {r.notification_id for r in existing_records}

    affected_rows = query.update(
        {NotificationStatus.is_read: True}, synchronize_session=False)

    for n_id in request.notification_ids:
        if n_id not in existing_ids:
            new_status = NotificationStatus(
                notification_id=n_id,
                recipient_id=user_id,
                recipient_role=user_role,
                is_read=True,
                is_deleted=False
            )
            db.add(new_status)

    db.commit()

    return {"message": f"Successfully marked {affected_rows + (len(request.notification_ids) - len(existing_ids))} notifications as read"}


@router.delete("/admin/notifications/delete", status_code=status.HTTP_200_OK)
def delete_admin_notifications(request: NotificationRequest, db: Session = Depends(get_db), current_user: Admin=Depends(manager)):
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    if not current_user.is_active:
        raise HTTPException(
            status_code=403, detail="Forbidden: Account is inactive")

    try:
        affected_rows = db.query(Notifications).filter(Notifications.id.in_(
            request.notification_ids)).delete(synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()

    return {"message": f"Successfully deleted {affected_rows} notifications"}


@router.post('/notifications/delete', status_code=status.HTTP_200_OK)
def delete_user_notification(request: NotificationSoftDelete, db: Session = Depends(get_db), current_user=Depends(manager)):
    if not current_user or not current_user.is_active:
        raise HTTPException(status_code=403, detail="Active account required")

    if current_user.role in {"member", "trainer"} and getattr(current_user, "email_verified", True) is False:
        raise HTTPException(status_code=403, detail="Email not verified. Please verify your email to continue.")

    if current_user.role not in ['member', 'trainer']:
        raise HTTPException(status_code=403, detail="Member or trainer access required")

    user_role = current_user.role
    user_id = current_user.user_id if user_role == 'member' else current_user.trainer_id
    
    query = db.query(NotificationStatus).filter(
        NotificationStatus.notification_id.in_(request.notification_ids),
        NotificationStatus.recipient_id == user_id,
        NotificationStatus.recipient_role == user_role
    )

    existing_records = query.all()
    existing_ids = {r.notification_id for r in existing_records}
    
    query.update({NotificationStatus.is_deleted: True}, synchronize_session=False)

    
    for n_id in request.notification_ids:
        if n_id not in existing_ids:
            new_status = NotificationStatus(
                notification_id=n_id,
                recipient_id=user_id,
                recipient_role=user_role,
                is_deleted=True,
                is_read=False
            )
            db.add(new_status)
    
    try:
        db.commit()
        return {"message": "Successfully deleted notifications"}
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Database error")
