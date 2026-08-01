from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.auth.security import user_id_from_token
from app.db.models import User
from app.db.session import ANON_EMAIL, get_db

_bearer = HTTPBearer(auto_error=False)


def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: Session = Depends(get_db),
) -> Optional[User]:
    if credentials is None or not credentials.credentials:
        return None
    uid = user_id_from_token(credentials.credentials)
    if uid is None:
        return None
    user = db.scalar(
        select(User)
        .options(joinedload(User.portfolios))
        .where(User.id == uid)
    )
    if user is None or user.email == ANON_EMAIL:
        return None
    return user


def get_current_user(
    user: Optional[User] = Depends(get_optional_user),
) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Sign in to continue.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
