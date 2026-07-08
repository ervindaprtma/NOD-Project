# §9.10 — pytest configuration placeholder

# This conftest.py is a stub. Once test infrastructure (in-memory SQLite +
# mocked OpenSearch) is in place, fixtures for db_session, sample_rule,
# and a mocked _run_group_query belong here.
#
# Ponytail: don't add the fixtures until a test needs them. The two
# pure tests (test_secret_roundtrip, test_severity_gating) need no
# fixtures; the 4 integration stubs are @pytest.mark.skip'd for now.
