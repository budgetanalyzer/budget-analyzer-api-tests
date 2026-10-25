from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from api_tests.dependency_contract import (
    PYPI_INDEX,
    pinned_uv_executable,
    validate_contract_files,
)
from api_tests.dependency_evidence import (
    GENERATED_OUTPUTS,
    LEGACY_OUTPUTS,
    SETUP_OUTPUTS,
    DependencyEvidenceError,
    generate_dependency_evidence,
    redact_text,
    write_failure_diagnostics,
)
from api_tests.local_bootstrap import BootstrapError, bootstrap_environment, sanitized_environment


def main(argv: Sequence[str] | None = None) -> int:
    """Prepare isolated tools and emit one complete dependency-evidence directory."""
    parser = argparse.ArgumentParser(description="Generate exact Python dependency evidence")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/dependency-evidence"),
        help="output directory under artifacts/",
    )
    parser.add_argument("--generate-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--uv", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--scanner", type=Path, help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[3]
    output_dir = (
        (root / arguments.output).resolve()
        if not arguments.output.is_absolute()
        else arguments.output.resolve()
    )
    artifacts_root = (root / "artifacts").resolve()
    if not output_dir.is_relative_to(artifacts_root) or output_dir == artifacts_root:
        print(
            "dependency evidence output must be a child of the repository artifacts directory",
            file=sys.stderr,
        )
        return 2
    output_dir.mkdir(parents=True, exist_ok=True)

    if arguments.generate_only:
        if arguments.uv is None or arguments.scanner is None:
            print("internal generation requires --uv and --scanner", file=sys.stderr)
            return 2
        try:
            generate_dependency_evidence(
                root=root,
                output_dir=output_dir,
                python_executable=Path(sys.executable),
                uv_executable=arguments.uv.resolve(),
                scanner_executable=arguments.scanner.resolve(),
            )
        except DependencyEvidenceError as exc:
            print(f"dependency evidence failed: {exc}", file=sys.stderr)
            return 1
        print(f"Dependency evidence complete: {output_dir}")
        return 0

    for name in SETUP_OUTPUTS + GENERATED_OUTPUTS + LEGACY_OUTPUTS:
        (output_dir / name).unlink(missing_ok=True)
    bootstrap_log = output_dir / "bootstrap.log"
    scanner_log = output_dir / "scanner-install.log"
    try:
        contract, issues = validate_contract_files(root)
        if contract is None or issues:
            details = "; ".join(issues) if issues else "dependency contract is incomplete"
            raise DependencyEvidenceError(details)
        bootstrap_environment(
            root,
            bootstrap_python_executable=Path(sys.executable),
            bootstrap_runtime_version=platform.python_version(),
        )
        bootstrap_log.write_text(
            f"Python {contract.python_pin}\nuv {contract.uv_version}\n"
            "Frozen project environment synchronized and verified.\n",
            encoding="utf-8",
        )

        scanner_root = root / ".dependency-tools" / f"pip-audit-{contract.pip_audit_version}"
        scanner_executable = scanner_root / "bin" / "pip-audit"
        create = _run((str(sys.executable), "-m", "venv", "--clear", str(scanner_root)), root)
        if create.returncode != 0:
            raise DependencyEvidenceError(
                "could not create scanner environment: " + _detail(create)
            )
        install = _run(
            (
                str(scanner_root / "bin" / "python"),
                "-m",
                "pip",
                "install",
                "--isolated",
                "--disable-pip-version-check",
                "--index-url",
                PYPI_INDEX,
                f"pip-audit=={contract.pip_audit_version}",
            ),
            root,
        )
        scanner_log.write_text(redact_text(install.stdout + install.stderr), encoding="utf-8")
        if install.returncode != 0:
            raise DependencyEvidenceError("could not install pinned pip-audit: " + _detail(install))

        uv_executable = pinned_uv_executable(root, contract)
        project_python = root / ".venv" / "bin" / "python"
        child = _run(
            (
                str(project_python),
                "-m",
                "api_tests.tools.dependency_evidence",
                "--generate-only",
                "--output",
                str(output_dir),
                "--uv",
                str(uv_executable),
                "--scanner",
                str(scanner_executable),
            ),
            root,
        )
        if child.stdout:
            print(child.stdout, end="")
        if child.returncode != 0:
            if child.stderr:
                print(redact_text(child.stderr), end="", file=sys.stderr)
            return child.returncode
        return 0
    except (BootstrapError, DependencyEvidenceError, OSError, subprocess.SubprocessError) as exc:
        message = redact_text(str(exc))
        if not bootstrap_log.exists():
            bootstrap_log.write_text(message + "\n", encoding="utf-8")
        if not scanner_log.exists():
            scanner_log.write_text("Scanner setup did not complete.\n", encoding="utf-8")
        write_failure_diagnostics(output_dir, stage="environment setup", error=message)
        print(f"dependency evidence setup failed: {message}", file=sys.stderr)
        return 1


def _run(command: Sequence[str], root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=root,
        env=sanitized_environment(),
        check=False,
        capture_output=True,
        text=True,
    )


def _detail(result: subprocess.CompletedProcess[str]) -> str:
    return redact_text(
        result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
