from __future__ import annotations

import json
import os
import re
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


USERNAME = os.environ.get("PROFILE_USERNAME", "KangDohwa")
PROFILE_REPOSITORY_OWNER = os.environ.get(
    "PROFILE_REPOSITORY_OWNER", USERNAME
).casefold()
PROFILE_REPOSITORY = os.environ.get(
    "GITHUB_REPOSITORY", f"{USERNAME}/{USERNAME}"
).casefold()
README_PATH = Path(os.environ.get("README_PATH", "README.md"))
START_MARKER = "<!--START_SECTION:activity-->"
END_MARKER = "<!--END_SECTION:activity-->"
MAX_ITEMS = 5


def fetch_public_events() -> list[dict[str, Any]]:
    request = urllib.request.Request(
        f"https://api.github.com/users/{USERNAME}/events/public?per_page=50",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"{USERNAME}-profile-readme",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )

    token = os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def format_date(value: str) -> str:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d")


def format_event(event: dict[str, Any]) -> str | None:
    event_type = event.get("type")
    payload = event.get("payload", {})
    repository = event.get("repo", {}).get("name")
    created_at = event.get("created_at")

    if not repository or not created_at:
        return None

    repository_link = f"[`{repository}`](https://github.com/{repository})"
    date = format_date(created_at)

    if event_type == "PushEvent":
        branch = payload.get("ref", "").removeprefix("refs/heads/") or "a branch"
        head = payload.get("head")
        commit_link = (
            f" ([latest commit](https://github.com/{repository}/commit/{head}))"
            if head and set(head) != {"0"}
            else ""
        )
        return (
            f"- `{date}` Pushed to `{branch}` in {repository_link}{commit_link}"
        )

    if event_type == "PullRequestEvent":
        pull_request = payload.get("pull_request", {})
        number = pull_request.get("number")
        url = pull_request.get("html_url")
        action = payload.get("action", "updated").capitalize()
        if number and url:
            return f"- `{date}` {action} [PR #{number}]({url}) in {repository_link}"

    if event_type == "IssuesEvent":
        issue = payload.get("issue", {})
        number = issue.get("number")
        url = issue.get("html_url")
        action = payload.get("action", "updated").capitalize()
        if number and url:
            return f"- `{date}` {action} [issue #{number}]({url}) in {repository_link}"

    if event_type == "ReleaseEvent":
        release = payload.get("release", {})
        name = release.get("name") or release.get("tag_name")
        url = release.get("html_url")
        if name and url:
            return f"- `{date}` Released [{name}]({url}) from {repository_link}"

    if event_type == "CreateEvent" and payload.get("ref_type") == "repository":
        return f"- `{date}` Created {repository_link}"

    if event_type == "ForkEvent":
        forkee = payload.get("forkee", {})
        name = forkee.get("full_name")
        url = forkee.get("html_url")
        if name and url:
            return f"- `{date}` Forked {repository_link} to [`{name}`]({url})"

    return None


def build_activity(events: list[dict[str, Any]]) -> str:
    items: list[str] = []
    seen: set[tuple[str, str]] = set()

    for event in events:
        repository = event.get("repo", {}).get("name", "")
        owner = repository.partition("/")[0].casefold()
        if (
            owner != PROFILE_REPOSITORY_OWNER
            or repository.casefold() == PROFILE_REPOSITORY
        ):
            continue

        key = (
            event.get("type", ""),
            repository,
        )
        if key in seen:
            continue

        item = format_event(event)
        if not item:
            continue
        seen.add(key)
        items.append(item)
        if len(items) == MAX_ITEMS:
            break

    if not items:
        return "_표시할 최근 공개 활동이 없습니다._"

    return "\n".join(items)


def update_readme(activity: str) -> bool:
    original = README_PATH.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"{re.escape(START_MARKER)}.*?{re.escape(END_MARKER)}",
        re.DOTALL,
    )
    replacement = f"{START_MARKER}\n{activity}\n{END_MARKER}"
    updated, count = pattern.subn(replacement, original, count=1)

    if count != 1:
        raise RuntimeError("README activity markers are missing or duplicated")

    if updated == original:
        return False

    README_PATH.write_text(updated, encoding="utf-8", newline="\n")
    return True


def main() -> None:
    events = fetch_public_events()
    changed = update_readme(build_activity(events))
    print("README updated." if changed else "README is already up to date.")


if __name__ == "__main__":
    main()
