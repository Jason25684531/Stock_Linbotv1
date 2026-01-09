# Tasks: Sentiment Analysis & Circuit Breaker

## 1. News Agent & Sentiment Engine
- [x] **Config Update**: Add `SENTIMENT_THRESHOLD = -0.5` (Circuit Breaker Trigger) and `ENABLE_SENTIMENT_FILTER = False` (Opt-in) to `config.py`.
- [x] **Refactor `tool/news_agent.py`**:
    - Implement `get_daily_sentiment(date_str)` function.
    - *Dev Note*: Implement a **Mock Provider** first (returning deterministic scores based on date hash) to ensure the pipeline works without external API keys. Add comments for where to plug in real APIs (Google News/LLM) later.

## 2. Circuit Breaker Logic (Strategy Layer)
- [x] **Update `tool/strategy.py`**:
    - In `get_v30_candidates` and `get_best_stocks_v31_hybrid`:
    - Call `news_agent.get_daily_sentiment(today)`.
    - If `score < Config.SENTIMENT_THRESHOLD` and `Config.ENABLE_SENTIMENT_FILTER` is True:
        - **STOP TRADING**: Return empty DataFrame or log a "Circuit Breaker Triggered" warning.

## 3. XGBoost Feature Expansion (Training Layer)
- [x] **Update `3_train_model.py`**:
    - In the data preparation phase, merge the daily sentiment score into the training dataset.
    - Add `sentiment_score` to the `features` list.
    - Ensure `ML_Data/` has a (mock) csv or data source for historical sentiment to prevent training errors.

## 4. Documentation & Cleanup
- [x] Update `UpdateList.md` with all changes made in this phase
- [x] Update `README.md` architecture section to reflect sentiment integration
- [x] Verify code quality: no duplicates, clean readable code
- [x] Ensure all task items are checked off
