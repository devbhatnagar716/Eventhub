"""Custom middleware for the events app."""

import logging
import time

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware:
    """Log method, path, status code and duration for every request.

    Registered in ``settings.MIDDLEWARE`` as
    ``events.middleware.RequestLoggingMiddleware``. The ``events`` logger is
    configured in settings with an INFO console handler, which is what makes
    these lines appear in the ``runserver`` terminal.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start_time = time.time()

        response = self.get_response(request)

        duration = time.time() - start_time
        logger.info(
            f"{request.method} {request.path} - "
            f"{response.status_code} - {duration:.2f}s"
        )

        return response
