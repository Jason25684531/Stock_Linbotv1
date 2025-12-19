class Config:
    # 密碼對應 docker-compose.yaml 裡的 MYSQL_ROOT_PASSWORD
    SQLALCHEMY_DATABASE_URI = 'mysql+pymysql://root:my_secret_password@localhost:3306/stock_ai_db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    LINE_CHANNEL_ACCESS_TOKEN = 'KBl386t0eh2puuuZsgcrGVU2OHJ/Rbyw/h7hEnb6XcWMDGdzUTVEWooMZjoBQtoyqCOIMnd3KHVeA1HAJ1FPJGU2MfDfakPiVZKwvowT6tT4/ZrnqGe+cC61QqZd5S+upAlpMxpftxi6tubsvFYMZwdB04t89/1O/w1cDnyilFU='
    LINE_CHANNEL_SECRET = 'd5357cddabb11529890938731af41f95'
    GEMINI_API_KEY = 'AIzaSyBg-8fEJcjc6LoTVEJQjvrtvGKziKvfgZQ'