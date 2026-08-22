from __future__ import annotations

from psycopg.errors import UniqueViolation
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ...db import Session
from ...errors import ApiError
from ...models import Role, User
from ...security import hash_password, sign_access_token, verify_password
from .schemas import AuthResult, LoginInput, PublicUser, RegisterInput


def _issue(user: PublicUser) -> str:
    return sign_access_token({"sub": user.id, "role": user.role.value})


async def register(data: RegisterInput) -> AuthResult:
    password_hash = hash_password(data.password)

    user = User(
        email=data.email,
        name=data.name,
        password_hash=password_hash,
        # Hard-coded, not taken from input. Organiser and admin accounts come
        # from the seed script or an admin-only promote endpoint.
        role=Role.CUSTOMER,
    )

    async with Session() as session:
        session.add(user)
        try:
            # Let the unique index decide, rather than checking for an existing
            # email first — a check-then-insert races two simultaneous signups.
            await session.commit()
        except IntegrityError as err:
            await session.rollback()
            if isinstance(err.orig, UniqueViolation):
                raise ApiError.conflict(
                    "EMAIL_TAKEN", "An account with that email already exists."
                ) from err
            raise
        await session.refresh(user)
        public = PublicUser.model_validate(user)

    return AuthResult(user=public, accessToken=_issue(public))


async def login(data: LoginInput) -> AuthResult:
    async with Session() as session:
        user = (
            (await session.execute(select(User).where(User.email == data.email))).scalars().first()
        )

        # One message and one code for both "no such email" and "wrong
        # password". Distinguishing them tells an attacker which addresses have
        # accounts. verify_password burns the same CPU either way, so the timing
        # does not give away what the message withholds.
        ok = verify_password(user.password_hash if user else None, data.password)
        if user is None or not ok:
            raise ApiError.unauthorized("Incorrect email or password.")

        public = PublicUser.model_validate(user)

    return AuthResult(user=public, accessToken=_issue(public))


async def get_by_id(user_id: str) -> PublicUser | None:
    async with Session() as session:
        user = (await session.execute(select(User).where(User.id == user_id))).scalars().first()
        return PublicUser.model_validate(user) if user else None
