# Local neural news sentiment

The news reader uses the six-layer financial DistilRoBERTa classifier published by [mrm8488](https://huggingface.co/mrm8488/distilroberta-finetuned-financial-news-sentiment-analysis). CPU INT8 ONNX weights come from [Xenova's export](https://huggingface.co/Xenova/distilroberta-finetuned-financial-news-sentiment-analysis), pinned to revision `e6953e30167c7fc3d1ad62f3df31617dd712fbc1`. The upstream model is Apache-2.0 licensed. The installer records file hashes and checks the large weight file against its published LFS hash. No remote Python model code executes.

Install the optional runtime with `.venv\Scripts\python.exe -m pip install -r requirements-local-news.txt -c requirements.txt`, then run `scripts/install_local_news_model.py`. In `.env`, select `AGX_NEWS_MODEL=local-neural`; keep `AGX_JEV_NEWS=0` and `AGX_JEV_BOT=0` to disable paid JEV requests. Restart the backend after configuration changes. The credential itself is preserved.

The model labels headline/summary text positive, negative or neutral. Existing ticker-relevance and confidence gates are retained. Broad or multi-company stories receive no inferred ticker-specific direction. Confidence is a text-classification score, not a calibrated probability of a stock-price move. This model does not generate explanations of causes or change trade-entry/exit rules.

Inference is queued off the HTTP path, with one CPU thread, a 128-token bound and no thread spinning. At most 200 stories are queued at once. A pinned-model cache deduplicates by ticker and story content, so a revised headline at the same URL is re-read. Paid models are never a failure fallback. Missing runtime, invalid outputs and inference errors leave the AI reading unavailable, rather than inventing a neutral answer. Publisher keyword sentiment remains a separate field.

New annotations are `aiNews` and identify their model, provenance and read time. Legacy `jev` readings are not relabeled as neural output. `/api/news-feed/latest` exposes `newsAi` status. `/api/momx-scanner/status` reports the active local trading model and whether paid JEV trading is enabled.

The local trading model and news model are separate. Numerical paper-trade models are assessed with chronological holdouts. A more complex network is not promoted simply for being larger; no profit or prediction guarantee follows from sentiment or model scores.
