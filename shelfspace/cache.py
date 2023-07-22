from contextlib import ContextDecorator
import json


class Cache:
    _kv: dict
    filename = ".cache"

    def __init__(self):
        self.load()

    def __getitem__(self, key):
        return self._kv[key]

    def __setitem__(self, key, value):
        self._kv[key] = value

    def __contains__(self, value):
        return value in self._kv

    def load(self):
        try:
            with open(self.filename, "r") as f:
                self._kv = json.load(f)
        except FileNotFoundError:
            self._kv = {}
        except json.JSONDecodeError:
            self._kv = {}

    def save(self):
        with open(self.filename, "w") as f:
            json.dump(self._kv, f)


cache = Cache()


class cached(ContextDecorator):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        cache.save()
        return False
