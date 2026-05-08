from pydantic import BaseModel
from typing import Any, Optional


class WhatsAppValue(BaseModel):
    messages: Optional[list[dict]] = None
    contacts: Optional[list[dict]] = None
    metadata: Optional[dict] = None

    class Config:
        extra = "allow"


class WhatsAppChange(BaseModel):
    value: WhatsAppValue
    field: str

    class Config:
        extra = "allow"


class WhatsAppEntry(BaseModel):
    id: str
    changes: list[WhatsAppChange]

    class Config:
        extra = "allow"


class WhatsAppPayload(BaseModel):
    object: str
    entry: list[WhatsAppEntry]

    class Config:
        extra = "allow"
