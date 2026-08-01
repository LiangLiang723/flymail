"""Tenant-scoped mailbox account routes for FlyMail V2."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from flymail.api.dependencies import (
    get_accounts_service,
    get_request_context,
    require_csrf,
    require_session,
    require_user,
)
from flymail.api.schemas.accounts import (
    AccountListResponse,
    AccountResponse,
    CreateAccountRequest,
    CreateIdentityRequest,
    DeleteAccountRequest,
    DeleteAccountResponse,
    IdentityListResponse,
    IdentityResponse,
    OAuthCallbackResponse,
    OAuthStartRequest,
    OAuthStartResponse,
    OAuthStatusResponse,
    ProxyResponse,
    SaveProxyRequest,
    UpdateAccountRequest,
    UpdateCredentialRequest,
    UpdateIdentityRequest,
    VerificationResponse,
)
from flymail.application.accounts import (
    AccountsService,
    CreateAccountCommand,
    SaveProxyCommand,
    StartOAuthCommand,
    UpdateAccountCommand,
    UpdateCredentialCommand,
    UpdateIdentityCommand,
    UpsertIdentityCommand,
)
from flymail.application.auth import AuthenticatedSession
from flymail.repositories.base import TenantContext
from flymail.repositories.users import User


router = APIRouter(prefix="/api/v2/accounts", tags=["accounts"])


@router.get("", response_model=AccountListResponse)
async def list_accounts(
    user: User = Depends(require_user),
    service: AccountsService = Depends(get_accounts_service),
) -> AccountListResponse:
    accounts = await service.list_accounts(TenantContext(user.id))
    return AccountListResponse(
        items=[AccountResponse.from_account(account) for account in accounts]
    )


@router.get("/proxy", response_model=ProxyResponse | None)
async def get_oauth_proxy(
    user: User = Depends(require_user),
    service: AccountsService = Depends(get_accounts_service),
) -> ProxyResponse | None:
    proxy = await service.get_oauth_proxy(TenantContext(user.id))
    if proxy is None:
        return None
    return ProxyResponse(
        id=proxy.id,
        scheme=proxy.scheme,
        host=proxy.host,
        port=proxy.port,
        enabled=proxy.enabled,
        has_credentials=proxy.has_credentials,
    )


@router.put("/proxy", response_model=ProxyResponse)
async def save_oauth_proxy(
    payload: SaveProxyRequest,
    request: Request,
    _session: AuthenticatedSession = Depends(require_csrf),
    user: User = Depends(require_user),
    service: AccountsService = Depends(get_accounts_service),
) -> ProxyResponse:
    proxy = await service.save_oauth_proxy(
        TenantContext(user.id),
        SaveProxyCommand(
            scheme=payload.scheme,
            host=payload.host,
            port=payload.port,
            username=payload.username,
            password=payload.password,
        ),
        request_id=get_request_context(request).request_id,
    )
    return ProxyResponse(
        id=proxy.id,
        scheme=proxy.scheme,
        host=proxy.host,
        port=proxy.port,
        enabled=proxy.enabled,
        has_credentials=proxy.has_credentials,
    )


@router.post(
    "/oauth/start",
    response_model=OAuthStartResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_oauth(
    payload: OAuthStartRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
    user: User = Depends(require_user),
    service: AccountsService = Depends(get_accounts_service),
) -> OAuthStartResponse:
    result = await service.start_oauth(
        TenantContext(user.id),
        session.session_id,
        StartOAuthCommand(
            provider_key=payload.provider_key,
            email=payload.email,
            display_name=payload.display_name,
            redirect_uri=payload.redirect_uri,
            account_id=payload.account_id,
        ),
        request_id=get_request_context(request).request_id,
    )
    return OAuthStartResponse(
        state=result.state,
        account_id=result.account_id,
        authorization_url=result.authorization_url,
        expires_at=result.expires_at,
    )


@router.get("/oauth/status", response_model=OAuthStatusResponse)
async def oauth_status(
    state: str,
    session: AuthenticatedSession = Depends(require_session),
    service: AccountsService = Depends(get_accounts_service),
) -> OAuthStatusResponse:
    value = await service.oauth_status(
        TenantContext(session.user.id),
        session.session_id,
        state,
    )
    return OAuthStatusResponse(status=value)


@router.get("/oauth/callback", response_model=OAuthCallbackResponse)
async def oauth_callback(
    state: str,
    code: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
    service: AccountsService = Depends(get_accounts_service),
) -> OAuthCallbackResponse:
    result = await service.complete_oauth(
        TenantContext(session.user.id),
        session.session_id,
        state=state,
        code=code,
        request_id=get_request_context(request).request_id,
    )
    return OAuthCallbackResponse(
        account=AccountResponse.from_account(result.account),
        job_id=result.job_id,
    )


@router.get("/{account_id}", response_model=AccountResponse)
async def get_account(
    account_id: str,
    user: User = Depends(require_user),
    service: AccountsService = Depends(get_accounts_service),
) -> AccountResponse:
    account = await service.get_account(TenantContext(user.id), account_id)
    return AccountResponse.from_account(account)


@router.patch("/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: str,
    payload: UpdateAccountRequest,
    request: Request,
    _session: AuthenticatedSession = Depends(require_csrf),
    user: User = Depends(require_user),
    service: AccountsService = Depends(get_accounts_service),
) -> AccountResponse:
    account = await service.update_account(
        TenantContext(user.id),
        account_id,
        UpdateAccountCommand(
            display_name=payload.display_name,
            remark=payload.remark,
            group_name=payload.group_name,
            poll_interval_seconds=payload.poll_interval_seconds,
            enabled=payload.enabled,
        ),
        request_id=get_request_context(request).request_id,
    )
    return AccountResponse.from_account(account)


@router.put(
    "/{account_id}/credentials",
    response_model=VerificationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def update_credential(
    account_id: str,
    payload: UpdateCredentialRequest,
    request: Request,
    _session: AuthenticatedSession = Depends(require_csrf),
    user: User = Depends(require_user),
    service: AccountsService = Depends(get_accounts_service),
) -> VerificationResponse:
    job_id = await service.update_credential(
        TenantContext(user.id),
        account_id,
        UpdateCredentialCommand(
            credential_type=payload.credential_type,
            credential=payload.credential,
        ),
        request_id=get_request_context(request).request_id,
    )
    return VerificationResponse(
        job_id=job_id,
        status_url=f"/api/v2/jobs/{job_id}",
    )


@router.delete(
    "/{account_id}",
    response_model=DeleteAccountResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def delete_account(
    account_id: str,
    payload: DeleteAccountRequest,
    request: Request,
    _session: AuthenticatedSession = Depends(require_csrf),
    user: User = Depends(require_user),
    service: AccountsService = Depends(get_accounts_service),
) -> DeleteAccountResponse:
    result = await service.delete_account(
        TenantContext(user.id),
        account_id,
        confirm_email=payload.confirm_email,
        request_id=get_request_context(request).request_id,
    )
    return DeleteAccountResponse(
        account=AccountResponse.from_account(result.account),
        cleanup_job_id=result.cleanup_job_id,
    )


@router.get(
    "/{account_id}/identities",
    response_model=IdentityListResponse,
)
async def list_identities(
    account_id: str,
    user: User = Depends(require_user),
    service: AccountsService = Depends(get_accounts_service),
) -> IdentityListResponse:
    identities = await service.list_identities(TenantContext(user.id), account_id)
    return IdentityListResponse(
        items=[IdentityResponse.from_identity(identity) for identity in identities]
    )


@router.post(
    "/{account_id}/identities",
    response_model=IdentityResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_identity(
    account_id: str,
    payload: CreateIdentityRequest,
    request: Request,
    _session: AuthenticatedSession = Depends(require_csrf),
    user: User = Depends(require_user),
    service: AccountsService = Depends(get_accounts_service),
) -> IdentityResponse:
    identity = await service.create_identity(
        TenantContext(user.id),
        account_id,
        UpsertIdentityCommand(
            from_address=payload.from_address,
            display_name=payload.display_name,
            reply_to=payload.reply_to,
            signature_html=payload.signature_html,
            signature_text=payload.signature_text,
            is_default=payload.is_default,
        ),
        request_id=get_request_context(request).request_id,
    )
    return IdentityResponse.from_identity(identity)


@router.patch(
    "/{account_id}/identities/{identity_id}",
    response_model=IdentityResponse,
)
async def update_identity(
    account_id: str,
    identity_id: str,
    payload: UpdateIdentityRequest,
    request: Request,
    _session: AuthenticatedSession = Depends(require_csrf),
    user: User = Depends(require_user),
    service: AccountsService = Depends(get_accounts_service),
) -> IdentityResponse:
    identity = await service.update_identity(
        TenantContext(user.id),
        account_id,
        identity_id,
        UpdateIdentityCommand(
            display_name=payload.display_name,
            reply_to=payload.reply_to,
            signature_html=payload.signature_html,
            signature_text=payload.signature_text,
            is_default=payload.is_default,
        ),
        request_id=get_request_context(request).request_id,
    )
    return IdentityResponse.from_identity(identity)


@router.post(
    "/{account_id}/verify",
    response_model=VerificationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def verify_account(
    account_id: str,
    request: Request,
    _session: AuthenticatedSession = Depends(require_csrf),
    user: User = Depends(require_user),
    service: AccountsService = Depends(get_accounts_service),
) -> VerificationResponse:
    job_id = await service.request_verification(
        TenantContext(user.id),
        account_id,
        request_id=get_request_context(request).request_id,
    )
    return VerificationResponse(
        job_id=job_id,
        status_url=f"/api/v2/jobs/{job_id}",
    )


@router.post("", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
async def create_account(
    payload: CreateAccountRequest,
    request: Request,
    _session: AuthenticatedSession = Depends(require_csrf),
    user: User = Depends(require_user),
    service: AccountsService = Depends(get_accounts_service),
) -> AccountResponse:
    account = await service.create_account(
        TenantContext(user.id),
        CreateAccountCommand(
            provider_key=payload.provider_key,
            email=payload.email,
            display_name=payload.display_name,
            credential_type=payload.credential_type,
            credential=payload.credential,
            endpoint_config=(
                payload.endpoint_config.model_dump(mode="json")
                if payload.endpoint_config is not None
                else None
            ),
            poll_interval_seconds=payload.poll_interval_seconds,
        ),
        request_id=get_request_context(request).request_id,
    )
    return AccountResponse.from_account(account)
