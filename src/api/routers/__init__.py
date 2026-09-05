from fastapi import APIRouter

from src.api.routers.admin import router as admin_router
from src.api.routers.auth import router as auth_router
from src.api.routers.glossary import router as glossary_router
from src.api.routers.gpu_monitor import router as gpu_monitor_router
from src.api.routers.library import router as library_router
from src.api.routers.projects import analysts_router, presets_router
from src.api.routers.projects import router as projects_router
from src.api.routers.prompts import router as prompts_router
from src.api.routers.settings import router as settings_router
from src.api.routers.users import router as users_router
from src.api.routers.verify_coverage import router as verify_coverage_router

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(auth_router)
api_v1_router.include_router(users_router)
api_v1_router.include_router(projects_router)
api_v1_router.include_router(verify_coverage_router)
api_v1_router.include_router(glossary_router)
api_v1_router.include_router(presets_router)
api_v1_router.include_router(analysts_router)
api_v1_router.include_router(admin_router)
api_v1_router.include_router(gpu_monitor_router)
api_v1_router.include_router(library_router)
api_v1_router.include_router(prompts_router)
api_v1_router.include_router(settings_router)

__all__ = ["api_v1_router"]
