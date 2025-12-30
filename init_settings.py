"""
資料庫設定表初始化腳本 (V2.0 安全增強版)
============================================
執行方式: python init_settings.py
功能: 建立 user_settings 表格並插入預設值
"""
from sqlalchemy import create_engine, text
from config import Config
import sys

# 使用統一設定（避免硬編碼）
DB_URL = Config.SQLALCHEMY_DATABASE_URI


def init_settings_table():
    """建立並初始化 user_settings 表格"""
    print("=" * 60)
    print("🔧 資料庫設定表初始化程序")
    print("=" * 60)
    
    try:
        engine = create_engine(DB_URL)
        print(f"✅ 資料庫連線成功: {DB_URL.split('@')[1]}")
    except Exception as e:
        print(f"❌ 資料庫連線失敗: {e}")
        sys.exit(1)
    
    # 建表 SQL (加入時間戳記與說明欄位)
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS user_settings (
        setting_key VARCHAR(50) PRIMARY KEY,
        setting_value VARCHAR(100) NOT NULL,
        description VARCHAR(200),
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_updated_at (updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """
    
    # 預設參數設定（完整版）
    default_settings = [
        # 策略模式
        ('mode', 'conservative', '策略模式 (conservative穩健/aggressive積極)'),
        
        # AI 參數
        ('ai_threshold', '0.50', 'AI 信心門檻（50%）'),
        ('ai_top_n', '5', 'AI 推薦數量（前N名）'),
        
        # 風控參數
        ('stop_loss', '0.08', '停損點（8%）'),
        ('take_profit', '0.20', '停利點（20%）'),
        ('max_hold_days', '20', '最長持有天數'),
        
        # 倉位管理
        ('max_holdings', '3', '最大持倉數'),
        ('position_size', '0.33', '單筆倉位比例（33%）'),
        
        # 選股篩選器
        ('volume_filter_conservative', '2000000', '成交量門檻-穩健模式（200萬股）'),
        ('volume_filter_aggressive', '1000000', '成交量門檻-積極模式（100萬股）'),
        ('use_ma20_filter', 'true', '是否使用月線過濾（站上MA20）'),
        
        # 功能開關2
        ('enable_news', 'true', '是否啟用新聞推播'),
        ('enable_chips_display', 'true', '是否顯示籌碼資訊'),
        ('enable_strategy_report', 'true', '是否啟用策略報告'),
        
        # 通知設定
        ('notify_threshold', '0.60', '高信心提醒門檻（60%）'),
        ('daily_report_time', '08:00', '每日報告推播時間'),
    ]
    
    try:
        with engine.connect() as conn:
            # 1. 建立表格
            print("\n📋 建立 user_settings 表格...")
            conn.execute(text(create_table_sql))
            
            # 2. 插入預設值（使用參數化查詢避免 SQL Injection）
            insert_sql = """
            INSERT INTO user_settings (setting_key, setting_value, description) 
            VALUES (:key, :value, :desc)
            ON DUPLICATE KEY UPDATE 
                description = :desc,
                updated_at = CURRENT_TIMESTAMP
            """
            
            print("📝 插入預設參數...")
            for key, value, desc in default_settings:
                conn.execute(
                    text(insert_sql), 
                    {'key': key, 'value': value, 'desc': desc}
                )
            
            conn.commit()
            
        print("\n✅ user_settings 表格建立成功！")
        print("\n" + "=" * 60)
        print("📋 預設參數清單")
        print("=" * 60)
        
        # 分類顯示
        categories = {
            '🎯 策略模式': ['mode'],
            '🤖 AI 參數': ['ai_threshold', 'ai_top_n', 'notify_threshold'],
            '🛡️ 風控參數': ['stop_loss', 'take_profit', 'max_hold_days'],
            '💰 倉位管理': ['max_holdings', 'position_size'],
            '📊 選股篩選': ['volume_filter_conservative', 'volume_filter_aggressive', 'use_ma20_filter'],
            '⚙️ 功能開關': ['enable_news', 'enable_chips_display', 'enable_strategy_report'],
            '🔔 通知設定': ['daily_report_time'],
        }
        
        settings_dict = {s[0]: (s[1], s[2]) for s in default_settings}
        
        for category, keys in categories.items():
            print(f"\n{category}")
            print("-" * 60)
            for key in keys:
                if key in settings_dict:
                    value, desc = settings_dict[key]
                    print(f"  {key:<30} = {value:<12} # {desc}")
        
        print("\n" + "=" * 60)
        print("🎉 初始化完成！")
        print("\n💡 提示：")
        print("  - Line Bot 指令：「查看設定」可查看當前參數")
        print("  - Line Bot 指令：「切換積極」或「切換穩健」可切換模式")
        print("  - Line Bot 指令：「設定信心 60」可調整 AI 門檻")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ 初始化失敗: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def verify_settings():
    """驗證設定表是否正常"""
    print("\n🔍 驗證設定表...")
    try:
        engine = create_engine(DB_URL)
        with engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM user_settings")).scalar()
            print(f"✅ 設定表驗證成功！共 {result} 筆參數")
    except Exception as e:
        print(f"❌ 驗證失敗: {e}")


if __name__ == "__main__":
    init_settings_table()
    verify_settings()
