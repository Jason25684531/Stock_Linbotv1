"""建立 daily_recommendations 資料表"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tool.db_helper import get_db_engine
from sqlalchemy import text

def create_table():
    engine = get_db_engine()
    
    sql = """
    CREATE TABLE IF NOT EXISTS daily_recommendations (
        id INT AUTO_INCREMENT PRIMARY KEY,
        stock_id VARCHAR(10) NOT NULL,
        trade_date DATE NOT NULL,
        strategy VARCHAR(50) NOT NULL,
        close_price DECIMAL(10, 2),
        ai_score DECIMAL(5, 4),
        rsi DECIMAL(5, 2),
        volume BIGINT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_date_strategy (trade_date, strategy),
        INDEX idx_stock_date (stock_id, trade_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    
    with engine.connect() as conn:
        conn.execute(text(sql))
        conn.commit()
    
    print("✅ daily_recommendations 資料表建立成功")

if __name__ == "__main__":
    create_table()
