"""Tests for the dashboard authentication gate.

These exist because of a real incident: MERIDIAN_DASHBOARD_PASSWORD was
absent from the Brain container, the gate treated "no password" as "auth
disabled", and the whole wiki (40 client records included) was served
anonymously on the public internet.

The rule these tests protect is simple and worth stating plainly: a
configuration mistake must never be the thing that opens the door. Every
test below is a way that door could be pushed open.

Run with: python -m pytest tests/test_auth.py -v
"""

import importlib
import os
import tempfile

import pytest

# Point MERIDIAN_ROOT at a temp dir before importing so the module does
# not read from /meridian/ (which does not exist locally).
_TMPDIR = tempfile.mkdtemp()
os.environ["MERIDIAN_ROOT"] = _TMPDIR

PASSWORD = "correct-horse-battery-staple"

# A page that must never be readable without a session.
PROTECTED_PATH = "/clients/"


def _load_app(password: str | None, allow_anonymous: bool = False):
    """Reload web.app with a given auth configuration.

    DASHBOARD_PASSWORD is read at import time, so switching configuration
    means reloading the module rather than patching an attribute.
    """
    if password is None:
        os.environ.pop("MERIDIAN_DASHBOARD_PASSWORD", None)
    else:
        os.environ["MERIDIAN_DASHBOARD_PASSWORD"] = password

    if allow_anonymous:
        os.environ["MERIDIAN_DASHBOARD_ALLOW_ANONYMOUS"] = "1"
    else:
        os.environ.pop("MERIDIAN_DASHBOARD_ALLOW_ANONYMOUS", None)

    import web.app as app_module
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    return app_module


@pytest.fixture
def configured_client():
    """Client for a correctly configured dashboard."""
    module = _load_app(PASSWORD)
    with module.app.test_client() as client:
        yield client


@pytest.fixture
def unconfigured_client():
    """Client for a dashboard with no password set (the incident case)."""
    module = _load_app(None)
    with module.app.test_client() as client:
        yield client


# =========================================================================
# Fail-closed behavior: the actual incident
# =========================================================================

class TestFailsClosedWithoutPassword:
    def test_protected_page_is_not_served_anonymously(self, unconfigured_client):
        response = unconfigured_client.get(PROTECTED_PATH)
        assert response.status_code == 503
        assert b"not configured" in response.data

    def test_root_is_not_served_anonymously(self, unconfigured_client):
        assert unconfigured_client.get("/").status_code == 503

    def test_admin_page_is_not_served_anonymously(self, unconfigured_client):
        assert unconfigured_client.get("/admin/").status_code == 503

    def test_admin_stats_json_is_not_served_anonymously(self, unconfigured_client):
        """This endpoint leaked disk, memory, uptime, and git state."""
        assert unconfigured_client.get("/admin/stats.json").status_code == 503

    def test_api_stats_is_not_served_anonymously(self, unconfigured_client):
        assert unconfigured_client.get("/api/stats").status_code == 503

    def test_blank_password_is_not_accepted_as_login(self, unconfigured_client):
        """hmac.compare_digest("", "") is True, so this must be guarded.

        Without an explicit check, an unconfigured container would treat an
        empty form submission as a valid login.
        """
        response = unconfigured_client.post("/login", data={"password": ""})
        assert response.status_code == 503

    def test_any_password_is_rejected_when_unconfigured(self, unconfigured_client):
        response = unconfigured_client.post("/login", data={"password": "anything"})
        assert response.status_code == 503


# =========================================================================
# Normal operation with a password configured
# =========================================================================

class TestConfiguredAuth:
    def test_anonymous_request_redirects_to_login(self, configured_client):
        response = configured_client.get(PROTECTED_PATH)
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_login_page_is_reachable_anonymously(self, configured_client):
        assert configured_client.get("/login").status_code == 200

    def test_correct_password_grants_access(self, configured_client):
        response = configured_client.post("/login", data={"password": PASSWORD})
        assert response.status_code == 302

        follow_up = configured_client.get(PROTECTED_PATH)
        assert follow_up.status_code == 200

    def test_wrong_password_does_not_grant_access(self, configured_client):
        configured_client.post("/login", data={"password": "wrong"})
        response = configured_client.get(PROTECTED_PATH)
        assert response.status_code == 302

    def test_logout_revokes_access(self, configured_client):
        configured_client.post("/login", data={"password": PASSWORD})
        assert configured_client.get(PROTECTED_PATH).status_code == 200

        configured_client.get("/logout")
        assert configured_client.get(PROTECTED_PATH).status_code == 302

    def test_json_request_gets_401_rather_than_a_redirect(self, configured_client):
        """API clients need a status code, not an HTML login page."""
        response = configured_client.get(
            "/api/stats", headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 401


# =========================================================================
# Deliberate opt-out for local development
# =========================================================================

class TestAnonymousOptOut:
    def test_allow_anonymous_env_var_opens_access(self):
        module = _load_app(None, allow_anonymous=True)
        with module.app.test_client() as client:
            assert client.get(PROTECTED_PATH).status_code == 200

    def test_opt_out_requires_exactly_one(self):
        """Only "1" counts. "true", "yes", and friends must not open it."""
        for value in ("true", "yes", "0", "", "TRUE"):
            os.environ["MERIDIAN_DASHBOARD_ALLOW_ANONYMOUS"] = value
            os.environ.pop("MERIDIAN_DASHBOARD_PASSWORD", None)
            import web.app as app_module
            importlib.reload(app_module)
            app_module.app.config["TESTING"] = True
            with app_module.app.test_client() as client:
                assert client.get(PROTECTED_PATH).status_code == 503, (
                    f"value {value!r} should not disable auth"
                )
        os.environ.pop("MERIDIAN_DASHBOARD_ALLOW_ANONYMOUS", None)


# =========================================================================
# Crawler protection
# =========================================================================

class TestCrawlerProtection:
    def test_robots_txt_disallows_everything(self, configured_client):
        response = configured_client.get("/robots.txt")
        assert response.status_code == 200
        assert b"Disallow: /" in response.data

    def test_robots_txt_is_reachable_without_a_session(self, unconfigured_client):
        assert unconfigured_client.get("/robots.txt").status_code == 200

    def test_noindex_header_on_every_response(self, configured_client):
        response = configured_client.get("/login")
        assert "noindex" in response.headers.get("X-Robots-Tag", "")

    def test_clickjacking_and_sniffing_headers(self, configured_client):
        response = configured_client.get("/login")
        assert response.headers.get("X-Frame-Options") == "DENY"
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
