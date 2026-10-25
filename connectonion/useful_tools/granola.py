"""
Purpose: Read-only, explicitly scoped Granola meeting-notes source for co rem.
LLM-Note:
  Dependencies: imports from [os, re, time, httpx, importlib.metadata, datetime, yaml, pathlib] | imported by [cli/commands/granola_commands.py] | tested by [tests/unit/test_granola.py, tests/unit/test_granola_adapter.py]
  Data flow: GranolaClient methods → Bearer-token httpx.Client GET requests to BASE_URL → raw Granola API v1 JSON → returned as plain dicts/lists to the CLI/import layer; write_source_yaml() creates source.yaml under source_dir
  State/Effects: GRANOLA_API_KEY is read ONLY from os.environ (user-managed personal key; never persisted, never logged) | no writes to Granola (read-only) | write_source_yaml() creates source.yaml under source_dir
  Errors: GranolaConfigError (missing/invalid key or profile) | GranolaAuthError (HTTP 401) | GranolaPermissionError (HTTP 403) | GranolaIncompleteRead (5xx retries exhausted, carries partial raw pages)

Granola public-API client: verify key, list folders, list notes, get note.

Usage (Phase 2+):
    from connectonion.useful_tools.granola import GranolaClient, validate_profile
    client = GranolaClient()
    folders = client.list_folders()
    notes   = client.list_notes(folder_id="fld-abc", limit=50)
"""

import os
import re
import time

# ---------------------------------------------------------------------------
# Module-level constants — frozen here as part of the Phase-1 contract.
# ---------------------------------------------------------------------------

BASE_URL = "https://public-api.granola.ai/v1"
TIMEOUT = 30.0

#: Maximum number of cursor-advance pages before we stop and raise GranolaIncompleteRead.
MAX_PAGES = 1_000
#: Maximum total notes a single list_notes() call may return.
MAX_SELECTED = 1_000

#: Granola page_size upper bound (official maximum is 30).
_MAX_PAGE_SIZE = 30

#: HTTP statuses that warrant a limited automatic retry.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
#: Maximum number of retries for a single transient failure before giving up.
_MAX_RETRIES = 3

# Profile: 1–64 chars, ASCII alphanumeric + hyphen, must start and end with alphanumeric.
# Colons are excluded to prevent breakage of the composite record ID "granola:<profile>:<note_id>".
PROFILE_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?$")

# ---------------------------------------------------------------------------
# Exceptions — all are ValueError subclasses so CLI can catch them uniformly.
# ---------------------------------------------------------------------------


class GranolaConfigError(ValueError):
    """Local configuration missing or invalid (GRANOLA_API_KEY / profile)."""


class GranolaAuthError(ValueError):
    """Granola returned HTTP 401 — API key is missing, expired, or invalid."""


class GranolaPermissionError(ValueError):
    """Granola returned HTTP 403 — this key cannot access the requested resource."""


class GranolaIncompleteRead(Exception):
    """Retry budget exhausted before all requested notes could be fetched.

    Carries the raw note batches successfully received before the failure
    and a safe, non-secret failure reason.  The import layer maps
    partial_pages to records and persists them; the CLI then marks the
    result complete:false and exits 1.

    Attributes:
        reason:        human-readable safe failure description (no credentials).
        partial_pages: list of raw note-batch lists, one entry per page fetched.
    """

    def __init__(self, reason: str, partial_pages: list):
        super().__init__(reason)
        self.reason = reason
        self.partial_pages: list[list[dict]] = partial_pages


# ---------------------------------------------------------------------------
# Profile validation (Phase-1 contract; used by CLI before any network call).
# ---------------------------------------------------------------------------


def validate_profile(profile: str) -> None:
    """Raise GranolaConfigError if *profile* is not a valid non-secret user label.

    Profile rules (frozen in Phase 1):
    - 1–64 characters
    - ASCII letters, digits, and hyphens only
    - Must start and end with a letter or digit (never a hyphen)
    - Must not contain a colon (colons break the compound record ID)

    The profile is a non-secret convenience label chosen by the user
    (e.g. ``work``, ``home-2024``).  It is not the API key or an email.
    """
    if not isinstance(profile, str) or not PROFILE_RE.fullmatch(profile):
        raise GranolaConfigError(
            f"Invalid profile {profile!r}: must be 1–64 ASCII letters/digits/hyphens, "
            "starting and ending with a letter or digit (e.g. 'work', 'home-2024').\n"
            "Repair: choose a profile that matches [A-Za-z0-9][A-Za-z0-9-]{0,62}[A-Za-z0-9]?$"
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class _TransientError(Exception):
    """Raised internally by _request_once() for 429/5xx — triggers the retry loop."""

    def __init__(self, status: int, retry_after: str | None = None):
        super().__init__(f"Transient HTTP {status}")
        self.status = status
        self.retry_after = retry_after


def _retry_delay(retry_after_header: str | None, attempt: int) -> float:
    """Return seconds to wait before the next retry attempt.

    Respects Retry-After header when present; otherwise uses bounded exponential back-off.
    """
    if retry_after_header:
        try:
            return max(0.0, float(retry_after_header))
        except ValueError:
            pass
    return min(float(2 ** attempt), 30.0)


# ---------------------------------------------------------------------------
# GranolaClient — Phase-2 implementation.
# ---------------------------------------------------------------------------


class GranolaClient:
    """Read-only Granola API client: verify key, list folders, list notes, get note.

    Reads GRANOLA_API_KEY from os.environ only.  The key is transmitted via
    Authorization: Bearer header and is never logged, stored, or returned.

    Methods raise GranolaConfigError / GranolaAuthError / GranolaPermissionError
    on non-transient failures; transient 429/5xx failures are retried up to
    _MAX_RETRIES times before raising GranolaIncompleteRead.
    """

    def __init__(self, *, _base_url: str = BASE_URL, _http=None) -> None:
        """Read GRANOLA_API_KEY from the environment; raise GranolaConfigError if absent.

        Args:
            _base_url: Override the API base URL (tests; internal use only).
            _http:     Inject a pre-built httpx.Client (tests; internal use only).
                       When None, a new client is created with the Bearer key from env.
        """
        import httpx

        key = os.environ.get("GRANOLA_API_KEY", "")
        if not key:
            raise GranolaConfigError(
                "GRANOLA_API_KEY environment variable is missing or empty.\n"
                "Set it to your personal Granola API key before using GranolaClient."
            )
        self._base_url = _base_url.rstrip("/")
        if _http is not None:
            self._http = _http
        else:
            self._http = httpx.Client(
                headers={"Authorization": f"Bearer {key}"},
                timeout=TIMEOUT,
            )

    # ------------------------------------------------------------------
    # Private request helpers
    # ------------------------------------------------------------------

    def _request_once(self, path: str, *, params: dict | None = None) -> dict:
        """Single GET attempt; raises typed errors, never returns None.

        Raises:
            GranolaAuthError:      on HTTP 401.
            GranolaPermissionError: on HTTP 403.
            ValueError:            on HTTP 404 or non-JSON body.
            GranolaConfigError:    on network / timeout error or unexpected status.
            _TransientError:       on HTTP 429 / 5xx (caught by _request).
        """
        import httpx

        url = self._base_url + path
        try:
            response = self._http.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise GranolaConfigError(
                f"Request to Granola API timed out after {TIMEOUT}s."
            ) from exc
        except httpx.RequestError as exc:
            raise GranolaConfigError(
                "Network error while reaching Granola API."
            ) from exc

        status = response.status_code
        if status == 401:
            raise GranolaAuthError(
                "Granola API returned 401 — API key is missing, expired, or invalid."
            )
        if status == 403:
            raise GranolaPermissionError(
                "Granola API returned 403 — this key cannot access the requested resource."
            )
        if status == 404:
            raise ValueError(
                "Granola API returned 404 — note not found, deleted, or not yet processed."
            )
        if status in _RETRY_STATUSES:
            raise _TransientError(status, retry_after=response.headers.get("Retry-After"))
        if status >= 400:
            raise GranolaConfigError(f"Granola API returned unexpected HTTP {status}.")

        try:
            return response.json()
        except Exception as exc:
            raise ValueError(
                f"Granola API returned non-JSON response (HTTP {status})."
            ) from exc

    def _request(
        self, path: str, *, params: dict | None = None, partial_pages: list | None = None
    ) -> dict:
        """GET with automatic retry on transient failures.

        Calls _request_once up to (_MAX_RETRIES + 1) times.  Raises
        GranolaIncompleteRead (with partial_pages accumulated so far) after all
        retry attempts are exhausted on consecutive transient failures.
        """
        last_exc: _TransientError | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return self._request_once(path, params=params)
            except _TransientError as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    time.sleep(_retry_delay(exc.retry_after, attempt))

        raise GranolaIncompleteRead(
            reason=(
                f"Granola API returned transient HTTP {last_exc.status} "  # type: ignore[union-attr]
                f"on {_MAX_RETRIES + 1} consecutive attempts."
            ),
            partial_pages=partial_pages or [],
        )

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    def verify(self) -> dict:
        """Probe the API with a minimal single-note request.

        Returns {"verified_read": True, "note": "..."} on success.
        Only proves the key can perform the smallest read; does not prove
        full folder/space access or account identity.
        Never exposes the key in the return value or logs.
        """
        data = self._request("/notes", params={"page_size": 1})
        notes = data.get("notes") or []
        note_summary = notes[0].get("title", "(untitled)") if notes else "(no notes yet)"
        return {"verified_read": True, "note": note_summary}

    def list_folders(self) -> list[dict]:
        """Return all personal folders accessible with this key.

        Paginates automatically until hasMore is False or MAX_PAGES is reached.
        An empty list is a valid response (no folders created yet).
        HTTP 403 is GranolaPermissionError, not an inference about key type.

        Raises:
            GranolaIncompleteRead: if retries exhaust or pagination guard trips;
                                   carries partial_pages of folder batches received.
        """
        all_folders: list[dict] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        pages_fetched = 0
        partial_pages: list[list[dict]] = []

        while True:
            params: dict = {"page_size": _MAX_PAGE_SIZE}
            if cursor:
                params["cursor"] = cursor

            data = self._request("/folders", params=params, partial_pages=partial_pages)
            batch: list[dict] = data.get("folders") or []
            partial_pages.append(batch)
            all_folders.extend(batch)
            pages_fetched += 1

            has_more = data.get("hasMore", False)
            if not has_more:
                break

            next_cursor = data.get("cursor")
            if not next_cursor:
                raise GranolaIncompleteRead(
                    reason="Folders API returned hasMore=true but cursor is absent or empty.",
                    partial_pages=partial_pages,
                )
            if next_cursor in seen_cursors:
                raise GranolaIncompleteRead(
                    reason="Folders API repeated a cursor — pagination loop detected.",
                    partial_pages=partial_pages,
                )
            seen_cursors.add(next_cursor)
            cursor = next_cursor

            if pages_fetched >= MAX_PAGES:
                raise GranolaIncompleteRead(
                    reason=f"Folders pagination exceeded MAX_PAGES={MAX_PAGES} without completing.",
                    partial_pages=partial_pages,
                )

        return all_folders

    def list_notes(
        self,
        *,
        folder_id: str | None = None,
        limit: int | None = None,
        created_after: str | None = None,
        updated_after: str | None = None,
        created_before: str | None = None,
    ) -> list[dict]:
        """Fetch notes from the specified personal folder.

        Args:
            folder_id:      Personal folder ID (required for MVP; use list_folders()).
            limit:          Maximum total notes to return, 1–MAX_SELECTED.
                            The tool layer maintains `remaining = limit - fetched`
                            and uses page_size = min(_MAX_PAGE_SIZE, remaining) per request.
                            If None, defaults to MAX_SELECTED (hard cap on total notes returned).
            created_after:  ISO-8601 timestamp; return only notes created after this time.
            updated_after:  ISO-8601 timestamp; return only notes updated after this time.
            created_before: ISO-8601 timestamp; return only notes created before this time.

        Raises:
            GranolaConfigError:    if limit is out of range or folder_id is missing.
            GranolaAuthError:      on HTTP 401.
            GranolaPermissionError: on HTTP 403.
            GranolaIncompleteRead: if retries exhaust before all pages are fetched;
                                   carries partial_pages and a safe failure reason.

        Returns raw note dicts as returned by the API; adapter maps them to records.
        """
        if folder_id is None:
            raise GranolaConfigError(
                "folder_id is required for list_notes() in MVP scope.\n"
                "Use list_folders() to discover available folder IDs."
            )
        if limit is not None and not (1 <= limit <= MAX_SELECTED):
            raise GranolaConfigError(
                f"limit must be between 1 and {MAX_SELECTED}; got {limit!r}."
            )

        effective_limit = limit if limit is not None else MAX_SELECTED
        all_notes: list[dict] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        pages_fetched = 0
        partial_pages: list[list[dict]] = []

        while True:
            remaining = effective_limit - len(all_notes)
            page_size = min(_MAX_PAGE_SIZE, remaining)

            params: dict = {"folder_id": folder_id, "page_size": page_size}
            if cursor:
                params["cursor"] = cursor
            if created_after:
                params["created_after"] = created_after
            if updated_after:
                params["updated_after"] = updated_after
            if created_before:
                params["created_before"] = created_before

            data = self._request("/notes", params=params, partial_pages=partial_pages)
            batch: list[dict] = data.get("notes") or []
            partial_pages.append(batch)
            all_notes.extend(batch)
            pages_fetched += 1

            has_more = data.get("hasMore", False)
            if not has_more:
                break

            next_cursor = data.get("cursor")
            if not next_cursor:
                raise GranolaIncompleteRead(
                    reason="API returned hasMore=true but cursor is absent or empty.",
                    partial_pages=partial_pages,
                )
            if next_cursor in seen_cursors:
                raise GranolaIncompleteRead(
                    reason="API repeated a cursor — pagination loop detected.",
                    partial_pages=partial_pages,
                )
            seen_cursors.add(next_cursor)
            cursor = next_cursor

            if len(all_notes) >= effective_limit:
                break
            if pages_fetched >= MAX_PAGES:
                raise GranolaIncompleteRead(
                    reason=f"Pagination exceeded MAX_PAGES={MAX_PAGES} without completing.",
                    partial_pages=partial_pages,
                )

        return all_notes[:effective_limit]

    def get_note(self, note_id: str) -> dict:
        """Fetch a single note by ID.

        Raises:
            GranolaAuthError:       on HTTP 401.
            GranolaPermissionError: on HTTP 403.
            ValueError:             on HTTP 404 (deleted, inaccessible, or still processing).
        """
        return self._request(f"/notes/{note_id}")

    def hydrate_notes(self, notes: list[dict]) -> list[dict]:
        """Fetch raw detail objects for lightweight list-note results."""
        details: list[dict] = []
        for note in notes:
            note_id = str(note.get("id") or "") if isinstance(note, dict) else ""
            if not note_id:
                raise GranolaConfigError("Granola list response included a note without an id.")
            try:
                details.append(self.get_note(note_id))
            except GranolaIncompleteRead as exc:
                raise GranolaIncompleteRead(
                    reason=f"Could not fetch detail for note {note_id}: {exc.reason}",
                    partial_pages=[details],
                ) from exc
        return details

    def write_source_yaml(
        self,
        source_dir,
        folder_id: str,
        profile: str,
        *,
        limit: int | None = None,
        complete: bool = True,
        note_id: str | None = None,
    ) -> None:
        """Write source_dir/source.yaml with scope, account, retention, and validated_with.

        Args:
            source_dir:  Directory under ~/.co/rem/docs/granola (or --output).
            folder_id:   The personal folder ID selected for import.
            profile:     Non-secret user label used as the record account component.
            limit:       Effective note limit used; None means MAX_SELECTED was used.
                         For precise imports (note_id given), this is always 1.
            complete:    False when the read was interrupted (GranolaIncompleteRead);
                         recorded in the file so downstream tools know the data is partial.
            note_id:     When set, records this note ID in the scope for traceability.
                         Precise imports always set limit=1 alongside note_id.

        Token and API key never appear in the written file.
        Raises ValueError if profile is invalid (via validate_profile).
        """
        import importlib.metadata
        import datetime
        import yaml

        validate_profile(profile)

        from pathlib import Path as _Path
        source_dir = _Path(source_dir)
        source_dir.mkdir(parents=True, exist_ok=True)
        version = importlib.metadata.version("connectonion")
        scope: dict = {
            "folder_id": folder_id,
            "limit": limit if limit is not None else MAX_SELECTED,
        }
        if note_id is not None:
            scope["note_id"] = note_id
        doc = {
            "driver": "granola",
            "account": profile,
            "scope": scope,
            "retention": "90d",
            "media_policy": "metadata_only",
            "complete": complete,
            "validated_with": {
                "connectonion_version": version,
                "command": "co rem sources",
                "path": str(source_dir / "source.yaml"),
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            },
        }
        target = source_dir / "source.yaml"
        with open(target, "w", encoding="utf-8") as fh:
            yaml.safe_dump(doc, fh, allow_unicode=True, sort_keys=False)
