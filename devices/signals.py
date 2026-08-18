"""Cache invalidation for the ADMS hot path.

``devices.adms.get_device`` caches the Device behind a serial number, because
resolving it is the one query every device request has to make before it can do
anything else. That cache has to be dropped whenever the underlying record
changes, or a device deactivated in the admin would keep being served.
"""
from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .adms import device_cache_key
from .models import Device


@receiver(post_save, sender=Device)
@receiver(post_delete, sender=Device)
def invalidate_device_cache(sender, instance, **kwargs):
    """Drop the cached copy of a device whenever its record is written.

    Note what this can and cannot do with the default local-memory cache: it
    evicts the entry in *this* worker process only. Other workers keep their
    own copy until it expires, so an admin change takes up to
    ADMS_DEVICE_CACHE_SECONDS to be visible everywhere. Set REDIS_URL to make
    the cache shared, and the invalidation immediate across all workers.
    """
    if instance.serial_number:
        cache.delete(device_cache_key(instance.serial_number))
