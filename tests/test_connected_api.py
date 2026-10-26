import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from fastapi.testclient import TestClient
from core.auth import require_user
from main import create_app

class ConnectedAPITests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        def identity(request):
            return 'fixture-user'
        from fastapi import Request
        def user(request: Request):
            request.state.access_token = 'fixture-token'
            return 'fixture-user'
        self.app.dependency_overrides[require_user] = user
        self.client = TestClient(self.app)

    def test_health_configuration_is_boolean(self):
        response = self.client.get('/health')
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json()['ai_configured'], bool)

    def test_create_rejects_client_owner_and_invalid_initial_status(self):
        payload = dict(company='Example', job_title='Analyst', job_description='A sufficiently long job description')
        with patch('routers.applications.user_client') as database:
            for extra in [{'user_id':'another-user'}, {'initial_status':'offer'}]:
                self.assertEqual(self.client.post('/api/v1/applications', json=payload | extra).status_code, 422)
            database.assert_not_called()

    def test_create_uses_atomic_rpc_with_authenticated_token(self):
        database = Mock()
        database.rpc.return_value.execute.return_value.data = {'id':'created'}
        with patch('routers.applications.user_client',return_value=database) as factory, patch('routers.applications.configured',return_value=True), patch('routers.applications.sync_account') as sync:
            response = self.client.post('/api/v1/applications',json=dict(company='Example',job_title='Analyst',job_description='A sufficiently long job description'))
        self.assertEqual(response.status_code,201)
        sync.assert_called_once_with('fixture-user','job_saved','created')
        factory.assert_called_once_with('fixture-token')
        args=database.rpc.call_args.args
        self.assertEqual(args[0],'create_application')
        self.assertNotIn('user_id',args[1]['p_payload'])

    def test_avatar_rejects_mime_spoof_before_storage(self):
        with patch('routers.profile.user_client') as database:
            response=self.client.post('/api/v1/profile/avatar',files={'file':('bad.png',b'not an image','image/png')})
        self.assertEqual(response.status_code,415)
        database.assert_not_called()
