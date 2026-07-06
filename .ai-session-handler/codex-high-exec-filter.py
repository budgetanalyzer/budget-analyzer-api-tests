#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


MARKER_PATTERN = re.compile(
    r"<(?P<tag>phase-complete|phase-blocked|phase-needs-clarification)>"
    r"(?P<text>.*?)"
    r"</(?P=tag)>",
    re.DOTALL,
)


def _sanitize_markers(text: str) -> str:
    return (
        text.replace("<phase-complete>", "[phase-complete]")
        .replace("</phase-complete>", "[/phase-complete]")
        .replace("<phase-blocked>", "[phase-blocked]")
        .replace("</phase-blocked>", "[/phase-blocked]")
        .replace("<phase-needs-clarification>", "[phase-needs-clarification]")
        .replace("</phase-needs-clarification>", "[/phase-needs-clarification]")
    )


def main() -> int:
    prompt = sys.stdin.read()
    env = os.environ.copy()
    env["CODEX_REASONING_EFFORT"] = "high"

    with tempfile.TemporaryDirectory(prefix="ai-session-handler-codex-") as temp_dir:
        final_message_path = Path(temp_dir) / "final-message.txt"
        process = subprocess.run(
            [
                "codex-lean",
                "exec",
                "--color",
                "never",
                "--output-last-message",
                str(final_message_path),
            ],
            input=prompt,
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

        sys.stdout.write(_sanitize_markers(process.stdout))
        sys.stderr.write(_sanitize_markers(process.stderr))

        try:
            final_message = final_message_path.read_text(encoding="utf-8")
        except OSError:
            final_message = ""

        matches = list(MARKER_PATTERN.finditer(final_message))
        if len(matches) == 1:
            sys.stdout.write(matches[0].group(0))
            sys.stdout.write("\n")
        elif final_message.strip():
            sys.stdout.write(_sanitize_markers(final_message))
            if not final_message.endswith("\n"):
                sys.stdout.write("\n")

        return process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
