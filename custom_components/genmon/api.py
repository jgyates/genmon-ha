"""Async API client for communicating with the genhalink addon."""
from __future__ import annotations

import asyncio
import logging
import re

import aiohttp

_LOGGER = logging.getLogger(__name__)


class GenmonConnectionError(Exception):
    """Error connecting to genhalink."""


class GenmonAuthError(Exception):
    """Authentication error with genhalink."""


class GenmonCommandError(Exception):
    """genmon rejected a command, or genhalink got no answer from genmon.

    The message has genmon's answer but not the command: a controller
    button's command can hold the controller passcode.
    """

    def __init__(self, command: str, result: str | None) -> None:
        self.command = command
        self.result = result
        super().__init__(f"genmon rejected the command: {result!r}")


# genmon answers commands with text: "... sent", "OK" etc. on success, and
# these on failure (genmonlib Set*/ExecuteRemoteCommand/SetCommandButton)
_COMMAND_ERROR = re.compile(
    r"^(error|invalid|validation error|command not found|malformed|no commands defined)"
    r"|not supported",
    re.IGNORECASE,
)


def command_failed(result: str | None) -> bool:
    """True if genmon's answer to a command is an error (or no answer)."""
    if result is None:
        return True
    text = str(result).strip()
    return not text or bool(_COMMAND_ERROR.search(text))


class GenmonApiClient:
    """Async client for the genhalink REST + WebSocket API."""

    def __init__(
        self,
        host: str,
        port: int,
        api_key: str,
        session: aiohttp.ClientSession | None = None,
        use_ssl: bool = True,
    ) -> None:
        self._host = host
        self._port = port
        self._api_key = api_key
        self._session = session
        self._use_ssl = use_ssl
        http_scheme = "https" if use_ssl else "http"
        self._base_url = f"{http_scheme}://{host}:{port}"
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        # HTTPS: genhalink's certificate is self-signed, so encrypt without
        # verifying it (False). Plain HTTP: aiohttp's default (True; None
        # is deprecated).
        self._ssl_context: bool = not use_ssl

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        session = await self._ensure_session()
        url = f"{self._base_url}{path}"
        try:
            async with session.request(
                method, url, headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=10),
                ssl=self._ssl_context, **kwargs
            ) as resp:
                if resp.status == 401:
                    raise GenmonAuthError("Invalid API key")
                if resp.status != 200:
                    raise GenmonConnectionError(
                        f"HTTP {resp.status} from {path}"
                    )
                return await resp.json()
        except aiohttp.ClientError as err:
            raise GenmonConnectionError(f"Cannot connect to {url}: {err}") from err
        except asyncio.TimeoutError as err:
            raise GenmonConnectionError(f"Timeout connecting to {url}") from err

    async def get_health(self) -> dict:
        """Check health (unauthenticated)."""
        session = await self._ensure_session()
        url = f"{self._base_url}/api/health"
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=5),
                ssl=self._ssl_context,
            ) as resp:
                return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise GenmonConnectionError(f"Cannot connect: {err}") from err

    async def get_info(self) -> dict:
        """Get generator start info (controller type, capabilities, model)."""
        return await self._request("GET", "/api/info")

    async def get_status(self) -> dict:
        """Get full state snapshot."""
        return await self._request("GET", "/api/status")

    async def get_entities(self) -> dict:
        """Get entity definitions (predefined + dynamic)."""
        return await self._request("GET", "/api/entities")

    async def send_command(self, command: str) -> str:
        """Send a command to genmon and return its answer.

        genhalink answers HTTP 200 with {"result": <genmon's answer>} even
        when genmon rejects the command, so the answer is checked here.
        """
        response = await self._request(
            "POST", "/api/command", json={"command": command}
        )
        result = response.get("result") if isinstance(response, dict) else None
        if command_failed(result):
            raise GenmonCommandError(command, result)
        return str(result).strip()

    async def validate_connection(self) -> dict:
        """Validate connection: check health then auth.

        If use_ssl is True and the HTTPS probe fails, automatically retries
        over plain HTTP so auto-detection works for older genhalink instances.
        """
        try:
            await self.get_health()
        except GenmonConnectionError:
            if self._use_ssl:
                # Transparently fall back to HTTP
                self._use_ssl = False
                self._ssl_context = True
                self._base_url = f"http://{self._host}:{self._port}"
                await self.get_health()
            else:
                raise
        return await self.get_info()

    async def listen_ws(self, callback) -> bool:
        """Connect to WebSocket and call callback on each message.

        Returns when the connection closes: True if genhalink accepted the
        login, False if the connection failed before that. Raises
        GenmonAuthError if genhalink rejects the API key.
        """
        logged_in = False
        session = await self._ensure_session()
        ws_scheme = "wss" if self._use_ssl else "ws"
        url = f"{ws_scheme}://{self._host}:{self._port}/ws"
        try:
            async with session.ws_connect(url, ssl=self._ssl_context) as ws:
                # Authenticate via first message
                await ws.send_json({"type": "auth", "token": self._api_key})
                auth_resp = await ws.receive_json(timeout=10)
                if auth_resp.get("type") != "auth_ok":
                    raise GenmonAuthError(f"WebSocket auth failed: {auth_resp}")
                logged_in = True
                self._ws = ws
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            data = msg.json()
                        except Exception:
                            _LOGGER.debug("Failed to parse WS message")
                            continue
                        await callback(data)
                    elif msg.type in (
                        aiohttp.WSMsgType.ERROR,
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.CLOSED,
                    ):
                        break
        except (aiohttp.ClientError, asyncio.TimeoutError, TypeError, ValueError) as err:
            # TypeError / ValueError: the auth answer was not a JSON text message
            _LOGGER.debug("WebSocket connection error: %s", err)
        finally:
            self._ws = None
        return logged_in

    async def close(self) -> None:
        """Close the session."""
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()
