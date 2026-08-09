from enum import StrEnum

from pydantic import BaseModel, EmailStr


class ResultType(StrEnum):
    success = "success"
    partial = "partial"
    failed = "failed"


class NotifyRequest(BaseModel):
    target_email: EmailStr
    result_url: str
    result_type: ResultType
