"""Camada HTTP mínima da aplicação."""

from app.api.router import api_router, build_api_router

__all__ = ["api_router", "build_api_router"]
