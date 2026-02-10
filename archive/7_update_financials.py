import requests
import pandas as pd
from io import StringIO

def check_rd_column(year, season):
    url = "https://mopsov.twse.com.tw/mops/web/ajax_t163sb04"
    print(f"🚀 [最終確認] 連線: {url} (民國{year} Q{season})")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Origin': 'https://mopsov.twse.com.tw',
        'Referer': 'https://mopsov.twse.com.tw/mops/web/t163sb04',
    }
    payload = {
        'encodeURIComponent': '1', 'step': '1', 'firstin': '1', 'off': '1',
        'TYPEK': 'sii', 'year': str(year), 'season': f"{season:02d}",
    }

    try:
        res = requests.post(url, headers=headers, data=payload, timeout=30)
        res.encoding = 'utf-8' # 確認是用 UTF-8
        
        dfs = pd.read_html(StringIO(res.text))
        
        # 檢查表格 4 和 表格 6 (索引是 3 和 5)
        for i in [3, 5]: 
            if i < len(dfs):
                df = dfs[i]
                print(f"\n📊 檢查表格 {i+1} ({df.shape}):")
                # 列出所有欄位名稱
                cols = [str(c).replace(' ','') for c in df.columns]
                # print(cols) # 印出全部欄位
                
                # 搜尋研發
                rd_cols = [c for c in cols if '研發' in c or '研究' in c]
                if rd_cols:
                    print(f"   🎉 恭喜！發現研發欄位: {rd_cols}")
                    # 偷看一下台積電
                    try:
                        # 假設第一欄是代號
                        df.columns = cols
                        row = df[df.iloc[:, 0].astype(str) == '2330']
                        if not row.empty:
                            print(f"   👀 台積電數據: {row[rd_cols[0]].values[0]}")
                    except:
                        pass
                else:
                    print("   ❌ 殘念... 此表格只有 '營業費用' 總數，沒有研發細項。")

    except Exception as e:
        print(f"❌ 錯誤: {e}")

if __name__ == "__main__":
    check_rd_column(112, 3)