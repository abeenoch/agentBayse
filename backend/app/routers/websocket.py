from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.dependencies import get_current_websocket_user
from app.websocket_manager import manager

router = APIRouter()


@router.websocket("/ws/live")
async def websocket_endpoint(
    websocket: WebSocket,
    current_user: str = Depends(get_current_websocket_user),
):
    await manager.connect(websocket)
    try:
        while True:
            _ = await websocket.receive_text()
            await websocket.send_json({"type": "heartbeat"})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
