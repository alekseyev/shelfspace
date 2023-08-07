import requests


class BaseAPI:
    base_url: str = ""

    def _headers(self) -> dict:
        return {}

    def _get(self, url, params={}, headers=None):
        if headers is None:
            headers = self._headers()
        full_url = self.base_url + url
        response = requests.get(full_url, headers=headers, params=params)
        result = response.json()
        return result

    def _post(self, url, params={}, headers=None):
        if headers is None:
            headers = self._headers()
        full_url = self.base_url + url
        response = requests.post(full_url, headers=headers, json=params)
        result = response.json()
        return result
