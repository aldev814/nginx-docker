#!/usr/bin/env python3
"""Prepare a dependency-update PR, preserving the current Nginx version series."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess

VERSION = re.compile(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?\Z")
COMPONENTS = (
    ("NGINX_VERSION", "nginx/nginx", "release-", "IN_NGINX"),
    ("HEADERS_MORE_VERSION", "openresty/headers-more-nginx-module", "v", "IN_HEADERS_MORE"),
    ("PCRE2_VERSION", "PCRE2Project/pcre2", "pcre2-", "IN_PCRE2"),
)


def version_tuple(value):
    if not VERSION.fullmatch(value):
        raise ValueError(f"Invalid numeric version: {value!r}")
    parts = tuple(map(int, value.split(".")))
    return parts + (0,) * (3 - len(parts))


def available_versions(repo, prefix):
    result = subprocess.check_output(
        ["git", "ls-remote", "--tags", "--refs", f"https://github.com/{repo}.git"],
        text=True, timeout=60,
    )
    versions = set()
    for line in result.splitlines():
        tag = line.split("refs/tags/", 1)[-1]
        if tag.startswith(prefix):
            value = tag[len(prefix):]
            if VERSION.fullmatch(value):
                versions.add(value)
    if not versions:
        raise ValueError(f"No stable numeric tags found in {repo}")
    return versions


def choose_version(current, requested, versions, nginx=False, allow_downgrade=False):
    version_tuple(current)
    if requested:
        version_tuple(requested)
    if nginx:
        if requested and len(requested.split(".")) == 3:
            candidates = {requested} & versions
        else:
            series = tuple(map(int, (requested or current).split(".")[:2]))
            candidates = {v for v in versions if len(v.split(".")) == 3 and version_tuple(v)[:2] == series}
    else:
        candidates = ({requested} & versions) if requested else versions
    if not candidates:
        raise ValueError(f"Requested stable version/series was not found: {requested or current}")
    selected = max(candidates, key=version_tuple)
    if version_tuple(selected) < version_tuple(current) and not allow_downgrade:
        if requested:
            raise ValueError("Downgrades require allow_downgrade=true")
        return current
    return selected


def get_arg(source, name):
    match = re.search(rf'^ARG {re.escape(name)}="?([^"\n]+)"?$', source, re.M)
    if not match:
        raise ValueError(f"Missing Dockerfile argument: {name}")
    return match[1]


def set_arg(source, name, value):
    source, count = re.subn(rf"^ARG {re.escape(name)}=.*$", f'ARG {name}="{value}"', source, flags=re.M)
    if count != 1:
        raise ValueError(f"Expected exactly one {name} declaration")
    # Nginx's version is also read by the release planner.
    if name == "NGINX_VERSION":
        source = source.replace(f'ARG {name}="{value}"', f"ARG {name}={value}")
    return source


def prepare(source, inputs, allow_downgrade=False):
    # Validate all user inputs before making requests or changing the Dockerfile.
    for name, _, _, input_name in COMPONENTS:
        if inputs.get(input_name):
            version_tuple(inputs[input_name])
    changes = []
    for name, repo, prefix, input_name in COMPONENTS:
        current = get_arg(source, name)
        target = choose_version(current, inputs.get(input_name, ""), available_versions(repo, prefix),
                                nginx=name == "NGINX_VERSION", allow_downgrade=allow_downgrade)
        if target != current:
            source = set_arg(source, name, target)
            changes.append(f"{name}: {current} → {target}")
    return source, changes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    path = Path("Dockerfile")
    source, changes = prepare(path.read_text(), os.environ, os.environ.get("ALLOW_DOWNGRADE") == "true")
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"updated={'true' if changes else 'false'}\n")
        if not changes:
            print("No active build dependency changes")
            return
        branch = "codex/dependency-updates-" + hashlib.sha256(source.encode()).hexdigest()[:16]
        output.write(f"branch={branch}\n")
    path.write_text(source)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    # Run IDs avoid collisions between multiple releases on the same day.
    tag = f"v{get_arg(source, 'NGINX_VERSION')}-upd{os.environ['GITHUB_RUN_ID']}"
    title = f"chore: update build dependencies [RELEASE_TAG:{tag}]"
    (args.output_dir / "title.txt").write_text(title + "\n")
    body = "Update the active build dependencies:\n\n" + "\n".join(f"- {change}" for change in changes)
    body += (f"\n\nCI must pass before merging. Keep `[RELEASE_TAG:{tag}]` in the merge/squash commit "
             "to request a release after the main-branch tests pass.\n")
    (args.output_dir / "body.md").write_text(body)
    print(body)


if __name__ == "__main__":
    main()
