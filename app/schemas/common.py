# app/schemas/common.py

from pydantic import BaseModel


class QueuedResponse(BaseModel):
    status: str = "queued"
    message: str
    resource_id: str