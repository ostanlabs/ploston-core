"""Spec tests for forwarded client-cert identity parsing (V-1 / DEC-200).

The CP accepts a runner in proxy mode only if the trusted proxy's forwarded
``X-Runner-Client-CN`` identifies the registering runner. Proxies forward that
identity in two shapes:

  * nginx-ingress ``$ssl_client_s_dn_cn`` -> a bare CN (``runner-foo``);
  * Caddy ``{http.request.tls.client.subject}`` -> a full RFC 4514 subject DN
    (``CN=runner-foo,OU=...,O=Ploston,C=US``) — because Caddy has no CN-only
    placeholder.

These assert the CP normalises BOTH to the CN and matches the runner, and that
the original-bug failure modes (literal unresolved placeholder, empty value)
are rejected.
"""

from ploston_core.api.routers.runner_static import _cn_matches_runner, _extract_cn


class TestExtractCn:
    def test_bare_cn_returned_unchanged(self):
        assert _extract_cn("runner-foo") == "runner-foo"

    def test_full_subject_dn_yields_cn(self):
        dn = "CN=runner-foo,OU=runner-id-1,O=Ploston,C=US"
        assert _extract_cn(dn) == "runner-foo"

    def test_dn_with_cn_not_first_component(self):
        dn = "O=Ploston,OU=runner-id-1,CN=runner-bar,C=US"
        assert _extract_cn(dn) == "runner-bar"

    def test_cn_key_is_case_insensitive(self):
        assert _extract_cn("cn=runner-foo,O=Ploston") == "runner-foo"

    def test_escaped_comma_in_cn_preserved(self):
        # RFC 4514 escapes a comma inside a value as "\,".
        assert _extract_cn("CN=runner\\,weird,O=Ploston") == "runner,weird"

    def test_unresolved_placeholder_passes_through_then_fails_match(self):
        # The original bug: Caddy's bogus subject_cn placeholder forwarded
        # literally. It has no '=', so it is returned unchanged...
        literal = "{http.request.tls.client.subject_cn}"
        assert _extract_cn(literal) == literal


class TestCnMatchesRunner:
    name = "v1-runner"
    rid = "runner-id-xyz"

    def test_bare_cn_from_nginx_matches(self):
        assert _cn_matches_runner(f"runner-{self.name}", self.name, self.rid)

    def test_full_dn_from_caddy_matches(self):
        dn = f"CN=runner-{self.name},OU={self.rid},O=Ploston,C=US"
        assert _cn_matches_runner(dn, self.name, self.rid)

    def test_raw_name_matches(self):
        assert _cn_matches_runner(self.name, self.name, self.rid)

    def test_empty_rejected(self):
        assert not _cn_matches_runner("", self.name, self.rid)

    def test_unresolved_placeholder_rejected(self):
        # ...and crucially, the literal placeholder must NOT match — otherwise
        # the original bug would have silently "passed" for every runner.
        assert not _cn_matches_runner("{http.request.tls.client.subject_cn}", self.name, self.rid)

    def test_other_runner_dn_rejected(self):
        dn = "CN=runner-someone-else,OU=other,O=Ploston,C=US"
        assert not _cn_matches_runner(dn, self.name, self.rid)
