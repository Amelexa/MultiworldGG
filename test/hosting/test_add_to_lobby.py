"""Exercise the options route against an isolated SQLite database."""
import unittest
import uuid
from unittest.mock import patch
from pony.orm import db_session
from WebHostLib import app
from WebHostLib.models import db, Lobby, LobbyPlayer, LobbyYaml
from WebHostLib.options import add_to_lobby


class AddToLobbyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.bind(provider='sqlite', filename=':memory:')
        db.generate_mapping(create_tables=True)
        app.config.update(TESTING=True, SECRET_KEY='isolated-test')

    def test_add_and_reject_duplicate_without_orm_decompile_error(self):
        from flask import session
        owner = uuid.uuid4()
        with db_session:
            lobby = Lobby(title='Test', owner=owner, max_yamls_per_player=3)
            LobbyPlayer(lobby=lobby, session_id=owner, player_name='Tester')
            lobby_id = str(lobby.id)
        def post():
            with app.test_request_context('/games/Balatro/add-to-lobby', method='POST',
                                          data={'lobby-id': lobby_id}), db_session:
                session['_id'] = owner
                # Bypass limiter only; run the actual handler and real ORM query.
                return add_to_lobby.__wrapped__('Balatro')
        with patch('WebHostLib.options._parse_player_options_form',
                   return_value=('Tester', {'name': 'Tester', 'game': 'Balatro'}, None)), \
             patch('WebHostLib.check.roll_options', return_value=({}, {})):
            response = post()
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.get_json()['success'])
            response, status = post()
            self.assertEqual(status, 400)
            self.assertIn('already used', response.get_json()['error'])
        with db_session:
            self.assertEqual(LobbyYaml.select().count(), 1)
