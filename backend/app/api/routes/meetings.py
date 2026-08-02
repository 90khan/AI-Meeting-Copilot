"""Minimal loopback Meeting commands for the desktop session flow."""

from typing import cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from app.application.dto import CreateMeetingCommand, StartMeetingCommand
from app.core.container import Container
from app.domain.exceptions import InvalidStateTransitionError, ValidationError
from app.domain.value_objects import MeetingId

router = APIRouter(prefix="/api/v1/meetings")


class CreateMeetingRequest(BaseModel):
    """Input accepted to create one Meeting."""

    model_config = ConfigDict(extra="forbid")

    name: str


class CreateMeetingResponse(BaseModel):
    """Safe meeting identity returned to the desktop."""

    meeting_id: UUID


@router.post(
    "", response_model=CreateMeetingResponse, status_code=status.HTTP_201_CREATED
)
async def create_meeting(
    payload: CreateMeetingRequest, request: Request
) -> CreateMeetingResponse:
    """Create a Meeting through the application use case."""

    try:
        result = (
            await _container(request)
            .get_create_meeting_use_case()
            .execute(CreateMeetingCommand(name=payload.name))
        )
    except ValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY) from error
    return CreateMeetingResponse(meeting_id=result.meeting_id.value)


@router.post("/{meeting_id}/start", status_code=status.HTTP_204_NO_CONTENT)
async def start_meeting(meeting_id: UUID, request: Request) -> None:
    """Start an existing Meeting through the application use case."""

    try:
        await _container(request).get_start_meeting_use_case().execute(
            StartMeetingCommand(meeting_id=MeetingId(value=meeting_id))
        )
    except LookupError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from error
    except (ValidationError, InvalidStateTransitionError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT) from error


def _container(request: Request) -> Container:
    """Return the lifespan-managed composition root."""

    return cast(Container, request.app.state.container)
