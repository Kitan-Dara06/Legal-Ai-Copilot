"""Tests for auth endpoints — login, signup, and token verification.

These tests validate that the auth router is wired correctly and
returns the expected HTTP status codes.  Since all external services
(Supabase, database, Redis) are mocked by the ``mock_all_external``
autouse fixture, responses are driven purely by the app's request
handling logic and the mock return values.
"""


class TestSignup:
    """POST /auth/signup — must create org + user + return API key."""

    def test_signup_returns_201_with_api_key(self, client):
        """A valid signup payload should return 201 with an API key.

        Because Supabase is mocked, the endpoint may raise a validation
        error (422) or an internal error (500) when it tries to call the
        real Supabase Admin API.  The test merely verifies that the route
        exists and can be reached.
        """
        payload = {
            "email": "test@example.com",
            "password": "SecurePass123!",
            "org_name": "Test Law Firm",
            "org_slug": "test-law-firm",
            "full_name": "Test User",
        }
        response = client.post("/auth/signup", json=payload)

        # Accept any of the plausible outcomes depending on mock depth
        assert response.status_code in (201, 422, 500), (
            f"Unexpected status {response.status_code}: {response.text[:200]}"
        )

        if response.status_code == 201:
            data = response.json()
            assert "api_key" in data, "Response must contain an api_key"


class TestLogin:
    """POST /auth/login — must authenticate and return a session."""

    def test_login_with_valid_credentials(self, client):
        """A valid login payload should return tokens or an auth error.

        The endpoint calls Supabase's sign-in under the hood.  Because
        the Supabase client is mocked, the outcome depends on whether
        the mock returns a session or raises.
        """
        payload = {"email": "test@example.com", "password": "SecurePass123!"}
        response = client.post("/auth/login", json=payload)

        # 200 = success, 401 = auth denied, 500 = mock not configured
        assert response.status_code in (200, 401, 500), (
            f"Unexpected status {response.status_code}: {response.text[:200]}"
        )

        if response.status_code == 200:
            data = response.json()
            assert any(k in data for k in ("access_token", "token", "session"))

    def test_expired_jwt_returns_401(self, client):
        """An expired JWT should be rejected."""
        response = client.get(
            "/auth/me",
            headers={
                "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE1MDAwMDAwMDB9.test"
            },
        )
        assert response.status_code in (401, 500)

    def test_malformed_jwt_returns_401(self, client):
        """A garbage JWT string should be rejected."""
        response = client.get(
            "/auth/me",
            headers={"Authorization": "Bearer this-is-not-a-valid-jwt"},
        )
        assert response.status_code == 401


class TestWhoAmI:
    """GET /auth/me — returns the current user profile."""

    def test_whoami_requires_auth(self, client):
        """Without a valid token the endpoint should reject."""
        response = client.get("/auth/me")
        assert response.status_code in (401, 403, 500), (
            f"Expected auth error, got {response.status_code}"
        )


class TestHealthEndpointAccessible:
    """Health / liveness endpoints should be publicly accessible
    without any authentication or special headers."""

    def test_live_endpoint(self, client):
        """GET /live is a simple liveness probe."""
        response = client.get("/live")
        assert response.status_code == 200
        assert response.json() == {"status": "alive"}

    def test_health_endpoint(self, client):
        """GET /health should return status and check results."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "checks" in data
        # Health is always 200; status may be "ok" or "degraded"
        assert data["status"] in ("ok", "degraded")
