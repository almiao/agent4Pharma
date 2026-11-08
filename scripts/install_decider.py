"""下载固定官方 Decider 发布物，逐文件核对 SHA-256；不读取 Hugging Face 凭证。"""

from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.request import getproxies

import httpx

MODEL = "perplexity-ai/pplx-decider-flash-v1.2"
REVISION = "9ff7cf9838e59df461c276b7699d56b244d2af58"
ROOT = Path("data/models/decider-flash-v1.2").resolve()


def download(name: str, expected: dict | None = None) -> None:
    """有限重试及断点续传，只把与官方校验值匹配的文件改成正式名称。"""
    target = ROOT / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and expected:
        with target.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest == expected["sha256"]:
            print("verified " + name, flush=True)
            return
    partial = target.with_suffix(target.suffix + ".partial")
    for attempt in range(3):
        offset = partial.stat().st_size if partial.exists() else 0
        try:
            with httpx.Client(proxy=getproxies().get("http"), timeout=120, follow_redirects=True) as client:
                headers = {"Range": f"bytes={offset}-"} if offset else {}
                url = f"https://huggingface.co/{MODEL}/resolve/{REVISION}/{name}?download=true"
                with client.stream("GET", url, headers=headers) as response:
                    response.raise_for_status()
                    append = bool(offset and response.status_code == 206)
                    with partial.open("ab" if append else "wb") as stream:
                        count = offset if append else 0
                        last = time.monotonic()
                        for chunk in response.iter_bytes(1024 * 1024):
                            stream.write(chunk)
                            count += len(chunk)
                            if time.monotonic() - last > 25:
                                print(f"{name}: {count // 1048576} MiB", flush=True)
                                last = time.monotonic()
            with partial.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if expected and (partial.stat().st_size != expected["bytes"] or digest != expected["sha256"]):
                raise ValueError("Downloaded file does not match official release manifest: " + name)
            partial.replace(target)
            print("downloaded " + name, flush=True)
            return
        except httpx.HTTPError as exc:
            print(f"retry {attempt + 1} {name}: {type(exc).__name__}", flush=True)
            if attempt == 2:
                raise RuntimeError("Download failed: " + name) from None
            time.sleep(2)


def main() -> None:
    """先取得官方清单，再下载权重与可审阅源码，不自动执行供应方代码。"""
    ROOT.mkdir(parents=True, exist_ok=True)
    download("release-manifest.json")
    manifest = json.loads((ROOT / "release-manifest.json").read_text(encoding="utf-8"))
    files = {name: value for name, value in manifest["files"].items() if name != "README.md"}
    with ThreadPoolExecutor(max_workers=3) as executor:
        for future in as_completed([executor.submit(download, name, value) for name, value in files.items()]):
            future.result()
    (ROOT / "installation.json").write_text(
        json.dumps(
            {
                "model": MODEL,
                "revision": REVISION,
                "verified_files": list(files),
                "manifest_sha256": hashlib.sha256((ROOT / "release-manifest.json").read_bytes()).hexdigest(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
