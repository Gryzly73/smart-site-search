"""Versioned catalog vocabulary shared by the index and interpreter."""
import hashlib
import json
from functools import lru_cache
from importlib.resources import files


@lru_cache
def _resource_bytes(name: str) -> bytes:
    return files(__package__).joinpath(f"{name}.json").read_bytes()


def read_resource(name: str):
    return json.loads(_resource_bytes(name))


def load_taxonomy() -> dict:
    return {name: read_resource(name) for name in ("classes", "roles", "fields")}


def taxonomy_digest() -> str:
    digest = hashlib.sha256()
    for name in ("classes", "roles", "fields", "classification", "aliases", "relations"):
        digest.update(name.encode())
        digest.update(_resource_bytes(name))
    return digest.hexdigest()
