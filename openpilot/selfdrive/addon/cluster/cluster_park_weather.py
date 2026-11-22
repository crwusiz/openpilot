import json
import math
import threading
import time
from http.client import HTTPException
from urllib.parse import urlencode
from urllib.request import urlopen


WEATHER_REFRESH_S = 600.0
WEATHER_RETRY_S = 120.0
WEATHER_MAX_AGE_S = 3600.0


def _number(value):
  return value if type(value) is int or (type(value) is float and math.isfinite(value)) else None


def weather_position(gps):
  if not getattr(gps, 'hasFix', False):
    return None
  latitude = _number(gps.latitude)
  longitude = _number(gps.longitude)
  if latitude is None or longitude is None or not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
    return None
  # Weather needs an approximate location, so keep and send only two decimal places.
  return round(latitude, 2), round(longitude, 2)


def _section(response, key):
  section = response.get(key) if isinstance(response, dict) else None
  return section if isinstance(section, dict) else {}


def _temperature(value):
  value = _number(value)
  return value if value is not None and -100 <= value <= 100 else None


def _daily_temperature(data, key):
  values = data.get(key)
  return _temperature(values[0]) if isinstance(values, list) and values else None


def weather_from_response(forecast, air):
  current = _section(forecast, 'current')
  daily = _section(forecast, 'daily')
  code = current.get('weather_code')
  air_now = _section(air, 'current')
  grades = []
  for pollutant, thresholds in (('pm10', (30, 80, 150)), ('pm2_5', (15, 35, 75))):
    value = _number(air_now.get(pollutant))
    if value is not None and value >= 0:
      grades.append(sum(value > threshold for threshold in thresholds))

  result = {
    'code': code if type(code) is int and 0 <= code <= 99 else None,
    'temperature_c': _temperature(current.get('temperature_2m')),
    'max_c': _daily_temperature(daily, 'temperature_2m_max'),
    'min_c': _daily_temperature(daily, 'temperature_2m_min'),
    'dust_grade': max(grades, default=None),
  }
  return result if any(value is not None for value in result.values()) else None


def _get_json(url):
  try:
    with urlopen(url, timeout=5.0) as response:
      return json.loads(response.read(65536))
  except (HTTPException, OSError, ValueError):
    return None


def _nearby(position, other):
  return other is not None and all(abs(a - b) < 0.05 for a, b in zip(position, other, strict=True))


class ParkWeather:
  def __init__(self, last_position):
    self._last_position = last_position
    self._lock = threading.Lock()
    self._in_flight = False
    self._next_fetch = 0.0
    self._attempt_position = None
    self._sample_at = 0.0
    self._sample = None
    self._sample_position = None

  def snapshot(self, position):
    now = time.monotonic()
    with self._lock:
      new_position = position is not None and not _nearby(position, self._attempt_position)
      if not self._in_flight and (now >= self._next_fetch or new_position):
        self._in_flight = True
        try:
          threading.Thread(target=self._fetch, args=(position,), daemon=True, name='cluster-park-weather').start()
        except RuntimeError:
          self._in_flight = False
          self._attempt_position = position
          self._next_fetch = now + WEATHER_RETRY_S
      nearby = position is None or _nearby(position, self._sample_position)
      return self._sample if nearby and now - self._sample_at <= WEATHER_MAX_AGE_S else None

  def _fetch(self, position):
    sample = None
    try:
      position = position or self._last_position()
      if position is None:
        return
      latitude, longitude = position
      coordinates = {'latitude': f'{latitude:.2f}', 'longitude': f'{longitude:.2f}'}
      forecast_query = urlencode({**coordinates, 'timezone': 'auto', 'forecast_days': 1,
                                  'current': 'temperature_2m,weather_code',
                                  'daily': 'temperature_2m_max,temperature_2m_min'})
      air_query = urlencode({**coordinates, 'current': 'pm10,pm2_5'})
      forecast = _get_json(f'https://api.open-meteo.com/v1/forecast?{forecast_query}')
      air = _get_json(f'https://air-quality-api.open-meteo.com/v1/air-quality?{air_query}')
      sample = weather_from_response(forecast, air)
    finally:
      with self._lock:
        now = time.monotonic()
        if sample is not None:
          self._sample, self._sample_at, self._sample_position = sample, now, position
        complete = sample is not None and all(value is not None for value in sample.values())
        self._next_fetch = now + (WEATHER_REFRESH_S if complete else WEATHER_RETRY_S)
        self._attempt_position = position
        self._in_flight = False
