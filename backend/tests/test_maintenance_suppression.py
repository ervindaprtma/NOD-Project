"""§9.10 test_maintenance_suppression.py

Tests the maintenance-window suppression in evaluate_all_rules:
- §5.3 caveat: suppression is by site_name only. Rules with no site_name
  (HA, throughput, VPN) are never suppressed, even when a maintenance
  window is active for any site.

Skeleton — full implementation needs the same test infra as
test_alert_state_machine.py.
"""
import pytest


@pytest.mark.skip(reason="§9.10 scaffold: needs test DB + MaintenanceWindow rows")
def test_rule_in_maintenance_is_skipped():
    """Active window for site='DC' → rules with site_name='DC' are NOT
    state-transitioned, NOT logged, NOT notified."""
    pass


@pytest.mark.skip(reason="§9.10 scaffold")
def test_rule_without_site_name_never_suppressed():
    """§5.3 caveat: a rule with site_name=None is never suppressed, even
    during a maintenance window for any site."""
    pass


@pytest.mark.skip(reason="§9.10 scaffold")
def test_maintenance_in_past_does_not_skip():
    """A maintenance window that has ended (ends_at < now) does NOT
    suppress any rules."""
    pass


@pytest.mark.skip(reason="§9.10 scaffold")
def test_maintenance_in_future_does_not_skip():
    """A maintenance window that hasn't started (starts_at > now) does
    NOT suppress any rules yet."""
    pass


@pytest.mark.skip(reason="§9.10 scaffold")
def test_overlapping_maintenance_windows_union():
    """Two active windows for different sites both apply. A rule with
    site_name matching either is suppressed."""
    pass
