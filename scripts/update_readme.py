from __future__ import annotations

from base64 import b64encode
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
WAKATIME_START_MARKER = "<!--START_SECTION:wakatime-->"
WAKATIME_END_MARKER = "<!--END_SECTION:wakatime-->"
MAX_ITEMS = 5
MAX_LANGUAGES = 5
GRAPH_WIDTH = 20


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


def fetch_wakatime_stats() -> dict[str, Any] | None:
    api_key = os.environ.get("WAKATIME_API_KEY")
    if not api_key:
        return None

    encoded_key = b64encode(api_key.encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        "https://wakatime.com/api/v1/users/current/stats/last_7_days",
        headers={
            "Accept": "application/json",
            "Authorization": f"Basic {encoded_key}",
            "User-Agent": f"{USERNAME}-profile-readme",
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)

    stats = payload.get("data")
    if not isinstance(stats, dict):
        raise RuntimeError("WakaTime stats response is invalid")
    return stats


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


def build_wakatime(stats: dict[str, Any]) -> str:
    languages = [
        language
        for language in stats.get("languages", [])
        if language.get("name")
    ][:MAX_LANGUAGES]

    if not languages:
        return "_No activity tracked._"

    name_width = max(len(str(language["name"])) for language in languages)
    lines: list[str] = []
    total = stats.get("human_readable_total")
    if total:
        lines.extend((f"Total: {total}", ""))

    for language in languages:
        name = str(language["name"])
        duration = str(language.get("text") or "0 secs")
        percent = float(language.get("percent") or 0)
        filled = max(0, min(GRAPH_WIDTH, round(percent / 100 * GRAPH_WIDTH)))
        graph = "█" * filled + "░" * (GRAPH_WIDTH - filled)
        lines.append(
            f"{name.ljust(name_width)}   {duration:<18} {graph}   {percent:05.2f} %"
        )

    return "```text\n" + "\n".join(lines) + "\n```"


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
    updated, count = pattern.subn(replacement, content, count=1)

    if count != 1:
        raise RuntimeError(
            f"README {section_name} markers are missing or duplicated"
        )

    return updated


def update_readme(activity: str, wakatime: str | None = None) -> bool:
    original = README_PATH.read_text(encoding="utf-8")
    updated = replace_section(
        original,
        START_MARKER,
        END_MARKER,
        activity,
        "activity",
    )

    if wakatime is not None:
        updated = replace_section(
            updated,
            WAKATIME_START_MARKER,
            WAKATIME_END_MARKER,
            wakatime,
            "WakaTime",
        )

    if updated == original:
        return False

    README_PATH.write_text(updated, encoding="utf-8", newline="\n")
    return True


def main() -> None:
    events = fetch_public_events()
    wakatime_stats = fetch_wakatime_stats()
    wakatime = build_wakatime(wakatime_stats) if wakatime_stats is not None else None
    changed = update_readme(build_activity(events), wakatime)
    if wakatime_stats is None:
        print("WakaTime update skipped: WAKATIME_API_KEY is not configured.")
    print("README updated." if changed else "README is already up to date.")


if __name__ == "__main__":
    main()
