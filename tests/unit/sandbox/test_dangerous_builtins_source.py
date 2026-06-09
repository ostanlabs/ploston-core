"""L-8: DANGEROUS_BUILTINS single-source-of-truth + dual-purpose invariants.

After H-2 the sandbox switched to a fail-CLOSED allowlist (``SAFE_BUILTINS``).
``DANGEROUS_BUILTINS`` is intentionally retained, but ONLY as a
documentation/lint surface (workflow schema-gen + the static
``check_forbidden_builtins`` validator) — it is no longer the runtime
enforcement mechanism.

These tests lock in that contract so the two-source-of-truth confusion the
finding worried about cannot silently re-appear:
  * there is exactly one definition, imported by every consumer;
  * the denylist never overlaps the allowlist (lint can't flag an
    actually-permitted builtin, and the allowlist can't admit a
    deny-listed one);
  * enforcement is the allowlist, unchanged by anything here.
"""

from ploston_core.sandbox.sandbox import DANGEROUS_BUILTINS, SAFE_BUILTINS


class TestDangerousBuiltinsSingleSource:
    """The denylist lives in exactly one module and every consumer imports it."""

    def test_canonical_definition_is_in_sandbox_module(self) -> None:
        """The symbol resolves from its canonical home unchanged."""
        assert isinstance(DANGEROUS_BUILTINS, set)
        assert DANGEROUS_BUILTINS  # non-empty

    def test_schema_generator_consumes_same_object(self) -> None:
        """workflow schema-gen imports the same identity (no second copy)."""
        # The local import inside generate_workflow_schema() must resolve to
        # the sandbox definition — assert via the module the generator imports
        # from, proving there's no shadow definition in the workflow package.
        from ploston_core.sandbox import sandbox as sandbox_mod
        from ploston_core.workflow import schema_generator

        assert schema_generator  # importable
        assert "DANGEROUS_BUILTINS" not in vars(schema_generator), (
            "schema_generator must not define its own DANGEROUS_BUILTINS; "
            "it must import the canonical one from sandbox.sandbox"
        )
        assert sandbox_mod.DANGEROUS_BUILTINS is DANGEROUS_BUILTINS

    def test_no_second_definition_in_workflow_package(self) -> None:
        """tools.py / validator.py must import, never redefine, the denylist."""
        from ploston_core.workflow import tools, validator

        assert "DANGEROUS_BUILTINS" not in vars(tools)
        assert "DANGEROUS_BUILTINS" not in vars(validator)


class TestDenylistAllowlistDisjoint:
    """The lint denylist and the enforcement allowlist must never overlap."""

    def test_no_overlap(self) -> None:
        assert SAFE_BUILTINS & DANGEROUS_BUILTINS == set()

    def test_known_dangerous_names_blocked(self) -> None:
        """Core escape vectors are denylisted and absent from the allowlist."""
        for name in ("eval", "exec", "compile", "open", "__import__"):
            assert name in DANGEROUS_BUILTINS
            assert name not in SAFE_BUILTINS


class TestEnforcementIsAllowlist:
    """Enforcement is the allowlist; the denylist does not gate the scope."""

    def test_dangerous_builtins_are_absent_from_runtime_scope(self) -> None:
        """A denylisted name is simply not in the fail-closed scope.

        This is the real control: even a dangerous builtin NOT present in
        DANGEROUS_BUILTINS would be excluded, because only SAFE_BUILTINS is
        admitted. Verify a denylisted name (open) is excluded by the allowlist.
        """
        assert "open" not in SAFE_BUILTINS

    def test_safe_staple_builtins_remain_allowed(self) -> None:
        """Allowlist enforcement is unchanged — common safe builtins present."""
        for name in ("len", "range", "print", "isinstance", "dict", "sum"):
            assert name in SAFE_BUILTINS
