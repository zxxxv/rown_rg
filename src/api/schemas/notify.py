from enum import Enum

from pydantic import BaseModel, EmailStr


class ResultType(str, Enum):
    success = "success"
    partial = "partial"
    failed = "failed"


class NotifyRequest(BaseModel):
    target_email: EmailStr
    result_url: str
    result_type: ResultType
