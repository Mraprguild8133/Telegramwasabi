        self.api_id = os.getenv('API_ID', '')
        self.api_hash = os.getenv('API_HASH', '')
        self.bot_token = os.getenv('BOT_TOKEN', '')
        
        # Wasabi configuration
        wasabi_access_key = os.getenv('WASABI_ACCESS_KEY', '')
        wasabi_secret_key = os.getenv('WASABI_SECRET_KEY', '')
        wasabi_bucket = os.getenv('WASABI_BUCKET', '')
        wasabi_region = os.getenv('WASABI_REGION', '')
