"""資料庫設定表初始化腳本。"""

import sys

from sqlalchemy import text

from config.settings import (
    DEFAULT_USER_SETTINGS,
    USER_SETTINGS_CATEGORIES,
    USER_SETTINGS_CREATE_TABLE_SQL,
    USER_SETTINGS_UPSERT_SQL,
    get_user_settings_dict,
)
from core.db_helper import get_db_engine


def init_settings_table():
    """建立並初始化 user_settings 表格"""
    print("=" * 60)
    print("🔧 資料庫設定表初始化程序")
    print("=" * 60)
    
    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            # 測試連接
            conn.execute(text("SELECT 1"))
        print(f"✅ 資料庫連線成功")
    except Exception as e:
        print(f"❌ 資料庫連線失敗: {e}")
        sys.exit(1)
    
    try:
        with engine.connect() as conn:
            # 1. 建立表格
            print("\n📋 建立 user_settings 表格...")
            conn.execute(text(USER_SETTINGS_CREATE_TABLE_SQL))
            
            print("📝 插入預設參數...")
            for key, value, desc in DEFAULT_USER_SETTINGS:
                conn.execute(
                    text(USER_SETTINGS_UPSERT_SQL), 
                    {'key': key, 'value': value, 'desc': desc}
                )
            
            conn.commit()
            
        print("\n✅ user_settings 表格建立成功！")
        print("\n" + "=" * 60)
        print("📋 預設參數清單")
        print("=" * 60)
        
        settings_dict = get_user_settings_dict(DEFAULT_USER_SETTINGS)
        
        for category, keys in USER_SETTINGS_CATEGORIES.items():
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
        engine = get_db_engine()
        with engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM user_settings")).scalar()
            print(f"✅ 設定表驗證成功！共 {result} 筆參數")
    except Exception as e:
        print(f"❌ 驗證失敗: {e}")


if __name__ == "__main__":
    init_settings_table()
    verify_settings()
