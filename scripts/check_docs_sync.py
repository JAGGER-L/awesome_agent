from __future__ import annotations

import argparse
import subprocess
from collections.abc import Iterable

README_DOCS = {"README.md", "README.zh-CN.md"}


def required_docs_for(changed_files: Iterable[str]) -> dict[str, set[str]]:
    changed = {path.replace("\\", "/") for path in changed_files}
    requirements: dict[str, set[str]] = {}

    def require(reason: str, documents: set[str]) -> None:
        requirements[reason] = documents

    if any(
        path.startswith(("src/awesome_agent/api/", "src/awesome_agent/cli/"))
        for path in changed
    ):
        require(
            "API or CLI behavior",
            README_DOCS
            | {
                "ARCHITECTURE.md",
                "docs/product-specs/local-coding-agent.md",
            },
        )

    if any(
        path.startswith(
            ("src/awesome_agent/orchestration/", "src/awesome_agent/agents/")
        )
        for path in changed
    ):
        require(
            "agent orchestration",
            {
                "docs/design-docs/agent-team-and-subagents.md",
                "docs/design-docs/task-and-verification-model.md",
                "docs/design-docs/runtime-agent-harness.md",
            },
        )

    if any(path.startswith("src/awesome_agent/providers/") for path in changed):
        require(
            "model providers",
            README_DOCS
            | {
                "ARCHITECTURE.md",
                "docs/design-docs/agent-team-and-subagents.md",
            },
        )

    if any(path.startswith("src/awesome_agent/memory/") for path in changed):
        require(
            "memory behavior",
            {
                "docs/design-docs/memory-architecture.md",
                "docs/SECURITY.md",
                "docs/RELIABILITY.md",
            },
        )

    if any(
        path.startswith(
            (
                "src/awesome_agent/tools/",
                "src/awesome_agent/sandbox/",
            )
        )
        for path in changed
    ):
        require(
            "tools, approvals, or sandbox",
            {
                "docs/SECURITY.md",
                "docs/RELIABILITY.md",
                "docs/design-docs/runtime-agent-harness.md",
            },
        )

    if any(
        path.startswith(("src/awesome_agent/persistence/", "migrations/"))
        for path in changed
    ):
        require(
            "persistence or schema",
            {
                "ARCHITECTURE.md",
                "docs/RELIABILITY.md",
                "docs/generated/db-schema.md",
            },
        )

    if any(
        path.startswith(
            (
                "src/awesome_agent/observability/",
                "src/awesome_agent/runtime/events.py",
            )
        )
        for path in changed
    ):
        require(
            "observability or events",
            {
                "ARCHITECTURE.md",
                "docs/design-docs/observability.md",
            },
        )

    if any(
        path in {".env.example", "src/awesome_agent/settings.py"} for path in changed
    ):
        require(
            "configuration",
            README_DOCS | {".env.example", "docs/SECURITY.md"},
        )

    if any(path in {"pyproject.toml", "uv.lock"} for path in changed):
        require("dependencies", README_DOCS)

    if any(
        path == "AGENTS.md"
        or path.startswith(("docs/engineering/", "scripts/", ".github/workflows/"))
        for path in changed
    ):
        require(
            "repository engineering harness",
            {
                "AGENTS.md",
                "docs/engineering/engineering-harness.md",
                "docs/engineering/documentation-sync.md",
            },
        )

    return requirements


def missing_documentation(changed_files: Iterable[str]) -> dict[str, set[str]]:
    changed = {path.replace("\\", "/") for path in changed_files}
    return {
        reason: documents - changed
        for reason, documents in required_docs_for(changed).items()
        if documents - changed
    }


def _git(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _working_tree_files() -> set[str]:
    tracked = set(_git("diff", "--name-only", "HEAD"))
    untracked = set(_git("ls-files", "--others", "--exclude-standard"))
    return tracked | untracked


def _revision_files(base: str, head: str) -> set[str]:
    if set(base) == {"0"}:
        return set(_git("show", "--pretty=", "--name-only", head))
    return set(_git("diff", "--name-only", f"{base}...{head}"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Require documentation updates for mapped code changes."
    )
    parser.add_argument("--base")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args()

    if args.base:
        changed = _revision_files(args.base, args.head)
    else:
        changed = _working_tree_files()

    missing = missing_documentation(changed)
    if not missing:
        print("Documentation synchronization check passed.")
        return 0

    print("Documentation synchronization check failed.")
    print("Changed implementation areas require these files in the same diff:")
    for reason, documents in sorted(missing.items()):
        print(f"- {reason}:")
        for document in sorted(documents):
            print(f"  - {document}")
    print(
        "Update the mapped documentation or refine the mapping in "
        "docs/engineering/documentation-sync.md with a durable justification."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
