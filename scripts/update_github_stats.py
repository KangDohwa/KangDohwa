from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape


API_BASE_URL = "https://api.github.com"
API_VERSION = "2022-11-28"
USERNAME = os.environ.get("PROFILE_USERNAME", "KangDohwa")
TOKEN = os.environ.get("GH_STATS_TOKEN")
ASSETS_DIR = Path(os.environ.get("STATS_ASSETS_DIR", "assets"))
STATS_PATH = ASSETS_DIR / "github-stats.svg"
LANGUAGES_PATH = ASSETS_DIR / "top-langs.svg"
MAX_LANGUAGES = 6
ROLLING_DAYS = 30

LANGUAGE_COLORS = {
    "C#": "#178600",
    "C++": "#f34b7d",
    "CSS": "#663399",
    "Go": "#00ADD8",
    "HTML": "#e34c26",
    "Java": "#b07219",
    "JavaScript": "#f1e05a",
    "Jupyter Notebook": "#DA5B0B",
    "Kotlin": "#A97BFF",
    "PHP": "#4F5D95",
    "PowerShell": "#012456",
    "Python": "#3572A5",
    "Ruby": "#701516",
    "Rust": "#dea584",
    "Shell": "#89e051",
    "Swift": "#F05138",
    "TypeScript": "#3178c6",
}


@dataclass(frozen=True)
class RollingStats:
    repositories: int
    commits: int
    prs_opened: int
    prs_merged: int


def api_request(path_or_url: str) -> tuple[Any, dict[str, str]]:
    if not TOKEN:
        raise RuntimeError("GH_STATS_TOKEN is not configured")

    url = (
        path_or_url
        if path_or_url.startswith("https://")
        else f"{API_BASE_URL}{path_or_url}"
    )
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {TOKEN}",
            "User-Agent": f"{USERNAME}-profile-stats",
            "X-GitHub-Api-Version": API_VERSION,
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response), dict(response.headers.items())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"GitHub API request failed ({error.code}) for {url}: {detail}"
        ) from error


def graphql_request(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    if not TOKEN:
        raise RuntimeError("GH_STATS_TOKEN is not configured")

    request = urllib.request.Request(
        f"{API_BASE_URL}/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": f"{USERNAME}-profile-stats",
            "X-GitHub-Api-Version": API_VERSION,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"GitHub GraphQL request failed ({error.code}): {detail}"
        ) from error

    if not isinstance(payload, dict):
        raise RuntimeError("GitHub GraphQL response is invalid")
    if payload.get("errors"):
        raise RuntimeError(f"GitHub GraphQL request failed: {payload['errors']}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("GitHub GraphQL response has no data")
    return data


def paginated(path: str) -> list[dict[str, Any]]:
    separator = "&" if "?" in path else "?"
    items: list[dict[str, Any]] = []
    page = 1

    while True:
        payload, _ = api_request(f"{path}{separator}per_page=100&page={page}")
        if not isinstance(payload, list):
            raise RuntimeError(f"Expected a list response from GitHub API: {path}")
        items.extend(payload)
        if len(payload) < 100:
            break
        page += 1

    return items


def authenticated_user() -> dict[str, Any]:
    user, _ = api_request("/user")
    if not isinstance(user, dict):
        raise RuntimeError("GitHub user response is invalid")
    login = str(user.get("login", ""))
    if login.casefold() != USERNAME.casefold():
        raise RuntimeError(
            f"GH_STATS_TOKEN belongs to {login or 'an unknown user'}, not {USERNAME}"
        )
    return user


def owner_organizations() -> list[str]:
    try:
        memberships = paginated("/user/memberships/orgs?state=active")
    except RuntimeError as error:
        if "(403)" in str(error):
            raise RuntimeError(
                "GH_STATS_TOKEN needs the read:org scope to detect owner organizations"
            ) from error
        raise

    owners = {
        str(membership.get("organization", {}).get("login", ""))
        for membership in memberships
        if membership.get("state") == "active" and membership.get("role") == "admin"
    }
    return sorted(owner for owner in owners if owner)


def included_repositories(organizations: list[str]) -> list[dict[str, Any]]:
    repositories = paginated(
        "/user/repos?affiliation=owner&visibility=all&sort=full_name"
    )
    for organization in organizations:
        encoded = urllib.parse.quote(organization, safe="")
        repositories.extend(
            paginated(f"/orgs/{encoded}/repos?type=all&sort=full_name")
        )

    unique: dict[str, dict[str, Any]] = {}
    for repository in repositories:
        full_name = str(repository.get("full_name", ""))
        if not full_name or repository.get("fork"):
            continue
        unique[full_name.casefold()] = repository

    return sorted(unique.values(), key=lambda item: str(item["full_name"]).casefold())


def search_issues(query: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page = 1

    while True:
        params = urllib.parse.urlencode(
            {"q": query, "per_page": 100, "page": page}
        )
        payload, _ = api_request(f"/search/issues?{params}")
        if not isinstance(payload, dict):
            raise RuntimeError("GitHub issue search response is invalid")
        if payload.get("incomplete_results"):
            raise RuntimeError("GitHub issue search returned incomplete results")

        total_count = int(payload.get("total_count") or 0)
        if total_count > 1000:
            raise RuntimeError(
                "GitHub issue search exceeded the 1,000-result API limit"
            )

        page_items = payload.get("items")
        if not isinstance(page_items, list):
            raise RuntimeError("GitHub issue search items are invalid")
        items.extend(item for item in page_items if isinstance(item, dict))

        if len(items) >= total_count or len(page_items) < 100:
            break
        page += 1

    return items


def parse_github_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def search_item_repository(item: dict[str, Any]) -> str:
    repository_url = str(item.get("repository_url", ""))
    prefix = f"{API_BASE_URL}/repos/"
    if not repository_url.startswith(prefix):
        return ""
    return urllib.parse.unquote(repository_url[len(prefix) :]).casefold()


def rolling_stats(repositories: list[dict[str, Any]]) -> RollingStats:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=ROLLING_DAYS)
    allowed_repositories = {
        str(repository.get("full_name", "")).casefold()
        for repository in repositories
        if repository.get("full_name")
    }

    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          commitContributionsByRepository(maxRepositories: 100) {
            repository { nameWithOwner }
            contributions(first: 100) {
              nodes { commitCount }
            }
          }
        }
      }
    }
    """
    data = graphql_request(
        query,
        {
            "login": USERNAME,
            "from": since.isoformat().replace("+00:00", "Z"),
            "to": now.isoformat().replace("+00:00", "Z"),
        },
    )

    user = data.get("user")
    if not isinstance(user, dict):
        raise RuntimeError("GitHub contributions user response is invalid")
    collection = user.get("contributionsCollection")
    if not isinstance(collection, dict):
        raise RuntimeError("GitHub contributions response is invalid")

    worked_repositories: set[str] = set()
    commit_count = 0
    commit_groups = collection.get("commitContributionsByRepository")
    if not isinstance(commit_groups, list):
        raise RuntimeError("GitHub commit contributions response is invalid")
    for group in commit_groups:
        if not isinstance(group, dict):
            continue
        repository = group.get("repository")
        contributions = group.get("contributions")
        if not isinstance(repository, dict) or not isinstance(contributions, dict):
            continue
        name = str(repository.get("nameWithOwner", "")).casefold()
        if name not in allowed_repositories:
            continue
        nodes = contributions.get("nodes")
        if not isinstance(nodes, list):
            continue
        repository_commits = sum(
            int(node.get("commitCount") or 0)
            for node in nodes
            if isinstance(node, dict)
        )
        if repository_commits:
            worked_repositories.add(name)
            commit_count += repository_commits

    opened_items = search_issues(
        f"is:pr author:{USERNAME} created:>={since.date().isoformat()}"
    )
    opened_pr_count = 0
    for item in opened_items:
        name = search_item_repository(item)
        if name not in allowed_repositories:
            continue
        created_at = parse_github_datetime(item.get("created_at"))
        if created_at is None or not since <= created_at <= now:
            continue
        worked_repositories.add(name)
        opened_pr_count += 1

    merged_items = search_issues(
        f"is:pr is:merged author:{USERNAME} merged:>={since.date().isoformat()}"
    )
    merged_pr_count = 0
    for item in merged_items:
        name = search_item_repository(item)
        if name not in allowed_repositories:
            continue
        pull_request = item.get("pull_request")
        if not isinstance(pull_request, dict):
            continue
        merged_at = parse_github_datetime(pull_request.get("merged_at"))
        if merged_at is None or not since <= merged_at <= now:
            continue
        worked_repositories.add(name)
        merged_pr_count += 1

    return RollingStats(
        repositories=len(worked_repositories),
        commits=commit_count,
        prs_opened=opened_pr_count,
        prs_merged=merged_pr_count,
    )


def language_totals(repositories: list[dict[str, Any]]) -> dict[str, int]:
    totals: defaultdict[str, int] = defaultdict(int)

    def fetch_languages(repository: dict[str, Any]) -> dict[str, int]:
        languages_url = str(repository.get("languages_url", ""))
        if not languages_url:
            return {}
        languages, _ = api_request(languages_url)
        if not isinstance(languages, dict):
            return {}
        return {
            str(language): size
            for language, size in languages.items()
            if isinstance(size, int) and size > 0
        }

    worker_count = min(8, max(1, len(repositories)))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        language_results = executor.map(fetch_languages, repositories)
        for languages in language_results:
            for language, size in languages.items():
                totals[language] += size

    return dict(totals)


def language_color(name: str) -> str:
    if name in LANGUAGE_COLORS:
        return LANGUAGE_COLORS[name]
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
    hue = int(digest[:4], 16) % 360
    return f"hsl({hue} 62% 52%)"


def shared_style() -> str:
    return """
    <style>
      text { font-family: Inter, "Segoe UI", Arial, sans-serif; }
      .card { fill: #ffffff; stroke: #e2e8f0; }
      .title { fill: #0f172a; font-weight: 700; }
      .subtitle, .label { fill: #64748b; }
      .value, .language { fill: #1e293b; font-weight: 600; }
      .track { fill: #e2e8f0; }
      @media (prefers-color-scheme: dark) {
        .card { fill: #0d1117; stroke: #30363d; }
        .title { fill: #f0f6fc; }
        .subtitle, .label { fill: #8b949e; }
        .value, .language { fill: #c9d1d9; }
        .track { fill: #30363d; }
      }
    </style>
    """.strip()


def render_stats_card(stats: RollingStats, organizations: list[str]) -> str:
    metrics = (
        ("Repositories", stats.repositories),
        ("PRs opened", stats.prs_opened),
        ("Commits", stats.commits),
        ("PRs merged", stats.prs_merged),
    )

    metric_nodes: list[str] = []
    positions = ((32, 92), (250, 92), (32, 151), (250, 151))
    for (label, value), (x, y) in zip(metrics, positions):
        metric_nodes.append(
            f'<text class="label" x="{x}" y="{y}" font-size="13">{escape(label)}</text>'
        )
        metric_nodes.append(
            f'<text class="value" x="{x}" y="{y + 24}" font-size="22">{value:,}</text>'
        )

    subtitle = f"Last {ROLLING_DAYS} days · Personal + {len(organizations)} owner organizations"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="480" height="190" viewBox="0 0 480 190" role="img" aria-labelledby="stats-title stats-desc">
  <title id="stats-title">KangDohwa GitHub statistics</title>
  <desc id="stats-desc">Rolling {ROLLING_DAYS}-day contribution statistics from personal repositories and organizations owned by KangDohwa.</desc>
  <defs>{shared_style()}</defs>
  <rect class="card" x="0.5" y="0.5" width="479" height="189" rx="12"/>
  <text class="title" x="28" y="36" font-size="20">GitHub Stats</text>
  <text class="subtitle" x="28" y="59" font-size="12">{escape(subtitle)}</text>
  {''.join(metric_nodes)}
</svg>
"""


def render_languages_card(totals: dict[str, int], organizations: list[str]) -> str:
    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    total_bytes = sum(size for _, size in ranked)
    ranked = ranked[:MAX_LANGUAGES]
    subtitle = (
        f"Personal repositories + {len(organizations)} owner organization"
        f"{'s' if len(organizations) != 1 else ''}"
    )

    rows: list[str] = []
    for index, (name, size) in enumerate(ranked):
        percent = size / total_bytes * 100 if total_bytes else 0
        y = 83 + index * 26
        bar_width = round(150 * percent / 100, 1)
        color = language_color(name)
        rows.append(
            f'<rect class="track" x="154" y="{y - 12}" width="150" height="8" rx="4"/>'
            f'<rect x="154" y="{y - 12}" width="{bar_width}" height="8" rx="4" fill="{color}"/>'
            f'<circle cx="28" cy="{y - 4}" r="5" fill="{color}"/>'
            f'<text class="language" x="42" y="{y}" font-size="13">{escape(name)}</text>'
            f'<text class="label" x="352" y="{y}" font-size="12" text-anchor="end">{percent:.1f}%</text>'
        )

    if not rows:
        rows.append(
            '<text class="subtitle" x="28" y="104" font-size="13">No language data available.</text>'
        )

    height = max(132, 78 + max(1, len(ranked)) * 26)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="380" height="{height}" viewBox="0 0 380 {height}" role="img" aria-labelledby="languages-title languages-desc">
  <title id="languages-title">KangDohwa most used languages</title>
  <desc id="languages-desc">Language distribution by bytes across personal repositories and organizations owned by KangDohwa.</desc>
  <defs>{shared_style()}</defs>
  <rect class="card" x="0.5" y="0.5" width="379" height="{height - 1}" rx="12"/>
  <text class="title" x="24" y="34" font-size="19">Most Used Languages</text>
  <text class="subtitle" x="24" y="55" font-size="11">{escape(subtitle)}</text>
  {''.join(rows)}
</svg>
"""


def write_if_changed(path: Path, content: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.write_text(content, encoding="utf-8", newline="\n")
    return True


def main() -> None:
    authenticated_user()
    organizations = owner_organizations()
    repositories = included_repositories(organizations)
    stats = rolling_stats(repositories)
    totals = language_totals(repositories)

    changed = False
    changed |= write_if_changed(
        STATS_PATH, render_stats_card(stats, organizations)
    )
    changed |= write_if_changed(
        LANGUAGES_PATH, render_languages_card(totals, organizations)
    )

    print(
        f"Generated GitHub cards from {len(repositories)} repositories "
        f"and {len(organizations)} owner organizations; last {ROLLING_DAYS} days: "
        f"{stats.repositories} active repositories, {stats.commits} commits, "
        f"{stats.prs_opened} PRs opened, {stats.prs_merged} PRs merged."
    )
    print("GitHub cards updated." if changed else "GitHub cards are up to date.")


if __name__ == "__main__":
    main()
