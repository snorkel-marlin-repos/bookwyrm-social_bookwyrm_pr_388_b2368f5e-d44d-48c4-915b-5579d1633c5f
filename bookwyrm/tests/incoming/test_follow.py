from unittest.mock import patch
from django.test import TestCase

from bookwyrm import models, incoming


class IncomingFollow(TestCase):
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
            'mouse', 'mouse@mouse.com', 'mouseword', local=True)
        self.local_user.remote_id = 'http://local.com/user/mouse'
        self.local_user.save()


    def test_handle_follow(self):
        activity = {
            "@context": "https://www.w3.org/ns/activitystreams",
            "id": "https://example.com/users/rat/follows/123",
            "type": "Follow",
            "actor": "https://example.com/users/rat",
            "object": "http://local.com/user/mouse"
        }

        with patch('bookwyrm.broadcast.broadcast_task.delay') as _:
            incoming.handle_follow(activity)

        # notification created
        notification = models.Notification.objects.get()
        self.assertEqual(notification.user, self.local_user)
        self.assertEqual(notification.notification_type, 'FOLLOW')

        # the request should have been deleted
        requests = models.UserFollowRequest.objects.all()
        self.assertEqual(list(requests), [])

        # the follow relationship should exist
        follow = models.UserFollows.objects.get(user_object=self.local_user)
        self.assertEqual(follow.user_subject, self.remote_user)


    def test_handle_follow_manually_approved(self):
        activity = {
            "@context": "https://www.w3.org/ns/activitystreams",
            "id": "https://example.com/users/rat/follows/123",
            "type": "Follow",
            "actor": "https://example.com/users/rat",
            "object": "http://local.com/user/mouse"
        }

        self.local_user.manually_approves_followers = True
        self.local_user.save()

        with patch('bookwyrm.broadcast.broadcast_task.delay') as _:
            incoming.handle_follow(activity)

        # notification created
        notification = models.Notification.objects.get()
        self.assertEqual(notification.user, self.local_user)
        self.assertEqual(notification.notification_type, 'FOLLOW_REQUEST')

        # the request should exist
        request = models.UserFollowRequest.objects.get()
        self.assertEqual(request.user_subject, self.remote_user)
        self.assertEqual(request.user_object, self.local_user)

        # the follow relationship should not exist
        follow = models.UserFollows.objects.all()
        self.assertEqual(list(follow), [])
