# plotter.py
import mplfinance as mpf
import pandas as pd
import os

def plot_stock_chart(stock_id, df, strategy_res):
    """
    功能：畫出個股 K 線圖 + MA60 + 支撐壓力線
    回傳：圖片的檔名 (例如 '2330.png')
    """
    try:
        # 1. 資料處理：確保索引是日期格式 (mplfinance 要求)
        df_plot = df.copy()
        df_plot['trade_date'] = pd.to_datetime(df_plot['trade_date'])
        df_plot.set_index('trade_date', inplace=True)
        
        # 只取最近 60 天 (畫面比較清楚)
        df_plot = df_plot.tail(60)
        
        # 2. 準備額外的線圖 (MA60, S1, R1)
        # S1 (支撐) 用綠色虛線，R1 (壓力) 用紅色虛線
        s1_line = [strategy_res['s1']] * len(df_plot)
        r1_line = [strategy_res['r1']] * len(df_plot)
        
        # 設定附加圖層
        ap = [
            mpf.make_addplot(df_plot['MA60'], color='orange', width=1.5, label='MA60'), # 季線
            mpf.make_addplot(s1_line, color='green', linestyle='--', width=1.0),       # 支撐
            mpf.make_addplot(r1_line, color='red', linestyle='--', width=1.0)          # 壓力
        ]
        
        # 3. 設定存檔路徑 (存到 static 資料夾)
        # 檔名用 stock_id.png，每次覆蓋舊的即可
        filename = f"{stock_id}.png"
        save_path = os.path.join('static', filename)
        
        # 4. 開始畫圖
        # style='yahoo' 是經典紅綠配色
        # volume=True 顯示成交量
        mpf.plot(
            df_plot, 
            type='candle', 
            style='yahoo', 
            volume=True, 
            addplot=ap,
            title=f"\n{stock_id} Analysis",
            savefig=save_path, # 存檔
            tight_layout=True
        )
        
        return filename
        
    except Exception as e:
        print(f"❌ 繪圖失敗: {e}")
        return None