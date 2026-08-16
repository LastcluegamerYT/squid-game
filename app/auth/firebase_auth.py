import time
import os
import requests
import jwt
from typing import Optional, Dict, Any
from fastapi import Header, HTTPException, status, Depends
from cryptography.x509 import load_pem_x509_certificate
from cryptography.hazmat.backends import default_backend

from app.config import settings
from app.models.user import UserProfile
from app.database.db import db

# Cache for Google's public x509 certs
GOOGLE_CERTS_URL = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
_cached_certs: Dict[str, str] = {}
_certs_expiry: float = 0

_IS_PRODUCTION = os.getenv("ENV", "development").lower() == "production"
_ALLOW_DEV_TOKENS = os.getenv("ALLOW_DEV_TOKENS", "false" if _IS_PRODUCTION else "true").lower() in {"1", "true", "yes"}
_ALLOW_INSECURE_TOKEN_FALLBACK = os.getenv(
    "ALLOW_INSECURE_FIREBASE_TOKENS", "false" if _IS_PRODUCTION else "true"
).lower() in {"1", "true", "yes"}

def get_google_public_certs() -> Dict[str, str]:
    global _cached_certs, _certs_expiry
    now = time.time()
    if _cached_certs and now < _certs_expiry:
        return _cached_certs
    
    try:
        resp = requests.get(GOOGLE_CERTS_URL, timeout=5)
        if resp.status_code == 200:
            _cached_certs = resp.json()
            # Parse max-age from Cache-Control header or default to 6 hours
            cache_control = resp.headers.get("Cache-Control", "")
            max_age = 21600
            for part in cache_control.split(","):
                if "max-age=" in part:
                    try:
                        max_age = int(part.split("max-age=")[1].strip())
                    except Exception:
                        pass
            _certs_expiry = now + max_age
            return _cached_certs
    except Exception as e:
        print(f"Warning: Could not fetch Google public certs: {e}")
        if _cached_certs:
            return _cached_certs
    return {}

def verify_firebase_token(token: str) -> Dict[str, Any]:
    """
    Verifies a Firebase ID token.
    1. Checks for test/mock tokens in dev mode
    2. Validates JWT signature with Google's public key
    3. Validates aud (FIREBASE_PROJECT_ID) and iss (https://securetoken.google.com/{FIREBASE_PROJECT_ID})
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    # Support development & mock tokens for local testing only. They must never
    # silently become production credentials.
    if token.startswith("mock-"):
        if not _ALLOW_DEV_TOKENS:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Development tokens are disabled")
        uid = token[len("mock-"):]
        return {
            "uid": uid,
            "user_id": uid,
            "email": f"{uid}@example.com",
            "name": f"User {uid.replace('-', ' ').title()}",
            "picture": f"https://api.dicebear.com/7.x/bottts/svg?seed={uid}"
        }
    if token.startswith("dev-"):
        if not _ALLOW_DEV_TOKENS:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Development tokens are disabled")
        uid = token[len("dev-"):]
        return {
            "uid": uid,
            "user_id": uid,
            "email": f"{uid}@example.com",
            "name": f"User {uid.replace('-', ' ').title()}",
            "picture": f"https://api.dicebear.com/7.x/bottts/svg?seed={uid}"
        }


    try:
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        certs = get_google_public_certs()
        
        if kid and kid in certs:
            pem_cert = certs[kid].encode("utf-8")
            cert = load_pem_x509_certificate(pem_cert, default_backend())
            public_key = cert.public_key()
            
            payload = jwt.decode(
                token,
                public_key,
                algorithms=["RS256"],
                audience=settings.FIREBASE_PROJECT_ID,
                issuer=f"https://securetoken.google.com/{settings.FIREBASE_PROJECT_ID}"
            )
            return payload
        raise ValueError("Firebase signing certificate was not available for this token")

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Firebase ID token has expired. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as exc:
        # Offline, unsigned decoding is deliberately an explicit development
        # opt-in. Production deployments fail closed if Google cert retrieval or
        # signature verification cannot succeed.
        if _ALLOW_INSECURE_TOKEN_FALLBACK:
            try:
                payload = jwt.decode(token, options={"verify_signature": False})
                audience = payload.get("aud") or payload.get("project_id")
                if audience and audience != settings.FIREBASE_PROJECT_ID:
                    raise ValueError("Token audience does not match Firebase project")
                return payload
            except Exception:
                pass
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Firebase ID token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

def extract_token_from_header(authorization: Optional[str] = Header(None)) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None

async def get_current_user(authorization: Optional[str] = Header(None)) -> UserProfile:
    token = extract_token_from_header(authorization)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing or invalid. Format: 'Bearer <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    payload = verify_firebase_token(token)
    uid = payload.get("uid") or payload.get("user_id") or payload.get("sub")
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload: missing user ID",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    email = payload.get("email")
    name = payload.get("name") or (email.split("@")[0] if email else "Pulse Pioneer")
    # Do not replace a previously saved Google photo with a generated avatar
    # when an otherwise-valid Firebase token omits the optional `picture` claim.
    # New accounts still receive a local default in db.get_or_create_user().
    picture = payload.get("picture")
    
    # Get or create user in database
    user = db.get_or_create_user(
        uid=uid,
        email=email,
        display_name=name,
        photo_url=picture
    )
    return user

async def get_optional_user(authorization: Optional[str] = Header(None)) -> Optional[UserProfile]:
    token = extract_token_from_header(authorization)
    if not token:
        return None
    try:
        payload = verify_firebase_token(token)
        uid = payload.get("uid") or payload.get("user_id") or payload.get("sub")
        if uid:
            email = payload.get("email")
            name = payload.get("name") or (email.split("@")[0] if email else "Pulse Pioneer")
            picture = payload.get("picture")
            return db.get_or_create_user(uid=uid, email=email, display_name=name, photo_url=picture)
    except Exception:
        return None
    return None
