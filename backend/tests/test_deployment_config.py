from pathlib import Path
import unittest

from app.core.config import Settings
from app.core.deployment import validate_self_hosting_settings


class DeploymentConfigTest(unittest.TestCase):
    def test_valid_self_hosting_settings_pass(self) -> None:
        settings = Settings(
            app_env="production",
            self_url="https://autoresearch.example",
            database_url="postgresql://user:pass@localhost:5432/autoresearch",
            access_passphrase="strong-passphrase",
            model_api_key="model-key",
            object_storage_bucket="autoresearch-artifacts",
            object_storage_access_key_id="storage-key",
            object_storage_secret_access_key="storage-secret",
            object_storage_prefix="autoresearch/",
        )

        validation = validate_self_hosting_settings(settings)

        self.assertEqual(validation.errors, [])
        self.assertEqual(validation.warnings, [])

    def test_missing_required_self_hosting_settings_fail(self) -> None:
        settings = Settings(
            database_url="sqlite:///local.db",
            access_passphrase="",
            object_storage_bucket="",
            object_storage_access_key_id="storage-key",
            object_storage_secret_access_key="",
            discovery_default_limit=0,
        )

        validation = validate_self_hosting_settings(settings)

        keys = [check.key for check in validation.errors]
        self.assertIn("DATABASE_URL", keys)
        self.assertIn("ACCESS_PASSPHRASE", keys)
        self.assertIn("OBJECT_STORAGE_BUCKET", keys)
        self.assertIn("OBJECT_STORAGE_ACCESS_KEY_ID", keys)
        self.assertIn("DISCOVERY_DEFAULT_LIMIT", keys)
        self.assertFalse(validation.ok)

    def test_env_example_covers_all_settings_fields(self) -> None:
        env_example = Path(__file__).resolve().parents[2] / ".env.example"
        keys = {
            line.split("=", 1)[0]
            for line in env_example.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#") and "=" in line
        }
        expected_keys = {field_name.upper() for field_name in Settings.model_fields}

        self.assertEqual(expected_keys - keys, set())


if __name__ == "__main__":
    unittest.main()
