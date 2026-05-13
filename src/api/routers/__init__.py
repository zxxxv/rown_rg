from fastapi import APIRouter

from src.api.routers.auth import router as auth_router
from src.api.routers.users import router as users_router

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(auth_router)
api_v1_router.include_router(users_router)

__all__ = ["api_v1_router"]
