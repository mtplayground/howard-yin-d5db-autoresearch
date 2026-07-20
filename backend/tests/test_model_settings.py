import unittest

from app.core.config import Settings
from app.services.model_settings import decrypt_api_key, encrypt_api_key


class ModelSettingsTest(unittest.TestCase):
    def test_encrypts_and_decrypts_api_key(self) -> None:
        settings = Settings(
            database_url="postgresql://user:pass@example/db",
            access_passphrase="single-account-secret",
        )

        encrypted = encrypt_api_key(settings, "sk-test")

        self.assertNotEqual(encrypted, "sk-test")
        self.assertEqual(decrypt_api_key(settings, encrypted), "sk-test")


if __name__ == "__main__":
    unittest.main()
