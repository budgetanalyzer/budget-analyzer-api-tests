from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from api_tests.dependency_evidence import (
    SUCCESSFUL_OUTPUTS,
    DependencyEvidenceError,
    PackageRecord,
    compare_exact_packages,
    normalize_package_records,
    parse_audit_report,
    parse_dependency_tree,
    parse_lock_export,
    parse_sbom_report,
    redact_text,
    require_exact_comparisons,
    validate_complete_outputs,
)


def test_normalization_is_pep_503_compatible_and_rejects_duplicates() -> None:
    packages = normalize_package_records(
        [("Types_PyYAML", "6.0"), ("Example.Package", "1.2.3")], source="test"
    )

    assert packages == (
        PackageRecord("example-package", "1.2.3"),
        PackageRecord("types-pyyaml", "6.0"),
    )

    with pytest.raises(DependencyEvidenceError, match="duplicate normalized"):
        normalize_package_records(
            [("Example_Package", "1"), ("example-package", "1")], source="test"
        )


def test_selected_lock_export_requires_hashes_and_evaluates_markers() -> None:
    selected = parse_lock_export(
        """
example_package==1.2.3 \\
    --hash=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
windows-only==9.0 ; sys_platform == 'win32' \\
    --hash=sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
"""
    )

    assert selected == (PackageRecord("example-package", "1.2.3"),)

    with pytest.raises(DependencyEvidenceError, match="has no SHA-256 hash"):
        parse_lock_export("example==1.0\n")


def test_exact_comparison_reports_missing_extra_and_version_mismatch() -> None:
    expected = (
        PackageRecord("alpha", "1"),
        PackageRecord("beta", "2"),
    )
    actual = (
        PackageRecord("alpha", "9"),
        PackageRecord("extra", "3"),
    )

    comparison = compare_exact_packages(expected, actual, source="inventory")

    assert comparison.status == "mismatch"
    assert comparison.missing == ("beta",)
    assert comparison.extra == ("extra",)
    assert comparison.version_mismatches[0].name == "alpha"
    with pytest.raises(DependencyEvidenceError, match="package parity failed"):
        require_exact_comparisons((comparison,))


def test_audit_findings_are_valid_non_gating_output() -> None:
    raw = json.dumps(
        {
            "dependencies": [
                {
                    "name": "Example_Package",
                    "version": "1.2.3",
                    "vulns": [{"id": "PYSEC-1", "fix_versions": ["1.2.4"]}],
                }
            ],
            "fixes": [],
        }
    )

    report = parse_audit_report(raw, exit_code=1)

    assert report.packages == (PackageRecord("example-package", "1.2.3"),)
    assert report.vulnerability_count == 1
    assert report.vulnerable_package_count == 1


@pytest.mark.parametrize("exit_code", [0, 2])
def test_audit_findings_with_unexpected_status_are_operational_failure(exit_code: int) -> None:
    raw = json.dumps(
        {
            "dependencies": [
                {
                    "name": "example",
                    "version": "1.0",
                    "vulns": [{"id": "PYSEC-1"}],
                }
            ]
        }
    )

    with pytest.raises(DependencyEvidenceError, match="returned"):
        parse_audit_report(raw, exit_code=exit_code)


def test_malformed_scanner_output_is_operational_failure() -> None:
    with pytest.raises(DependencyEvidenceError, match="malformed JSON"):
        parse_audit_report("not-json", exit_code=1)

    with pytest.raises(DependencyEvidenceError, match="no dependency array"):
        parse_audit_report(json.dumps({"dependencies": None}), exit_code=0)


def test_cyclonedx_requires_complete_components_and_matching_status() -> None:
    report = parse_sbom_report(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.4",
                "components": [{"type": "library", "name": "Example.Package", "version": "1.2.3"}],
                "vulnerabilities": [],
            }
        ),
        exit_code=0,
    )

    assert report.packages == (PackageRecord("example-package", "1.2.3"),)

    with pytest.raises(DependencyEvidenceError, match="needs name and version"):
        parse_sbom_report(
            json.dumps(
                {
                    "bomFormat": "CycloneDX",
                    "specVersion": "1.4",
                    "components": [{"name": "incomplete"}],
                }
            ),
            exit_code=0,
        )


def test_dependency_tree_is_machine_readable_and_strips_workspace_paths() -> None:
    project_id = "project==0.1@editable+/secret/workspace"
    package_id = "Example_Package==1.2.3@registry+https://pypi.org/simple"
    tree = parse_dependency_tree(
        json.dumps(
            {
                "roots": [{"id": project_id}],
                "resolution": {
                    project_id: {
                        "name": "budget-analyzer-api-tests",
                        "version": "0.1.0",
                        "kind": "package",
                        "source": {"editable": "/secret/workspace"},
                        "dependencies": [{"id": package_id}],
                    },
                    package_id: {
                        "name": "Example_Package",
                        "version": "1.2.3",
                        "kind": "package",
                        "source": {"registry": "https://pypi.org/simple"},
                        "dependencies": [],
                    },
                },
            }
        )
    )

    assert tree.packages == (PackageRecord("example-package", "1.2.3"),)
    serialized = json.dumps(tree.document)
    assert "/secret/workspace" not in serialized
    assert "project:budget-analyzer-api-tests==0.1.0" in serialized


def test_successful_output_contract_rejects_missing_empty_and_extra_files(tmp_path: Path) -> None:
    for name in SUCCESSFUL_OUTPUTS:
        (tmp_path / name).write_text("evidence\n", encoding="utf-8")
    validate_complete_outputs(tmp_path)

    (tmp_path / "parity.json").unlink()
    with pytest.raises(DependencyEvidenceError, match=r"parity\.json"):
        validate_complete_outputs(tmp_path)

    (tmp_path / "parity.json").touch()
    (tmp_path / "unexpected.txt").write_text("extra\n", encoding="utf-8")
    with pytest.raises(DependencyEvidenceError, match=r"unexpected\.txt"):
        validate_complete_outputs(tmp_path)


def test_workflow_upload_allowlist_matches_successful_output_contract() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow_value = yaml.safe_load(
        (root / ".github/workflows/dependency-evidence.yml").read_text(encoding="utf-8")
    )
    upload = workflow_value["jobs"]["evidence"]["steps"][-1]["with"]
    paths = tuple(line.strip() for line in upload["path"].splitlines() if line.strip())

    assert {Path(path).name for path in paths} == set(SUCCESSFUL_OUTPUTS)
    assert all("*" not in path for path in paths)
    assert upload["if-no-files-found"] == "error"
    assert upload["retention-days"] == 7


def test_diagnostic_redaction_covers_headers_assignments_urls_and_tokens() -> None:
    raw = (
        "Authorization: Bearer-secret password=hunter2 "
        "https://user:pass@example.test/simple "
        "github_pat_abcdefghijklmnopqrstuvwxyz123456"
    )

    redacted = redact_text(raw)

    assert "Bearer-secret" not in redacted
    assert "hunter2" not in redacted
    assert "user:pass" not in redacted
    assert "github_pat_" not in redacted
    assert redacted.count("<redacted>") == 4
