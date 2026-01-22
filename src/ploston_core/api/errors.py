"""REST API error handlers."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ploston_core.api.middleware.request_id import get_request_id
from ploston_core.errors import AELError


def setup_error_handlers(app: FastAPI) -> None:
    """Configure error handlers for the FastAPI app."""

    @app.exception_handler(AELError)
    async def ael_error_handler(request: Request, exc: AELError) -> JSONResponse:
        """Handle AEL errors."""
        error_dict = exc.to_dict()
        error_dict["request_id"] = get_request_id()

        return JSONResponse(
            status_code=exc.http_status,
            content={"error": error_dict},
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """Handle HTTP exceptions."""
        # If detail is already a dict (from our handlers), use it
        if isinstance(exc.detail, dict):
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": exc.detail},
            )

        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": f"HTTP_{exc.status_code}",
                    "category": "SYSTEM",
                    "message": str(exc.detail),
                    "request_id": get_request_id(),
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Handle request validation errors."""
        errors = exc.errors()
        first_error = errors[0] if errors else {"msg": "Validation error"}

        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "category": "VALIDATION",
                    "message": first_error.get("msg", "Validation error"),
                    "detail": str(errors),
                    "request_id": get_request_id(),
                }
            },
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Handle unexpected exceptions."""
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "category": "SYSTEM",
                    "message": "An unexpected error occurred",
                    "detail": str(exc) if app.debug else None,
                    "request_id": get_request_id(),
                }
            },
        )

