''' models for storing different kinds of Activities '''
from django.utils import timezone
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from model_utils.managers import InheritanceManager

from bookwyrm import activitypub
from .base_model import ActivitypubMixin, OrderedCollectionPageMixin
from .base_model import BookWyrmModel, PrivacyLevels
from . import fields
from .fields import image_serializer

class Status(OrderedCollectionPageMixin, BookWyrmModel):
    ''' any post, like a reply to a review, etc '''
    user = fields.ForeignKey(
        'User', on_delete=models.PROTECT, activitypub_field='attributedTo')
    content = fields.TextField(blank=True, null=True)
    mention_users = fields.TagField('User', related_name='mention_user')
    mention_books = fields.TagField('Edition', related_name='mention_book')
    local = models.BooleanField(default=True)
    privacy = models.CharField(
        max_length=255,
        default='public',
        choices=PrivacyLevels.choices
    )
    sensitive = fields.BooleanField(default=False)
    # the created date can't be this, because of receiving federated posts
    published_date = fields.DateTimeField(
        default=timezone.now, activitypub_field='published')
    deleted = models.BooleanField(default=False)
    deleted_date = models.DateTimeField(blank=True, null=True)
    favorites = models.ManyToManyField(
        'User',
        symmetrical=False,
        through='Favorite',
        through_fields=('status', 'user'),
        related_name='user_favorites'
    )
    reply_parent = fields.ForeignKey(
        'self',
        null=True,
        on_delete=models.PROTECT,
        activitypub_field='inReplyTo',
    )
    objects = InheritanceManager()

    activity_serializer = activitypub.Note
    serialize_reverse_fields = [('attachments', 'attachment')]
    deserialize_reverse_fields = [('attachments', 'attachment')]

    #----- replies collection activitypub ----#
    @classmethod
    def replies(cls, status):
        ''' load all replies to a status. idk if there's a better way
            to write this so it's just a property '''
        return cls.objects.filter(reply_parent=status).select_subclasses()

    @property
    def status_type(self):
        ''' expose the type of status for the ui using activity type '''
        return self.activity_serializer.__name__

    def to_replies(self, **kwargs):
        ''' helper function for loading AP serialized replies to a status '''
        return self.to_ordered_collection(
            self.replies(self),
            remote_id='%s/replies' % self.remote_id,
            **kwargs
        )

    def to_activity(self, pure=False):
        ''' return tombstone if the status is deleted '''
        if self.deleted:
            return activitypub.Tombstone(
                id=self.remote_id,
                url=self.remote_id,
                deleted=self.deleted_date.isoformat(),
                published=self.deleted_date.isoformat()
            ).serialize()
        activity = ActivitypubMixin.to_activity(self)
        activity['replies'] = self.to_replies()

        # privacy controls
        public = 'https://www.w3.org/ns/activitystreams#Public'
        mentions = [u.remote_id for u in self.mention_users.all()]
        # this is a link to the followers list:
        followers = self.user.__class__._meta.get_field('followers')\
                .field_to_activity(self.user.followers)
        if self.privacy == 'public':
            activity['to'] = [public]
            activity['cc'] = [followers] + mentions
        elif self.privacy == 'unlisted':
            activity['to'] = [followers]
            activity['cc'] = [public] + mentions
        elif self.privacy == 'followers':
            activity['to'] = [followers]
            activity['cc'] = mentions
        if self.privacy == 'direct':
            activity['to'] = mentions
            activity['cc'] = []

        # "pure" serialization for non-bookwyrm instances
        if pure:
            activity['content'] = self.pure_content
            if 'name' in activity:
                activity['name'] = self.pure_name
            activity['type'] = self.pure_type
            activity['attachment'] = [
                image_serializer(b.cover) for b in self.mention_books.all() \
                        if b.cover]
            if hasattr(self, 'book'):
                activity['attachment'].append(
                    image_serializer(self.book.cover)
                )
        return activity


    def save(self, *args, **kwargs):
        ''' update user active time '''
        if self.user.local:
            self.user.last_active_date = timezone.now()
            self.user.save()
        return super().save(*args, **kwargs)


class GeneratedNote(Status):
    ''' these are app-generated messages about user activity '''
    @property
    def pure_content(self):
        ''' indicate the book in question for mastodon (or w/e) users '''
        message = self.content
        books = ', '.join(
            '<a href="%s">"%s"</a>' % (book.remote_id, book.title) \
            for book in self.mention_books.all()
        )
        return '%s %s %s' % (self.user.display_name, message, books)

    activity_serializer = activitypub.GeneratedNote
    pure_type = 'Note'


class Comment(Status):
    ''' like a review but without a rating and transient '''
    book = fields.ForeignKey(
        'Edition', on_delete=models.PROTECT, activitypub_field='inReplyToBook')

    @property
    def pure_content(self):
        ''' indicate the book in question for mastodon (or w/e) users '''
        return self.content + '<br><br>(comment on <a href="%s">"%s"</a>)' % \
                (self.book.remote_id, self.book.title)

    activity_serializer = activitypub.Comment
    pure_type = 'Note'


class Quotation(Status):
    ''' like a review but without a rating and transient '''
    quote = fields.TextField()
    book = fields.ForeignKey(
        'Edition', on_delete=models.PROTECT, activitypub_field='inReplyToBook')

    @property
    def pure_content(self):
        ''' indicate the book in question for mastodon (or w/e) users '''
        return '"%s"<br>-- <a href="%s">"%s"</a><br><br>%s' % (
            self.quote,
            self.book.remote_id,
            self.book.title,
            self.content,
        )

    activity_serializer = activitypub.Quotation
    pure_type = 'Note'


class Review(Status):
    ''' a book review '''
    name = fields.CharField(max_length=255, null=True)
    book = fields.ForeignKey(
        'Edition', on_delete=models.PROTECT, activitypub_field='inReplyToBook')
    rating = fields.IntegerField(
        default=None,
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )

    @property
    def pure_name(self):
        ''' clarify review names for mastodon serialization '''
        if self.rating:
            return 'Review of "%s" (%d stars): %s' % (
                self.book.title,
                self.rating,
                self.name
            )
        return 'Review of "%s": %s' % (
            self.book.title,
            self.name
        )

    @property
    def pure_content(self):
        ''' indicate the book in question for mastodon (or w/e) users '''
        return self.content + '<br><br>(<a href="%s">"%s"</a>)' % \
                (self.book.remote_id, self.book.title)

    activity_serializer = activitypub.Review
    pure_type = 'Article'


class Favorite(ActivitypubMixin, BookWyrmModel):
    ''' fav'ing a post '''
    user = fields.ForeignKey(
        'User', on_delete=models.PROTECT, activitypub_field='actor')
    status = fields.ForeignKey(
        'Status', on_delete=models.PROTECT, activitypub_field='object')

    activity_serializer = activitypub.Like

    def save(self, *args, **kwargs):
        ''' update user active time '''
        self.user.last_active_date = timezone.now()
        self.user.save()
        super().save(*args, **kwargs)

    class Meta:
        ''' can't fav things twice '''
        unique_together = ('user', 'status')


class Boost(Status):
    ''' boost'ing a post '''
    boosted_status = fields.ForeignKey(
        'Status',
        on_delete=models.PROTECT,
        related_name='boosters',
        activitypub_field='object',
    )

    activity_serializer = activitypub.Boost

    # This constraint can't work as it would cross tables.
    # class Meta:
    #     unique_together = ('user', 'boosted_status')


class ReadThrough(BookWyrmModel):
    ''' Store progress through a book in the database. '''
    user = models.ForeignKey('User', on_delete=models.PROTECT)
    book = models.ForeignKey('Book', on_delete=models.PROTECT)
    pages_read = models.IntegerField(
        null=True,
        blank=True)
    start_date = models.DateTimeField(
        blank=True,
        null=True)
    finish_date = models.DateTimeField(
        blank=True,
        null=True)

    def save(self, *args, **kwargs):
        ''' update user active time '''
        self.user.last_active_date = timezone.now()
        self.user.save()
        super().save(*args, **kwargs)


NotificationType = models.TextChoices(
    'NotificationType',
    'FAVORITE REPLY MENTION TAG FOLLOW FOLLOW_REQUEST BOOST IMPORT')

class Notification(BookWyrmModel):
    ''' you've been tagged, liked, followed, etc '''
    user = models.ForeignKey('User', on_delete=models.PROTECT)
    related_book = models.ForeignKey(
        'Edition', on_delete=models.PROTECT, null=True)
    related_user = models.ForeignKey(
        'User',
        on_delete=models.PROTECT, null=True, related_name='related_user')
    related_status = models.ForeignKey(
        'Status', on_delete=models.PROTECT, null=True)
    related_import = models.ForeignKey(
        'ImportJob', on_delete=models.PROTECT, null=True)
    read = models.BooleanField(default=False)
    notification_type = models.CharField(
        max_length=255, choices=NotificationType.choices)

    class Meta:
        ''' checks if notifcation is in enum list for valid types '''
        constraints = [
            models.CheckConstraint(
                check=models.Q(notification_type__in=NotificationType.values),
                name="notification_type_valid",
            )
        ]
