"""Install exception handler for process crash."""
from selfdrive.swaglog import cloudlog
from selfdrive.version import version
import traceback
import datetime


import sentry_sdk
from sentry_sdk.integrations.threading import ThreadingIntegration

def capture_exception(*args, **kwargs) -> None:
  cloudlog.error("crash", exc_info=kwargs.get('exc_info', 1))

  try:
    with open('/data/log/last_exception', 'w') as f:
      now = datetime.datetime.now()
      f.write(now.strftime('[%Y-%m-%d %H:%M:%S]') + "\n\n" + str(traceback.format_exc()))
  except Exception:
    pass

  try:
    sentry_sdk.capture_exception(*args, **kwargs)
    sentry_sdk.flush()  # https://github.com/getsentry/sentry-python/issues/291
  except Exception:
    cloudlog.exception("sentry exception")

def bind_user(**kwargs) -> None:
  sentry_sdk.set_user(kwargs)

def bind_extra(**kwargs) -> None:
  for k, v in kwargs.items():
    sentry_sdk.set_tag(k, v)

def init() -> None:
  sentry_sdk.init("https://a8dc76b5bfb34908a601d67e2aa8bcf9@o33823.ingest.sentry.io/77924",
                  default_integrations=False, integrations=[ThreadingIntegration(propagate_hub=True)],
                  release=version)
