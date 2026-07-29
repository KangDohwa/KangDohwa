from email.message import Message
from io import BytesIO
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from scripts import update_github_stats, update_readme


class ProfileScriptsTest(unittest.TestCase):
    def test_wakatime_total_includes_other_language(self) -> None:
        output = update_readme.build_wakatime(
            {
                "human_readable_total": "1 hr",
                "human_readable_total_including_other_language": "2 hrs",
                "languages": [
                    {"name": "Other", "text": "1 hr", "percent": 50},
                    {"name": "Python", "text": "1 hr", "percent": 50},
                ],
            }
        )

        self.assertIn("Total: 2 hrs", output)

    def test_replace_section_rejects_duplicate_markers(self) -> None:
        section = "<!-- START -->old<!-- END -->"

        with self.assertRaisesRegex(RuntimeError, "markers are missing or duplicated"):
            update_readme.replace_section(
                f"{section}\n{section}",
                "<!-- START -->",
                "<!-- END -->",
                "new",
                "test",
            )

    def test_api_errors_do_not_expose_repository_names(self) -> None:
        private_url = "https://api.github.com/repos/private-org/private-repo/languages"
        error = HTTPError(
            private_url,
            404,
            "Not Found",
            Message(),
            BytesIO(b'{"message":"private-org/private-repo"}'),
        )

        with (
            patch.object(update_github_stats, "TOKEN", "test-token"),
            patch.object(
                update_github_stats.urllib.request,
                "urlopen",
                side_effect=error,
            ),
            self.assertRaises(RuntimeError) as raised,
        ):
            update_github_stats.api_request(private_url)

        self.assertEqual("GitHub API request failed (404)", str(raised.exception))

    def test_wrong_token_error_does_not_expose_login(self) -> None:
        with (
            patch.object(
                update_github_stats,
                "api_request",
                return_value={"login": "private-account"},
            ),
            self.assertRaises(RuntimeError) as raised,
        ):
            update_github_stats.authenticated_user()

        self.assertEqual(
            "GH_STATS_TOKEN belongs to an unexpected GitHub account",
            str(raised.exception),
        )

    def test_api_request_returns_json_payload(self) -> None:
        with (
            patch.object(update_github_stats, "TOKEN", "test-token"),
            patch.object(
                update_github_stats.urllib.request,
                "urlopen",
                return_value=BytesIO(b'{"ok":true}'),
            ),
        ):
            payload = update_github_stats.api_request("/user")

        self.assertEqual({"ok": True}, payload)


if __name__ == "__main__":
    unittest.main()
