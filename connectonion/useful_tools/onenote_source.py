"""Read-only OneNote connection using the existing Microsoft credential flow."""

from urllib.parse import quote, urlsplit
import httpx
from ..provider_credentials import ProviderCredentialError
from .onenote import TIMEOUT
from .outlook import Outlook

MAX_PAGES = 1000
MAX_SELECTED = 1000
READ_SCOPES = {"Notes.Read", "Notes.Read.All", "Notes.ReadWrite", "Notes.ReadWrite.All"}

def _id(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ValueError("A nonempty OneNote ID is required. List IDs with co onenote source.")
    return quote(value, safe="")


def _named(rows: list[dict], value: str, kind: str) -> dict:
    exact = [row for row in rows if row.get("id") == value]
    matches = exact or [row for row in rows if row.get("displayName") == value]
    if len(matches) != 1:
        raise ValueError(f"Unknown, inaccessible or ambiguous {kind}. "
                         "Use an ID from co onenote source notebooks/sections.")
    return matches[0]


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

    def _items(self, endpoint: str, *, limit: int | None = None, params=None) -> tuple[list[dict], bool]:
        """Follow the exact collection's Graph nextLink, with loop and page guards."""
        rows, seen = [], set()
        collection = urlsplit(endpoint).path
        for _ in range(MAX_PAGES):
            if endpoint in seen:
                raise ValueError("OneNote pagination repeated a link; no complete result is available.")
            seen.add(endpoint)
            result = self._json(endpoint, **({"params": params} if params is not None else {}))
            params = None
            batch = result.get("value")
            if not isinstance(batch, list) or any(not isinstance(row, dict) or not row.get("id") for row in batch):
                raise ValueError("OneNote returned malformed pagination data. Retry the listing.")
            link = result.get("@odata.nextLink")
            rows.extend(batch)
            if limit is not None and len(rows) >= limit:
                return rows[:limit], bool(link or len(rows) > limit)
            if not link:
                return rows, False
            parsed = urlsplit(link) if isinstance(link, str) else None
            if (parsed is None or parsed.scheme != "https" or parsed.netloc != "graph.microsoft.com"
                    or parsed.fragment or parsed.path != "/v1.0" + collection):
                raise ValueError("OneNote pagination points outside the selected Graph collection; refused.")
            endpoint = parsed.path.removeprefix("/v1.0") + ("?" + parsed.query if parsed.query else "")
        raise ValueError("OneNote pagination exceeded its safety bound; no complete result is available.")

    def notebooks(self) -> list[dict]:
        """All accessible notebooks, including later result pages."""
        rows, _ = self._items("/me/onenote/notebooks", params={"$select": "id,displayName", "$top": 100})
        return [{"id": row["id"], "displayName": row.get("displayName") or "(untitled)"} for row in rows]

    def sections(self, notebook: str) -> list[dict]:
        """All sections within the chosen notebook, including nested section groups."""
        selected = _named(self.notebooks(), notebook, "notebook")
        pending = [f"/me/onenote/notebooks/{_id(selected['id'])}"]
        seen, rows = set(), []
        while pending:
            parent = pending.pop()
            if parent in seen or len(seen) >= MAX_PAGES:
                raise ValueError("OneNote section group pagination repeated or exceeded its safety bound.")
            seen.add(parent)
            sections, _ = self._items(parent + "/sections", params={"$select": "id,displayName", "$top": 100})
            rows.extend(sections)
            groups, _ = self._items(parent + "/sectionGroups", params={"$select": "id,displayName", "$top": 100})
            pending.extend(f"/me/onenote/sectionGroups/{_id(group['id'])}" for group in groups)
        return [{"id": row["id"], "displayName": row.get("displayName") or "(untitled)"} for row in rows]

    def scope(self, notebook: str, section: str) -> dict:
        selected = _named(self.notebooks(), notebook, "notebook")
        chosen = _named(self.sections(selected["id"]), section, "section")
        return {"notebook": selected["id"], "section": chosen["id"]}

    def pages(self, notebook: str, section: str, *, limit: int = 100) -> dict:
        """Bounded listing; explicitly report whether later pages remain."""
        if not isinstance(limit, int) or not 1 <= limit <= MAX_SELECTED:
            raise ValueError(f"Page limit must be between 1 and {MAX_SELECTED}.")
        scope = self.scope(notebook, section)
        rows, truncated = self._items(f"/me/onenote/sections/{_id(scope['section'])}/pages", limit=limit,
                                     params={"$top": min(limit, 100), "$select": "id,title,lastModifiedDateTime",
                                             "$orderby": "lastModifiedDateTime desc"})
        return {"pages": rows, "truncated": truncated, "limit": limit, "scope": scope}

    def selected_page(self, page_id: str, scope: dict) -> dict:
        """Read metadata only, rejecting pages outside the explicitly chosen scope."""
        page = self._json(f"/me/onenote/pages/{_id(page_id)}", params={"$expand": "parentNotebook,parentSection"})
        if (page.get("id") != page_id or (page.get("parentNotebook") or {}).get("id") != scope["notebook"]
                or (page.get("parentSection") or {}).get("id") != scope["section"]):
            raise ValueError("Page is outside the selected notebook/section or has malformed scope metadata.")
        return page
