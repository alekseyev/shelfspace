import requests
import json

from shelfspace.cache import cache


class BaseAPI:
    base_url: str = ""
    _reload: bool = False

    def _headers(self) -> dict:
        return {}

    def _get(self, url, params={}, cached=True):
        full_url = self.base_url + url
        param_key = full_url + "|" + json.dumps(params)
        if cached and not self._reload:
            if param_key in cache:
                return cache[param_key]
        response = requests.get(
            full_url, headers=self._headers(), params=params
        )
        result = response.json()
        if cached:
            cache[param_key] = result
        return result

    def _post(self, url, params={}, cached=False):
        full_url = self.base_url + url
        param_key = full_url + "|" + json.dumps(params)
        if cached and not self._reload:
            if param_key in cache:
                return cache[param_key]
        response = requests.post(
            full_url, headers=self._headers(), json=params
        )
        result = response.json()
        if cached:
            print(param_key)
            cache[param_key] = result
        return result

    def __init__(self):
        self.reload = False

    def reload(self):
        self._reload = True
        return self
    
    def cached(self):
        self._reload = False
        return self