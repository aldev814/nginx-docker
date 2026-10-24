#!/usr/bin/env python3
"""Validate release intent before building or making registry changes."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

TAG_PATTERN = re.compile(r"v([0-9]+\.[0-9]+\.[0-9]+)(?:-[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)?\Z")


def release_plan(event, ref, manual_tag, message, nginx_version):
    tag = ""
    if event == "workflow_dispatch":
        tag = manual_tag  # Empty explicitly means test only, regardless of the commit.
    elif event == "push":
        tags = re.findall(r"\[RELEASE_TAG:([^\]]*)\]", message)
        if len(tags) > 1:
            raise ValueError("A commit must contain at most one release marker")
        if tags:
            tag = tags[0]
            if not tag:
                raise ValueError("Release marker must not be empty")
    if not tag:
        return {"tag": "", "version": "", "major_minor": ""}
    if ref != "refs/heads/main":
        raise ValueError("Publishing is only allowed from main")
    match = TAG_PATTERN.fullmatch(tag)
    if not match or len(tag) > 128:
        raise ValueError("Use vMAJOR.MINOR.PATCH with an optional alphanumeric suffix")
    if match[1] != nginx_version:
        raise ValueError("Release version must match NGINX_VERSION in Dockerfile")
    return {"tag": tag, "version": tag[1:], "major_minor": ".".join(match[1].split(".")[:2])}


def check_target(tag, sha):
    subprocess.run(["git", "check-ref-format", f"refs/tags/{tag}"], check=True)
    result = subprocess.check_output(
        ["git", "ls-remote", "origin", "refs/heads/main", f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"],
        text=True, timeout=60,
    )
    refs = dict(reversed(line.split()) for line in result.splitlines())
    if refs.get("refs/heads/main") != sha:
        raise ValueError("main has advanced; refusing to publish stale aliases")
    if f"refs/tags/{tag}" in refs or f"refs/tags/{tag}^{{}}" in refs:
        raise ValueError("Release tag already exists; choose a new tag (existing releases are immutable)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["plan", "check-target"])
    args = parser.parse_args()
    if args.command == "check-target":
        check_target(os.environ["RELEASE_TAG"], os.environ["GITHUB_SHA"])
        return
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    nginx_version = re.search(r"^ARG NGINX_VERSION=([0-9.]+)$", Path("Dockerfile").read_text(), re.M)[1]
    plan = release_plan(
        os.environ["GITHUB_EVENT_NAME"], os.environ["GITHUB_REF"],
        os.environ.get("MANUAL_RELEASE_TAG", ""), event.get("head_commit", {}).get("message", ""), nginx_version,
    )
    if plan["tag"]:
        check_target(plan["tag"], os.environ["GITHUB_SHA"])
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        for name, value in plan.items():
            output.write(f"{name}={value}\n")
    print(f"Release requested: {plan['tag']}" if plan["tag"] else "Test only; publishing is disabled")


if __name__ == "__main__":
    main()
