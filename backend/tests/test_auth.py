import os
import unittest

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


class SingleAccountAuthTest(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["ACCESS_PASSPHRASE"] = "correct-passphrase"
        get_settings.cache_clear()
        self.client = TestClient(create_app(), follow_redirects=False)

    def tearDown(self) -> None:
        get_settings.cache_clear()

    def test_api_requires_session(self) -> None:
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Authentication required")

    def test_console_redirects_to_login_without_session(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers["location"].startswith("/login"))

    def test_login_session_and_logout(self) -> None:
        failed = self.client.post("/api/auth/login", json={"passphrase": "wrong"})
        self.assertEqual(failed.status_code, 401)

        login = self.client.post("/api/auth/login", json={"passphrase": "correct-passphrase"})
        self.assertEqual(login.status_code, 200)
        self.assertTrue(login.json()["authenticated"])

        authenticated = self.client.get("/api/health")
        self.assertEqual(authenticated.status_code, 200)

        logout = self.client.post("/api/auth/logout")
        self.assertEqual(logout.status_code, 200)
        self.assertFalse(logout.json()["authenticated"])

        protected = self.client.get("/api/health")
        self.assertEqual(protected.status_code, 401)


if __name__ == "__main__":
    unittest.main()
