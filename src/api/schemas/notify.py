from pydantic import BaseModel, EmailStr


class NotifyRequest(BaseModel):
    target_email: EmailStr
    result_url: str
