"""Download pinned, public ONNX weights and tokenizer; never execute model code."""
import hashlib
import json
from pathlib import Path
import urllib.request

REPO = "Xenova/distilroberta-finetuned-financial-news-sentiment-analysis"
REVISION = "e6953e30167c7fc3d1ad62f3df31617dd712fbc1"
FILES = ("config.json", "tokenizer.json", "onnx/model_quantized.onnx")
TARGET = Path(__file__).resolve().parents[1] / "artifacts" / "local_news_model"


def main():
    with urllib.request.urlopen(f"https://huggingface.co/api/models/{REPO}/revision/{REVISION}?blobs=true", timeout=30) as response:
        metadata = json.load(response)
    if metadata.get("sha") != REVISION:
        raise RuntimeError("Model revision did not match")
    siblings = {s["rfilename"]: s for s in metadata["siblings"]}
    manifest = {"repository": REPO, "revision": REVISION, "license": "apache-2.0", "files": {}}
    for name in FILES:
        info = siblings[name]
        expected = (info.get("lfs") or {}).get("sha256")
        destination = TARGET / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.is_file() or (expected and hashlib.sha256(destination.read_bytes()).hexdigest() != expected):
            temporary = destination.with_suffix(destination.suffix + ".part")
            with urllib.request.urlopen(f"https://huggingface.co/{REPO}/resolve/{REVISION}/{name}", timeout=60) as response, temporary.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            actual = hashlib.sha256(temporary.read_bytes()).hexdigest()
            if expected and actual != expected:
                raise RuntimeError(f"Checksum mismatch: {name}")
            temporary.replace(destination)
        manifest["files"][name] = hashlib.sha256(destination.read_bytes()).hexdigest()
        print(f"Ready: {name} ({destination.stat().st_size:,} bytes)", flush=True)
    (TARGET / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Pinned local news model installed; no paid API used.", flush=True)


if __name__ == "__main__":
    main()
