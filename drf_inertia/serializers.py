from collections import OrderedDict

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.middleware.csrf import get_token
from django.utils.module_loading import import_string
from rest_framework import fields, serializers, status

from .config import SHARED_DATA_SERIALIZER, USER_SERIALIZER

User = get_user_model()


class SharedSerializerBase(serializers.Serializer):
    """
    SharedSerializerBase is used to include common data across
    requests in each inertia response.

    You can define your own SharedSerializer by setting the
    INERTIA_SHARED_SERIALIZER_CLASS in your settings.

    Each SharedSerializer receives an Inertia as the
    instance to be "serialized" as well as the render_context
    as its context.

    The SharedSerializer serializes the Request by merging
    its own fields with the data on the Inertia. Data from
    the Inertia is never overwritten by the SharedSerializer.
    In this way you can override the default shared data in your
    own views if necessary.

    Since the SharedSerializer is used for every Inertia response
    you should avoid long running operations and always return
    from methods as soon as possible.

    """

    def __init__(self, instance=None, *args, **kwargs):
        # exclude fields already in data or not in instance.partial_data
        exclude = instance.inertia.data.keys()
        for field in self.fields:
            if instance.inertia.partial_data and field not in instance.inertia.partial_data:
                exclude.append(field)

        for field in exclude:
            if field in self.fields:
                self.fields.pop(field)

        super(SharedSerializerBase, self).__init__(instance, *args, **kwargs)

    def to_representation(self, instance):
        # merge the shared data with the component data
        # ensuring that component data is always prioritized
        data = super(SharedSerializerBase, self).to_representation(instance)
        data.update(instance.inertia.data)
        return data


class SharedField(fields.Field):
    """
    Shared fields by default are Read-only and require a context
    """
    requires_context = True

    def __init__(self, **kwargs):
        kwargs['read_only'] = True
        super().__init__(**kwargs)

    @property
    def is_conflict(self):
        return self.context["response"].status_code == status.HTTP_409_CONFLICT

    def get_attribute(self, instance):
        return instance


# let's put some basic meta information in all props
class PageMetaSerializer(SharedField):
    def to_representation(self, value):
        # no need to iterate (and mark used) messages if 409 response
        app_meta = {}
        request = self.context["request"]
        app_meta = {
            "appName": request.resolver_match.app_name,
            "namespace": request.resolver_match.namespace,
            "urlName": request.resolver_match.url_name,
            "csrfToken": get_token(request),
        }
        return app_meta


class FlashSerializer(SharedField):
    def to_representation(self, value):
        # no need to iterate (and mark used) messages if 409 response
        flash = {}
        if not self.is_conflict:
            storage = messages.get_messages(self.context["request"])
            for message in storage:
                flash[message.level_tag] = message.message
        return flash


class SessionSerializerField(SharedField):
    def __init__(self, session_field, **kwargs):
        self.session_field = session_field
        super(SessionSerializerField, self).__init__(**kwargs)

    def to_representation(self, value):
        if not hasattr(self.context["request"], "session"):
            return {}

        if not self.is_conflict and self.session_field in self.context["request"].session:
            return self.context["request"].session.pop(self.session_field, None)

        return {}


class DefaultSharedSerializer(SharedSerializerBase):
    errors = SessionSerializerField(
        "errors", default=OrderedDict(), source='*')
    flash = FlashSerializer(default=OrderedDict(), source='*')
    pageMeta = PageMetaSerializer(default=OrderedDict(), source="*")


class DefaultUserSerializer(serializers.ModelSerializer):
    # set required to false - throwing an error if AnonymousUser
    email = serializers.EmailField(required=False)
    name = serializers.CharField(required=False, trim_whitespace=False)

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "name",
            "is_superuser",
            "is_staff",
        )


class AuthSerializer(serializers.Serializer):
    user = import_string(USER_SERIALIZER)


class InertiaSharedSerializer(DefaultSharedSerializer):
    user = AuthSerializer(source="*")

    class Meta:
        fields = ("flash", "errors", "user", "pageMeta")


class InertiaSerializer(serializers.Serializer):
    component = serializers.CharField()
    props = serializers.SerializerMethodField()
    version = serializers.CharField()
    url = serializers.URLField()

    def get_props(self, obj):
        serializer_class = import_string(SHARED_DATA_SERIALIZER)
        serializer = serializer_class(
            self.context["request"], context=self.context)
        return serializer.data
