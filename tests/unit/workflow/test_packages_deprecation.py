"""Tests for the `packages.*` deprecation warning.

`packages.profile` / `packages.additional` are still parsed (existing configs
like ploston-config.yaml ship `default_profile: standard`) but have no runtime
effect — the sandbox import allowlist is fixed. Parsing them must emit a
deprecation warning while still parsing successfully.
"""

import logging

from ploston_core.workflow.parser import parse_workflow_yaml

_WF_WITH_PROFILE = """
name: pkg-deprecation-wf
packages:
  profile: standard
  additional:
    - requests
steps:
  - id: x
    code: "pass"
"""

_WF_WITHOUT_PACKAGES = """
name: no-pkg-wf
steps:
  - id: x
    code: "pass"
"""


def test_packages_profile_emits_deprecation_warning(caplog) -> None:
    """Parsing a workflow with packages.profile emits a deprecation warning."""
    with caplog.at_level(logging.WARNING):
        wf = parse_workflow_yaml(_WF_WITH_PROFILE)

    # Still parses successfully, preserving the parsed values.
    assert wf.packages is not None
    assert wf.packages.profile == "standard"
    assert wf.packages.additional == ["requests"]

    # And warns that the fields have no runtime effect.
    messages = " ".join(r.getMessage() for r in caplog.records).lower()
    assert "no runtime effect" in messages or "no effect" in messages
    assert "packages" in messages


def test_no_packages_block_no_warning(caplog) -> None:
    """A workflow without a packages block must not emit the deprecation warning."""
    with caplog.at_level(logging.WARNING):
        wf = parse_workflow_yaml(_WF_WITHOUT_PACKAGES)

    assert wf.packages is None
    messages = " ".join(r.getMessage() for r in caplog.records).lower()
    assert "no runtime effect" not in messages
