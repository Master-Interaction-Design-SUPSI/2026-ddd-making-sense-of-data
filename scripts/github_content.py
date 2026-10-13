"""Bounded, read-only GitHub API access for public group content."""

import json
import os
import time
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ImportFailure(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


class GitHubClient:
    def __init__(self):
        self.opener = build_opener(NoRedirect())
        self.deadline = time.monotonic() + 180
        self.headers = {
            "User-Agent": "ddd-course-content-importer",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def remaining(self):
        seconds = self.deadline - time.monotonic()
        if seconds <= 0:
            raise ImportFailure("timeout", "Group import exceeded its 180-second time budget; retry later")
        return seconds

    def get(self, path, limit, *, raw=False, missing="unavailable_repository"):
        if not path.startswith("/repos/") or any(c in path for c in ("\\", "\r", "\n")):
            raise ValueError("Expected a GitHub repository API path")
        headers = {**self.headers, "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json"}
        request = Request("https://api.github.com" + path, headers=headers)
        for attempt in range(3):
            try:
                with self.opener.open(request, timeout=min(15, self.remaining())) as response:
                    length = response.headers.get("Content-Length")
                    if length and (not length.isdigit() or int(length) > limit):
                        raise ImportFailure("response_too_large", "GitHub response exceeds the allowed byte limit")
                    payload = bytearray()
                    while len(payload) <= limit:
                        self.remaining()
                        chunk = response.read1(min(65536, limit + 1 - len(payload)))
                        if not chunk:
                            return bytes(payload)
                        payload.extend(chunk)
                    raise ImportFailure("response_too_large", "GitHub response exceeds the allowed byte limit")
            except HTTPError as error:
                status = error.code
                rate_limited = status == 429 or (status == 403 and (
                    error.headers.get("X-RateLimit-Remaining") == "0" or error.headers.get("Retry-After")
                ))
                if error.fp is not None:
                    error.close()
                if rate_limited:
                    raise ImportFailure("rate_limited", "GitHub rate limit reached; retry after the limit resets") from error
                if status in (404, 409):
                    detail = "Branch is missing or the repository is empty; check branch in groups.json" if missing == "missing_branch" else "Repository/object is unavailable; check the public repository address"
                    raise ImportFailure(missing, detail) from error
                if 300 <= status < 400:
                    raise ImportFailure("redirect", "Repository redirected; update its address in groups.json") from error
                if status not in (500, 502, 503, 504):
                    raise ImportFailure("access_denied", f"GitHub returned HTTP {status}; check repository access and token permissions") from error
                failure = ImportFailure("remote_error", f"GitHub returned HTTP {status} after bounded retries")
            except (URLError, TimeoutError, OSError, HTTPException):
                failure = ImportFailure("network_error", "GitHub request failed or timed out; check connectivity and retry")
            if attempt == 2:
                raise failure
            time.sleep(min(attempt + 1, self.remaining()))

    def json(self, path, *, missing="unavailable_repository"):
        try:
            value = json.loads(self.get(path, 2 * 1024 * 1024, missing=missing))
        except (ValueError, UnicodeError, RecursionError) as error:
            raise ImportFailure("invalid_response", "GitHub returned invalid JSON") from error
        if not isinstance(value, dict):
            raise ImportFailure("invalid_response", "GitHub returned an unexpected response")
        return value
