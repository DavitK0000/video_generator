import os, time, datetime
from typing import Optional
import queue
import socket
import pickle
from contextlib import contextmanager

from PyQt5.QtCore import QThread, pyqtSignal, QMutex, QMutexLocker
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError

class UploadThread(QThread):
    """Thread for uploading videos to YouTube
    
    This class handles the entire YouTube upload process, including automatic token refresh
    when credentials expire. When a token is refreshed, the updated credentials are emitted
    via token_refresh_signal for the parent application to save.
    """
    
    # Signals
    progress_signal = pyqtSignal(int)
    status_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(str, str)  # url, video_id
    error_signal = pyqtSignal(str)
    token_refresh_signal = pyqtSignal(object)  # Emits refreshed credentials for persistent storage
    
    def __init__(self, 
                 credentials, 
                 video_path, 
                 title, 
                 description, 
                 category, 
                 tags, 
                 privacy_status, 
                 thumbnail_path=None, 
                 publish_at: datetime=None, 
                 made_for_kids=False):
        super().__init__()
        
        self.credentials = credentials
        self.video_path = video_path
        self.title = title
        self.description = description
        self.category = category
        self.tags = tags.split(",") if tags else []
        self.privacy_status = privacy_status
        self.thumbnail_path = thumbnail_path
        self.publish_at = publish_at
        self.made_for_kids = made_for_kids
        self._running = True
        self.mutex = QMutex()
        self.youtube = None
        self.insert_request = None
        
        # Create a queue for thread-safe communication
        self.status_queue = queue.Queue()
        self.progress_queue = queue.Queue()
    
    @property
    def running(self):
        """Thread-safe access to running state"""
        with QMutexLocker(self.mutex):
            return self._running
    
    @running.setter
    def running(self, value):
        """Thread-safe setting of running state"""
        with QMutexLocker(self.mutex):
            self._running = value
    
    @contextmanager
    def configure_timeouts(self):
        """Configure timeouts for the upload process"""
        original_timeout = socket.getdefaulttimeout()
        try:
            socket.setdefaulttimeout(600)  # 10 minutes timeout
            yield
        finally:
            socket.setdefaulttimeout(original_timeout)
    
    def cleanup(self):
        """Clean up resources"""
        try:
            if self.youtube:
                del self.youtube
            if self.insert_request:
                # Just set it to None, no need to call cancel()
                self.insert_request = None
        except Exception as e:
            self.status_signal.emit(f"Cleanup error: {str(e)}")
        finally:
            self.youtube = None
            self.insert_request = None
    
    def refresh_credentials(self):
        """Refresh the access token if expired"""
        try:
            # Check if credentials are expired
            if hasattr(self.credentials, 'expired') and self.credentials.expired:
                if hasattr(self.credentials, 'refresh_token') and self.credentials.refresh_token:
                    self.status_signal.emit("Access token expired. Refreshing...")
                    try:
                        self.credentials.refresh(Request())
                        self.token_refresh_signal.emit(self.credentials)
                        self.status_signal.emit("Token refreshed successfully")
                        return True
                    except Exception as refresh_error:
                        error_str = str(refresh_error)
                        if "invalid_grant" in error_str or "Token has been expired or revoked" in error_str:
                            self.error_signal.emit("Your refresh token has expired or been revoked. Please re-authenticate your YouTube account.")
                            self.status_signal.emit("Complete re-authentication required due to expired refresh token.")
                            return False
                        # Re-raise for other errors to be caught by outer try-except
                        raise
                else:
                    self.error_signal.emit("No refresh token available. Please re-authenticate.")
                    return False
            return True  # Credentials are valid
        except RefreshError as e:
            self.error_signal.emit(f"Could not refresh token: {str(e)}")
            self.status_signal.emit("Authentication error. Please re-authenticate your YouTube account.")
            return False
        except Exception as e:
            self.error_signal.emit(f"Error refreshing token: {str(e)}")
            return False
    
    def run(self):
        """Upload the video to YouTube
        
        This method handles the upload process and includes automatic token refresh
        when credentials expire. If a token refresh is successful, the updated
        credentials are emitted via token_refresh_signal to be saved by the parent.
        """
        try:
            if not os.path.exists(self.video_path):
                self.error_signal.emit(f"Video file not found: {self.video_path}")
                return
            
            # Check and refresh credentials if needed
            if not self.refresh_credentials():
                self.error_signal.emit("Failed to refresh credentials. Please re-authenticate.")
                return
            
            # Get file size
            file_size = os.path.getsize(self.video_path)
            self.status_signal.emit(f"File size: {file_size / 1024 / 1024:.2f} MB")
            
            # Configure timeouts and build YouTube service
            with self.configure_timeouts():
                self.youtube = build('youtube', 'v3', credentials=self.credentials)
                
                # Set up video metadata
                body = {
                    'snippet': {
                        'title': self.title,
                        'description': self.description,
                        'tags': self.tags,
                        'categoryId': self.category
                    },
                    'status': {
                        'privacyStatus': self.privacy_status,
                        'selfDeclaredMadeForKids': self.made_for_kids
                    }
                }
                
                # Add scheduled publishing if specified
                if self.publish_at and self.privacy_status == 'public':
                    body['status']['publishAt'] = self.publish_at.isoformat()
                    body['status']['privacyStatus'] = 'private'  # Set to private until publish time
                
                # Calculate optimal chunk size based on file size
                # Use smaller chunks for better reliability
                chunk_size = min(512 * 1024, max(256 * 1024, file_size // 200))
                self.status_signal.emit(f"Using chunk size: {chunk_size / 1024:.2f} KB")
                
                # Set up the media file upload with retry mechanism
                media = MediaFileUpload(
                    self.video_path,
                    chunksize=chunk_size,
                    resumable=True
                )
                
                # Start the upload
                self.status_signal.emit("Starting upload...")
                self.insert_request = self.youtube.videos().insert(
                    part=','.join(body.keys()),
                    body=body,
                    media_body=media
                )
                
                # Monitor upload progress with retry mechanism
                response = None
                retry_count = 0
                max_retries = 3
                last_progress = 0
                
                while response is None and self.running:
                    try:
                        status, response = self.insert_request.next_chunk()
                        if status:
                            progress = int(status.progress() * 100)
                            if progress > last_progress:  # Only emit if progress increased
                                self.progress_signal.emit(progress)
                                self.status_signal.emit(f"Uploading: {progress}%")
                                last_progress = progress
                            retry_count = 0  # Reset retry count on successful chunk
                    except HttpError as e:
                        if not self.running:
                            self.cleanup()
                            self.error_signal.emit("Upload cancelled")
                            return
                        
                        # Check if token expired (401 error)
                        if e.resp.status == 401:
                            self.status_signal.emit("Token expired during upload. Attempting to refresh...")
                            if self.refresh_credentials():
                                # Recreate the YouTube service with refreshed credentials
                                self.youtube = build('youtube', 'v3', credentials=self.credentials)
                                # Recreate the insert request
                                self.insert_request = self.youtube.videos().insert(
                                    part=','.join(body.keys()),
                                    body=body,
                                    media_body=media
                                )
                                self.status_signal.emit("Resuming upload after token refresh...")
                                continue
                            else:
                                raise e
                        
                        retry_count += 1
                        if retry_count > max_retries:
                            raise e
                        
                        self.status_signal.emit(f"Upload interrupted, retrying ({retry_count}/{max_retries})...")
                        time.sleep(2)  # Wait before retry
                        continue
                    except Exception as e:
                        if not self.running:
                            self.cleanup()
                            self.error_signal.emit("Upload cancelled")
                            return
                        raise e
                
                if not self.running:
                    self.cleanup()
                    self.error_signal.emit("Upload cancelled")
                    return
                
                # Get the video ID
                video_id = response['id']
                
                # Upload thumbnail if provided
                if self.thumbnail_path and os.path.exists(self.thumbnail_path):
                    try:
                        self.status_signal.emit("Uploading thumbnail...")
                        # Ensure credentials are valid before thumbnail upload
                        if not self.refresh_credentials():
                            self.error_signal.emit("Failed to refresh credentials for thumbnail upload")
                        else:
                            self.youtube.thumbnails().set(
                                videoId=video_id,
                                media_body=MediaFileUpload(self.thumbnail_path)
                            ).execute()
                    except HttpError as e:
                        # Check if token expired (401 error)
                        if e.resp.status == 401:
                            self.status_signal.emit("Token expired during thumbnail upload. Attempting to refresh...")
                            if self.refresh_credentials():
                                # Retry with refreshed credentials
                                try:
                                    self.youtube = build('youtube', 'v3', credentials=self.credentials)
                                    self.youtube.thumbnails().set(
                                        videoId=video_id,
                                        media_body=MediaFileUpload(self.thumbnail_path)
                                    ).execute()
                                    self.status_signal.emit("Thumbnail uploaded successfully after token refresh")
                                except Exception as e2:
                                    self.status_signal.emit(f"Thumbnail upload failed after token refresh: {str(e2)}")
                            else:
                                self.status_signal.emit(f"Thumbnail upload failed: {str(e)}")
                        else:
                            self.status_signal.emit(f"Thumbnail upload failed: {str(e)}")
                    except Exception as e:
                        self.status_signal.emit(f"Thumbnail upload failed: {str(e)}")
                
                # Prepare video URL
                video_url = f"https://www.youtube.com/watch?v={video_id}"
                
                # Signal completion
                self.progress_signal.emit(100)
                
                # Determine final status message
                if self.publish_at and self.privacy_status == 'public':
                    status_msg = f"Video scheduled for {self.publish_at.strftime('%Y-%m-%d %H:%M')}"
                else:
                    status_msg = f"Video {self.privacy_status} at {video_url}"
                
                self.status_signal.emit(status_msg)
                self.finished_signal.emit(video_url, video_id)
        
        except HttpError as e:
            error_content = e.content.decode('utf-8') if hasattr(e, 'content') else str(e)
            error_details = []
            
            # Collect detailed error information
            error_details.append(f"HTTP Error: {error_content}")
            error_details.append(f"Error code: {e.resp.status}")
            if hasattr(e, 'reason'):
                error_details.append(f"Reason: {e.reason}")
            
            # Add specific error context based on status code
            if e.resp.status == 400:
                error_details.append("Bad Request - Check video format, size, and metadata")
            elif e.resp.status == 401:
                error_details.append("Authentication failed - Token may be invalid or expired")
            elif e.resp.status == 403:
                error_details.append("Permission denied - Make sure your account has access to this channel and can upload videos")
                error_details.append("Also check if you've accepted YouTube Terms of Service")
            elif e.resp.status == 404:
                error_details.append("Resource not found - API endpoint may have changed")
            elif e.resp.status == 413:
                error_details.append("Video file too large - Check YouTube size limits")
            elif e.resp.status == 500:
                error_details.append("YouTube server error - Try again later")
            elif e.resp.status == 503:
                error_details.append("YouTube service unavailable - Try again later")
            
            # Try to parse detailed error message from response
            try:
                if hasattr(e, 'content'):
                    import json
                    error_json = json.loads(e.content.decode('utf-8'))
                    if 'error' in error_json and 'message' in error_json['error']:
                        error_details.append(f"API Error Message: {error_json['error']['message']}")
                    if 'error' in error_json and 'errors' in error_json['error']:
                        for error in error_json['error']['errors']:
                            if 'message' in error:
                                error_details.append(f"Detailed Error: {error['message']}")
                            if 'reason' in error:
                                error_details.append(f"Error Reason: {error['reason']}")
            except Exception as parse_error:
                error_details.append(f"Could not parse detailed error: {str(parse_error)}")
            
            # Send all error details
            for detail in error_details:
                self.status_signal.emit(detail)
            
            # Send main error message
            self.error_signal.emit(f"Upload failed: {error_content}")
        
        except Exception as e:
            self.error_signal.emit(f"Error: {str(e)}")
        finally:
            self.cleanup()
    
    def cancel(self):
        """Cancel the upload"""
        self.running = False
        self.cleanup()