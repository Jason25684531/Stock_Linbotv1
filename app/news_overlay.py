"""News sentiment summary and overlay helpers for LINE/dashboard signals."""

# -*- coding: utf-8 -*-
import threading
import time
from contextlib import contextmanager

import pandas as pd

import app as app_pkg
from config import Config
from core.db_helper import get_news_sentiment, get_stock_sector, safe_float, safe_int


_stock_news_runtime = threading.local()


def _get_current_stock_news_deadline() -> float | None:
    deadline = getattr(_stock_news_runtime, 'deadline_monotonic', None)
    if isinstance(deadline, (int, float)):
        return float(deadline)
    return None


@contextmanager
def _live_signal_news_timeout_scope():
    timeout_seconds = 0.0
    try:
        timeout_seconds = max(0.0, float(Config.DASHBOARD_NEWS_TIMEOUT_SECONDS))
    except Exception:
        timeout_seconds = 3.0

    previous_deadline = _get_current_stock_news_deadline()
    next_deadline = previous_deadline
    if timeout_seconds > 0:
        candidate_deadline = time.monotonic() + timeout_seconds
        next_deadline = min(previous_deadline, candidate_deadline) if previous_deadline is not None else candidate_deadline

    _stock_news_runtime.deadline_monotonic = next_deadline
    try:
        yield next_deadline
    finally:
        if previous_deadline is None:
            if hasattr(_stock_news_runtime, 'deadline_monotonic'):
                delattr(_stock_news_runtime, 'deadline_monotonic')
        else:
            _stock_news_runtime.deadline_monotonic = previous_deadline


def _parse_news_reason(news_reason: str) -> dict:
    raw = str(news_reason or '').strip()
    normalized = raw.replace('|', '｜')
    items = [part.strip() for part in normalized.split('｜') if part.strip()]
    is_bearish = any(('利空' in item or '承壓' in item or '下修' in item or '風險' in item) for item in items)
    return {
        'raw': raw,
        'items': items,
        'is_bearish': is_bearish,
        'title': '🔴 利空警示' if is_bearish else '🟢 利多原因',
    }


def _get_stock_mentions_map(stock_ids: list[str]) -> dict:
    if not stock_ids:
        return {}
    if not Config.is_news_boost_enabled():
        return {}
    try:
        from core.news_agent import get_stock_news_mentions

        return get_stock_news_mentions(
            stock_ids,
            deadline_monotonic=_get_current_stock_news_deadline(),
        )
    except Exception as exc:
        print(f'⚠️ 個股新聞讀取失敗: {exc}')
        return {}


def _get_sector_news_summary(sector: str, date_str: str = None) -> dict:
    payload = {
        'raw': '',
        'items': [],
        'is_bearish': False,
        'title': '',
    }
    if not sector:
        return payload
    if not Config.is_news_boost_enabled():
        return payload

    sentiment = app_pkg.get_news_sentiment(date_str)
    bull_sectors = sentiment.get('bull_sectors', [])
    bear_sectors = sentiment.get('bear_sectors', [])

    if sector in bull_sectors:
        items = []
        theme = (sentiment.get('bull_theme_map') or {}).get(sector)
        if theme:
            items.append(f'主題: {theme}')
        items.extend([str(item) for item in sentiment.get('bull_reasons', []) if str(item).strip()])
        items = items[:3]
        return {
            'raw': '｜'.join(items),
            'items': items,
            'is_bearish': False,
            'title': f'🟢 {sector} 消息面',
        }

    if sector in bear_sectors:
        items = []
        theme = (sentiment.get('bear_theme_map') or {}).get(sector)
        if theme:
            items.append(f'主題: {theme}')
        items.extend([str(item) for item in sentiment.get('bear_reasons', []) if str(item).strip()])
        items = items[:3]
        return {
            'raw': '｜'.join(items),
            'items': items,
            'is_bearish': True,
            'title': f'🔴 {sector} 消息面',
        }

    return payload


def _get_stock_specific_news_summary(stock_id: str, stock_mentions_map: dict) -> dict:
    payload = {
        'raw': '',
        'items': [],
        'is_bearish': False,
        'title': '',
    }
    stock_info = (stock_mentions_map or {}).get(str(stock_id))
    if not stock_info:
        return payload

    score = safe_int(stock_info.get('score')) or 0
    reason = str(stock_info.get('reason') or '').strip()
    if score == 0 or not reason:
        return payload

    is_bearish = score < 0
    item = f'利空: {reason}' if is_bearish else f'利多: {reason}'
    return {
        'raw': item,
        'items': [item],
        'is_bearish': is_bearish,
        'title': '🔴 個股新聞' if is_bearish else '🟢 個股新聞',
    }


def _resolve_signal_news_info(row, date_str: str, stock_mentions_map: dict) -> dict:
    stock_id = str(row.get('stock_id', '')).strip()
    sector = app_pkg.get_stock_sector(stock_id)

    sector_info = app_pkg._get_sector_news_summary(sector, date_str)
    if sector_info['items']:
        return sector_info

    stock_info = _get_stock_specific_news_summary(stock_id, stock_mentions_map)
    if stock_info['items']:
        return stock_info

    return _parse_news_reason(row.get('news_boost_reason') or '')


def _apply_news_sentiment_overlay(candidates: pd.DataFrame, date_str: str) -> pd.DataFrame:
    if candidates is None or candidates.empty:
        return pd.DataFrame() if candidates is None else candidates

    boosted = candidates.copy()
    if 'news_boost_reason' not in boosted.columns:
        boosted['news_boost_reason'] = ''
    if 'ai_score' not in boosted.columns:
        return boosted
    boosted['ai_score'] = pd.to_numeric(boosted['ai_score'], errors='coerce').astype('float64')
    if not Config.is_news_boost_enabled():
        return boosted

    try:
        sentiment = app_pkg.get_news_sentiment(date_str)
        bull_sectors = set(sentiment.get('bull_sectors') or [])
        bear_sectors = set(sentiment.get('bear_sectors') or [])
        bull_theme_map = sentiment.get('bull_theme_map') or {}
        bear_theme_map = sentiment.get('bear_theme_map') or {}
        stock_mentions_map = app_pkg._get_stock_mentions_map([str(sid) for sid in boosted['stock_id'].tolist()])

        bull_factor = min(Config.NEWS_BOOST_FACTOR, Config.NEWS_BOOST_MAX)
        bear_factor = Config.NEWS_PENALTY_FACTOR

        for idx, row in boosted.iterrows():
            stock_id = str(row.get('stock_id', '')).strip()
            sector = app_pkg.get_stock_sector(stock_id)
            score = safe_float(row.get('ai_score')) or 0.0
            reason_parts: list[str] = []

            if sector in bull_sectors:
                score *= (1 + bull_factor)
                topic = bull_theme_map.get(sector)
                reason_parts.append(f'{sector}題材: {topic or "消息面偏多"}')

            stock_news = stock_mentions_map.get(stock_id) or {}
            stock_news_score = safe_int(stock_news.get('score')) or 0
            stock_news_reason = str(stock_news.get('reason') or '').strip()
            if stock_news_score > 0 and stock_news_reason:
                extra = min(bull_factor, max(Config.NEWS_BOOST_MAX - bull_factor, 0))
                if extra > 0:
                    score *= (1 + extra)
                reason_parts.append(f'個股: {stock_news_reason}')
            elif stock_news_score < 0 and stock_news_reason:
                score *= (1 - bear_factor)
                reason_parts.append(f'個股利空: {stock_news_reason}')

            if sector in bear_sectors:
                score *= (1 - bear_factor)
                topic = bear_theme_map.get(sector)
                reason_parts.append(f'{sector}承壓: {topic or "消息面偏空"}')

            boosted.at[idx, 'ai_score'] = score
            boosted.at[idx, 'news_boost_reason'] = '｜'.join(reason_parts)[:100] if reason_parts else ''

        return boosted.sort_values('ai_score', ascending=False)
    except Exception as exc:
        print(f'⚠️ 即時選股消息面加權失敗: {exc}')
        return boosted
