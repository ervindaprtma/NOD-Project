"""§9.10 test_group_batching.py

Tests that _run_group_query is called ONCE per distinct (data_source,
site_name, evaluation_window_minutes) key, even when multiple rules
share that key — across single rules, composite clauses, and mixed
cases (§9.4 fix).

Skeleton — full implementation needs the same test infra as
test_alert_state_machine.py.
"""
import pytest


@pytest.mark.skip(reason="§9.10 scaffold: needs test DB + mocked _run_group_query")
def test_single_rules_share_group_key():
    """Two single rules with the same (ds, site, window) → 1 OS call total."""
    pass


@pytest.mark.skip(reason="§9.10 scaffold")
def test_composite_clauses_share_group_key():
    """One composite rule with 2 clauses sharing the same key → 1 OS call
    (down from 2 pre-§9.4)."""
    pass


@pytest.mark.skip(reason="§9.10 scaffold")
def test_single_plus_composite_share_group_key():
    """1 single rule + 1 composite rule on the same (ds, site, window) → 1
    OS call total, not 2."""
    pass


@pytest.mark.skip(reason="§9.10 scaffold")
def test_different_keys_make_separate_calls():
    """Distinct (ds, site, window) keys → distinct OS calls. No
    over-fetching."""
    pass


@pytest.mark.skip(reason="§9.10 scaffold")
def test_composite_different_clause_keys_make_separate_calls():
    """A composite rule with 2 clauses on different keys → 2 OS calls.
    Verify the cache does not over-share."""
    pass
