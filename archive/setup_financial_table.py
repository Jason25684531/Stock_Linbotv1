"""
財報資料表建立腳本
============================================
功能：建立 financial_statements 資料表以儲存季報數據
執行方式：python tool/setup_financial_table.py
"""
import sys
import os

# 將專案根目錄加入 Python 路徑
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from config import Config


def create_financial_table():
    """建立財報資料表"""
    engine = create_engine(Config.SQLALCHEMY_DATABASE_URI)
    
    sql_script = """
    CREATE TABLE IF NOT EXISTS financial_statements (
        stock_id VARCHAR(10) NOT NULL COMMENT '股票代號',
        year INT NOT NULL COMMENT '年度 (民國年)',
        quarter INT NOT NULL COMMENT '季度 (1-4)',
        revenue BIGINT COMMENT '營業收入 (千元)',
        rd_expense BIGINT COMMENT '研究發展費用 (千元)',
        eps DECIMAL(10, 4) COMMENT '基本每股盈餘 (元)',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '資料建立時間',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '資料更新時間',
        PRIMARY KEY (stock_id, year, quarter),
        INDEX idx_stock_year (stock_id, year),
        INDEX idx_year_quarter (year, quarter)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='季報財務數據表';
    """
    
    try:
        with engine.connect() as conn:
            conn.execute(text(sql_script))
            conn.commit()
            print("✅ 資料表 financial_statements 建立成功")
            
            # 檢查資料表結構
            result = conn.execute(text("DESCRIBE financial_statements"))
            print("\n📋 資料表結構：")
            for row in result:
                print(f"  {row[0]:15} {row[1]:20} {row[2]:8} {row[3]:8}")
                
        return True
    except Exception as e:
        print(f"❌ 建立資料表失敗: {e}")
        return False


if __name__ == '__main__':
    print("🚀 開始建立財報資料表...")
    success = create_financial_table()
    if success:
        print("\n✅ 資料表建立完成！")
        print("💡 接下來可以執行：python 7_update_financials.py 抓取季報數據")
    else:
        print("\n❌ 資料表建立失敗，請檢查資料庫連線設定")
