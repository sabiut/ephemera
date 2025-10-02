from sqlalchemy.orm import Session
from typing import Optional
from app.models.user import User


def get_user_by_github_id(db: Session, github_id: int) -> Optional[User]:
    """Get user by GitHub ID"""
    return db.query(User).filter(User.github_id == github_id).first()


def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    """Get user by ID"""
    return db.query(User).filter(User.id == user_id).first()


def create_user(
    db: Session,
    github_id: int,
    github_login: str,
    email: Optional[str] = None,
    avatar_url: Optional[str] = None
) -> User:
    """Create a new user"""
    db_user = User(
        github_id=github_id,
        github_login=github_login,
        email=email,
        avatar_url=avatar_url
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


def get_or_create_user(
    db: Session,
    github_id: int,
    github_login: str,
    email: Optional[str] = None,
    avatar_url: Optional[str] = None
) -> User:
    """Get existing user or create if not exists"""
    user = get_user_by_github_id(db, github_id)
    if user:
        # Update user info if changed
        if user.github_login != github_login:
            user.github_login = github_login
        if email and user.email != email:
            user.email = email
        if avatar_url and user.avatar_url != avatar_url:
            user.avatar_url = avatar_url
        db.commit()
        db.refresh(user)
        return user

    return create_user(db, github_id, github_login, email, avatar_url)
