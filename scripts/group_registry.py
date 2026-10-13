"""Read the maintainer-owned list of public GitHub content repositories."""

import json
import re
from dataclasses import dataclass


SITE_DIRECTORIES = frozenset({"static", "scripts", "_site", "templates"})
RESERVED_SLUGS = SITE_DIRECTORIES | {"example", "index", "entries", "groups"}
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
REPOSITORY = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9_][A-Za-z0-9_.-]*")


@dataclass(frozen=True)
class Group:
    slug: str
    repository: str
    branch: str
    enabled: bool


def validate_branch(branch):
    if not isinstance(branch, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch):
        raise ValueError("branch: use a branch name such as main or draft/content")
    if ".." in branch or any(
        not part or part.startswith(".") or part.endswith((".", ".lock"))
        for part in branch.split("/")
    ):
        raise ValueError(f"branch: invalid branch name {branch!r}")
    return branch


def load_registry(path):
    registry = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(registry, dict) or set(registry) != {"groups"}:
        raise ValueError("registry: expected an object containing only groups")
    if not isinstance(registry["groups"], list):
        raise ValueError("registry: groups must be a list")
    groups = []
    slugs = set()
    repositories = set()
    for index, entry in enumerate(registry["groups"], start=1):
        label = f"registry group {index}"
        if not isinstance(entry, dict):
            raise ValueError(f"{label}: expected an object")
        if not {"slug", "repository", "enabled"} <= entry.keys() or entry.keys() - {
            "slug", "repository", "branch", "enabled"
        }:
            raise ValueError(f"{label}: require slug, repository, enabled; branch is optional")
        slug = entry["slug"]
        if not isinstance(slug, str) or len(slug) > 63 or not SLUG.fullmatch(slug):
            raise ValueError(f"{label}: slug must use lowercase letters, digits and single hyphens (max 63)")
        if slug in RESERVED_SLUGS or slug in slugs:
            raise ValueError(f"{label}: reserved or duplicate slug {slug!r}")
        repository = entry["repository"]
        if not isinstance(repository, str) or not REPOSITORY.fullmatch(repository) or repository.endswith(".git"):
            raise ValueError(f"{label}: repository must be owner/name, without a URL or .git suffix")
        if repository.casefold() in repositories:
            raise ValueError(f"{label}: duplicate repository {repository!r}")
        if type(entry["enabled"]) is not bool:
            raise ValueError(f"{label}: enabled must be true or false")
        branch = validate_branch(entry.get("branch", "main"))
        groups.append(Group(slug, repository, branch, entry["enabled"]))
        slugs.add(slug)
        repositories.add(repository.casefold())
    return groups
