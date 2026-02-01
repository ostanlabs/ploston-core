"""Runner router.

Implements S-184: Runner REST API
- T-530: POST /api/v1/runners - Create runner
- T-531: GET /api/v1/runners - List runners
- T-532: GET /api/v1/runners/{name} - Get runner details
- T-533: DELETE /api/v1/runners/{name} - Delete runner
"""

from fastapi import APIRouter, HTTPException, Path, Request

from ploston_core.api.auth import RunnerPermissions, auth_hook
from ploston_core.api.models import (
    RunnerCreateRequest,
    RunnerCreateResponse,
    RunnerDeleteResponse,
    RunnerDetail,
    RunnerListResponse,
    RunnerStatusEnum,
    RunnerSummary,
    RunnerTokenResponse,
)
from ploston_core.runner_management import RunnerRegistry, RunnerStatus

runner_router = APIRouter(prefix="/runners", tags=["Runners"])


def _status_to_enum(status: RunnerStatus) -> RunnerStatusEnum:
    """Convert internal RunnerStatus to API RunnerStatusEnum."""
    return (
        RunnerStatusEnum.CONNECTED
        if status == RunnerStatus.CONNECTED
        else RunnerStatusEnum.DISCONNECTED
    )


def _get_registry(request: Request) -> RunnerRegistry:
    """Get runner registry from app state."""
    registry = getattr(request.app.state, "runner_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="Runner registry not available")
    return registry


def _get_cp_url(request: Request) -> str:
    """Get control plane URL for install command."""
    # Try to get from app state config, fallback to request URL
    config = getattr(request.app.state, "config", None)
    if config and hasattr(config, "cp_url") and config.cp_url:
        return config.cp_url
    # Fallback: construct from request
    return f"{request.url.scheme}://{request.url.netloc}"


@runner_router.post("", response_model=RunnerCreateResponse)
async def create_runner(
    request: Request,
    create_request: RunnerCreateRequest,
) -> RunnerCreateResponse:
    """Create a new runner - DEPRECATED.

    Runners must be defined in the config file under the 'runners' section.
    Use 'ploston runner get-token <name>' to get the connection token.
    """
    # Runners can only be created via config file
    raise HTTPException(
        status_code=400,
        detail=(
            "Runners must be defined in the config file. "
            "Add the runner to the 'runners' section in ael-config.yaml, "
            "then use 'ploston runner get-token <name>' to get the connection token."
        ),
    )


@runner_router.get("", response_model=RunnerListResponse)
async def list_runners(
    request: Request,
    status: RunnerStatusEnum | None = None,
) -> RunnerListResponse:
    """List all runners, optionally filtered by status."""
    # Auth hook: Enterprise overrides to check RBAC
    await auth_hook.check_permission(request, RunnerPermissions.LIST)

    registry = _get_registry(request)

    runners = registry.list()

    # Filter by status if provided
    if status:
        internal_status = (
            RunnerStatus.CONNECTED
            if status == RunnerStatusEnum.CONNECTED
            else RunnerStatus.DISCONNECTED
        )
        runners = [r for r in runners if r.status == internal_status]

    summaries = [
        RunnerSummary(
            id=r.id,
            name=r.name,
            status=_status_to_enum(r.status),
            last_seen=r.last_seen,
            tool_count=len(r.available_tools),
        )
        for r in runners
    ]

    return RunnerListResponse(runners=summaries, total=len(summaries))


@runner_router.get("/{name}", response_model=RunnerDetail)
async def get_runner(
    request: Request,
    name: str = Path(..., description="Runner name"),
) -> RunnerDetail:
    """Get detailed information about a specific runner."""
    # Auth hook: Enterprise overrides to check RBAC
    await auth_hook.check_permission(request, RunnerPermissions.READ)

    registry = _get_registry(request)

    runner = registry.get_by_name(name)
    if not runner:
        raise HTTPException(status_code=404, detail=f"Runner '{name}' not found")

    return RunnerDetail(
        id=runner.id,
        name=runner.name,
        status=_status_to_enum(runner.status),
        created_at=runner.created_at,
        last_seen=runner.last_seen,
        available_tools=runner.available_tools,
        mcps=runner.mcps,
    )


def _is_config_mode(request: Request) -> bool:
    """Check if the system is in configuration mode."""
    mode_manager = getattr(request.app.state, "mode_manager", None)
    if mode_manager is None:
        return False
    return mode_manager.is_configuration_mode()


@runner_router.delete("/{name}", response_model=RunnerDeleteResponse)
async def delete_runner(
    request: Request,
    name: str = Path(..., description="Runner name"),
) -> RunnerDeleteResponse:
    """Delete a runner and revoke its token.

    Also removes the runner from the config file immediately.
    Not available in configuration mode - use config_delete instead.
    """
    # Block in config mode
    if _is_config_mode(request):
        raise HTTPException(
            status_code=400,
            detail=(
                "Runner delete not available in configuration mode. "
                f"Use 'config_delete runners.{name}' to stage runner removal, "
                "then 'config_done' to apply."
            ),
        )

    # Auth hook: Enterprise overrides to check RBAC
    await auth_hook.check_permission(request, RunnerPermissions.DELETE)

    registry = _get_registry(request)

    # Use async version if available (PersistentRunnerRegistry)
    # This also updates the config file
    if hasattr(registry, "delete_by_name_async"):
        deleted = await registry.delete_by_name_async(name)
    else:
        deleted = registry.delete_by_name(name)

    if not deleted:
        raise HTTPException(status_code=404, detail=f"Runner '{name}' not found")

    return RunnerDeleteResponse(deleted=True, name=name)


@runner_router.post("/{name}/regenerate-token", response_model=RunnerTokenResponse)
async def regenerate_runner_token(
    request: Request,
    name: str = Path(..., description="Runner name"),
) -> RunnerTokenResponse:
    """Regenerate a runner's authentication token.

    This invalidates the old token and generates a new one.
    The new token is only shown once.
    """
    # Auth hook: Enterprise overrides to check RBAC
    await auth_hook.check_permission(
        request, RunnerPermissions.DELETE
    )  # Requires elevated permission

    registry = _get_registry(request)

    # Check runner exists
    runner = registry.get_by_name(name)
    if not runner:
        raise HTTPException(status_code=404, detail=f"Runner '{name}' not found")

    # Regenerate token (only available on PersistentRunnerRegistry)
    if not hasattr(registry, "regenerate_token"):
        raise HTTPException(
            status_code=501,
            detail="Token regeneration requires Redis persistence",
        )

    token = await registry.regenerate_token(name)
    if not token:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to regenerate token for runner '{name}'",
        )

    cp_url = _get_cp_url(request)
    install_command = f"ploston-runner install --cp-url {cp_url} --token {token}"

    return RunnerTokenResponse(
        name=name,
        token=token,
        install_command=install_command,
    )
