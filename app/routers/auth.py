from fastapi import APIRouter, Depends
from app.models.user import UserProfile
from app.auth.firebase_auth import get_current_user
from app.services.event_service import event_service
from app.models.event import EventType

router = APIRouter(prefix="/auth", tags=["Authentication"])

@router.get("/me", response_model=UserProfile)
async def get_my_profile(current_user: UserProfile = Depends(get_current_user)):
    """Returns the authenticated user profile and onboarding status"""
    return current_user

@router.post("/verify", response_model=UserProfile)
async def verify_auth_token(current_user: UserProfile = Depends(get_current_user)):
    """Validates the Firebase client token after Google Sign-In and returns user state"""
    event_service.record_event(
        user_id=current_user.uid,
        event_type=EventType.CLICK,
        metadata={"action": "sign_in_verification"}
    )
    return current_user
