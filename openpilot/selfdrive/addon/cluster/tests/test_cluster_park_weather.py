from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from openpilot.selfdrive.addon.cluster import cluster_park_weather as weather


def test_park_weather_uses_valid_readings_and_worst_pollutant_grade():
  forecast = {'current': {'weather_code': 2, 'temperature_2m': 17},
              'daily': {'temperature_2m_max': [18], 'temperature_2m_min': [10]}}
  assert weather.weather_from_response(forecast, {'current': {'pm10': 25, 'pm2_5': 36}}) == {
    'code': 2, 'temperature_c': 17, 'max_c': 18, 'min_c': 10, 'dust_grade': 2,
  }
  assert weather.weather_from_response(forecast, None)['dust_grade'] is None
  assert weather.weather_from_response(None, {'current': {'pm10': 80}})['dust_grade'] == 1
  assert weather.weather_from_response({'current': {'temperature_2m': float('nan')}},
                                       {'current': {'pm2_5': -1}}) is None


def test_park_weather_queues_one_fetch_and_expires_stale_and_distant_data(monkeypatch):
  pending = []
  clock = SimpleNamespace(now=10.0)
  queries = []

  def make_thread(target, args, **_kwargs):
    return SimpleNamespace(start=lambda: pending.append((target, args)))

  def get_json(url):
    parsed = urlparse(url)
    queries.append((parsed.netloc, parse_qs(parsed.query)))
    return {'current': {'temperature_2m': 17, 'pm10': 30}}

  monkeypatch.setattr(weather, 'threading', SimpleNamespace(Lock=weather.threading.Lock, Thread=make_thread))
  monkeypatch.setattr(weather, 'time', SimpleNamespace(monotonic=lambda: clock.now))
  monkeypatch.setattr(weather, '_get_json', get_json)

  reader = weather.ParkWeather(lambda: (37.56, 126.98))
  assert reader.snapshot(None) is None
  assert reader.snapshot(None) is None and len(pending) == 1
  fetch, args = pending.pop()
  fetch(*args)
  assert [host for host, query in queries] == ['api.open-meteo.com', 'air-quality-api.open-meteo.com']
  assert all(query['latitude'] == ['37.56'] and query['longitude'] == ['126.98'] for host, query in queries)
  assert reader.snapshot((37.57, 126.99))['temperature_c'] == 17
  assert not pending
  assert reader.snapshot((35.18, 129.08)) is None and len(pending) == 1
  clock.now += weather.WEATHER_MAX_AGE_S + 1
  assert reader.snapshot(None) is None
