"""Hardware detection and local-model performance recommendations."""

from __future__ import annotations

import ctypes
import json
import os
import platform
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, replace
from typing import Any

import requests

from .ai_model_catalog import (
    model_memory_gb as catalog_model_memory_gb,
    recommended_model_for_hardware,
)

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
PERFORMANCE_PROFILES = ("fast", "standard", "max")
PERFORMANCE_LABELS = {
    "fast": "Fast",
    "standard": "Standard",
    "max": "Maximum quality",
}
MODEL_MEMORY_GB = {
    "qwen3:4b": 2.5,
    "qwen3:8b": 5.2,
    "qwen3.5:9b": 6.6,
    "qwen3:14b": 9.3,
    "qwen3.5:27b": 17.0,
    "qwen3:30b-a3b": 19.0,
    "qwen3.6:35b-a3b": 23.0,
}


@dataclass(frozen=True)
class HardwareInfo:
    os_name: str
    cpu_name: str
    cpu_cores: int
    ram_gb: float
    gpu_name: str = ""
    gpu_vendor: str = ""
    vram_gb: float | None = None

    @property
    def has_gpu(self) -> bool:
        return bool(self.gpu_name.strip())


@dataclass(frozen=True)
class ModelRecommendation:
    profile: str
    model: str
    model_memory_gb: float | None
    expected_time: str
    rationale: str


@dataclass(frozen=True)
class BenchmarkResult:
    model: str
    tokens_per_second: float
    generated_tokens: int
    elapsed_seconds: float


def _run(command: list[str], timeout: float = 4.0) -> str:
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _ram_gb() -> float:
    if os.name == "nt":

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return round(status.ullTotalPhys / 1024**3, 1)
        except (AttributeError, OSError):
            pass
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return round(float(pages * page_size) / 1024**3, 1)
    except (AttributeError, OSError, ValueError):
        return 0.0


def _cpu_name() -> str:
    if sys_name := platform.system():
        if sys_name == "Linux":
            try:
                with open("/proc/cpuinfo", encoding="utf-8", errors="ignore") as cpuinfo:
                    for line in cpuinfo:
                        if line.lower().startswith("model name"):
                            return line.split(":", 1)[1].strip()
            except OSError:
                pass
        elif sys_name == "Darwin":
            value = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
            if value:
                return value
    return platform.processor().strip() or platform.machine() or "Unknown CPU"


def _vendor_from_name(name: str) -> str:
    lowered = name.lower()
    if "nvidia" in lowered or "geforce" in lowered or "quadro" in lowered:
        return "NVIDIA"
    if "amd" in lowered or "radeon" in lowered:
        return "AMD"
    if "intel" in lowered or "arc" in lowered:
        return "Intel"
    if "apple" in lowered:
        return "Apple"
    return ""


def _parse_memory_gb(value: str) -> float | None:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(GB|MB)", value, re.IGNORECASE)
    if not match:
        return None
    amount = float(match.group(1))
    return amount if match.group(2).upper() == "GB" else amount / 1024.0


def _detect_nvidia() -> tuple[str, str, float | None] | None:
    if not shutil.which("nvidia-smi"):
        return None
    output = _run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total",
            "--format=csv,noheader,nounits",
        ]
    )
    if not output:
        return None
    first = output.splitlines()[0]
    parts = [part.strip() for part in first.split(",", 1)]
    name = parts[0]
    try:
        vram = float(parts[1]) / 1024.0 if len(parts) > 1 else None
    except ValueError:
        vram = None
    return name, "NVIDIA", round(vram, 1) if vram is not None else None


def _detect_macos_gpu() -> tuple[str, str, float | None] | None:
    if platform.system() != "Darwin" or not shutil.which("system_profiler"):
        return None
    output = _run(["system_profiler", "SPDisplaysDataType", "-json"], timeout=8.0)
    if not output:
        return None
    try:
        payload = json.loads(output)
        items = payload.get("SPDisplaysDataType") or []
        if not items:
            return None
        item = items[0]
        name = str(item.get("sppci_model") or item.get("_name") or "Apple GPU")
        memory = str(
            item.get("spdisplays_vram") or item.get("spdisplays_vram_shared") or ""
        )
        return name, _vendor_from_name(name) or "Apple", _parse_memory_gb(memory)
    except (TypeError, ValueError, KeyError):
        return None


def _detect_windows_gpu() -> tuple[str, str, float | None] | None:
    if platform.system() != "Windows":
        return None
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            "Get-CimInstance Win32_VideoController | Select-Object -First 1 "
            "Name,AdapterRAM | ConvertTo-Json -Compress"
        ),
    ]
    output = _run(command, timeout=6.0)
    if not output:
        return None
    try:
        payload = json.loads(output)
        name = str(payload.get("Name") or "")
        memory = payload.get("AdapterRAM")
        vram = (
            float(memory) / 1024**3
            if isinstance(memory, (int, float)) and memory > 0
            else None
        )
        return name, _vendor_from_name(name), round(vram, 1) if vram is not None else None
    except (TypeError, ValueError):
        return None


def _detect_linux_gpu() -> tuple[str, str, float | None] | None:
    if platform.system() != "Linux" or not shutil.which("lspci"):
        return None
    output = _run(["lspci"])
    for line in output.splitlines():
        lowered = line.lower()
        if "vga compatible controller" not in lowered and "3d controller" not in lowered:
            continue
        name = line.split(": ", 1)[-1].strip()
        return name, _vendor_from_name(name), None
    return None


def detect_hardware() -> HardwareInfo:
    gpu = (
        _detect_nvidia()
        or _detect_macos_gpu()
        or _detect_windows_gpu()
        or _detect_linux_gpu()
    )
    gpu_name, gpu_vendor, vram_gb = gpu or ("", "", None)
    return HardwareInfo(
        os_name=f"{platform.system()} {platform.release()}".strip(),
        cpu_name=_cpu_name(),
        cpu_cores=os.cpu_count() or 1,
        ram_gb=_ram_gb(),
        gpu_name=gpu_name,
        gpu_vendor=gpu_vendor,
        vram_gb=vram_gb,
    )


def override_hardware(
    hardware: HardwareInfo,
    *,
    ram_gb: float | None = None,
    gpu_name: str | None = None,
    vram_gb: float | None = None,
) -> HardwareInfo:
    name = hardware.gpu_name if gpu_name is None else gpu_name.strip()
    return replace(
        hardware,
        ram_gb=hardware.ram_gb if ram_gb is None else max(0.0, float(ram_gb)),
        gpu_name=name,
        gpu_vendor=_vendor_from_name(name),
        vram_gb=hardware.vram_gb if vram_gb is None else max(0.0, float(vram_gb)),
    )


def legacy_hardware_profile(hardware: HardwareInfo) -> str:
    # The existing token estimator distinguishes a GPU-offload profile from a
    # conservative CPU/system-RAM profile. Non-NVIDIA GPUs remain conservative
    # until their offload behaviour is confirmed by Ollama on that machine.
    return "gpu16" if hardware.gpu_vendor == "NVIDIA" else "cpu32"


def _cpu_models(ram_gb: float) -> dict[str, str]:
    if ram_gb >= 32:
        return {
            "fast": "qwen3:8b",
            "standard": "qwen3:30b-a3b",
            "max": "qwen3.6:35b-a3b",
        }
    if ram_gb >= 24:
        return {
            "fast": "qwen3:4b",
            "standard": "qwen3.5:9b",
            "max": "qwen3:14b",
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
    return {profile: "qwen3:4b" for profile in PERFORMANCE_PROFILES}


def _gpu_models(hardware: HardwareInfo) -> dict[str, str]:
    vram = hardware.vram_gb or 0.0
    if vram >= 24:
        return {
            "fast": "qwen3.5:9b",
            "standard": "qwen3:30b-a3b",
            "max": "qwen3.6:35b-a3b",
        }
    if vram >= 12:
        return {
            "fast": "qwen3:4b",
            "standard": "qwen3:14b",
            "max": "qwen3:30b-a3b" if hardware.ram_gb >= 24 else "qwen3:14b",
        }
    if vram >= 7:
        return {
            "fast": "qwen3:4b",
            "standard": "qwen3.5:9b",
            "max": "qwen3:14b",
        }
    if vram >= 4:
        return {
            "fast": "qwen3:4b",
            "standard": "qwen3:8b",
            "max": "qwen3.5:9b",
        }
    # A detected GPU with unknown/very small dedicated VRAM is useful context,
    # but system RAM remains the safer sizing signal.
    return _cpu_models(hardware.ram_gb)


def _expected_time(hardware: HardwareInfo, profile: str) -> str:
    if hardware.vram_gb and hardware.vram_gb >= 12:
        values = {
            "fast": "usually <1 min",
            "standard": "about 1–3 min",
            "max": "about 3–8 min",
        }
        return values[profile]
    if hardware.vram_gb and hardware.vram_gb >= 6:
        values = {
            "fast": "usually <2 min",
            "standard": "about 2–5 min",
            "max": "about 5–15+ min",
        }
        return values[profile]
    if hardware.ram_gb >= 32:
        values = {
            "fast": "about 1–4 min",
            "standard": "about 5–15 min",
            "max": "15 min or more",
        }
        return values[profile]
    values = {
        "fast": "about 2–6 min",
        "standard": "about 5–15+ min",
        "max": "15 min or more",
    }
    return values[profile]


def recommend_model(
    hardware: HardwareInfo, profile: str = "standard"
) -> ModelRecommendation:
    profile = profile if profile in PERFORMANCE_PROFILES else "standard"
    model = recommended_model_for_hardware(
        ram_gb=hardware.ram_gb,
        vram_gb=hardware.vram_gb,
        has_gpu=hardware.has_gpu,
        profile=profile,
    )
    if hardware.has_gpu:
        accelerator = hardware.gpu_name
        memory_text = (
            f"{hardware.vram_gb:.1f} GB VRAM"
            if hardware.vram_gb is not None
            else "VRAM not reported"
        )
        rationale = (
            f"Sized for {accelerator} ({memory_text}) and {hardware.ram_gb:.0f} GB RAM. "
            "The catalogue can adopt newer compatible Ollama generations automatically."
        )
    else:
        rationale = (
            f"Sized conservatively for CPU inference and {hardware.ram_gb:.0f} GB RAM. "
            "The catalogue can adopt newer compatible Ollama generations automatically."
        )
    return ModelRecommendation(
        profile=profile,
        model=model,
        model_memory_gb=catalog_model_memory_gb(model) or MODEL_MEMORY_GB.get(model),
        expected_time=_expected_time(hardware, profile),
        rationale=rationale,
    )


def reasoning_value(model: str, profile: str) -> bool | str:
    """Return Ollama's `think` value for the chosen performance profile.

    GPT-OSS exposes low/medium/high reasoning levels. Models such as Qwen 3
    expose thinking primarily as an on/off capability, so Fast disables the
    reasoning trace while Standard/Maximum keep it enabled; the model size is
    what primarily differentiates those three profiles.
    """

    profile = profile if profile in PERFORMANCE_PROFILES else "standard"
    normalized = model.strip().lower()
    if normalized.startswith("gpt-oss") or "/gpt-oss" in normalized:
        return {"fast": "low", "standard": "medium", "max": "high"}[profile]
    return profile != "fast"


def benchmark_model(
    model: str,
    *,
    base_url: str = DEFAULT_OLLAMA_URL,
    timeout: float = 120.0,
) -> BenchmarkResult:
    """Run a short local generation and return Ollama's measured decode speed."""

    started = time.monotonic()
    response = requests.post(
        f"{base_url.rstrip('/')}/api/generate",
        json={
            "model": model,
            "prompt": (
                "In one short paragraph, explain why local data processing protects privacy."
            ),
            "stream": False,
            "think": reasoning_value(model, "fast"),
            "options": {"temperature": 0.0, "num_ctx": 2048, "num_predict": 96},
            "keep_alive": "5m",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    elapsed = max(0.001, time.monotonic() - started)
    count = int(payload.get("eval_count") or 0)
    duration_ns = int(payload.get("eval_duration") or 0)
    decode_seconds = duration_ns / 1_000_000_000 if duration_ns > 0 else elapsed
    speed = count / max(0.001, decode_seconds)
    return BenchmarkResult(
        model=model,
        tokens_per_second=speed,
        generated_tokens=count,
        elapsed_seconds=elapsed,
    )
