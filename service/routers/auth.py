import time
import logging
from datetime import datetime, timedelta
from uuid import UUID, uuid4
from email.message import EmailMessage
import smtplib
import ssl
from typing import Optional # Import Optional

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from passlib.context import CryptContext
from jose import JWTError, jwt

from service.config import settings
from service.db.models import User, UserPersonalData, Token, UserRegisterRequest, UserLoginRequest, PasswordResetRequest, PasswordResetConfirm # Import new models

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Authentication"]) # Define APIRouter

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT secret key and algorithm from settings
SECRET_KEY = settings.jwt_secret_key
ALGORITHM = settings.jwt_algorithm
ACCESS_TOKEN_EXPIRE_MINUTES = settings.access_token_expire_minutes
REFRESH_TOKEN_EXPIRE_DAYS = settings.refresh_token_expire_days

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    # For refresh tokens, we'll use a random UUID string directly, not a JWT
    # This simplifies revocation and avoids JWT-specific refresh token issues.
    return str(uuid4())

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

async def send_verification_email(email: str, verification_token: UUID, background_tasks: BackgroundTasks):
    subject = "Verify your email for Cijene API"
    verification_link = f"{settings.email_verification_base_url}/{verification_token}"
    body = f"""
    Hi there,

    Thank you for registering with Cijene API.
    Please verify your email address by clicking on the link below:

    {verification_link}

    If you did not register for this service, please ignore this email.

    Best regards,
    The Cijene API Team
    """
    
    msg = EmailMessage()
    msg.set_content(body)
    msg["Subject"] = subject
    msg["From"] = settings.sender_email
    msg["To"] = email

    # Use BackgroundTasks to send email asynchronously
    background_tasks.add_task(_send_email_sync, msg)

def _send_email_sync(msg: EmailMessage):
    try:
        # MailHog on port 1025 does not use SSL, so use SMTP instead of SMTP_SSL
        with smtplib.SMTP(settings.smtp_server, settings.smtp_port) as server:
            # MailHog does not require authentication by default, but we keep the login call
            # in case it's configured for it or for compatibility with other SMTP servers.
            if settings.smtp_username and settings.smtp_password:
                server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(msg)
        logger.info(f"Verification email sent to {msg['To']}")
    except Exception as e:
        logger.error(f"Failed to send verification email to {msg['To']}: {e}")

async def send_password_reset_email(email: str, reset_token: str, background_tasks: BackgroundTasks):
    subject = "Password Reset for Cijene API"
    reset_link = f"{settings.email_verification_base_url.replace('verify-email', 'reset-password')}/{reset_token}" # Assuming a reset-password endpoint
    body = f"""
    Hi,

    You have requested a password reset for your Cijene API account.
    Please click on the link below to reset your password:

    {reset_link}

    This link is valid for a limited time. If you did not request a password reset, please ignore this email.

    Best regards,
    The Cijene API Team
    """
    
    msg = EmailMessage()
    msg.set_content(body)
    msg["Subject"] = subject
    msg["From"] = settings.sender_email
    msg["To"] = email

    background_tasks.add_task(_send_email_sync, msg)


# Security scheme for OpenAPI documentation
security_scheme = HTTPBearer(scheme_name="HTTPBearer")

db = settings.get_db()


@router.post("/register", response_model=dict, status_code=status.HTTP_201_CREATED)
async def register_user(
    request: UserRegisterRequest,
    background_tasks: BackgroundTasks,
):
    """
    Register a new user.
    """
    existing_user, _ = await db.users.get_user_by_email(request.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    hashed_password = get_password_hash(request.password)
    verification_token = uuid4()

    user, personal_data = await db.users.add_user_with_password(
        name=request.name,
        email=request.email,
        hashed_password=hashed_password,
        verification_token=verification_token,
    )

    if not user or not personal_data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user",
        )
    
    # Send verification email in the background
    await send_verification_email(personal_data.email, verification_token, background_tasks)

    return {"message": "User registered successfully. Please check your email for verification."}

@router.post("/token", response_model=Token)
async def login_for_access_token(
    request: UserLoginRequest,
):
    """
    Authenticate user and return JWT access and refresh tokens.
    """
    user, personal_data = await db.users.get_user_by_email(request.email)
    if not user or not personal_data or not verify_password(request.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email not verified. Please check your email for a verification link.",
        )

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(user.id)}, expires_delta=access_token_expires
    )

    refresh_token_expires_at = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    refresh_token = create_refresh_token(
        data={"sub": str(user.id)}, expires_delta=timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS) # Pass delta for consistency, though not used by new create_refresh_token
    )

    # Store refresh token in DB
    await db.users.add_refresh_token(user.id, refresh_token, refresh_token_expires_at)

    return {"access_token": access_token, "token_type": "bearer", "refresh_token": refresh_token}

@router.post("/refresh", response_model=Token)
async def refresh_access_token(
    refresh_token: str = Depends(HTTPBearer(auto_error=False)), # Use HTTPBearer for refresh token
):
    """
    Refresh access token using a valid refresh token.
    """
    if not refresh_token or not refresh_token.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token missing",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Retrieve refresh token from DB
    db_refresh_token = await db.users.get_refresh_token(refresh_token.credentials)
    
    if not db_refresh_token or db_refresh_token["expires_at"].replace(tzinfo=None) < datetime.utcnow():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user_id = db_refresh_token["user_id"]

    # Fetch the user object
    user, _ = await db.users.get_user_by_id(user_id)
    if user is None or not user.is_active or user.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Delete old refresh token from DB
    await db.users.delete_refresh_token(refresh_token.credentials)

    # Create new access token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    new_access_token = create_access_token(
        data={"sub": str(user.id)}, expires_delta=access_token_expires
    )

    # Create new refresh token (random string)
    new_refresh_token_expires_at = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    new_refresh_token = create_refresh_token(
        data={"sub": str(user.id)}, expires_delta=timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    )

    # Store new refresh token in DB
    await db.users.add_refresh_token(user.id, new_refresh_token, new_refresh_token_expires_at)

    return {"access_token": new_access_token, "token_type": "bearer", "refresh_token": new_refresh_token}

@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout_user(
    refresh_token: str = Depends(HTTPBearer(auto_error=False)),
):
    """
    Logout user by invalidating refresh token.
    """
    if refresh_token and refresh_token.credentials:
        await db.users.delete_refresh_token(refresh_token.credentials)
    return

@router.get("/verify-email/{verification_token}", status_code=status.HTTP_200_OK)
async def verify_email(verification_token: UUID):
    """
    Verify user's email address using a verification token.
    """
    user = await db.users.get_user_by_verification_token(verification_token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired verification token",
        )
    
    if user.is_verified:
        return {"message": "Email already verified."}

    verified = await db.users.verify_user_email(verification_token)
    if not verified:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify email",
        )
    return {"message": "Email verified successfully!"}

@router.post("/forgot-password", status_code=status.HTTP_200_OK)
async def forgot_password(
    request: PasswordResetRequest,
    background_tasks: BackgroundTasks,
):
    """
    Request a password reset link for the given email.
    """
    user, personal_data = await db.users.get_user_by_email(request.email)
    if not user or not personal_data:
        # Return a generic success message to prevent email enumeration
        return {"message": "If an account with that email exists, a password reset link has been sent."}

    reset_token = str(uuid4())
    expires_at = datetime.utcnow() + timedelta(hours=1) # Token valid for 1 hour

    await db.users.add_password_reset_token(user.id, reset_token, expires_at)
    await send_password_reset_email(personal_data.email, reset_token, background_tasks)

    return {"message": "If an account with that email exists, a password reset link has been sent."}

@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(
    request: PasswordResetConfirm,
):
    """
    Reset password using a valid reset token.
    """
    reset_token_data = await db.users.get_password_reset_token(request.token)
    if not reset_token_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired password reset token",
        )
    
    if reset_token_data["used"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password reset token already used",
        )

    user_id = reset_token_data["user_id"]
    hashed_password = get_password_hash(request.new_password)

    async with db.users._atomic() as conn: # Use atomic transaction for multiple DB operations
        updated = await db.users.update_user_password(user_id, hashed_password)
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update password",
            )
        
        marked_used = await db.users.mark_password_reset_token_used(reset_token_data["id"])
        if not marked_used:
            # This is a critical error, token should be marked used
            logger.error(f"Failed to mark password reset token {reset_token_data['id']} as used after password update.")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Password updated, but failed to invalidate reset token. Please contact support.",
            )

    return {"message": "Password reset successfully!"}


async def verify_authentication(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
) -> UserPersonalData: # Return UserPersonalData
    """
    Verify bearer token (JWT) authentication.

    Args:
        credentials: The HTTP authorization credentials containing the bearer token.

    Returns:
        The authenticated UserPersonalData object.
    """
    if credentials and credentials.scheme == "Bearer":
        token = credentials.credentials
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            user_id: str = payload.get("sub")
            if user_id is None:
                raise JWTError
            
            user, personal_data = await db.users.get_user_by_id(UUID(user_id))
            if user is None or personal_data is None or not user.is_active or user.deleted_at is not None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid authentication credentials",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            logger.debug(f"Authenticated access (JWT) for user: {personal_data.name} (id={personal_data.user_id})")
            return personal_data
        except JWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
    
    logger.debug("No valid JWT provided.")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


# Dependency for protecting routes
RequireAuth = Depends(verify_authentication)
