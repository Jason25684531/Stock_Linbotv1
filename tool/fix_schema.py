import pandas as pd
from sqlalchemy import create_engine, text

# 資料庫連線 (請確認密碼跟你的設定一樣)
DB_URL = "mysql+pymysql://root:my_secret_password@localhost:3306/stock_ai_db"

def fix_db():
    engine = create_engine(DB_URL)
    with engine.connect() as conn:
        print("🛠️ 正在修復資料庫欄位...")
        
        # 1. 新增 foreign_buy (外資)
        try:
            conn.execute(text("ALTER TABLE daily_market_data ADD COLUMN foreign_buy FLOAT DEFAULT 0"))
            print("✅ foreign_buy 新增成功")
        except Exception as e:
            print("👌 foreign_buy 已存在或錯誤:", e)

        # 2. 新增 trust_buy (投信)
        try:
            conn.execute(text("ALTER TABLE daily_market_data ADD COLUMN trust_buy FLOAT DEFAULT 0"))
            print("✅ trust_buy 新增成功")
        except Exception as e:
            print("👌 trust_buy 已存在或錯誤:", e)

if __name__ == "__main__":
    fix_db()