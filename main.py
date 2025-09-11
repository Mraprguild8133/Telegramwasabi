import asyncio
import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
import uuid
import time
import math
import threading

from config import START_PIC
from pyrogram import filters, types
from pyrogram.client import Client
import boto3
from botocore.exceptions import ClientError
from botocore.config import Config
from boto3.s3.transfer import TransferConfig
import aiofiles
from asyncio_throttle import Throttler
from flask import Flask, render_template, request, redirect, url_for, send_file, jsonify, Response
from flask_cors import CORS
import requests
from urllib.parse import quote

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class ProgressTracker:
    """Real-time progress tracking for file operations."""
    
    def __init__(self, total_size: int, update_interval: float = 2.0):
        self.total_size = total_size
        self.update_interval = update_interval
        self.bytes_transferred = 0
        self.last_update_time = 0
        self.start_time = time.time()
        self.lock = threading.Lock()
        self.current_progress = None
    
    def __call__(self, bytes_amount: int):
        """Progress callback for S3 transfers."""
        with self.lock:
            self.bytes_transferred += bytes_amount
            current_time = time.time()
            
            # Update progress every interval or at completion
            if (current_time - self.last_update_time > self.update_interval or 
                self.bytes_transferred >= self.total_size):
                
                percentage = (self.bytes_transferred / self.total_size) * 100
                elapsed_time = current_time - self.start_time
                
                # Calculate speed
                if elapsed_time > 0:
                    speed_bps = self.bytes_transferred / elapsed_time
                    speed_mbps = speed_bps / (1024 * 1024)
                    
                    # Estimate time remaining
                    if self.bytes_transferred > 0:
                        eta_seconds = (self.total_size - self.bytes_transferred) / speed_bps
                        eta_str = self._format_time(eta_seconds)
                    else:
                        eta_str = "--:--"
                else:
                    speed_mbps = 0
                    eta_str = "--:--"
                
                # Create progress bar
                progress_bar = self._create_progress_bar(percentage)
                
                progress_text = f"⚡ **TURBO UPLOAD** ⚡\n\n"
                progress_text += f"{progress_bar}\n"
                progress_text += f"📊 **{percentage:.1f}%** ({self._format_size(self.bytes_transferred)} / {self._format_size(self.total_size)})\n"
                progress_text += f"🚀 **Speed:** {speed_mbps:.2f} MB/s\n"
                progress_text += f"⏱️ **ETA:** {eta_str}\n"
                progress_text += f"⚡ **High-speed cloud upload in progress...**"
                
                # Store progress for async update
                self.current_progress = progress_text
                self.last_update_time = current_time
    
    def get_progress(self):
        """Get current progress text."""
        with self.lock:
            return self.current_progress
    
    def _create_progress_bar(self, percentage: float, length: int = 20) -> str:
        """Create visual progress bar."""
        filled = int(percentage / 100 * length)
        bar = "█" * filled + "░" * (length - filled)
        return f"[{bar}] {percentage:.1f}%"
    
    def _format_size(self, size_bytes: int) -> str:
        """Format file size in human readable format."""
        if size_bytes == 0:
            return "0 B"
        
        size_names = ["B", "KB", "MB", "GB"]
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s} {size_names[i]}"
    
    def _format_time(self, seconds: float) -> str:
        """Format time in MM:SS format."""
        if seconds < 0 or math.isinf(seconds) or math.isnan(seconds):
            return "--:--"
        
        minutes = int(seconds // 60)
        seconds = int(seconds % 60)
        return f"{minutes:02d}:{seconds:02d}"

class WasabiStorage:
    """High-speed Wasabi storage handler with turbo optimizations."""
    
    def __init__(self, access_key: str, secret_key: str, bucket: str, region: str):
        self.bucket = bucket
        self.region = region
        
        # Optimized S3 client configuration for maximum speed
        self.s3_client = boto3.client(
            's3',
            endpoint_url=f'https://s3.{region}.wasabisys.com',
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(
                max_pool_connections=50,  # Increase connection pool
                retries={
                    'max_attempts': 3,
                    'mode': 'adaptive'
                }
            )
        )
        
        # Turbo transfer configuration
        self.transfer_config = TransferConfig(
            multipart_threshold=1024 * 25,  # 25MB
            max_concurrency=10,  # Concurrent uploads
            multipart_chunksize=1024 * 25,  # 25MB chunks
            use_threads=True
        )
        
    async def upload_file(self, file_path: str, object_key: str, progress_message=None) -> Optional[str]:
        """Turbo-speed upload with real-time progress tracking."""
        try:
            file_size = os.path.getsize(file_path)
            logger.info(f"Starting turbo upload: {file_path} ({file_size} bytes)")
            
            # Create progress tracker
            progress_tracker = ProgressTracker(file_size, update_interval=1.5)
            
            # Use high-speed multipart upload for large files
            def upload_sync():
                self.s3_client.upload_file(
                    file_path,
                    self.bucket,
                    object_key,
                    Config=self.transfer_config,
                    Callback=progress_tracker
                )
            
            # Start upload in thread and monitor progress
            upload_task = asyncio.get_event_loop().run_in_executor(None, upload_sync)
            
            # Monitor progress and update message
            if progress_message:
                while not upload_task.done():
                    await asyncio.sleep(1.5)  # Check every 1.5 seconds
                    current_progress = progress_tracker.get_progress()
                    if current_progress:
                        try:
                            await progress_message.edit_text(current_progress)
                        except Exception:
                            # Handle rate limits gracefully
                            pass
            
            # Wait for upload to complete
            await upload_task
            
            # Generate download URL
            download_url = f"https://s3.{self.region}.wasabisys.com/{self.bucket}/{object_key}"
            logger.info(f"Turbo upload completed: {download_url}")
            return download_url
            
        except ClientError as e:
            logger.error(f"Turbo upload failed: {e}")
            return None
    
    async def get_download_link(self, object_key: str, expires_in: int = 3600) -> Optional[str]:
        """Generate temporary download link for streaming."""
        try:
            download_url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket, 'Key': object_key},
                ExpiresIn=expires_in
            )
            return download_url
        except ClientError as e:
            logger.error(f"Failed to generate download link: {e}")
            return None
    
    async def delete_file(self, object_key: str) -> bool:
        """Delete file from Wasabi storage."""
        try:
            self.s3_client.delete_object(Bucket=self.bucket, Key=object_key)
            logger.info(f"File deleted: {object_key}")
            return True
        except ClientError as e:
            logger.error(f"Delete failed: {e}")
            return False

class TelegramFileBot:
    """High-performance Telegram bot for file sharing with Wasabi storage."""
    
    def __init__(self):
        # Load environment variables with validation
        self.api_id = os.getenv('API_ID', '')
        self.api_hash = os.getenv('API_HASH', '')
        self.bot_token = os.getenv('BOT_TOKEN', '')
        
        # Wasabi configuration
        wasabi_access_key = os.getenv('WASABI_ACCESS_KEY', '')
        wasabi_secret_key = os.getenv('WASABI_SECRET_KEY', '')
        wasabi_bucket = os.getenv('WASABI_BUCKET', '')
        wasabi_region = os.getenv('WASABI_REGION', '')
        
        # Initialize Telegram client
        self.app = Client(
            "file_bot",
            api_id=self.api_id,
            api_hash=self.api_hash,
            bot_token=self.bot_token,
            workdir="./session"
        )
        
        # Initialize Wasabi storage
        self.storage = WasabiStorage(
            wasabi_access_key, 
            wasabi_secret_key, 
            wasabi_bucket, 
            wasabi_region
        )
        
        # File tracking
        self.uploaded_files: Dict[str, Dict[str, Any]] = {}
        
        # Rate limiting
        self.throttler = Throttler(rate_limit=10, period=1)  # 10 operations per second
        
        # Setup handlers
        self._setup_handlers()
    
    def _setup_handlers(self):
        """Setup message handlers for the bot."""
        
        @self.app.on_message(filters.command("start"))
        async def start_handler(client, message):
            await self.handle_start(client, message)
        
        @self.app.on_message(filters.command("help"))
        async def help_handler(client, message):
            await self.handle_help(client, message)
        
        @self.app.on_message(filters.command("upload"))
        async def upload_handler(client, message):
            await self.handle_upload_command(client, message)
        
        @self.app.on_message(filters.command("list"))
        async def list_handler(client, message):
            await self.handle_list_files(client, message)
        
        @self.app.on_message(filters.command("download"))
        async def download_handler(client, message):
            await self.handle_download_command(client, message)
        
        @self.app.on_message(filters.document | filters.video | filters.audio | filters.photo)
        async def file_handler(client, message):
            await self.handle_file_upload(client, message)
    
    async def handle_start(self, client, message):
        """Handle /start command."""
        welcome_text = """
🚀 **High-Speed File Sharing Bot**

This bot supports:
📁 Files up to 4GB
☁️ Wasabi cloud storage
🎥 MX Player & VLC compatible links
⚡ High-speed streaming

**Commands:**
/upload - Upload a file
/list - List your uploaded files
/download [file_id] - Get download link
/help - Show this help

Simply send any file to upload it automatically!
        """
        START_PIC = os.getenv("START_PIC", None)  # Read from env

    if START_PIC:  # If a start picture is set
        await message.reply_photo(
            photo=START_PIC,
            caption=welcome_text
        )
    else:
        await message.reply_text(welcome_text)
    
    async def handle_help(self, client, message):
        """Handle /help command."""
        help_text = """
📋 **Bot Commands:**

🔹 `/start` - Start the bot
🔹 `/upload` - Upload a file (or just send any file)
🔹 `/list` - List your uploaded files
🔹 `/download [file_id]` - Get streaming download link
🔹 `/help` - Show this help

**Supported Files:**
✅ Videos (MP4, MKV, AVI, etc.)
✅ Audio (MP3, FLAC, WAV, etc.)
✅ Documents (PDF, ZIP, etc.)
✅ Images (JPG, PNG, etc.)

**Features:**
⚡ Up to 4GB file size
🌐 Direct streaming links
📱 MX Player & VLC compatible
☁️ Secure cloud storage
        """
        await message.reply_text(help_text)
    
    async def handle_upload_command(self, client, message):
        """Handle /upload command."""
        await message.reply_text(
            "📤 Ready to upload!\n\n"
            "Simply send me any file and I'll upload it to secure cloud storage.\n"
            "Supported: Videos, Audio, Documents, Images (up to 4GB)"
        )
    
    async def handle_file_upload(self, client, message):
        """Handle file upload with progress tracking."""
        async with self.throttler:
            try:
                # Get file info
                file_info = None
                if message.document:
                    file_info = message.document
                elif message.video:
                    file_info = message.video
                elif message.audio:
                    file_info = message.audio
                elif message.photo:
                    file_info = message.photo[-1]  # Get largest photo
                
                if not file_info:
                    await message.reply_text("❌ Unsupported file type.")
                    return
                
                # Check file size (4GB limit)
                file_size = getattr(file_info, 'file_size', 0)
                if file_size > 4 * 1024 * 1024 * 1024:  # 4GB
                    await message.reply_text("❌ File too large! Maximum size is 4GB.")
                    return
                
                # Generate unique file ID and object key
                file_id = str(uuid.uuid4())
                original_name = getattr(file_info, 'file_name', f"file_{file_id}")
                object_key = f"files/{message.from_user.id}/{file_id}_{original_name}"
                
                # Send turbo download progress message
                progress_msg = await message.reply_text(
                    "⚡ **TURBO DOWNLOAD INITIATED** ⚡\n\n"
                    "🚀 High-speed download from Telegram...\n"
                    "📡 Optimizing transfer protocols..."
                )
                
                # Create downloads directory
                downloads_dir = Path("downloads")
                downloads_dir.mkdir(exist_ok=True)
                local_file_path = downloads_dir / f"{file_id}_{original_name}"
                
                # Turbo download from Telegram with progress tracking
                start_time = time.time()
                await self._turbo_download_media(
                    message, str(local_file_path), progress_msg, file_size
                )
                download_time = time.time() - start_time
                
                # Show download completion with speed stats
                download_speed = (file_size / download_time) / (1024 * 1024) if download_time > 0 else 0
                await progress_msg.edit_text(
                    f"✅ **TURBO DOWNLOAD COMPLETE** ⚡\n\n"
                    f"📁 **File:** {original_name}\n"
                    f"📊 **Size:** {self._format_file_size(file_size)}\n"
                    f"🚀 **Speed:** {download_speed:.2f} MB/s\n"
                    f"⏱️ **Time:** {download_time:.1f}s\n\n"
                    f"☁️ **Initializing cloud upload...**"
                )
                
                # Turbo upload initialization already handled in download completion
                
                # Upload to Wasabi with real-time progress
                download_url = await self.storage.upload_file(
                    str(local_file_path),
                    object_key,
                    progress_message=progress_msg
                )
                
                if download_url:
                    # Store file info
                    self.uploaded_files[file_id] = {
                        'original_name': original_name,
                        'object_key': object_key,
                        'download_url': download_url,
                        'file_size': file_size,
                        'upload_time': datetime.now().isoformat(),
                        'user_id': message.from_user.id
                    }
                    
                    # Generate streaming link
                    streaming_url = await self.storage.get_download_link(object_key, expires_in=86400)  # 24 hours
                    
                    success_text = f"""
✅ **TURBO UPLOAD COMPLETE!** ⚡

📁 **File:** {original_name}
📊 **Size:** {self._format_file_size(file_size)}
🆔 **File ID:** `{file_id}`

🔗 **High-Speed Streaming Link:** 
`{streaming_url}`

🚀 **Features:**
• ⚡ Turbo-speed upload completed
• 📱 MX Player/VLC compatible
• 🌐 Direct streaming (no download needed)
• ☁️ Secure cloud storage
⬇️ **Get Link Again:** /download {file_id}
                    """
                    
                    await progress_msg.edit_text(success_text)
                    
                    # Clean up local file
                    try:
                        os.remove(local_file_path)
                    except:
                        pass
                else:
                    await progress_msg.edit_text("❌ Upload failed. Please try again.")
                    
            except Exception as e:
                logger.error(f"Upload error: {e}")
                await message.reply_text(f"❌ Upload failed: {str(e)}")
    
    async def handle_list_files(self, client, message):
        """Handle /list command to show user's uploaded files."""
        user_files = [
            f for f in self.uploaded_files.values() 
            if f['user_id'] == message.from_user.id
        ]
        
        if not user_files:
            await message.reply_text("📁 No files uploaded yet.\n\nSend me any file to get started!")
            return
        
        files_text = "📋 **Your Uploaded Files:**\n\n"
        for file_id, file_info in self.uploaded_files.items():
            if file_info['user_id'] == message.from_user.id:
                files_text += f"📁 **{file_info['original_name']}**\n"
                files_text += f"🆔 ID: `{file_id}`\n"
                files_text += f"📊 Size: {self._format_file_size(file_info['file_size'])}\n"
                files_text += f"📅 Uploaded: {file_info['upload_time'][:10]}\n"
                files_text += f"⬇️ Download: /download {file_id}\n\n"
        
        await message.reply_text(files_text)
    
    async def handle_download_command(self, client, message):
        """Handle /download command."""
        try:
            command_parts = message.text.split()
            if len(command_parts) < 2:
                await message.reply_text("❌ Please provide a file ID.\n\nUsage: /download [file_id]")
                return
            
            file_id = command_parts[1]
            if file_id not in self.uploaded_files:
                await message.reply_text("❌ File not found. Use /list to see available files.")
                return
            
            file_info = self.uploaded_files[file_id]
            
            # Generate fresh streaming link
            streaming_url = await self.storage.get_download_link(
                file_info['object_key'], 
                expires_in=86400  # 24 hours
            )
            
            if streaming_url:
                download_text = f"""
📁 **{file_info['original_name']}**
📊 **Size:** {self._format_file_size(file_info['file_size'])}

🔗 **Streaming Link (24h):**
`{streaming_url}`

📱 **For Mobile Players:**
• Copy the link above
• Open MX Player or VLC
• Select "Stream" or "Network Stream"
• Paste the link

💡 **Tip:** This link works for direct streaming without downloading!
                """
                await message.reply_text(download_text)
            else:
                await message.reply_text("❌ Failed to generate download link. Please try again.")
                
        except Exception as e:
            logger.error(f"Download error: {e}")
            await message.reply_text("❌ Error generating download link.")
    
    async def _turbo_download_media(self, message, file_path: str, progress_msg, file_size: int):
        """Turbo-speed download with real-time progress tracking."""
        try:
            # Create progress tracker for download
            class DownloadProgress:
                def __init__(self, total_size, progress_message):
                    self.total_size = total_size
                    self.progress_message = progress_message
                    self.downloaded = 0
                    self.start_time = time.time()
                    self.last_update = 0
                
                async def update(self, current, total):
                    self.downloaded = current
                    current_time = time.time()
                    
                    # Update every 1.5 seconds
                    if current_time - self.last_update > 1.5:
                        percentage = (current / total) * 100
                        elapsed = current_time - self.start_time
                        
                        if elapsed > 0:
                            speed_bps = current / elapsed
                            speed_mbps = speed_bps / (1024 * 1024)
                            eta_seconds = (total - current) / speed_bps if speed_bps > 0 else 0
                            eta_str = self._format_time(eta_seconds)
                        else:
                            speed_mbps = 0
                            eta_str = "--:--"
                        
                        progress_bar = self._create_download_progress_bar(percentage)
                        
                        progress_text = f"⚡ **TURBO DOWNLOAD** ⚡\n\n"
                        progress_text += f"{progress_bar}\n"
                        progress_text += f"📊 **{percentage:.1f}%** ({self._format_size(current)} / {self._format_size(total)})\n"
                        progress_text += f"🚀 **Speed:** {speed_mbps:.2f} MB/s\n"
                        progress_text += f"⏱️ **ETA:** {eta_str}\n"
                        progress_text += f"📡 **High-speed Telegram download...**"
                        
                        try:
                            await self.progress_message.edit_text(progress_text)
                        except Exception:
                            pass
                        
                        self.last_update = current_time
                
                def _format_size(self, size_bytes: int) -> str:
                    if size_bytes == 0:
                        return "0 B"
                    size_names = ["B", "KB", "MB", "GB"]
                    import math
                    i = int(math.floor(math.log(size_bytes, 1024)))
                    p = math.pow(1024, i)
                    s = round(size_bytes / p, 2)
                    return f"{s} {size_names[i]}"
                
                def _format_time(self, seconds: float) -> str:
                    if seconds < 0 or math.isinf(seconds) or math.isnan(seconds):
                        return "--:--"
                    minutes = int(seconds // 60)
                    seconds = int(seconds % 60)
                    return f"{minutes:02d}:{seconds:02d}"
                
                def _create_download_progress_bar(self, percentage: float, length: int = 20) -> str:
                    filled = int(percentage / 100 * length)
                    bar = "█" * filled + "░" * (length - filled)
                    return f"[{bar}] {percentage:.1f}%"
            
            # Create progress tracker
            progress_tracker = DownloadProgress(file_size, progress_msg)
            
            # Download with progress callback
            await self.app.download_media(
                message,
                file_name=file_path,
                progress=progress_tracker.update
            )
            
        except Exception as e:
            logger.error(f"Turbo download error: {e}")
            raise e

    def _format_file_size(self, size_bytes: int) -> str:
        """Format file size in human readable format."""
        if size_bytes == 0:
            return "0 B"
        
        size_names = ["B", "KB", "MB", "GB"]
        import math
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s} {size_names[i]}"
    
    async def run(self):
        """Start the bot."""
        logger.info("Starting Telegram File Bot...")
        
        # Create session directory
        os.makedirs("session", exist_ok=True)
        
        try:
            await self.app.start()
            logger.info("✅ Bot started successfully!")
            
            # Send startup message to log
            me = await self.app.get_me()
            logger.info(f"Bot @{me.username} is running...")
            
            # Keep the bot running
            await asyncio.Event().wait()
            
        except Exception as e:
            logger.error(f"Failed to start bot: {e}")
        finally:
            await self.app.stop()

class WebServer:
    """Flask web server for file rendering and access on port 5000."""
    
    def __init__(self, bot_instance):
        self.bot = bot_instance
        self.app = Flask(__name__)
        self.app.config['SECRET_KEY'] = 'high-speed-file-bot-2024'
        CORS(self.app)
        
        # Setup routes
        self._setup_routes()
    
    def _setup_routes(self):
        """Setup Flask routes for web interface."""
        
        @self.app.route('/')
        def home():
            """Home page showing bot information."""
            return f'''
            <!DOCTYPE html>
            <html>
            <head>
                <title>🚀 High-Speed File Bot</title>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <style>
                    body {{
                        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                        margin: 0;
                        padding: 20px;
                        min-height: 100vh;
                        color: white;
                    }}
                    .container {{
                        max-width: 800px;
                        margin: 0 auto;
                        text-align: center;
                        padding: 40px 20px;
                    }}
                    .hero {{
                        background: rgba(255,255,255,0.1);
                        border-radius: 20px;
                        padding: 40px;
                        backdrop-filter: blur(10px);
                        border: 1px solid rgba(255,255,255,0.2);
                        margin-bottom: 40px;
                    }}
                    .feature {{
                        display: inline-block;
                        margin: 10px 20px;
                        padding: 15px 25px;
                        background: rgba(255,255,255,0.2);
                        border-radius: 25px;
                        font-size: 16px;
                    }}
                    .files-section {{
                        background: rgba(255,255,255,0.1);
                        border-radius: 20px;
                        padding: 30px;
                        backdrop-filter: blur(10px);
                        border: 1px solid rgba(255,255,255,0.2);
                    }}
                    .file-item {{
                        background: rgba(255,255,255,0.2);
                        margin: 10px 0;
                        padding: 15px;
                        border-radius: 10px;
                        text-align: left;
                    }}
                    .file-link {{
                        color: #00ff88;
                        text-decoration: none;
                        font-weight: bold;
                    }}
                    .file-link:hover {{
                        text-decoration: underline;
                    }}
                    h1 {{ font-size: 3em; margin-bottom: 20px; }}
                    h2 {{ color: #00ff88; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="hero">
                        <h1>🚀 High-Speed File Bot</h1>
                        <p style="font-size: 1.2em;">Ultra-fast file sharing with Wasabi cloud storage</p>
                        
                        <div style="margin: 30px 0;">
                            <span class="feature">⚡ Turbo Upload Speed</span>
                            <span class="feature">📱 MX Player Compatible</span>
                            <span class="feature">🌐 Direct Streaming</span>
                            <span class="feature">☁️ 4GB File Support</span>
                        </div>
                        
                        <p>Start chatting with <strong>@mraprguildbot</strong> on Telegram</p>
                    </div>
                    
                    <div class="files-section">
                        <h2>📋 Recent Files</h2>
                        <div id="files-list">
                            <p>Upload files through Telegram to see them here!</p>
                        </div>
                    </div>
                </div>
                
                <script>
                    // Auto-refresh files list
                    setInterval(() => {{
                        fetch('/api/files')
                            .then(response => response.json())
                            .then(data => {{
                                const filesList = document.getElementById('files-list');
                                if (data.files && data.files.length > 0) {{
                                    filesList.innerHTML = data.files.map(file => `
                                        <div class="file-item">
                                            <strong>${{file.name}}</strong> (${{file.size}})
                                            <br>
                                            <a href="${{file.streaming_url}}" class="file-link" target="_blank">
                                                🎥 Stream/Download
                                            </a>
                                            <small style="color: #ccc; margin-left: 20px;">
                                                ID: ${{file.id}} | Uploaded: ${{file.date}}
                                            </small>
                                        </div>
                                    `).join('');
                                }} else {{
                                    filesList.innerHTML = '<p>No files uploaded yet. Send files to the Telegram bot!</p>';
                                }}
                            }})
                            .catch(err => console.log('Error fetching files:', err));
                    }}, 5000);
                </script>
            </body>
            </html>
            '''
        
        @self.app.route('/api/files')
        def api_files():
            """API endpoint to get list of uploaded files."""
            try:
                files_data = []
                for file_id, file_info in self.bot.uploaded_files.items():
                    files_data.append({
                        'id': file_id,
                        'name': file_info['original_name'],
                        'size': self._format_file_size(file_info['file_size']),
                        'date': file_info['upload_time'][:10],
                        'streaming_url': file_info.get('download_url', '#')
                    })
                
                # Sort by upload time (newest first)
                files_data.sort(key=lambda x: x['date'], reverse=True)
                
                return jsonify({
                    'status': 'success',
                    'files': files_data[:20]  # Limit to 20 most recent
                })
            except Exception as e:
                return jsonify({
                    'status': 'error',
                    'message': str(e)
                })
        
        @self.app.route('/stream/<file_id>')
        def stream_file(file_id):
            """Stream file directly from Wasabi storage."""
            try:
                if file_id not in self.bot.uploaded_files:
                    return "File not found", 404
                
                file_info = self.bot.uploaded_files[file_id]
                # Generate streaming URL
                streaming_url = asyncio.run(
                    self.bot.storage.get_download_link(
                        file_info['object_key'], 
                        expires_in=3600
                    )
                )
                
                if streaming_url:
                    # Redirect to Wasabi streaming URL
                    return redirect(streaming_url)
                else:
                    return "Failed to generate streaming link", 500
                    
            except Exception as e:
                return f"Error: {str(e)}", 500
        
        @self.app.route('/health')
        def health():
            """Health check endpoint."""
            return jsonify({
                'status': 'healthy',
                'bot_running': True,
                'files_count': len(self.bot.uploaded_files),
                'server_time': datetime.now().isoformat()
            })
    
    def _format_file_size(self, size_bytes: int) -> str:
        """Format file size in human readable format."""
        if size_bytes == 0:
            return "0 B"
        
        size_names = ["B", "KB", "MB", "GB"]
        import math
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s} {size_names[i]}"
    
    def run(self):
        """Run the Flask web server."""
        logger.info("🌐 Starting web server on port 5000...")
        self.app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)

async def run_bot(bot):
    """Run the Telegram bot."""
    await bot.run()

def run_web_server(bot):
    """Run the web server."""
    web_server = WebServer(bot)
    web_server.run()

async def main():
    """Main function to run both bot and web server."""
    # Check required environment variables
    required_vars = [
        'API_ID', 'API_HASH', 'BOT_TOKEN',
        'WASABI_ACCESS_KEY', 'WASABI_SECRET_KEY', 
        'WASABI_BUCKET', 'WASABI_REGION'
    ]
    
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        logger.error(f"Missing environment variables: {', '.join(missing_vars)}")
        return
    
    # Initialize bot
    bot = TelegramFileBot()
    
    # Start web server in separate thread
    web_thread = threading.Thread(target=run_web_server, args=(bot,), daemon=True)
    web_thread.start()
    
    # Run bot in main thread
    await run_bot(bot)

if __name__ == "__main__":
    asyncio.run(main())
