"""Seed retired request contracts for compatibility tests, never through creation APIs."""

from uuid import UUID, uuid4

import httpx

from novel_writer.generation.schemas import NovelRunSpec
from novel_writer.generation.service import GenerationService
from tests.integration.support import DATABASE_URL, database_url_error


def saved_generation_fixture(client, path, data):
    if error := database_url_error(DATABASE_URL):
        raise ValueError(error)

    async def seed():
        async with client.app.state.database.session() as session, session.begin():
            service = GenerationService(session, client.app.state.provider_profile_store)
            return await service.create(
                UUID(path.split("/")[3]), NovelRunSpec.model_validate(data), str(uuid4())
            )

    return httpx.Response(200, json=client.portal.call(seed))


def saved_amendment_fixture(client, path, data):
    """Prepare a historical amendment receipt without exposing retired creation APIs."""
    from novel_writer.generation.amendments import preview_amendment
    from novel_writer.generation.schemas import AmendmentRequest
    from novel_writer.services.errors import ConflictError, WorkflowError

    if error := database_url_error(DATABASE_URL):
        raise ValueError(error)

    async def seed():
        async with client.app.state.database.session() as session, session.begin():
            service = GenerationService(session, client.app.state.provider_profile_store)
            batch = await service.batch(UUID(path.split("/")[3]), UUID(path.split("/")[5]))
            return await preview_amendment(service, batch, AmendmentRequest.model_validate(data))

    try:
        return httpx.Response(200, json=client.portal.call(seed))
    except ConflictError as error:
        return httpx.Response(409, json={"detail": str(error)})
    except WorkflowError as error:
        return httpx.Response(400, json={"detail": str(error)})
