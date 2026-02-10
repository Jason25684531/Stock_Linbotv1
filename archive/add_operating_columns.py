import sys
import os

# 將專案根目錄加入路徑
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from tool.db_helper import get_db_engine

def add_columns():
    print("🔧 正在擴充資料庫欄位...")
    engine = get_db_engine()
    
    with engine.begin() as conn:
        # 1. 新增 營業費用
        try:
            print("   👉 嘗試新增 'operating_expense'...")
            conn.execute(text("ALTER TABLE financial_statements ADD COLUMN operating_expense BIGINT COMMENT '營業費用 (千元)'"))
            print("      ✅ 成功！")
        except Exception as e:
            if "Duplicate column" in str(e) or "exists" in str(e):
                print("      ⚠️ 欄位已存在 (略過)")
            else:
                print(f"      ❌ 失敗: {e}")

        # 2. 新增 營業利益
        try:
            print("   👉 嘗試新增 'operating_profit'...")
            conn.execute(text("ALTER TABLE financial_statements ADD COLUMN operating_profit BIGINT COMMENT '營業利益 (千元)'"))
            print("      ✅ 成功！")
        except Exception as e:
            if "Duplicate column" in str(e) or "exists" in str(e):
                print("      ⚠️ 欄位已存在 (略過)")
            else:
                print(f"      ❌ 失敗: {e}")

if __name__ == "__main__":
    add_columns()