from __future__ import annotations

import json
import os
import re
import urllib.request
from datetime import datetime, timezone
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
EVENTS_PER_PAGE = 100
MAX_EVENT_PAGES = 3


def fetch_public_events() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    token = os.environ.get("GITHUB_TOKEN")
    for page in range(1, MAX_EVENT_PAGES + 1):
        request = urllib.request.Request(
            f"https://api.github.com/users/{USERNAME}/events/public"
            f"?per_page={EVENTS_PER_PAGE}&page={page}",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"{USERNAME}-profile-readme",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        if token:
            request.add_header("Authorization", f"Bearer {token}")

        with urllib.request.urlopen(request, timeout=30) as response:
            batch = json.load(response)
        if not isinstance(batch, list):
            raise RuntimeError("GitHub public events response is invalid")
        events.extend(event for event in batch if isinstance(event, dict))
        if (
            len(batch) < EVENTS_PER_PAGE
            or len(build_activity_items(events)) >= MAX_ITEMS
        ):
            break
    return events


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

    if event_type == "PublicEvent":
        return f"- `{date}` Made {repository_link} public"

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
        if payload.get("action") == "closed" and pull_request.get("merged"):
            action = "Merged"
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


def build_activity_items(events: list[dict[str, Any]]) -> list[str]:
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

    return items


def build_activity(events: list[dict[str, Any]]) -> str:
    items = build_activity_items(events)
    return "\n".join(items) if items else "_No recent public activity to display._"


def replace_section(
    content: str,
    start_marker: str,
    end_marker: str,
    body: str,
    section_name: str,
) -> str:
    pattern = re.compile(
        rf"{re.escape(start_marker)}.*?{re.escape(end_marker)}",
        re.DOTALL,
    )
    replacement = f"{start_marker}\n{body}\n{end_marker}"
    updated, count = pattern.subn(replacement, content)

    if count != 1:
        raise RuntimeError(
            f"README {section_name} markers are missing or duplicated"
        )

    return updated


def update_readme(activity: str) -> bool:
    original = README_PATH.read_text(encoding="utf-8")
    updated = replace_section(
        original,
        START_MARKER,
        END_MARKER,
        activity,
        "activity",
    )

    if updated == original:
        return False

    README_PATH.write_text(updated, encoding="utf-8", newline="\n")
    return True


def main() -> None:
    events = fetch_public_events()
    updated_date = datetime.now(timezone.utc).date().isoformat()
    activity = (
        f"{build_activity(events)}\n\n"
        f"<sub>Updated: {updated_date} UTC · Public activity in personal repositories.</sub>"
    )
    changed = update_readme(activity)
    print("README updated." if changed else "README is already up to date.")


if __name__ == "__main__":
    main()
