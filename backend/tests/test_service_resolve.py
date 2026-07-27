"""M0: hybrid service resolver — flow.application.name first, port fallback.
See design_service_field_migration.md §3."""
from app.opensearch._common import resolve_service


def test_classified_app_wins_over_port():
    # AppID named it -> return the name verbatim, ignore the port.
    assert resolve_service("HTTPS", 443) == "HTTPS"
    assert resolve_service("POSTGRESQL", 5432) == "POSTGRESQL"


def test_unclassified_falls_back_to_port():
    # app-0 -> resolve the server port instead.
    assert resolve_service("app-0", 11105) == "Port-11105"   # ephemeral, no IANA name
    assert resolve_service("app-0", 3260) == "iscsi-target"  # IANA name recovered


def test_unclassified_detection_is_case_insensitive():
    assert resolve_service("APP-0", 443) == "https"
    assert resolve_service("Unclassified", 443) == "https"
    assert resolve_service("Unknown", 443) == "https"


def test_unclassified_and_no_port_keeps_raw_label():
    # decision 2: no usable name AND no port -> keep the label, don't invent one.
    assert resolve_service("app-0", None) == "app-0"
    assert resolve_service("Unclassified", None) == "Unclassified"
    assert resolve_service("app-0", "") == "app-0"
    assert resolve_service("app-0", 0) == "app-0"


def test_missing_app_name_falls_back_to_port():
    assert resolve_service(None, 443) == "https"
    assert resolve_service("", 3260) == "iscsi-target"


def test_nothing_usable_returns_unclassified():
    assert resolve_service(None, None) == "Unclassified"
    assert resolve_service("", "") == "Unclassified"


def test_collision_merge_by_final_string():
    # Two ephemeral ports that AppID can't name collapse to distinct Port-N labels;
    # two ports that map to the same IANA name collapse to one. The shaping code sums
    # bytes per resolved string — verify the resolver yields mergeable keys.
    labels = [resolve_service("app-0", p) for p in (11105, 11105, 3260)]
    assert labels == ["Port-11105", "Port-11105", "iscsi-target"]
