"""Bounded, read-only GitHub API access for public group content."""

import json
import os
import re
import time
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import quote
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
        self.metrics = {"rest_requests": 0, "raw_requests": 0, "rest_bytes": 0, "raw_bytes": 0}
        self.api_remaining = None
        self.api_budget = None
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
        return self.request("https://api.github.com" + path, headers, limit, missing, "rest")

    def raw_file(self, repository, commit, path, limit):
        if (not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
                or not re.fullmatch(r"[0-9a-f]{40}", commit)
                or not all(re.fullmatch(r"[a-z0-9][a-z0-9._-]*", part) for part in path.split("/"))):
            raise ValueError("Expected a validated repository, immutable commit and content path")
        url = "https://raw.githubusercontent.com/" + repository + "/" + commit + "/" + quote(path, safe="/")
        return self.request(url, {"User-Agent": self.headers["User-Agent"]}, limit, "missing_blob", "raw")

    def request(self, url, headers, limit, missing, transport):
        request = Request(url, headers=headers)
        for attempt in range(3):
            self.remaining()
            if transport == "rest" and self.api_remaining is not None and self.api_remaining <= 200:
                raise ImportFailure("rate_limited", "Preserving GitHub API headroom; retry after quota resets")
            if transport == "rest" and self.api_budget is not None:
                if (self.api_budget["requests"] >= self.api_budget["maximum"]
                        or self.api_budget["remaining"] is not None and self.api_budget["remaining"] <= 200):
                    raise ImportFailure("rate_limited", "Source metadata budget exhausted; preserving publishing API headroom")
                self.api_budget["requests"] += 1
            self.metrics[transport + "_requests"] += 1
            try:
                with self.opener.open(request, timeout=min(15, self.remaining())) as response:
                    if transport == "rest":
                        remaining = response.headers.get("X-RateLimit-Remaining")
                        if remaining and remaining.isdigit():
                            self.api_remaining = int(remaining)
                            if self.api_budget is not None:
                                self.api_budget["remaining"] = self.api_remaining
                    length = response.headers.get("Content-Length")
                    if length and (not length.isdigit() or int(length) > limit):
                        raise ImportFailure("response_too_large", "GitHub response exceeds the allowed byte limit")
                    payload = bytearray()
                    while len(payload) <= limit:
                        self.remaining()
                        chunk = response.read1(min(65536, limit + 1 - len(payload)))
                        if not chunk:
                            return bytes(payload)
                        self.metrics[transport + "_bytes"] += len(chunk)
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

    def write(self, path, body, *, method):
        """Bounded API mutation, attempted once because a lost reply may mean success."""
        if (method not in {'POST', 'PATCH'} or not path.startswith('/repos/')
                or any(c in path for c in ('\\', '\r', '\n'))):
            raise ValueError('Expected a fixed GitHub repository write path')
        self.remaining()
        if self.api_remaining is not None and self.api_remaining <= 200:
            raise ImportFailure('rate_limited', 'Preserving API headroom for publication')
        if self.api_budget is not None:
            if (self.api_budget['requests'] >= self.api_budget['maximum']
                    or self.api_budget['remaining'] is not None and self.api_budget['remaining'] <= 200):
                raise ImportFailure('rate_limited', 'API write budget exhausted')
            self.api_budget['requests'] += 1
        data = json.dumps(body).encode()
        if len(data) > 2 * 1024 * 1024:
            raise ValueError('API write body exceeds byte limit')
        request = Request('https://api.github.com' + path, data=data, method=method,
            headers={**self.headers, 'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json'})
        self.metrics['rest_requests'] += 1
        try:
            with self.opener.open(request, timeout=min(15, self.remaining())) as response:
                remaining = response.headers.get('X-RateLimit-Remaining')
                if remaining and remaining.isdigit():
                    self.api_remaining = int(remaining)
                    if self.api_budget is not None:
                        self.api_budget['remaining'] = self.api_remaining
                payload = bytearray()
                while len(payload) <= 2 * 1024 * 1024:
                    self.remaining()
                    chunk = response.read1(min(65536, 2 * 1024 * 1024 + 1 - len(payload)))
                    if not chunk:
                        return json.loads(payload)
                    payload.extend(chunk)
                    self.metrics['rest_bytes'] += len(chunk)
                raise ImportFailure('response_too_large', 'API write response exceeds byte limit')
        except HTTPError as error:
            limited = error.code == 429 or error.code == 403 and (
                error.headers.get('X-RateLimit-Remaining') == '0' or error.headers.get('Retry-After'))
            if error.fp is not None:
                error.close()
            raise ImportFailure('rate_limited' if limited else 'remote_error',
                                'API write failed; reconcile its outcome before retrying') from error
        except (URLError, TimeoutError, OSError, HTTPException) as error:
            raise ImportFailure('network_error', 'API write reply is uncertain; reconcile before retrying') from error
