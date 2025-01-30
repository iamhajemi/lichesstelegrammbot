import os
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()

# Bot yapılandırma ayarları
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME")

# Token kontrolü
if not TOKEN or not BOT_USERNAME:
    raise ValueError("Lütfen .env dosyasında TELEGRAM_BOT_TOKEN ve TELEGRAM_BOT_USERNAME değerlerini ayarlayın") 