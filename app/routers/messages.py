"""
Private Messaging Router
Endpoints for DM conversations, real-time message delivery, and read receipts.
"""
import json as _json
import asyncio
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status, Query, WebSocket, WebSocketDisconnect
from app.auth.firebase_auth import get_current_user, get_optional_user, verify_firebase_token
from app.models.user import UserProfile
from app.models.message import MessageCreate, MessageEdit, ConversationMeta, MessageResponse
from app.database.db import db

router = APIRouter(prefix="/messages", tags=["Messaging"])


# ─── Helper ──────────────────────────────────────────────────────────────────

def _check_conv_access(conv_id: str, uid: str):
    conv = db.conversations.get(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if uid not in conv.get("participants", []):
        raise HTTPException(status_code=403, detail="Access denied to this conversation")
    return conv


def _websocket_token(websocket: WebSocket) -> Optional[str]:
    """Read the token from the dedicated WebSocket subprotocol pair.

    Browsers cannot attach an Authorization header to WebSocket handshakes.
    The client offers ["ideon-auth", firebaseToken] and the server selects the
    stable protocol name after validating the second value.
    """
    protocols = [value.strip() for value in websocket.headers.get("sec-websocket-protocol", "").split(",")]
    try:
        marker_index = protocols.index("ideon-auth")
        return protocols[marker_index + 1] if marker_index + 1 < len(protocols) else None
    except ValueError:
        return None


# ─── Conversations ────────────────────────────────────────────────────────────

@router.get("/conversations", response_model=List[ConversationMeta])
async def list_conversations(
    current_user: UserProfile = Depends(get_current_user)
):
    """
    Get the authenticated user's inbox — all conversations sorted newest-message-first.
    Each item includes last message preview, unread count, and friend status.
    """
    convs = db.get_user_conversations(current_user.uid)
    return [ConversationMeta(**c) for c in convs]


@router.post("/conversations", response_model=ConversationMeta)
async def start_or_get_conversation(
    target_uid: str = Query(..., description="Firebase UID of the person to message"),
    current_user: UserProfile = Depends(get_current_user)
):
    """
    Open (or retrieve an existing) 1-to-1 conversation with target_uid.
    No restriction on friend status — anyone can message anyone.
    """
    if target_uid == current_user.uid:
        raise HTTPException(status_code=400, detail="Cannot message yourself")

    target = db.get_user(target_uid)
    if not target:
        # Try username lookup
        target = db.get_user_by_username(target_uid.lstrip("@"))
    if not target:
        raise HTTPException(status_code=404, detail="Target user not found")

    conv = db.get_or_create_conversation(current_user.uid, target.uid)
    other = db.users.get(target.uid, {})

    return ConversationMeta(
        conv_id=conv["conv_id"],
        other_uid=target.uid,
        other_display_name=other.get("display_name", "Pulse Member"),
        other_avatar=other.get("avatar_url") or other.get("photo_url"),
        other_username=other.get("username"),
        last_message_text=conv.get("last_message_text"),
        last_message_at=conv.get("last_message_at"),
        unread_count=conv.get("unread", {}).get(current_user.uid, 0),
        is_friend=db.is_friend(current_user.uid, target.uid),
    )


# ─── Messages in a conversation ───────────────────────────────────────────────

@router.get("/{conv_id}", response_model=List[MessageResponse])
async def get_messages(
    conv_id: str,
    limit: int = Query(50, ge=1, le=200),
    before_id: Optional[str] = Query(None, description="Paginate: messages older than this ID"),
    current_user: UserProfile = Depends(get_current_user)
):
    """
    Load paginated message history for a conversation (oldest-first within the page).
    Automatically marks conversation as read.
    """
    _check_conv_access(conv_id, current_user.uid)

    msgs = db.get_messages(conv_id, limit=limit, before_id=before_id)
    # Mark as read
    db.mark_conv_read(conv_id, current_user.uid)

    return [MessageResponse(**m) for m in msgs]


@router.post("/{conv_id}", response_model=MessageResponse, status_code=201)
async def send_message(
    conv_id: str,
    body: MessageCreate,
    current_user: UserProfile = Depends(get_current_user)
):
    """
    Send a text message (with optional image URL) in a conversation.
    Delivers in real-time to any open WebSocket connections of the recipient.
    """
    _check_conv_access(conv_id, current_user.uid)

    msg = db.send_message(
        conv_id=conv_id,
        sender_uid=current_user.uid,
        text=body.text,
        image_url=body.image_url,
    )
    if not msg:
        raise HTTPException(status_code=400, detail="Could not send message")

    # Real-time push to recipient's open WebSocket connections
    conv = db.conversations.get(conv_id, {})
    ws_payload = _json.dumps({
        "type": "new_message",
        "conv_id": conv_id,
        "message": msg,
    }, ensure_ascii=False)
    for uid in conv.get("participants", []):
        if uid != current_user.uid:
            dead_sockets = []
            for ws in list(db.ws_user_connections.get(uid, [])):
                try:
                    await ws.send_text(ws_payload)
                except Exception:
                    dead_sockets.append(ws)
            for ws in dead_sockets:
                try:
                    db.ws_user_connections.get(uid, []).remove(ws)
                except ValueError:
                    pass

    return MessageResponse(**msg)


@router.patch("/{conv_id}/{msg_id}", response_model=MessageResponse)
async def edit_message(
    conv_id: str,
    msg_id: str,
    body: MessageEdit,
    current_user: UserProfile = Depends(get_current_user)
):
    """Edit your own message (marks it as edited with timestamp)."""
    _check_conv_access(conv_id, current_user.uid)
    updated = db.edit_message(conv_id, msg_id, current_user.uid, body.text)
    if not updated:
        raise HTTPException(status_code=404, detail="Message not found or not yours")
    for uid in db.conversations.get(conv_id, {}).get("participants", []):
        if uid != current_user.uid:
            await db.broadcast_to_user(uid, {
                "type": "message_updated",
                "conv_id": conv_id,
                "message": updated,
            })
    return MessageResponse(**updated)


@router.delete("/{conv_id}/{msg_id}")
async def delete_message(
    conv_id: str,
    msg_id: str,
    current_user: UserProfile = Depends(get_current_user)
):
    """Soft-delete your own message. Recipients see '[Message deleted]'."""
    _check_conv_access(conv_id, current_user.uid)
    ok = db.delete_message(conv_id, msg_id, current_user.uid)
    if not ok:
        raise HTTPException(status_code=404, detail="Message not found or not yours")
    for uid in db.conversations.get(conv_id, {}).get("participants", []):
        if uid != current_user.uid:
            await db.broadcast_to_user(uid, {
                "type": "message_deleted",
                "conv_id": conv_id,
                "message_id": msg_id,
            })
    return {"success": True}


@router.post("/{conv_id}/read")
async def mark_read(
    conv_id: str,
    current_user: UserProfile = Depends(get_current_user)
):
    """Mark all messages in a conversation as read. Clears the unread badge."""
    _check_conv_access(conv_id, current_user.uid)
    db.mark_conv_read(conv_id, current_user.uid)
    return {"success": True}


# ─── WebSocket: per-user DM channel ──────────────────────────────────────────

@router.websocket("/ws/{uid}")
async def chat_websocket(websocket: WebSocket, uid: str):
    """
    Per-user WebSocket endpoint for real-time DM delivery.
    Frontend connects here after login. The server pushes new_message events
    to this socket whenever someone sends this user a DM.

    Messages sent FROM the client on this socket are ignored (use POST /{conv_id}).
    """
    token = _websocket_token(websocket)
    if not token:
        await websocket.close(code=1008, reason="Authentication required")
        return
    try:
        payload = await asyncio.to_thread(verify_firebase_token, token)
        authenticated_uid = payload.get("uid") or payload.get("user_id") or payload.get("sub")
        if authenticated_uid != uid:
            raise ValueError("Socket user does not match token")
    except Exception:
        await websocket.close(code=1008, reason="Invalid authentication")
        return

    await websocket.accept(subprotocol="ideon-auth")
    # Register
    if uid not in db.ws_user_connections:
        db.ws_user_connections[uid] = []
    db.ws_user_connections[uid].append(websocket)
    try:
        while True:
            await websocket.receive_text()  # keep alive, ignore incoming
    except WebSocketDisconnect:
        pass
    finally:
        try:
            db.ws_user_connections[uid].remove(websocket)
        except (ValueError, KeyError):
            pass
