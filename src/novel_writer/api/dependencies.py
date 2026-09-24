from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Provide one transaction-scoped database session per API request."""
    async with request.app.state.database.session() as session, session.begin():
        yield session


Session = Annotated[AsyncSession, Depends(get_session)]

IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200)]
