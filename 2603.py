# reset_today.py
from sqlalchemy import create_engine, text

DB_URL = "mysql+pymysql://root:my_secret_password@localhost:3306/stock_ai_db"
engine = create_engine(DB_URL)

with engine.connect() as conn:
    print("🗑️ 正在刪除 2025-12-26 的資料，準備重新抓取...")
    conn.execute(text("DELETE FROM daily_market_data WHERE trade_date = '2025-12-26'"))
    conn.commit()
    print("✅ 刪除完成！現在可以重新跑更新了。")