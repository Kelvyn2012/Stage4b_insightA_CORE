import concurrent.futures
from typing import Any, Dict

import httpx

from .countries import COUNTRY_NAMES
from .exceptions import ExternalAPIException, InvalidProfileDataException

_API_LABELS = {
    "genderize": "Genderize",
    "agify": "Agify",
    "nationalize": "Nationalize",
}


class ProfileAggregatorService:
    @staticmethod
    def _age_group(age: int) -> str:
        if age <= 12:
            return "child"
        if age <= 19:
            return "teenager"
        if age <= 59:
            return "adult"
        return "senior"

    @classmethod
    def fetch_and_process_data(cls, name: str) -> Dict[str, Any]:
        urls = {
            "genderize": f"https://api.genderize.io?name={name}",
            "agify": f"https://api.agify.io?name={name}",
            "nationalize": f"https://api.nationalize.io?name={name}",
        }

        responses: Dict[str, Any] = {}
        with httpx.Client(timeout=10.0) as client:
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                future_to_key = {
                    executor.submit(client.get, url): key
                    for key, url in urls.items()
                }
                for future in concurrent.futures.as_completed(future_to_key):
                    key = future_to_key[future]
                    label = _API_LABELS[key]
                    try:
                        resp = future.result()
                        resp.raise_for_status()
                        responses[key] = resp.json()
                    except Exception:
                        raise ExternalAPIException(
                            f"{label} returned an invalid response"
                        )

        g = responses.get("genderize", {})
        a = responses.get("agify", {})
        n = responses.get("nationalize", {})

        if not g.get("gender") or g.get("count", 0) == 0:
            raise InvalidProfileDataException(
                "Genderize returned insufficient data for this name"
            )
        if a.get("age") is None:
            raise InvalidProfileDataException(
                "Agify returned insufficient data for this name"
            )
        if not n.get("country"):
            raise InvalidProfileDataException(
                "Nationalize returned insufficient data for this name"
            )

        top_country = max(n["country"], key=lambda c: c["probability"])
        country_code = top_country["country_id"]

        return {
            "name": name,
            "gender": g["gender"],
            "gender_probability": g["probability"],
            "sample_size": g["count"],
            "age": a["age"],
            "age_group": cls._age_group(a["age"]),
            "country_id": country_code,
            "country_name": COUNTRY_NAMES.get(country_code, ""),
            "country_probability": top_country["probability"],
        }
