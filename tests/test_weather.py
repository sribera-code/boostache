"""weather.py : analyse des réponses d'Open-Meteo (colonnes, luminosité, fourchette des
scénarios, journées), lieu enregistré, recherche et erreurs réseau (requêtes
remplacées par des doublures : rien ne part sur Internet)."""

from tests import _env  # noqa: F401  (données temporaires)

import math
import unittest
from unittest import mock

import weather
from storage import settings


def hours(days=2):
    return [f"2026-11-{11 + d:02d}T{h:02d}:00" for d in range(days) for h in range(24)]


def forecast_json(days=2, **overrides):
    times = hours(days)
    n = len(times)
    hourly = {"time": times}
    for key in weather.HOURLY:
        hourly[key] = [float(i % 24) for i in range(n)]
    hourly["is_day"] = [1 if 8 <= i % 24 < 17 else 0 for i in range(n)]
    hourly["sunshine_duration"] = [1800.0] * n                       # secondes
    # Sommet de l'atmosphère : soleil au plus haut à midi
    hourly["terrestrial_radiation_instant"] = [max(0.0, 800 * math.sin(math.pi * (i % 24 - 6) / 12)) for i in range(n)]
    hourly["shortwave_radiation_instant"] = [100.0] * n
    hourly.update(overrides)
    day_list = [times[24 * d][:10] for d in range(days)]
    return {
        "latitude": 45.75, "longitude": 4.85, "utc_offset_seconds": 3600, "timezone": "Europe/Paris",
        "hourly": hourly,
        "daily": {"time": day_list, "weather_code": [3] * days, "temperature_2m_max": [15.2] * days,
                  "temperature_2m_min": [4.1] * days, "sunrise": [f"{d}T07:50" for d in day_list],
                  "sunset": [f"{d}T17:15" for d in day_list], "precipitation_sum": [2.5] * days,
                  "precipitation_probability_max": [60] * days},
        "current": {"temperature_2m": 12.3, "is_day": 1},
    }


def ensemble_json(days=2, members=10):
    times = hours(days)
    out = {"time": times, "temperature_2m": [10.0] * len(times)}
    for m in range(1, members):
        out[f"temperature_2m_member{m:02d}"] = [10.0 + m] * len(times)
    return {"hourly": out}


class ParseTest(unittest.TestCase):
    def test_columns_and_days(self):
        f = weather.parse(forecast_json(), None)
        self.assertEqual(len(f["times"]), 48)
        self.assertEqual(f["utc_offset"], 3600)
        self.assertEqual(f["temperature"][13], 13.0)
        self.assertEqual(f["sunshine"][0], 30.0)                      # minutes
        self.assertEqual(f["is_day"][:9], [False] * 8 + [True])
        self.assertEqual([d["start"] for d in f["days"]], [0, 24])
        self.assertEqual(f["days"][0]["t_max"], 15.2)
        self.assertEqual(f["days"][0]["sunrise"], "2026-11-11T07:50")
        self.assertEqual(f["current"]["temperature_2m"], 12.3)
        self.assertIsNone(f["temp_low"])
        self.assertEqual(f["members"], 0)

    def test_brightness_is_percent_of_clear_sky_noon(self):
        peak = weather._clear_sky(800)
        f = weather.parse(forecast_json(shortwave_radiation_instant=[peak / 2] * 48), None)
        self.assertAlmostEqual(f["brightness"][0], 50.0, places=0)
        # Plus que le ciel dégagé (nuages qui réfléchissent) : borné à 100
        f = weather.parse(forecast_json(shortwave_radiation_instant=[peak * 2] * 48), None)
        self.assertEqual(f["brightness"][5], 100.0)

    def test_missing_values_become_none(self):
        temps = [None] * 48
        temps[3] = 7.25
        f = weather.parse(forecast_json(temperature_2m=temps, cloud_cover=[1.0]), None)
        self.assertIsNone(f["temperature"][0])
        self.assertEqual(f["temperature"][3], 7.2)
        self.assertEqual(f["cloud_cover"], [None] * 48)              # mauvaise longueur : ignorée

    def test_ensemble_percentiles(self):
        f = weather.parse(forecast_json(), ensemble_json())
        self.assertEqual(f["members"], 10)
        # Membres 10, 11, …, 19 : percentiles 10 et 90 interpolés
        self.assertAlmostEqual(f["temp_low"][0], 10.9)
        self.assertAlmostEqual(f["temp_high"][0], 18.1)

    def test_ensemble_too_few_members(self):
        f = weather.parse(forecast_json(), ensemble_json(members=3))
        self.assertEqual(f["temp_low"], [None] * 48)

    def test_empty_forecast(self):
        with self.assertRaises(weather.WeatherError):
            weather.parse({"hourly": {"time": []}}, None)


class PlaceTest(unittest.TestCase):
    def setUp(self):
        self.service = weather.WeatherService()

    def tearDown(self):
        settings.set("weather_place", None)

    def test_set_place_validates(self):
        saved = self.service.set_place({"name": "Lyon", "detail": "France", "lat": "45.7", "lon": 400})
        self.assertEqual(saved, {"name": "Lyon", "detail": "France", "lat": 45.7, "lon": 180.0})
        self.assertEqual(settings.get("weather_place"), saved)
        self.assertEqual(self.service.set_place({"name": "sans coordonnées"}), saved)
        self.assertIsNone(self.service.set_place(None))
        self.assertIsNone(settings.get("weather_place"))

    def test_search_puts_home_country_first(self):
        results = {"results": [
            {"name": "Brest", "latitude": 52.1, "longitude": 23.7, "country": "Biélorussie", "country_code": "BY"},
            {"name": "Brest", "latitude": 48.4, "longitude": -4.5, "admin1": "Bretagne", "country": "France",
             "country_code": "FR"},
            {"name": "Sans coordonnées", "country_code": "FR"},
        ]}
        with mock.patch.object(self.service, "_get", return_value=results), \
                mock.patch.object(weather, "home_country", return_value="FR"):
            found = self.service.search("Brest")["results"]
        self.assertEqual([p["detail"] for p in found], ["Bretagne, France", "Biélorussie"])
        self.assertEqual(self.service.search("B"), {"results": []})

    def test_forecast_failure_returns_cached_forecast_of_same_place(self):
        good = {"forecast": weather.parse(forecast_json(), None), "lat": 45.75, "lon": 4.85,
                "place": "Lyon", "here": False, "updated": "2026-11-11T10:00:00+00:00"}
        self.service._cache = good
        with mock.patch.object(self.service, "_get", side_effect=weather.WeatherError("Le service météo a répondu 503.")):
            res = self.service.forecast(45.76, 4.84, "Lyon")
            self.assertEqual(res["error"], "Le service météo a répondu 503.")
            self.assertIs(res["stale"], good)
            self.assertNotIn("stale", self.service.forecast(48.4, -4.5, "Brest"))

    def test_forecast_without_ensemble(self):
        def get(url, params, timeout=20):
            if "ensemble" in url:
                raise OSError("hors ligne")
            return forecast_json()
        with mock.patch.object(self.service, "_get", side_effect=get):
            res = self.service.forecast(45.75, 4.85, "Lyon")
        self.assertNotIn("error", res)
        self.assertIsNone(res["forecast"]["temp_low"])
        self.assertEqual(res["place"], "Lyon")
        self.assertTrue(weather.CACHE_FILE.exists())


if __name__ == "__main__":
    unittest.main()
