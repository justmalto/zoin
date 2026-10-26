import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from pydantic import SecretStr
from cryptography.fernet import Fernet
spec = importlib.util.spec_from_file_location('start_backend',Path(__file__).resolve().parents[1]/'scripts/start_backend.py')
module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
class DeploymentStartTests(unittest.TestCase):
    def settings(self):
        return SimpleNamespace(groq_api_key=SecretStr('fixture'),mail_encryption_key=SecretStr(Fernet.generate_key().decode()),mail_worker_key=SecretStr('x'*48),allowed_origins=['https://zoin.example'],mail_scheduler_enabled=True)
    def test_valid_production_configuration(self):
        module.validate_deployment(self.settings(),'production',8080)
    def test_missing_key_never_silently_disables_sync(self):
        settings=self.settings();settings.mail_worker_key=SecretStr('')
        with self.assertRaisesRegex(ValueError,'MAIL_WORKER_KEY'):module.validate_deployment(settings,'production',8080)
    def test_production_rejects_localhost_and_wildcard_origins(self):
        for origin in ['http://localhost:5173','https://*.vercel.app','https://example.com/path']:
            settings=self.settings();settings.allowed_origins=[origin]
            with self.assertRaisesRegex(ValueError,'ALLOWED_ORIGINS'):module.validate_deployment(settings,'production',8080)
    def test_production_requires_scheduler_on(self):
        settings=self.settings();settings.mail_scheduler_enabled=False
        with self.assertRaisesRegex(ValueError,'MAIL_SCHEDULER_ENABLED'):module.validate_deployment(settings,'production',8080)
    def test_invalid_port_rejected(self):
        with self.assertRaises(ValueError):module.validate_deployment(self.settings(),'production',0)
