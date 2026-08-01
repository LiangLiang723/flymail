"""Serialized, stateful IMAP session independent of any concrete transport."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from enum import Enum

from flymail.providers.contracts import ProviderPlugin, ServiceEndpoint
from flymail.providers.core.imap_commands import (
    IdleEvent,
    ImapCommand,
    ImapCredentials,
    ImapResponse,
    ImapTransport,
    SelectedMailbox,
    capability_command,
    select_command,
)
from flymail.providers.errors import ProviderError, ProviderErrorCode


class ImapSessionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    AUTHENTICATED = "authenticated"
    SELECTED = "selected"
    IDLING = "idling"
    CLOSING = "closing"
    FAILED = "failed"


_POLICY_CAPABILITIES = {
    "IDLE": "supports_idle",
    "MOVE": "supports_move",
    "UIDPLUS": "supports_uidplus",
    "CONDSTORE": "supports_condstore",
    "QRESYNC": "supports_qresync",
    "SPECIAL-USE": "supports_special_use",
    "X-GM-EXT-1": "supports_gmail_labels",
}


class ImapSession:
    """Own one transport and serialize every command over its IMAP stream."""

    def __init__(self, plugin: ProviderPlugin, transport: ImapTransport) -> None:
        if not isinstance(plugin, ProviderPlugin):
            raise TypeError("plugin must satisfy ProviderPlugin")
        self._plugin = plugin
        self._transport = transport
        self._command_lock = asyncio.Lock()
        self._transport_closed = False
        self._endpoint: ServiceEndpoint | None = None
        self._state = ImapSessionState.DISCONNECTED
        self._selected_mailbox: SelectedMailbox | None = None
        self._server_capabilities: frozenset[str] = frozenset()
        self._capabilities: frozenset[str] = frozenset()

    @property
    def state(self) -> ImapSessionState:
        return self._state

    @property
    def selected_mailbox(self) -> SelectedMailbox | None:
        return self._selected_mailbox

    @property
    def server_capabilities(self) -> frozenset[str]:
        return self._server_capabilities

    @property
    def capabilities(self) -> frozenset[str]:
        return self._capabilities

    async def connect(
        self,
        credentials: ImapCredentials,
        endpoint: ServiceEndpoint,
        proxy: object | None = None,
        *,
        timeout_seconds: float = 30.0,
    ) -> "ImapSession":
        if not isinstance(credentials, ImapCredentials):
            raise TypeError("credentials must be ImapCredentials")
        if not isinstance(endpoint, ServiceEndpoint):
            raise TypeError("endpoint must be ServiceEndpoint")
        timeout = float(timeout_seconds)
        if timeout <= 0:
            raise ValueError("connect timeout must be positive")

        async with self._command_lock:
            if self._state is not ImapSessionState.DISCONNECTED:
                raise self._state_error("connect")
            self._state = ImapSessionState.CONNECTING
            self._endpoint = endpoint
            self._transport_closed = False
            try:
                async with asyncio.timeout(timeout):
                    response = await self._transport.connect(credentials, endpoint, proxy)
                self._raise_for_response("imap.connect", response, close_on_bye=True)
                self._state = ImapSessionState.AUTHENTICATED
                server_capabilities = await self._execute_locked(capability_command())
                self._server_capabilities = server_capabilities
                self._capabilities = self._apply_plugin_policy(server_capabilities)
                return self
            except asyncio.CancelledError:
                await self._mark_failed()
                raise
            except TimeoutError as exc:
                await self._mark_failed()
                raise ProviderError(
                    ProviderErrorCode.CONNECTION_FAILED,
                    debug_context={"operation": "imap.connect", "reason": "timeout"},
                ) from exc
            except ProviderError:
                await self._mark_failed()
                raise
            except Exception as exc:
                error = self._plugin.classify_error("imap.connect", exc)
                await self._mark_failed()
                raise error from exc

    async def execute(self, command: ImapCommand):
        if not isinstance(command, ImapCommand):
            raise TypeError("command must be ImapCommand")
        async with self._command_lock:
            self._require_command_state(command.name)
            return await self._execute_locked(command)

    async def select(
        self,
        mailbox_native_key: str,
        *,
        timeout_seconds: float = 30.0,
    ) -> SelectedMailbox:
        command = select_command(mailbox_native_key, timeout_seconds=timeout_seconds)
        async with self._command_lock:
            self._require_command_state("SELECT")
            selected = await self._execute_locked(command)
            self._selected_mailbox = selected
            self._state = ImapSessionState.SELECTED
            return selected

    async def idle(
        self,
        events: AsyncIterator[IdleEvent] | None = None,
    ) -> AsyncIterator[IdleEvent]:
        await self._command_lock.acquire()
        source: AsyncIterator[IdleEvent] | None = None
        try:
            if self._state is not ImapSessionState.SELECTED or self._selected_mailbox is None:
                raise self._state_error("IDLE")
            if "IDLE" not in self._capabilities:
                raise ProviderError(
                    ProviderErrorCode.UNSUPPORTED_OPERATION,
                    debug_context={"provider": self._plugin.key, "operation": "IDLE"},
                )
            self._state = ImapSessionState.IDLING
            source = events or self._transport.idle(self._selected_mailbox.native_key)
            try:
                async for event in source:
                    if not isinstance(event, IdleEvent):
                        await self._mark_failed()
                        raise ProviderError(
                            ProviderErrorCode.PROTOCOL_ERROR,
                            debug_context={
                                "provider": self._plugin.key,
                                "operation": "IDLE",
                                "event_type": type(event).__name__,
                            },
                        )
                    if event.kind == "bye":
                        await self._mark_failed()
                        raise ProviderError(
                            ProviderErrorCode.CONNECTION_FAILED,
                            debug_context={"provider": self._plugin.key, "operation": "IDLE"},
                        )
                    yield event
            except asyncio.CancelledError:
                await self._mark_failed()
                raise
            except ProviderError:
                raise
            except Exception as exc:
                error = self._plugin.classify_error("imap.idle", exc)
                await self._mark_failed()
                raise error from exc
        finally:
            if source is not None:
                close_source = getattr(source, "aclose", None)
                if callable(close_source):
                    try:
                        await close_source()
                    except Exception:
                        await self._mark_failed()
            if self._state is ImapSessionState.IDLING:
                self._state = ImapSessionState.SELECTED
            self._command_lock.release()

    async def disconnect(self) -> None:
        async with self._command_lock:
            if self._state is ImapSessionState.DISCONNECTED:
                return
            self._state = ImapSessionState.CLOSING
            await self._close_transport()
            self._selected_mailbox = None
            self._server_capabilities = frozenset()
            self._capabilities = frozenset()
            self._state = ImapSessionState.DISCONNECTED

    async def _execute_locked(self, command: ImapCommand):
        try:
            async with asyncio.timeout(command.timeout_seconds):
                response = await self._transport.execute(command.name, command.arguments)
            self._raise_for_response(f"imap.{command.name.casefold()}", response, close_on_bye=True)
            try:
                return command.parse(response)
            except ProviderError:
                raise
            except Exception as exc:
                await self._mark_failed()
                raise ProviderError(
                    ProviderErrorCode.PROTOCOL_ERROR,
                    debug_context={
                        "provider": self._plugin.key,
                        "operation": command.name,
                        "parser_error": type(exc).__name__,
                    },
                ) from exc
        except asyncio.CancelledError:
            await self._mark_failed()
            raise
        except TimeoutError as exc:
            await self._mark_failed()
            raise ProviderError(
                ProviderErrorCode.CONNECTION_FAILED,
                debug_context={
                    "provider": self._plugin.key,
                    "operation": command.name,
                    "reason": "timeout",
                },
            ) from exc
        except ProviderError:
            if self._state is ImapSessionState.FAILED:
                await self._close_transport()
            raise
        except Exception as exc:
            error = self._plugin.classify_error(f"imap.{command.name.casefold()}", exc)
            await self._mark_failed()
            raise error from exc

    def _raise_for_response(
        self,
        operation: str,
        response: ImapResponse,
        *,
        close_on_bye: bool,
    ) -> None:
        if not isinstance(response, ImapResponse):
            raise ProviderError(
                ProviderErrorCode.PROTOCOL_ERROR,
                debug_context={
                    "provider": self._plugin.key,
                    "operation": operation,
                    "response_type": type(response).__name__,
                },
            )
        if response.status in {"OK", "PREAUTH"}:
            return
        if response.status == "BYE":
            if close_on_bye:
                self._state = ImapSessionState.FAILED
            raise ProviderError(
                ProviderErrorCode.CONNECTION_FAILED,
                debug_context={
                    "provider": self._plugin.key,
                    "operation": operation,
                    "status": response.status,
                    "response": response.text,
                },
            )
        raise self._plugin.classify_error(
            operation,
            {"status": response.status, "message": response.text, "data": response.data},
        )

    def _require_command_state(self, operation: str) -> None:
        if self._state not in {ImapSessionState.AUTHENTICATED, ImapSessionState.SELECTED}:
            raise self._state_error(operation)

    def _state_error(self, operation: str) -> ProviderError:
        code = (
            ProviderErrorCode.CONNECTION_FAILED
            if self._state in {
                ImapSessionState.DISCONNECTED,
                ImapSessionState.CLOSING,
                ImapSessionState.FAILED,
            }
            else ProviderErrorCode.PROTOCOL_ERROR
        )
        return ProviderError(
            code,
            debug_context={
                "provider": self._plugin.key,
                "operation": operation,
                "session_state": self._state.value,
            },
        )

    def _apply_plugin_policy(self, server_capabilities: frozenset[str]) -> frozenset[str]:
        policy = self._plugin.capabilities()
        effective: set[str] = set()
        for capability in server_capabilities:
            policy_field = _POLICY_CAPABILITIES.get(capability)
            if policy_field is None or bool(getattr(policy, policy_field)):
                effective.add(capability)
        return frozenset(effective)

    async def _mark_failed(self) -> None:
        self._state = ImapSessionState.FAILED
        await self._close_transport()

    async def _close_transport(self) -> None:
        if self._transport_closed:
            return
        self._transport_closed = True
        try:
            await self._transport.close()
        except Exception:
            # Closing is best-effort; callers already receive the primary failure.
            return

    def __repr__(self) -> str:
        endpoint = self._endpoint.host if self._endpoint is not None else None
        selected = self._selected_mailbox.native_key if self._selected_mailbox is not None else None
        return (
            "ImapSession("
            f"provider={self._plugin.key!r}, state={self._state.value!r}, "
            f"endpoint={endpoint!r}, selected_mailbox={selected!r})"
        )
