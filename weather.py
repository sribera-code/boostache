"""
weather.py – Section Météo : prévision sur 14 jours en courbes (d'après weatherline).

- Prévision horaire : Open-Meteo, gratuit et sans clé. La pluie et sa
  probabilité portent sur l'heure écoulée (la valeur de 13h couvre 12h–13h) :
  l'interface les dessine au milieu de cette heure.
- Fourchette de température : percentiles 10 et 90 des 51 scénarios de
  l'ensemble ECMWF (ecmwf_ifs025). Facultative : absente si l'API d'ensemble
  ne répond pas.
- Luminosité : rayonnement global (soleil direct + lumière diffusée par les
  nuages) en % du plein soleil à midi par ciel dégagé sur la période (modèle
  de Haurwitz) : un voile fin la baisse peu, un ciel gris beaucoup.
- Position : celle de Windows (Windows.Devices.Geolocation, via PowerShell
  comme ocr.py), ou une ville cherchée (géocodage Open-Meteo). Nom du lieu
  d'une position : géocodage inverse BigDataCloud (gratuit, sans clé).

La dernière prévision est gardée dans weather.json : affichée tout de suite
au démarrage, et quand le réseau ne répond pas.
"""

import ctypes
import math
import ssl
import subprocess
import threading
from datetime import datetime, timezone

from engine import logger
from storage import DATA_DIR, read_json, settings, write_json

FORECAST_DAYS = 14
CACHE_FILE = DATA_DIR / "weather.json"
LOCATE_TIMEOUT = 25            # secondes (PowerShell + position Windows)

HOURLY = ("temperature_2m", "apparent_temperature", "relative_humidity_2m", "dew_point_2m",
          "precipitation", "precipitation_probability", "sunshine_duration",
          "shortwave_radiation_instant", "terrestrial_radiation_instant", "cloud_cover",
          "wind_speed_10m", "wind_gusts_10m", "wind_direction_10m", "weather_code", "is_day")
DAILY = ("weather_code", "temperature_2m_max", "temperature_2m_min", "sunrise", "sunset",
         "precipitation_sum", "precipitation_probability_max")
CURRENT = ("temperature_2m", "apparent_temperature", "relative_humidity_2m", "weather_code",
           "wind_speed_10m", "wind_direction_10m", "is_day")

# Position Windows. Pas de guillemets doubles (ligne de commande, voir ocr.py).
# Code de sortie 2 : accès refusé (Paramètres > Confidentialité > Emplacement).
_LOCATE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); if (-not $task.Wait(20000)) { throw 'délai dépassé' }; $task.Result }
$null = [Windows.Devices.Geolocation.Geolocator, Windows.Devices.Geolocation, ContentType = WindowsRuntime]
$access = Await ([Windows.Devices.Geolocation.Geolocator]::RequestAccessAsync()) ([Windows.Devices.Geolocation.GeolocationAccessStatus])
if ($access -ne 'Allowed') { exit 2 }
$g = New-Object Windows.Devices.Geolocation.Geolocator
$pos = Await ($g.GetGeopositionAsync()) ([Windows.Devices.Geolocation.Geoposition])
$c = $pos.Coordinate.Point.Position
[Console]::Out.WriteLine([string]::Format([Globalization.CultureInfo]::InvariantCulture, '{0:R} {1:R}', $c.Latitude, $c.Longitude))
"""


class WeatherError(Exception):
    """Message destiné à l'utilisateur."""


# ─────────────────────────────────────────────
#  Analyse des réponses d'Open-Meteo
# ─────────────────────────────────────────────
def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _clear_sky(toa: float | None) -> float:
    """Rayonnement global par ciel dégagé (modèle de Haurwitz). La hauteur du soleil
    se déduit du rayonnement au sommet de l'atmosphère (toa)."""
    if toa is None:
        return 0.0
    cos_zenith = toa / 1361
    return 1098 * cos_zenith * math.exp(-.057 / cos_zenith) if cos_zenith > .01 else 0.0


def _percentile(ordered: list[float], q: float) -> float:
    pos = (len(ordered) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _round(v: float | None, digits: int = 1):
    return None if v is None else round(v, digits)


def parse(fc: dict, ens: dict | None) -> dict:
    """Prévision en colonnes (une liste par variable, alignées sur times) pour
    l'interface. Valeur absente : None."""
    h = fc.get("hourly") or {}
    times = [str(t) for t in h.get("time") or []]
    n = len(times)
    if not n:
        raise WeatherError("La prévision reçue est vide.")

    def col(key):
        v = h.get(key)
        return [_num(x) for x in v] if isinstance(v, list) and len(v) == n else [None] * n

    hourly = {key: col(key) for key in HOURLY}
    sunshine = [None if s is None else s / 60 for s in hourly["sunshine_duration"]]   # minutes
    # Luminosité : rayonnement global en % du plein soleil à midi par ciel dégagé
    peak = max((_clear_sky(t) for t in hourly["terrestrial_radiation_instant"]), default=0)
    brightness = [None if g is None or peak <= 0 else max(0.0, min(100.0, 100 * g / peak))
                  for g in hourly["shortwave_radiation_instant"]]

    # ── Fourchette de température (percentiles 10 et 90 des scénarios) ──
    low = high = None
    members = 0
    eh = (ens or {}).get("hourly")
    if isinstance(eh, dict) and isinstance(eh.get("time"), list):
        index = {str(t): i for i, t in enumerate(eh["time"])}
        series = [[_num(x) for x in v] for k, v in eh.items()
                  if (k == "temperature_2m" or k.startswith("temperature_2m_member")) and isinstance(v, list)]
        members = len(series)
        low, high = [None] * n, [None] * n
        for i, t in enumerate(times):
            j = index.get(t)
            if j is None:
                continue
            values = sorted(m[j] for m in series if j < len(m) and m[j] is not None)
            if len(values) < 5:
                continue
            low[i], high[i] = _percentile(values, .1), _percentile(values, .9)

    # ── Journées ──
    d = fc.get("daily") or {}
    d_times = [str(t) for t in d.get("time") or []]

    def dcol(key):
        v = d.get(key)
        return v if isinstance(v, list) and len(v) == len(d_times) else [None] * len(d_times)

    position = {t: i for i, t in enumerate(times)}
    days = []
    for k, day in enumerate(d_times):
        start = position.get(f"{day}T00:00")
        if start is None:
            continue
        days.append({
            "date": day,
            "start": start,
            "code": _num(dcol("weather_code")[k]),
            "t_max": _num(dcol("temperature_2m_max")[k]),
            "t_min": _num(dcol("temperature_2m_min")[k]),
            "precip": _num(dcol("precipitation_sum")[k]),
            "precip_prob": _num(dcol("precipitation_probability_max")[k]),
            "sunrise": dcol("sunrise")[k] if isinstance(dcol("sunrise")[k], str) else None,
            "sunset": dcol("sunset")[k] if isinstance(dcol("sunset")[k], str) else None,
        })

    cur = fc.get("current") or {}
    return {
        "latitude": _num(fc.get("latitude")),
        "longitude": _num(fc.get("longitude")),
        "utc_offset": int(_num(fc.get("utc_offset_seconds")) or 0),
        "timezone": str(fc.get("timezone") or ""),
        "times": times,
        "temperature": [_round(v) for v in hourly["temperature_2m"]],
        "apparent": [_round(v) for v in hourly["apparent_temperature"]],
        "humidity": hourly["relative_humidity_2m"],
        "dew_point": [_round(v) for v in hourly["dew_point_2m"]],
        "precipitation": [_round(v, 2) for v in hourly["precipitation"]],
        "precip_prob": hourly["precipitation_probability"],
        "sunshine": [_round(v) for v in sunshine],
        "brightness": [_round(v) for v in brightness],
        "cloud_cover": hourly["cloud_cover"],
        "wind": hourly["wind_speed_10m"],
        "gusts": hourly["wind_gusts_10m"],
        "wind_dir": hourly["wind_direction_10m"],
        "code": [None if c is None else int(c) for c in hourly["weather_code"]],
        "is_day": [v == 1 for v in hourly["is_day"]],
        "temp_low": None if low is None else [_round(v) for v in low],
        "temp_high": None if high is None else [_round(v) for v in high],
        "members": members,
        "days": days,
        "current": {key: _num(cur.get(key)) for key in CURRENT},
    }


def home_country() -> str:
    """Pays des paramètres régionaux de Windows (code ISO à deux lettres), ou ""."""
    try:
        kernel32 = ctypes.windll.kernel32
        geo = kernel32.GetUserGeoID(16)                     # GEOCLASS_NATION
        buf = ctypes.create_unicode_buffer(8)
        if kernel32.GetGeoInfoW(geo, 4, buf, len(buf), 0):  # GEO_ISO2
            return buf.value.upper()
    except (AttributeError, OSError):
        pass
    return ""


def place_label(item: dict) -> dict:
    """Résultat du géocodage d'Open-Meteo → lieu enregistré dans les réglages."""
    detail = ", ".join(x for x in (item.get("admin1"), item.get("country")) if x and x != item.get("name"))
    return {"name": str(item.get("name") or ""), "detail": detail,
            "lat": float(item["latitude"]), "lon": float(item["longitude"])}


# ─────────────────────────────────────────────
#  Service
# ─────────────────────────────────────────────
class WeatherService:
    def __init__(self):
        self._web = None
        self._lock = threading.Lock()
        self._here = None          # dernière position Windows : {lat, lon, name}
        self._cache = read_json(CACHE_FILE) if CACHE_FILE.exists() else None

    def _http(self):
        """Client HTTPS vérifié avec les certificats de Windows (truststore), comme
        ollama_admin : derrière un antivirus qui inspecte le HTTPS, ceux de Python sont refusés."""
        if self._web is None:
            import httpx
            try:
                import truststore
                verify = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            except ImportError:
                verify = True
            self._web = httpx.Client(timeout=20, follow_redirects=True, verify=verify,
                                     headers={"User-Agent": "Boostache"})
        return self._web

    def _get(self, url: str, params: dict, timeout: float = 20) -> dict:
        r = self._http().get(url, params=params, timeout=timeout)
        if r.status_code != 200:
            raise WeatherError(f"Le service météo a répondu {r.status_code} ({r.url.host}).")
        return r.json()

    def snapshot(self) -> dict:
        """État initial de l'interface : lieu choisi et dernière prévision gardée."""
        cache = self._cache if isinstance(self._cache, dict) else None
        return {"place": settings.get("weather_place"), "cached": cache}

    # ── Position ──
    def locate(self) -> dict:
        """Position Windows et nom du lieu : {lat, lon, name} ou {error}."""
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _LOCATE_SCRIPT],
                capture_output=True, encoding="utf-8", errors="replace", timeout=LOCATE_TIMEOUT,
                creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            return {"error": "Impossible d'obtenir la position (délai dépassé)."}
        except Exception as e:
            logger.log(f"Météo : position impossible ({e})")
            return {"error": "Impossible d'obtenir la position."}
        if result.returncode == 2:
            return {"error": "Windows refuse l'accès à la position.\n"
                             "Active-la dans Paramètres > Confidentialité > Emplacement, ou choisis une ville.",
                    "denied": True}
        try:
            lat, lon = (float(x) for x in result.stdout.split()[:2])
        except ValueError:
            reason = (result.stderr or "").strip().splitlines()[-1:] or ["sans réponse"]
            logger.log(f"Météo : position impossible ({reason[0]})")
            return {"error": "Impossible d'obtenir la position.\n"
                             "La localisation est peut-être désactivée dans Windows : choisis une ville."}
        name = self._place_name(lat, lon)
        self._here = {"lat": lat, "lon": lon, "name": name}
        return dict(self._here)

    def _place_name(self, lat: float, lon: float) -> str:
        """Nom de la ville (géocodage inverse gratuit, sans clé) ; coordonnées sinon."""
        try:
            j = self._get("https://api.bigdatacloud.net/data/reverse-geocode-client",
                          {"latitude": lat, "longitude": lon, "localityLanguage": "fr"}, timeout=6)
            for key in ("city", "locality", "principalSubdivision", "countryName"):
                v = j.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        except Exception as e:
            logger.log(f"Météo : nom du lieu introuvable ({e})")
        return f"{lat:.2f}, {lon:.2f}"

    def search(self, query: str) -> dict:
        """Villes dont le nom commence par query (géocodage Open-Meteo, en français)."""
        query = (query or "").strip()
        if len(query) < 2:
            return {"results": []}
        try:
            j = self._get("https://geocoding-api.open-meteo.com/v1/search",
                          {"name": query, "count": 8, "language": "fr", "format": "json"}, timeout=8)
        except Exception as e:
            return {"error": f"Recherche impossible : {e}"}
        # Classées par population : les villes du pays de Windows d'abord (Brest en Bretagne
        # avant Brest en Biélorussie)
        home = home_country()
        items = sorted(j.get("results") or [], key=lambda it: str(it.get("country_code", "")).upper() != home)
        out = []
        for item in items:
            try:
                out.append(place_label(item))
            except (KeyError, TypeError, ValueError):
                continue
        return {"results": out}

    def set_place(self, place) -> dict | None:
        """Lieu fixe choisi ({name, detail, lat, lon}) ; None : position actuelle."""
        if place is not None:
            try:
                place = {"name": str(place.get("name") or "")[:120], "detail": str(place.get("detail") or "")[:200],
                         "lat": max(-90.0, min(90.0, float(place["lat"]))),
                         "lon": max(-180.0, min(180.0, float(place["lon"])))}
            except (AttributeError, KeyError, TypeError, ValueError):
                return settings.get("weather_place")
        settings.set("weather_place", place)
        return place

    # ── Prévision ──
    def forecast(self, lat: float, lon: float, name: str = "", here: bool = False) -> dict:
        """Prévision de 14 jours : {forecast, place, here, updated} ou {error}.
        En cas d'échec, la dernière prévision du même lieu est renvoyée avec l'erreur."""
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            return {"error": "Lieu invalide."}
        common = {"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "timezone": "auto",
                  "forecast_days": FORECAST_DAYS}
        ens_box = {}

        def ensemble():
            # Facultatif : en cas d'échec, pas de fourchette
            try:
                ens_box["data"] = self._get("https://ensemble-api.open-meteo.com/v1/ensemble",
                                            {**common, "hourly": "temperature_2m", "models": "ecmwf_ifs025"},
                                            timeout=25)
            except Exception as e:
                logger.log(f"Météo : fourchette de température indisponible ({e})")

        thread = threading.Thread(target=ensemble, name="weather-ensemble", daemon=True)
        thread.start()
        try:
            fc = self._get("https://api.open-meteo.com/v1/forecast",
                           {**common, "hourly": ",".join(HOURLY), "daily": ",".join(DAILY),
                            "current": ",".join(CURRENT)})
            thread.join(26)
            data = parse(fc, ens_box.get("data"))
        except Exception as e:
            message = str(e) if isinstance(e, WeatherError) else f"Impossible de charger la météo.\n{e}"
            logger.log(f"Météo : {message}")
            cache = self._cache if isinstance(self._cache, dict) else None
            same = cache and abs(cache.get("lat", 999) - lat) < .05 and abs(cache.get("lon", 999) - lon) < .05
            return {"error": message, **({"stale": cache} if same else {})}

        result = {"forecast": data, "place": name or self._place_name(lat, lon), "here": bool(here),
                  "lat": lat, "lon": lon, "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        with self._lock:
            self._cache = result
        write_json(CACHE_FILE, result)
        return result

