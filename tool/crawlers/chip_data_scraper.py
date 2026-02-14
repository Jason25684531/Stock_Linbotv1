"""
籌碼面資料爬蟲 — 融資融券 + 自營商
============================================
資料來源：
  - TWSE (上市): https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN
  - TPEx (上櫃): https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/margin_bal_result.php

產出欄位：
  - margin_balance (融資今日餘額, 張)
  - short_balance  (融券今日餘額, 張)

使用方式：
  from tool.crawlers.chip_data_scraper import fetch_margin_balance
  df = fetch_margin_balance('2026-02-13')
"""

import requests
import pandas as pd
import time
import random
from datetime import datetime

# ============================================
# 共用設定
# ============================================

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
}


def _clean_number(x) -> float:
    """清洗數字（去逗號、處理無效值）"""
    if pd.isna(x):
        return 0
    s = str(x).replace(',', '').replace('---', '0').replace('--', '0').strip()
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0


# ============================================
# TWSE 融資融券
# ============================================

def fetch_margin_balance_twse(date_str: str, max_retries: int = 3) -> pd.DataFrame:
    """
    抓取上市 (TWSE) 融資融券餘額

    來源: MI_MARGN endpoint
    欄位: 融資今日餘額、融券今日餘額

    Args:
        date_str: 日期 (YYYY-MM-DD)
        max_retries: 最大重試次數

    Returns:
        DataFrame[stock_id, margin_balance, short_balance] 或空 DataFrame
    """
    clean_date = date_str.replace('-', '')
    url = (
        f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
        f"?date={clean_date}&selectType=ALL&response=json"
    )

    for attempt in range(max_retries):
        try:
            time.sleep(random.uniform(1.0, 2.5))
            res = requests.get(url, headers=HEADERS, timeout=15)
            data = res.json()

            if data.get('stat') != 'OK':
                if attempt < max_retries - 1:
                    continue
                return pd.DataFrame()

            # 找到包含融資融券資料的表格
            tables = data.get('tables', [])
            target_table = None
            for t in tables:
                title = t.get('title', '')
                # MI_MARGN 通常有兩個表：信用交易統計 + 融資融券彙總
                # 我們要個股資料的那張表（包含股票代號）
                if '融資' in title and '融券' in title:
                    target_table = t
                    break

            if target_table is None:
                # Fallback: 取最後一張表（通常是個股資料）
                target_table = tables[-1] if tables else None

            if target_table is None:
                return pd.DataFrame()

            fields = target_table.get('fields', [])
            rows = target_table.get('data', [])

            if not rows:
                return pd.DataFrame()

            df = pd.DataFrame(rows, columns=fields)

            # 動態欄位匹配（名稱可能略有差異）
            id_col = next((c for c in df.columns if '代號' in c), None)
            margin_bal_col = next(
                (c for c in df.columns if '融資' in c and '今日餘額' in c), None
            )
            short_bal_col = next(
                (c for c in df.columns if '融券' in c and '今日餘額' in c), None
            )

            if not id_col or not margin_bal_col or not short_bal_col:
                print(f"  ⚠️ TWSE MI_MARGN 欄位匹配失敗: {list(df.columns)}")
                return pd.DataFrame()

            result = df[[id_col, margin_bal_col, short_bal_col]].copy()
            result.columns = ['stock_id', 'margin_balance', 'short_balance']
            result['stock_id'] = result['stock_id'].astype(str).str.strip()
            result['margin_balance'] = result['margin_balance'].apply(_clean_number)
            result['short_balance'] = result['short_balance'].apply(_clean_number)

            # 過濾無效代號
            result = result[result['stock_id'].str.match(r'^\d{4,6}$')]

            return result

        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 1))
            else:
                print(f"  ❌ TWSE 融資融券抓取失敗: {e}")
                return pd.DataFrame()

    return pd.DataFrame()


# ============================================
# TPEx 融資融券
# ============================================

def fetch_margin_balance_tpex(date_str: str, max_retries: int = 3) -> pd.DataFrame:
    """
    抓取上櫃 (TPEx) 融資融券餘額

    來源: margin_bal_result endpoint
    欄位: 融資今日餘額、融券今日餘額

    Args:
        date_str: 日期 (YYYY-MM-DD)
        max_retries: 最大重試次數

    Returns:
        DataFrame[stock_id, margin_balance, short_balance] 或空 DataFrame
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    minguo_date = f"{dt.year - 1911}/{dt.month:02d}/{dt.day:02d}"

    url = (
        f"https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/"
        f"margin_bal_result.php?l=zh-tw&d={minguo_date}&o=json"
    )

    headers = HEADERS.copy()
    headers.update({
        'Referer': 'https://www.tpex.org.tw/web/stock/margin_trading/'
                   'margin_balance/margin_bal.php',
        'Origin': 'https://www.tpex.org.tw',
    })

    for attempt in range(max_retries):
        try:
            time.sleep(random.uniform(1.5, 3.0))
            res = requests.get(url, headers=headers, timeout=20)

            if res.status_code != 200:
                if attempt < max_retries - 1:
                    continue
                return pd.DataFrame()

            try:
                data = res.json()
            except ValueError:
                if attempt < max_retries - 1:
                    continue
                return pd.DataFrame()

            rows = data.get('aaData', [])
            if not rows:
                return pd.DataFrame()

            df = pd.DataFrame(rows)

            # TPEx 融資融券欄位（位置固定，基於官方 API 規格）
            # 欄位順序：代號(0), 名稱(1), 融資買進(2), 融資賣出(3),
            #           融資現金償還(4), 融資前日餘額(5), 融資今日餘額(6),
            #           融資限額(7), 融資使用率(8),
            #           融券買進(9), 融券賣出(10), 融券現金償還(11),
            #           融券前日餘額(12), 融券今日餘額(13), ...
            if df.shape[1] < 14:
                print(f"  ⚠️ TPEx 融資融券欄位不足: {df.shape[1]} 欄")
                return pd.DataFrame()

            result = df.iloc[:, [0, 6, 13]].copy()
            result.columns = ['stock_id', 'margin_balance', 'short_balance']
            result['stock_id'] = result['stock_id'].astype(str).str.strip()
            result['margin_balance'] = result['margin_balance'].apply(_clean_number)
            result['short_balance'] = result['short_balance'].apply(_clean_number)

            # 過濾無效代號
            result = result[result['stock_id'].str.match(r'^\d{4,6}$')]

            return result

        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(3 * (attempt + 1))
            else:
                print(f"  ❌ TPEx 融資融券抓取失敗: {e}")
                return pd.DataFrame()

    return pd.DataFrame()


# ============================================
# 統一入口
# ============================================

def fetch_margin_balance(date_str: str) -> pd.DataFrame:
    """
    抓取全市場（上市 + 上櫃）融資融券餘額

    合併 TWSE 與 TPEx 的融資融券資料，
    供 1_update_database.py 合併至 daily_market_data。

    Args:
        date_str: 日期 (YYYY-MM-DD)

    Returns:
        DataFrame[stock_id, margin_balance, short_balance]
        若全部失敗則回傳空 DataFrame
    """
    print(f"  🔸 正在抓取融資融券資料...", end="")

    twse_df = fetch_margin_balance_twse(date_str)
    tpex_df = fetch_margin_balance_tpex(date_str)

    parts = []
    if not twse_df.empty:
        parts.append(twse_df)
    if not tpex_df.empty:
        parts.append(tpex_df)

    if not parts:
        print(" (無資料)")
        return pd.DataFrame()

    result = pd.concat(parts, ignore_index=True)
    # 去重（以最新一筆為準）
    result = result.drop_duplicates(subset=['stock_id'], keep='last')

    print(f" ✅ ({len(result)} 檔)")
    return result
