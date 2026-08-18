from django.apps import AppConfig


class DevicesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'devices'

    def ready(self):
        # Registers the Device cache invalidation receivers. Imported here
        # rather than at module level because models are not loaded yet when
        # the AppConfig class itself is imported.
        from . import signals  # noqa: F401
