import os
import sys
import json
import queue
import datetime
import pytz
import logging
from pathlib import Path
import pickle

# PyQt imports
from PyQt5.QtGui import QFont, QPalette, QColor
from PyQt5.QtCore import (
    Qt, QDateTime, QThread, pyqtSlot, Q_ARG, QTimer
)
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QProgressBar, QFileDialog,
    QGroupBox, QSpinBox, QGridLayout, QSplitter, QSpacerItem, QSizePolicy,
    QMessageBox, QTabWidget, QScrollArea, QStyleFactory,
    QCheckBox, QDateTimeEdit, QDialog, QDoubleSpinBox, QComboBox
)

# Local imports
import log
from utils import get_default_settings, title_to_safe_folder_name
from worker import GenerationWorker
from accounts import AccountManagerDialog, AccountManager
from uploader import UploadThread
from variables import VariableDialog

# Set up base directory and change working directory
BASE_DIR = Path(os.path.dirname(os.path.abspath(sys.argv[0])))
os.chdir(BASE_DIR)

# Constants
DEFAULT_WINDOW_SIZE = (1200, 800)
MIN_WINDOW_SIZE = (900, 700)
GENERATE_BUTTON_HEIGHT = 50
PROGRESS_BAR_HEIGHT = 25

class VideoGeneratorApp(QMainWindow):
    # Constants
    MAX_LORAS = 5

    def __init__(self):
        super().__init__()
        self.logger, _ = log.setup_logger()
        self.setup_style()
        self.init_ui()
        self.setup_timer_based_logging()
        self.setup_state()

    def setup_state(self):
        """Initialize application state"""
        self.selected_channel = None
        self.variables = {}
        self.workflow_file = None
        self.credentials = None
        self.video_title = None
        self.current_generation_worker = None
        self.current_upload_thread = None

    def setup_style(self):
        """Setup application style and color scheme"""
        self.setStyle(QStyleFactory.create("Fusion"))
        
        # Set application-wide stylesheet for better disabled state visibility
        self.setStyleSheet("""
            QLineEdit:disabled, QTextEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {
                background-color: #2a2a2a;
                color: #888888;
                border: 1px solid #444444;
            }
            
            QPushButton:disabled {
                background-color: #3a3a3a !important;
                color: #888888 !important;
                border: 1px solid #555555 !important;
            }
            
            QPushButton:disabled:hover {
                background-color: #3a3a3a !important;
                color: #888888 !important;
            }
            
            QCheckBox:disabled {
                color: #888888;
            }
            
            QDateTimeEdit:disabled {
                background-color: #2a2a2a;
                color: #888888;
                border: 1px solid #444444;
            }
        """)

    def init_ui(self):
        """Initialize the main UI components"""
        self.setWindowTitle('AI Video Generator')
        self.setGeometry(100, 100, *DEFAULT_WINDOW_SIZE)
        self.setMinimumSize(*MIN_WINDOW_SIZE)
        
        # Initialize account manager
        self.init_account_manager()
        
        # Set up main layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        
        # Create and configure splitter
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)
        
        # Add left and right panels to splitter
        splitter.addWidget(self.create_left_panel())
        splitter.addWidget(self.create_right_panel())
        splitter.setSizes([800, 400])

    def init_account_manager(self):
        """Initialize the account manager"""
        self.account_manager = AccountManager(
            accounts_file=BASE_DIR / 'accounts.json',
            client_secrets_file=BASE_DIR / 'google_auth.json',
            logger=self.logger
        )

    def create_left_panel(self):
        """Create the left panel with tabs"""
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # Create and setup tab widget
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabPosition(QTabWidget.North)
        self.tab_widget.setDocumentMode(True)

        # Add tabs
        self.setup_general_tab()
        self.setup_prompts_tab()
        self.setup_settings_tab()
        self.setup_youtube_tab()

        left_layout.addWidget(self.tab_widget)
        left_layout.addWidget(self.create_generate_button())
        
        return left_panel

    def create_generate_button(self):
        """Create the generate button container"""
        container = QWidget()
        layout = QVBoxLayout(container)
        
        self.generate_btn = QPushButton("GENERATE VIDEO")
        self.generate_btn.setFont(QFont("Arial", 12, QFont.Bold))
        self.generate_btn.setFixedHeight(GENERATE_BUTTON_HEIGHT)
        self.generate_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border-radius: 4px;
                border: none;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:pressed {
                background-color: #3d8b40;
            }
            QPushButton:disabled {
                background-color: #3a3a3a;
                color: #888888;
                border: 1px solid #555555;
            }
        """)
        self.generate_btn.clicked.connect(self.start_generation)
        
        layout.addWidget(self.generate_btn)
        return container

    def create_right_panel(self):
        """Create the right panel with progress and logs"""
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        # Add progress groups
        right_layout.addWidget(self.create_progress_group())
        right_layout.addWidget(self.create_upload_progress_group())
        right_layout.addWidget(self.create_log_group())
        
        return right_panel

    def create_progress_group(self):
        """Create the main progress group"""
        group = self.create_group_box("Progress")
        layout = QVBoxLayout()
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                border: 1px solid #bbb;
                border-radius: 4px;
                text-align: center;
                height: {PROGRESS_BAR_HEIGHT}px;
            }}
            QProgressBar::chunk {{
                background-color: #4CAF50;
            }}
        """)
        layout.addWidget(self.progress_bar)
        
        self.current_operation_label = QLabel("Ready")
        self.current_operation_label.setAlignment(Qt.AlignCenter)
        self.current_operation_label.setStyleSheet("font-weight: bold; color: #4CAF50;")
        layout.addWidget(self.current_operation_label)
        
        group.setLayout(layout)
        return group

    def create_upload_progress_group(self):
        """Create the upload progress group"""
        group = self.create_group_box("Upload Progress")
        layout = QVBoxLayout()
        
        self.youtube_upload_progress_bar = QProgressBar()
        self.youtube_upload_progress_bar.setRange(0, 100)
        self.youtube_upload_progress_bar.setValue(0)
        self.youtube_upload_progress_bar.setStyleSheet(f"""
            QProgressBar {{
                border: 1px solid #bbb;
                border-radius: 4px;
                text-align: center;
                height: {PROGRESS_BAR_HEIGHT}px;
            }}
            QProgressBar::chunk {{
                background-color: #4CAF50;
            }}
        """)
        layout.addWidget(self.youtube_upload_progress_bar)
        
        self.youtube_status_label = QLabel("Status: Ready")
        layout.addWidget(self.youtube_status_label)
        
        self.result_url = QLineEdit()
        self.result_url.setReadOnly(True)
        self.result_url.setPlaceholderText("Video URL will appear here after upload")
        layout.addWidget(self.result_url)
        
        group.setLayout(layout)
        return group

    def create_log_group(self):
        """Create the log group"""
        group = self.create_group_box("Log")
        layout = QVBoxLayout()
        
        self.log_window = QTextEdit()
        self.log_window.setReadOnly(True)
        self.log_window.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #f0f0f0;
                border: 1px solid #444;
                border-radius: 4px;
                font-family: Consolas, monospace;
            }
        """)
        layout.addWidget(self.log_window)
        
        self.clear_log_btn = QPushButton("Clear Log")
        self.clear_log_btn.setStyleSheet("""
            QPushButton {
                background-color: #555;
                color: white;
                border-radius: 4px;
                padding: 5px;
            }
            QPushButton:hover {
                background-color: #666;
            }
            QPushButton:pressed {
                background-color: #444;
            }
        """)
        self.clear_log_btn.clicked.connect(self.clear_log)
        layout.addWidget(self.clear_log_btn)
        
        group.setLayout(layout)
        return group

    def setup_timer_based_logging(self):
        """Alternative approach using QTimer for even safer logging"""
        # Create a timer to periodically check for log messages
        self.log_timer = QTimer()
        self.log_timer.timeout.connect(self.process_log_queue)
        self.log_timer.start(100)  # Check every 100ms
        
        # Use a queue for log messages
        self.log_message_queue = queue.Queue()
        
        # Create custom handler that uses the queue
        class QueueLogHandler(logging.Handler):
            def __init__(self, message_queue):
                super().__init__()
                self.message_queue = message_queue
                formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
                self.setFormatter(formatter)
            
            def emit(self, record):
                try:
                    msg = self.format(record)
                    try:
                        self.message_queue.put_nowait(msg)
                    except queue.Full:
                        pass  # Skip if queue is full
                except Exception:
                    pass
        
        # Add the queue handler to logger
        self.queue_handler = QueueLogHandler(self.log_message_queue)
        self.logger.addHandler(self.queue_handler)
    
    def process_log_queue(self):
        """Process log messages from queue (called by timer)"""
        messages_processed = 0
        max_messages_per_update = 10  # Limit to prevent UI blocking
        
        try:
            while messages_processed < max_messages_per_update:
                try:
                    message = self.log_message_queue.get_nowait()
                    self.update_log(message)
                    messages_processed += 1
                except queue.Empty:
                    break
        except Exception:
            pass  # Ignore errors in log processing
    
    def update_log(self, message):
        """Thread-safe log update method"""
        try:
            # Make sure we're in the main thread
            if QThread.currentThread() != QApplication.instance().thread():
                # If not in main thread, use QMetaObject.invokeMethod for thread safety
                from PyQt5.QtCore import QMetaObject, Qt
                QMetaObject.invokeMethod(
                    self, 
                    "_update_log_ui", 
                    Qt.QueuedConnection,
                    Q_ARG(str, message)
                )
            else:
                self._update_log_ui(message)
        except Exception:
            pass  # Ignore UI update errors
    
    @pyqtSlot(str)
    def _update_log_ui(self, message):
        """Actually update the UI (must be called from main thread)"""
        try:
            self.log_window.append(message)
            
            # Limit the number of lines in log window to prevent memory issues
            max_lines = 1000
            if self.log_window.document().lineCount() > max_lines:
                cursor = self.log_window.textCursor()
                cursor.movePosition(cursor.Start)
                cursor.movePosition(cursor.Down, cursor.KeepAnchor, 100)  # Remove first 100 lines
                cursor.removeSelectedText()
            
            # Auto-scroll to bottom
            scrollbar = self.log_window.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
            
        except Exception:
            pass  # Ignore UI errors
    
    def closeEvent(self, event):
        """Clean up resources when closing the application"""
        try:
            # Stop the timer if using timer-based logging
            if hasattr(self, 'log_timer'):
                self.log_timer.stop()
            
            # Clean up logging handlers
            if hasattr(self, 'logger'):
                handlers = self.logger.handlers[:]
                for handler in handlers:
                    handler.close()
                    self.logger.removeHandler(handler)
            
            # Clean up worker threads
            self.cleanup_workers()

            # Clean up any active event loops (if needed in the future)
            pass

            # Clean up any active connections (if needed in the future)
            pass
            
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
        finally:
            # Ensure the event is accepted and app closes
            event.accept()

    def setup_general_tab(self):
        """Setup general tab with API key, video title, etc."""
        general_tab = QWidget()
        general_layout = QVBoxLayout(general_tab)

        # API Key Group
        api_key_group = self.create_group_box("OpenAI API Key")
        api_key_layout = QHBoxLayout()

        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setPlaceholderText("Enter your OpenAI API key")
        self.api_key_input.setStyleSheet("padding: 8px;")

        self.toggle_key_visibility_btn = QPushButton("Show")
        self.toggle_key_visibility_btn.setFixedWidth(80)
        self.toggle_key_visibility_btn.setStyleSheet("padding: 8px;")
        self.toggle_key_visibility_btn.clicked.connect(
            self.toggle_key_visibility)

        api_key_layout.addWidget(self.api_key_input)
        api_key_layout.addWidget(self.toggle_key_visibility_btn)
        api_key_group.setLayout(api_key_layout)
        general_layout.addWidget(api_key_group)

        # Video Title Group
        video_title_group = self.create_group_box("Video Settings")
        video_title_layout = QGridLayout()

        video_title_label = QLabel("Video Title:")
        self.video_title_input = QLineEdit()
        self.video_title_input.setPlaceholderText("Enter your video title")
        self.video_title_input.setStyleSheet("padding: 8px;")

        background_music_label = QLabel("Background music:")
        self.background_music_input = QLineEdit()
        self.background_music_input.setPlaceholderText("Path of background music")
        self.background_music_input.setStyleSheet("padding: 8px;")
        self.background_music_input.setReadOnly(True)
        self.load_background_music_btn = QPushButton("Load file")
        self.load_background_music_btn.setStyleSheet("padding: 8px;")
        self.load_background_music_btn.clicked.connect(self.load_background_music)

        video_title_layout.addWidget(video_title_label, 0, 0)
        video_title_layout.addWidget(self.video_title_input, 0, 1, 1, 2)
        video_title_layout.addWidget(background_music_label, 1, 0)
        video_title_layout.addWidget(self.background_music_input, 1, 1)
        video_title_layout.addWidget(self.load_background_music_btn, 1, 2)

        video_title_group.setLayout(video_title_layout)
        general_layout.addWidget(video_title_group)

        # Presets Group
        presets_group = self.create_group_box("Presets")
        presets_layout = QVBoxLayout()

        self.settings_filepath_input = QLineEdit()
        self.settings_filepath_input.setReadOnly(True)
        self.settings_filepath_input.setPlaceholderText(
            "No preset file selected")
        self.settings_filepath_input.setStyleSheet("padding: 8px;")

        presets_buttons_layout = QHBoxLayout()

        self.settings_save_button = QPushButton("Save Presets")
        self.settings_save_button.clicked.connect(self.toggle_save_settings)
        self.settings_save_button.setStyleSheet("padding: 8px;")

        self.settings_load_button = QPushButton("Load Presets")
        self.settings_load_button.clicked.connect(self.toggle_load_settings)
        self.settings_load_button.setStyleSheet("padding: 8px;")

        presets_buttons_layout.addItem(QSpacerItem(
            20, 20, QSizePolicy.Expanding, QSizePolicy.Minimum))
        presets_buttons_layout.addWidget(self.settings_save_button)
        presets_buttons_layout.addWidget(self.settings_load_button)

        presets_layout.addWidget(self.settings_filepath_input)
        presets_layout.addLayout(presets_buttons_layout)
        presets_group.setLayout(presets_layout)
        general_layout.addWidget(presets_group)

        # Add stretch to push everything up
        general_layout.addStretch()

        # Add tab
        self.tab_widget.addTab(general_tab, "General")

    def setup_prompts_tab(self):
        """Setup prompts tab with all prompt input fields"""
        prompts_tab = QScrollArea()
        prompts_tab.setWidgetResizable(True)
        prompts_content = QWidget()
        prompts_layout = QVBoxLayout(prompts_content)
        prompts_grid = QGridLayout()

        # Thumbnail Prompt Group
        thumbnail_group = self.create_group_box("Thumbnail Prompt")
        thumbnail_layout = QVBoxLayout()

        thumbnail_label = QLabel(
            "Enter prompt for generating a youtube thumbnail:")
        self.thumbnail_prompt_input = QTextEdit()
        self.thumbnail_prompt_input.setPlaceholderText(
            "For example: A vibrant, eye-catching thumbnail for a video about $title...")
        self.thumbnail_prompt_input.setMinimumHeight(80)

        thumbnail_layout.addWidget(thumbnail_label)
        thumbnail_layout.addWidget(self.thumbnail_prompt_input)
        
        # Add Runware Model field for thumbnail
        runware_model_layout = QHBoxLayout()
        runware_model_label = QLabel("Runware Model:")
        self.runware_model_input = QLineEdit()
        self.runware_model_input.setPlaceholderText("e.g., runware:100@1")
        self.runware_model_input.setText("runware:100@1")
        self.runware_model_input.setStyleSheet("padding: 5px;")
        
        runware_model_layout.addWidget(runware_model_label)
        runware_model_layout.addWidget(self.runware_model_input)
        thumbnail_layout.addLayout(runware_model_layout)
        
        # Lora section with dynamic add/remove for thumbnail
        lora_section_label = QLabel(f"Runware Loras (optional, -4 to 4 weight range, max {self.MAX_LORAS}):")
        thumbnail_layout.addWidget(lora_section_label)
        
        # Container for lora inputs
        self.lora_container = QWidget()
        self.lora_layout = QVBoxLayout(self.lora_container)
        self.lora_layout.setContentsMargins(0, 0, 0, 0)
        self.lora_layout.setSpacing(5)
        
        # List to keep track of lora input rows
        self.lora_rows = []
        
        # Add button for more loras
        add_lora_button_layout = QHBoxLayout()
        self.add_lora_button = QPushButton("Add Another Lora")
        self.add_lora_button.setStyleSheet("""
            QPushButton {
                background-color: #3d85c6;
                color: white;
                padding: 5px;
                border-radius: 3px;
                border: none;
            }
            QPushButton:hover {
                background-color: #5a9bd5;
            }
            QPushButton:pressed {
                background-color: #2a5885;
            }
            QPushButton:disabled {
                background-color: #3a3a3a;
                color: #888888;
                border: 1px solid #555555;
            }
        """)
        self.add_lora_button.clicked.connect(self.add_lora_input_row)
        add_lora_button_layout.addStretch()
        add_lora_button_layout.addWidget(self.add_lora_button)
        
        thumbnail_layout.addWidget(self.lora_container)
        thumbnail_layout.addLayout(add_lora_button_layout)
        
        # Add initial lora input row
        self.add_lora_input_row()
        
        thumbnail_group.setLayout(thumbnail_layout)
        prompts_grid.addWidget(thumbnail_group, 0, 0)

        # Images Prompt Group
        images_group = self.create_group_box("Images Prompt")
        images_layout = QVBoxLayout()

        images_label = QLabel(
            "Enter prompt for generating video images (use $chunk for the text chunk):")
        self.images_prompt_input = QTextEdit()
        self.images_prompt_input.setPlaceholderText(
            "For example: Create a realistic image depicting $chunk...")
        self.images_prompt_input.setMinimumHeight(80)

        images_layout.addWidget(images_label)
        images_layout.addWidget(self.images_prompt_input)
        
        # Add Runware Model field for images
        images_model_layout = QHBoxLayout()
        images_model_label = QLabel("Runware Model:")
        self.images_model_input = QLineEdit()
        self.images_model_input.setPlaceholderText("e.g., runware:100@1")
        self.images_model_input.setText("runware:100@1")
        self.images_model_input.setStyleSheet("padding: 5px;")
        
        images_model_layout.addWidget(images_model_label)
        images_model_layout.addWidget(self.images_model_input)
        images_layout.addLayout(images_model_layout)
        
        # Lora section with dynamic add/remove for images
        images_lora_section_label = QLabel(f"Runware Loras (optional, -4 to 4 weight range, max {self.MAX_LORAS}):")
        images_layout.addWidget(images_lora_section_label)
        
        # Container for images lora inputs
        self.images_lora_container = QWidget()
        self.images_lora_layout = QVBoxLayout(self.images_lora_container)
        self.images_lora_layout.setContentsMargins(0, 0, 0, 0)
        self.images_lora_layout.setSpacing(5)
        
        # List to keep track of images lora input rows
        self.images_lora_rows = []
        
        # Add button for more loras for images
        images_add_lora_button_layout = QHBoxLayout()
        self.images_add_lora_button = QPushButton("Add Another Lora")
        self.images_add_lora_button.setStyleSheet("""
            QPushButton {
                background-color: #3d85c6;
                color: white;
                padding: 5px;
                border-radius: 3px;
                border: none;
            }
            QPushButton:hover {
                background-color: #5a9bd5;
            }
            QPushButton:pressed {
                background-color: #2a5885;
            }
            QPushButton:disabled {
                background-color: #3a3a3a;
                color: #888888;
                border: 1px solid #555555;
            }
        """)
        self.images_add_lora_button.clicked.connect(self.add_images_lora_input_row)
        images_add_lora_button_layout.addStretch()
        images_add_lora_button_layout.addWidget(self.images_add_lora_button)
        
        images_layout.addWidget(self.images_lora_container)
        images_layout.addLayout(images_add_lora_button_layout)
        
        # Add initial lora input row for images
        self.add_images_lora_input_row()
        
        images_group.setLayout(images_layout)
        prompts_grid.addWidget(images_group, 1, 0)

        # Disclaimer Text Group
        disclaimer_group = self.create_group_box("Disclaimer Text")
        disclaimer_layout = QVBoxLayout()
        
        disclaimer_label = QLabel("Enter text for disclaimer in the description:")
        self.disclaimer_input = QTextEdit()
        self.disclaimer_input.setPlaceholderText(
            "DISCLAIMER: ...")
        self.disclaimer_input.setMinimumHeight(80)
        
        disclaimer_layout.addWidget(disclaimer_label)
        disclaimer_layout.addWidget(self.disclaimer_input)
        disclaimer_group.setLayout(disclaimer_layout)
        prompts_grid.addWidget(disclaimer_group, 2, 0)

        # Script Prompts Group
        script_group = self.create_group_box("Script Prompts")
        script_layout = QVBoxLayout()
        
        # Language and Voice Selection
        lang_voice_layout = QGridLayout()
        
        # Language Selection
        language_label = QLabel("Language:")
        self.language_combo = QComboBox()
        self.language_combo.setStyleSheet("padding: 5px;")
        
        # Voice Selection
        voice_label = QLabel("Voice:")
        self.voice_combo = QComboBox()
        self.voice_combo.setStyleSheet("padding: 5px;")
        
        # Populate language and voice data
        self._setup_language_voice_data()
        self._populate_language_combo()
        
        # Connect language change to update voices
        self.language_combo.currentIndexChanged.connect(lambda: self._on_language_changed(self.language_combo.currentData()))
        
        lang_voice_layout.addWidget(language_label, 0, 0)
        lang_voice_layout.addWidget(self.language_combo, 0, 1)
        lang_voice_layout.addWidget(voice_label, 1, 0)
        lang_voice_layout.addWidget(self.voice_combo, 1, 1)
        
        script_layout.addLayout(lang_voice_layout)
        
        # Intro Prompt
        intro_label = QLabel("Intro Prompt:")
        self.intro_prompt_input = QTextEdit()
        self.intro_prompt_input.setPlaceholderText(
            "Enter first prompt for generating the introduction part of the script")
        self.intro_prompt_input.setMinimumHeight(80)

        # Looping Prompt
        looping_label = QLabel("Looping Prompt:")
        self.looping_prompt_input = QTextEdit()
        self.looping_prompt_input.setPlaceholderText(
            "Enter second prompt for generating the main content of the script")
        self.looping_prompt_input.setMinimumHeight(80)

        # Outro Prompt
        outro_label = QLabel("Outro Prompt:")
        self.outro_prompt_input = QTextEdit()
        self.outro_prompt_input.setPlaceholderText(
            "Enter third prompt for generating the conclusion part of the script")
        self.outro_prompt_input.setMinimumHeight(80)

        script_layout.addWidget(intro_label)
        script_layout.addWidget(self.intro_prompt_input)
        script_layout.addWidget(looping_label)
        script_layout.addWidget(self.looping_prompt_input)
        script_layout.addWidget(outro_label)
        script_layout.addWidget(self.outro_prompt_input)

        script_group.setLayout(script_layout)
        prompts_grid.addWidget(script_group, 0, 1, 3, 1)  # Span 3 rows

        # Add buttons at the bottom
        buttons_layout = QHBoxLayout()
        
        # Add spacer to push the manage variables button to the right
        buttons_layout.addItem(QSpacerItem(20, 20, QSizePolicy.Expanding, QSizePolicy.Minimum))
        
        # Manage variables button
        self.manage_prompt_variables_button = QPushButton("Manage Variables")
        self.manage_prompt_variables_button.clicked.connect(self.open_variable_dialog)
        self.manage_prompt_variables_button.setStyleSheet("""
            QPushButton {
                background-color: #3d85c6;
                color: white;
                padding: 8px 16px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #5a9bd5;
            }
            QPushButton:pressed {
                background-color: #2a5885;
            }
        """)
        buttons_layout.addWidget(self.manage_prompt_variables_button)

        prompts_layout.addLayout(prompts_grid)
        prompts_layout.addLayout(buttons_layout)
        prompts_tab.setWidget(prompts_content)
        self.tab_widget.addTab(prompts_tab, "Prompts")

    def add_lora_input_row(self):
        """Add a new row of lora input fields"""
        # Check if we've reached the maximum number of loras
        if len(self.lora_rows) >= self.MAX_LORAS:
            self.add_lora_button.setEnabled(False)
            return
            
        # Create a row container
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        
        # Lora model input
        lora_model = QLineEdit()
        lora_model.setPlaceholderText("Lora model (e.g., lora:name@version)")
        lora_model.setStyleSheet("padding: 5px;")
        
        # Weight input with validator
        weight_input = QDoubleSpinBox()
        weight_input.setRange(-4.0, 4.0)
        weight_input.setSingleStep(0.1)
        weight_input.setValue(1.0)
        weight_input.setDecimals(1)
        weight_input.setFixedWidth(70)
        weight_input.setStyleSheet("padding: 5px;")
        
        # Delete button
        delete_button = QPushButton("×")
        delete_button.setFixedWidth(30)
        delete_button.setStyleSheet("""
            QPushButton {
                background-color: #ff4d4d;
                color: white;
                font-weight: bold;
                font-size: 14px;
                border-radius: 3px;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: #ff6666;
            }
        """)
        delete_button.clicked.connect(lambda: self.remove_lora_row(row_widget))
        
        # Add widgets to layout
        row_layout.addWidget(lora_model, 7)
        row_layout.addWidget(QLabel("Weight:"), 1)
        row_layout.addWidget(weight_input, 2)
        row_layout.addWidget(delete_button, 1)
        
        # Save references to the inputs
        row_data = {
            'widget': row_widget,
            'model': lora_model,
            'weight': weight_input
        }
        
        # Add to the container and save to our list
        self.lora_layout.addWidget(row_widget)
        self.lora_rows.append(row_data)
        
    def remove_lora_row(self, row_widget):
        """Remove a lora input row"""
        # Find the row in our list
        for i, row_data in enumerate(self.lora_rows):
            if row_data['widget'] == row_widget:
                # Remove from layout
                self.lora_layout.removeWidget(row_widget)
                row_widget.deleteLater()
                # Remove from our list
                self.lora_rows.pop(i)
                break
                
        # Re-enable the add button if we're below the limit
        if len(self.lora_rows) < self.MAX_LORAS:
            self.add_lora_button.setEnabled(True)

    def setup_settings_tab(self):
        """Setup settings tab with generation parameters"""
        settings_tab = QWidget()
        settings_layout = QVBoxLayout(settings_tab)

        # Script Settings Group
        script_settings = self.create_group_box("Script Generation Settings")
        script_layout = QGridLayout()

        # Prompt looping length
        prompt_loop_label = QLabel("Prompt Looping Length:")
        self.prompt_loop_spinbox = QSpinBox()
        self.prompt_loop_spinbox.setRange(1, 100)
        self.prompt_loop_spinbox.setValue(3)
        self.prompt_loop_spinbox.setStyleSheet("padding: 5px;")

        prompt_loop_help = QLabel(
            "Number of times to repeat the looping prompt")
        prompt_loop_help.setStyleSheet("color: #aaa; font-style: italic;")

        script_layout.addWidget(prompt_loop_label, 0, 0)
        script_layout.addWidget(self.prompt_loop_spinbox, 0, 1)
        script_layout.addWidget(prompt_loop_help, 1, 0, 1, 2)

        # Word limit per audio chunk
        audio_word_limit_label = QLabel("Word Limit per Audio Chunk:")
        self.audio_word_limit_spinbox = QSpinBox()
        self.audio_word_limit_spinbox.setRange(10, 800)
        self.audio_word_limit_spinbox.setValue(400)
        self.audio_word_limit_spinbox.setStyleSheet("padding: 5px;")

        audio_word_limit_help = QLabel(
            "Maximum number of words in each audio chunk")
        audio_word_limit_help.setStyleSheet("color: #aaa; font-style: italic;")

        script_layout.addWidget(audio_word_limit_label, 2, 0)
        script_layout.addWidget(self.audio_word_limit_spinbox, 2, 1)
        script_layout.addWidget(audio_word_limit_help, 3, 0, 1, 2)

        script_settings.setLayout(script_layout)
        settings_layout.addWidget(script_settings)

        # Image Settings Group
        image_settings = self.create_group_box("Image Generation Settings")
        image_layout = QGridLayout()

        # Image chunk count
        image_chunk_count_label = QLabel("Image Chunks Count:")
        self.image_chunk_count_spinbox = QSpinBox()
        self.image_chunk_count_spinbox.setRange(1, 20)
        self.image_chunk_count_spinbox.setValue(3)
        self.image_chunk_count_spinbox.setStyleSheet("padding: 5px;")

        image_chunk_count_help = QLabel("Number of images to generate")
        image_chunk_count_help.setStyleSheet(
            "color: #aaa; font-style: italic;")

        image_layout.addWidget(image_chunk_count_label, 0, 0)
        image_layout.addWidget(self.image_chunk_count_spinbox, 0, 1)
        image_layout.addWidget(image_chunk_count_help, 1, 0, 1, 2)

        # Image chunk word limit
        image_chunk_word_limit_label = QLabel(
            "Word Limit For Image Prompt Chunk:")
        self.image_chunk_word_limit_spinbox = QSpinBox()
        self.image_chunk_word_limit_spinbox.setRange(5, 100)
        self.image_chunk_word_limit_spinbox.setValue(15)
        self.image_chunk_word_limit_spinbox.setStyleSheet("padding: 5px;")

        image_chunk_word_limit_help = QLabel(
            "Maximum number of words in each image prompt")
        image_chunk_word_limit_help.setStyleSheet(
            "color: #aaa; font-style: italic;")

        image_layout.addWidget(image_chunk_word_limit_label, 2, 0)
        image_layout.addWidget(self.image_chunk_word_limit_spinbox, 2, 1)
        image_layout.addWidget(image_chunk_word_limit_help, 3, 0, 1, 2)

        image_settings.setLayout(image_layout)
        settings_layout.addWidget(image_settings)

        # Add stretch to push everything up
        settings_layout.addStretch()

        # Add tab
        self.tab_widget.addTab(settings_tab, "Settings")

    def setup_youtube_tab(self):
        """Setup YouTube tab with credentials settings"""
        youtube_tab = QWidget()
        youtube_layout = QVBoxLayout(youtube_tab)

        # YouTube Upload Control Group
        upload_control_group = self.create_group_box("Upload Control")
        upload_control_layout = QVBoxLayout()

        # YouTube upload checkbox
        self.youtube_upload_checkbox = QCheckBox("Upload video to YouTube")
        self.youtube_upload_checkbox.setStyleSheet("padding: 8px; font-weight: bold;")
        self.youtube_upload_checkbox.stateChanged.connect(self.toggle_youtube_upload)
        upload_control_layout.addWidget(self.youtube_upload_checkbox)

        # Channel name input (for when YouTube upload is disabled)
        channel_name_layout = QHBoxLayout()
        channel_name_label = QLabel("Channel Name:")
        self.channel_name_input = QLineEdit()
        self.channel_name_input.setPlaceholderText("Enter channel name for file organization")
        self.channel_name_input.setStyleSheet("padding: 8px;")
        self.channel_name_input.setEnabled(True)  # Enabled by default when YouTube upload is off
        
        channel_name_layout.addWidget(channel_name_label)
        channel_name_layout.addWidget(self.channel_name_input)
        upload_control_layout.addLayout(channel_name_layout)

        upload_control_group.setLayout(upload_control_layout)
        youtube_layout.addWidget(upload_control_group)

        # YouTube Credentials Group
        youtube_group = self.create_group_box("YouTube API Credentials")
        youtube_cred_layout = QVBoxLayout()

        youtube_info = QLabel(
            "Configure your YouTube API credentials to enable video uploads.")
        youtube_info.setWordWrap(True)
        youtube_info.setStyleSheet("color: #ddd; margin-bottom: 10px;")

        credential_detail_layout = QGridLayout()

        account_name_label = QLabel("Account:")
        self.account_name_edit = QLineEdit()
        self.account_name_edit.setReadOnly(True)
        self.account_name_edit.setPlaceholderText("No credentials loaded")
        self.account_name_edit.setStyleSheet("padding: 8px;")
        self.account_name_edit.setEnabled(False)  # Disabled by default
                
        channel_combo_label = QLabel("Channel:")
        self.channel_edit = QLineEdit()
        self.channel_edit.setReadOnly(True)
        self.channel_edit.setPlaceholderText("No channel selected")
        self.channel_edit.setStyleSheet("padding: 8px;")
        self.channel_edit.setEnabled(False)  # Disabled by default

        category_id_label = QLabel("Category ID:")
        self.category_id_edit = QLineEdit()
        self.category_id_edit.setPlaceholderText("Input the category id")
        self.category_id_edit.setText('24')
        self.category_id_edit.setStyleSheet("padding: 8px;")
        self.category_id_edit.setEnabled(False)  # Disabled by default

        # Scheduling
        self.schedule_checkbox = QCheckBox("Schedule publication")
        self.schedule_checkbox.stateChanged.connect(self.toggle_schedule)
        self.schedule_checkbox.setEnabled(False)  # Disabled by default

        self.schedule_datetime = QDateTimeEdit()
        self.schedule_datetime.setMinimumDateTime(QDateTime.currentDateTime().addSecs(300))
        self.schedule_datetime.setEnabled(False)
        self.schedule_datetime.setStyleSheet("padding: 8px;")

        credential_detail_layout.addWidget(account_name_label, 0, 0)
        credential_detail_layout.addWidget(self.account_name_edit, 0, 1)
        credential_detail_layout.addWidget(channel_combo_label, 1, 0)
        credential_detail_layout.addWidget(self.channel_edit, 1, 1)
        credential_detail_layout.addWidget(category_id_label, 2, 0)
        credential_detail_layout.addWidget(self.category_id_edit, 2, 1)
        credential_detail_layout.addWidget(self.schedule_checkbox, 3, 0)
        credential_detail_layout.addWidget(self.schedule_datetime, 3, 1)

        credential_control_layout = QHBoxLayout()

        self.load_youtube_credential_button = QPushButton("Load Credentials")
        self.load_youtube_credential_button.clicked.connect(
            self.load_youtube_credential)
        self.load_youtube_credential_button.setStyleSheet("""
            QPushButton {
                background-color: #3d85c6;
                color: white;
                padding: 8px 16px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #5a9bd5;
            }
            QPushButton:pressed {
                background-color: #2a5885;
            }
        """)
        self.load_youtube_credential_button.setEnabled(False)  # Disabled by default

        credential_control_layout.addItem(QSpacerItem(
            20, 20, QSizePolicy.Expanding, QSizePolicy.Minimum))
        credential_control_layout.addWidget(
            self.load_youtube_credential_button)

        youtube_guide = QLabel(
            "1. Go to Google Cloud Console and create a project\n"
            "2. Enable the YouTube Data API v3\n"
            "3. Create OAuth 2.0 credentials\n"
            "4. Download the JSON file and load it here"
        )
        youtube_guide.setStyleSheet(
            "color: #aaa; font-style: italic; margin-top: 15px;")
        youtube_guide.setWordWrap(True)

        youtube_cred_layout.addWidget(youtube_info)
        youtube_cred_layout.addLayout(credential_detail_layout)
        youtube_cred_layout.addLayout(credential_control_layout)
        youtube_cred_layout.addWidget(youtube_guide)

        youtube_group.setLayout(youtube_cred_layout)
        youtube_layout.addWidget(youtube_group)

        # Add stretch to push everything up
        youtube_layout.addStretch()

        # Add tab
        self.tab_widget.addTab(youtube_tab, "YouTube")

    def toggle_youtube_upload(self, state):
        """Toggle YouTube upload functionality"""
        is_enabled = state == Qt.Checked
        
        # Enable/disable YouTube credential fields
        self.account_name_edit.setEnabled(is_enabled)
        self.channel_edit.setEnabled(is_enabled)
        self.category_id_edit.setEnabled(is_enabled)
        self.schedule_checkbox.setEnabled(is_enabled)
        self.schedule_datetime.setEnabled(is_enabled and self.schedule_checkbox.isChecked())
        self.load_youtube_credential_button.setEnabled(is_enabled)
        
        # Enable/disable channel name input (opposite of YouTube upload)
        self.channel_name_input.setEnabled(not is_enabled)
        
        # Clear credentials if YouTube upload is disabled
        if not is_enabled:
            self.credentials = None
            self.account_name_edit.clear()
            self.channel_edit.clear()

    def create_group_box(self, title):
        """Helper method to create styled group boxes"""
        group = QGroupBox(title)
        group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid #555;
                border-radius: 5px;
                margin-top: 1ex;
                padding: 10px;
                color: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding: 0 5px;
            }
        """)
        return group

    def _setup_language_voice_data(self):
        """Setup language and voice metadata"""
        self.supported_languages = {
            'a': {'code': 'en-US', 'name': 'American English', 'flag': '🇺🇸'},
            'b': {'code': 'en-GB', 'name': 'British English', 'flag': '🇬🇧'},
            'e': {'code': 'es', 'name': 'Spanish', 'flag': '🇪🇸'},
            'f': {'code': 'fr-FR', 'name': 'French', 'flag': '🇫🇷'},
            'h': {'code': 'hi', 'name': 'Hindi', 'flag': '🇮🇳'},
            'i': {'code': 'it', 'name': 'Italian', 'flag': '🇮🇹'},
            'j': {'code': 'ja', 'name': 'Japanese', 'flag': '🇯🇵'},
            'p': {'code': 'pt-BR', 'name': 'Brazilian Portuguese', 'flag': '🇧🇷'},
            'z': {'code': 'zh-CN', 'name': 'Mandarin Chinese', 'flag': '🇨🇳'}
        }

        self.voice_metadata = {
            "af_alloy": {"name": "Alloy", "gender": "female", "language": "a", "description": "American English female voice"},
            "af_aoede": {"name": "Aoede", "gender": "female", "language": "a", "description": "American English female voice"},
            "af_bella": {"name": "Bella", "gender": "female", "language": "a", "description": "American English female voice"},
            "af_heart": {"name": "Heart", "gender": "female", "language": "a", "description": "American English female voice"},
            "af_jessica": {"name": "Jessica", "gender": "female", "language": "a", "description": "American English female voice"},
            "af_kore": {"name": "Kore", "gender": "female", "language": "a", "description": "American English female voice"},
            "af_nicole": {"name": "Nicole", "gender": "female", "language": "a", "description": "American English female voice"},
            "af_nova": {"name": "Nova", "gender": "female", "language": "a", "description": "American English female voice"},
            "af_river": {"name": "River", "gender": "female", "language": "a", "description": "American English female voice"},
            "af_sarah": {"name": "Sarah", "gender": "female", "language": "a", "description": "American English female voice"},
            "af_sky": {"name": "Sky", "gender": "female", "language": "a", "description": "American English female voice"},
            "am_adam": {"name": "Adam", "gender": "male", "language": "a", "description": "American English male voice"},
            "am_echo": {"name": "Echo", "gender": "male", "language": "a", "description": "American English male voice"},
            "am_eric": {"name": "Eric", "gender": "male", "language": "a", "description": "American English male voice"},
            "am_fenrir": {"name": "Fenrir", "gender": "male", "language": "a", "description": "American English male voice"},
            "am_liam": {"name": "Liam", "gender": "male", "language": "a", "description": "American English male voice"},
            "am_michael": {"name": "Michael", "gender": "male", "language": "a", "description": "American English male voice"},
            "am_onyx": {"name": "Onyx", "gender": "male", "language": "a", "description": "American English male voice"},
            "am_puck": {"name": "Puck", "gender": "male", "language": "a", "description": "American English male voice"},
            "am_santa": {"name": "Santa", "gender": "male", "language": "a", "description": "American English male voice"},
            "bf_alice": {"name": "Alice", "gender": "female", "language": "b", "description": "British English female voice"},
            "bf_emma": {"name": "Emma", "gender": "female", "language": "b", "description": "British English female voice"},
            "bf_isabella": {"name": "Isabella", "gender": "female", "language": "b", "description": "British English female voice"},
            "bf_lily": {"name": "Lily", "gender": "female", "language": "b", "description": "British English female voice"},
            "bm_daniel": {"name": "Daniel", "gender": "male", "language": "b", "description": "British English male voice"},
            "bm_fable": {"name": "Fable", "gender": "male", "language": "b", "description": "British English male voice"},
            "bm_george": {"name": "George", "gender": "male", "language": "b", "description": "British English male voice"},
            "bm_lewis": {"name": "Lewis", "gender": "male", "language": "b", "description": "British English male voice"},
            "ef_dora": {"name": "Dora", "gender": "female", "language": "e", "description": "Spanish female voice"},
            "em_alex": {"name": "Alex", "gender": "male", "language": "e", "description": "Spanish male voice"},
            "em_santa": {"name": "Santa", "gender": "male", "language": "e", "description": "Spanish male voice"},
            "ff_siwis": {"name": "Siwis", "gender": "female", "language": "f", "description": "French female voice"},
            "hf_alpha": {"name": "Alpha", "gender": "female", "language": "h", "description": "Hindi female voice"},
            "hf_beta": {"name": "Beta", "gender": "female", "language": "h", "description": "Hindi female voice"},
            "hm_omega": {"name": "Omega", "gender": "male", "language": "h", "description": "Hindi male voice"},
            "hm_psi": {"name": "Psi", "gender": "male", "language": "h", "description": "Hindi male voice"},
            "if_sara": {"name": "Sara", "gender": "female", "language": "i", "description": "Italian female voice"},
            "im_nicola": {"name": "Nicola", "gender": "male", "language": "i", "description": "Italian male voice"},
            "jf_alpha": {"name": "Alpha", "gender": "female", "language": "j", "description": "Japanese female voice"},
            "jf_gongitsune": {"name": "Gongitsune", "gender": "female", "language": "j", "description": "Japanese female voice"},
            "jf_nezumi": {"name": "Nezumi", "gender": "female", "language": "j", "description": "Japanese female voice"},
            "jf_tebukuro": {"name": "Tebukuro", "gender": "female", "language": "j", "description": "Japanese female voice"},
            "jm_kumo": {"name": "Kumo", "gender": "male", "language": "j", "description": "Japanese male voice"},
            "pf_dora": {"name": "Dora", "gender": "female", "language": "p", "description": "Portuguese female voice"},
            "pm_alex": {"name": "Alex", "gender": "male", "language": "p", "description": "Portuguese male voice"},
            "pm_santa": {"name": "Santa", "gender": "male", "language": "p", "description": "Portuguese male voice"},
            "zf_xiaobei": {"name": "Xiaobei", "gender": "female", "language": "z", "description": "Chinese female voice"},
            "zf_xiaoni": {"name": "Xiaoni", "gender": "female", "language": "z", "description": "Chinese female voice"},
            "zf_xiaoxiao": {"name": "Xiaoxiao", "gender": "female", "language": "z", "description": "Chinese female voice"},
            "zf_xiaoyi": {"name": "Xiaoyi", "gender": "female", "language": "z", "description": "Chinese female voice"},
            "zm_yunjian": {"name": "Yunjian", "gender": "male", "language": "z", "description": "Chinese male voice"},
            "zm_yunxi": {"name": "Yunxi", "gender": "male", "language": "z", "description": "Chinese male voice"},
            "zm_yunxia": {"name": "Yunxia", "gender": "male", "language": "z", "description": "Chinese male voice"},
            "zm_yunyang": {"name": "Yunyang", "gender": "male", "language": "z", "description": "Chinese male voice"}
        }

    def _populate_language_combo(self):
        """Populate the language combobox with available languages"""
        self.language_combo.clear()
        for lang_code, lang_info in self.supported_languages.items():
            display_text = f"{lang_info['flag']} {lang_info['name']}"
            self.language_combo.addItem(display_text, lang_code)
        
        # Set default to American English
        self.language_combo.setCurrentIndex(0)
        self._on_language_changed('a')  # Initialize voice combo

    def _on_language_changed(self, language_code):
        """Update voice combobox when language changes"""
        self.voice_combo.clear()
        
        # Filter voices by selected language
        available_voices = {
            voice_id: voice_info 
            for voice_id, voice_info in self.voice_metadata.items() 
            if voice_info['language'] == language_code
        }
        
        # Sort voices by gender (female first), then by name
        sorted_voices = sorted(
            available_voices.items(),
            key=lambda x: (x[1]['gender'] == 'male', x[1]['name'])
        )
        
        for voice_id, voice_info in sorted_voices:
            gender_icon = "♀️" if voice_info['gender'] == 'female' else "♂️"
            display_text = f"{gender_icon} {voice_info['name']}"
            self.voice_combo.addItem(display_text, voice_id)
        
        # Set default voice for language
        if self.voice_combo.count() > 0:
            self.voice_combo.setCurrentIndex(0)

    def save_settings(self, file_path):
        """Save current settings to a JSON file"""
        try:
            # Get thumbnail lora data
            thumbnail_lora_data = []
            for row in self.lora_rows:
                model = row['model'].text().strip()
                weight = row['weight'].value()
                if model:  # Only include non-empty models
                    thumbnail_lora_data.append({"model": model, "weight": weight})
            
            # Get image lora data
            image_lora_data = []
            for row in self.images_lora_rows:
                model = row['model'].text().strip()
                weight = row['weight'].value()
                if model:  # Only include non-empty models
                    image_lora_data.append({"model": model, "weight": weight})
            
            settings = {
                "api_key": self.api_key_input.text(),
                "background_music": self.background_music_input.text(),
                "thumbnail_prompt": self.thumbnail_prompt_input.toPlainText(),
                "images_prompt": self.images_prompt_input.toPlainText(),
                "disclaimer": self.disclaimer_input.toPlainText(),
                "intro_prompt": self.intro_prompt_input.toPlainText(),
                "looping_prompt": self.looping_prompt_input.toPlainText(),
                "outro_prompt": self.outro_prompt_input.toPlainText(),
                "prompt_variables": self.variables,
                "loop_length": self.prompt_loop_spinbox.value(),
                "audio_word_limit": self.audio_word_limit_spinbox.value(),
                "image_count": self.image_chunk_count_spinbox.value(),
                "image_word_limit": self.image_chunk_word_limit_spinbox.value(),
                "thumbnail_model": self.runware_model_input.text(),
                "thumbnail_loras": thumbnail_lora_data,
                "image_model": self.images_model_input.text(),
                "image_loras": image_lora_data,
                "language": self.language_combo.currentData(),
                "voice": self.voice_combo.currentData(),
                "youtube_upload_enabled": self.youtube_upload_checkbox.isChecked(),
                "channel_name": self.channel_name_input.text().strip(),
                "category_id": self.category_id_edit.text().strip(),
                "schedule_enabled": self.schedule_checkbox.isChecked(),
                "schedule_datetime": self.schedule_datetime.dateTime().toString(Qt.ISODate) if self.schedule_checkbox.isChecked() else ""
            }

            with open(file_path, 'w') as f:
                json.dump(settings, f, indent=4)

            self.logger.info(f"Settings saved to {file_path}")
            QMessageBox.information(
                self, "Settings Saved", "Settings have been saved successfully!")
        except Exception as e:
            self.logger.error(f"Error saving settings: {str(e)}")
            QMessageBox.critical(
                self, "Error", f"Failed to save settings: {str(e)}")

    def load_settings(self, file_path):
        """Load settings from a JSON file"""
        try:
            with open(file_path, 'r') as f:
                settings = json.load(f)

            # Load basic settings
            self.api_key_input.setText(settings.get('api_key', ''))
            self.background_music_input.setText(settings.get('background_music', ''))
            self.thumbnail_prompt_input.setPlainText(settings.get('thumbnail_prompt', ''))
            self.images_prompt_input.setPlainText(settings.get('images_prompt', ''))
            self.disclaimer_input.setPlainText(settings.get('disclaimer', ''))
            self.intro_prompt_input.setPlainText(settings.get('intro_prompt', ''))
            self.looping_prompt_input.setPlainText(settings.get('looping_prompt', ''))
            self.outro_prompt_input.setPlainText(settings.get('outro_prompt', ''))
            self.variables = settings.get('prompt_variables', {})
            
            # Load numeric settings
            self.prompt_loop_spinbox.setValue(settings.get('loop_length', 3))
            self.audio_word_limit_spinbox.setValue(settings.get('audio_word_limit', 400))
            self.image_chunk_count_spinbox.setValue(settings.get('image_count', 3))
            self.image_chunk_word_limit_spinbox.setValue(settings.get('image_word_limit', 15))
            
            # Load thumbnail model and loras
            self.runware_model_input.setText(settings.get('thumbnail_model', 'runware:100@1'))
            
            # Clear existing thumbnail lora rows
            for row in self.lora_rows:
                row['widget'].deleteLater()
            self.lora_rows.clear()
            
            # Add new thumbnail lora rows
            thumbnail_loras = settings.get('thumbnail_loras', [])
            for lora in thumbnail_loras:
                self.add_lora_input_row()
                row = self.lora_rows[-1]
                row['model'].setText(lora['model'])
                row['weight'].setValue(lora['weight'])
            
            # Load image model and loras
            self.images_model_input.setText(settings.get('image_model', 'runware:100@1'))
            
            # Clear existing image lora rows
            for row in self.images_lora_rows:
                row['widget'].deleteLater()
            self.images_lora_rows.clear()
            
            # Add new image lora rows
            image_loras = settings.get('image_loras', [])
            for lora in image_loras:
                self.add_images_lora_input_row()
                row = self.images_lora_rows[-1]
                row['model'].setText(lora['model'])
                row['weight'].setValue(lora['weight'])
            
            # Load language
            language = settings.get('language', 'a')  # Default to 'a' for American English
            for i in range(self.language_combo.count()):
                if self.language_combo.itemData(i) == language:
                    self.language_combo.setCurrentIndex(i)
                    self._on_language_changed(language)  # Update voice combo
                    break
            
            # Load voice (after language is set)
            voice = settings.get('voice', None)
            if voice:
                for i in range(self.voice_combo.count()):
                    if self.voice_combo.itemData(i) == voice:
                        self.voice_combo.setCurrentIndex(i)
                        break
            
            # Load YouTube upload settings
            youtube_upload_enabled = settings.get('youtube_upload_enabled', False)
            self.youtube_upload_checkbox.setChecked(youtube_upload_enabled)
            self.toggle_youtube_upload(Qt.Checked if youtube_upload_enabled else Qt.Unchecked)
            
            # Load channel name
            self.channel_name_input.setText(settings.get('channel_name', ''))
            
            # Load category ID
            self.category_id_edit.setText(settings.get('category_id', '24'))
            
            # Load schedule settings
            schedule_enabled = settings.get('schedule_enabled', False)
            self.schedule_checkbox.setChecked(schedule_enabled)
            self.schedule_datetime.setEnabled(schedule_enabled)
            
            schedule_datetime = settings.get('schedule_datetime', '')
            if schedule_datetime and schedule_enabled:
                try:
                    schedule_dt = QDateTime.fromString(schedule_datetime, Qt.ISODate)
                    self.schedule_datetime.setDateTime(schedule_dt)
                except Exception:
                    pass
            
            # Update add buttons state
            self.add_lora_button.setEnabled(len(self.lora_rows) < self.MAX_LORAS)
            self.images_add_lora_button.setEnabled(len(self.images_lora_rows) < self.MAX_LORAS)

            self.logger.info(f"Settings loaded from {file_path}")
            QMessageBox.information(
                self, "Settings Loaded", "Settings have been loaded successfully!")
        except Exception as e:
            self.logger.error(f"Error loading settings: {str(e)}")
            QMessageBox.critical(
                self, "Error", f"Failed to load settings: {str(e)}")

    def toggle_key_visibility(self):
        if self.api_key_input.echoMode() == QLineEdit.Password:
            self.api_key_input.setEchoMode(QLineEdit.Normal)
            self.toggle_key_visibility_btn.setText("Hide")
        else:
            self.api_key_input.setEchoMode(QLineEdit.Password)
            self.toggle_key_visibility_btn.setText("Show")
            
    def load_background_music(self):
        file_name, _ = QFileDialog.getOpenFileName(
            self, 'Select Background Music', '', 'Audio Files (*.mp3 *.wav *.ogg)')
        if file_name:
            self.background_music_input.setText(file_name)
            self.logger.info(f'Selected background music: {file_name}')

    def update_log(self, message):
        self.log_window.append(message)
        # Auto-scroll to bottom
        self.log_window.verticalScrollBar().setValue(
            self.log_window.verticalScrollBar().maximum()
        )

    def clear_log(self):
        self.log_window.clear()
        self.logger.info("Log cleared")

    def cleanup_workers(self):
        """Clean up any running worker threads"""
        if self.current_generation_worker and self.current_generation_worker.isRunning():
            self.current_generation_worker.cancel()
            self.current_generation_worker.wait()
            
        if self.current_upload_thread and self.current_upload_thread.isRunning():
            self.current_upload_thread.cancel()
            self.current_upload_thread.wait()

    def start_generation(self):
        """Start the video generation process"""
        try:
            # Check if YouTube upload is enabled
            if self.youtube_upload_checkbox.isChecked():
                # Check YouTube credentials if upload is enabled
                if not self.credentials:
                    QMessageBox.warning(
                        self,
                        "YouTube Authentication Required",
                        "Please load your YouTube credentials first by clicking 'Load Credentials' in the YouTube tab before starting generation."
                    )
                    self.tab_widget.setCurrentIndex(self.tab_widget.count() - 1)  # Switch to YouTube tab
                    return
                    
                if not self.credentials.valid:
                    QMessageBox.warning(
                        self,
                        "YouTube Authentication Required",
                        "Your YouTube credentials are invalid or expired. Please re-authenticate by clicking 'Load Credentials' in the YouTube tab."
                    )
                    self.tab_widget.setCurrentIndex(self.tab_widget.count() - 1)  # Switch to YouTube tab
                    return
            else:
                # Check if channel name is provided when YouTube upload is disabled
                if not self.channel_name_input.text().strip():
                    QMessageBox.warning(
                        self,
                        "Channel Name Required",
                        "Please enter a channel name for file organization when YouTube upload is disabled."
                    )
                    self.tab_widget.setCurrentIndex(self.tab_widget.count() - 1)  # Switch to YouTube tab
                    return

            # Get input data
            input_data = self._get_input_data()
            if not input_data:
                return
            
            # Create and start worker thread
            self.current_generation_worker = GenerationWorker(
                api_key=input_data['api_key'],
                video_title=input_data['video_title'],
                background_music_path=input_data['background_music_path'],
                thumbnail_prompt=input_data['thumbnail_prompt'],
                images_prompt=input_data['images_prompt'],
                intro_prompt=input_data['intro_prompt'],
                looping_prompt=input_data['looping_prompt'],
                outro_prompt=input_data['outro_prompt'],
                loop_length=input_data['loop_length'],
                word_limit=input_data['word_limit'],
                image_count=input_data['image_count'],
                image_word_limit=input_data['image_word_limit'],
                runware_model=input_data['thumbnail_model'],
                runware_loras=input_data['thumbnail_loras'],
                image_model=input_data['image_model'],
                image_loras=input_data['image_loras'],
                language=input_data['language'],
                voice=input_data['voice'],
                channel_name=input_data['channel_name'],
                logger=input_data['logger']
            )
            
            # Connect signals
            self._connect_generation_signals()
            
            # Start generation
            self.current_generation_worker.start()
            
            # Update UI
            self.toggle_ui_elements(False)
            self.progress_bar.setValue(0)
            self.current_operation_label.setText("Starting generation...")
            self.generate_btn.setText("Cancel")
            self.generate_btn.clicked.disconnect()
            self.generate_btn.clicked.connect(self.cancel_generation)
            
        except Exception as e:
            self.logger.error(f"Failed to start generation: {e}")
            QMessageBox.critical(
                self, "Error", f"Failed to start generation: {str(e)}")

    def validate_inputs(self) -> bool:
        """Validate all input fields"""
        # Check API key
        if not self.api_key_input.text().strip():
            self.show_error("Please enter your OpenAI API key")
            return False

        # Check video title
        if not self.video_title_input.text().strip():
            self.show_error("Please enter a video title")
            return False

        # Check prompts
        if not self.thumbnail_prompt_input.toPlainText().strip():
            self.show_error("Please enter a thumbnail prompt")
            return False

        if not self.images_prompt_input.toPlainText().strip():
            self.show_error("Please enter an images prompt")
            return False

        if not self.intro_prompt_input.toPlainText().strip():
            self.show_error("Please enter an intro prompt")
            return False

        if not self.looping_prompt_input.toPlainText().strip():
            self.show_error("Please enter a looping prompt")
            return False

        if not self.outro_prompt_input.toPlainText().strip():
            self.show_error("Please enter an outro prompt")
            return False

        # Check Runware models
        if not self.runware_model_input.text().strip():
            self.show_error("Please enter a Runware model for thumbnail generation")
            return False

        if not self.images_model_input.text().strip():
            self.show_error("Please enter a Runware model for image generation")
            return False

        # Validate Runware Loras if any are provided
        for lora_row in self.lora_rows:
            model_input, weight_input = lora_row
            if model_input.text().strip():  # If model is provided
                try:
                    weight = float(weight_input.text())
                    if not (-4 <= weight <= 4):
                        self.show_error("Thumbnail Lora weights must be between -4 and 4")
                        return False
                except ValueError:
                    self.show_error("Invalid thumbnail Lora weight value")
                    return False

        for lora_row in self.images_lora_rows:
            model_input, weight_input = lora_row
            if model_input.text().strip():  # If model is provided
                try:
                    weight = float(weight_input.text())
                    if not (-4 <= weight <= 4):
                        self.show_error("Image Lora weights must be between -4 and 4")
                        return False
                except ValueError:
                    self.show_error("Invalid image Lora weight value")
                    return False

        return True

    def _get_input_data(self):
        """Get and process all input data for generation"""
        try:
            video_title = self.video_title_input.text().strip()
            safe_title = title_to_safe_folder_name(video_title)
            self.video_title = safe_title
            
            # Get channel name based on YouTube upload setting
            if self.youtube_upload_checkbox.isChecked():
                # Use YouTube channel name when upload is enabled
                channel_name = self.channel_edit.text().strip()
                if not channel_name:
                    channel_name = "default"  # Use default if no channel is selected
            else:
                # Use manual channel name input when upload is disabled
                channel_name = self.channel_name_input.text().strip()
                if not channel_name:
                    channel_name = "default"  # Use default if no channel name provided
            
            # Get all prompts
            thumbnail_prompt = self._process_prompt(self.thumbnail_prompt_input.toPlainText().strip(), video_title)
            images_prompt = self._process_prompt(self.images_prompt_input.toPlainText().strip(), video_title)
            intro_prompt = self._process_prompt(self.intro_prompt_input.toPlainText().strip(), video_title)
            looping_prompt = self._process_prompt(self.looping_prompt_input.toPlainText().strip(), video_title)
            outro_prompt = self._process_prompt(self.outro_prompt_input.toPlainText().strip(), video_title)
            
            # Get thumbnail lora data
            thumbnail_loras = []
            for row in self.lora_rows:
                model = row['model'].text().strip()
                weight = row['weight'].value()
                if model:  # Only include non-empty models
                    thumbnail_loras.append({"model": model, "weight": weight})
            
            # Get image lora data
            image_loras = []
            for row in self.images_lora_rows:
                model = row['model'].text().strip()
                weight = row['weight'].value()
                if model:  # Only include non-empty models
                    image_loras.append({"model": model, "weight": weight})
            
            return {
                'api_key': self.api_key_input.text().strip(),
                'video_title': safe_title,
                'background_music_path': self.background_music_input.text(),
                'thumbnail_prompt': thumbnail_prompt,
                'images_prompt': images_prompt,
                'intro_prompt': intro_prompt,
                'looping_prompt': looping_prompt,
                'outro_prompt': outro_prompt,
                'loop_length': self.prompt_loop_spinbox.value(),
                'word_limit': self.audio_word_limit_spinbox.value(),
                'image_count': self.image_chunk_count_spinbox.value(),
                'image_word_limit': self.image_chunk_word_limit_spinbox.value(),
                'thumbnail_model': self.runware_model_input.text().strip(),
                'thumbnail_loras': thumbnail_loras,
                'image_model': self.images_model_input.text().strip(),
                'image_loras': image_loras,
                'language': self.language_combo.currentData(),
                'voice': self.voice_combo.currentData(),
                'channel_name': channel_name,
                'logger': self.logger
            }
            
        except Exception as e:
            self.logger.error(f"Error processing input data: {e}")
            return None

    def _process_prompt(self, prompt, title):
        """Process a prompt by replacing variables"""
        result = prompt.replace('$title', title)
        
        if self.variables:
            for key, value in self.variables.items():
                result = result.replace(f"${key}", value)
                
        return result

    def _connect_generation_signals(self):
        """Connect all signals for the generation worker"""
        self.current_generation_worker.progress_update.connect(self.update_progress)
        self.current_generation_worker.operation_update.connect(self.update_operation)
        self.current_generation_worker.error_occurred.connect(self.handle_generation_error)
        self.current_generation_worker.generation_finished.connect(self.handle_generation_finished)

    def handle_generation_error(self, error_msg):
        """Handle errors from the generation worker"""
        self.logger.error(f"Generation error: {error_msg}")
        QMessageBox.critical(self, "Error", str(error_msg))
        self.toggle_ui_elements(True)
        self.cleanup_workers()

    def handle_generation_finished(self, description):
        """Handle successful completion of generation"""
        try:
            self.logger.info("Video generation completed")
            self.current_operation_label.setText("Generation completed")
            self.progress_bar.setValue(100)
            
            # Only start upload if YouTube upload is enabled
            if self.youtube_upload_checkbox.isChecked():
                if not self.credentials:
                    raise Exception("Please authenticate with YouTube first - no credentials found.")
                if not self.credentials.valid:
                    raise Exception("Please authenticate with YouTube first - credentials are invalid.")
                
                # Prepare upload parameters
                upload_params = self._prepare_upload_params(description)
                
                # Initialize upload progress
                self.youtube_upload_progress_bar.setValue(0)
                self.youtube_status_label.setText("Status: Preparing upload...")
                
                # Create and start upload thread
                self.current_upload_thread = UploadThread(**upload_params)
                self._connect_upload_signals()
                self.current_upload_thread.start()
            else:
                # Just show completion message when YouTube upload is disabled
                self.logger.info("Video generation completed successfully. No upload performed.")
                self.toggle_ui_elements(True)
                QMessageBox.information(
                    self, 
                    "Generation Complete", 
                    "Video generation completed successfully!\n\nNo upload was performed as YouTube upload is disabled."
                )
            
        except Exception as e:
            self.logger.error(f"Error starting upload: {e}")
            QMessageBox.critical(self, "Error", str(e))
            self.toggle_ui_elements(True)

    def _prepare_upload_params(self, description):
        """Prepare parameters for video upload"""
        video_title = self.video_title_input.text()
        safe_folder = title_to_safe_folder_name(self.video_title)
        
        # Get channel name from the UI
        channel_name = self.channel_edit.text().strip()
        if not channel_name:
            channel_name = "default"  # Use default if no channel is selected
        
        # Determine the base directory (same level as exe file)
        if getattr(sys, 'frozen', False):
            # Running as PyInstaller executable - use directory containing the executable
            base_dir = os.path.dirname(sys.executable)
        else:
            # Running as script - use directory containing the script
            base_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Create the structured path: ./output/{channel name}/{video title}
        output_base = os.path.join(base_dir, "output")
        channel_dir = os.path.join(output_base, channel_name)
        video_dir = os.path.join(channel_dir, safe_folder)
        
        params = {
            'credentials': self.credentials,
            'video_path': os.path.join(video_dir, "final_slideshow_with_audio.mp4"),
            'thumbnail_path': os.path.join(video_dir, "thumbnail.jpg"),
            'title': video_title,
            'description': description + "\n\n" + self.disclaimer_input.toPlainText(),
            'category': self.category_id_edit.text(),
            'tags': "",
            'privacy_status': "public",
            'made_for_kids': False,
            'publish_at': None
        }
        
        if self.schedule_checkbox.isChecked():
            publish_at = self.schedule_datetime.dateTime().toPyDateTime()
            # Convert local datetime to UTC
            local_tz = datetime.datetime.now(datetime.timezone.utc).astimezone().tzinfo
            aware_local_time = publish_at.replace(tzinfo=local_tz)
            params['publish_at'] = aware_local_time.astimezone(pytz.UTC)
            
        return params

    def _connect_upload_signals(self):
        """Connect all signals for the upload thread"""
        self.current_upload_thread.progress_signal.connect(self.update_youtube_upload_progress)
        self.current_upload_thread.finished_signal.connect(self.handle_upload_finished)
        self.current_upload_thread.error_signal.connect(self.handle_upload_error)
        self.current_upload_thread.status_signal.connect(self.update_upload_youtube_status)
        # Connect token refresh signal to update credentials in account manager
        self.current_upload_thread.token_refresh_signal.connect(self.handle_token_refresh)

    def handle_token_refresh(self, refreshed_credentials):
        """Handle refreshed token by updating the credentials"""
        try:
            # Update the app's credential reference
            self.credentials = refreshed_credentials
            
            # If using account manager, update the account credentials
            if hasattr(self, 'account_manager') and self.account_manager.current_account:
                # Serialize credentials to bytes
                credentials_bytes = pickle.dumps(refreshed_credentials)
                
                # Update stored credentials in account manager
                self.account_manager.accounts[self.account_manager.current_account]['credentials'] = credentials_bytes
                self.account_manager.save_accounts()
                self.logger.info(f"Updated refreshed credentials for account: {self.account_manager.current_account}")
        except Exception as e:
            self.logger.error(f"Error updating refreshed credentials: {str(e)}")

    def update_progress(self, value):
        self.progress_bar.setValue(value)

    def update_operation(self, operation):
        self.current_operation_label.setText(operation)

    def update_youtube_upload_progress(self, progress):
        self.youtube_upload_progress_bar.setValue(progress)
    
    def update_upload_youtube_status(self, status):
        self.youtube_status_label.setText(f"Status: {status}")

    def handle_upload_finished(self, url, video_id):
        self.toggle_ui_elements(True)
        # Update status
        self.youtube_upload_progress_bar.setValue(100)
        
        # Show URL
        self.result_url.setText(url)
        
        # Show success message with different text based on privacy status
        if self.schedule_checkbox.isChecked():
            success_msg = f"Video uploaded and scheduled for publication!\nURL: {url}\nVideo ID: {video_id}"
        else:
            success_msg = f"Video uploaded and published publicly!\nURL: {url}\nVideo ID: {video_id}\n\nYour video is now live and can be viewed by anyone!"
            
        self.logger.info(success_msg)
    
    def handle_upload_error(self, error_msg):
        self.toggle_ui_elements(True)
        # Re-enable UI elements
        
        # Update status
        self.youtube_status_label.setText(f"Status: Error: {str(error_msg)}")
        
        # Show error message
        self.logger.error(self, "Upload Error", f"Failed to upload video: {str(error_msg)}")

    def toggle_load_settings(self):
        file_name, _ = QFileDialog.getOpenFileName(
            self, 'Open Settings File', '', 'JSON Files (*.json);;All Files (*)')
        if file_name:
            self.logger.info(f'Selected settings file: {file_name}')
            self.settings_filepath_input.setText(file_name)
            self.load_settings(file_name)

    def toggle_save_settings(self):
        file_name, _ = QFileDialog.getSaveFileName(
            self, 'Save File', '', 'JSON Files (*.json)')
        if file_name:
            self.logger.info(f'Save settings to: {file_name}')
            self.save_settings(file_name)

    def load_youtube_credential(self):
        dialog = AccountManagerDialog(self.account_manager, self)
        dialog.account_changed.connect(self.on_account_changed)
        if dialog.exec_():
            # Account selected and dialog accepted
            self.logger.info(f"Selected account: {self.account_manager.current_account}")

    def on_account_changed(self, account_name, credentials, channel_title):
        self.logger.info(f"Account changed to: {account_name}")
        self.logger.info(f"Credentials state - Valid: {credentials.valid if credentials else 'No credentials'}")
        self.credentials = credentials  # Save current account's credentials
        self.account_name_edit.setText(account_name)
        self.channel_edit.setText(channel_title)
        self.logger.info("Account credentials and UI updated")
    
    def on_channel_selected(self, index):
        if index >= 0:
            self.selected_channel = {
                'title': self.channel_edit.currentText(),
                'id': self.channel_edit.itemData(index)
            }
        
    def toggle_schedule(self, state):
        self.schedule_datetime.setEnabled(state == Qt.Checked)
        
    def open_variable_dialog(self):
        """Open the variable management dialog"""
        dialog = VariableDialog(self.variables, self)
        dialog.variables_saved.connect(self.handle_variables_saved)
        
        # Show dialog and process result
        if dialog.exec_() == QDialog.Accepted:
            # Variables are handled through signal
            pass
    
    def handle_variables_saved(self, variables):
        """Handle the variables saved from dialog"""
        self.variables = variables
        
        # Update status label
        if self.variables:
            count = len(self.variables)
            self.logger.info(f"{count} variable{'s' if count > 1 else ''} defined")

    def import_workflow_json(self):
        file_name, _ = QFileDialog.getOpenFileName(
            self, 'Open Workflow File', '', 'JSON Files (*.json)')
        if file_name:
            self.logger.info(f'Selected workflow file: {file_name}')
            self.workflow_file = file_name

    def toggle_ui_elements(self, enabled):
        # Enable/disable all input widgets
        self.api_key_input.setEnabled(enabled)
        self.toggle_key_visibility_btn.setEnabled(enabled)
        self.video_title_input.setReadOnly(enabled)
        self.thumbnail_prompt_input.setReadOnly(enabled)
        self.images_prompt_input.setReadOnly(enabled)
        self.intro_prompt_input.setReadOnly(enabled)
        self.looping_prompt_input.setReadOnly(enabled)
        self.outro_prompt_input.setReadOnly(enabled)
        self.prompt_loop_spinbox.setEnabled(enabled)
        self.audio_word_limit_spinbox.setEnabled(enabled)
        self.image_chunk_count_spinbox.setEnabled(enabled)
        self.image_chunk_word_limit_spinbox.setEnabled(enabled)
        self.runware_model_input.setEnabled(enabled)
        self.images_model_input.setEnabled(enabled)
        self.language_combo.setEnabled(enabled)
        self.voice_combo.setEnabled(enabled)
        self.add_lora_button.setEnabled(enabled)
        self.images_add_lora_button.setEnabled(enabled)
        
        # Enable/disable YouTube upload controls
        self.youtube_upload_checkbox.setEnabled(enabled)
        if enabled:
            # Re-enable based on current state
            is_youtube_enabled = self.youtube_upload_checkbox.isChecked()
            self.channel_name_input.setEnabled(not is_youtube_enabled)
            self.account_name_edit.setEnabled(is_youtube_enabled)
            self.channel_edit.setEnabled(is_youtube_enabled)
            self.category_id_edit.setEnabled(is_youtube_enabled)
            self.schedule_checkbox.setEnabled(is_youtube_enabled)
            self.schedule_datetime.setEnabled(is_youtube_enabled and self.schedule_checkbox.isChecked())
            self.load_youtube_credential_button.setEnabled(is_youtube_enabled)
        else:
            # Disable all YouTube controls during generation
            self.channel_name_input.setEnabled(False)
            self.account_name_edit.setEnabled(False)
            self.channel_edit.setEnabled(False)
            self.category_id_edit.setEnabled(False)
            self.schedule_checkbox.setEnabled(False)
            self.schedule_datetime.setEnabled(False)
            self.load_youtube_credential_button.setEnabled(False)
        
        # Enable/disable all lora input rows
        for row in self.lora_rows:
            row['model'].setEnabled(enabled)
            row['weight'].setEnabled(enabled)
            # Find the delete button - it's the last widget in the layout
            layout = row['widget'].layout()
            for i in range(layout.count()):
                item = layout.itemAt(i).widget()
                if isinstance(item, QPushButton):
                    item.setEnabled(enabled)
        
        self.settings_save_button.setEnabled(enabled)
        self.settings_load_button.setEnabled(enabled)
        # self.generate_btn.setEnabled(enabled)
        self.manage_prompt_variables_button.setEnabled(enabled)

        # Update button appearance
        if not enabled:
            self.generate_btn.setText("GENERATING...")
            self.generate_btn.setStyleSheet("""
                QPushButton {
                    background-color: #3a3a3a;
                    color: #888888;
                    border-radius: 4px;
                    border: 1px solid #555555;
                }
            """)
        else:
            self.generate_btn.setText("GENERATE VIDEO")
            self.generate_btn.setStyleSheet("""
                QPushButton {
                    background-color: #4CAF50;
                    color: white;
                    border-radius: 4px;
                    border: none;
                }
                QPushButton:hover {
                    background-color: #45a049;
                }
                QPushButton:pressed {
                    background-color: #3d8b40;
                }
            """)

    def add_images_lora_input_row(self):
        """Add a new row of inputs for image loras"""
        if len(self.images_lora_rows) >= self.MAX_LORAS:
            return
        
        # Create row widget and layout
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        
        # Model input
        model_input = QLineEdit()
        model_input.setPlaceholderText("Lora model name")
        model_input.setStyleSheet("padding: 5px;")
        
        # Weight spinbox
        weight_input = QDoubleSpinBox()
        weight_input.setRange(-4.0, 4.0)
        weight_input.setSingleStep(0.1)
        weight_input.setValue(1.0)
        weight_input.setStyleSheet("padding: 5px;")
        
        # Delete button
        delete_button = QPushButton("×")
        delete_button.setStyleSheet("""
            QPushButton {
                background-color: #d93025;
                color: white;
                padding: 5px 10px;
                border-radius: 3px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #ea4335;
            }
            QPushButton:pressed {
                background-color: #b31412;
            }
        """)
        
        # Add widgets to layout
        row_layout.addWidget(model_input, stretch=2)
        row_layout.addWidget(weight_input, stretch=1)
        row_layout.addWidget(delete_button)
        
        # Store row data
        row_data = {
            'widget': row_widget,
            'model': model_input,
            'weight': weight_input,
            'delete': delete_button
        }
        
        # Connect delete button
        delete_button.clicked.connect(lambda: self.remove_images_lora_row(row_data))
        
        # Add to container and list
        self.images_lora_layout.addWidget(row_widget)
        self.images_lora_rows.append(row_data)
        
        # Update add button state
        self.images_add_lora_button.setEnabled(len(self.images_lora_rows) < self.MAX_LORAS)
    
    def remove_images_lora_row(self, row_data):
        """Remove a row of image lora inputs"""
        if row_data in self.images_lora_rows:
            self.images_lora_rows.remove(row_data)
            row_data['widget'].deleteLater()
            self.images_add_lora_button.setEnabled(True)

    def cancel_generation(self):
        """Handle cancellation of generation"""
        if self.current_generation_worker and self.current_generation_worker.isRunning():
            self.current_generation_worker.cancel()
            self.current_generation_worker.wait()
        self.toggle_ui_elements(True)
        self.progress_bar.setValue(0)
        self.current_operation_label.setText("Generation cancelled")
        self.generate_btn.setText("GENERATE VIDEO")
        self.generate_btn.clicked.disconnect()
        self.generate_btn.clicked.connect(self.start_generation)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    # Use Fusion style for cross-platform consistency
    app.setStyle(QStyleFactory.create('Fusion'))

    # Set up application palette for a modern look
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(53, 53, 53))
    palette.setColor(QPalette.WindowText, Qt.white)
    palette.setColor(QPalette.Base, QColor(25, 25, 25))
    palette.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
    palette.setColor(QPalette.ToolTipBase, Qt.white)
    palette.setColor(QPalette.ToolTipText, Qt.white)
    palette.setColor(QPalette.Text, Qt.white)
    palette.setColor(QPalette.Button, QColor(53, 53, 53))
    palette.setColor(QPalette.ButtonText, Qt.white)
    palette.setColor(QPalette.BrightText, Qt.red)
    palette.setColor(QPalette.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.HighlightedText, Qt.black)
    app.setPalette(palette)
    window = VideoGeneratorApp()
    window.show()
    sys.exit(app.exec_())
