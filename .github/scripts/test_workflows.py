"""Regression tests for release decisions, updates and the CI HTTP assertions."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import release
import update_dependencies as update


class ReleaseTests(unittest.TestCase):
    def plan(self, event="workflow_dispatch", ref="refs/heads/main", tag="", message=""):
        return release.release_plan(event, ref, tag, message, "1.31.8")

    def test_manual_empty_ignores_commit_release_marker(self):
        self.assertEqual(self.plan(message="[RELEASE_TAG:v1.31.8]")["tag"], "")

    def test_push_marker_and_unique_update_suffix(self):
        result = self.plan(event="push", message="update [RELEASE_TAG:v1.31.8-upd123456]")
        self.assertEqual(result, {"tag": "v1.31.8-upd123456", "version": "1.31.8-upd123456", "major_minor": "1.31"})

    def test_regular_push_and_pr_do_not_publish(self):
        self.assertEqual(self.plan(event="push")["tag"], "")
        self.assertEqual(self.plan(event="pull_request", message="[RELEASE_TAG:v1.31.8]")["tag"], "")

    def test_non_main_release_is_rejected(self):
        with self.assertRaises(ValueError):
            self.plan(ref="refs/heads/feature", tag="v1.31.8")

    def test_invalid_or_mismatched_tags_are_rejected(self):
        for tag in ('v1.31.8$(echo injected)', 'v1.31.8";echo bad', 'v1.31.8\nother=value',
                    'v1.31.8/path', 'main', 'v1.30.0', ' v1.31.8', 'v1.31.8-' + 'x' * 128):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                self.plan(tag=tag)

    def test_duplicate_or_empty_push_markers_are_rejected(self):
        for message in ('[RELEASE_TAG:]', '[RELEASE_TAG:v1.31.8] [RELEASE_TAG:v1.31.8]'):
            with self.subTest(message=message), self.assertRaises(ValueError):
                self.plan(event="push", message=message)

    def test_existing_release_is_rejected_before_side_effects(self):
        with patch.object(release.subprocess, "run"), patch.object(release.subprocess, "check_output", return_value="abc\trefs/heads/main\nabc\trefs/tags/v1.31.8\n"):
            with self.assertRaisesRegex(ValueError, "already exists"):
                release.check_target("v1.31.8", "abc")

    def test_stale_main_and_remote_errors_are_not_ignored(self):
        with patch.object(release.subprocess, "run"), patch.object(release.subprocess, "check_output", return_value="new\trefs/heads/main\n"):
            with self.assertRaisesRegex(ValueError, "advanced"):
                release.check_target("v1.31.8", "old")
        with patch.object(release.subprocess, "run"), patch.object(release.subprocess, "check_output", side_effect=subprocess.CalledProcessError(128, "git")):
            with self.assertRaises(subprocess.CalledProcessError):
                release.check_target("v1.31.8", "abc")


class UpdateTests(unittest.TestCase):
    def test_automatic_nginx_updates_stay_in_current_series(self):
        versions = {"1.31.8", "1.31.9", "1.32.0", "1.30.5"}
        self.assertEqual(update.choose_version("1.31.8", "", versions, nginx=True), "1.31.9")

    def test_minor_input_resolves_highest_patch(self):
        self.assertEqual(update.choose_version("1.31.8", "1.32", {"1.32.1", "1.32.10", "1.32.2"}, nginx=True), "1.32.10")

    def test_downgrade_requires_explicit_opt_in(self):
        with self.assertRaisesRegex(ValueError, "Downgrades"):
            update.choose_version("1.31.8", "1.30.5", {"1.30.5"}, nginx=True)
        self.assertEqual(update.choose_version("1.31.8", "1.30.5", {"1.30.5"}, nginx=True, allow_downgrade=True), "1.30.5")
        self.assertEqual(update.choose_version("0.40", "", {"0.39"}), "0.40")

    def test_missing_requested_release_is_rejected(self):
        with self.assertRaises(ValueError):
            update.choose_version("0.40", "0.41", {"0.40"})

    def test_stable_tag_filter_preserves_repository_prefixes(self):
        refs = "\n".join(f"abc\trefs/tags/{tag}" for tag in ("release-1.31.8", "release-1.31.9", "release-1.32.0-rc1", "unrelated"))
        with patch.object(update.subprocess, "check_output", return_value=refs):
            self.assertEqual(update.available_versions("nginx/nginx", "release-"), {"1.31.8", "1.31.9"})

    def test_bad_inputs_fail_before_network_calls(self):
        with patch.object(update, "available_versions") as network:
            with self.assertRaises(ValueError):
                update.prepare((ROOT / "Dockerfile").read_text(), {"IN_PCRE2": '10.47\nRUN echo bad'})
            network.assert_not_called()

    def test_only_active_dependencies_are_updated(self):
        source = (ROOT / "Dockerfile").read_text()
        versions = {"nginx/nginx": {"1.31.9"}, "openresty/headers-more-nginx-module": {"0.41"}, "PCRE2Project/pcre2": {"10.48"}}
        with patch.object(update, "available_versions", side_effect=lambda repo, prefix: versions[repo]) as network:
            updated, changes = update.prepare(source, {})
        self.assertEqual(len(changes), 3)
        self.assertEqual(network.call_count, 3)
        for name in ("JEMALLOC_VERSION", "MIMALLOC_VERSION", "MALLOC_IMPL", "GEOIP2_VERSION", "FANCYINDEX_VERSION"):
            self.assertEqual(update.get_arg(source, name), update.get_arg(updated, name))

    def test_network_failure_does_not_produce_a_partial_update(self):
        source = (ROOT / "Dockerfile").read_text()
        with patch.object(update, "available_versions", side_effect=subprocess.CalledProcessError(128, "git")):
            with self.assertRaises(subprocess.CalledProcessError):
                update.prepare(source, {})
        self.assertEqual(source, (ROOT / "Dockerfile").read_text())


class HttpAssertionTests(unittest.TestCase):
    """Run the actual CI shell helper with deterministic command stubs, without Docker."""
    def run_helper(self, **overrides):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commands = root / "bin"
            commands.mkdir()
            scripts = {
                "docker": "import sys\nif sys.argv[1]=='inspect':print('true')\n",
                "openssl": "import sys\nfrom pathlib import Path\nfor flag in ('-keyout','-out'):\n Path(sys.argv[sys.argv.index(flag)+1]).write_text('test certificate')\n",
                "curl": """import os,sys
from pathlib import Path
args=sys.argv[1:]
if '--dump-header' in args:
 if os.environ.get('CURL_FAIL')=='true':sys.exit(22)
 protocol='1.1' if '--http1.1' in args else '2'
 status=os.environ.get('HTTP_STATUS','200')
 headers=f'HTTP/{protocol} {status} OK\\r\\nContent-Encoding: br\\r\\n'
 if os.environ.get('SERVER_HEADER')=='true':headers+='Server: nginx\\r\\n'
 Path(args[args.index('--dump-header')+1]).write_text(headers+'\\r\\n')
 Path(args[args.index('--output')+1]).write_text('<p>It works!</p>\\n')
 print(status+' '+protocol,end='')
""",
            }
            for command, source in scripts.items():
                path = commands / command
                path.write_text(f"#!{sys.executable}\n" + source)
                path.chmod(0o755)
            env = {**os.environ, "PATH": str(commands) + os.pathsep + os.environ["PATH"], "RUNNER_TEMP": temporary, **overrides}
            return subprocess.run(["bash", str(HERE / "test_image.sh"), "test-image"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=10)

    def test_successful_responses_pass(self):
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_server_header_is_rejected(self):
        result = self.run_helper(SERVER_HEADER="true")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Server response header must be absent", result.stderr)

    def test_non_200_is_rejected_even_with_expected_body(self):
        self.assertNotEqual(self.run_helper(HTTP_STATUS="404").returncode, 0)

    def test_curl_failure_is_not_hidden(self):
        self.assertNotEqual(self.run_helper(CURL_FAIL="true").returncode, 0)


if __name__ == "__main__":
    unittest.main()
