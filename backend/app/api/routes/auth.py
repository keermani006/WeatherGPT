"""
app/api/routes/auth.py

Endpoints for authentication:
  - POST /api/v1/auth/register
  - POST /api/v1/auth/login
  - POST /api/v1/auth/logout
  - POST /api/v1/auth/demo-token
  - GET  /api/v1/auth/me
"""

import hashlib
import logging
import secrets
import uuid
from typing import Optional, Dict
from pydantic import BaseModel, Field, field_validator
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.auth import AuthUser, create_access_token, get_current_user
from app.core.database import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])

# In-memory user fallback store (email -> {id, email, password_hash, salt, name})
_IN_MEMORY_USERS: Dict[str, dict] = {}


def _hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    if not salt:
        salt = secrets.token_hex(16)
    hashed = hashlib.sha256(f"{salt}{password}".encode("utf-8")).hexdigest()
    return hashed, salt


class RegisterRequest(BaseModel):
    email: str = Field(description="User email address")
    password: str = Field(min_length=6, description="User password (min 6 chars)")
    name: Optional[str] = Field(default=None, description="Optional display name")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Invalid email format")
        return v


class LoginRequest(BaseModel):
    email: str = Field(description="User email address")
    password: str = Field(description="User password")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Invalid email format")
        return v


class AuthUserData(BaseModel):
    id: str
    email: str
    name: Optional[str] = None
    role: str = "authenticated"


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUserData


class DemoTokenRequest(BaseModel):
    user_id: Optional[str] = Field(default=None, description="Optional custom user UUID")
    email: Optional[str] = Field(default="demo@weathergpt.local", description="User email label")


class DemoTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
    description="Creates a new user account via Supabase (or local fallback) and returns an access token.",
)
async def register_user(req: RegisterRequest):
    email = req.email.strip().lower()

    # In-memory store duplicate check
    if email in _IN_MEMORY_USERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "USER_ALREADY_EXISTS", "message": "A user with this email already exists."}},
        )

    sb = get_supabase()
    pw_hash, salt = _hash_password(req.password)

    if sb:
        try:
            # Try admin user creation first to auto-confirm email for immediate login
            res = sb.auth.admin.create_user({
                "email": email,
                "password": req.password,
                "email_confirm": True,
                "user_metadata": {"name": req.name or ""}
            })
            user_id = str(res.user.id)
            user_email = res.user.email or email
            token = create_access_token(user_id=user_id, email=user_email)
            _IN_MEMORY_USERS[email] = {
                "id": user_id,
                "email": user_email,
                "password_hash": pw_hash,
                "salt": salt,
                "name": req.name,
            }
            return AuthResponse(
                access_token=token,
                token_type="bearer",
                user=AuthUserData(id=user_id, email=user_email, name=req.name, role="authenticated"),
            )
        except Exception as exc:
            err_msg = str(exc).lower()
            if "already" in err_msg or "registered" in err_msg or "exists" in err_msg:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"error": {"code": "USER_ALREADY_EXISTS", "message": "A user with this email already exists."}},
                )
            logger.warning("Supabase admin create_user failed, falling back to sign_up or local: %s", exc)
            try:
                res = sb.auth.sign_up({"email": email, "password": req.password})
                if res.user:
                    user_id = str(res.user.id)
                    token = create_access_token(user_id=user_id, email=email)
                    _IN_MEMORY_USERS[email] = {
                        "id": user_id,
                        "email": email,
                        "password_hash": pw_hash,
                        "salt": salt,
                        "name": req.name,
                    }
                    return AuthResponse(
                        access_token=token,
                        token_type="bearer",
                        user=AuthUserData(id=user_id, email=email, name=req.name, role="authenticated"),
                    )
            except Exception as e2:
                logger.warning("Supabase sign_up also failed: %s", e2)
                err2_msg = str(e2).lower()
                if "already" in err2_msg or "registered" in err2_msg or "exists" in err2_msg:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail={"error": {"code": "USER_ALREADY_EXISTS", "message": "A user with this email already exists."}},
                    )

    user_id = str(uuid.uuid4())
    _IN_MEMORY_USERS[email] = {
        "id": user_id,
        "email": email,
        "password_hash": pw_hash,
        "salt": salt,
        "name": req.name,
    }
    token = create_access_token(user_id=user_id, email=email)
    return AuthResponse(
        access_token=token,
        token_type="bearer",
        user=AuthUserData(id=user_id, email=email, name=req.name, role="authenticated"),
    )



@router.post(
    "/login",
    response_model=AuthResponse,
    summary="Sign in user",
    description="Authenticates credentials and returns a Bearer JWT access token.",
)
async def login_user(req: LoginRequest):
    email = req.email.strip().lower()
    sb = get_supabase()

    if sb:
        try:
            res = sb.auth.sign_in_with_password({"email": email, "password": req.password})
            if res.user:
                user_id = str(res.user.id)
                user_email = res.user.email or email
                token = create_access_token(user_id=user_id, email=user_email)
                user_name = res.user.user_metadata.get("name") if res.user.user_metadata else None
                return AuthResponse(
                    access_token=token,
                    token_type="bearer",
                    user=AuthUserData(id=user_id, email=user_email, name=user_name, role="authenticated"),
                )
        except Exception as exc:
            logger.info("Supabase password sign in did not match or failed: %s", exc)

    # Check fallback in-memory user registry
    user_record = _IN_MEMORY_USERS.get(email)
    if user_record:
        check_hash, _ = _hash_password(req.password, user_record["salt"])
        if check_hash == user_record["password_hash"]:
            token = create_access_token(user_id=user_record["id"], email=email)
            return AuthResponse(
                access_token=token,
                token_type="bearer",
                user=AuthUserData(
                    id=user_record["id"],
                    email=email,
                    name=user_record["name"],
                    role="authenticated",
                ),
            )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": {"code": "INVALID_CREDENTIALS", "message": "Invalid email or password."}},
    )


@router.post(
    "/logout",
    summary="Sign out user",
    description="Logs out the current session.",
)
async def logout_user():
    return {"message": "Logged out successfully"}


@router.post(
    "/demo-token",
    response_model=DemoTokenResponse,
    summary="Get demo JWT session token",
    description="Returns a verified HS256 JWT access token for testing user-scoped alerts without manual auth configuration.",
)
async def get_demo_token(body: Optional[DemoTokenRequest] = None):
    req = body or DemoTokenRequest()
    uid = req.user_id or str(uuid.uuid4())
    try:
        token = create_access_token(user_id=uid, email=req.email)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "AUTH_NOT_CONFIGURED", "message": str(exc)}},
        )
    return DemoTokenResponse(
        access_token=token,
        token_type="bearer",
        user_id=uid,
        email=req.email,
    )


@router.get(
    "/me",
    summary="Get current authenticated user",
    description="Verify current Bearer token and return identity info.",
)
async def get_me(user: AuthUser = Depends(get_current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
    }

