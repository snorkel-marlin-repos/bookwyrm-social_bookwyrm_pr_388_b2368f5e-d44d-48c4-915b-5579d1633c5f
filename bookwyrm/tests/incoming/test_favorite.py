import json
import pathlib
from unittest.mock import patch
from django.test import TestCase

from bookwyrm import models, incoming


class Favorite(TestCase):
    def setUp(self):
        with patch('bookwyrm.models.user.set_remote_server.delay'):
            with patch('bookwyrm.models.user.get_remote_reviews.delay'):
                self.remote_user = models.User.objects.create_user(
                    'rat', 'rat@rat.com', 'ratword',
                    local=False,
                    remote_id='https://example.com/users/rat',
                    inbox='https://example.com/users/rat/inbox',
                    outbox='https://example.com/users/rat/outbox',
                )
        self.local_user = models.User.objects.create_user(
            'mouse', 'mouse@mouse.com', 'mouseword', local=True,
            remote_id='http://local.com/user/mouse')

        self.status = models.Status.objects.create(
            user=self.local_user,
            content='Test status',
            remote_id='http://local.com/status/1',
        )

        datafile = pathlib.Path(__file__).parent.joinpath(
            '../data/ap_user.json'
        )
        self.user_data = json.loads(datafile.read_bytes())



    def test_handle_favorite(self):
        activity = {
            '@context': 'https://www.w3.org/ns/activitystreams',
            'id': 'http://example.com/fav/1',
            'actor': 'https://example.com/users/rat',
            'published': 'Mon, 25 May 2020 19:31:20 GMT',
            'object': 'http://local.com/status/1',
        }

        incoming.handle_favorite(activity)

        fav = models.Favorite.objects.get(remote_id='http://example.com/fav/1')
        self.assertEqual(fav.status, self.status)
        self.assertEqual(fav.remote_id, 'http://example.com/fav/1')
        self.assertEqual(fav.user, self.remote_user)
