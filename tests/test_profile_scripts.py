from email.message import Message
from io import BytesIO
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import xml.etree.ElementTree as ET

from scripts import update_github_stats, update_readme


class ProfileScriptsTest(unittest.TestCase):
    def test_profile_workflow_uses_wakapi_variable_and_secret(self) -> None:
        workflow = Path(".github/workflows/update-profile.yml").read_text(encoding="utf-8")

        self.assertIn("WAKAPI_BASE_URL: ${{ vars.WAKAPI_BASE_URL }}", workflow)
        self.assertIn("WAKAPI_API_KEY: ${{ secrets.WAKAPI_API_KEY }}", workflow)
        self.assertNotIn("WAKATIME_API_KEY", workflow)

    def test_public_event_is_rendered(self) -> None:
        output = update_readme.build_activity(
            [
                {
                    "type": "PublicEvent",
                    "repo": {"name": "KangDohwa/hermes-runtime-clock"},
                    "created_at": "2026-08-01T13:29:41Z",
                }
            ]
        )

        self.assertEqual(
            "- `2026-08-01` Made "
            "[`KangDohwa/hermes-runtime-clock`]("
            "https://github.com/KangDohwa/hermes-runtime-clock) public",
            output,
        )

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

    def test_wakapi_stats_use_configured_https_compatibility_endpoint(self) -> None:
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.get_header("Authorization")
            captured["timeout"] = timeout
            return BytesIO(b'{"data":{"languages":[]}}')

        with (
            patch.dict(
                os.environ,
                {
                    "WAKAPI_BASE_URL": "https://stats.example.com/",
                    "WAKAPI_API_KEY": "test-key",
                },
                clear=False,
            ),
            patch.object(update_readme.urllib.request, "urlopen", side_effect=fake_urlopen),
        ):
            stats = update_readme.fetch_wakatime_stats()

        self.assertEqual(stats, {"languages": []})
        self.assertEqual(
            captured["url"],
            "https://stats.example.com/api/compat/wakatime/v1/users/current/stats/last_7_days",
        )
        self.assertTrue(captured["authorization"].startswith("Basic "))
        self.assertNotIn("test-key", captured["authorization"])
        self.assertEqual(captured["timeout"], 30)

    def test_wakapi_rejects_insecure_or_partial_configuration(self) -> None:
        with (
            patch.dict(
                os.environ,
                {"WAKAPI_BASE_URL": "http://stats.example.com", "WAKAPI_API_KEY": "key"},
                clear=False,
            ),
            self.assertRaisesRegex(RuntimeError, "HTTPS"),
        ):
            update_readme.fetch_wakatime_stats()

        with (
            patch.dict(os.environ, {"WAKAPI_BASE_URL": "https://stats.example.com"}, clear=True),
            self.assertRaisesRegex(RuntimeError, "configured together"),
        ):
            update_readme.fetch_wakatime_stats()

    def test_wakapi_missing_configuration_skips_update(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(update_readme.fetch_wakatime_stats())

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

    def test_github_cards_share_midpoint_size(self) -> None:
        stats = update_github_stats.RollingStats(1, 2, 3, 4)
        cards = (
            update_github_stats.render_stats_card(stats, []),
            update_github_stats.render_languages_card(
                {f"Language {index}": index for index in range(1, 7)}, []
            ),
        )

        dimensions = {
            (
                root.attrib["width"],
                root.attrib["height"],
                root.attrib["viewBox"],
            )
            for root in (ET.fromstring(card) for card in cards)
        }

        self.assertEqual({("394", "190", "0 0 394 190")}, dimensions)


if __name__ == "__main__":
    unittest.main()
