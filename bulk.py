import os
import sys
current_directory = os.path.dirname(os.path.abspath(sys.argv[0]))
os.chdir(current_directory)

import tracemalloc
tracemalloc.start()

# Enable fault handler for better crash reports, but only if not running as PyInstaller executable
import faulthandler
try:
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # Running as PyInstaller bundle
        pass
    else:
        # Running in normal Python environment
        faulthandler.enable()
except Exception:
    pass  # Ignore faulthandler errors

# Enable memory logging
import psutil
import gc
def log_memory_usage():
    process = psutil.Process()
    gc.collect()  # Force garbage collection
    memory_info = process.memory_info()
    return f"Memory usage: {memory_info.rss / 1024 / 1024:.2f} MB"

from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum

import pandas as pd
import numpy as np
import time
import log
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QTableWidget, QTableWidgetItem, 
                             QPushButton, QProgressBar, QTextEdit, QLabel,
                             QFileDialog, QMessageBox, QDialog, QFormLayout,
                             QLineEdit, QComboBox, QDialogButtonBox, QHeaderView,
                             QSplitter, QFrame, QStyleFactory, QAbstractItemView,
                             QCheckBox, QDateTimeEdit, QGroupBox, QStackedWidget,
                             QShortcut)
from PyQt5.QtCore import QThread, Qt, QDateTime, pyqtSlot, Q_ARG, QTimer, QMetaObject, QMutex, QMutexLocker
from PyQt5.QtGui import QPalette, QColor, QFont, QKeySequence, QKeyEvent
from accounts import AccountManager
from utils import validate_preset_content, validate_workflow_content, title_to_safe_folder_name
from worker import GenerationWorker, cleanup_all_temp_dirs
from uploader import UploadThread
import logging
import queue
import json
import datetime
import pytz

# Constants
class TableColumns(Enum):
    VIDEO_TITLE = 0
    PRESET_PATH = 1
    CHANNEL_NAME = 2
    ACCOUNT = 3
    CATEGORY = 4
    SCHEDULE = 5
    STATUS = 6
    GEN_PROGRESS = 7
    UPLOAD_PROGRESS = 8
    VIDEO_URL = 9
    REGENERATE_BTN = 10
    REUPLOAD_BTN = 11

class StatusColors:
    COMPLETED = QColor(40, 80, 40)  # Dark green
    PROCESSING = QColor(80, 60, 30)  # Dark yellow/orange
    ERROR = QColor(80, 40, 40)  # Dark red
    VALIDATING = QColor(50, 50, 80)  # Dark blue

@dataclass
class RowData:
    video_title: str = ''
    preset_path: str = ''
    youtube_upload_enabled: bool = False
    channel_name: str = ''
    account: str = ''
    category: str = ''
    schedule: str = ''
    status: str = 'Ready'
    gen_progress: str = '0%'
    upload_progress: str = '0%'
    video_url: str = ''
    needs_regeneration: bool = False
    needs_reupload: bool = False
    saved_description: str = ''  # Store description for reupload

class TableManager:
    """Manages table operations and data handling"""
    
    def __init__(self, table: QTableWidget, logger: logging.Logger):
        self.table = table
        self.logger = logger
        self._setup_table()
    
    def _setup_table(self):
        """Initialize table settings"""
        self.table.setColumnCount(len(TableColumns))
        headers = [col.name.replace('_', ' ') for col in TableColumns]
        # Make the progress headers more user-friendly
        headers[TableColumns.GEN_PROGRESS.value] = "Generation"
        headers[TableColumns.UPLOAD_PROGRESS.value] = "Upload"
        headers[TableColumns.REGENERATE_BTN.value] = ""  # Empty header for buttons
        headers[TableColumns.REUPLOAD_BTN.value] = ""
        self.table.setHorizontalHeaderLabels(headers)
        
        header = self.table.horizontalHeader()
        if header:
            header.setSectionResizeMode(QHeaderView.Interactive)
        
        # Set column widths
        for col in [TableColumns.VIDEO_TITLE, TableColumns.PRESET_PATH, TableColumns.CHANNEL_NAME]:
            self.table.setColumnWidth(col.value, 200)
        
        # Set smaller widths for progress columns and buttons
        self.table.setColumnWidth(TableColumns.GEN_PROGRESS.value, 100)
        self.table.setColumnWidth(TableColumns.UPLOAD_PROGRESS.value, 100)
        self.table.setColumnWidth(TableColumns.REGENERATE_BTN.value, 100)
        self.table.setColumnWidth(TableColumns.REUPLOAD_BTN.value, 100)
        
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setAlternatingRowColors(True)
        
        # Set row height
        vertical_header = self.table.verticalHeader()
        if vertical_header:
            vertical_header.setDefaultSectionSize(30)  # Increase row height
    
    def create_button(self, text: str, enabled: bool = False) -> QPushButton:
        """Create a styled button for the table"""
        button = QPushButton(text)
        button.setEnabled(enabled)
        button.setStyleSheet("""
            QPushButton {
                padding: 5px;
                border-radius: 3px;
                background-color: #2ecc71;
                color: white;
            }
            QPushButton:disabled {
                background-color: #95a5a6;
            }
            QPushButton:hover:!disabled {
                background-color: #27ae60;
            }
        """)
        return button

    def add_row(self, data: RowData) -> int:
        """Add a new row to the table"""
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.update_row(row, data)
        return row
    
    def update_row(self, row: int, data: RowData):
        """Update an existing row with new data"""
        if row >= self.table.rowCount():
            return
            
        # Map data to columns
        for col in TableColumns:
            if col in [TableColumns.REGENERATE_BTN, TableColumns.REUPLOAD_BTN]:
                continue  # Skip buttons, they're handled separately
            
            value = getattr(data, col.name.lower())
            # Convert boolean values to display strings
            if isinstance(value, bool):
                value = "Yes" if value else "No"
            item = QTableWidgetItem(str(value))
            
            # Make status and progress columns read-only
            if col in [TableColumns.STATUS, TableColumns.GEN_PROGRESS, TableColumns.UPLOAD_PROGRESS]:
                # Set read-only by removing edit flag
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                
            self.table.setItem(row, col.value, item)
        
        # Create and update buttons
        regenerate_btn = self.create_button("Re-generate", data.needs_regeneration)
        # Only enable reupload button if YouTube upload is enabled
        reupload_enabled = data.needs_reupload and data.youtube_upload_enabled
        reupload_btn = self.create_button("Re-upload", reupload_enabled)
        
        self.table.setCellWidget(row, TableColumns.REGENERATE_BTN.value, regenerate_btn)
        self.table.setCellWidget(row, TableColumns.REUPLOAD_BTN.value, reupload_btn)
        
        self.validate_and_color_row(row, data)
    
    def get_row_data(self, row: int) -> Optional[RowData]:
        """Get data from a table row"""
        if row < 0 or row >= self.table.rowCount():
            return None
            
        data = RowData()
        for col in TableColumns:
            item = self.table.item(row, col.value)
            if item:
                value = item.text()
                # Handle boolean fields
                if col.name.lower() in ['youtube_upload_enabled', 'needs_regeneration', 'needs_reupload']:
                    value = value.lower() in ['true', '1', 'yes', 'on']
                setattr(data, col.name.lower(), value)
        return data
    
    def validate_and_color_row(self, row: int, data: RowData) -> bool:
        """Validate row data and apply color coding"""
        is_valid = True
        
        # Validate preset file
        preset_item = self.table.item(row, TableColumns.PRESET_PATH.value)
        if preset_item:
            if not data.preset_path or not os.path.exists(data.preset_path):
                preset_item.setBackground(StatusColors.ERROR)
                is_valid = False
            elif not validate_preset_content(data.preset_path):
                preset_item.setBackground(StatusColors.PROCESSING)
                is_valid = False
            else:
                preset_item.setBackground(StatusColors.COMPLETED)
        
        # Validate channel name (required when YouTube upload is disabled)
        channel_item = self.table.item(row, TableColumns.CHANNEL_NAME.value)
        if channel_item:
            if not data.youtube_upload_enabled and not data.channel_name:
                channel_item.setBackground(StatusColors.ERROR)
                is_valid = False
            elif data.channel_name:
                channel_item.setBackground(StatusColors.COMPLETED)
            else:
                channel_item.setBackground(StatusColors.PROCESSING)
        
        # Validate account (required when YouTube upload is enabled)
        account_item = self.table.item(row, TableColumns.ACCOUNT.value)
        if account_item:
            if data.youtube_upload_enabled and not data.account:
                account_item.setBackground(StatusColors.ERROR)
                is_valid = False
            elif data.account:
                account_item.setBackground(StatusColors.COMPLETED)
            else:
                account_item.setBackground(StatusColors.PROCESSING)
        
        return is_valid
    
    def update_row_status(self, row: int, status: str, gen_progress: str, upload_progress: str, log_progress: bool = True):
        """Update row status and progress"""
        if row >= self.table.rowCount():
            return
            
        # Update status and progress
        self.table.setItem(row, TableColumns.STATUS.value, QTableWidgetItem(status))
        if gen_progress is not None:
            progress_item = QTableWidgetItem(gen_progress)
            # Color error progress cells red
            if gen_progress == "Error":
                progress_item.setBackground(StatusColors.ERROR)
            self.table.setItem(row, TableColumns.GEN_PROGRESS.value, progress_item)
        if upload_progress is not None:
            progress_item = QTableWidgetItem(upload_progress)
            # Color error progress cells red
            if upload_progress == "Error":
                progress_item.setBackground(StatusColors.ERROR)
            self.table.setItem(row, TableColumns.UPLOAD_PROGRESS.value, progress_item)
            
        # Only log completed or failed status changes
        # if log_progress and (status == "Completed" or "Error" or "Validating" in status):
        #     self.logger.info(f"Item {row + 1}: {status}")
        
        # Set color based on status
        color = {
            "Completed": StatusColors.COMPLETED,
            "Processing": StatusColors.PROCESSING,
            "Uploading": StatusColors.PROCESSING,
            "Error": StatusColors.ERROR,
            "Error (Validation)": StatusColors.ERROR,
            "Validating": StatusColors.VALIDATING
        }.get(status, StatusColors.PROCESSING)
        
        status_item = self.table.item(row, TableColumns.STATUS.value)
        if status_item:
            status_item.setBackground(color)
        
        # Update button states based on status
        needs_regeneration = status == "Error" and "Upload" not in status
        needs_reupload = status == "Error" and "Upload" in status
        
        # Get row data to check YouTube upload setting
        row_data = self.get_row_data(row)
        if row_data and not row_data.youtube_upload_enabled:
            # Disable reupload button if YouTube upload is disabled
            needs_reupload = False
        
        regenerate_btn = self.table.cellWidget(row, TableColumns.REGENERATE_BTN.value)
        reupload_btn = self.table.cellWidget(row, TableColumns.REUPLOAD_BTN.value)
        
        if regenerate_btn:
            regenerate_btn.setEnabled(needs_regeneration)
        if reupload_btn:
            reupload_btn.setEnabled(needs_reupload)
    
    def clear(self):
        """Clear all rows from the table"""
        self.table.setRowCount(0)
    
    def get_all_data(self) -> List[RowData]:
        """Get data from all rows"""
        data = []
        for row in range(self.table.rowCount()):
            row_data = self.get_row_data(row)
            if row_data:
                data.append(row_data)
        return data

class SettingsDialog(QDialog):
    """Dialog for editing generation settings"""
    
    def __init__(self, parent=None, row_data: Optional[Dict[str, str]] = None, accounts: Optional[List[str]] = None):
        super().__init__(parent)
        self.setWindowTitle("Edit Generation Settings")
        self.setModal(True)
        self.resize(500, 200)
        
        self.available_accounts = accounts or []
        self.setup_ui()
        
        if row_data:
            self.load_data(row_data)
    
    def setup_ui(self):
        """Initialize the dialog UI"""
        layout = QFormLayout()
        
        # Video Title
        self.video_title_edit = QLineEdit()
        self.video_title_edit.setPlaceholderText("Input the video title")
        
        # Preset file path
        preset_layout = QHBoxLayout()
        self.preset_edit = QLineEdit()
        self.preset_edit.setReadOnly(True)
        self.preset_browse_btn = QPushButton("Browse")
        self.preset_browse_btn.clicked.connect(lambda: self.browse_file("Preset"))
        preset_layout.addWidget(self.preset_edit)
        preset_layout.addWidget(self.preset_browse_btn)
        
        # YouTube upload checkbox
        self.youtube_upload_checkbox = QCheckBox("Upload video to YouTube")
        self.youtube_upload_checkbox.setStyleSheet("padding: 8px; font-weight: bold;")
        self.youtube_upload_checkbox.stateChanged.connect(self.toggle_youtube_upload)
        
        # Channel name input (for when YouTube upload is disabled)
        self.channel_name_edit = QLineEdit()
        self.channel_name_edit.setPlaceholderText("Enter channel name for file organization")
        self.channel_name_edit.setStyleSheet("padding: 8px;")
        self.channel_name_edit.setEnabled(True)  # Enabled by default when YouTube upload is off
        
        # Account selection
        self.account_combo = QComboBox()
        self.account_combo.setEditable(False)
        self.account_combo.addItems(self.available_accounts)
        self.account_combo.setEnabled(False)  # Disabled by default
        
        # Category ID
        self.category_id_edit = QLineEdit()
        self.category_id_edit.setPlaceholderText("Input the category id")
        self.category_id_edit.setText('24')  # Default category
        self.category_id_edit.setEnabled(False)  # Disabled by default
        
        # Schedule
        self.schedule_checkbox = QCheckBox("Schedule publication")
        self.schedule_checkbox.stateChanged.connect(self.toggle_schedule)
        self.schedule_checkbox.setEnabled(False)  # Disabled by default
        
        self.schedule_datetime = QDateTimeEdit()
        self.schedule_datetime.setMinimumDateTime(QDateTime.currentDateTime().addSecs(300))
        self.schedule_datetime.setEnabled(False)
        
        # Add fields to layout
        layout.addRow("Video Title:", self.video_title_edit)
        layout.addRow("Preset File:", preset_layout)
        layout.addRow("Upload to YouTube:", self.youtube_upload_checkbox)
        layout.addRow("Channel Name:", self.channel_name_edit)
        layout.addRow("Account:", self.account_combo)
        layout.addRow("Category Id:", self.category_id_edit)
        layout.addRow("Schedule publication:", self.schedule_checkbox)
        layout.addRow("", self.schedule_datetime)
        
        # Dialog buttons
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.validate_and_accept)
        self.button_box.rejected.connect(self.reject)
        layout.addRow(self.button_box)
        
        self.setLayout(layout)
    
    def toggle_youtube_upload(self, state):
        """Toggle YouTube upload functionality"""
        is_enabled = state == Qt.CheckState.Checked
        
        # Enable/disable YouTube credential fields
        self.account_combo.setEnabled(is_enabled)
        self.category_id_edit.setEnabled(is_enabled)
        self.schedule_checkbox.setEnabled(is_enabled)
        self.schedule_datetime.setEnabled(is_enabled and self.schedule_checkbox.isChecked())
        
        # Enable/disable channel name input (opposite of YouTube upload)
        self.channel_name_edit.setEnabled(not is_enabled)
    
    def toggle_schedule(self, state: int):
        """Toggle schedule datetime field"""
        self.schedule_datetime.setEnabled(state == Qt.CheckState.Checked)
    
    def browse_file(self, file_type: str):
        """Browse for preset or workflow file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, f"Select {file_type} File", "", "JSON Files (*.json)")
        
        if file_path:
            if file_type == "Preset":
                self.preset_edit.setText(file_path)
    
    def load_data(self, data: Dict[str, str]):
        """Load existing data into dialog"""
        self.preset_edit.setText(data.get('preset_path', ''))
        self.video_title_edit.setText(data.get('video_title', ''))
        self.category_id_edit.setText(data.get('category', '24'))
        
        # Load YouTube upload setting
        youtube_upload_enabled = bool(data.get('youtube_upload_enabled', False))
        self.youtube_upload_checkbox.setChecked(youtube_upload_enabled)
        self.toggle_youtube_upload(Qt.CheckState.Checked if youtube_upload_enabled else Qt.CheckState.Unchecked)
        
        # Load channel name
        self.channel_name_edit.setText(data.get('channel_name', ''))
        
        # Set account
        account = data.get('account', '')
        index = self.account_combo.findText(account)
        if index >= 0:
            self.account_combo.setCurrentIndex(index)
        
        # Set schedule
        schedule = data.get('schedule', '')
        if schedule:
            self.schedule_checkbox.setChecked(True)
            try:
                schedule_dt = QDateTime.fromString(schedule, Qt.DateFormat.ISODate)
                self.schedule_datetime.setDateTime(schedule_dt)
            except Exception:
                pass
    
    def validate_and_accept(self):
        """Validate input before accepting"""
        video_title = self.video_title_edit.text().strip()
        if not video_title:
            QMessageBox.warning(self, "Input Error", "Video title cannot be empty.")
            return
        
        preset_path = self.preset_edit.text().strip()
        if not preset_path or not os.path.exists(preset_path):
            QMessageBox.warning(self, "Input Error", "Please select a valid preset file.")
            return
        
        # if not self.account_combo.currentText():
        #     QMessageBox.warning(self, "Input Error", "Please select an account.")
        #     return
        
        self.accept()
    
    def get_data(self) -> Dict[str, Any]:
        """Get dialog data as dictionary"""
        schedule = ""
        if self.schedule_checkbox.isChecked():
            schedule = self.schedule_datetime.dateTime().toString(Qt.DateFormat.ISODate)
        
        return {
            'video_title': self.video_title_edit.text().strip(),
            'preset_path': self.preset_edit.text().strip(),
            'youtube_upload_enabled': self.youtube_upload_checkbox.isChecked(),
            'channel_name': self.channel_name_edit.text().strip(),
            'account': self.account_combo.currentText(),
            'category': self.category_id_edit.text().strip(),
            'schedule': schedule
        }

class BulkGenerationApp(QMainWindow):
    """Main application window"""
    
    def __init__(self):
        super().__init__()
        self.logger, _ = log.setup_logger()
        
        # Initialize state
        self.generation_worker = None
        self.upload_thread = None
        self.current_index = 0
        self.generation_data = []
        self.is_cancelled = False
        self.mutex = QMutex()
        
        self.current_directory = os.path.dirname(os.path.abspath(sys.argv[0]))
        os.chdir(self.current_directory)
        
        # Setup UI and connections
        self.setup_ui()
        self.setup_connections()
        self.setup_timer_based_logging()
        self.setup_shortcuts()
    
    def setup_shortcuts(self):
        """Setup keyboard shortcuts"""
        # Delete/Backspace key for row deletion (platform independent)
        delete_keys = [Qt.Key.Key_Delete, Qt.Key.Key_Backspace]
        for key in delete_keys:
            shortcut = QShortcut(key, self)
            shortcut.activated.connect(self.delete_row)
        
        # Save shortcut (Ctrl+S on Windows/Linux, Cmd+S on macOS)
        save_shortcut = QShortcut(QKeySequence.Save, self)
        save_shortcut.activated.connect(self.save_data)
        
        # Open shortcut (Ctrl+O on Windows/Linux, Cmd+O on macOS)
        load_shortcut = QShortcut(QKeySequence.Open, self)
        load_shortcut.activated.connect(self.load_data)
        
        # Make shortcuts work when table has focus
        self.settings_table.keyPressEvent = self.handle_table_keypress
    
    def handle_table_keypress(self, e):
        """Handle key press events in the table"""
        if e.key() in [Qt.Key.Key_Delete, Qt.Key.Key_Backspace]:
            self.delete_row()
        elif e.matches(QKeySequence.Save):
            self.save_data()
        elif e.matches(QKeySequence.Open):
            self.load_data()
        else:
            # Make sure we maintain default table key handling
            QTableWidget.keyPressEvent(self.settings_table, e)
    
    def setup_ui(self):
        """Initialize the user interface"""
        self.setWindowTitle("Bulk Generation Manager")
        self.setGeometry(100, 100, 1200, 800)
        
        # Initialize account manager
        self.account_manager = AccountManager(
            accounts_file=os.path.join(self.current_directory, 'accounts.json'),
            client_secrets_file=os.path.join(self.current_directory, 'google_auth.json'),
            logger=self.logger
        )
        
        # Create central widget and layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        
        # Create splitter for panels
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)
        
        # Add panels
        splitter.addWidget(self.create_left_panel())
        splitter.addWidget(self.create_right_panel())
        splitter.setSizes([900, 300])
    
    def create_left_panel(self):
        """Create the left panel with settings table"""
        panel = QFrame()
        panel.setFrameStyle(QFrame.StyledPanel)
        layout = QVBoxLayout(panel)
        
        # Add title
        title = QLabel("Generation Settings")
        title.setFont(QFont("Arial", 12, QFont.Bold))
        layout.addWidget(title)
        
        # Create and setup table
        self.settings_table = QTableWidget()
        self.table_manager = TableManager(self.settings_table, self.logger)
        layout.addWidget(self.settings_table)
        
        # Add button layout
        button_layout = QHBoxLayout()
        
        # Create buttons
        self.add_btn = QPushButton("Add Row")
        self.edit_btn = QPushButton("Edit Row")
        self.delete_btn = QPushButton("Delete Row")
        self.load_btn = QPushButton("Load Data")
        self.save_btn = QPushButton("Save Data")
        
        # Add buttons to layout
        for btn in [self.add_btn, self.edit_btn, self.delete_btn]:
            button_layout.addWidget(btn)
        button_layout.addStretch()
        for btn in [self.load_btn, self.save_btn]:
            button_layout.addWidget(btn)
        
        layout.addLayout(button_layout)
        return panel
    
    def create_right_panel(self):
        """Create the right panel with progress and logs"""
        panel = QFrame()
        panel.setFrameStyle(QFrame.StyledPanel)
        layout = QVBoxLayout(panel)
        
        # Add title
        title = QLabel("Generation Progress")
        title.setFont(QFont("Arial", 12, QFont.Bold))
        layout.addWidget(title)
        
        # Generation progress section
        gen_group = QGroupBox("Generation")
        gen_layout = QVBoxLayout(gen_group)
        
        self.generation_progress = QProgressBar()
        self.generation_progress.setMinimumHeight(25)  # Make progress bar taller
        self.generation_status = QLabel("Ready to start generation")
        
        gen_layout.addWidget(self.generation_progress)
        gen_layout.addWidget(self.generation_status)
        layout.addWidget(gen_group)
        
        # Upload progress section
        upload_group = QGroupBox("Upload")
        upload_layout = QVBoxLayout(upload_group)
        
        self.upload_progress = QProgressBar()
        self.upload_progress.setMinimumHeight(25)  # Make progress bar taller
        self.upload_status = QLabel("Waiting for upload")
        
        upload_layout.addWidget(self.upload_progress)
        upload_layout.addWidget(self.upload_status)
        layout.addWidget(upload_group)
        
        # Add logs section
        logs_label = QLabel("Logs:")
        logs_label.setFont(QFont("Arial", 10, QFont.Bold))
        layout.addWidget(logs_label)
        
        self.log_window = QTextEdit()
        self.log_window.setReadOnly(True)
        self.log_window.setMaximumHeight(300)
        layout.addWidget(self.log_window)
        
        # Add control buttons with improved styling
        control_layout = QHBoxLayout()
        
        # Create a stacked widget for the buttons
        self.button_stack = QStackedWidget()
        self.button_stack.setMinimumHeight(40)  # Set minimum height for buttons
        
        # Start button
        self.start_btn = QPushButton("Start Generation")
        self.start_btn.setMinimumHeight(40)
        self.start_btn.setStyleSheet("""
            QPushButton {
                padding: 8px 16px;
                font-size: 16px;
                font-weight: bold;
                background-color: #2ecc71;
                border: none;
                border-radius: 4px;
                color: white;
            }
            QPushButton:hover {
                background-color: #27ae60;
            }
            QPushButton:pressed {
                background-color: #219a52;
            }
        """)
        
        # Cancel button
        self.cancel_btn = QPushButton("Cancel Generation")
        self.cancel_btn.setMinimumHeight(40)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                padding: 8px 16px;
                font-size: 16px;
                font-weight: bold;
                background-color: #e74c3c;
                border: none;
                border-radius: 4px;
                color: white;
            }
            QPushButton:hover {
                background-color: #c0392b;
            }
            QPushButton:pressed {
                background-color: #a93226;
            }
        """)
        
        # Add buttons to stack - now using full width
        button_container1 = QWidget()
        button_layout1 = QHBoxLayout(button_container1)
        button_layout1.setContentsMargins(0, 0, 0, 0)  # Remove margins
        button_layout1.addWidget(self.start_btn)
        
        button_container2 = QWidget()
        button_layout2 = QHBoxLayout(button_container2)
        button_layout2.setContentsMargins(0, 0, 0, 0)  # Remove margins
        button_layout2.addWidget(self.cancel_btn)
        
        self.button_stack.addWidget(button_container1)
        self.button_stack.addWidget(button_container2)
        
        control_layout.addWidget(self.button_stack)
        layout.addLayout(control_layout)
        layout.addStretch()
        
        return panel
    
    def setup_connections(self):
        """Setup signal-slot connections"""
        # Button connections
        self.add_btn.clicked.connect(self.add_row)
        self.edit_btn.clicked.connect(self.edit_row)
        self.delete_btn.clicked.connect(self.delete_row)
        self.load_btn.clicked.connect(self.load_data)
        self.save_btn.clicked.connect(self.save_data)
        self.start_btn.clicked.connect(self.start_generation)
        self.cancel_btn.clicked.connect(self.cancel_generation)
        
        # Table double-click and button connections
        self.settings_table.doubleClicked.connect(self.edit_row)
        self.settings_table.cellWidget = self.handle_table_button_click
    
    def handle_table_button_click(self, row: int, column: int):
        """Handle clicks on table buttons"""
        if column == TableColumns.REGENERATE_BTN.value:
            self.handle_regenerate(row)
        elif column == TableColumns.REUPLOAD_BTN.value:
            self.handle_reupload(row)
    
    def handle_regenerate(self, row: int):
        """Handle regeneration of a specific row"""
        row_data = self.table_manager.get_row_data(row)
        if not row_data or not row_data.needs_regeneration:
            return
        
        # Reset flags and status
        row_data.needs_regeneration = False
        row_data.needs_reupload = False
        row_data.saved_description = ''
        self.table_manager.update_row(row, row_data)
        
        # Start generation for this row
        self.current_index = row
        self.generation_data = [row_data.__dict__]
        self.start_item_generation(self.generation_data[0])
    
    def handle_reupload(self, row: int):
        """Handle reupload of a specific row"""
        row_data = self.table_manager.get_row_data(row)
        if not row_data or not row_data.needs_reupload or not row_data.saved_description:
            return
        
        # Check if YouTube upload is enabled for this row
        if not row_data.youtube_upload_enabled:
            self.logger.warning(f"Cannot reupload row {row + 1}: YouTube upload is disabled")
            return
        
        # Reset reupload flag but keep the saved description
        row_data.needs_reupload = False
        self.table_manager.update_row(row, row_data)
        
        # Start upload for this row
        self.current_index = row
        self.generation_data = [row_data.__dict__]
        self.start_item_upload(row_data.saved_description)
    
    def setup_timer_based_logging(self):
        """Setup timer-based logging with queue"""
        self.log_timer = QTimer()
        self.log_timer.timeout.connect(self.process_log_queue)
        self.log_timer.start(100)  # Check every 100ms
        
        self.log_message_queue = queue.Queue()
        
        # Create and add queue handler
        class QueueLogHandler(logging.Handler):
            def __init__(self, message_queue):
                super().__init__()
                self.message_queue = message_queue
                self.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
            
            def emit(self, record):
                try:
                    msg = self.format(record)
                    self.message_queue.put_nowait(msg)
                except queue.Full:
                    pass  # Skip if queue is full
                except Exception:
                    pass
        
        self.queue_handler = QueueLogHandler(self.log_message_queue)
        self.logger.addHandler(self.queue_handler)
    
    def process_log_queue(self):
        """Process messages from the log queue"""
        try:
            for _ in range(10):  # Process up to 10 messages per tick
                message = self.log_message_queue.get_nowait()
                self.update_log(message)
        except queue.Empty:
            pass
        except Exception:
            pass
    
    def update_log(self, message: str):
        """Thread-safe log update"""
        try:
            app_instance = QApplication.instance()
            if app_instance and QThread.currentThread() != app_instance.thread():
                QMetaObject.invokeMethod(
                    self, "_update_log_ui",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, message)
                )
            else:
                self._update_log_ui(message)
        except Exception:
            pass
    
    @pyqtSlot(str)
    def _update_log_ui(self, message: str):
        """Update the log window UI"""
        try:
            self.log_window.append(message)
            
            # Limit log size
            document = self.log_window.document()
            if document and document.lineCount() > 1000:
                cursor = self.log_window.textCursor()
                cursor.movePosition(cursor.Start)
                cursor.movePosition(cursor.Down, cursor.KeepAnchor, 100)
                cursor.removeSelectedText()
            
            # Auto-scroll
            scrollbar = self.log_window.verticalScrollBar()
            if scrollbar:
                scrollbar.setValue(scrollbar.maximum())
        except Exception:
            pass
    
    def add_row(self):
        """Add a new row to the table"""
        dialog = SettingsDialog(self, accounts=self.account_manager.get_accounts_list())
        if dialog.exec_() == QDialog.Accepted:
            data = dialog.get_data()
            # Convert boolean fields to proper types for RowData
            data['needs_regeneration'] = False
            data['needs_reupload'] = False
            self.table_manager.add_row(RowData(**data))
    
    def edit_row(self):
        """Edit the selected row"""
        current_row = self.settings_table.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "Warning", "Please select a row to edit.")
            return
        
        row_data = self.table_manager.get_row_data(current_row)
        if not row_data:
            return
        
        dialog = SettingsDialog(self, row_data.__dict__, accounts=self.account_manager.get_accounts_list())
        if dialog.exec_() == QDialog.Accepted:
            if not dialog.video_title_edit.text().strip():
                QMessageBox.warning(self, "Input Error", "Video title cannot be empty.")
                self.edit_row()
                return
            data = dialog.get_data()
            # Preserve existing boolean flags
            data['needs_regeneration'] = row_data.needs_regeneration
            data['needs_reupload'] = row_data.needs_reupload
            data['saved_description'] = row_data.saved_description
            self.table_manager.update_row(current_row, RowData(**data))
    
    def delete_row(self):
        """Delete the selected row"""
        current_row = self.settings_table.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "Warning", "Please select a row to delete.")
            return
        
        if QMessageBox.question(
            self, "Confirm Delete",
            "Are you sure you want to delete this row?",
            QMessageBox.Yes | QMessageBox.No
        ) == QMessageBox.Yes:
            self.settings_table.removeRow(current_row)
    
    def load_data(self):
        """Load data from file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Load Data", "", "Excel Files (*.xlsx);;CSV Files (*.csv)")
        
        if not file_path:
            return
        
        try:
            # Read file with pandas
            df = pd.read_excel(file_path) if file_path.endswith('.xlsx') else pd.read_csv(file_path)
            
            # Replace NaN values with empty strings
            df = df.replace({np.nan: ''})
            
            self.table_manager.clear()
            
            # Process each row
            for _, row in df.iterrows():
                try:
                    data = RowData(
                        video_title=str(row.get('video_title', '')).strip(),
                        preset_path=str(row.get('preset_path', '')).strip(),
                        youtube_upload_enabled=bool(row.get('youtube_upload_enabled', False)),
                        channel_name=str(row.get('channel_name', '')).strip(),
                        account=str(row.get('account', '')).strip(),
                        category=str(row.get('category', '24')).strip(),
                        schedule=str(row.get('schedule', '')).strip(),
                        needs_regeneration=bool(row.get('needs_regeneration', False)),
                        needs_reupload=bool(row.get('needs_reupload', False)),
                        saved_description=str(row.get('saved_description', ''))
                    )
                    self.table_manager.add_row(data)
                except Exception as row_error:
                    self.logger.warning(f"Failed to process row: {row_error}")
                    continue
            
            self.logger.info(f"Successfully loaded {len(df)} rows from {file_path}")
            
        except Exception as e:
            self.logger.error(f"Failed to load data: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to load data: {str(e)}")
    
    def save_data(self):
        """Save data to file"""
        if self.settings_table.rowCount() == 0:
            QMessageBox.warning(self, "Warning", "No data to save.")
            return
        
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Data", "", "Excel Files (*.xlsx);;CSV Files (*.csv)")
        
        if not file_path:
            return
        
        try:
            data = []
            for row_data in self.table_manager.get_all_data():
                if row_data:
                    # Save essential data and flags
                    data.append({
                        'video_title': row_data.video_title,
                        'preset_path': row_data.preset_path,
                        'youtube_upload_enabled': row_data.youtube_upload_enabled,
                        'channel_name': row_data.channel_name,
                        'account': row_data.account,
                        'category': row_data.category,
                        'schedule': row_data.schedule,
                        'needs_regeneration': row_data.needs_regeneration,
                        'needs_reupload': row_data.needs_reupload,
                        'saved_description': row_data.saved_description
                    })
            
            if not data:
                QMessageBox.warning(self, "Warning", "No valid data to save.")
                return
            
            df = pd.DataFrame(data)
            
            if file_path.endswith('.xlsx'):
                df.to_excel(file_path, index=False)
            else:
                df.to_csv(file_path, index=False)
            
            self.logger.info(f"Successfully saved {len(data)} rows to {file_path}")
            
        except Exception as e:
            self.logger.error(f"Failed to save data: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to save data: {str(e)}")
    
    def start_generation(self):
        """Start the bulk generation process"""
        if self.settings_table.rowCount() == 0:
            QMessageBox.warning(self, "Warning", "No data to generate.")
            return
        
        # Collect generation data
        self.generation_data = []
        for row in range(self.settings_table.rowCount()):
            row_data = self.table_manager.get_row_data(row)
            if row_data:
                data = row_data.__dict__
                
                # Check if YouTube upload is enabled for this row
                if data.get('youtube_upload_enabled', False):
                    # Get credentials for YouTube upload
                    credentials = self.account_manager.get_account_credentials(data['account'])
                    if not credentials:
                        QMessageBox.warning(
                            self, 
                            "YouTube Authentication Required", 
                            f"Please load YouTube credentials for account '{data['account']}' before starting generation."
                        )
                        return
                    data['credentials'] = credentials
                else:
                    # Check if channel name is provided when YouTube upload is disabled
                    if not data.get('channel_name', '').strip():
                        QMessageBox.warning(
                            self, 
                            "Channel Name Required", 
                            f"Please enter a channel name for row {row + 1} when YouTube upload is disabled."
                        )
                        return
                    data['credentials'] = None
                
                self.generation_data.append(data)
        
        if not self.generation_data:
            QMessageBox.warning(self, "Warning", "No valid data to generate.")
            return
        
        # Reset state
        self.current_index = 0
        self.is_cancelled = False
        
        # Switch to cancel button and reset progress
        self.button_stack.setCurrentIndex(1)
        self.generation_progress.setValue(0)
        self.upload_progress.setValue(0)
        
        # Start processing first item
        self.process_next_item()
        self.logger.info(f"Started bulk generation for {len(self.generation_data)} items")
    
    def process_next_item(self):
        """Process the next item in the queue with enhanced error recovery"""
        try:
            with QMutexLocker(self.mutex):
                if self.is_cancelled:
                    self.finish_generation("Bulk generation cancelled by user")
                    return
                
                if self.current_index >= len(self.generation_data):
                    self.finish_generation("Bulk generation completed")
                    return
                
                # Clean up between items to prevent memory issues
                self.cleanup_between_items()
                
                item = self.generation_data[self.current_index]
                self.update_row_status(self.current_index, "Validating", "0%", "0%")
                
                if not self.validate_item(item):
                    self.handle_item_error("Validation failed")
                    return
                
                self.start_item_generation(item)
                
        except Exception as e:
            self.logger.error(f"Error in process_next_item: {e}")
            self.handle_item_error(f"Processing error: {str(e)}")
    
    def cleanup_between_items(self):
        """Clean up resources between processing items"""
        try:
            # Ensure all workers are properly cleaned up
            self.safe_worker_cleanup(self.generation_worker)
            self.safe_worker_cleanup(self.upload_thread)
            
            # Force garbage collection
            gc.collect()
            
            # Clean up any remaining temporary files
            cleanup_all_temp_dirs()
            
            # Reset worker references
            self.generation_worker = None
            self.upload_thread = None
            
            # Small delay to allow system to stabilize
            time.sleep(0.2)
            
        except Exception as e:
            self.logger.warning(f"Error during cleanup: {e}")
    
    def safe_worker_cleanup(self, worker):
        """Safely clean up worker threads with enhanced error handling"""
        if worker:
            try:
                # Check if worker is still running
                if worker.isRunning():
                    # First try graceful cancellation
                    worker.cancel()
                    
                    # Wait with timeout
                    if not worker.wait(5000):
                        self.logger.warning("Worker did not stop gracefully, forcing termination")
                        worker.terminate()
                        worker.wait(2000)
                
                # Ensure thread is finished before cleanup
                if worker.isRunning():
                    self.logger.warning("Worker still running after cleanup attempts, forcing termination")
                    worker.terminate()
                    worker.wait(3000)
                
                # Only delete if thread is not running
                if not worker.isRunning():
                    worker.deleteLater()
                else:
                    self.logger.error("Worker could not be stopped, skipping deleteLater")
                
                worker = None
                
            except Exception as e:
                self.logger.error(f"Error cleaning up worker: {e}")
                # Force cleanup
                try:
                    if worker and worker.isRunning():
                        worker.terminate()
                        worker.wait(1000)
                    if worker and not worker.isRunning():
                        worker.deleteLater()
                except Exception:
                    pass
                finally:
                    worker = None
    
    def validate_item(self, item):
        """Validate a single item"""
        if not os.path.exists(item['preset_path']):
            self.update_status(f"Preset file not found: {item['preset_path']}")
            return False
        
        # Check YouTube upload requirements
        if item.get('youtube_upload_enabled', False):
            if not item.get('account', '').strip():
                self.update_status("Account name is required when YouTube upload is enabled")
                return False
        else:
            if not item.get('channel_name', '').strip():
                self.update_status("Channel name is required when YouTube upload is disabled")
                return False
        
        if not validate_preset_content(item['preset_path']):
            self.update_status(f"Invalid preset content: {item['preset_path']}")
            return False
        
        return True
    
    def start_item_generation(self, item):
        """Start generation for an item"""
        try:
            # Ensure previous worker is cleaned up
            self.safe_worker_cleanup(self.generation_worker)
            
            with open(item['preset_path'], 'r') as f:
                preset = json.load(f)
            
            # Prepare generation parameters
            params = self.prepare_generation_params(item, preset)
            
            # Create and start generation worker
            self.generation_worker = GenerationWorker(**params)
            self.generation_worker.progress_update.connect(lambda p: self.on_generation_progress(p))
            self.generation_worker.operation_update.connect(lambda op: self.on_generation_operation(op))
            self.generation_worker.generation_finished.connect(lambda desc: self.on_generation_finished(desc))
            self.generation_worker.error_occurred.connect(lambda err: self.on_generation_error(err))
            
            # Set parent to ensure proper cleanup
            self.generation_worker.setParent(self)
            
            self.update_row_status(self.current_index, "Processing", "0%", "0%")
            self.generation_worker.start()
            
        except Exception as e:
            self.handle_item_error(f"Failed to start generation: {str(e)}")
    
    def prepare_generation_params(self, item, preset):
        """Prepare parameters for generation worker"""
        # Replace variables in prompts
        prompts = {
            'thumbnail_prompt': preset['thumbnail_prompt'],
            'intro_prompt': preset['intro_prompt'],
            'looping_prompt': preset['looping_prompt'],
            'outro_prompt': preset['outro_prompt'],
            'images_prompt': preset['images_prompt']
        }
        
        for key, prompt in prompts.items():
            prompt = prompt.replace('$title', item['video_title'])
            if 'prompt_variables' in preset:
                for var_key, var_value in preset['prompt_variables'].items():
                    prompt = prompt.replace(f"${var_key}", var_value)
            prompts[key] = prompt
        
        # Get runware models and loras from preset
        thumbnail_model = preset.get('thumbnail_model', 'runware:100@1')
        thumbnail_loras = preset.get('thumbnail_loras', [])
        image_model = preset.get('image_model', 'runware:100@1')
        image_loras = preset.get('image_loras', [])
        
        # Get language and voice settings from preset
        language = preset.get('language', 'a')  # Default to American English
        voice = preset.get('voice', 'am_michael')  # Default to Michael voice
        
        # Get channel name from item
        channel_name = item.get('channel_name', '')
        if not channel_name:
            # Fallback to account name if channel name is not provided
            channel_name = item.get('account', 'default')
        if not channel_name:
            channel_name = 'default'
        
        # Use proper title sanitization instead of simple space replacement
        from utils import title_to_safe_folder_name
        safe_video_title = title_to_safe_folder_name(item['video_title'])
        
        return {
            'api_key': preset['api_key'],
            'video_title': safe_video_title,
            'background_music_path': preset.get('background_music', ''),
            'thumbnail_prompt': prompts['thumbnail_prompt'],
            'images_prompt': prompts['images_prompt'],
            'intro_prompt': prompts['intro_prompt'],
            'looping_prompt': prompts['looping_prompt'],
            'outro_prompt': prompts['outro_prompt'],
            'loop_length': preset['loop_length'],
            'word_limit': preset['audio_word_limit'],
            'image_count': preset['image_count'],
            'image_word_limit': preset['image_word_limit'],
            'runware_model': thumbnail_model,
            'runware_loras': thumbnail_loras,
            'image_model': image_model,
            'image_loras': image_loras,
            'language': language,
            'voice': voice,
            'channel_name': channel_name,
            'logger': self.logger
        }
    
    def start_item_upload(self, description):
        """Start upload for current item"""
        try:
            item = self.generation_data[self.current_index]
            with open(item['preset_path'], 'r') as f:
                preset = json.load(f)
            
            # Use the new folder structure: ./output/{channel name}/{video title}
            # Use channel_name if YouTube upload is disabled, otherwise use account name
            if item.get('youtube_upload_enabled', False):
                channel_name = item.get('account', 'default')
            else:
                channel_name = item.get('channel_name', 'default')
            
            if not channel_name:
                channel_name = 'default'
            
            # Determine the base directory (same level as exe file)
            if getattr(sys, 'frozen', False):
                # Running as PyInstaller executable - use directory containing the executable
                base_dir = os.path.dirname(sys.executable)
            else:
                # Running as script - use directory containing the script
                base_dir = os.path.dirname(os.path.abspath(__file__))
            
            # Create the structured path: ./output/{channel name}/{video title}
            # Use create_output_directory to get the correct path with proper sanitization
            from utils import create_output_directory
            video_dir = create_output_directory(item['video_title'], channel_name)
            
            video_path = os.path.join(video_dir, "final_slideshow_with_audio.mp4")
            thumbnail_path = os.path.join(video_dir, "thumbnail.jpg")
            
            # Prepare upload parameters
            upload_params = {
                'credentials': item['credentials'],
                'video_path': video_path,
                'title': item['video_title'][:80],
                'description': description + "\n\n" + preset['disclaimer'],
                'category': item['category'],
                'tags': "",
                'privacy_status': "public",
                'thumbnail_path': thumbnail_path,
                'made_for_kids': False
            }
            
            # Handle scheduling
            if item['schedule']:
                publish_at = datetime.datetime.fromisoformat(item['schedule'])
                local_timezone = datetime.datetime.now(datetime.timezone.utc).astimezone().tzinfo
                aware_local_time = publish_at.replace(tzinfo=local_timezone)
                upload_params['publish_at'] = aware_local_time.astimezone(pytz.UTC)
            
            # Ensure previous upload thread is cleaned up
            self.safe_worker_cleanup(self.upload_thread)
            
            # Create and start upload thread
            self.upload_thread = UploadThread(**upload_params)
            self.upload_thread.progress_signal.connect(self.on_upload_progress)
            self.upload_thread.status_signal.connect(self.on_upload_status)
            self.upload_thread.finished_signal.connect(self.on_upload_finished)
            self.upload_thread.error_signal.connect(self.on_upload_error)
            
            # Set parent to ensure proper cleanup
            self.upload_thread.setParent(self)
            
            self.update_row_status(self.current_index, "Uploading", "100%", "0%")
            self.upload_thread.start()
            
        except Exception as e:
            self.handle_item_error(f"Failed to start upload: {str(e)}")
    
    def handle_item_error(self, error_message):
        """Handle error for current item"""
        self.logger.error(f"Error for item {self.current_index + 1}: {error_message}")
        self.update_row_status(self.current_index, "Error", "Error", "Error")
        
        # Update status labels to show error
        self.generation_status.setText(f"Error on item {self.current_index + 1}: {error_message}")
        
        # Reset progress bars for the errored item
        self.generation_progress.setValue(int((self.current_index) / len(self.generation_data) * 100))
        self.upload_progress.setValue(0)
        
        self.current_index += 1
        QTimer.singleShot(100, self.process_next_item)
    
    def finish_generation(self, message):
        """Finish the generation process"""
        self.logger.info(message)
        self.generation_status.setText("Generation completed")
        self.upload_status.setText("Upload completed")
        # Ensure progress bar shows 100% at completion
        self.generation_progress.setValue(100)
        self.reset_generation_ui()
    
    # Signal handlers
    def on_generation_progress(self, progress):
        """Handle generation progress update"""
        self.update_row_status(self.current_index, "Processing", f"{progress}%", "0%")
        # Calculate total generation progress across all items
        # Each item contributes equally to the total progress
        if self.current_index == len(self.generation_data) - 1 and progress == 100:
            # If this is the last item and it's at 100%, set total to 100%
            total_progress = 100
        else:
            # Otherwise calculate based on current progress
            total_progress = int((self.current_index + (progress/100)) / len(self.generation_data) * 100)
        self.generation_progress.setValue(total_progress)
    
    def on_generation_operation(self, operation):
        """Handle generation operation update"""
        self.update_status(f"[{self.current_index + 1}/{len(self.generation_data)}] {operation}")
    
    def on_generation_finished(self, description):
        """Handle generation completion"""
        try:
            if description is None:
                self.logger.error("Generation finished with no description")
                self.handle_item_error("Generation failed: No description provided")
                return
            
            # Save the description for potential reupload
            row_data = self.table_manager.get_row_data(self.current_index)
            if row_data:
                row_data.saved_description = description
                self.table_manager.update_row(self.current_index, row_data)
            
            # Check if YouTube upload is enabled for this item
            if self.current_index < len(self.generation_data):
                item = self.generation_data[self.current_index]
                if item.get('youtube_upload_enabled', False):
                    # YouTube upload is enabled, start upload
                    QTimer.singleShot(1000, lambda: self.safely_start_upload(description))
                else:
                    # YouTube upload is disabled, mark as completed and move to next item
                    self.update_row_status(self.current_index, "Completed", "100%", "N/A")
                    self.logger.info(f"Generation completed for item {self.current_index + 1} (no upload - YouTube upload disabled)")
                    self.current_index += 1
                    QTimer.singleShot(100, self.process_next_item)
            else:
                # Fallback: move to next item
                self.current_index += 1
                QTimer.singleShot(100, self.process_next_item)
        except Exception as e:
            self.logger.error(f"Error in generation completion handler: {str(e)}")
            self.handle_item_error(f"Generation completion error: {str(e)}")
    
    def safely_start_upload(self, description):
        """Safely start the upload process with error handling"""
        try:
            self.start_item_upload(description)
        except Exception as e:
            self.logger.error(f"Error starting upload: {str(e)}")
            self.handle_item_error(f"Upload start error: {str(e)}")
    
    def on_generation_error(self, error_message):
        """Handle generation error"""
        # Set regeneration flag
        if self.current_index < len(self.generation_data):
            row_data = self.table_manager.get_row_data(self.current_index)
            if row_data:
                row_data.needs_regeneration = True
                row_data.needs_reupload = False
                row_data.saved_description = ''
                self.table_manager.update_row(self.current_index, row_data)
        
        # Clean up the current worker with better error handling
        self.safe_worker_cleanup(self.generation_worker)
        
        # Force garbage collection to free up resources
        gc.collect()
        
        # Log detailed error information
        self.logger.error("=" * 50)
        self.logger.error("GENERATION ERROR OCCURRED")
        self.logger.error(f"Item {self.current_index + 1}: {error_message}")
        if "timeout" in error_message.lower():
            self.logger.error("This appears to be a timeout error. Consider:")
            self.logger.error("1. Reducing video complexity (fewer images, shorter duration)")
            self.logger.error("2. Checking system resources (CPU/Memory)")
            self.logger.error("3. Ensuring FFmpeg is properly installed")
        elif "ffmpeg" in error_message.lower():
            self.logger.error("This appears to be an FFmpeg-related error. Consider:")
            self.logger.error("1. Checking if FFmpeg is properly installed and in PATH")
            self.logger.error("2. Verifying FFmpeg has sufficient permissions")
            self.logger.error("3. Checking available disk space for temporary files")
        elif "process" in error_message.lower() or "subprocess" in error_message.lower():
            self.logger.error("This appears to be a process-related error. Consider:")
            self.logger.error("1. Closing other resource-intensive applications")
            self.logger.error("2. Restarting the application if the issue persists")
            self.logger.error("3. Checking system stability and available resources")
        self.logger.error("=" * 50)
        
        self.handle_item_error(f"Generation failed: {error_message}")
    
    def on_upload_progress(self, progress):
        """Handle upload progress update"""
        # Update UI elements without logging
        self.update_row_status(self.current_index, "Uploading", "100%", f"{progress}%", log_progress=False)
        # Show upload progress directly (0-100%) for current item
        self.upload_progress.setValue(progress)
    
    def on_upload_status(self, status_message):
        """Handle upload status messages for logging"""
        # Update both the log window and the upload status label
        self.logger.info(f"Upload: {status_message}")
        self.upload_status.setText(status_message)
        
        # If this is an error message, highlight it in the log
        if any(error_term in status_message.lower() for error_term in ['error', 'failed', 'denied', 'invalid']):
            self.logger.error(f"Upload Error Detail: {status_message}")
    
    def on_upload_finished(self, url, video_id):
        """Handle upload completion"""
        self.update_row_status(self.current_index, "Completed", "100%", "100%")
        self.logger.info(f"Upload completed successfully. Video URL: {url}")
        self.upload_status.setText(f"Upload completed. Video ID: {video_id}")
        
        # Store the video URL in the table
        url_item = self.settings_table.item(self.current_index, TableColumns.VIDEO_URL.value)
        if url_item:
            url_item.setText(url)
        
        self.current_index += 1
        QTimer.singleShot(100, self.process_next_item)
    
    def on_upload_error(self, error_message):
        """Handle upload error"""
        # Set reupload flag
        if self.current_index < len(self.generation_data):
            row_data = self.table_manager.get_row_data(self.current_index)
            if row_data:
                row_data.needs_regeneration = False
                row_data.needs_reupload = True
                self.table_manager.update_row(self.current_index, row_data)
        
        # Clean up the current upload thread
        self.safe_worker_cleanup(self.upload_thread)
        
        # Log the error with high visibility
        self.logger.error("=" * 50)
        self.logger.error("UPLOAD ERROR OCCURRED")
        self.logger.error(error_message)
        self.logger.error("=" * 50)
        
        # Update UI to show error state
        self.upload_status.setText(f"Error: {error_message}")
        
        # Update row status with error indication
        self.update_row_status(self.current_index, "Upload Error", "100%", "Error")
        
        self.current_index += 1
        QTimer.singleShot(100, self.process_next_item)
    
    def cancel_generation(self):
        """Cancel the ongoing generation"""
        with QMutexLocker(self.mutex):
            self.is_cancelled = True
            self.logger.info("Cancellation requested...")
            
            # Cancel generation worker
            if self.generation_worker and self.generation_worker.isRunning():
                self.safe_worker_cleanup(self.generation_worker)
            
            # Cancel upload thread  
            if self.upload_thread and self.upload_thread.isRunning():
                self.safe_worker_cleanup(self.upload_thread)
            
            # Reset UI state
            self.reset_generation_ui()
            self.logger.info("Cancellation completed")
    
    def reset_generation_ui(self):
        """Reset UI after generation"""
        # Switch back to start button
        self.button_stack.setCurrentIndex(0)
        
        # Clean up workers
        if self.generation_worker:
            self.generation_worker.cancel()
            self.generation_worker.wait(5000)  # Wait up to 5 seconds
            self.generation_worker.deleteLater()
            self.generation_worker = None
            
        if self.upload_thread:
            self.upload_thread.cancel()
            self.upload_thread.wait(5000)  # Wait up to 5 seconds
            self.upload_thread.deleteLater()
            self.upload_thread = None
        
        self.generation_data = []
        self.current_index = 0
        self.is_cancelled = False
    
    def update_status(self, message: str):
        """Update the status label"""
        if "upload" in message.lower():
            self.upload_status.setText(message)
        else:
            self.generation_status.setText(message)
        self.logger.info(message)
    
    def update_row_status(self, row: int, status: str, gen_progress: str, upload_progress: str, log_progress: bool = True):
        """Update row status and progress"""
        # Only pass log_progress=False for upload percentage updates
        self.table_manager.update_row_status(row, status, gen_progress, upload_progress, log_progress)
        
        # Update upload progress bar if we're in upload phase
        if status == "Uploading" and upload_progress is not None:
            try:
                progress_value = int(upload_progress.rstrip('%'))
                self.upload_progress.setValue(progress_value)
            except ValueError:
                pass
    
    def closeEvent(self, event):
        """Clean up when closing the application"""
        try:
            # Stop timer first
            if hasattr(self, 'log_timer'):
                self.log_timer.stop()
                self.log_timer.deleteLater()
            
            # Clean up logger handlers
            if hasattr(self, 'logger'):
                for handler in self.logger.handlers[:]:
                    try:
                        handler.close()
                        self.logger.removeHandler(handler)
                    except Exception:
                        pass
            
            # Safely clean up workers
            self.safe_worker_cleanup(self.generation_worker)
            self.safe_worker_cleanup(self.upload_thread)
            
            # Clean up all temporary directories
            cleanup_all_temp_dirs()
            
            # Force garbage collection
            gc.collect()
            
            # Small delay to ensure cleanup completes
            time.sleep(0.5)
            
        except Exception as e:
            print(f"Error during cleanup: {str(e)}")
        
        event.accept()

def main():
    app = QApplication(sys.argv)
    app.setStyle(QStyleFactory.create('Fusion'))
    
    # Set up dark theme palette
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(53, 53, 53))
    palette.setColor(QPalette.WindowText, QColor(255, 255, 255))
    palette.setColor(QPalette.Base, QColor(25, 25, 25))
    palette.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
    palette.setColor(QPalette.ToolTipBase, QColor(255, 255, 255))
    palette.setColor(QPalette.ToolTipText, QColor(255, 255, 255))
    palette.setColor(QPalette.Text, QColor(255, 255, 255))
    palette.setColor(QPalette.Button, QColor(53, 53, 53))
    palette.setColor(QPalette.ButtonText, QColor(255, 255, 255))
    palette.setColor(QPalette.BrightText, QColor(255, 0, 0))
    palette.setColor(QPalette.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.HighlightedText, QColor(0, 0, 0))
    app.setPalette(palette)
    
    window = BulkGenerationApp()
    window.show()

    sys.exit(app.exec_())

if __name__ == "__main__":
    main()