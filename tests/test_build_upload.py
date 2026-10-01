import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from build_upload import match_rule  # noqa: E402

# load_rules() sorts longest-first; these fixtures are already in that order.
RULES = [
    ("google workspace", "Dues and Subscriptions"),
    ("google play", "Dues and Subscriptions"),
    ("google ads", "Advertising and Promotion"),
    ("google", "Advertising and Promotion"),
]


def test_exact_match():
    assert match_rule(RULES, "Google Ads") == "Advertising and Promotion"


def test_rule_inside_payee_wins_longest_first():
    assert match_rule(RULES, "Google Workspace_acme") == "Dues and Subscriptions"


def test_short_payee_does_not_match_longer_rule():
    # "Google" (Google Ads charges) must not match the "Google Play" rule and
    # land in Dues and Subscriptions.
    assert match_rule(RULES, "Google") == "Advertising and Promotion"
    assert match_rule([("google play", "Dues and Subscriptions")], "Google") is None


def test_no_rule_returns_none():
    assert match_rule(RULES, "Amazon") is None
