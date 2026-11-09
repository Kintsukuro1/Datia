import unittest
from unittest.mock import patch
from app.core.config import settings
from main import validate_startup_security

class TestStartupSecurity(unittest.TestCase):
    """
    Tests ensuring that the application enforces strong security validations upon startup in production.
    """

    def test_production_fails_with_default_secret_key(self):
        """
        When ENVIRONMENT is set to 'production' and SECRET_KEY is the repository default,
        validate_startup_security must raise a RuntimeError preventing server startup.
        """
        with patch.object(settings, "ENVIRONMENT", "production"):
            with patch.object(settings, "SECRET_KEY", settings.DEFAULT_SECRET_KEY):
                with self.assertRaises(RuntimeError) as ctx:
                    validate_startup_security()
                self.assertIn("SECRET_KEY no fue configurado para producción", str(ctx.exception))

    def test_production_succeeds_with_custom_secret_key(self):
        """
        When ENVIRONMENT is set to 'production' and a unique SECRET_KEY is provided,
        validation passes cleanly.
        """
        with patch.object(settings, "ENVIRONMENT", "production"), \
             patch.object(settings, "SECRET_KEY", "super_custom_unique_prod_secret_key_998877665544332211"), \
             patch.object(settings, "CORS_ORIGINS", ["http://localhost:8080"]), \
             patch.object(settings, "FERNET_KEY", "clave-fernet-de-prueba"), \
             patch.object(settings, "DEMO_MODE", False):
            # Should not raise exception
            validate_startup_security()

    def test_development_allows_default_secret_key(self):
        """
        In local 'development' mode, the default SECRET_KEY is permitted without raising exception.
        """
        with patch.object(settings, "ENVIRONMENT", "development"):
            with patch.object(settings, "SECRET_KEY", settings.DEFAULT_SECRET_KEY):
                validate_startup_security()

    def test_production_wildcard_cors_fails_closed(self):
        """Un `*` en CORS no tiene uso legítimo en una instalación on-prem."""
        with patch.object(settings, "ENVIRONMENT", "production"), \
             patch.object(settings, "SECRET_KEY", "clave-unica-de-prueba-para-produccion"), \
             patch.object(settings, "CORS_ORIGINS", ["*"]), \
             patch.object(settings, "DEMO_MODE", False), \
             patch.object(settings, "FERNET_KEY", "clave-fernet-de-prueba"):
            with self.assertRaises(RuntimeError) as ctx:
                validate_startup_security()
            self.assertIn("CORS_ORIGINS no puede contener '*'", str(ctx.exception))

    def test_production_fails_without_fernet_key(self):
        """Sin FERNET_KEY, rotar SECRET_KEY deja ilegibles las credenciales."""
        with patch.object(settings, "ENVIRONMENT", "production"), \
             patch.object(settings, "SECRET_KEY", "clave-unica-de-prueba-para-produccion"), \
             patch.object(settings, "CORS_ORIGINS", ["http://localhost:8080"]), \
             patch.object(settings, "FERNET_KEY", None), \
             patch.object(settings, "DEMO_MODE", False):
            with self.assertRaises(RuntimeError) as ctx:
                validate_startup_security()
            self.assertIn("FERNET_KEY no fue configurada", str(ctx.exception))

if __name__ == "__main__":
    unittest.main()
