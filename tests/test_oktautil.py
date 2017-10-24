import okta
import os
import pickle
import requests
import unittest

from io import BytesIO
from unittest.mock import call, Mock, patch

from .context import bmx
from bmx.constants import OKTA_API_TOKEN, OKTA_BASE_URL
import bmx.oktautil

SESSION_ID = 'session id'
COOKIES = 'cookies'
APP_URL = 'app url'
SAML_VALUE = 'saml value'

USERNAME = 'john'
PASSWORD = 'cats'
STATE = 'this is the state'
FACTOR_ID = 'factor id'
TOTP_CODE = 'totp code'

class MockCookie():
    def __init__(self, cookies=None):
        if cookies is None:
            cookies = {}
        self.cookies = cookies
    def get_dict(self):
        return self.cookies

class MockSession():
    def __init__(self):
        pass
    def validate_session(self, x):
        return x

def create_auth_response_mock(factor_type):
    props = {
        'stateToken': STATE,
        'status': 'MFA_REQUIRED',
        'embedded.factors': [Mock(id = FACTOR_ID, factorType = factor_type)]
    }
    return Mock(**props)

def create_state_mock(username, cookies):
    return {'username': username, 'cookies': cookies}

class OktaUtilTests(unittest.TestCase):
    @patch('getpass.getpass', return_value=PASSWORD)
    @patch('builtins.input', return_value=TOTP_CODE)
    @patch('requests.get')
    @patch('bmx.oktautil.create_sessions_client')
    @patch('bmx.oktautil.create_auth_client')
    def test_authenticate_should_follow_full_totp_mfa_flow(self, mock_auth_client, *args):
        mock_auth_client.return_value.authenticate.return_value = create_auth_response_mock('token:software:totp')

        bmx.oktautil.get_new_session(USERNAME)

        mock_auth_client.return_value.authenticate.assert_called_once_with(
            USERNAME,
            PASSWORD
        )
        mock_auth_client.return_value.auth_with_factor.assert_called_once_with(
            STATE,
            FACTOR_ID,
            TOTP_CODE
        )

    @patch('getpass.getpass', return_value=PASSWORD)
    @patch('builtins.input', return_value=TOTP_CODE)
    @patch('requests.get')
    @patch('bmx.oktautil.create_sessions_client')
    @patch('bmx.oktautil.create_auth_client')
    def test_authenticate_should_follow_full_sms_mfa_flow(self, mock_auth_client, *args):
        mock_auth_client.return_value.authenticate.return_value = create_auth_response_mock('sms')

        bmx.oktautil.get_new_session(USERNAME)

        mock_auth_client.return_value.authenticate.assert_called_once_with(
            USERNAME,
            PASSWORD
        )
        self.assertEqual(mock_auth_client.return_value.auth_with_factor.call_count, 2)
        mock_auth_client.return_value.auth_with_factor.assert_has_calls([
            call(STATE, FACTOR_ID, None),
            call(STATE, FACTOR_ID, TOTP_CODE)
        ])

    @patch('getpass.getpass')
    @patch('bmx.oktautil.create_auth_client')
    def test_authenticate_should_fail_for_unimplemented_factor(self, mock_auth_client, *args):
        mock_auth_client.return_value.authenticate.return_value = create_auth_response_mock('unimplemented-factor')
        self.assertRaises(NotImplementedError, bmx.oktautil.get_new_session, USERNAME)

    @patch('bmx.oktautil.get_new_session')
    @patch('bmx.oktautil.get_cached_session', return_value=(SESSION_ID, COOKIES))
    def test_get_okta_session_returns_cached_session(self, mock_cached, mock_new):
        self.assertEqual((SESSION_ID, COOKIES), bmx.oktautil.get_okta_session(USERNAME))
        self.assertFalse(mock_new.called)

    @patch('bmx.oktautil.set_cached_session')
    @patch('bmx.oktautil.get_new_session', return_value=(SESSION_ID, COOKIES, USERNAME))
    @patch('bmx.oktautil.get_cached_session', return_value=(SESSION_ID, None))
    def test_get_okta_session_gets_new_session_if_no_cache(self, mock_cached, mock_new, *args):
        self.assertEqual((SESSION_ID, COOKIES), bmx.oktautil.get_okta_session(USERNAME))

    def test_create_auth_client_should_pass(self):
        okta.AuthClient.__init__ = Mock(return_value=None)

        bmx.oktautil.create_auth_client()

        okta.AuthClient.__init__.assert_called_once_with(
            OKTA_BASE_URL,
            OKTA_API_TOKEN,
            headers={
                'Authorization': None
            }
        )

    @patch('bmx.oktautil.cookie_string', return_value=COOKIES)
    def test_create_sessions_client_should_pass_cookies(self, *args):
        okta.SessionsClient.__init__ = Mock(return_value=None)

        bmx.oktautil.create_sessions_client(COOKIES)

        okta.SessionsClient.__init__.assert_called_once_with(
            OKTA_BASE_URL,
            OKTA_API_TOKEN,
            headers={
                'Authorization': None,
                'Cookie': COOKIES
            }
        )

    def test_create_users_client_should_pass_session_id_always(self):
        okta.UsersClient.__init__ = Mock(return_value=None)

        bmx.oktautil.create_users_client(MockCookie({'sid': SESSION_ID}))

        okta.UsersClient.__init__.assert_called_once_with(
            OKTA_BASE_URL,
            OKTA_API_TOKEN,
            headers={
                'Authorization': None,
                'Cookie': 'sid={0}'.format(SESSION_ID)
            }
        )

    @patch('requests.Response')
    def test_connect_to_app_should_return_saml_response_when_one_exists(self, mock_response):
        requests.get = Mock(return_value=mock_response)

        mock_response.raise_for_status.return_value = None
        mock_response.content = """
            <html>
                <head/>
                <body>
                    <input name="SAMLResponse" value="{}"/>
                </body>
            </html>""".format(SAML_VALUE)

        expected_cookies = MockCookie({'sid': SESSION_ID})
        self.assertEqual(
            SAML_VALUE,
            bmx.oktautil.connect_to_app(APP_URL, expected_cookies)
        )

        requests.get.assert_called_once_with(
            APP_URL,
            cookies=expected_cookies
        )

        mock_response.raise_for_status.assert_called_with()

    @patch('builtins.open', return_value=BytesIO())
    @patch('bmx.fileutil.open_path_secure')
    def test_cached_session_serializes(self,
            mock_open_file_secure,
            mock_open,
            *args):

        username, cookies = 'username', 'expected_cached_object'

        bytesIo = mock_open.return_value
        bytesIo.close = Mock() # BytesIO's value is unavailable after a close

        bmx.oktautil.set_cached_session(username, cookies)

        self.assertEqual(create_state_mock(username, cookies),
                pickle.loads(bytesIo.getvalue()))

    @patch('bmx.oktautil.create_sessions_client', return_value=MockSession())
    @patch('pickle.load', return_value=create_state_mock('username', MockCookie({'sid': 'expectedSession'})))
    @patch('builtins.open')
    def test_get_cache_session_exists(self, mock_open, mock_pickle, mock_session_client):
        for username in ['username', None]:
            with self.subTest(username=username):
                session, cookies = bmx.oktautil.get_cached_session(username)
                self.assertTrue(mock_pickle.called)
                self.assertTrue(mock_session_client.called)
                self.assertEqual('expectedSession', session)
                self.assertEqual(cookies.cookies, {'sid': 'expectedSession'})

    @patch('pickle.load', return_value=create_state_mock('username', MockCookie({'sid': 'expectedSession'})))
    @patch('builtins.open')
    def test_get_cache_session_wrong_username(self, mock_open, mock_pickle):
        session, cookies = bmx.oktautil.get_cached_session('wrong-username')
        self.assertTrue(mock_pickle.called)
        self.assertIsNone(session)
        self.assertIsNone(cookies)

    def test_cookies_to_string_when_none(self):
        cookie_string = bmx.oktautil.cookie_string(None)
        self.assertEqual('', cookie_string)

    def test_cookies_to_string_when_present(self):
        cookie_string = bmx.oktautil.cookie_string(
            MockCookie({'first': 'first', 'second': 'second'}))
        cookie_parts = cookie_string.split(';')
        self.assertEqual(2, len(cookie_parts))
        self.assertIn('first=first', cookie_parts)
        self.assertIn('second=second', cookie_parts)

if __name__ == '__main__':
    unittest.main()
