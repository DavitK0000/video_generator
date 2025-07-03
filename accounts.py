import os
import pickle
import json
import base64
import datetime
from datetime import timezone
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
                            QListWidget, QInputDialog, QMessageBox, QLineEdit, QListWidgetItem,
                            QGroupBox, QDialogButtonBox, QApplication)
from PyQt5.QtCore import Qt, pyqtSignal
import google_auth_oauthlib.flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from PyQt5.QtGui import QColor

# Constants
SCOPES = [
    'https://www.googleapis.com/auth/youtube.readonly',
    'https://www.googleapis.com/auth/youtube.upload',
]
API_SERVICE_NAME = 'youtube'
API_VERSION = 'v3'

class AccountManager:
    """Class to manage multiple Google accounts, each representing a YouTube channel"""
    
    def __init__(self, accounts_file, client_secrets_file=None, logger=None):
        self.accounts_file = accounts_file
        self.client_secrets_file = client_secrets_file
        self.accounts = {}
        self.current_account = None
        self.logger = logger
        self.load_accounts()
    
    def log(self, message, level="info"):
        """Log message if logger is available"""
        if self.logger:
            if level == "info":
                self.logger.info(message)
            elif level == "error":
                self.logger.error(message)
            elif level == "warning":
                self.logger.warning(message)
    
    def load_accounts(self):
        """Load saved accounts from file"""
        if os.path.exists(self.accounts_file):
            try:
                with open(self.accounts_file, 'r') as f:
                    data = json.load(f)
                    # Convert base64 string back to credentials bytes
                    accounts_data = data.get('accounts', {})
                    for name, account_info in accounts_data.items():
                        if 'credentials' in account_info:
                            try:
                                # Decode the base64 string to bytes
                                creds_bytes = base64.b64decode(account_info['credentials'])
                                # Store the bytes directly
                                account_info['credentials'] = creds_bytes
                            except:
                                self.log(f"Failed to decode credentials for account {name}", "error")
                    
                    self.accounts = accounts_data
                    self.current_account = data.get('current_account')
                self.log(f"Loaded {len(self.accounts)} accounts")
            except Exception as e:
                self.log(f"Error loading accounts: {str(e)}", "error")
                self.accounts = {}
                self.current_account = None
    
    def save_accounts(self):
        """Save accounts to file"""
        try:
            # Create a copy of accounts to modify for JSON serialization
            serializable_accounts = {}
            for name, account_info in self.accounts.items():
                serializable_account = account_info.copy()
                if 'credentials' in serializable_account:
                    # Convert credentials bytes to base64 encoded string for JSON serialization
                    credentials_bytes = serializable_account['credentials']
                    serializable_account['credentials'] = base64.b64encode(credentials_bytes).decode('utf-8')
                serializable_accounts[name] = serializable_account
            
            data = {
                'accounts': serializable_accounts,
                'current_account': self.current_account
            }
            
            with open(self.accounts_file, 'w') as f:
                json.dump(data, f, indent=2)
            self.log(f"Saved {len(self.accounts)} accounts")
            return True
        except Exception as e:
            self.log(f"Error saving accounts: {str(e)}", "error")
            return False
    
    def set_client_secrets_file(self, path):
        """Set the client secrets file path"""
        self.client_secrets_file = path
        self.log(f"Set client secrets file: {path}")
    
    def add_account(self, name, credentials=None):
        """Add a new account (representing a YouTube channel)"""
        if name in self.accounts:
            self.log(f"Account {name} already exists", "warning")
            return False
        
        if credentials is None:
            # Authenticate with Google
            if not self.client_secrets_file:
                self.log("Client secrets file not set", "error")
                return False
            
            try:
                flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
                    self.client_secrets_file, SCOPES)
                credentials = flow.run_local_server(port=9000)
                
                # Test the credentials by getting user info
                youtube = build(API_SERVICE_NAME, API_VERSION, credentials=credentials)
                response = youtube.channels().list(part="snippet", mine=True).execute()
                
                if not response.get('items'):
                    self.log("Failed to get channel info for new account", "error")
                    return False
                
                # Get channel information
                channel_id = response['items'][0]['id']
                channel_title = response['items'][0]['snippet']['title']
                
                # Serialize credentials to bytes
                credentials_bytes = pickle.dumps(credentials)
                
                # Store account with channel info directly
                self.accounts[name] = {
                    'credentials': credentials_bytes,
                    'display_name': name,
                    'channel_id': channel_id,
                    'channel_title': channel_title
                }
                
                self.current_account = name
                self.save_accounts()
                self.log(f"Added new account: {name} for channel: {channel_title}")
                return True
                
            except Exception as e:
                self.log(f"Error adding account: {str(e)}", "error")
                return False
        else:
            # Add with provided credentials
            try:
                credentials_bytes = pickle.dumps(credentials)
                
                # Try to get channel info
                youtube = build(API_SERVICE_NAME, API_VERSION, credentials=credentials)
                response = youtube.channels().list(part="snippet", mine=True).execute()
                
                if response.get('items'):
                    channel_id = response['items'][0]['id']
                    channel_title = response['items'][0]['snippet']['title']
                else:
                    channel_id = "unknown"
                    channel_title = "Unknown Channel"
                
                self.accounts[name] = {
                    'credentials': credentials_bytes,
                    'display_name': name,
                    'channel_id': channel_id,
                    'channel_title': channel_title
                }
                self.save_accounts()
                return True
            except Exception as e:
                self.log(f"Error adding account with provided credentials: {str(e)}", "error")
                return False
    
    def rename_account(self, old_name, new_name):
        """Rename an account"""
        if old_name not in self.accounts:
            self.log(f"Account {old_name} not found", "error")
            return False
        
        if new_name in self.accounts:
            self.log(f"Account {new_name} already exists", "error")
            return False
        
        self.accounts[new_name] = self.accounts[old_name]
        self.accounts[new_name]['display_name'] = new_name
        del self.accounts[old_name]
        
        if self.current_account == old_name:
            self.current_account = new_name
            
        self.save_accounts()
        self.log(f"Renamed account {old_name} to {new_name}")
        return True
    
    def remove_account(self, name):
        """Remove an account"""
        if name not in self.accounts:
            self.log(f"Account {name} not found", "error")
            return False
        
        del self.accounts[name]
        
        if self.current_account == name:
            self.current_account = None if not self.accounts else list(self.accounts.keys())[0]
            
        self.save_accounts()
        self.log(f"Removed account: {name}")
        return True
    
    def select_account(self, name):
        """Select an account as current"""
        if name not in self.accounts:
            self.log(f"Account {name} not found", "error")
            return False
        
        self.current_account = name
        self.save_accounts()
        self.log(f"Selected account: {name}")
        return True
    
    def get_account_credentials(self, name=None):
        """Get credentials for an account"""
        account_name = name if name else self.current_account
        
        if not account_name or account_name not in self.accounts:
            self.log(f"Account {account_name} not found", "error")
            return None
        
        try:
            # Deserialize credentials
            credentials = pickle.loads(self.accounts[account_name]['credentials'])
            
            # Check if credentials need refreshing
            if credentials.expired and credentials.refresh_token:
                try:
                    credentials.refresh(Request())
                    # Update stored credentials
                    self.accounts[account_name]['credentials'] = pickle.dumps(credentials)
                    self.save_accounts()
                    self.log(f"Refreshed credentials for {account_name}")
                except Exception as refresh_error:
                    # Check for invalid_grant error which indicates revoked/expired token
                    error_str = str(refresh_error)
                    if "invalid_grant" in error_str or "Token has been expired or revoked" in error_str:
                        self.log(f"Refresh token for {account_name} has been revoked or expired. Re-authentication required.", "error")
                        # Mark account as needing re-authentication
                        self.accounts[account_name]['needs_reauth'] = True
                        self.save_accounts()
                    raise refresh_error
            
            return credentials
        except Exception as e:
            self.log(f"Error getting credentials: {str(e)}", "error")
            return None
    
    def get_current_credentials(self):
        """Get credentials for current account"""
        return self.get_account_credentials(self.current_account)
    
    def get_accounts_list(self):
        """Get list of account names"""
        return list(self.accounts.keys())
    
    def get_current_channel_info(self):
        """Get channel info for current account"""
        if not self.current_account or self.current_account not in self.accounts:
            return None
        
        account_info = self.accounts[self.current_account]
        return {
            'id': account_info.get('channel_id', 'unknown'),
            'title': account_info.get('channel_title', 'Unknown Channel')
        }
    
    def refresh_channel_info(self, name=None):
        """Refresh channel info for an account"""
        account_name = name if name else self.current_account
        
        if not account_name or account_name not in self.accounts:
            return False
        
        credentials = self.get_account_credentials(account_name)
        if not credentials:
            return False
        
        try:
            youtube = build(API_SERVICE_NAME, API_VERSION, credentials=credentials)
            
            # Get the specific channel ID for this account
            channel_id = self.accounts[account_name].get('channel_id')
            if not channel_id or channel_id == 'unknown':
                # If no channel ID, try to get it using mine=True
                response = youtube.channels().list(part="snippet", mine=True).execute()
                if response.get('items'):
                    channel_id = response['items'][0]['id']
                    channel_title = response['items'][0]['snippet']['title']
                else:
                    self.log(f"No channel found for account {account_name}", "warning")
                    return False
            else:
                # Use the stored channel ID
                response = youtube.channels().list(part="snippet", id=channel_id).execute()
                if response.get('items'):
                    channel_title = response['items'][0]['snippet']['title']
                else:
                    self.log(f"No channel found for account {account_name} with ID {channel_id}", "warning")
                    return False
            
            # Update stored channel info
            self.accounts[account_name]['channel_id'] = channel_id
            self.accounts[account_name]['channel_title'] = channel_title
            self.save_accounts()
            self.log(f"Updated channel info for {account_name}: {channel_title}")
            return True
            
        except Exception as e:
            self.log(f"Error refreshing channel info: {str(e)}", "error")
            return False
    
    def needs_reauthentication(self, name=None):
        """Check if an account needs re-authentication"""
        account_name = name if name else self.current_account
        
        if not account_name or account_name not in self.accounts:
            return False
        
        # Check if account is marked as needing re-authentication
        if self.accounts[account_name].get('needs_reauth', False):
            return True
            
        # Also check if credentials are expired with no refresh token
        try:
            credentials = pickle.loads(self.accounts[account_name]['credentials'])
            if credentials.expired and not credentials.refresh_token:
                return True
        except Exception:
            # If we can't unpickle or there's an error, re-auth is needed
            return True
            
        return False
        
    def reauthorize_account(self, name):
        """Re-authorize an existing account
        
        Note: If the account has multiple YouTube channels, this may switch to a different
        channel than the one previously selected. Use 'Show Channels' to see all available
        channels for the account.
        """
        if name not in self.accounts:
            self.log(f"Account {name} not found", "error")
            return False
            
        if not self.client_secrets_file:
            self.log("Client secrets file not set", "error")
            return False
            
        try:
            # Store original account info
            original_account_info = self.accounts[name].copy()
            
            # Re-authenticate
            flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
                self.client_secrets_file, SCOPES)
            credentials = flow.run_local_server(port=9000)
            
            # Test the credentials
            youtube = build(API_SERVICE_NAME, API_VERSION, credentials=credentials)
            response = youtube.channels().list(part="snippet", mine=True).execute()
            
            if not response.get('items'):
                self.log("Failed to get channel info after re-authentication", "error")
                return False
                
            # Get updated channel info
            channel_id = response['items'][0]['id']
            channel_title = response['items'][0]['snippet']['title']
            
            # Update account with new credentials and channel info
            credentials_bytes = pickle.dumps(credentials)
            self.accounts[name] = {
                'credentials': credentials_bytes,
                'display_name': name,
                'channel_id': channel_id,
                'channel_title': channel_title,
                'needs_reauth': False  # Clear re-auth flag
            }
            
            self.save_accounts()
            self.log(f"Re-authenticated account: {name} for channel: {channel_title}")
            return True
            
        except Exception as e:
            self.log(f"Error re-authenticating account: {str(e)}", "error")
            return False
    
    def get_channel_statistics(self, name=None):
        """Get channel statistics (views, subscribers, etc.) for an account
        
        Returns statistics including:
        - Total views
        - Views for videos published in last 24 hours
        - Subscriber count
        - Total video count
        - Uploads in last 24 hours
        - Recent upload titles (up to 3)
        """
        account_name = name if name else self.current_account
        
        if not account_name or account_name not in self.accounts:
            return None
        
        credentials = self.get_account_credentials(account_name)
        if not credentials:
            return None
        
        try:
            youtube = build(API_SERVICE_NAME, API_VERSION, credentials=credentials)
            
            # Get the specific channel ID for this account
            channel_id = self.accounts[account_name].get('channel_id')
            if not channel_id or channel_id == 'unknown':
                self.log(f"No valid channel ID found for account {account_name}", "error")
                return None
            
            # Get channel statistics using the specific channel ID
            response = youtube.channels().list(
                part="statistics,snippet",
                id=channel_id
            ).execute()
            
            if response.get('items'):
                channel = response['items'][0]
                statistics = channel.get('statistics', {})
                
                # Format numbers for display
                def format_number(num_str):
                    if not num_str or num_str == '0':
                        return '0'
                    try:
                        num = int(num_str)
                        if num >= 1000000:
                            return f"{num/1000000:.1f}M"
                        elif num >= 1000:
                            return f"{num/1000:.1f}K"
                        else:
                            return str(num)
                    except (ValueError, TypeError):
                        return num_str
                
                # Get videos uploaded in last 24 hours
                # Calculate 24 hours ago
                now = datetime.datetime.now(timezone.utc)
                yesterday = now - datetime.timedelta(hours=24)
                yesterday_str = yesterday.isoformat()
                
                # Search for videos uploaded in last 24 hours
                try:
                    search_response = youtube.search().list(
                        part="snippet",
                        channelId=channel_id,
                        type="video",
                        order="date",
                        publishedAfter=yesterday_str,
                        maxResults=50
                    ).execute()
                    
                    videos_24h = search_response.get('items', [])
                    uploads_24h = len(videos_24h)
                    
                    # Get titles of recent uploads (up to 3 for display)
                    recent_upload_titles = []
                    for video in videos_24h[:3]:
                        title = video.get('snippet', {}).get('title', 'Unknown Title')
                        recent_upload_titles.append(title)
                    
                    # Calculate views for videos published in last 24 hours
                    views_24h = 0
                    if videos_24h:
                        try:
                            # Get video IDs for recent uploads
                            video_ids = [video['id']['videoId'] for video in videos_24h]
                            
                            # Get detailed video statistics
                            videos_response = youtube.videos().list(
                                part="statistics",
                                id=','.join(video_ids)
                            ).execute()
                            
                            # Sum up view counts
                            for video in videos_response.get('items', []):
                                view_count = video.get('statistics', {}).get('viewCount', '0')
                                try:
                                    views_24h += int(view_count)
                                except (ValueError, TypeError):
                                    pass
                        except Exception as video_error:
                            self.log(f"Error fetching video statistics: {str(video_error)}", "warning")
                            views_24h = 0
                        
                        # Format the 24h views
                        def format_24h_views(num):
                            if num >= 1000000:
                                return f"{num/1000000:.1f}M"
                            elif num >= 1000:
                                return f"{num/1000:.1f}K"
                            else:
                                return str(num)
                        
                        views_24h_formatted = format_24h_views(views_24h)
                    else:
                        views_24h_formatted = "0"
                    
                except Exception as search_error:
                    self.log(f"Error fetching recent uploads: {str(search_error)}", "warning")
                    uploads_24h = 0
                    recent_upload_titles = []
                    views_24h_formatted = "0"
                
                stats = {
                    'view_count': format_number(statistics.get('viewCount', '0')),
                    'subscriber_count': format_number(statistics.get('subscriberCount', '0')),
                    'video_count': format_number(statistics.get('videoCount', '0')),
                    'uploads_24h': uploads_24h,
                    'views_24h': views_24h_formatted,
                    'recent_upload_titles': recent_upload_titles,
                    'hidden_subscriber_count': statistics.get('hiddenSubscriberCount', False),
                    'channel_title': channel.get('snippet', {}).get('title', 'Unknown Channel')
                }
                
                # Store statistics in account info
                self.accounts[account_name]['statistics'] = stats
                self.save_accounts()
                
                return stats
            else:
                self.log(f"No channel found for account {account_name} with ID {channel_id}", "warning")
                return None
        except Exception as e:
            self.log(f"Error getting channel statistics: {str(e)}", "error")
            return None
    
    def get_stored_statistics(self, name=None):
        """Get stored channel statistics for an account"""
        account_name = name if name else self.current_account
        
        if not account_name or account_name not in self.accounts:
            return None
        
        return self.accounts[account_name].get('statistics')


class AccountManagerDialog(QDialog):
    """Dialog for managing Google accounts"""
    
    account_changed = pyqtSignal(str, object, str)  # Signal when account is changed (name, credentials, channel)
    
    def __init__(
        self, 
        account_manager : AccountManager, 
        parent=None):
        super().__init__(parent)
        self.account_manager = account_manager
        self.setup_ui()
        
    def setup_ui(self):
        self.setWindowTitle("YouTube Account Manager")
        self.setMinimumSize(600, 400)  # Increased height to accommodate statistics
        
        main_layout = QVBoxLayout(self)
                
        # Account list
        accounts_group = QGroupBox("YouTube Accounts")
        accounts_layout = QVBoxLayout()
        
        self.account_list = QListWidget()
        accounts_layout.addWidget(self.account_list)
                
        # Account buttons
        account_buttons_layout = QHBoxLayout()
        
        self.add_account_btn = QPushButton("Add Account")
        self.add_account_btn.clicked.connect(self.add_account)
        self.add_account_btn.setEnabled(bool(self.account_manager.client_secrets_file))
        
        self.rename_account_btn = QPushButton("Rename")
        self.rename_account_btn.clicked.connect(self.rename_account)
        self.rename_account_btn.setEnabled(False)
        
        self.remove_account_btn = QPushButton("Remove")
        self.remove_account_btn.clicked.connect(self.remove_account)
        self.remove_account_btn.setEnabled(False)
        
        self.refresh_btn = QPushButton("Refresh Info")
        self.refresh_btn.clicked.connect(self.refresh_channel_info)
        self.refresh_btn.setEnabled(False)
        
        self.refresh_views_btn = QPushButton("Refresh Stats")
        self.refresh_views_btn.clicked.connect(self.refresh_views)
        self.refresh_views_btn.setEnabled(False)
        self.refresh_views_btn.setStyleSheet("background-color: #27ae60; color: white;")
        
        self.reauth_btn = QPushButton("Re-authenticate")
        self.reauth_btn.clicked.connect(self.reauth_account)
        self.reauth_btn.setEnabled(False)
        self.reauth_btn.setStyleSheet("background-color: #e74c3c; color: white;")
        
        account_buttons_layout.addWidget(self.add_account_btn)
        account_buttons_layout.addWidget(self.rename_account_btn)
        account_buttons_layout.addWidget(self.remove_account_btn)
        account_buttons_layout.addWidget(self.refresh_btn)
        account_buttons_layout.addWidget(self.refresh_views_btn)
        account_buttons_layout.addWidget(self.reauth_btn)
        
        accounts_layout.addLayout(account_buttons_layout)
        accounts_group.setLayout(accounts_layout)
        
        main_layout.addWidget(accounts_group)
        
        # Info Section
        info_group = QGroupBox("Channel Information")
        info_layout = QVBoxLayout()
        
        self.channel_info_label = QLabel("Select an account to see channel details")
        self.channel_info_label.setWordWrap(True)
        info_layout.addWidget(self.channel_info_label)
        
        # Statistics section
        self.statistics_label = QLabel("")
        self.statistics_label.setWordWrap(True)
        self.statistics_label.setStyleSheet("color: #27ae60; font-weight: bold; margin-top: 10px;")
        info_layout.addWidget(self.statistics_label)
        
        info_group.setLayout(info_layout)
        main_layout.addWidget(info_group)
        
        # Dialog buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)
        
        # Connect signals
        self.account_list.currentRowChanged.connect(self.on_account_selected)
        
        # Load accounts
        self.refresh_account_list()
        
    def refresh_account_list(self):
        """Refresh the account list widget"""
        self.account_list.clear()
        
        accounts = self.account_manager.get_accounts_list()
        current_account = self.account_manager.current_account
        
        for account in accounts:
            item = QListWidgetItem(account)
            
            # Mark the current account
            if account == current_account:
                item.setBackground(QColor(70, 130, 180, 100))  # SteelBlue, semi-transparent
                
            # Check if account needs re-authentication
            if self.account_manager.needs_reauthentication(account):
                item.setForeground(QColor(231, 76, 60))  # Red
                item.setText(f"{account} (Re-authentication needed)")
                
            self.account_list.addItem(item)
            
        # Select the current account if available
        if current_account in accounts:
            self.account_list.setCurrentRow(accounts.index(current_account))
        elif accounts:
            self.account_list.setCurrentRow(0)
    
    def on_account_selected(self, row):
        """Handle account selection"""
        if row < 0:
            self.channel_info_label.setText("No account selected")
            self.statistics_label.setText("")
            self.rename_account_btn.setEnabled(False)
            self.remove_account_btn.setEnabled(False)
            self.refresh_btn.setEnabled(False)
            self.refresh_views_btn.setEnabled(False)
            self.reauth_btn.setEnabled(False)
            return
            
        account_name = self.account_list.item(row).text().split(" (")[0]  # Remove any status text
        
        # Enable buttons for account actions
        self.rename_account_btn.setEnabled(True)
        self.remove_account_btn.setEnabled(True)
        self.refresh_btn.setEnabled(True)
        self.refresh_views_btn.setEnabled(True)
        
        # Check if re-authentication is needed
        needs_reauth = self.account_manager.needs_reauthentication(account_name)
        self.reauth_btn.setEnabled(needs_reauth)
        self.reauth_btn.setVisible(needs_reauth)
        
        # Update channel info
        account_info = self.account_manager.accounts.get(account_name, {})
        channel_title = account_info.get('channel_title', 'Unknown')
        channel_id = account_info.get('channel_id', 'Unknown')
        
        info_text = f"Channel: {channel_title}\nID: {channel_id}"
        
        if needs_reauth:
            info_text += "\n\n⚠️ This account needs to be re-authenticated before it can be used."
            
        self.channel_info_label.setText(info_text)
        
        # Display statistics
        self.update_statistics_display(account_name)
    
    def update_statistics_display(self, account_name):
        """Update the statistics display for the selected account"""
        # First try to get stored statistics
        stats = self.account_manager.get_stored_statistics(account_name)
        
        if stats:
            stats_text = f"📊 Channel Statistics:\n"
            stats_text += f"👁️ Total Views: {stats['view_count']}\n"
            stats_text += f"👁️ Views (24h): {stats.get('views_24h', '0')}\n"
            
            if stats['hidden_subscriber_count']:
                stats_text += f"👥 Subscribers: Hidden\n"
            else:
                stats_text += f"👥 Subscribers: {stats['subscriber_count']}\n"
                
            stats_text += f"📹 Videos: {stats['video_count']}\n"
            stats_text += f"🆕 Uploads (24h): {stats.get('uploads_24h', 0)}"
            
            # Show recent upload titles if any
            recent_titles = stats.get('recent_upload_titles', [])
            if recent_titles:
                stats_text += f"\n\n📺 Recent Uploads:"
                for i, title in enumerate(recent_titles, 1):
                    # Truncate long titles
                    display_title = title[:40] + "..." if len(title) > 40 else title
                    stats_text += f"\n  {i}. {display_title}"
            
            self.statistics_label.setText(stats_text)
        else:
            self.statistics_label.setText("📊 Statistics: Click 'Refresh Stats' to load")
    
    def refresh_channel_info(self):
        """Refresh channel info for the selected account"""
        row = self.account_list.currentRow()
        if row < 0:
            return
            
        account_name = self.account_list.item(row).text().split(" (")[0]  # Remove any status text
        
        if self.account_manager.refresh_channel_info(account_name):
            # Also refresh statistics
            self.account_manager.get_channel_statistics(account_name)
            self.update_statistics_display(account_name)
            
            # Update the account list to show updated channel info
            self.refresh_account_list()
            QMessageBox.information(self, "Success", "Channel information and statistics updated successfully")
        else:
            QMessageBox.warning(self, "Warning", "Failed to update channel information")
    
    def add_account(self):
        """Add a new Google account"""
        if not self.account_manager.client_secrets_file:
            QMessageBox.warning(self, "Warning", "Please select a client secrets file first")
            return
            
        name, ok = QInputDialog.getText(self, "Add Account", "Enter account name:")
        
        if ok and name:
            # Start authentication process
            QMessageBox.information(
                self, "Authentication", 
                "The browser will open for you to sign in to your Google account.\n"
                "Please complete the authentication process."
            )
            
            if self.account_manager.add_account(name):
                self.refresh_account_list()
                # Get channel info from the newly added account
                channel_info = self.account_manager.accounts[name]
                channel_title = channel_info.get('channel_title', 'Unknown Channel')
                
                # Automatically fetch and display statistics
                self.account_manager.get_channel_statistics(name)
                self.update_statistics_display(name)
                
                QMessageBox.information(self, "Success", 
                                      f"Account '{name}' was added successfully\n"
                                      f"Channel: {channel_title}")
            else:
                QMessageBox.critical(self, "Error", f"Failed to add account '{name}'")
    
    def rename_account(self):
        """Rename the selected account"""
        if not self.account_list.currentItem():
            return
            
        old_name = self.account_list.currentItem().text().split(" (")[0]
        new_name, ok = QInputDialog.getText(
            self, "Rename Account", 
            "Enter new account name:", 
            text=old_name
        )
        
        if ok and new_name and new_name != old_name:
            if self.account_manager.rename_account(old_name, new_name):
                self.refresh_account_list()
            else:
                QMessageBox.critical(self, "Error", f"Failed to rename account to '{new_name}'")
    
    def remove_account(self):
        """Remove the selected account"""
        if not self.account_list.currentItem():
            return
            
        account_name = self.account_list.currentItem().text().split(" (")[0]
        
        reply = QMessageBox.question(
            self, "Confirm Removal",
            f"Are you sure you want to remove account '{account_name}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            if self.account_manager.remove_account(account_name):
                self.refresh_account_list()
            else:
                QMessageBox.critical(self, "Error", f"Failed to remove account '{account_name}'")
    
    def reauth_account(self):
        """Re-authenticate the selected account"""
        row = self.account_list.currentRow()
        if row < 0:
            return
            
        account_name = self.account_list.item(row).text().split(" (")[0]  # Remove any status text
        
        # Confirm re-authentication
        reply = QMessageBox.question(
            self, 
            "Re-authenticate Account",
            f"Do you want to re-authenticate the account '{account_name}'?\n\n"
            "This will open a browser window where you need to sign in to your Google account again.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            # Start re-authentication process
            QMessageBox.information(
                self, "Authentication", 
                "The browser will open for you to sign in to your Google account.\n"
                "Please complete the authentication process."
            )
            
            if self.account_manager.reauthorize_account(account_name):
                self.refresh_account_list()
                
                # Get updated channel info
                credentials = self.account_manager.get_account_credentials(account_name)
                channel_info = self.account_manager.accounts.get(account_name, {})
                channel_title = channel_info.get('channel_title', 'Unknown Channel')
                
                # Automatically fetch and display statistics
                self.account_manager.get_channel_statistics(account_name)
                self.update_statistics_display(account_name)
                
                # Set as current account
                self.account_manager.current_account = account_name
                self.account_manager.save_accounts()
                
                # Emit account changed signal
                self.account_changed.emit(account_name, credentials, channel_title)
                
                QMessageBox.information(
                    self, "Success", 
                    f"Account '{account_name}' was re-authenticated successfully\n"
                    f"Channel: {channel_title}"
                )
            else:
                QMessageBox.critical(
                    self, "Error", 
                    f"Failed to re-authenticate account '{account_name}'"
                )
    
    def accept(self):
        """Accept dialog and emit signal with selected account"""
        row = self.account_list.currentRow()
        if row >= 0:
            account_name = self.account_list.item(row).text().split(" (")[0]
            
            # Check if account needs re-authentication
            if self.account_manager.needs_reauthentication(account_name):
                QMessageBox.warning(
                    self, 
                    "Re-authentication Required",
                    f"The account '{account_name}' needs to be re-authenticated before it can be used.\n\n"
                    "Please click the 'Re-authenticate' button before selecting this account."
                )
                return
                
            # Set as current account
            self.account_manager.current_account = account_name
            self.account_manager.save_accounts()
            
            # Get credentials and emit signal
            credentials = self.account_manager.get_account_credentials(account_name)
            if credentials:
                channel_info = self.account_manager.accounts.get(account_name, {})
                channel_title = channel_info.get('channel_title', 'Unknown Channel')
                self.account_changed.emit(account_name, credentials, channel_title)
                super().accept()
            else:
                QMessageBox.critical(
                    self, 
                    "Error", 
                    "Could not get account credentials. The account may need to be re-authenticated."
                )
        else:
            QMessageBox.warning(self, "No Account Selected", "Please select an account first.")
    
    def refresh_views(self):
        """Refresh all statistics including views, subscribers, and recent uploads for the selected account"""
        row = self.account_list.currentRow()
        if row < 0:
            return
            
        account_name = self.account_list.item(row).text().split(" (")[0]  # Remove any status text
        
        # Check if account needs re-authentication
        if self.account_manager.needs_reauthentication(account_name):
            QMessageBox.warning(
                self, 
                "Re-authentication Required",
                f"The account '{account_name}' needs to be re-authenticated before statistics can be refreshed.\n\n"
                "Please click the 'Re-authenticate' button first."
            )
            return
        
        # Show progress message
        self.statistics_label.setText("🔄 Refreshing statistics...")
        QApplication.processEvents()  # Update UI
        
        try:
            # Get fresh statistics
            stats = self.account_manager.get_channel_statistics(account_name)
            
            if stats:
                # Update the display
                self.update_statistics_display(account_name)
                
                # Prepare success message
                success_msg = f"Statistics updated for '{account_name}'\n"
                success_msg += f"Views: {stats['view_count']}\n"
                success_msg += f"Views (24h): {stats.get('views_24h', '0')}\n"
                success_msg += f"Subscribers: {stats['subscriber_count'] if not stats['hidden_subscriber_count'] else 'Hidden'}\n"
                success_msg += f"Videos: {stats['video_count']}\n"
                success_msg += f"Uploads (24h): {stats.get('uploads_24h', 0)}"
                
                # Add recent upload titles if any
                recent_titles = stats.get('recent_upload_titles', [])
                if recent_titles:
                    success_msg += f"\n\nRecent uploads:"
                    for i, title in enumerate(recent_titles, 1):
                        display_title = title[:30] + "..." if len(title) > 30 else title
                        success_msg += f"\n{i}. {display_title}"
                
                QMessageBox.information(self, "Success", success_msg)
            else:
                self.statistics_label.setText("❌ Failed to load statistics")
                QMessageBox.warning(
                    self, 
                    "Warning", 
                    f"Failed to refresh statistics for '{account_name}'\n"
                    "Please check your internet connection and try again."
                )
        except Exception as e:
            self.statistics_label.setText("❌ Error loading statistics")
            QMessageBox.critical(
                self, 
                "Error", 
                f"Error refreshing statistics: {str(e)}"
            )