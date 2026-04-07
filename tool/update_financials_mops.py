"""
單季財報更新工具 (MCP-backed)
============================================
功能：透過 MCP 抓取指定季度的財報並更新至資料庫
使用方式：python tool/update_financials_mops.py --year 112 --quarter 3
"""
import sys
import os
import argparse
import time
from typing import Any, Mapping

import pandas as pd

# 將專案根目錄加入 Python 路徑，確保能 import tool 模組
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config  # noqa: E402
from tool.db_helper import (  # noqa: E402
    ensure_financial_columns,
    get_db_engine,
    upsert_financial_statements,
)
from tool.mcp_client import MCPClient, MCPClientError  # noqa: E402


ALLOWED_FINANCIAL_UNITS: dict[str, int] = {
    'TWD': 1,
    'thousand_TWD': 1000,
}
REQUIRED_FINANCIAL_COLUMNS = [
    'stock_id',
    'revenue',
    'operating_expense',
    'operating_profit',
]


def build_financial_correlation_id(year: int, quarter: int) -> str:
    """建立季度財報同步使用的 correlation id。"""
    return (
        f"financial-{year + 1911}-Q{quarter}-{int(time.time())}"
    )


def prepare_financial_dataframe(
    payload: Mapping[str, Any],
    expected_west_year: int,
    expected_quarter: int,
) -> pd.DataFrame:
    """驗證 MCP 財報 payload，並轉成既有 DB 契約。"""
    period = payload.get('period')
    if not isinstance(period, Mapping):
        raise ValueError('財報 payload 缺少 period 資訊')

    payload_year = int(period.get('year', 0))
    payload_quarter = int(period.get('quarter', 0))
    if (
        payload_year != expected_west_year
        or payload_quarter != expected_quarter
    ):
        raise ValueError(
            '財報 period 與請求不一致: '
            f'expected={expected_west_year}Q{expected_quarter}, '
            f'actual={payload_year}Q{payload_quarter}'
        )

    unit = str(payload.get('unit', '')).strip()
    if unit not in ALLOWED_FINANCIAL_UNITS:
        raise ValueError(f'不支援的財報 unit: {unit or "<empty>"}')

    df = MCPClient.historical_financial_statements_to_frame(payload)
    if df.empty:
        raise ValueError('MCP 未回傳任何財報資料')

    missing_columns = [
        column
        for column in REQUIRED_FINANCIAL_COLUMNS
        if column not in df.columns
    ]
    if missing_columns:
        raise ValueError(f'財報資料缺少必填欄位: {missing_columns}')

    required_numeric_columns = [
        'revenue',
        'operating_expense',
        'operating_profit',
    ]
    for column in required_numeric_columns:
        df[column] = pd.to_numeric(df[column], errors='coerce')
        if df[column].isna().any():
            raise ValueError(
                f'財報欄位 {column} 含有無法解析的數值'
            )

    if 'rd_expense' not in df.columns:
        df['rd_expense'] = 0
    df['rd_expense'] = pd.to_numeric(
        df['rd_expense'],
        errors='coerce',
    ).fillna(0)

    if 'eps' not in df.columns:
        df['eps'] = 0.0
    df['eps'] = pd.to_numeric(df['eps'], errors='coerce').fillna(0.0)

    multiplier = ALLOWED_FINANCIAL_UNITS[unit]
    for column in [
        'revenue',
        'rd_expense',
        'operating_expense',
        'operating_profit',
    ]:
        df[column] = (df[column] * multiplier).round().astype('int64')

    df['stock_id'] = (
        df['stock_id']
        .astype(str)
        .str.replace('.0', '', regex=False)
        .str.strip()
    )
    df = df[df['stock_id'].str.match(r'^\d{4}$')]
    if df.empty:
        raise ValueError('財報資料無有效四碼股票代號')

    df['year'] = expected_west_year
    df['quarter'] = expected_quarter
    df['unit'] = unit
    return df[
        [
            'stock_id',
            'year',
            'quarter',
            'revenue',
            'rd_expense',
            'operating_expense',
            'operating_profit',
            'eps',
            'unit',
        ]
    ]


def load_quarter_financial_dataframe(
    year: int,
    quarter: int,
    mcp_client: MCPClient | None = None,
) -> tuple[pd.DataFrame, str]:
    """透過 MCP 載入單一季度財報並轉成既有 DB 契約。"""
    west_year = year + 1911
    correlation_id = build_financial_correlation_id(year, quarter)
    client = mcp_client or MCPClient()
    payload = client.fetch_historical_financial_statements_sync(
        west_year,
        quarter,
        market=Config.MCP_DEFAULT_MARKET,
        correlation_id=correlation_id,
    )

    try:
        df = prepare_financial_dataframe(payload, west_year, quarter)
    except ValueError as exc:
        raise ValueError(f'correlation_id={correlation_id} | {exc}') from exc

    return df, correlation_id


def update_quarter(
    year: int,
    quarter: int,
    dry_run: bool = False,
    mcp_client: MCPClient | None = None,
) -> bool:
    """
    更新單一季度的財報數據
    
    Args:
        year: 民國年（如 112）
        quarter: 季度（1-4）
        dry_run: 是否為測試模式（不實際寫入資料庫）
        mcp_client: 可選的共用 MCP client 實例
    """
    print(f"\n{'='*60}")
    print('📊 財報更新工具 - V35 版本 (MCP financial contract)')
    print(f"{'='*60}")
    print(f"📅 目標季度: 民國 {year} 年 Q{quarter} (西元 {year + 1911} 年)")
    print(f"🔧 模式: {'測試模式 (不寫入)' if dry_run else '正式模式'}")
    print(f"{'='*60}\n")
    
    # 1. 驗證參數
    if not (100 <= year <= 120):
        print("❌ 民國年份應介於 100-120")
        return False
    if not (1 <= quarter <= 4):
        print("❌ 季度應介於 1-4")
        return False

    correlation_id = 'n/a'
    try:
        df, correlation_id = load_quarter_financial_dataframe(
            year,
            quarter,
            mcp_client=mcp_client,
        )
    except MCPClientError as exc:
        print(
            '❌ MCP 財報抓取失敗: '
            f'correlation_id={exc.correlation_id} | {exc}'
        )
        return False
    except ValueError as exc:
        print(f'❌ 財報資料驗證失敗: {exc}')
        return False

    print(f'🔗 correlation_id: {correlation_id}')
    print(f"\n✅ 成功爬取 {len(df)} 筆資料")
    print("\n📋 資料預覽（前 5 筆）:")
    print(df.head().to_string(index=False))

    if dry_run:
        print("\n⚠️ 測試模式結束，未寫入資料庫")
        return True

    # 3. 寫入資料庫
    print("\n📝 準備寫入資料庫...")
    engine = get_db_engine()

    try:
        west_year = year + 1911
        with engine.begin() as conn:
            ensure_financial_columns(conn)
            inserted_count = upsert_financial_statements(
                conn,
                df,
                west_year,
                quarter,
            )
            print(f"   ✅ 新增/更新資料: {inserted_count} 筆")

        print(f"\n{'='*60}")
        print("✅ 更新完成！")
        print(f"{'='*60}\n")
        return True

    except Exception as exc:
        print(
            '❌ 資料庫寫入失敗: '
            f'correlation_id={correlation_id} | {exc}'
        )
        return False


def main():
    parser = argparse.ArgumentParser(
        description="更新指定季度的財報數據",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
範例:
  # 更新 112 年 Q3
  python tool/update_financials_mops.py --year 112 --quarter 3
  
  # 測試模式（不寫入資料庫）
  python tool/update_financials_mops.py --year 112 --quarter 3 --dry-run
  
  # 使用預設值（年份和季度）
  python tool/update_financials_mops.py
        """
    )
    
    parser.add_argument('--year', type=int, default=113, help='民國年（預設 113）')
    parser.add_argument('--quarter', type=int, default=3, help='季度 1-4（預設 3）')
    parser.add_argument('--dry-run', action='store_true', help='測試模式，不實際寫入資料庫')
    
    args = parser.parse_args()
    
    success = update_quarter(args.year, args.quarter, args.dry_run)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
