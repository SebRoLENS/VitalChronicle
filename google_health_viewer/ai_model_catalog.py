"""Dynamic Ollama model catalogue and hardware-aware recommendations.

The catalogue deliberately keeps VitalChronicle local-first: cloud-tagged models are
never suggested or added to the automatic model list.  A curated fallback keeps the
UI useful offline, while the official Ollama library is queried periodically to find
new generations (for example a future qwen4/gemma5) without requiring an app release.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from .i18n import _

OLLAMA_LIBRARY_URL = "https://ollama.com/library"
OLLAMA_REGISTRY_URL = "https://registry.ollama.ai"
CATALOG_CACHE_SECONDS = 6 * 60 * 60

# These are intentionally real, local Ollama tags with known memory footprints.
# Online discovery supplements this tuple; it does not replace the offline fallback.
CURATED_MODEL_OPTIONS = (
    "qwen3.8",
    "gemma4:12b",
    "qwen3.5:9b",
    "gemma4:e2b",
    "gemma4:e4b",
    "gpt-oss:20b",
    "qwen3:14b",
    "qwen3:30b-a3b",
    "qwen3.5:27b",
    "qwen3.6:35b-a3b",
    "gemma4:26b",
    "gemma4:31b",
    "gemma3:4b",
    "gemma3:12b",
    "gemma3:27b",
    "qwen3:8b",
    "qwen3:4b",
)

MODEL_MEMORY_GB = {
    "qwen3.8": 18.0,
    "qwen3.8:latest": 18.0,
    "qwen3.8:27b": 18.0,
    "gemma4:e2b": 7.2,
    "gemma4:e4b": 9.6,
    "gemma4": 9.6,
    "gemma4:latest": 9.6,
    "gemma4:12b": 7.6,
    "gemma4:26b": 19.0,
    "gemma4:31b": 20.0,
    "gpt-oss": 14.0,
    "gpt-oss:latest": 14.0,
    "gpt-oss:20b": 14.0,
    "gpt-oss:120b": 65.0,
    "gemma3:4b": 3.3,
    "gemma3:12b": 8.1,
    "gemma3:27b": 17.0,
    "qwen3:4b": 2.5,
    "qwen3:8b": 5.2,
    "qwen3.5:9b": 6.6,
    "qwen3:14b": 9.3,
    "qwen3.5:27b": 17.0,
    "qwen3:30b-a3b": 19.0,
    "qwen3.6:35b-a3b": 23.0,
}

CURATED_MODEL_DESCRIPTIONS = {
    "qwen3.8": _("Latest Qwen generation · 27B · about 18 GB · 256K context · thinking"),
    "gemma4:12b": _("Google Gemma 4 · about 7.6 GB · 256K context · strong local reasoning"),
    "qwen3.5:9b": _("Efficient Qwen · about 6.6 GB · good balance for mid-range hardware"),
    "gemma4:e2b": _("Google Gemma 4 edge model · about 7.2 GB · 128K context"),
    "gemma4:e4b": _("Google Gemma 4 edge model · about 9.6 GB · 128K context"),
    "gpt-oss:20b": _("OpenAI open-weight reasoning model · about 14 GB · 128K context"),
    "qwen3:14b": _("Qwen compatibility option · about 9.3 GB"),
    "qwen3:30b-a3b": _("Qwen MoE compatibility option · about 19 GB"),
    "qwen3.5:27b": _("Dense Qwen · about 17 GB · accurate but slow on CPU"),
    "qwen3.6:35b-a3b": _("Large Qwen MoE · about 23 GB · demanding on system memory"),
    "gemma4:26b": _("Google Gemma 4 MoE · about 19 GB · 256K context"),
    "gemma4:31b": _("Google Gemma 4 dense model · about 20 GB · 256K context"),
    "gemma3:4b": _("Compact Google Gemma · about 3.3 GB · compatibility"),
    "gemma3:12b": _("Google Gemma 3 · about 8.1 GB · compatibility"),
    "gemma3:27b": _("Google Gemma 3 · about 17 GB · compatibility"),
    "qwen3:8b": _("Compact Qwen · about 5.2 GB · compatibility"),
    "qwen3:4b": _("Lightweight Qwen · about 2.5 GB · compatibility"),
}

# Search broad families rather than fixed generation names.  This is what lets a
# future qwen4/gemma5 show up automatically.
DISCOVERY_QUERIES = (
    "qwen",
    "gemma",
    "gpt-oss",
    "llama",
    "mistral",
    "deepseek",
    "phi",
    "glm",
    "granite",
    "nemotron",
)
ALLOWED_FAMILY_PREFIXES = DISCOVERY_QUERIES
_FAMILY_LINK_RE = re.compile(r'href=["\']?/library/([A-Za-z0-9][A-Za-z0-9._+-]*)')
_VERSIONED_FAMILY_RE = re.compile(
    r"^(qwen|gemma|llama|mistral|deepseek|phi|glm|granite|nemotron)[-_.]?([0-9]+(?:[._][0-9]+)*)",
    re.IGNORECASE,
)


def is_cloud_model(model: str) -> bool:
    """Return True for Ollama cloud-only selectors that violate local-only semantics."""

    value = model.strip().lower()
    return "cloud" in value or value.startswith("gemini")


def _cache_path() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Caches"
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return root / "VitalChronicle" / "ai-model-catalog.json"


def _read_cache() -> dict[str, Any]:
    try:
        payload = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_cache(payload: dict[str, Any]) -> None:
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        # Discovery is an optional convenience.  A read-only home directory must
        # never prevent the local AI page from opening.
        pass


def _family_sort_key(name: str) -> tuple[str, tuple[int, ...], str]:
    match = _VERSIONED_FAMILY_RE.match(name.lower())
    if not match:
        return (name.lower(), (), name.lower())
    version = tuple(int(part) for part in re.split(r"[._]", match.group(2)))
    return (match.group(1).lower(), version, name.lower())


def _extract_model_families(html: str) -> tuple[str, ...]:
    families: set[str] = set()
    for match in _FAMILY_LINK_RE.finditer(html):
        family = match.group(1).strip().lower()
        if not family or is_cloud_model(family):
            continue
        if not any(family.startswith(prefix) for prefix in ALLOWED_FAMILY_PREFIXES):
            continue
        families.add(family)
    return tuple(sorted(families, key=_family_sort_key, reverse=True))


def _fetch_query(query: str) -> tuple[str, ...]:
    response = requests.get(
        OLLAMA_LIBRARY_URL,
        params={"q": query},
        headers={"User-Agent": "VitalChronicle-model-catalog/1"},
        timeout=4,
    )
    response.raise_for_status()
    return _extract_model_families(response.text)


def discover_model_families(*, force: bool = False) -> tuple[str, ...]:
    """Discover current official Ollama families with a six-hour local cache."""

    cache = _read_cache()
    now = time.time()
    cached = tuple(
        str(item) for item in cache.get("families", []) if item and not is_cloud_model(str(item))
    )
    try:
        timestamp = float(cache.get("updated_at", 0.0) or 0.0)
    except (TypeError, ValueError):
        timestamp = 0.0
    if cached and not force and now - timestamp < CATALOG_CACHE_SECONDS:
        return cached

    discovered: set[str] = set()
    try:
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(_fetch_query, query): query for query in DISCOVERY_QUERIES}
            for future in as_completed(futures):
                try:
                    discovered.update(future.result())
                except (requests.RequestException, ValueError):
                    continue
    except (OSError, RuntimeError):
        discovered.clear()

    if not discovered:
        return cached

    families = tuple(sorted(discovered, key=_family_sort_key, reverse=True))
    cache["families"] = list(families)
    cache["updated_at"] = now
    cache.setdefault("sizes_gb", {})
    _write_cache(cache)
    return families


def discover_model_options(
    *, installed: Iterable[str] = (), force: bool = False
) -> tuple[str, ...]:
    """Return curated tags, locally installed models, and discovered family defaults."""

    result: list[str] = []

    def add(value: str) -> None:
        model = value.strip()
        if not model or is_cloud_model(model) or model in result:
            return
        result.append(model)

    for model in installed:
        add(str(model))
    for model in CURATED_MODEL_OPTIONS:
        add(model)
    for family in discover_model_families(force=force):
        add(family)
    return tuple(result)


def remember_installed_models(model_items: Iterable[dict[str, Any]]) -> None:
    """Cache exact local model sizes reported by Ollama for future recommendations."""

    cache = _read_cache()
    sizes = cache.get("sizes_gb")
    if not isinstance(sizes, dict):
        sizes = {}
    changed = False
    for item in model_items:
        name = str(item.get("name") or "").strip()
        size = item.get("size")
        if not name or is_cloud_model(name) or not isinstance(size, (int, float)) or size <= 0:
            continue
        sizes[name] = round(float(size) / 1024**3, 2)
        changed = True
    if changed:
        cache["sizes_gb"] = sizes
        _write_cache(cache)


def model_memory_gb(model: str) -> float | None:
    name = model.strip().lower()
    if name in MODEL_MEMORY_GB:
        return MODEL_MEMORY_GB[name]
    if name.endswith(":latest") and name.removesuffix(":latest") in MODEL_MEMORY_GB:
        return MODEL_MEMORY_GB[name.removesuffix(":latest")]
    cached = _read_cache().get("sizes_gb", {})
    if isinstance(cached, dict):
        try:
            value = float(cached.get(name))
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
    return None


def _remote_model_size_gb(model: str) -> float | None:
    """Read a default/tag manifest size from Ollama's registry, without downloading weights."""

    name, separator, tag = model.strip().lower().partition(":")
    if not name or "/" in name or is_cloud_model(model):
        return None
    tag = tag if separator and tag else "latest"
    try:
        response = requests.get(
            f"{OLLAMA_REGISTRY_URL}/v2/library/{quote(name, safe='')}/manifests/{quote(tag, safe='')}",
            headers={
                "Accept": (
                    "application/vnd.oci.image.manifest.v1+json, "
                    "application/vnd.docker.distribution.manifest.v2+json"
                )
            },
            timeout=5,
        )
        response.raise_for_status()
        payload = response.json()
        layers = payload.get("layers") or []
        total = sum(
            float(layer.get("size", 0) or 0)
            for layer in layers
            if isinstance(layer, dict)
        )
    except (requests.RequestException, TypeError, ValueError):
        return None
    if total <= 0:
        return None
    size_gb = round(total / 1024**3, 2)
    cache = _read_cache()
    sizes = cache.get("sizes_gb")
    if not isinstance(sizes, dict):
        sizes = {}
    sizes[model.strip().lower()] = size_gb
    cache["sizes_gb"] = sizes
    _write_cache(cache)
    return size_gb


def model_description(model: str) -> str:
    value = model.strip()
    if value in CURATED_MODEL_DESCRIPTIONS:
        return CURATED_MODEL_DESCRIPTIONS[value]
    if is_cloud_model(value):
        return _("Cloud model blocked: VitalChronicle health analysis is designed to remain local.")
    memory = model_memory_gb(value)
    if memory is not None:
        return _(
            "Local Ollama model · about {size:.1f} GB · detected dynamically",
            size=memory,
        )
    cached_families = tuple(
        str(item) for item in _read_cache().get("families", []) if item
    )
    if value in cached_families:
        return _(
            "Official Ollama family discovered dynamically. Check/download it locally to read exact size and context."
        )
    return _("Custom local model: check that the name is available in Ollama.")


def recommended_model_for_legacy_profile(profile: str) -> str:
    # Keep the mid-range default conservative; CPU/32 GB can now use Qwen3.8.
    return "qwen3.8" if profile == "cpu32" else "qwen3.5:9b"


def _hardware_base_models(
    ram_gb: float, vram_gb: float | None, has_gpu: bool
) -> dict[str, str]:
    if has_gpu:
        vram = vram_gb or 0.0
        if vram >= 24:
            return {
                "fast": "qwen3.5:9b",
                "standard": "gemma4:26b",
                "max": "qwen3.8",
            }
        if vram >= 16:
            return {
                "fast": "qwen3.5:9b",
                "standard": "gemma4:12b",
                "max": "qwen3.8",
            }
        if vram >= 7:
            return {
                "fast": "qwen3:4b",
                "standard": "qwen3.5:9b",
                "max": "gemma4:12b",
            }
        if vram >= 4:
            return {
                "fast": "qwen3:4b",
                "standard": "qwen3:8b",
                "max": "qwen3.5:9b",
            }
    if ram_gb >= 32:
        return {
            "fast": "qwen3.5:9b",
            "standard": "gemma4:12b",
            "max": "qwen3.8",
        }
    if ram_gb >= 24:
        return {
            "fast": "qwen3:4b",
            "standard": "qwen3.5:9b",
            "max": "gemma4:12b",
        }
    if ram_gb >= 16:
        return {
            "fast": "qwen3:4b",
            "standard": "qwen3:8b",
            "max": "qwen3.5:9b",
        }
    if ram_gb >= 12:
        return {
            "fast": "qwen3:4b",
            "standard": "qwen3:4b",
            "max": "qwen3:8b",
        }
    return {"fast": "qwen3:4b", "standard": "qwen3:4b", "max": "qwen3:4b"}


def _family_version(name: str) -> tuple[str, tuple[int, ...]] | None:
    family = name.strip().lower().split(":", 1)[0]
    match = _VERSIONED_FAMILY_RE.match(family)
    if not match:
        return None
    version = tuple(int(part) for part in re.split(r"[._]", match.group(2)))
    return match.group(1).lower(), version


def _capacity_gb(ram_gb: float, vram_gb: float | None, has_gpu: bool) -> float:
    if has_gpu and vram_gb:
        # Conservative mixed-offload allowance: full VRAM plus only part of spare
        # system RAM. This avoids automatically choosing models that technically
        # load but make the desktop unusably slow.
        return max(vram_gb, vram_gb + max(0.0, ram_gb - 8.0) * 0.35)
    return max(2.5, ram_gb - 5.0)


def _newest_compatible_family(
    model: str,
    candidates: Iterable[str],
    *,
    capacity_gb: float,
    allow_unknown_size: bool,
) -> str | None:
    current = _family_version(model)
    if current is None:
        return None
    root, current_version = current
    newer: list[tuple[tuple[int, ...], str]] = []
    for candidate in candidates:
        parsed = _family_version(candidate)
        if parsed is None or parsed[0] != root or parsed[1] <= current_version:
            continue
        newer.append((parsed[1], candidate.strip()))
    for _version, candidate in sorted(newer, reverse=True):
        size = model_memory_gb(candidate)
        if size is None and allow_unknown_size:
            size = _remote_model_size_gb(candidate)
        if size is None:
            if allow_unknown_size:
                return candidate
            continue
        if size <= capacity_gb:
            return candidate
    return None


def recommended_model_for_hardware(
    *,
    ram_gb: float,
    vram_gb: float | None,
    has_gpu: bool,
    profile: str = "standard",
) -> str:
    profile = profile if profile in {"fast", "standard", "max"} else "standard"
    base = _hardware_base_models(ram_gb, vram_gb, has_gpu)[profile]

    # Automatic upgrades use only already-cached discovery data, so editing a RAM
    # field never blocks the GUI on network access. The regular status check fills
    # this cache every six hours.
    cached_families = tuple(
        str(item) for item in _read_cache().get("families", []) if item
    )
    if not cached_families:
        return base
    successor = _newest_compatible_family(
        base,
        cached_families,
        capacity_gb=_capacity_gb(ram_gb, vram_gb, has_gpu),
        allow_unknown_size=False,
    )
    return successor or base


def newer_model_suggestion(
    model: str,
    profile: str,
    *,
    catalog_models: Iterable[str] = (),
) -> str | None:
    """Suggest a newer same-family generation discovered from Ollama.

    Legacy profile capacity is intentionally conservative. Unknown future sizes are
    allowed for CPU/32 GB as an *offer to inspect/download*, but not for the smaller
    GPU16 profile; exact hardware-aware recommendations remain stricter.
    """

    candidates = tuple(catalog_models) or CURATED_MODEL_OPTIONS
    capacity = 25.0 if profile == "cpu32" else 10.5
    return _newest_compatible_family(
        model,
        candidates,
        capacity_gb=capacity,
        allow_unknown_size=profile == "cpu32",
    )
