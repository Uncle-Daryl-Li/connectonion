"""Read-only OneNote connection using the existing Microsoft credential flow."""

from urllib.parse import quote
import httpx
from ..provider_credentials import ProviderCredentialError
from .onenote import TIMEOUT
from .outlook import Outlook

READ_SCOPES = {"Notes.Read", "Notes.Read.All", "Notes.ReadWrite", "Notes.ReadWrite.All"}

def _id(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ValueError("A nonempty OneNote ID is required. List IDs with co onenote source.")
    return quote(value, safe="")


class OneNoteSource:
    """Read-only connection; accepts existing compatible Microsoft grants."""

    def __init__(self):
        # Do not instantiate Outlook's mail tools or require Mail scopes.
        self._graph = Outlook.__new__(Outlook)
        credentials = self._graph._credentials
        credentials.require_configured()
        if not credentials.scopes & READ_SCOPES:
            raise ProviderCredentialError(
                "missing_scope", "OneNote requires Notes.Read. Reconnect using the client's Microsoft flow. "
                "If consent still omits it, wait for client support (#1886); no separate Azure app is needed.",
                credentials.auth_command)
        self._graph._access_token = None
        self._account = None

    def _json(self, endpoint: str, **kwargs) -> dict:
        try:
            result = self._graph._request("GET", endpoint, timeout=TIMEOUT, **kwargs)
        except httpx.RequestError:
            raise ProviderCredentialError("unavailable", "Could not reach Microsoft OneNote. Retry the source command.",
                                          "co onenote source verify") from None
        except (TypeError, ValueError) as error:
            if isinstance(error, ProviderCredentialError):
                raise
            raise ValueError("OneNote returned malformed metadata. Retry the source command.") from None
        if not isinstance(result, dict):
            raise ValueError("OneNote returned malformed metadata. Retry the source command.")
        return result

    def verify_connection(self) -> dict:
        """Verify the selected Microsoft account and OneNote access without exposing tokens."""
        if self._account is None:
            # /me requires User.Read, already granted by co auth microsoft.
            profile = self._json("/me", params={"$select": "id"})
            account = f"{Outlook.GRAPH_API_URL}/users/{_id(profile.get('id'))}"
            self._json("/me/onenote/notebooks", params={"$top": 1, "$select": "id"})
            self._account = account
        return {"account": self._account, "read_only": True}
