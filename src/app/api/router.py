from fastapi import APIRouter

from app.api.routes import (
    chat_router,
    conversations_router,
    generated_audio_router,
    ops_router,
    settings_router,
    threads_router,
    webhooks_router,
)


def build_api_router(*, include_internal_routes: bool = True) -> APIRouter:
    router = APIRouter()
    router.include_router(ops_router)
    router.include_router(settings_router)
    router.include_router(generated_audio_router)
    router.include_router(conversations_router)
    if include_internal_routes:
        router.include_router(chat_router)
        router.include_router(threads_router)
    router.include_router(webhooks_router)
    return router


api_router = build_api_router()
