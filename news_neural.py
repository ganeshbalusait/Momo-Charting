"""Local financial-text neural sentiment. No model downloads or paid fallback at runtime."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import threading

from news_jev import JevNewsJudge

MODEL_ID = "financial-distilroberta-int8"
REVISION = "e6953e30167c7fc3d1ad62f3df31617dd712fbc1"
MODEL_DIR = Path(__file__).resolve().parent / "artifacts" / "local_news_model"
_READERS = {}
_READERS_LOCK = threading.Lock()


class LocalSentiment:
    def __init__(self, directory=MODEL_DIR):
        self.directory = Path(directory)
        self.session = None
        self.lock = threading.Lock()

    def __call__(self, text: str) -> dict:
        # Runs only on the background reader. ONNX uses one CPU thread and
        # releases Python's GIL, preserving foreground chart responsiveness.
        import numpy as np
        with self.lock:
            if self.session is None:
                import onnxruntime as ort
                from tokenizers import Tokenizer
                config = json.loads((self.directory / "config.json").read_text(encoding="utf-8"))
                manifest = json.loads((self.directory / "manifest.json").read_text(encoding="utf-8"))
                if manifest.get("revision") != REVISION:
                    raise ValueError("Local news model revision mismatch")
                self.labels = {int(k): v for k, v in config["id2label"].items()}
                if set(self.labels.values()) != {"positive", "negative", "neutral"}:
                    raise ValueError("Unexpected sentiment labels")
                self.tokenizer = Tokenizer.from_file(str(self.directory / "tokenizer.json"))
                self.tokenizer.enable_truncation(max_length=128)
                options = ort.SessionOptions()
                options.intra_op_num_threads = 1
                options.inter_op_num_threads = 1
                options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                options.add_session_config_entry("session.intra_op.allow_spinning", "0")
                self.session = ort.InferenceSession(str(self.directory / "onnx/model_quantized.onnx"),
                    sess_options=options, providers=["CPUExecutionProvider"])
            encoded = self.tokenizer.encode(str(text)[:2000])
            available = {"input_ids": encoded.ids, "attention_mask": encoded.attention_mask,
                         "token_type_ids": encoded.type_ids}
            inputs = {node.name: np.array([available[node.name]], dtype=np.int64) for node in self.session.get_inputs()}
            logits = np.asarray(self.session.run(None, inputs)[0][0], dtype=float)
            if logits.shape != (3,) or not np.isfinite(logits).all():
                raise ValueError("Invalid model output")
            weights = np.exp(logits - logits.max())
            probabilities = weights / weights.sum()
            return {self.labels[i]: float(p) for i, p in enumerate(probabilities)}


class NeuralNewsJudge(JevNewsJudge):
    def __init__(self, path: Path, *, inference=None, **kwargs):
        self.model_id = MODEL_ID
        self.inference = inference or LocalSentiment()
        super().__init__(path, key="local-only", post=self._local_post, **kwargs)
        self.enabled = inference is not None or (all(importlib.util.find_spec(m) for m in ("onnxruntime", "tokenizers"))
                         and (MODEL_DIR / "manifest.json").is_file())

    def _cache_key(self, item):
        # Re-read revised articles even when the publisher keeps its URL.
        content = [REVISION, item.get("symbol"), item.get("url"), item.get("headline"), str(item.get("summary") or "")[:1200]]
        return hashlib.sha256(json.dumps(content, ensure_ascii=False).encode()).hexdigest()

    def _can_queue(self):
        return self.enabled and len(self._queued) < 200

    def _local_post(self, body, unused_key):
        state = body["state"]
        text = str(state.get("headline") or "") + ". " + str(state.get("summary") or "")[:1200]
        probabilities = self.inference(text)
        if set(probabilities) != {"positive", "negative", "neutral"}:
            raise ValueError("Incomplete sentiment probabilities")
        values = [float(v) for v in probabilities.values()]
        if any(not 0 <= v <= 1 for v in values) or abs(sum(values) - 1) > 1e-4:
            raise ValueError("Invalid sentiment probabilities")
        mapping = {"positive": "bullish", "negative": "bearish", "neutral": "not_important"}
        probs = {mapping[k]: v for k, v in probabilities.items()}
        choice = max(probs, key=probs.get)
        return {"model": MODEL_ID, "answers": {"impact": {"type": "choice", "choice": choice,
                "probabilities": probs, "confidence": probs[choice]}}}

    def _judge(self, key, state):
        super()._judge(key, state)
        with self._lock:
            if key in self._cache:
                self._cache[key].update(provider="local-neural", model=MODEL_ID, revision=REVISION)

    def annotate(self, payload):
        items = []
        if isinstance(payload, dict):
            items = [x for x in payload.get("feed") or [] if isinstance(x, dict)]
            items += [x for x in (payload.get("latest") or {}).values() if isinstance(x, dict)]
        for item in items:
            item.pop("jev", None)
            item.pop("aiNews", None)
        # This classifier scores the text, not each entity in a mixed story.
        # A broad/multi-company headline cannot supply a ticker-specific sign.
        ambiguous = {id(item) for item in items if item.get("scope") == "market-wide"
                     or len(item.get("relatedSymbols") or []) > 1}
        if isinstance(payload, dict):
            super().annotate({"feed": [item for item in payload.get("feed") or [] if id(item) not in ambiguous],
                              "latest": {key: item for key, item in (payload.get("latest") or {}).items() if id(item) not in ambiguous}})
        for item in items:
            if id(item) in ambiguous:
                item["aiNews"] = {"label": "not_important", "p": None, "mixed": True, "provider": "local-neural",
                                  "model": "relevance-filter", "why": "Broad or multi-company story; no ticker-specific direction inferred"}
                continue
            reading = item.pop("jev", None)
            if reading:
                with self._lock:
                    original = dict(self._cache.get(self._cache_key(item)) or {})
                provenance = {key: original[key] for key in ("judgedAt", "publishedAt", "revision") if key in original}
                item["aiNews"] = {**reading, "provider": "local-neural",
                                  **provenance,
                                  "why": str(reading.get("why") or "").replace("Jev", "AI"),
                                  "model": "relevance-filter" if reading.get("offTopic") else MODEL_ID}
        return payload

    def status(self):
        return {**super().status(), "provider": "local-neural", "model": MODEL_ID, "revision": REVISION,
                "loaded": getattr(self.inference, "session", None) is not None,
                "paidApi": False, "reason": None if self.enabled else "Local news runtime or model is not installed"}


def get_news_judge(directory):
    mode = os.getenv("AGX_NEWS_MODEL", "jev").lower()
    key = (str(directory), mode)
    with _READERS_LOCK:
        if key not in _READERS:
            _READERS[key] = (NeuralNewsJudge(Path(directory) / "momx_neural_news.json") if mode == "local-neural"
                             else JevNewsJudge(Path(directory) / "momx_jev_news.json", **({"key": ""} if mode == "disabled" else {})))
        return _READERS[key]
