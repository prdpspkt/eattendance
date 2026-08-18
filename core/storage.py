"""Static file storage.

WhiteNoise's manifest storage renames files by content hash on collectstatic
(``app.4f2a1c9d.css``), which is what makes a long immutable cache safe: a
changed file gets a new URL, so browsers never serve a stale copy.

The stock behaviour is strict. Any ``{% static %}`` reference it cannot resolve
raises, which takes the whole page down with a 500. That fires in two ordinary
situations here:

  * the test suite, which never runs collectstatic, and
  * a deploy where the app is restarted before static files are collected.

Neither warrants a broken site, so an unresolvable reference falls back to the
plain path. Worst case one asset 404s and the page still renders.

The trade-off: a genuine typo in a ``{% static %}`` tag becomes a quiet 404
rather than a loud error. `manage.py collectstatic` and the browser console
both still surface it.
"""
import logging

from whitenoise.storage import CompressedManifestStaticFilesStorage

logger = logging.getLogger(__name__)


class ResilientManifestStaticFilesStorage(CompressedManifestStaticFilesStorage):
    """Hashed, compressed static files that degrade instead of raising."""

    # Missing manifest entry -> use the plain name rather than raising.
    manifest_strict = False

    def hashed_name(self, name, content=None, filename=None):
        """Fall back to the unhashed name when the source file is unavailable.

        Reached when there is no manifest at all and the file is not present
        under STATIC_ROOT, e.g. during tests.
        """
        try:
            return super().hashed_name(name, content, filename)
        except ValueError:
            logger.debug("No hashed name available for %s; using plain path", name)
            return name
